# SmartShapeCrop 代码审查发现清单（只读审查）

审查范围：`core/pool_designer/`（草图识别子系统）与 `gui/`（PyQt5 面板层）
版本：Python 3.13 / PyQt5 / V2.2
审查方式：只读，未修改任何文件
审查时间：2026-09-11

---

## 严重度统计

| 级别 | 数量 | 摘要 |
|------|------|------|
| P0 高危 | 2 | OCR 循环无整体超时；主界面关闭未接管 PropertyPanel/LShapePanel 后台线程 |
| P1 中危 | 4 | warmup terminate 强杀；warmup 期间重复点击生成导致多 Worker 并行；LShape 取消路径退役不完整；OCR 解析不可中断 |
| P2 低危 | 6 | 死代码/文档漂移/文案失真/防抖机制残留 |

---

## P0 高危

### P0-1 OCR 多尺度循环没有整体（循环级）超时 → 单洞最坏 ~29 分钟、L 形最坏 ~12 分钟卡死

**结论**：单次 `image_to_data` 有 `timeout=_PARSE_TIMEOUT_SEC`（硬上限 20s，pytesseract 会在超时后 Kill 子进程），**但"多尺度 OCR"与"方向标签 OCR"两个循环都没有循环级/整体 deadline**。代码中唯一的 deadline 只覆盖"进入循环之前"的预处理时间，一进循环就没有任何再检查点。GUI 表现为"识别中"，且 requestInterruption 无效（见 P1-4），近似卡死。

证据链：

1. **deadline 定义与唯一两处检查**：
   - `core/pool_designer/sketch_parser.py:70-78` 定义 `_check_deadline()`（基于 `_PARSE_TIMEOUT_SEC`）
   - `sketch_parser.py:117` 在 Step3 多尺度 OCR **之前**检查一次
   - `sketch_parser.py:128` 在 Step4 方向标签 OCR **之前**检查一次
   - 循环内部（`sketch_parser_vision.py`、`sketch_parser_numbers.py`）**零处**调用 `_check_deadline`

2. **Step3 多尺度 OCR 循环**（`sketch_parser_vision.py:445-546` `_multi_scale_ocr_scan`）：
   - `vision.py:453-465`：外层 `for scale in [1.0, 2.5, 4.0]`（3 档）× 内层 gray/enhanced 变体 × `psm in [6,8,11]`（3 种）
   - 最坏 ≈ 3 尺度 × 3~4 变体 × 3 PSM = **约 36 次 `image_to_data`**，每次 `timeout=_PARSE_TIMEOUT_SEC`（20s）→ 最坏 **720s ≈ 12 分钟**，无整体停止条件
   - 唯一提前退出：`vision.py:537-541` 仅当已获得 ≥8 个唯一 bbox 值才 break，依赖图像本身好；坏环境下永远达不到，会全部跑完

3. **Step4 方向标签 OCR 循环**（`sketch_parser_numbers.py:274-460+` `_extract_direction_label_numbers`）：
   - `numbers.py:347-358`：`scan_list` 最多 6 项（gray/enh × 1.0/2.5/4.0）
   - 每项 × `lang_options` 2 × `psm_list` [6,4,11,12] 4 = 8 次
   - 主循环最坏 **6×8 = 48 次** `image_to_data`（每次 20s）→ **960s ≈ 16 分钟**
   - 加 fallback 2 次 + Phase3 1 次 ≈ 51 次，合计 **≈ 17 分钟**
   - 即使 4 个字段方向标签已全部绑定也没有提前 break 保护（仅相位裁剪，见 `numbers.py:460-559` 附近；全部命中仍会继续尝试）

4. **L 形解析完全没有 deadline**：`core/pool_designer/lshape_sketch_parser.py:1137-1147` 直接调用 `_multi_scale_ocr_scan`，全文件 grep 无 `_check_deadline`/`deadline` 调用 → 最坏同样约 12 分钟，且前端 `lshape_panel.py:512` 显示提示文案"约 10~20 秒"（P2-3）。

5. **多洞 9 步法**：`sketch_parser_multihole.py:1470-1526` `_9step_multi_hole_parse` 设有 `deadline`，但也只在步骤之间检查，Step4/Step5 OCR 循环内（`multihole.py:908`、`multihole.py:997` 的 timeout=20s 是单次调用）仍无循环内检查。若图被 `quick_check` 判定为"多洞"但实际不是，会先耗掉 9 步法整段 OCR 再回退单洞（`sketch_parser.py:466-517` 入口决策链），最坏先白耗 ~12 分钟。

**建议修复方向**（不在本次只读范围执行）：把每次 `image_to_data` 的剩余预算改为 `min(timeout, remaining_deadline)`；或在循环体首行加 `_check_deadline()`；或给 Worker 传入可轮询的取消令牌（见 P1-4）。

---

### P0-2 主窗口关闭未接管 PropertyPanel/LShapePanel 的后台 QThread → 关闭窗口时"QThread: Destroyed while thread is still running"崩溃风险（0xC0000409 候选根因）

**证据**：
- `gui/main.py:454-470` `closeEvent` 仅调用 `self.cropper.shutdown()` + `self.canvas.shutdown()`；`main.py:528-529` `aboutToQuit` 也只连 cropper/canvas 两处
- **PropertyPanel 下三个 worker 均无 shutdown 接管**：
  - `_PoolRenderWorker`（`gui/property_panel_generate.py:127-143`，`property_panel_workers.py:63`，run() 内跑 `parse_sketch` 可达十几分钟）
  - `_SketchParseWorker`（`gui/property_panel_poolbox.py:642-675`）
  - `_WarmupScanWorker`（`gui/property_panel_poolbox.py:301-326`）
- **LShapePanel 下 `_LShapeParseWorker` 同样无 shutdown 接管**（`gui/lshape_panel.py:489-506`，run() 内跑 lshape OCR 无 deadline）
- `gui/property_panel.py` 全文件无 `shutdown`/`closeEvent`/`aboutToQuit` 挂钩；`gui/lshape_panel.py` 中 `cancel_running_parse`（`lshape_panel.py:722-730`）只在功能按钮路径被调用（如 poolbox.py:930），**不是关闭路径**
- 反例：`gui/canvas_widget.py:254-264` 才是规范 shutdown 实现（中断+wait(3000)），`main.py` 只接管了它和 cropper——property_panel 系成为缺口

**后果**：用户在 OCR 解析中（P0-1 背景下最长十几分钟）直接关窗 → QThread 析构时线程仍在运行 → PyQt 底层未定义行为（经典 0xC0000409/堆损坏候选）。即使多数 Worker 用 `requestInterruption` 也救不了——它们的 `run()` 内 OCR 循环不检查中断（见 P1-4），中断请求只是摆设，线程会一直跑到自然结束。

**候选位置**（若要复现，关窗时机选在草图解析刚启动 1~2 秒）：`property_panel_workers.py:127-638`（_PoolRenderWorker.run 长跑）与 `main.py:454-470`（缺少对应 shutdown 调用）。

---

## P1 中危

### P1-1 `WarmupScanWorker` 用 `quit()`+`terminate()` 强杀线程（poolbox.py:301-326）

- `gui/property_panel_poolbox.py:312` `warmup.quit()` —— **quit() 只对运行事件循环的线程有效**；`_WarmupScanWorker.run()`（`property_panel_workers.py:31-58`）是普通 Python 循环（扫描模板库），无 exec() → quit() 无效
- `poolbox.py:313-315`：`wait(2000)` 超时后直接 `warmup.terminate()` → Python 线程被强杀，**若恰逢持有 GIL 或正在写 `TemplateMatcher._cache` 类缓存则存在内存不一致/崩溃风险**；若在 terminate 时线程正持有 GIL，主线程后续任何 Python 操作都可能死锁
- 反观同文件 `_pool_auto_parse_sketch` 对 `_SketchParseWorker` 的退役（`poolbox.py:651-675`）走的是 requestInterruption + wait + finished→deleteLater 规范路径，说明本项目有正确范式，warmup 强杀属不一致实现
- 历史 ProductSummary V2.1.2 已记录此问题；此处按优先级归档，不视为新发现

### P1-2 warmup 进行中重复点击"生成预览"可导致多个 PoolRenderWorker 并行（generate.py:65-83）

- `gui/property_panel_generate.py:69-83`：每次进入 `_pool_run_generate`，若 warmup 在跑，就 `warmup.finished.connect(_after_warmup)` **再次连接一次**
- 用户连点 N 次 → N 个 `_after_warmup` 槽排队 → warmup 结束后 **依次触发 N 次 `_pool_start_generate_worker`**，每次无条件 `PoolRenderWorker(...).start()`（`generate.py:127-143`）
- 结果：**多个 `_PoolRenderWorker` 并行 start**，各自跑 `parse_sketch` + 渲染，同时写 `self._pool_worker` 引用与 render_id 逻辑；虽然有 isRunning 守卫在 worker 已启动后生效，但 warmup 结束后首次触发时 3 个 worker 可能同一事件循环批次内连续 start，竞态窗口存在
- 防抖侧已改进：`property_panel.py:159-169` 等已将 valueChanged 防抖驱动 DISCONNECTED，改为显式"生成预览"按钮——**连点按钮**是当前剩余入口

### P1-3 LShape 取消路径退役不完整（lshape_panel.py:722-730）

- `cancel_running_parse()`：`requestInterruption()` + `wait(2000)`，超时后**直接丢弃引用**，未连 `finished→deleteLater`，也未保留对象
- 对比 `_SketchParseWorker` 退役（`poolbox.py:651-675` 有 wait 失败后 `requestInterruption` + finished→deleteLater 兜底）与 `canvas_widget._retire_worker`（`canvas_widget.py:231-252`）都规范，LShape 是退役范式缺口
- 若 OCR 正在跑（P0-1 背景下 10 分钟+），2 秒 wait 必超时 → 对象悬空，线程继续跑；再次触发解析时新 worker 与旧线程并发，且关窗时（P0-2）风险叠加

### P1-4 OCR 解析不可中断（worker 内部无取消检查点）

- `_SketchParseWorker.run`（`property_panel_workers.py:642-663`）与 `_LShapeParseWorker.run`（`:674-700`）、`_PoolRenderWorker.run`（`:127-638`）均只在 run() 末尾检查 `isInterruptionRequested` 一次
- `_check_deadline` / `parse_sketch` 内部完全不感知 `QThread.requestInterruption`（线程对象不传入解析器）
- 后果：P0-1 的 12~29 分钟场景下，用户点"取消"无任何实际效果，线程跑满全部 OCR 才结束；叠加 P0-2 后关闭窗口即崩溃

---

## P2 低危

### P2-1 `sketch_parser.py:42` 冗余死代码
`_PARSE_TIMEOUT_SEC = 20` 在 `sketch_parser.py:42` 顶部重复定义一个，随后 `sketch_parser.py:49` `from .sketch_parser_base import _PARSE_TIMEOUT_SEC` 将其覆盖（值相同故无功能影响，但属死代码/文档漂移，改 base 值时会误导）——定义真源在 `sketch_parser_base.py:51`。

### P2-2 `sketch_parser.py:90` 注释与实现不符（deadline 语义夸大）
注释声称"多尺度OCR循环受总超时保护/deadline 覆盖"，实际（见 P0-1）只覆盖进入循环前的一次检查。注释与实现漂移点：`sketch_parser.py:88-92`。

### P2-3 LShape 面板提示文案失真
`lshape_panel.py:512` "约 10~20 秒" 声明 vs 实际最坏 ~12 分钟（见 P0-1-4）。

### P2-4 `except Exception: pass/continue` 吞异常点
- `vision.py` 多尺度 OCR 内层 `except Exception → continue`（OCR 容错合理，但**无日志**），若 Tesseract 路径配置错误（如 `tesseract_cmd` 指错）会在整轮循环 36 次全部静默失败后才在 7 步法步骤间报"全局OCR未识别"，排障体验差
- `numbers.py` 方向标签循环同模式（`numbers.py:460-559` 附近多处 `continue`，无失败计数）
- 不构成 P0/P1 的红线问题，属把错误降级为黑盒

### P2-5 防抖注释/实现不一致（property_panel_layers.py:374-413）
- 注释声称"200ms 防抖 + 800ms max-wait"（`:378`），实现仅见 200ms `QTimer.singleShot` 一处
- 且 2026-09-05 交互切换后 valueChanged 驱动已 DISCONNECTED（`property_panel.py:159-169/248-253/277/393-394/504`），防抖链实际由显式按钮驱动"生成预览"，注释残留旧设计

### P2-6 跨线程渲染：`target_w_cm/target_h_cm` 参数未真正参与 OCR 缩放
`vision.py:445` `_multi_scale_ocr_scan(..., **kwargs)` 接收 `target_w_cm/target_h_cm` 但从未在循环内使用（缩放档 1.0/2.5/4.0 与目标尺寸无关），`sketch_parser.py:119-122` 传入参数属于文档上的"预期能力"而非实际能力。

---

## 专题回答

### (a) OCR 多尺度循环是否存在无整体超时的卡死风险？位置在哪？

**存在。** 单次调用有 20s 硬超时（不会单次无限挂），但循环级无 deadline：

- **Step3 多尺度 OCR**：`core/pool_designer/sketch_parser_vision.py:445-546`（`_multi_scale_ocr_scan`），最坏 3 尺度 × 3~4 变体 × 3 PSM ≈ **36 次** `image_to_data` × 20s ≈ **12 分钟**，无循环内 deadline 检查（唯一检查点在 `sketch_parser.py:117` 进入循环前）
- **Step4 方向标签 OCR**：`core/pool_designer/sketch_parser_numbers.py:347/576/686/786`，`scan_list` 最多 6 项 × 2 语言 × 4 PSM ≈ **48+3 次** × 20s ≈ **17 分钟**，同样无循环内检查
- 7 步法单洞最坏合计 ≈ **29 分钟**；多洞 9 步法若误判会先白耗一段再回退；**L 形解析（lshape_sketch_parser.py:1137-1147）完全没有 deadline**，最坏 ≈ 12 分钟
- 追加风险：所有 worker 的 `requestInterruption` 不传入解析器 → 循环内无法中断（P1-4）

### (b) GUI 各 Worker 是否存在退役不完整/重复渲染竞态？位置在哪？

**退役不完整 3 处**：
1. **P0-2**：`main.py:454-470` closeEvent 未接管 PropertyPanel 的 `_PoolRenderWorker`/`_SketchParseWorker`/`_WarmupScanWorker` 与 LShapePanel 的 `_LShapeParseWorker`（唯一被接管的只有 cropper/canvas）
2. **P1-1**：`poolbox.py:312-315` warmup 用 quit（无效）+ terminate 强杀
3. **P1-3**：`lshape_panel.py:722-730` `cancel_running_parse` wait 超时后无 deleteLater 兜底

**重复渲染竞态 1 处**：
- **P1-2**：`property_panel_generate.py:69-83` warmup 期间连点"生成预览" → 多次 connect warmup.finished → 多个 `PoolRenderWorker` 并行 start
- 其余路径已规范：`canvas_widget.py:231-252`（requestInterruption + finished→deleteLater + render_id 丢弃过期结果）、`poolbox.py:651-675`（_SketchParseWorker 规范退役）、`lshape_panel.py:489-506`（常规解析退役）；防抖渲染已由按钮驱动取代（`property_panel.py:159-169` 等 DISCONNECTED），不再构成实时 valueChanged 竞态

### (c) safe_area / drawCrosshairCircle / PoolBox 崩溃线索现状？

- **`safe_area` / `SafeArea` / `drawCrosshairCircle` / `DrawCrosshair`：全仓库 grep 无任何匹配** —— 这些符号在当前代码库中不存在，应在早期重构中被改名或移除；作为 0xC0000409 崩溃根因已不适用
- **PoolBox 存在**：`gui/property_panel_poolbox.py`（`_PoolBoxMixin`）+ 对应渲染线程 **`PoolRenderWorker`（`gui/property_panel_workers.py:63-...`）**，其 run() 无中断检查（P1-4）、与 `_cached_outer_image` 共享只读 PIL Image（已按 F4 修复 clone 快照，安全）
- **当前 gui 层最可能导致堆损坏的候选（按可能性排序）**：
  1. **P0-2** 关闭窗口时 running QThread 被 QObject 数析构销毁（`main.py:454-470` 未接管 + `property_panel_workers.py:127-638` 长跑线程）→ 经典 "QThread: Destroyed while thread is still running" 0xC0000409 场景，**最优先候选**
  2. **P1-1** `poolbox.py:314` `warmup.terminate()` 强杀 Python 线程（GIL 持有时死锁 / 缓存写一半）
  3. **P1-3** LShape cancel 后悬空线程并发写模板缓存

---

## 附：已确认无问题的范围（规避重复排查）

- `sketch_parser_cache.py`（LRU + 锁 + deepcopy）：无问题
- `core/geometry.py`、`core/parser/name_parser.py`：尺寸/坐标模型一致，解析链完整
- `canvas_widget.py` 的 `_retire_worker` / `_on_full_render_done` render_id 丢弃过期结果：规范
- cropper_panel 的 CropWorker/AutoMatchWorker 常规退役：规范，且被 main.py 接管
- 防抖渲染与 Worker 竞争（200ms/800ms）：交互切换后 valueChanged 已 DISCONNECTED，不构成实时竞态（残留仅为注释漂移 P2-5）

---

*End of findings. 只读审查，未修改任何源代码。*