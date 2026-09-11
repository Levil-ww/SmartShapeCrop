# SmartShapeCrop 全面审查总结与建议报告

- **审查日期**：2026-09-11
- **被审查版本**：V2.2（git HEAD `33dc81e`，master，2026-09-11 13:35，工作区有未跟踪文件）
- **审查方式**：只读审查（未修改任何源码）+ 运行验证（全量测试）
- **审查范围**：全部源码（core / gui / tests / packaging / 配置与文档），core+gui 约 1.4 万行 Python；两个深度审查子代理分别细读「core 图像处理链路」与「草图识别 + GUI 线程层」
- **基线**：实测 `pytest tests/` **430 passed / 0 skipped / 0 failed**（51.91s），全绿
- **证据索引**：`.dumate/review/core_findings.md`（15 条）、`.dumate/review/gui_pool_findings.md`（12 条）、历史报告 `ProductSummary/SmartShapeCrop分析报告/`（20260910 主报告 + 复检更新）

---

## 摘要（TL;DR）

| 维度 | 结论 |
|---|---|
| 总体评价 | 功能完整、算法基础扎实、测试与文档习惯远优于同类内部工具；工程交付层（打包/GUI 线程生命周期）是主要风险区 |
| 测试基线 | 430 passed 全绿；本机此前缺 opencv/pytesseract/PyQt5 导致历史报告「11 failed」实为环境问题，补装后全量通过（含 tests/gui 56 个离屏用例） |
| 历史 P0×9 复检 | **2 项已修复**（P0-00 打包、P0-05 部分）、**1 项引入回归已修复**（N0-01 cropper 二次操作必现崩溃）、2 项实质改进未闭环（P0-04 OCR deadline）、**4 项仍存在**（P0-01/02/03/06） |
| 本轮新增 | P0×2（OCR 循环无整体超时最坏 ~29 分钟；主窗口关闭未接管 4 类后台线程 = 0xC0000409 首选根因）、P1×7、P2×14 |
| 崩溃专项 | 今日无复发（crash.log 不存在、日志无 ERROR）；历史符号 `safe_area`/`drawCrosshairCircle` 已不存在；当前最可能根因 = 未接管的 running QThread 被析构 |
| 最高优先整改 | ① main.py 关闭时接管 PropertyPanel/LShapePanel 线程（防崩溃）；② OCR 循环内 deadline 与取消检查点（防 29 分钟假死）；③ V13 绘制级回退补全 |

---

## 一、项目概览与总体评价

**SmartShapeCrop（智能形状裁剪设计器 V2.2）**：面向印刷/定制设计行业的 Windows 桌面工具（Python 3.13 / PyQt5），核心能力：

1. **圆角裁剪工具**：成品图等比缩放 + 四角独立圆角裁剪 + 多层边框自动检测与圆弧重绘
2. **水池设计器**：参数化 / 草图 OCR 智能识别（7 步串行流程 + 9 步多洞法），支持多洞嵌套、椭圆挖孔
3. **L 形挖角设计器**：独立面板 + 草图识别 + V2.2 新增「素材边框自动补全」（Profile / V13 / 旧路径三级路由）

**架构分层健康**：`main.py` → `gui/`（面板 facade + mixin + QThread worker）→ `core/`（纯业务层，不依赖 GUI），依赖单向、core 内部无环、`compat` 兼容层保持旧导入路径可用。

**总体评价**：业务价值明确、算法功底扎实（OCR 稳定性投票、多尺度扫描、距离场 mask、Profile 剖面扫描均为高质量实现）、测试与文档习惯良好。主要风险集中在三层：

1. **GUI 线程生命周期未收口**——5 类后台 worker 中仍有 4 类未接入关闭路径，是 0xC0000409 崩溃的首选根因；
2. **OCR 无循环级超时**——最坏场景单张草图可假死约 29 分钟，且不可取消；
3. **文档/承诺漂移**——README 宣称能力（白色扇形伪影检测、防抖渲染、1-2 亿像素内存）在代码中不存在或不成立。

此外昨日报告的核心问题（V13 回退链、大图 float64、嵌套矩形检测丢弃、matcher 无锁）除打包外均未根治，仅部分缓解。

---

## 二、审查基线验证（2026-09-11 实测）

### 2.1 版本与仓库状态

- HEAD `33dc81e`（2026-09-11 13:35，master，工作区有未跟踪文件）
- 今日提交链 8 个，覆盖：线程退役推广（`9b710fe`）、参数校验（`acc5bf3`）、原子写入（`65828de`）、PSD 占位图（`c19b85c`）、L 形数据源信任 target（`592923a`）、失效测试清理（`98f81b9`）、GUI 56 个离屏用例（`8a033c6`）等

### 2.2 测试基线

- 首次实跑 366 passed / 2 failed / 7 skipped —— 失败根因为**本机缺失 PyQt5**（历史报告「11 failed」同为环境问题，非代码缺陷）
- 补装 `opencv-python-headless / pytesseract / PyQt5` 后：**430 passed / 0 skipped / 0 failed（51.91s）**，含 `tests/gui/` 56 个离屏用例（test_cropper_panel / test_gui_smoke / test_lshape_panel，7 个文件）
- ⚠️ README:99/233/655 标注「374 passed / 0 skipped」与实测不符（缺 56 个 GUI 用例），需同步
- ⚠️ tests/gui 用例覆盖初始状态与轻量交互，**仍缺「二次操作」场景回归**（连续启动/退役 worker 的协议测试）——N0-01 同类回归仍有复发风险

### 2.3 构建与打包

- `packaging/packageV2.2.py:111-112` 已补 `core.lshape_border` / `core.lshape_border_route`；`:74` APP_NAME 已更新「智能裁剪设计器V2.2」
- 真机产物 `dist/智能裁剪设计器V2.2.exe`（约 212MB）已生成，打包链路从"脚本修好"进展到"实际跑通"
- 残留建议：**对 V2.2.exe 做一次 L 形挖角端到端冒烟**（V2.2 主卖点，至今未在发布版实测）；归档 `packageV2.1.2.py` 避免误用

### 2.4 崩溃现场

- `crash.log` 不存在、今日日志无 ERROR/Traceback → **0xC0000409 今日未复发**
- 历史符号 `safe_area` / `SafeArea` / `drawCrosshairCircle` / `DrawCrosshair` **全仓库零匹配** —— 已在重构中移除，作为崩溃根因不再适用
- 结论：崩溃风险从"已发生"退化为"潜在"（见 §五 候选根因分析）

---

## 三、历史问题复检对照（2026-09-10 报告 P0×9/P1×10/P2×15 + N0-01 → 今日状态）

### 3.1 P0 级（逐条）

| # | 问题 | 今日状态 | 证据 |
|---|---|---|---|
| P0-00 | PyInstaller 打包漏收 lshape_border 两模块 + 版本号未更新 | ✅ **已修复** | packageV2.2.py:74,111-112；spec 同源；dist/V2.2.exe 已生成 |
| P0-01 | L 形路由回退链断裂（V13 失败不回退） | 🔶 **部分修复**（见 N-P1-01） | 检测级已修：V13 返回 None → 回退 Profile/旧路径（lshape_border.py:538-541/586-609）；**绘制级仍断**：V13 命中后 patch 失败直接 return（:546/594） |
| P0-02 | 大图全图 float64 转换，与 1-2 亿像素宣称冲突 | ❌ **仍存在**（见 N-P1-02） | 活代码 4 处：image_cropper_border.py:344、image_cropper_mask.py:729、lshape_border.py:66/123；每处 2 亿像素 ≈ 4.8GB |
| P0-03 | 多层嵌套矩形检测结果被丢弃 | ❌ **仍存在**（见 N-P1-03） | corner_protect_map 全角 True（image_cropper_border.py:369-371）→ image_cropper_mask.py:297-298 `pass`，B 段 90 行死代码 |
| P0-04 | OCR 循环无真实 deadline | 🔶 **实质改进未闭环**（见 N-P0-01） | deadline 已传入 7 步法（sketch_parser.py:117/128/556 monotonic+20s）与 9 步法（sketch_parser_multihole.py:1519/1536/1935），但 **OCR 循环内无循环级 deadline 检查（tesseract 单次调用有 timeout 但循环间无校验）**，最坏仍 ~29 分钟 |
| P0-05 | GUI 面板侧 worker 未接入退役协议 | 🔶 **部分修复**（见 N-P0-02） | CropperPanel 已修复（N0-01）；lshape_panel / property_panel_poolbox 已接入（9b710fe）；但 main.py:454-470 关闭时**未接管** PropertyPanel/LShapePanel 4 类 worker；warmup 仍用 terminate 强杀（property_panel_poolbox.py:312-315） |
| P0-06 | TemplateMatcher 共享实例无锁 | ❌ **仍存在**（见 N-P1-04） | core/parser/ 无任何 threading/Lock/RLock |
| P0-07 | 预热等待信号竞态（永久「⏳」/ 双 worker） | 🔴 **仍存在**（见 N-P1-05） | property_panel_generate.py:69-83 每次点击生成都 `warmup.finished.connect`，连点可排队多个 `_after_warmup` → 多 worker 并行 |
| P0-08 | 跨线程信号连接纯 Python 回调 | ⚠️ **部分残留** | 导出路径仍在：cropper_panel.py:1097 `finished_ok.connect(lambda _: self._on_export_done(...))`，`:1107-1116` 在 worker 线程弹 QMessageBox；warmup 闭包跨线程改 UI（property_panel_generate.py:74-79） |

### 3.2 复检新增问题

| # | 问题 | 今日状态 | 证据 |
|---|---|---|---|
| N0-01 | cropper worker 修复引入必现回归（二次操作 RuntimeError） | ✅ **已修复** | cropper_panel.py:707/750 回调置 None + `parent=self` 托管 C++ 对象（[Fix 0xC0000409-v2]）+ finished→deleteLater（:697/980/995）；今日 56 个 GUI 离屏用例覆盖 |
| N1-01 | cut 角坐标未做 [0,H]×[0,W] 钳制（负索引静默切错） | ⚠️ **待复验** | 今日提交未涉及；建议补 min/max 钳制 + 单测（沿用复检建议） |

### 3.3 P1 级（10 项）

| # | 问题 | 今日状态 |
|---|---|---|
| P1-01 | 小图直边采样越界/负索引（image_cropper_mask.py:836-866） | ❌ 未复验（上轮仍存在，代码未见变更提交） |
| P1-02 | L 形数量级修正错改对象（lshape_sketch_parser.py:980-990） | ❌ 未复验 |
| P1-03 | 350px 间距死限不随 scale 缩放（sketch_parser_numbers.py:554-560） | ❌ 未复验 |
| P1-04 | 像素→厘米几何校验死代码（sketch_parser_margins.py:586-626） | ❌ 未复验 |
| P1-05 | 无边框素材切边裸边（_skip_unified 无条件跳过黑框） | ❌ **仍存在**（本轮确认掩盖链完整：image_ops.py:1143 跳过黑框 / :1209 返回值丢弃 / :1234 只兜异常） |
| P1-06 | 三条路由 scale 公式/补边方向不一致 | ❌ **仍存在**（本轮确认 lshape_border.py:617 非等比取平均） |
| P1-07 | 草图上传主线程解码大图（property_panel_poolbox.py:560-564） | ❌ 未复验 |
| P1-08 | GUI 线程模板库全扫描卡死（property_panel_generate.py:297-401） | ❌ 未复验 |
| P1-09 | 厚度封顶「截断最末层」分支与注释相悖 | ❌ **仍存在**（本轮确认 core/corner/detection.py:52/439/727，Step2 先 pop 后查） |
| P1-10 | gap 清理冗余全 ROI 扫描 + sector_render 死代码 | ❌ 未复验（与 N-P2-02/N-P2-03 同源） |

### 3.4 P2 级（15 项）

| # | 问题 | 今日状态 |
|---|---|---|
| P2-01 | README 与实现系统性漂移 | ⚠️ 部分同步（伪影检测/防抖仍失真，且测试基线 374 与实测 430 不符，见 §六） |
| P2-02 | 防抖渲染是死代码 | ⚠️ 仍存（本轮确认 valueChanged 已 DISCONNECTED，实现含 200ms singleShot + 800ms max-wait（与注释一致），但 `_schedule_apply_quiet` 无活跃调用方，防抖链整体为死代码） |
| P2-03 | 死信号 / 文档方法名漂移 | ❌ 未复验 |
| P2-04 | 魔法数字大量散落 | ❌ **仍存**（本轮确认 core/corner/detection.py:237-240 GAP_* 硬编码且 config 无键） |
| P2-05 | 箭头映射表重复键 + 错条目 | ❌ 未复验 |
| P2-06 | 静默吞异常无日志 | ⚠️ 仍存（本轮确认 sketch_parser_vision.py/sketch_parser_numbers.py `except → continue` 无日志、无失败计数） |
| P2-07 | 多洞结果不写缓存 | ❌ 未复验 |
| P2-08 | pool_mode 字段与 is_pool_mode() 不一致 | ❌ 未复验 |
| P2-09 | PSD 加载（隐藏层合成/失败静默/导出名序号） | 🔶 c19b85c「PSD 占位图」提交涉及占位逻辑；隐藏层/导出名未确认 |
| P2-10 | 模板缓存 6 小时上限 | ❌ 未复验 |
| P2-11 | LOD deepcopy 内存翻倍 | ❌ **仍存在**（本轮确认 image_ops.py:504-506 深拷贝含 `_cached_outer_image` 的 design） |
| P2-12 | 根目录卫生 | 🔶 **基本修复**：_dbg_*.png、_debug_v13.py、Test-multiplhole.py 均已清除（剩余 process_image.py 为正常脚本） |
| P2-13 | 三套历史记录实现重复 / 1cm 常量重复 | ❌ 未复验 |
| P2-14 | 'hua' 子串误匹配 | ❌ 未复验 |
| P2-15 | save_jpg 未指定 subsampling | ❌ 未复验 |

---

## 四、本轮深度审查新增发现

> 编号 N-*（New）。行号均为本次核验所得，可与历史清单精确衔接。

### 4.1 P0 级（2 项）

#### N-P0-01 · OCR 循环无整体（循环级）超时 → 单洞最坏 ~29 分钟、L 形最坏 ~12 分钟假死

历史 P0-04 修复了"deadline 未传入"的一半，**循环内部仍无循环级 deadline 检查（tesseract 单次调用有 timeout 但循环间无 deadline 校验）**：

| 环节 | 位置 | 最坏调用次数 × 20s | 说明 |
|---|---|---|---|
| 7 步法 Step3 多尺度 OCR | sketch_parser_vision.py:445-546 | 3 尺度 × 3~4 变体 × 3 PSM ≈ **36 次 ≈ 12 分钟** | 唯一提前退出（≥8 个唯一 bbox，:537-541）依赖图像质量，坏环境达不到 |
| 7 步法 Step4 方向标签 OCR | sketch_parser_numbers.py:347/576/686/786 | 6 项 × 2 语言 × 4 PSM ≈ **51 次 ≈ 17 分钟** | 方向标签已全部绑定也无 break 保护 |
| **单洞合计** | — | ≈ **29 分钟** | deadline 仅 sketch_parser.py:117/128 进入循环前各查一次 |
| L 形解析 | lshape_sketch_parser.py:1137-1147 | ≈ **12 分钟** | **全文件无任何 deadline** |
| 多洞误判 | sketch_parser_multihole.py:1470-1526 | 先白耗 9 步法整段再回退 | quick_check 误判即先耗 ~12 分钟 |

叠加 N-P1-06：`requestInterruption` 不传入解析器，循环内**无法中断** → GUI 表现为「识别中」假死。

**建议**：每次 `image_to_data` 预算改为 `min(timeout, remaining_deadline)`；或循环体首行 `_check_deadline()`；或给 Worker 传入可轮询的取消令牌。

#### N-P0-02 · 主窗口关闭未接管 PropertyPanel/LShapePanel 后台线程 → running QThread 析构（0xC0000409 首选候选）

- `main.py:454-470` `closeEvent` 仅接管 `cropper.shutdown()` + `canvas.shutdown()`；`main.py:528-529` `aboutToQuit` 同样只连这两处
- **未接管**（gui/property_panel.py 全文件无 shutdown/closeEvent 挂钩）：
  - `_PoolRenderWorker`（property_panel_workers.py:63，run 内跑 parse_sketch 可达十几分钟）
  - `_SketchParseWorker`（property_panel_poolbox.py:642-675）
  - `_WarmupScanWorker`（property_panel_poolbox.py:301-326）
  - `_LShapeParseWorker`（lshape_panel.py:489-506）
- 反例：canvas_widget.py:254-264 为规范 shutdown 实现（中断 + wait 3s）——接管范围不完整是唯一缺口
- **后果**：OCR 解析中（N-P0-01 背景下最长十几分钟）直接关窗 → QThread 析构时线程仍在运行 → PyQt 未定义行为，**经典 0xC0000409 场景**；即使 worker 有 requestInterruption 也无效（run 内 OCR 循环不检查，见 N-P1-06）

**建议**：main.py closeEvent/aboutToQuit 增接 `property_panel.shutdown()` 与 `lshape_panel.shutdown()`；为 4 类 worker 实现 `_retire_worker` 同款退役协议（requestInterruption + wait + finished→deleteLater）；补「解析启动 1~2 秒后关窗」的离屏回归用例。

### 4.2 P1 级（7 项）

#### N-P1-01 · V13 绘制级回退断裂 + 三层失败掩盖（历史 P0-01 剩余部分）

- `lshape_border.py:546/594` 两处 V13 命中分支 `return _apply_v13_path(...)`；函数内部 4 条失败出口全部 `return False` 无回退（:696-698 edge 缺失、:732-734 子图 <4px、:740-742 挖角 <1px、:760-762 ValueError）
- 失败被三层掩盖：`image_ops.py:1143` `_skip_unified` 跳过统一黑框兜底 → `:1209` 补全返回值从未读取 → `:1234-1235` 只兜异常不兜 False
- 用户可见：V13 命中但 patch 失败时 L 形切口**缺边框且无兜底、无告警（仅 info 级日志）**

**建议（最小改动）**：546/594 行改为「patch 成功才返回，失败继续向下流 Profile/旧路径」；补「V13 命中但 patch 抛 ValueError」单测；1209 行返回值接入调用方真值判断。

#### N-P1-02 · 大图内存峰值 4 处活代码 + LOD 深拷贝（历史 P0-02 / P2-11 精确定位）

- 全图 uint8→float64：`image_cropper_border.py:344`、`image_cropper_mask.py:729`（都只为 21×21 中值采样）、`lshape_border.py:66/123`（只为掩膜均值）——每处 2 亿像素 ≈ 4.8GB
- `image_ops.py:504-506` LOD `deepcopy(design)`：PIL.Image 深拷贝复制像素数据 → LOD 阶段内存翻倍
- 对照：`core/corner/detection.py:196-205` 是唯一已降采样点（注释明确"避免 2 亿像素 ×3×8=4.8GB"），方案未推广

**建议**：复用 core/corner/detection.py 降采样方案；LOD 分支只拷轻量字段或先降采样再深拷贝。

#### N-P1-03 · 嵌套矩形检测结果恒被丢弃 = 每次圆角裁剪白跑全图扫描（历史 P0-03 代价量化）

- `core/corner/detection.py:853` 全图 uint8 复制 + `:855-858` 四边步长 2 扫描 + `:861-873` 层合成，在每次 `apply_border_only_corners` 完整执行后全部丢弃
- 根因：`image_cropper_border.py:369-371` `corner_protect_map` 对**所有角恒为 True** → `image_cropper_mask.py:297-298` pass → B 段（:300-387 逐层恢复）恒不执行
- 附带：README 主打"R_eff 逐层递减"（README:270-273）实际未接线

**建议**：corner_protect 恒 True 时跳过检测并删除 B 段（或反向实现 R_eff 递减），二者互斥保留其一。

#### N-P1-04 · template_matcher 无锁并发读写（历史 P0-06 定位确认）

- `core/parser/` 全目录 grep `threading|Lock|RLock` **零命中**
- 竞态面：scan 写 `_cache/_idx_*/_subdir_mtimes`（tmp+replace 写盘）与 `find_best_match` 隐式触发 scan 并行；评分循环写共享 `TemplateEntry.score` 与 `_upsert_entry` 替换构成 check-then-act
- 可致：`RuntimeError: dictionary changed size during iteration`、评分交叉污染、缓存文件撕裂
- 已确认安全：`name_parser.py`（纯函数）、`psd/loader.py`（每次独立加载）

**建议**：加 `threading.RLock`，评分写入改局部副本。

#### N-P1-05 · warmup 强杀 + 连点多 worker（历史 P0-05/P0-07 剩余）

- `property_panel_poolbox.py:312` `warmup.quit()` —— quit 只对事件循环线程有效，`_WarmupScanWorker.run`（property_panel_workers.py:31-58）未实现中断检查 → **无效**；`:313-315` wait(2000) 超时后 `terminate()` 强杀 → GIL 持有时死锁 / 模板缓存写一半
- `property_panel_generate.py:69-83`：每次进 `_pool_run_generate` 在 warmup 运行时**再连接一次** `finished` → 连点 N 次 → warmup 结束后依次触发 N 次 `_pool_start_generate_worker` → **多个 PoolRenderWorker 并行 start**（同事件循环批次内竞态窗口存在）

**建议**：warmup 退役改 requestInterruption + wait + finished→deleteLater（本项目已有范式）；`finished` 连接前先 `disconnect` 或一次性标志位 + isRunning 复查。

#### N-P1-06 · LShape 取消悬空 + OCR 全程不可中断（历史 P0-05/P0-04 深化）

- `lshape_panel.py:722-730` `cancel_running_parse`：requestInterruption + wait(2000)，超时后**直接丢弃引用**，无 finished→deleteLater、无对象保留 → 悬空线程与下次解析并发，关窗时叠加 N-P0-02
- `_SketchParseWorker/_LShapeParseWorker/_PoolRenderWorker.run` 仅在**最末尾**检查一次 `isInterruptionRequested`（property_panel_workers.py:127-700）；解析器完全不感知中断 → 用户点「取消」无效，线程跑满全部 OCR

**建议**：取消路径对齐 `_SketchParseWorker` 退役（property_panel_poolbox.py:651-675 范式）；OCR 循环内轮询取消令牌。

#### N-P1-07 · 「边框检测只有 2px」源码结构仍在，仅被部分绕过

- `config.py:102`：`BORDER_SCAN_STEP_PX=2`、`BORDER_MIN_LAYER_THICKNESS_PX=2` 未变；`core/corner/detection.py:732` 步长 2 扫描，差分阈值 `25×3=75`
- 现状：L 形补全链路已被 V13 + Profile 绕过（对黑描边+主色带不再依赖 2px 结构）；但**圆角重绘主链**（`apply_border_only_corners` → `_scan_edge_boundaries`）仍受 2px 约束，历史问题**绕过而非根治**

### 4.3 P2 级（14 项，精简）

| # | 问题 | 证据 |
|---|---|---|
| N-P2-01 | GAP_* 常量硬编码 core/corner/detection.py:237-240，config 无对应键；:230-231 注释（20）与代码（25.0）漂移；docstring 声称"常量全在 config.py"失真 | core/corner/detection.py:9,230-240 |
| N-P2-02 | 死代码三函数 `_analyze_corner_sector_content`/`_estimate_outer_background`/`_corner_sector_has_content`（image_cropper_mask.py:396/502/531）无调用方，其中 :510/:552 为死代码上的全图 float64 陷阱 | image_cropper_mask.py:396-597 |
| N-P2-03 | mask B 段 90 行（:300-387）因 corner_protect 恒 True 语义不可达；`_completion_ok` 死变量 | image_ops.py:1209 |
| N-P2-04 | `_enforce_border_thickness_caps` Step2 先 pop 后查总量，轻微超限时最内层真实边框被整层误丢（与注释意图相反） | core/corner/detection.py:52,439,727 |
| N-P2-05 | `apply_lshape_border_completion` docstring 三级路由承诺（V13 失败回退旧路径）未覆盖 patch 级缺口 | lshape_border.py:482-492,781-782 |
| N-P2-06 | 素材 contain 等比+边缘延展与 scale 整图比值换算存在 ±px 误差；scale_avg 非等比取平均 | image_ops.py:630-651,1193-1202；lshape_border.py:617 |
| N-P2-07 | `lshape_border_route.py` 模块级惰性搜索状态（_SEARCH_STEPS/_r_cm 等）无锁，未来并行预览接入即交叉污染 | lshape_border_route.py（模块级） |
| N-P2-08 | cm↔px 换算分散 3+ 处无集中 converter，DPI 语义调整易漏改 | image_cropper_border.py:335；lshape_border_route.py；image_ops.py |
| N-P2-09 | `app_settings.py` QSettings 与 JSON 后备序列化行为不一致（str vs 对象；QT 分支 dumps 成字符串） | app_settings.py:127-135,198-213,324-352 |
| N-P2-10 | `sketch_parser.py:42` 冗余常量定义被 :49 导入覆盖；:88-92 注释夸大「loop 受 deadline 保护」 | sketch_parser.py:42,49,88-92 |
| N-P2-11 | LShape 面板文案「约 10~20 秒」（lshape_panel.py:512）vs 实际最坏 ~12 分钟 | lshape_panel.py:512 |
| N-P2-12 | OCR 循环 `except Exception → continue` 无日志无失败计数，Tesseract 配置错误时整轮 36 次静默失败，排障体验差 | sketch_parser_vision.py；sketch_parser_numbers.py |
| N-P2-13 | 防抖注释「200ms + 800ms max-wait」（property_panel_layers.py:374-413）与实现一致（200ms singleShot + 800ms max-wait），但 `_schedule_apply_quiet` 无活跃调用方，防抖链整体为死代码；valueChanged 已 DISCONNECTED，渲染由显式按钮驱动 | property_panel_layers.py:374-413 |
| N-P2-14 | `target_w_cm/target_h_cm` 参数传入 `_multi_scale_ocr_scan` 但循环内未使用（缩放档 1.0/2.5/4.0 与目标尺寸无关），属文档上的"预期能力" | sketch_parser_vision.py:445 |

---

## 五、0xC0000409 崩溃问题专项诊断

### 历史符号已消失

`safe_area` / `drawCrosshairCircle` / `DrawCrosshair` **全仓库零匹配**，不在当前代码库中；作为崩溃根因已不适用（已在早期重构更名为移除）。

### 当前 GUI 层最可能导致堆损坏的候选（按可能性排序）

| 序 | 候选 | 证据 | 触发场景 |
|---|---|---|---|
| 1 | **running QThread 被析构**：main closeEvent 未接管 PropertyPanel（3 worker）/LShapePanel（1 worker），OCR 解析中关窗 | main.py:454-470；property_panel_workers.py:127-638；property_panel.py 无 shutdown 挂钩 | 草图解析启动后 1~2 秒内直接关窗（N-P0-01 使窗口期长达数分钟） |
| 2 | **`warmup.terminate()` 强杀 Python 线程**：恰逢持有 GIL 或正在写 `TemplateMatcher._cache` 即内存不一致/死锁 | property_panel_poolbox.py:312-315 | 预热扫描中重开/关闭应用 |
| 3 | **LShape 取消后悬空线程并发写模板缓存** | lshape_panel.py:722-730 | 解析中取消后再次触发解析 |

### 现状

- 今日无复发实证（crash.log 不存在、当日日志无 ERROR/Traceback）
- 昨日必现回归 N0-01（AutoMatchWorker RuntimeError 7 连发）已随 cropper v2 修复关闭
- **修复注意力应全部转向候选 1**：它把崩溃窗口从"偶发高并发"扩展为"低概率但窗口期极长（分钟级）的确定性路径"

---

## 六、文档与实现漂移清单（README 需修正）

| README 声称 | 实现事实 | 位置 |
|---|---|---|
| 白色扇形伪影检测（<20 保留 / ≥50 清除 / 3px 簇） | **功能不存在**；实际是 beyond_arc 全清 + content_protect 保护 | README:20,64,293-297 |
| GUI 防抖渲染 200ms / 800ms 最大等待 | valueChanged 已 DISCONNECTED（所有 connect 调用已注释），防抖由显式按钮驱动；防抖实现含 200ms singleShot + 800ms max-wait，但 `_schedule_apply_quiet` 无活跃调用方，整体为死代码 | README:32,289；property_panel_layers.py:374-413 |
| 测试基线 374 passed / 0 skipped（27 文件 · 342 用例） | **实测 430 passed / 0 skipped**（51.91s，含 tests/gui 56 用例） | README:99,233,655 |
| 多层边框动态圆角 R_eff 逐层递减 | 未接线（corner_protect 恒 True，nested 恢复恒不执行） | README:270-273；image_cropper_mask.py:297-298 |
| 亮度突变阈值 25 | 实际 `BORDER_LUMINANCE_DIFF_THRESHOLD × 3 = 75`（3 线均值差分） | README:306；core/corner/detection.py:45-49 |
| 所有业务常量集中在 config.py | GAP_* 等仍硬编码于 core/corner/detection.py:237-240；pool_designer 40+ 阈值散落 | README:544；core/corner/detection.py:237-240 |

> 历史报告其余漂移项（mask 主路径不经过 carve_corner_on_mask、仅最外层圆角化、OUTER_BAND=3px vs 实际 5、is_outermost_solid 标志不存在等）仍有效，不再逐一复述，见 20260910 主报告 §六。

---

## 七、工程亮点（正面确认，建议保留）

1. **线程退役范式**：`canvas_widget._retire_worker`（requestInterruption + wait + deleteLater，canvas_widget.py:231-252）与 `property_panel_poolbox.py:651-675`（_SketchParseWorker）是规范模板，cropper v2 修复已对齐
2. **今日 8 提交质量**：原子写入（65828de tmp+os.replace）、参数校验（acc5bf3）、失效测试清理（98f81b9）方向正确；GUI 56 个离屏用例（8a033c6）补齐了 tests/gui 空目录的历史欠账
3. **崩溃修复闭环度**：cropper [Fix 0xC0000409-v2]（parent 托管 + deleteLater + 置 None）解决了 debounce 式"Python 引用与 C++ 生命周期错配"，且今日全量测试防回归
4. **性能好实践**：`core/corner/detection.py:196-205` 内容参考色降采样（注释明确防 4.8GB）；ROI 化 Step A 弧线重绘；`image_ops.py:1167` 延迟导入避免顶层负担
5. **诊断体系**：`log_setup.py` 幂等初始化 + 文件 handler 失败降级控制台；`artifact_cleanup.py` 白名单隔离 + 保护名单 + 原子写 + dry-run
6. **线程安全已确认面**：`name_parser.py` 纯函数无状态；`psd/loader.py` 每次独立加载；`sketch_parser_cache.py`（LRU + 锁 + deepcopy）；render_id 丢弃过期结果（canvas_widget）
7. **工程纪律**：pytest.ini 将 PytestReturnNotNoneWarning 升为 ERROR；根 conftest 屏蔽非 tests 目录 test_*；scripts/ 命名与生命周期约定

---

## 八、整改路线图

### 短期（优先，1 周内）

1. **关闭崩溃窗口（N-P0-02）**：main.py closeEvent/aboutToQuit 接管 PropertyPanel 与 LShapePanel；4 类 worker 统一 `_retire_worker` 退役协议；补「解析中关窗」离屏回归
2. **OCR 循环级 deadline + 取消（N-P0-01/N-P1-06）**：`image_to_data` 预算 `min(20s, remaining)`；循环体首行 `_check_deadline()`；将取消令牌传入解析器；L 形解析补总 deadline；lshape_panel.py:512 文案修正
3. **V13 绘制级回退（N-P1-01）**：546/594 改「失败继续向下回退」；`image_ops.py:1209` 返回值接入真值；补 ValueError 单测
4. **warmup 强杀与连点竞态（N-P1-05）**：warmup 退役改规范范式；finished 连接前 disconnect/一次性
5. **打包冒烟**：V2.2.exe 上跑 L 形挖角端到端；归档 packageV2.1.2.py
6. **README 同步**：按 §六 修正伪影/防抖/测试基线三处；374→430

### 中期（2-4 周）

7. **大图内存收敛（N-P1-02）**：4 处 float64 改降采样（复用 core/corner/detection.py:196-205）；LOD 避免整图深拷贝
8. **嵌套矩形接线或删死代码（N-P1-03）**：实现 R_eff 逐层递减，或删除检测与 B 段
9. **template_matcher 加锁（N-P1-04）** + lshape_border_route 模块状态隔离
10. **正确性补丁**：N1-01 坐标钳制、P1-02 逐侧数量级、P1-03 350×scale、N-P2-04 厚度截断、P1-01 采样 clip
11. **消除三层失败掩盖**（与 3 联动）：`_skip_unified` 改为"补全成功才跳过统一黑框"

### 长期（技术债）

12. **阈值收敛**：GAP_* / sector_render / mask / pool_designer 40+ 阈值迁入 config.py；`_ALGO_VERSION` 单源；cm↔px 集中 converter（N-P2-08）
13. **死代码清理**：mask 三死函数（N-P2-02）、B 段（N-P2-03）、防抖残留、12 个占位函数、`_render_async` 死分支
14. **双入口收敛**：`apply_rounded_corners` 与 `apply_border_only_corners` 语义二选一（避免测试固化旧语义）
15. **输出质量**：save_jpg 显式 4:4:4；PSD 隐藏层跳过合成 + 失败显式返回 + 导出名稳定化；`_looks_like_tile` 黑名单化
16. **可观测性**：OCR 失败计数与日志（N-P2-12）；跨线程回调全部改 QObject 槽 / QueuedConnection（P0-08 残留）

---

## 九、结论

项目**功能完整、算法正确性基础扎实**，今日 8 个提交质量良好（cropper 崩溃闭环、GUI 测试补账、原子写入），430 测试全绿。当前最现实的威胁集中在**三件事**：

1. **关闭窗口即触发 running QThread 析构（N-P0-02）** —— 崩溃窗口因 OCR 无超时被拉到分钟级，是 0xC0000409 的最可能复发路径，**应作为第一修复优先级**；
2. **OCR 假死（N-P0-01）** —— 最坏 29 分钟不可中断，直接损害"草图智能识别"核心体验；
3. **V13 回退断裂（N-P1-01）** —— 触发面窄但属主卖点静默失效，叠加三层掩盖后无感知。

历史 P0 清单的 9 项中，打包、cropper 回归两项已闭环，其余均未根治（部分缓解）；建议本轮集中修复 N-P0-02 + N-P0-01 + N-P1-01 三项后，再推进中期内存与并发收敛。修复前后均以 430 测试基线 +「关闭/取消/连点」三类 offscreen 回归守门。

---

### 附：证据文件索引

- `.dumate/review/core_findings.md` — core 图像处理链路 15 条发现 + 8 项正面确认
- `.dumate/review/gui_pool_findings.md` — 草图识别 + GUI 线程层 12 条发现 + 已确认无问题范围
- `ProductSummary/SmartShapeCrop分析报告/SmartShapeCrop-项目审查报告-20260910.md` — 上轮主报告（P0×9/P1×10/P2×15）
- `ProductSummary/SmartShapeCrop分析报告/SmartShapeCrop-项目审查报告-20260910-复检更新.md` — 上轮复检（N0-01 回归实证）