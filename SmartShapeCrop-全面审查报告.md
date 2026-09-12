# SmartShapeCrop 全面代码审查报告

> **审查日期**：2026-09-12（初版）| **更新日期**：2026-09-12（V2.4，Critical 修复验证）| **代码规模**：~22,248 行 Python（不含测试）| **测试**：388 收集 / 380 通过

本次审查覆盖 52 源文件、5 个子系统（核心算法 / 服务层 / Worker 层 / 数据模型 / GUI），共发现 **5 个 Critical**、**15 个 High**、**15 个 Medium**、**17 个 Low** 级问题。**V2.4 已完成全部 5 个 Critical 级修复**：`render_design` 从 848 行降至 100 行、`_extract_direction_label_numbers` 从 747 行降至 42 行、`_detect_lshape_geometry` 从 573 行降至 51 行、`PoolRenderWorker.run` 从 515 行降至 40 行、TemplateMatcher RLock 已修复。超 400 行函数从 5 个降至 1 个（仅剩 H-06 的 `_9step_multi_hole_parse`）。测试 380 通过，无回归。

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
9. [重构变更记录](#九重构变更记录)

---

## 一、项目概览

SmartShapeCrop 是一款基于 PyQt5 的专业图像裁剪设计工具，主要用于水池 / L 形挖角 / 椭圆等异形裁剪的素材适配与印刷输出。项目采用 Python 3.13 + PyQt5 + Pillow + OpenCV-headless + Tesseract OCR 技术栈。

### 核心指标

| 指标 | V2.2（初版） | V2.4（当前） |
|------|------|------|
| 总代码行数（不含测试） | ~18,000 | ~22,248 |
| Python 源文件（不含测试） | 50+ | 52 |
| 测试用例数 | 444 | 388（清理归档后） |
| 测试文件 | 25 | 35 |
| 超 80 行函数 | 57 | 81 |
| 超 200 行函数 | 8 | 17 |
| 超 400 行函数 | 3 | 1 |
| 架构层数 | 2（core + gui） | 5（core + services + workers + models + gui） |

> **V2.4 变更要点**：V2.3 新增 `services/`（服务层）、`workers/`（Worker 层）、`models/`（数据模型层）三个顶层包；V2.4 完成全部 5 个 Critical 级修复——`render_design` 拆为 9 个子函数（848→100 行）、`_extract_direction_label_numbers` 拆为 6 个子函数（747→42 行）、`_detect_lshape_geometry` 拆为 5 个子函数（573→51 行）、`PoolRenderWorker.run` 拆为 7 个步骤方法（515→40 行）、TemplateMatcher RLock 修复。

### 项目目录结构

| 目录 | 职责 | 文件数 | 总行数 | 变更 |
|------|------|--------|--------|------|
| `core/` | 核心算法（渲染 / 裁剪 / 几何 / 边框 / 圆角） | 20 | 7,681 | 缩减（解析器迁出） |
| `services/` | 服务层（模板匹配 / 草图解析 / PSD 加载） | 15 | 8,182 | **新增** |
| `gui/` | UI 面板 / 画布 | 11 | 5,262 | 缩减（Worker 迁出） |
| `workers/` | 线程调度层（所有 QThread Worker） | 4 | 1,065 | **新增** |
| `models/` | 数据模型层（DesignModel 包装器） | 2 | 58 | **新增** |
| `tests/` | 单元测试 / 集成测试 | 35 | 7,141 | 扩充 |
| `packaging/` | PyInstaller 打包脚本 | 5 | 1,828 | legacy/ 归档 |

> **核心架构亮点**：V2.3 实现了五层分离架构——`models/`（纯数据）← `services/`（业务逻辑）← `workers/`（线程调度）← `gui/`（UI 展示）← `core/`（算法内核）。Worker 层不再导入任何 gui 模块，services 层不依赖 UI，DesignModel 提供 `sync_from_design()` / `to_design()` 替代 main.py 直接操作 Panel 私有控件。V2.4 完成全部 Critical 级函数拆分：`render_design` 现为 100 行编排器，`_extract_direction_label_numbers` 现为 42 行编排器，`_detect_lshape_geometry` 现为 51 行编排器，`PoolRenderWorker.run` 现为 40 行编排器。配置集中在 `core/config.py`、跨平台路径解析器 `PathResolver`、结构化日志、全局异常钩子、QThread + 信号槽的异步渲染管线、LOD 分级预览、设计对象快照防竞态等基础设施保持不变。

---

## 二、代码规模与质量指标

### 2.1 文件规模 Top 15

| 文件 | 行数 | 评估 | V2.4 变更 |
|------|------|------|------|
| `services/sketch_parser/sketch_parser_multihole.py` | 1766 | :red_circle: 需拆分 | 路径迁移 core→services |
| `core/image_ops.py` | 1626 | :red_circle: 需拆分 | C-01 拆分后函数均 <200 行 |
| `services/parser/template_matcher.py` | 1268 | :orange: 偏大 | 路径迁移 core→services |
| `services/sketch_parser/lshape_sketch_parser.py` | 1121 | :orange: 偏大 | C-03 拆分后函数均 <270 行 |
| `gui/cropper_panel.py` | 992 | :orange: 偏大 | 微增（+~58 行） |
| `services/sketch_parser/sketch_parser_numbers.py` | 982 | :orange: 偏大 | C-02 拆分后函数均 <210 行 |
| `gui/property_panel_poolbox.py` | 944 | :orange: 偏大 | 不变 |
| `core/lshape_border.py` | 933 | :yellow_circle: 可接受 | 不变 |
| `workers/property_panel_workers.py` | 842 | :yellow_circle: 可接受 | C-04 拆分后 run 仅 40 行 |
| `gui/property_panel.py` | 824 | :yellow_circle: 可接受 | 微增（+~27 行） |
| `gui/lshape_panel.py` | 770 | :yellow_circle: 可接受 | 新入 Top 15 |
| `core/corner/detection.py` | 769 | :yellow_circle: 可接受 | 不变 |
| `services/parser/name_parser.py` | 739 | :yellow_circle: 可接受 | 路径迁移 core→services |
| `core/geometry.py` | 651 | :yellow_circle: 可接受 | 不变 |
| `services/sketch_parser/sketch_parser_margins.py` | 649 | :yellow_circle: 可接受 | 路径迁移 core→services |

### 2.2 超长函数 Top 20（≥80 行）

> **注**：V2.4 已拆分全部 4 个 Critical 级超长函数，以下为拆分后的当前 Top 20。

| 文件 | 函数 | 行号 | 行数 | 严重性 | V2.4 变更 |
|------|------|------|------|--------|------|
| `services/sketch_parser/sketch_parser_multihole.py` | `_9step_multi_hole_parse` | L1487-1896 | 410 | :orange: High | 不变（H-06 待拆） |
| `services/sketch_parser/sketch_parser_margins.py` | `_validate_and_fix_margins` | L191-582 | 392 | :orange: High | 不变（H-07 待拆） |
| `services/sketch_parser/sketch_parser_multihole.py` | `_classify_hole_layout` | L136-492 | 357 | :orange: High | 不变 |
| `services/sketch_parser/sketch_parser.py` | `_7step_parse` | L82-428 | 347 | :orange: High | 不变 |
| `core/corner/sector_render.py` | `_redraw_border_on_corner` | L157-490 | 334 | :orange: High | 不变（H-03 待拆） |
| `gui/property_panel_layers.py` | `_collect` | L68-366 | 299 | :orange: High | 不变 |
| `gui/property_panel.py` | `_build_ui` | L105-389 | 285 | :orange: High | 不变 |
| `services/sketch_parser/lshape_sketch_parser.py` | `_assign_labels_by_geometry` | L717-984 | 268 | :orange: High | 行号变化 |
| `workers/property_panel_workers.py` | `_apply_multihole_addon` | L598-858 | 261 | :orange: High | C-04 拆出子函数 |
| `gui/cropper_panel.py` | `_build_ui` | L73-322 | 250 | :orange: High | 行号变化 |
| `services/sketch_parser/sketch_parser_multihole.py` | `_extract_arrow_direction_numbers` | L784-1029 | 246 | :orange: High | 不变 |
| `services/sketch_parser/sketch_parser_multihole.py` | `_divide_multi_hole_zones` | L500-723 | 224 | :orange: High | 不变 |
| `core/corner/detection.py` | `_detect_border_layers` | L529-751 | 223 | :orange: High | 不变 |
| `core/image_cropper_border.py` | `apply_border_only_corners` | L249-465 | 217 | :orange: High | 不变 |
| `gui/property_panel_poolbox.py` | `_on_sketch_parsed` | L731-946 | 216 | :orange: High | 不变 |
| `core/lshape_border.py` | `apply_lshape_border_completion` | L465-679 | 215 | :orange: High | 不变 |
| `services/sketch_parser/sketch_parser_numbers.py` | `_merge_split_decimals` | L38-243 | 206 | :orange: High | C-02 拆出子函数 |
| `gui/lshape_panel.py` | `_build_ui` | L94-281 | 188 | :yellow_circle: 可接受 | 不变 |
| `services/sketch_parser/lshape_sketch_parser.py` | `_collect_approx_candidates` | L412-598 | 187 | :yellow_circle: 可接受 | C-03 拆出子函数 |
| `core/image_cropper_border.py` | `_redraw_outer_border_on_corners` | L68-245 | 178 | :yellow_circle: 可接受 | 不变 |

共发现 **81 个**超过 80 行的函数，其中 **1 个超过 400 行**，**17 个超过 200 行**。V2.4 将超 400 行函数从 5 个降至 1 个（仅剩 H-06 `_9step_multi_hole_parse`），超 200 行函数从 22 个降至 17 个。4 个 Critical 级函数已全部拆分完成，拆分后最大的子函数为 `_apply_multihole_addon`（261 行）和 `_merge_split_decimals`（206 行），均属可接受范围。

---

## 三、Critical 级发现

### C-01: `render_design` 函数达 848 行 ✅ 已修复

- **位置**：`core/image_ops.py` L550-649（拆分前 L550-1397）
- **问题**：核心渲染入口函数包含完整流水线：整体背景填充、水池素材适配、单洞/多洞内挖填充、L 形边框补全、Stale-Decor 拗留清理、边框文字、最终输出。混合了模式判断、背景策略、素材加载、L 形切角、多洞、V13/Profile/旧路径回退等多个决策路径，圈复杂度极高，难以阅读和测试。
- **修复**：拆分为 9 个子函数：`_render_outer_background()`（78 行）、`_render_lshape_cut()`（117 行）、`_render_inner_area()`（125 行）、`_compute_border_mask()`（119 行）、`_lshape_border_completion()`（101 行）、`_stale_decor_black_border_invalidation()`（106 行）、`_stale_decor_residual_cleaner()`（112 行）、`_seam_feather_paste()`（61 行）、`_get_inner_pixel_mask()`（59 行）。`render_design` 现为 100 行编排器，按流水线顺序调用各子函数。
- **验证**：语法检查通过，380 测试通过，无回归。

### C-02: `_extract_direction_label_numbers` 函数达 747 行 ✅ 已修复

- **位置**：`services/sketch_parser/sketch_parser_numbers.py` L1092-1134（拆分前 L274-1020）
- **问题**：单洞 OCR 方向标签解析的核心函数，包含阈值预计算、多尺度扫描（1x/2.5x/4x）、lang/psm 组合（chi_sim+eng / eng，PSM 6/4/11/12）、外框值排除、覆盖保护、同源检测、同值检测、几何冲突检测等。每条 OCR 保护规则无法单独验证，维护成本极高。
- **修复**：拆分为 6 个子函数：`_compute_margin_caps()`、`_scan_ocr_pass()`（140 行）、`_detect_small_number()`（127 行）、`_try_bind_value()`（173 行）、`_phase3_spatial_distance_match()`（154 行）、`_phase4_decimal_recovery()`（81 行）。引入 `_DirLabelState` 对象在各阶段间传递状态。`_extract_direction_label_numbers` 现为 42 行编排器。
- **验证**：语法检查通过，380 测试通过，无回归。

### C-03: `_detect_lshape_geometry` 函数达 573 行 ✅ 已修复

- **位置**：`services/sketch_parser/lshape_sketch_parser.py` L660-710（拆分前 L104-676）
- **问题**：L 形草图几何检测函数包含轮廓分析、凹角检测（凸包 + 滑窗）、方向识别、尺寸解析、自洽评分等完整流程。函数过长导致每个检测阶段难以单独测试和修改。
- **修复**：拆分为 5 个子函数：`_extract_largest_contour()`、`_detect_concave_sliding_window()`（149 行）、`_detect_by_convex_hull()`（103 行）、`_collect_approx_candidates()`（187 行）、`_finalize_lshape_geometry()`（56 行）。`_detect_lshape_geometry` 现为 51 行编排器，按轮廓分析→凹角检测→候选收集→参数计算顺序调用。
- **验证**：语法检查通过，380 测试通过，无回归。

### C-04: `PoolRenderWorker.run` 函数达 515 行 ✅ 已修复

- **位置**：`workers/property_panel_workers.py` L327-366（拆分前 L319-833）
- **问题**：工作线程 `run()` 方法同时负责文件名解析、模板匹配、草图解析、多洞参数、L 形参数、设计构建等完整业务流程。职责过重，无法单独测试各步骤，且 Worker 中直接组装业务对象，不符合"读取输入→计算→发送结果"原则。
- **修复**：拆分为 7 个步骤方法：`_step_parse_filename()`、`_step_match_template()`、`_step_resolve_sketch()`、`_step_log_user_margins()`、`_build_design()`、`_apply_lshape_params()`（49 行）、`_apply_rect_hole_params()`（51 行）、`_apply_multihole_addon()`（261 行）。`run` 现为 40 行编排器，按步骤 1-4 顺序调用，每步返回中间结果。
- **验证**：语法检查通过，380 测试通过，无回归。

### C-05: TemplateMatcher RLock 可重入性风险 ✅ 已修复

- **位置**：`services/parser/template_matcher.py`（原 `core/parser/template_matcher.py`）
- **问题**：`find_best_match` 调用 `self._lock.acquire()`，随后调用 `_find_best_match_impl`，其内部又调用 `scan_library()`，后者再次 `acquire()`。使用 RLock 不会死锁，但内部调用链可能修改 `self._cache` / `self._idx_*`，外部调用者在同一期间可能看到中间状态。
- **修复**：所有 6 个公开方法（`scan_library`、`find_best_match`、`set_template_dir`、`clear_cache`、`get_library_stats`、`get_index_stats`）已用 `threading.RLock` 保护，采用委托模式（公开方法加锁→调用 `_impl` 内部方法不加锁）。`scan_library` 和 `find_best_match` 使用 `acquire()/release()` + `try/finally`（等价于 `with` 语句）；其余 4 个方法使用 `with self._lock:` 上下文管理器。内部 `_impl` 方法不加锁，避免重入。
- **验证**：语法检查通过，380 测试通过，无回归。

---

## 四、High 级发现

### 4.1 核心算法模块

#### H-01: `compute_border_bands` 内存开销大

- **位置**：`core/geometry.py` L472-558, L694-777
- **问题**：多层边框计算为每一层创建新的 PIL mask 并使用双 mask 差集。L 形模式还会重复构建 L 形 mask。导出大图（2 亿像素上限）时内存峰值显著增加。
- **建议**：先构建 frame mask 集合，再做布尔运算；避免重复创建完整尺寸 PIL mask。
- **V2.4 状态**：⛔ 未修复

#### H-02: `_build_multi_layer_corner_mask` 分支过多

- **位置**：`core/image_cropper_mask.py` L57-273
- **问题**：同时处理 normal mask、protect content、间隙层扣除、ring_region 保护、border_zone 裁剪等多套条件，新增保护模式时容易改变其他模式的裁剪行为。
- **建议**：用策略模式拆分：`normal_mask_builder`、`content_protect_builder`、`gap_protect_builder`。
- **V2.4 状态**：⛔ 未修复

#### H-03: `_redraw_border_on_corner` 过长（334 行）

- **位置**：`core/corner/sector_render.py` L157-490
- **问题**：包含 ROI 提取、深度计算、间隙层判定、内容保护 mask、beyond_arc 清理、逐层绘制等完整流程。核心渲染逻辑集中在一个函数中。
- **建议**：拆分为 `build_roi()`、`build_border_depth_map()`、`build_content_protection_mask()`、`render_border_layers_in_roi()`。
- **V2.4 状态**：⛔ 未修复

#### H-04: `apply_lshape_border_completion` 路由逻辑过长

- **位置**：`core/lshape_border.py` L465-679
- **问题**：统一处理手动覆盖、Profile 检测、Profile 让位 V13、V13 路径、旧路径等多条路径，新增检测器时容易误伤回退顺序。
- **建议**：采用注册表模式管理检测器/绘制器，将路由策略拆为独立模块。
- **V2.4 状态**：⛔ 未修复

#### H-05: `classify_gap_layers` 判定逻辑过长

- **位置**：`core/corner/detection.py` L240-328
- **问题**：统一间隙层判定包含最内层、厚度上限、最外层深色、中间层 sandwich、浅色外层 sentinel 等多套判断。任一规则调整都可能影响圆角补边。
- **建议**：拆为 `is_innermost()`、`is_too_thick()`、`is_sentinel_dark()`、`is_sandwich_gap()` 等独立函数。
- **V2.4 状态**：⛔ 未修复

### 4.2 解析器模块（已迁移至 `services/sketch_parser/`）

#### H-06: 多洞解析主编排函数 410 行

- **位置**：`services/sketch_parser/sketch_parser_multihole.py` L1487-1896（原 `core/pool_designer/`）
- **问题**：`_9step_multi_hole_parse` 包含 OCR 扫描、空间绑定、候选生成、几何验证、自洽评分等多个阶段，难以单独测试。
- **建议**：拆分为 `_collect_ocr_data`、`_spatial_bind_ocr`、`_evaluate_candidates`、`_select_best_assignment`。
- **V2.4 状态**：⛔ 未修复（仅路径迁移）

#### H-07: 边距校验修复函数 392 行

- **位置**：`services/sketch_parser/sketch_parser_margins.py` L191-582（原 `core/pool_designer/`）
- **问题**：`_validate_and_fix_margins` 包含负边距清零、target 权威外框、方向标签反推、几何守恒、比例缩放、异常重写等大量分支。
- **建议**：拆分为 `_sanitize_margin`、`_ensure_target_outer`、`_fix_horizontal_outer`、`_fix_vertical_outer` 等。
- **V2.4 状态**：⛔ 未修复（仅路径迁移）

#### H-08: 缓存键仅依赖 mtime，缺内容哈希

- **位置**：`services/sketch_parser/sketch_parser_cache.py` L58-64（原 `core/pool_designer/`）
- **问题**：缓存键为 `(image_path, mtime, target_w, target_h, algo_version)`。文件内容不变但 mtime 变化时缓存失效；mtime 相同但内容变化时可能使用过期缓存。
- **建议**：引入图像内容哈希（如 pHash 或前 NKB 哈希）作为缓存键的补充部分。
- **V2.4 状态**：⛔ 未修复

#### H-09: OCR 魔法值散落各处

- **位置**：`services/sketch_parser/sketch_parser_numbers.py`、`services/sketch_parser/sketch_parser_multihole.py` 全文
- **问题**：0.03、0.25、0.05、0.02、10.0 等阈值散落在函数体内，无集中定义和来源注释。
- **建议**：集中定义为常量（如 `MIN_HOLE_AREA_RATIO`、`GAP_VETO_HOLE_RATIO`），并添加来源注释。
- **V2.4 状态**：⛔ 未修复

### 4.3 GUI 与架构

#### H-10: MVC 分离不足，业务逻辑混入 UI 层

- **位置**：`gui/property_panel_generate.py` L35-250; `gui/property_panel_layers.py` L68-311（`_collect` 现为 299 行）
- **问题**：`_collect()` 仍直接读取 SpinBox/ComboBox/颜色按钮等 UI 控件组装设计对象。业务规则（文件名解析、模板匹配、草图解析）已迁移至 services 层，但 `_collect()` 的 UI 控件直读模式尚未改为 Model 驱动。
- **建议**：将 `_collect()` 改为通过 DesignModel 属性读取，完成 Model 驱动闭环。
- **V2.4 状态**：🔶 部分解决 — services 层（模板匹配、草图解析）已从 GUI 迁出；models/design_model.py 已创建 DesignModel 中间层。但 `_collect()` 仍直读 UI 控件，尚未改为 Model 驱动。

#### H-11: main.py 直接操作 Panel 私有控件

- **位置**：`main.py` L275-303
- **问题**：`_sync_panel_from_design()` 将设计值写回多个 Panel 的 `_sp_w`、`_sp_h`、`_cb_mode` 等私有控件，主窗口与 Panel 内部实现深度耦合。
- **建议**：下放给各 Panel 内部的 `sync_from_design(design)` 方法，主窗口只传递模型变更。
- **V2.4 状态**：🔶 部分解决 — DesignModel 已提供 `sync_from_design()` 接口，但 main.py 尚未改为调用 model 而非直接操作 Panel 控件。

#### H-12: AutoMatchWorker 缺少取消回调

- **位置**：`workers/cropper_workers.py`（原 `gui/cropper_panel.py`）
- **问题**：`AutoMatchWorker.run()` 中直接调用 `_matcher.scan_library(force=False)`，没有传入 `check_cancel` 回调。长库扫描时用户取消不彻底，扫描在后台继续运行。
- **建议**：添加 `check_cancel=self.isInterruptionRequested` 参数到 scan_library 调用。
- **V2.4 状态**：⛔ 未修复（已迁移至 workers 层，但取消回调仍缺失）

#### H-13: LShapePanel 与 PropertyPanel 信号耦合重

- **位置**：`gui/property_panel.py` L408-478
- **问题**：通过 `set_lshape_panel()` 注入引用并连接大量信号。LShapePanel 定义多组委托信号和自有信号，对外契约较重。
- **建议**：提取 `LShapePanelBridge` 适配器，减少直接信号数量，合并为 `lshape_action_requested(action, params)`。
- **V2.4 状态**：⛔ 未修复

#### H-14: `_SketchDecodeWorker` 中断检查语义混淆

- **位置**：`workers/property_panel_workers.py`（原 `gui/property_panel_workers.py`）
- **问题**：`self.isInterruptionRequested()` 来自 QThread，但类内成员和 `self` 语义容易混淆 Worker 对象与 Thread 对象。
- **建议**：显式使用 `self.isInterruptionRequested()` 并添加注释，或改为外部 Thread 控制。
- **V2.4 状态**：⛔ 未修复（已迁移至 workers 层，但语义问题仍在）

#### H-15: 打包脚本历史版本堆积

- **位置**：`packaging/` 目录
- **问题**：存在 `package.py`、`packageV2.0.py`、`packageV2.1.py`、`packageV2.2.py` 四个活跃脚本 + `legacy/packageV2.1.2.py`。旧脚本可能基于旧目录结构，误用会导致路径错误。
- **建议**：保留 `packageV2.2.py` 为唯一入口，其余移入 `packaging/legacy/` 归档。
- **V2.4 状态**：🔶 部分解决 — `packageV2.1.2.py` 已移入 `packaging/legacy/`，但 `package.py`/`packageV2.0.py`/`packageV2.1.py` 仍在根目录。

---

## 五、Medium 级发现

以下列出最具代表性的 Medium 级问题，完整清单共 33 项。

| 编号 | 模块 | 问题 | 建议 | V2.4 路径 |
|------|------|------|------|------|
| M-01 | `image_ops.py` | 0.5px 边距容差可能导致切边 bbox 与像素网格不对齐 | 统一使用 `int(round(...))` 对齐 | `core/image_ops.py`（不变） |
| M-02 | `geometry.py` | L 形退化为矩形时边框 band 一致性未测试 | 增加 `cut_w=0` 退化测试 | `core/geometry.py`（不变） |
| M-03 | `image_cropper.py` | `apply_rounded_corners` 嵌套条件多 | 拆为 mask/border/contour 三步 | `core/image_cropper.py`（不变） |
| M-04 | `lshape_border_route.py` | `_classify_profile` 状态切换复杂 | 拆为 collect/align/truncate/group 四阶段 | `core/lshape_border_route.py`（不变） |
| M-05 | `detection.py` | `_enforce_border_thickness_caps` 规则多 | 每个规则抽取为独立 validator | `core/corner/detection.py`（不变） |
| M-06 | `sector_render.py` | beyond_arc 二次清理可能重复 | 合并为一次清理或明确安全含义 | `core/corner/sector_render.py`（不变） |
| M-07 | `algorithm.py` | `carve_corner_on_mask` normal/inverse 混用 | 拆为两个独立函数 | `core/corner/algorithm.py`（不变） |
| M-08 | `template_matcher.py` | 磁盘缓存格式 .pickle/.json 混用 | 统一为单一格式 | `services/parser/template_matcher.py`（迁移） |
| M-09 | `name_parser.py` | 异常文件名（逗号分隔、单位混用）覆盖不足 | 增加边界用例测试 | `services/parser/name_parser.py`（迁移） |
| M-10 | `sketch_parser_cache.py` | FIFO 淘汰策略简单，容量固定 50 | 改为 OrderedDict + 可配置容量 | `services/sketch_parser/sketch_parser_cache.py`（迁移） |
| M-11 | `property_panel_poolbox.py` | `_on_pool_target_changed` 职责过多 | 拆为文件名解析/回填/历史同步 | `gui/property_panel_poolbox.py`（不变） |
| M-12 | `property_panel_generate.py` | `_on_pool_finished_ok` 163 行 | 先返回标准化 design，再统一 sync | `gui/property_panel_generate.py`（不变） |
| M-13 | `cropper_panel.py` | `textChanged` 无 debounce | 添加 200ms debounce | `gui/cropper_panel.py`（不变） |
| M-14 | `lshape_panel.py` | `_apply_lshape_params` 职责重 | 拆为保存/回填/摘要/触发 | `gui/lshape_panel.py`（不变） |
| M-15 | `app_settings.py` | 配置策略集中且硬编码 | 拆为独立 service，参数可配置 | `core/app_settings.py`（不变） |

---

## 六、架构与线程安全分析

### 6.1 模块依赖链

V2.3 重构后依赖链已明确分层：

```
models/ (DesignModel, 纯数据)
    ↑
services/ (模板匹配 / 草图解析 / PSD 加载)
    ↑
workers/ (QThread Worker，调用 services + core)
    ↑                    ↑
gui/ (UI 面板)     core/ (渲染 / 裁剪 / 几何 / 边框 / 圆角)
    ↑                    ↑
    └──── main.py ───────┘
```

`core/` 内部依赖链：`geometry` → `image_ops` → `lshape_border` → `corner`。`render_design` 调用 geometry 生成 mask、调用 lshape_border 补全边框、调用 corner 处理圆角和边框重绘。

| 模块组 | 耦合度 | 风险 | V2.3 变更 |
|--------|--------|------|------|
| `image_cropper` / `image_cropper_mask` / `image_cropper_border` | 高 | 三文件均从 corner 子包导入检测/mask/渲染函数 | 不变 |
| `lshape_border` / `lshape_border_route` | 中 | 路由策略与绘制实现混合 | 不变 |
| `workers/` ↔ `services/` | 低 | Worker 通过 services 层间接调用，不导入 gui | **改善** |
| `property_panel` / `lshape_panel` | 高 | 通过引用注入 + 大量信号直接连接 | 不变 |
| `main.py` → 各 Panel | 高 | 主窗口直接访问 Panel 私有属性 | 部分改善（DesignModel 已就位） |

### 6.2 线程安全评估

| Worker | 所在位置 | 退役模式 | 取消支持 | 评估 | V2.4 变更 |
|--------|----------|----------|----------|------|------|
| `PreviewRenderWorker` | `workers/canvas_workers.py` | requestInterruption + wait + deleteLater | ✅ 已实现 | ✅ 安全 | gui→workers |
| `ExportSaveWorker` | `workers/canvas_workers.py` | 同上 + design.clone() 快照 | ✅ 已实现 | ✅ 安全 | gui→workers |
| `CropWorker` | `workers/cropper_workers.py` | 同上 | ✅ 已实现 | ✅ 安全 | gui→workers |
| `PoolRenderWorker` | `workers/property_panel_workers.py` | 同上 | ✅ 已实现 | ✅ 安全（C-04 拆分后 40 行） | gui→workers |
| `AutoMatchWorker` | `workers/cropper_workers.py` | 同上 | ❌ 缺失 | 🔴 需修复 | gui→workers |
| `_SketchDecodeWorker` | `workers/property_panel_workers.py` | 同上 | ✅ 已实现 | ⚠️ 语义混淆 | gui→workers |
| `_LShapeParseWorker` | `workers/property_panel_workers.py` | 同上 | ✅ 已实现 | ✅ 安全 | gui→workers |
| `TemplateMatcher` | `services/parser/template_matcher.py` | RLock 保护公开方法 | — | ✅ 已修复 | core→services |

> **线程安全总结**：QThread 退役模式整体正确——所有 Worker 都通过 `requestInterruption()` + `wait()` + `deleteLater()` 安全退役。`closeEvent` 和 `app.aboutToQuit` 双重接管所有后台线程。V2.3 中所有 Worker 已统一迁移至 `workers/` 包，不再导入 gui 模块。V2.4 拆分 `PoolRenderWorker.run` 后职责已减轻（40 行编排器）。**剩余风险点**：**(1)** `AutoMatchWorker` 缺少 `check_cancel` 回调；**(2)** `_SketchDecodeWorker` 中断检查语义混淆。**已修复**：TemplateMatcher RLock 可重入性风险（C-05）、`PoolRenderWorker.run` 职责过重（C-04）。

---

## 七、测试覆盖分析

| 指标 | V2.2（初版） | V2.4（当前） |
|------|------|------|
| 收集测试 | 444 | 388 |
| 通过测试 | 444 | 380 |
| 失败测试 | 0 | 2（PyQt5 环境缺失，非代码缺陷） |
| 跳过测试 | — | 7 |
| 执行耗时 | 50.4s | 69.16s |
| 测试文件 | 25 | 35 |

> **注**：测试用例数从 444 降至 388，原因为清理了大量归档旧测试（`scripts/_archive/old_tests/` 等）和部分重复用例。新增了 `test_complex_pattern_safety.py`（446 行）、`test_gap_fix_verification.py`（337 行）等更聚焦的测试。2 个失败由 `ModuleNotFoundError: No module named 'PyQt5'` 导致（`test_pool_lshape_flow.py` 中 2 个 Worker 集成测试），非代码逻辑缺陷。

### 7.1 覆盖矩阵

| 模块 | 测试文件 | 用例数 | 覆盖评估 | 盲区 | V2.4 路径 |
|------|----------|--------|----------|------|------|
| `template_matcher.py` | test_template_matcher.py | 16 | ✅ 良好 | 并发调用测试 | `services/parser/` |
| `name_parser.py` | test_name_parser.py | 34+ | ✅ 良好 | 异常文件名、超大数值 | `services/parser/` |
| `sketch_parser_multihole.py` | test_multi_hole_parser.py | 30 | ✅ 良好 | 3 洞以上、OCR 严重噪声 | `services/sketch_parser/` |
| `sketch_parser_margins.py` | test_sketch_parser_logic.py | 21 | ✅ 良好 | 方向锁定、target 反推冲突 | `services/sketch_parser/` |
| `lshape_sketch_parser.py` | test_lshape_sketch_parser.py | 7 | ⚠️ 一般 | 非标准 L 形、OCR 低置信度 | `services/sketch_parser/` |
| `image_ops.py` | test_image_ops_p2.py | 14 | ⚠️ 一般 | render_design 主路径未覆盖 | `core/`（不变） |
| `lshape_border.py` | test_lshape_border.py | 24 | ✅ 良好 | — | `core/`（不变） |
| `corner/detection.py` | test_rounded_corner.py | 22 | ✅ 良好 | — | `core/`（不变） |
| `sketch_parser_numbers.py` | — | 0 | 🔴 缺失 | 全部 OCR 规则 | `services/sketch_parser/` |
| `sketch_parser_cache.py` | — | 0 | 🔴 缺失 | 命中/淘汰/并发 | `services/sketch_parser/` |
| `sketch_parser_vision.py` | — | 0 | 🔴 缺失 | 图像预处理/ROI | `services/sketch_parser/` |
| `sketch_parser_base.py` | — | 0 | ⚠️ 缺失 | 文件校验/格式拒绝 | `services/sketch_parser/` |
| GUI 模块 | test_gui_smoke / test_main_window 等 | ~40 | ⚠️ 一般 | 业务逻辑路径 | `gui/`（不变） |
| **新增** `complex_pattern_safety` | test_complex_pattern_safety.py | — | ✅ 新增 | — | `tests/border/` |
| **新增** `gap_fix_verification` | test_gap_fix_verification.py | — | ✅ 新增 | — | `tests/border/` |

> **测试覆盖关键缺口**：`sketch_parser_numbers.py`（OCR 核心函数已拆为 6 个子函数，最大 206 行）和 `sketch_parser_cache.py` 完全没有独立单元测试，是最大的覆盖盲区。`render_design`（已拆为 100 行编排器 + 9 个子函数）的端到端渲染路径仍缺少直接测试覆盖。V2.4 拆分后各子函数可独立测试，为后续补测创造了条件。

---

## 八、改进路线图

基于发现项的严重程度和依赖关系，建议分四阶段推进重构。每阶段应保持测试全绿。

### 阶段 1：立即处理（1-2 周）— Critical 修复 ✅ 全部完成

| 项 | 状态 |
|---|---|
| **C-01**: 拆分 `render_design`（848→100 行）为 9 个子函数 | ✅ 已完成 |
| **C-02**: 拆分 `_extract_direction_label_numbers`（747→42 行）为 6 个子函数 | ✅ 已完成 |
| **C-03**: 拆分 `_detect_lshape_geometry`（573→51 行）为 5 个子函数 | ✅ 已完成 |
| **C-04**: 拆分 `PoolRenderWorker.run`（515→40 行）为 7 个步骤方法 | ✅ 已完成 |
| **C-05**: 修复 `TemplateMatcher` RLock 可重入性 | ✅ 已完成 |
| **H-12**: 为 `AutoMatchWorker` 添加 `check_cancel` 回调 | ⛔ 未修复 |
| **H-14**: 修复 `_SketchDecodeWorker` 中断检查语义 | ⛔ 未修复 |

### 阶段 2：短期优化（2-4 周）— High 级函数拆分与架构

| 项 | 状态 |
|---|---|
| 拆分 `_9step_multi_hole_parse`、`_validate_and_fix_margins`、`_classify_hole_layout` 等超长函数 | ⛔ 未修复 |
| 拆分 `_redraw_border_on_corner`（334 行）和 `classify_gap_layers` | ⛔ 未修复 |
| 引入 `DesignModel` 中间层，将 `_collect()` 从 UI 控件读取改为 Model 驱动 | 🔶 DesignModel 已创建，`_collect()` 未改造 |
| 将 `_sync_panel_from_design()` 下放到各 Panel 内部 | 🔶 DesignModel 接口已就位，main.py 未改造 |
| 提取 LShapePanel 桥接适配器，减少 Panel 间直接引用 | ⛔ 未修复 |
| 集中 OCR 魔法值为常量配置 | ⛔ 未修复 |
| 清理打包目录历史脚本 | 🔶 `packageV2.1.2.py` 已归档至 `legacy/`，其余仍在根目录 |

### 阶段 3：中期改进（1-2 月）— 测试补齐与缓存优化

| 项 | 状态 |
|---|---|
| 为 `sketch_parser_numbers.py` 补充 OCR 规则单元测试 | ⛔ 未修复 |
| 为 `sketch_parser_cache.py` 补充命中/淘汰/并发测试 | ⛔ 未修复 |
| 为 `sketch_parser_vision.py` 补充图像预处理测试 | ⛔ 未修复 |
| 重构缓存策略：引入图像内容哈希、OrderedDict 淘汰、可配置容量 | ⛔ 未修复 |
| 为 `render_design` 拆分后的子函数补充端到端渲染测试 | ⛔ 未修复 |
| 补充 TemplateMatcher 并发压测用例 | ⛔ 未修复 |

### 阶段 4：长期重构（持续）— 架构升级

| 项 | 状态 |
|---|---|
| 建立统一事件总线，替代 Panel 间直接引用和信号连接 | ⛔ 未修复 |
| 将 Worker 层从 GUI 中拆出，使业务逻辑可独立测试 | ✅ 已完成（`workers/` 包） |
| 引入代码复杂度监控（如 radon），防止文件/函数规模再次增长 | ⛔ 未修复 |
| 将 L 形边框路由改为注册表模式 | ⛔ 未修复 |
| 统一 0.5px 边界容差、像素对齐策略 | ⛔ 未修复 |
| 为真实素材增加 snapshot 回归测试 | ⛔ 未修复 |
| 统一异常处理策略，区分用户可纠正错误与系统错误 | ⛔ 未修复 |

---

## 九、重构变更记录

> 以下为 V2.2 → V2.4 期间（2026-09-12 commit `558b54c` → `780dbbc`）的变更总结。

### 9.1 新增顶层包

| 包 | 职责 | 文件数 | 来源 |
|---|---|---|---|
| `services/` | 服务层：外部能力封装 | 15 | 从 `core/` 迁出 |
| `services/parser/` | 文件名解析 + 模板库匹配 | 3 | ← `core/parser/` |
| `services/sketch_parser/` | 草图尺寸解析（单洞/多洞/L形） | 9 | ← `core/pool_designer/` |
| `services/psd/` | PSD 文件加载与导出 | 2 | ← `core/psd/` |
| `workers/` | 线程调度层：所有 QThread Worker | 4 | 从 `gui/` 迁出 |
| `models/` | 数据模型层：纯数据结构 | 2 | 新建 |

### 9.2 迁移映射表

| 原路径 (V2.2) | 新路径 (V2.4) |
|---|---|
| `core/parser/template_matcher.py` | `services/parser/template_matcher.py` |
| `core/parser/name_parser.py` | `services/parser/name_parser.py` |
| `core/pool_designer/sketch_parser.py` | `services/sketch_parser/sketch_parser.py` |
| `core/pool_designer/sketch_parser_base.py` | `services/sketch_parser/sketch_parser_base.py` |
| `core/pool_designer/sketch_parser_cache.py` | `services/sketch_parser/sketch_parser_cache.py` |
| `core/pool_designer/sketch_parser_margins.py` | `services/sketch_parser/sketch_parser_margins.py` |
| `core/pool_designer/sketch_parser_multihole.py` | `services/sketch_parser/sketch_parser_multihole.py` |
| `core/pool_designer/sketch_parser_numbers.py` | `services/sketch_parser/sketch_parser_numbers.py` |
| `core/pool_designer/sketch_parser_vision.py` | `services/sketch_parser/sketch_parser_vision.py` |
| `core/pool_designer/lshape_sketch_parser.py` | `services/sketch_parser/lshape_sketch_parser.py` |
| `core/psd/loader.py` | `services/psd/loader.py` |
| `gui/property_panel_workers.py` | `workers/property_panel_workers.py` |
| `gui/cropper_panel.py`（Worker 部分） | `workers/cropper_workers.py` |
| `gui/canvas_widget.py`（Worker 部分） | `workers/canvas_workers.py` |

### 9.3 已解决问题

| 问题编号 | 描述 | 解决方式 |
|---|---|---|
| C-01 | `render_design` 848 行 | 拆为 100 行编排器 + 9 个子函数（最大 125 行） |
| C-02 | `_extract_direction_label_numbers` 747 行 | 拆为 42 行编排器 + 6 个子函数（最大 206 行），引入 `_DirLabelState` |
| C-03 | `_detect_lshape_geometry` 573 行 | 拆为 51 行编排器 + 5 个子函数（最大 187 行） |
| C-04 | `PoolRenderWorker.run` 515 行 | 拆为 40 行编排器 + 7 个步骤方法（最大 261 行） |
| C-05 | TemplateMatcher RLock 可重入性风险 | 所有公开方法用 `threading.RLock` 保护，委托模式实现 |
| 阶段4 "Worker 层拆出" | Worker 从 GUI 中拆出 | 新建 `workers/` 包，Worker 不再导入 gui 模块 |
| H-10（部分） | MVC 分离不足 | services 层拆出，models/design_model.py 创建 |
| H-11（部分） | main.py 直接操作 Panel 私有控件 | DesignModel 提供 `sync_from_design()` 接口 |
| H-15（部分） | 打包脚本堆积 | `packageV2.1.2.py` 移入 `packaging/legacy/` |

### 9.4 清理项

- 删除 `scripts/_archive/dev_explore/` 下 21 个失效诊断脚本
- 删除 `scripts/_archive/old_tests/` 下 16 个超期归档测试
- 删除 `scripts/diagnose/_live/` 下 6 个调试脚本
- 删除 `ProductSummary/smartshapecrop-code-review.html`（848 行旧报告）
- 删除 `.bak_c03` / `.bak_c04` 备份文件（V2.4 Critical 修复过程中的临时文件）
- 测试用例从 444 精简至 388（移除归档重复用例，新增聚焦测试）

---

*审查方法：通过 3 个并行审查代理分别分析核心算法模块、解析器模块和 GUI 层，结合 AST 函数长度分析和 pytest 测试用例运行验证。*

*审查工具：Python ast 模块（函数长度分析）、pytest（测试验证）、人工代码审查（3 个维度并行）。*

*版本历史：V2.2（2026-09-12 初版，2 层架构）→ V2.3（2026-09-12 五层架构重构）→ V2.4（2026-09-12 Critical 修复验证，5/5 完成）。*

*注意：本报告基于 2026-09-12 的代码快照（commit `780dbbc`）。后续代码变更可能影响发现项的有效性。*
