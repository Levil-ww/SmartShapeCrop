# SmartShapeCrop 全面代码审查报告

> **审查日期**：2026-09-12 | **版本**：V2.2 | **代码规模**：~18,000 行 Python | **测试**：444 通过

本次审查覆盖 50+ 源文件、3 个子系统（核心算法 / 解析器 / GUI），共发现 **5 个 Critical**、**15 个 High**、**15 个 Medium**、**17 个 Low** 级问题。最严峻的挑战在于核心渲染函数 `render_design` 达 848 行、OCR 数字识别函数达 747 行，以及 GUI 层 MVC 分离不足导致的业务逻辑与 UI 控件深度耦合。建议分四阶段推进重构，优先拆分超长函数和修复线程安全隐患。

---

## 目录

1. [项目概览](#一项目概览)
2. [代码规模与质量指标](#二代码规模与质量指标)
3. [Critical 级发现](#三critical-级发现)
4. [High 级发现](#四high-级发现)
5. [Medium 级发现](#五medium-级发现)
6. [架构与线程安全分析](#六架构与线程安全分析)
7. [测试覆盖分析](#七测试覆盖分析)
8. [改进路线图](#八改进路线图)

---

## 一、项目概览

SmartShapeCrop 是一款基于 PyQt5 的专业图像裁剪设计工具，主要用于水池 / L 形挖角 / 椭圆等异形裁剪的素材适配与印刷输出。项目采用 Python 3.13 + PyQt5 + Pillow + OpenCV-headless + Tesseract OCR 技术栈。

### 核心指标

| 指标 | 数值 |
|------|------|
| 总代码行数 | ~18,000 |
| Python 源文件 | 50+ |
| 测试用例数 | 444 |
| 超 80 行函数 | 57 |
| 超 200 行函数 | 8 |
| 超 400 行函数 | 3 |

### 项目目录结构

| 目录 | 职责 | 文件数 | 总行数 |
|------|------|--------|--------|
| `core/` | 核心算法（渲染 / 裁剪 / 几何 / 边框 / OCR） | 22 | ~10,500 |
| `gui/` | UI 面板 / 工作线程 / 画布 | 10 | ~5,500 |
| `tests/` | 单元测试 / 集成测试 | 25 | ~3,200 |
| `scripts/` | 调试 / 诊断 / 验证脚本 | ~80 | 大量归档 |
| `packaging/` | PyInstaller 打包脚本 | 5 | ~800 |

> **核心架构亮点**：项目已实现了较成熟的架构基础：配置集中在 `core/config.py`、跨平台路径解析器 `PathResolver`、结构化日志、全局异常钩子、QThread + 信号槽的异步渲染管线、LOD 分级预览、设计对象快照防竞态等。这些基础为后续重构提供了良好的安全网。

---

## 二、代码规模与质量指标

### 2.1 文件规模 Top 10

| 文件 | 行数 | 大小 | 评估 |
|------|------|------|------|
| `core/pool_designer/sketch_parser_multihole.py` | 1766 | 87KB | :red_circle: 需拆分 |
| `core/image_ops.py` | 1626 | 92KB | :red_circle: 需拆分 |
| `core/parser/template_matcher.py` | 1268 | 63KB | :orange: 偏大 |
| `core/pool_designer/lshape_sketch_parser.py` | 1121 | 54KB | :orange: 偏大 |
| `gui/cropper_panel.py` | 1050 | 51KB | :orange: 偏大 |
| `core/pool_designer/sketch_parser_numbers.py` | 982 | 59KB | :orange: 偏大 |
| `gui/property_panel_poolbox.py` | 944 | 57KB | :orange: 偏大 |
| `core/lshape_border.py` | 933 | 45KB | :yellow_circle: 可接受 |
| `gui/property_panel_workers.py` | 841 | 51KB | :yellow_circle: 可接受 |
| `gui/property_panel.py` | 797 | 50KB | :yellow_circle: 可接受 |

### 2.2 超长函数 Top 15（≥80 行）

| 文件 | 函数 | 行号 | 行数 | 严重性 |
|------|------|------|------|--------|
| `image_ops.py` | `render_design` | L550-1397 | **848** | :red_circle: Critical |
| `sketch_parser_numbers.py` | `_extract_direction_label_numbers` | L274-1020 | **747** | :red_circle: Critical |
| `lshape_sketch_parser.py` | `_detect_lshape_geometry` | L104-677 | **574** | :red_circle: Critical |
| `property_panel_workers.py` | `PoolRenderWorker.run` | L317-831 | **515** | :red_circle: Critical |
| `sketch_parser_multihole.py` | `_9step_multi_hole_parse` | L1487-1897 | 411 | :orange: High |
| `sketch_parser_margins.py` | `_validate_and_fix_margins` | L191-582 | 392 | :orange: High |
| `sketch_parser_multihole.py` | `_classify_hole_layout` | L136-492 | 357 | :orange: High |
| `corner/sector_render.py` | `_redraw_border_on_corner` | L157-490 | 334 | :orange: High |
| `property_panel.py` | `_build_ui` | L105-389 | 285 | :orange: High |
| `lshape_sketch_parser.py` | `_assign_labels_by_geometry` | L684-951 | 268 | :orange: High |
| `image_cropper_mask.py` | `_post_cleanup_gap_regions` | L385-647 | 263 | :orange: High |
| `cropper_panel.py` | `_build_ui` | L150-399 | 250 | :orange: High |
| `sketch_parser_multihole.py` | `_extract_arrow_direction_numbers` | L784-1029 | 246 | :orange: High |
| `sketch_parser_multihole.py` | `_divide_multi_hole_zones` | L500-723 | 224 | :orange: High |
| `corner/detection.py` | `_detect_border_layers` | L503-725 | 223 | :orange: High |

共发现 **57 个**超过 80 行的函数，其中 **3 个超过 400 行**，**8 个超过 200 行**。这些超长函数是代码维护的最大障碍。

---

## 三、Critical 级发现

### C-01: `render_design` 函数达 848 行

- **位置**：`core/image_ops.py` L550-1397
- **问题**：核心渲染入口函数包含完整流水线：整体背景填充、水池素材适配、单洞/多洞内挖填充、L 形边框补全、Stale-Decor 拗留清理、边框文字、最终输出。混合了模式判断、背景策略、素材加载、L 形切角、多洞、V13/Profile/旧路径回退等多个决策路径，圈复杂度极高，难以阅读和测试。
- **建议**：拆分为 `render_background()`、`render_pool_material()`、`render_inner_area()`、`render_lshape_border_completion()`、`render_border_text()` 等子函数，使用 RenderContext 对象承载公共参数。

### C-02: `_extract_direction_label_numbers` 函数达 747 行

- **位置**：`core/pool_designer/sketch_parser_numbers.py` L274-1020
- **问题**：单洞 OCR 方向标签解析的核心函数，包含阈值预计算、多尺度扫描（1x/2.5x/4x）、lang/psm 组合（chi_sim+eng / eng，PSM 6/4/11/12）、外框值排除、覆盖保护、同源检测、同值检测、几何冲突检测等。每条 OCR 保护规则无法单独验证，维护成本极高。
- **建议**：拆分为 `_compute_margin_caps()`、`_scan_ocr()`、`_is_reasonable_margin()`、`_try_bind_value()`、`_detect_small_number()`，将 OCR 规则整理为策略表或配置类。

### C-03: `_detect_lshape_geometry` 函数达 574 行

- **位置**：`core/pool_designer/lshape_sketch_parser.py` L104-677
- **问题**：L 形草图几何检测函数包含轮廓分析、凹角检测（凸包 + 滑窗）、方向识别、尺寸解析、自洽评分等完整流程。函数过长导致每个检测阶段难以单独测试和修改。
- **建议**：拆为轮廓分析、OCR 数值提取、几何自洽、L 形参数计算四个独立模块，每阶段返回中间结构。

### C-04: `PoolRenderWorker.run` 函数达 515 行

- **位置**：`gui/property_panel_workers.py` L317-831
- **问题**：工作线程 `run()` 方法同时负责文件名解析、模板匹配、草图解析、多洞参数、L 形参数、设计构建等完整业务流程。职责过重，无法单独测试各步骤，且 Worker 中直接组装业务对象，不符合"读取输入→计算→发送结果"原则。
- **建议**：拆成"文件名解析 / 素材匹配 / 草图解析 / 设计构建"多个子步骤，每步返回中间结果，便于单测和容错。

### C-05: TemplateMatcher RLock 可重入性风险

- **位置**：`core/parser/template_matcher.py` L799-801, L481
- **问题**：`find_best_match` 在 L799 调用 `self._lock.acquire()`，随后调用 `_find_best_match_impl`，其内部又调用 `scan_library()`，后者在 L481 再次 `acquire()`。使用 RLock 不会死锁，但内部调用链可能修改 `self._cache` / `self._idx_*`，外部调用者在同一期间可能看到中间状态。
- **建议**：改用 `with self._lock:` 模式，或区分公开方法（加锁）和内部方法（不加锁），明确调用层级。

---

## 四、High 级发现

### 4.1 核心算法模块

#### H-01: `compute_border_bands` 内存开销大

- **位置**：`core/geometry.py` L472-558, L694-777
- **问题**：多层边框计算为每一层创建新的 PIL mask 并使用双 mask 差集。L 形模式还会重复构建 L 形 mask。导出大图（2 亿像素上限）时内存峰值显著增加。
- **建议**：先构建 frame mask 集合，再做布尔运算；避免重复创建完整尺寸 PIL mask。

#### H-02: `_build_multi_layer_corner_mask` 分支过多

- **位置**：`core/image_cropper_mask.py` L57-273
- **问题**：同时处理 normal mask、protect content、间隙层扣除、ring_region 保护、border_zone 裁剪等多套条件，新增保护模式时容易改变其他模式的裁剪行为。
- **建议**：用策略模式拆分：`normal_mask_builder`、`content_protect_builder`、`gap_protect_builder`。

#### H-03: `_redraw_border_on_corner` 过长（334 行）

- **位置**：`core/corner/sector_render.py` L157-490
- **问题**：包含 ROI 提取、深度计算、间隙层判定、内容保护 mask、beyond_arc 清理、逐层绘制等完整流程。核心渲染逻辑集中在一个函数中。
- **建议**：拆分为 `build_roi()`、`build_border_depth_map()`、`build_content_protection_mask()`、`render_border_layers_in_roi()`。

#### H-04: `apply_lshape_border_completion` 路由逻辑过长

- **位置**：`core/lshape_border.py` L465-679
- **问题**：统一处理手动覆盖、Profile 检测、Profile 让位 V13、V13 路径、旧路径等多条路径，新增检测器时容易误伤回退顺序。
- **建议**：采用注册表模式管理检测器/绘制器，将路由策略拆为独立模块。

#### H-05: `classify_gap_layers` 判定逻辑过长

- **位置**：`core/corner/detection.py` L240-328
- **问题**：统一间隙层判定包含最内层、厚度上限、最外层深色、中间层 sandwich、浅色外层 sentinel 等多套判断。任一规则调整都可能影响圆角补边。
- **建议**：拆为 `is_innermost()`、`is_too_thick()`、`is_sentinel_dark()`、`is_sandwich_gap()` 等独立函数。

### 4.2 解析器模块

#### H-06: 多洞解析主编排函数 411 行

- **位置**：`core/pool_designer/sketch_parser_multihole.py` L1487-1897
- **问题**：`_9step_multi_hole_parse` 包含 OCR 扫描、空间绑定、候选生成、几何验证、自洽评分等多个阶段，难以单独测试。
- **建议**：拆分为 `_collect_ocr_data`、`_spatial_bind_ocr`、`_evaluate_candidates`、`_select_best_assignment`。

#### H-07: 边距校验修复函数 392 行

- **位置**：`core/pool_designer/sketch_parser_margins.py` L191-582
- **问题**：`_validate_and_fix_margins` 包含负边距清零、target 权威外框、方向标签反推、几何守恒、比例缩放、异常重写等大量分支。
- **建议**：拆分为 `_sanitize_margin`、`_ensure_target_outer`、`_fix_horizontal_outer`、`_fix_vertical_outer` 等。

#### H-08: 缓存键仅依赖 mtime，缺内容哈希

- **位置**：`core/pool_designer/sketch_parser_cache.py` L58-64
- **问题**：缓存键为 `(image_path, mtime, target_w, target_h, algo_version)`。文件内容不变但 mtime 变化时缓存失效；mtime 相同但内容变化时可能使用过期缓存。
- **建议**：引入图像内容哈希（如 pHash 或前 NKB 哈希）作为缓存键的补充部分。

#### H-09: OCR 魔法值散落各处

- **位置**：`sketch_parser_numbers.py`、`sketch_parser_multihole.py` 全文
- **问题**：0.03、0.25、0.05、0.02、10.0 等阈值散落在函数体内，无集中定义和来源注释。
- **建议**：集中定义为常量（如 `MIN_HOLE_AREA_RATIO`、`GAP_VETO_HOLE_RATIO`），并添加来源注释。

### 4.3 GUI 与架构

#### H-10: MVC 分离不足，业务逻辑混入 UI 层

- **位置**：`gui/property_panel_generate.py` L35-250; `gui/property_panel_layers.py` L68-311
- **问题**：`_collect()` 直接读取 SpinBox/ComboBox/颜色按钮等 UI 控件组装设计对象，`_on_pool_finished_ok()` 同时执行设计写回、SpinBox 覆盖、模式处理、边距记录。业务规则（文件名解析、模板匹配、草图解析）集中在 GUI mixin 中。
- **建议**：引入 `DesignModel` 中间层，Panel 只负责展示和收集输入；素材匹配、草图解析等业务下沉到 service 层。

#### H-11: main.py 直接操作 Panel 私有控件

- **位置**：`main.py` L275-303
- **问题**：`_sync_panel_from_design()` 将设计值写回多个 Panel 的 `_sp_w`、`_sp_h`、`_cb_mode` 等私有控件，主窗口与 Panel 内部实现深度耦合。
- **建议**：下放给各 Panel 内部的 `sync_from_design(design)` 方法，主窗口只传递模型变更。

#### H-12: AutoMatchWorker 缺少取消回调

- **位置**：`gui/cropper_panel.py` L69-119
- **问题**：`AutoMatchWorker.run()` 中直接调用 `_matcher.scan_library(force=False)`，没有传入 `check_cancel` 回调。长库扫描时用户取消不彻底，扫描在后台继续运行。
- **建议**：添加 `check_cancel=self.isInterruptionRequested` 参数到 scan_library 调用。

#### H-13: LShapePanel 与 PropertyPanel 信号耦合重

- **位置**：`gui/property_panel.py` L408-478
- **问题**：通过 `set_lshape_panel()` 注入引用并连接大量信号。LShapePanel 定义多组委托信号和自有信号，对外契约较重。
- **建议**：提取 `LShapePanelBridge` 适配器，减少直接信号数量，合并为 `lshape_action_requested(action, params)`。

#### H-14: `_SketchDecodeWorker` 中断检查语义混淆

- **位置**：`gui/property_panel_workers.py` L48, L55, L59
- **问题**：`self.isInterruptionRequested()` 来自 QThread，但类内成员和 `self` 语义容易混淆 Worker 对象与 Thread 对象。
- **建议**：显式使用 `self.isInterruptionRequested()` 并添加注释，或改为外部 Thread 控制。

#### H-15: 打包脚本历史版本堆积

- **位置**：`packaging/` 目录
- **问题**：存在 `package.py`、`packageV2.0.py`、`packageV2.1.py`、`packageV2.2.py`、`packageV2.1.2.py` 五个脚本，README 说明唯一入口为 `packageV2.1.2.py`。旧脚本可能基于旧目录结构，误用会导致路径错误。
- **建议**：保留 `packageV2.1.2.py` 为唯一入口，其余移入 `packaging/legacy/` 归档。

---

## 五、Medium 级发现

以下列出最具代表性的 Medium 级问题，完整清单共 33 项。

| 编号 | 模块 | 问题 | 建议 |
|------|------|------|------|
| M-01 | `image_ops.py` | 0.5px 边距容差可能导致切边 bbox 与像素网格不对齐 | 统一使用 `int(round(...))` 对齐 |
| M-02 | `geometry.py` | L 形退化为矩形时边框 band 一致性未测试 | 增加 `cut_w=0` 退化测试 |
| M-03 | `image_cropper.py` | `apply_rounded_corners` 嵌套条件多 | 拆为 mask/border/contour 三步 |
| M-04 | `lshape_border_route.py` | `_classify_profile` 状态切换复杂 | 拆为 collect/align/truncate/group 四阶段 |
| M-05 | `detection.py` | `_enforce_border_thickness_caps` 规则多 | 每个规则抽取为独立 validator |
| M-06 | `sector_render.py` | beyond_arc 二次清理可能重复 | 合并为一次清理或明确安全含义 |
| M-07 | `algorithm.py` | `carve_corner_on_mask` normal/inverse 混用 | 拆为两个独立函数 |
| M-08 | `template_matcher.py` | 磁盘缓存格式 .pickle/.json 混用 | 统一为单一格式 |
| M-09 | `name_parser.py` | 异常文件名（逗号分隔、单位混用）覆盖不足 | 增加边界用例测试 |
| M-10 | `sketch_parser_cache.py` | FIFO 淘汰策略简单，容量固定 50 | 改为 OrderedDict + 可配置容量 |
| M-11 | `property_panel_poolbox.py` | `_on_pool_target_changed` 职责过多 | 拆为文件名解析/回填/历史同步 |
| M-12 | `property_panel_generate.py` | `_on_pool_finished_ok` 163 行 | 先返回标准化 design，再统一 sync |
| M-13 | `cropper_panel.py` | `textChanged` 无 debounce | 添加 200ms debounce |
| M-14 | `lshape_panel.py` | `_apply_lshape_params` 职责重 | 拆为保存/回填/摘要/触发 |
| M-15 | `app_settings.py` | 配置策略集中且硬编码 | 拆为独立 service，参数可配置 |

---

## 六、架构与线程安全分析

### 6.1 模块依赖链

当前核心模块依赖链为：`geometry` → `image_ops` → `lshape_border` → `corner`。`render_design` 调用 geometry 生成 mask、调用 lshape_border 补全边框、调用 corner 处理圆角和边框重绘。模块间直接 import 较多，建议使用 RenderContext 承载公共参数。

| 模块组 | 耦合度 | 风险 |
|--------|--------|------|
| `image_cropper` / `image_cropper_mask` / `image_cropper_border` | 高 | 三文件均从 corner 子包导入检测/mask/渲染函数 |
| `lshape_border` / `lshape_border_route` | 中 | 路由策略与绘制实现混合 |
| `property_panel` / `lshape_panel` | 高 | 通过引用注入 + 大量信号直接连接 |
| `main.py` → 各 Panel | 高 | 主窗口直接访问 Panel 私有属性 |

### 6.2 线程安全评估

| Worker | 退役模式 | 取消支持 | 评估 |
|--------|----------|----------|------|
| `PreviewRenderWorker` | requestInterruption + wait + deleteLater | ✅ 已实现 | ✅ 安全 |
| `ExportSaveWorker` | 同上 + design.clone() 快照 | ✅ 已实现 | ✅ 安全 |
| `CropWorker` | 同上 | ✅ 已实现 | ✅ 安全 |
| `PoolRenderWorker` | 同上 | ✅ 已实现 | ⚠️ 职责过重 |
| `AutoMatchWorker` | 同上 | ❌ 缺失 | 🔴 需修复 |
| `_SketchDecodeWorker` | 同上 | ✅ 已实现 | ⚠️ 语义混淆 |
| `_LShapeParseWorker` | 同上 | ✅ 已实现 | ✅ 安全 |
| `TemplateMatcher` | RLock 保护公开方法 | — | 🔴 可重入性风险 |

> **线程安全总结**：QThread 退役模式整体正确——所有 Worker 都通过 `requestInterruption()` + `wait()` + `deleteLater()` 安全退役。`closeEvent` 和 `app.aboutToQuit` 双重接管所有后台线程。主要风险点：**(1)** `AutoMatchWorker` 缺少 `check_cancel` 回调；**(2)** `TemplateMatcher` 的 RLock 可重入性可能导致中间状态泄露。

---

## 七、测试覆盖分析

| 指标 | 数值 |
|------|------|
| 通过测试 | 444 |
| 失败测试 | 0 |
| 执行耗时 | 50.4s |
| 测试文件 | 25 |

### 7.1 覆盖矩阵

| 模块 | 测试文件 | 用例数 | 覆盖评估 | 盲区 |
|------|----------|--------|----------|------|
| `template_matcher.py` | test_template_matcher.py | 16 | ✅ 良好 | 并发调用测试 |
| `name_parser.py` | test_name_parser.py | 34+ | ✅ 良好 | 异常文件名、超大数值 |
| `sketch_parser_multihole.py` | test_multi_hole_parser.py | 30 | ✅ 良好 | 3 洞以上、OCR 严重噪声 |
| `sketch_parser_margins.py` | test_sketch_parser_logic.py | 21 | ✅ 良好 | 方向锁定、target 反推冲突 |
| `lshape_sketch_parser.py` | test_lshape_sketch_parser.py | 7 | ⚠️ 一般 | 非标准 L 形、OCR 低置信度 |
| `image_ops.py` | test_image_ops_p2.py | 14 | ⚠️ 一般 | render_design 主路径未覆盖 |
| `lshape_border.py` | test_lshape_border.py | 24 | ✅ 良好 | — |
| `corner/detection.py` | test_rounded_corner.py | 22 | ✅ 良好 | — |
| `sketch_parser_numbers.py` | — | 0 | 🔴 缺失 | 全部 OCR 规则 |
| `sketch_parser_cache.py` | — | 0 | 🔴 缺失 | 命中/淘汰/并发 |
| `sketch_parser_vision.py` | — | 0 | 🔴 缺失 | 图像预处理/ROI |
| `sketch_parser_base.py` | — | 0 | ⚠️ 缺失 | 文件校验/格式拒绝 |
| GUI 模块 | test_gui_smoke / test_main_window 等 | ~40 | ⚠️ 一般 | 业务逻辑路径 |

> **测试覆盖关键缺口**：`sketch_parser_numbers.py`（747 行 OCR 核心函数）和 `sketch_parser_cache.py` 完全没有独立单元测试，是最大的覆盖盲区。`render_design`（848 行）的端到端渲染路径也缺少直接测试覆盖。

---

## 八、改进路线图

基于发现项的严重程度和依赖关系，建议分四阶段推进重构。每阶段应保持 444 个测试全绿。

### 阶段 1：立即处理（1-2 周）— Critical 修复

- **C-01**: 拆分 `render_design`（848 行）为 5-7 个子函数，使用 RenderContext 承载参数
- **C-02**: 拆分 `_extract_direction_label_numbers`（747 行）为 OCR 扫描框架 + 规则策略表
- **C-03**: 拆分 `_detect_lshape_geometry`（574 行）为四阶段独立模块
- **C-04**: 拆分 `PoolRenderWorker.run`（515 行）为子步骤，每步返回中间结果
- **C-05**: 修复 `TemplateMatcher` RLock 可重入性，改用 `with self._lock:` 模式
- **H-12**: 为 `AutoMatchWorker` 添加 `check_cancel` 回调
- **H-14**: 修复 `_SketchDecodeWorker` 中断检查语义

### 阶段 2：短期优化（2-4 周）— High 级函数拆分与架构

- 拆分 `_9step_multi_hole_parse`、`_validate_and_fix_margins`、`_classify_hole_layout` 等超长函数
- 拆分 `_redraw_border_on_corner`（334 行）和 `classify_gap_layers`
- 引入 `DesignModel` 中间层，将 `_collect()` 从 UI 控件读取改为 Model 驱动
- 将 `_sync_panel_from_design()` 下放到各 Panel 内部
- 提取 LShapePanel 桥接适配器，减少 Panel 间直接引用
- 集中 OCR 魔法值为常量配置
- 清理打包目录历史脚本

### 阶段 3：中期改进（1-2 月）— 测试补齐与缓存优化

- 为 `sketch_parser_numbers.py` 补充 OCR 规则单元测试（`_merge_split_decimals`、`_extract_direction_label_numbers`）
- 为 `sketch_parser_cache.py` 补充命中/淘汰/并发测试
- 为 `sketch_parser_vision.py` 补充图像预处理测试
- 重构缓存策略：引入图像内容哈希、OrderedDict 淘汰、可配置容量
- 为 `render_design` 拆分后的子函数补充端到端渲染测试
- 补充 TemplateMatcher 并发压测用例

### 阶段 4：长期重构（持续）— 架构升级

- 建立统一事件总线，替代 Panel 间直接引用和信号连接
- 将 Worker 层从 GUI 中拆出，使业务逻辑可独立测试
- 引入代码复杂度监控（如 radon），防止文件/函数规模再次增长
- 将 L 形边框路由改为注册表模式
- 统一 0.5px 边界容差、像素对齐策略
- 为真实素材增加 snapshot 回归测试
- 统一异常处理策略，区分用户可纠正错误与系统错误

---

*审查方法：通过 3 个并行审查代理分别分析核心算法模块、解析器模块和 GUI 层，结合 AST 函数长度分析和 444 个测试用例运行验证。审查覆盖 50+ 源文件，~18,000 行 Python 代码。*

*审查工具：Python ast 模块（函数长度分析）、pytest（测试验证）、人工代码审查（3 个维度并行）。*

*注意：本报告基于 2026-09-12 的代码快照。后续代码变更可能影响发现项的有效性。*
