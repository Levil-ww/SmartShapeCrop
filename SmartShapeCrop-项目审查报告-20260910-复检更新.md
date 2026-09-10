# SmartShapeCrop 项目审查报告（复检更新）

- **复检时间**：2026-09-10（用户更新代码后）
- **被审查版本**：V2.2（git HEAD `fdc8602`，2026-09-10 11:23）
- **复检基准**：上一份报告《SmartShapeCrop-项目审查报告-20260910.md》（HEAD `ee1097b`）所列全部 P0-P2 问题逐条复验
- **审查方式**：只读审查，未修改任何项目文件
- **测试基线**：实测 `.venv`（Python 3.13.14）`pytest tests/`：**320 passed / 5 skipped**（46.5s），仍全绿

---

## 一、本轮变更范围（ee1097b → fdc8602 + 未跟踪新增）

| 变更 | 内容 | 关联上轮问题 |
|---|---|---|
| `core/image_ops.py`（b607e3a, 10:54） | L 形挖角 cut 角 mask 四角坐标独立化修复（原四角共用 `cut_top_y/cut_right_x` 导致除 bl 外三角切边错位） | 新修复（上轮未列此 bug） |
| `gui/cropper_panel.py`（fdc8602, 11:23） | 新增 `_retire_worker/_retire_match_worker/shutdown`，创建 worker 前退役旧 worker，连接 `finished → deleteLater`，回调不再直接置 None | P0-05（局部）/新回归（见 §三） |
| `main.py`（fdc8602） | `closeEvent` 与 `aboutToQuit` 接入 `cropper.shutdown()` | P0-05（局部） |
| `packaging/packageV2.2.py`（未跟踪新增） | V2.2 打包脚本：补全 `core.lshape_border/lshape_border_route`，APP_NAME 更新为 V2.2，修复 PyInstaller 版本检查、sys.path | **P0-00（修复）** |
| `智能裁剪设计器V2.2.spec`（未跟踪新增） | 配套 spec（hiddenimports 含两模块，name=V2.2） | P0-00（修复） |
| 根目录清理 | `_dbg_tl/tr/bl/br.png` 已删除；`_debug_v13.py`、`Test-multiplhole.py` 仍留 | P2-12（部分修复） |
| `crash.log` | 新增 **2026-09-10 11:24 连续 7 条** `RuntimeError: wrapped C/C++ object of type AutoMatchWorker has been deleted`（cropper_panel.py:661） | **新回归铁证（见 §三）** |

另：上一份审查报告已被纳入版本库（b607e3a 附带提交，240 行）。

---

## 二、变更评估与本轮核心结论

### 2.1 打包链路（P0-00）—— 已修复并通过真机打包验证 ✅

- `packaging/packageV2.2.py:111-112` 已补 `core.lshape_border` / `core.lshape_border_route` 两个 V2.2 模块；`:74` APP_NAME 更新为「智能裁剪设计器V2.2」；同时修复了 `sys.path` 注入（`:67-72`）、PyInstaller <6.0 自动升级（`:248-258`）等脚本自身缺陷。
- `智能裁剪设计器V2.2.spec:8/42` 的 hiddenimports 与 name 与脚本一致，两处配置同源。
- **真机产物验证**：`dist/智能裁剪设计器V2.2.exe` 已于 2026-09-10 11:09 生成（build/ 同时存在），说明打包已实际跑通，不再是"脚本层面修好、没人执行"的状态。
- **残留建议**：① 在 V2.2.exe 上对 L 形挖角做一次端到端冒烟（V2.2 主卖点，此前从未在发布版验证过）；② `packageV2.1.2.py` 与 V2.2 脚本并存，建议归档旧脚本避免误用。

### 2.2 image_ops.py cut 角修复（b607e3a）—— 方向正确，评估通过 ✅

- 原 bug：四角分支复用 `cut_top_y/cut_right_x`（按 bl 角计算的坐标），br/tl/tr 三角实际切错区域；修复后每角独立计算 `_top/_left/_right/_bottom`，并抽出公共 `_extra & ~inner_mask` 合并，逻辑正确、无死代码残留。
- 注意事项（既有风险延续，非新增）：`_top = _ir_b - _ch` 等坐标在 `cut_w/cut_h` 大于 inner_rect 对应边时可为负值，numpy 切片负索引会静默取错区域。建议后续补 `max(0, ...)`/`min(H, ...)` 钳制 + 单测。

### 2.3 cropper worker 生命周期修复（fdc8602）—— 方向正确，但**引入了必现回归（新 P0）** ⚠️

**修复动机正确**：原实现在回调里直接 `self._worker = None` → GC 析构无 parent 的 QThread wrapper → 若线程尚未完全结束 → C++ 堆损坏（0xC0000409，退出码 -1073740791）。fdc8602 采用 finished→deleteLater 让事件循环在线程结束后释放对象，思路正确。

**但实现与所声称"照搬 canvas_widget.py 已验证的 _retire_worker 模式"存在两处关键差异，导致必现崩溃**：

| 差异点 | canvas_widget（已验证） | cropper_panel（新实现） |
|---|---|---|
| 创建 worker 时是否连 `finished → deleteLater` | 否（`:197-203` 只连业务信号，退役由 `_retire_worker` 统一处理） | 是（`cropper_panel.py:697, 1040`） |
| 入口是否有 `isRunning()` 前置守卫 | 无（`:171` 直接 `_retire_worker()`） | 有（`:661, 664, 1023, 1067` 四处） |

**崩溃机理**（crash.log 2026-09-10 11:24:19~37 连续 7 条实证）：

1. 第一次匹配/预览完成后，`finished → deleteLater` 的 DeferredDelete 事件被事件循环处理 → **C++ AutoMatchWorker/CropWorker 已销毁**，但 `self._match_worker`/`self._worker` 仍持有 Python wrapper（新实现刻意不再置 None）。
2. 用户第二次点击匹配/预览/导出 → 入口守卫 `self._match_worker.isRunning()`（`:661`，`:1023`/`:1067` 同理）→ **`RuntimeError: wrapped C/C++ object of type AutoMatchWorker has been deleted`** → 功能每次都失效。
3. 即使守卫被绕过，`_retire_match_worker`/`_retire_worker` 内部的 `old.isRunning()`（`:977, :992`）与 `shutdown` 的 `w.isRunning()`（`:1010, :1013`）对已删除 wrapper 同样会抛该异常。

**影响**：崩溃行为从原"多次运行后偶发 0xC0000409"变为"**第二次操作必现 RuntimeError**"，且当前测试套件全部通过（tests/gui/ 为空），该回归测试期无法暴露——用户 11:23 提交、11:24 即连续触发 7 次，已证实。

**推荐修复（三选一，按推荐序）**：

1. **完全照搬 canvas 模式**：① 删除四处入口守卫（`:661/:664/:1023/:1067`）；② 创建 worker 时移除 `:697/:1040` 的 `finished → deleteLater` 连接，退役统一交给 `_retire_worker`/`_retire_match_worker` 处理；③ `_on_match_done/_on_preview_done/_on_export_done/_on_match_err/_on_crop_error` 保持不置 None（与 canvas 一致），由下次 `_retire_*` 清引用。干净、与已验证范式完全对齐。
2. **安全访问器兜底**：所有 `isRunning()` 访问改走 `_safe_running(w)`（`try: return w.isRunning() except RuntimeError: return False`），保留现有结构。改动最小但 wrapper 悬垂仍在，后续易踩坑。
3. **引用转移（最稳妥的防御性方案）**：回调里恢复置 None，但先把 wrapper 移入面板级存活列表（`self._retired_workers.append(old)`），待 `finished → deleteLater` 处理后移除（`finished.connect(lambda w=old: self._retired_workers.remove(w))`），彻底杜绝"Python 引用与 C++ 生命周期错配"。

无论选哪种，**都应补一条 GUI 层回归测试**（`QT_QPA_PLATFORM=offscreen` + 连续两次启动/退役 worker），把"第二次操作"这类回归纳入 CI。

### 2.4 main.py 退出链 —— 评估通过 ✅

`closeEvent` 在 `canvas.shutdown()` 前接入 `cropper.shutdown()`（`:461-466`），`aboutToQuit` 同步连接（`:526-527`）。两次触发（closeEvent + aboutToQuit）因 `_retire_*` 对 `None` 幂等返回而安全；`shutdown` 在主线程 wait 最多 3s，可接受。唯一依赖 2.3 的修复（内部 `isRunning()` 同样需防 RuntimeError）。

---

## 三、上轮问题清单复检状态

### P0 —— 发布阻断 / 崩溃风险

| # | 上轮问题 | 状态 | 复检证据 |
|---|---|---|---|
| P0-00 | PyInstaller 漏收 lshape_border 两模块 + 版本号未更新 | ✅ **已修复** | packageV2.2.py:74,111-112；spec:8,42；dist/V2.2.exe 已生成（11:09） |
| P0-01 | 路由回退链断裂（V13 失败不回退 Profile/旧路径） | ❌ 仍存在 | lshape_border.py:546-559,594-607（未改动） |
| P0-02 | 大图路径多处全图 float64，与 2 亿像素宣称冲突 | ❌ 仍存在 | border.py:92-94,344；mask.py:510,729（未改动） |
| P0-03 | 多层嵌套矩形检测结果被丢弃（`pass` 分支） | ❌ 仍存在 | border.py:369-371；mask.py:297-298（未改动；detect_nested_rect_layers 仍无消费方） |
| P0-04 | OCR 循环无真实 deadline（最坏 79 次 × 20s） | ❌ 仍存在 | vision.py:445-546 未改动；deadline 仍仅步骤边界检查（sketch_parser.py:554-563,93-94） |
| P0-05 | 面板侧 5 类 worker 未接入退役协议 | 🔶 **部分修复** | CropperPanel 已接入（fdc8602）；**PropertyPanel（_pool_worker/_warmup_worker/_sketch_parse_worker）与 LShapePanel（_lshape_parse_worker）仍未接入**（generate.py:142、poolbox.py:307-316,655-656、lshape_panel.py:489-494 未改动）；且 2.3 回归使 CropperPanel 部分暂不可用 |
| P0-06 | TemplateMatcher 共享实例无锁 | ❌ 仍存在 | template_matcher.py（未改动） |
| P0-07 | 预热信号竞态（永久"⏳" / 双 worker） | ❌ 仍存在 | generate.py:69-83,87-143（未改动） |
| P0-08 | 跨线程信号连接纯 Python 回调（线程里弹框/改 UI） | ❌ 仍存在 | generate.py:74-79,137-141 未改动；**cropper_panel.py:1093 导出分支 lambda→_on_export_done（弹 QMessageBox）仍在**（fdc8602 未动此行） |

### P1 —— 正确性与健壮性（全部仍存在，文件未改动，证据同上一份报告）

| # | 问题 | 状态 |
|---|---|---|
| P1-01 | 小图直边采样越界/负索引（mask.py:836-866） | ❌ 仍存在 |
| P1-02 | L 形数量级修正错改对象（lshape_sketch_parser.py:980-990,1047-1076） | ❌ 仍存在 |
| P1-03 | 350px 间距死限不随 scale 缩放（numbers.py:554-560,622-654） | ❌ 仍存在 |
| P1-04 | 像素→厘米几何校验死代码（margins.py:586-626） | ❌ 仍存在 |
| P1-05 | 无边框素材切边裸边（image_ops.py:1131-1143,1227-1228；b607e3a 未触及此逻辑） | ❌ 仍存在 |
| P1-06 | 三条路由 scale 公式/补边方向不一致（lshape_border.py:617,708；route:568-569） | ❌ 仍存在 |
| P1-07 | 草图上传主线程解码大图（poolbox.py:560-564） | ❌ 仍存在 |
| P1-08 | GUI 线程模板库全扫描卡死（generate.py:297-401） | ❌ 仍存在 |
| P1-09 | 截断最末层分支不可达（detection.py:134-163） | ❌ 仍存在 |
| P1-10 | gap 清理冗余全 ROI 扫描 + sector_render 死代码（sector_render.py:367-375,464-471） | ❌ 仍存在 |

### P2 —— 维护性（除 P2-12 部分修复外全部仍存在）

| # | 问题 | 状态 |
|---|---|---|
| P2-01~P2-11 | README 漂移/防抖死代码/魔法数字/箭头映射重复键/静默吞异常/多洞缓存缺失/pool_mode 不一致/PSD 加载/LOD deepcopy 等 | ❌ 仍存在（文件未改动） |
| P2-12 | 根目录卫生 | 🔶 **部分修复**：`_dbg_tl/tr/bl/br.png` 已删除；`_debug_v13.py`(10:46)、`Test-multiplhole.py` 仍在；crash.log 增加 7 条新崩溃记录（本身在 .gitignore，但建议一并归档排查） |
| P2-13 | 三套历史记录实现重复 / 1cm 损耗常量 ×3 / 导出 lambda 弹框（现并入 N0 修复范围）等 | ❌ 仍存在 |
| P2-14 | `'hua'` 子串误匹配 / print 改 logger | ❌ 仍存在 |
| P2-15 | save_jpg 未指定 subsampling | ❌ 仍存在 |

---

## 四、本轮新增问题

| # | 级别 | 问题 | 证据 | 建议 |
|---|---|---|---|---|
| N0-01 | **P0** | **cropper worker 退役修复引入必现回归**：finished→deleteLater 后 wrapper 悬垂，二次操作时入口守卫/退役方法内 `isRunning()` 对已删除 C++ 对象抛 `RuntimeError: wrapped C/C++ object ... has been deleted`；crash.log 11:24 连续 7 次实证 | cropper_panel.py:661,664,697,977,992,1023,1040,1067；crash.log:73-168 | 见 §2.3 三项修复方案，并补 GUI offscreen 回归测试 |
| N1-01 | P1 | b607e3a cut 角坐标未做 `[0,H]×[0,W]` 钳制，cut_w/cut_h 超限时负索引静默切错区域（既有风险随本次重写延续） | image_ops.py:760-788（新） | min/max 钳制 + 单测 |

---

## 五、改进路线图（更新）

**本周（紧前）**：
1. **修复 N0-01**（fdc8602 回归）——当前圆角裁剪面板的匹配/预览/导出二次操作即失效，优先级高于一切；
2. 修好后在 `dist/智能裁剪设计器V2.2.exe` 上做 L 形挖角端到端冒烟，关闭 P0-00 的最后一步验证缺口。

**近期（1-2 周）**：
3. P0-01 回退链补全（V13 失败落 Profile/旧路径）；
4. P0-05 剩余 4 类 worker（PropertyPanel ×3、LShapePanel ×1）接入同一退役协议，并为全部 worker 生命周期补 offscreen 回归测试；
5. P0-07 warmup 竞态（isRunning 复查 + 连接前先 disconnect）；
6. P0-08 跨线程回调改 QueuedConnection 的安全落点（信号参数传包装数据，不开窗弹框）。

**中期**：
7. P0-02 大图 float64 收敛（边缘带 ROI 采样）；
8. P0-03 嵌套矩形检测结果接入生产路径（或删除死检测）；
9. P0-04 OCR deadline 下沉到 vision 层循环内；
10. P0-06 TemplateMatcher 加锁。

**长期**：P1/P2 清单按上份报告执行；建议引入 GUI 冒烟测试目录（tests/gui/ 已存在但为空，README:115 与其不符的漂移一并修正）。

---

## 六、结论

用户本轮修复动作**快且准**：打包漏收已连本带利解决（脚本 + spec + 真机 V2.2.exe 三关齐过），cut 角坐标 bug 修复质量高，退出链接管方向正确。**但 fdc8602 对 canvas 模式的"照搬"不完整，引入比原缺陷更易触发的必现回归（N0-01），已在 crash.log 留下 7 条实证**——该回归应作为当前最高优先级修复项。其余上轮 P0/P1/P2 问题基本未动（除 P0-00、P0-05 部分、P2-12 部分），测试基线 320 passed 全绿但无法覆盖 GUI 层缺陷，建议按路线图补齐 worker 生命周期回归测试后，再进入下一轮功能迭代。