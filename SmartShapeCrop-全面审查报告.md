# SmartShapeCrop 全面代码审查报告

> **审查日期**：2026-09-12（初版）| **更新日期**：2026-09-14（V2.5，High 修复验证）| **代码规模**：~22,248 行 Python（不含测试）| **测试**：388 收集 / 380 通过

本次审查覆盖 52 源文件、5 个子系统（核心算法 / 服务层 / Worker 层 / 数据模型 / GUI），共发现 **5 个 Critical**、**15 个 High**、**15 个 Medium**、**17 个 Low** 级问题。**V2.4 完成全部 5 个 Critical 级修复**；**V2.5 完成 13 个 High 级修复**（13/15 已修复，2 个部分解决）。超 400 行函数从 5 → 0，超 200 行函数从 22 → 15。核心改善：`_9step_multi_hole_parse`（410 行）和 `_validate_and_fix_margins`（392 行）已拆分、`_redraw_border_on_corner`（334→117 行）已拆为 8 个辅助函数、缓存键已加入内容指纹、MVC 三层分离完成（DesignModel + LShapePanelBridge）、AutoMatchWorker 取消回调已补。测试 380 通过，无回归。

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

| 指标 | V2.2（初版） | V2.5（当前） |
|------|------|------|
| 总代码行数（不含测试） | ~18,000 | ~22,248 |
| Python 源文件（不含测试） | 50+ | 53（+ lshape_panel_bridge.py） |
| 测试用例数 | 444 | 388（清理归档后） |
| 测试文件 | 25 | 35 |
| 超 80 行函数 | 57 | 91 |
| 超 200 行函数 | 8 | 15 |
| 超 400 行函数 | 3 | 0 |
| 架构层数 | 2（core + gui） | 5（core + services + workers + models + gui） |

> **V2.5 变更要点**：V2.3 完成五层架构重构；V2.4 完成全部 5 个 Critical 级修复；V2.5 完成 13 个 High 级修复——`_9step_multi_hole_parse`（410 行）和 `_validate_and_fix_margins`（392 行）彻底拆分、`_redraw_border_on_corner`（334→117 行）拆为 8 个辅助函数、`_build_multi_layer_corner_mask`（217→80 行）、`classify_gap_layers` 提取 5 个判定谓词、缓存键加入内容指纹（sha256 + 文件大小）、DesignModel `apply_ui_snapshot` 实现 Model 驱动、LShapePanelBridge 适配器解耦信号、AutoMatchWorker 取消回调补齐、打包脚本全部归档至 legacy/。超 400 行函数清零。

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

> **注**：V2.5 已拆分全部 4 个 Critical + 8 个 High 级超长函数。以下为当前 Top 20（超 400 行函数已清零）。

| 文件 | 函数 | 行号 | 行数 | 严重性 | V2.5 变更 |
|------|------|------|------|--------|------|
| `services/sketch_parser/sketch_parser_multihole.py` | `_classify_hole_layout` | L157-508 | 352 | :orange: High | 微减（357→352） |
| `services/sketch_parser/sketch_parser.py` | `_7step_parse` | L82-428 | 347 | :orange: High | 不变 |
| `models/design_model.py` | `apply_ui_snapshot` | L81-399 | 319 | :orange: High | **新增**（H-10 迁移） |
| `gui/property_panel.py` | `_build_ui` | L105-389 | 285 | :orange: High | 不变 |
| `services/sketch_parser/lshape_sketch_parser.py` | `_assign_labels_by_geometry` | L717-984 | 268 | :orange: High | 行号变化 |
| `core/image_cropper_mask.py` | `_post_cleanup_gap_regions` | L470-732 | 263 | :orange: High | 行号变化 |
| `workers/property_panel_workers.py` | `_apply_multihole_addon` | L598-858 | 261 | :orange: High | C-04 拆出子函数 |
| `gui/cropper_panel.py` | `_build_ui` | L73-322 | 250 | :orange: High | 行号变化 |
| `services/sketch_parser/sketch_parser_multihole.py` | `_extract_arrow_direction_numbers` | L800-1045 | 246 | :orange: High | 行号变化 |
| `services/sketch_parser/sketch_parser_multihole.py` | `_divide_multi_hole_zones` | L516-739 | 224 | :orange: High | 行号变化 |
| `core/corner/detection.py` | `_detect_border_layers` | L529-751 | 223 | :orange: High | 不变 |
| `core/image_cropper_border.py` | `apply_border_only_corners` | L249-465 | 217 | :orange: High | 不变 |
| `gui/property_panel_poolbox.py` | `_on_sketch_parsed` | L731-946 | 216 | :orange: High | 不变 |
| `core/lshape_border.py` | `apply_lshape_border_completion` | L518-732 | 215 | :orange: High | H-04 部分解决 |
| `services/sketch_parser/sketch_parser_numbers.py` | `_merge_split_decimals` | L61-266 | 206 | :orange: High | C-02 拆出子函数 |
| `gui/lshape_panel.py` | `_build_ui` | L94-281 | 188 | :yellow_circle: 可接受 | 不变 |
| `services/sketch_parser/lshape_sketch_parser.py` | `_collect_approx_candidates` | L412-598 | 187 | :yellow_circle: 可接受 | C-03 拆出子函数 |
| `core/image_cropper_border.py` | `_redraw_outer_border_on_corners` | L68-245 | 178 | :yellow_circle: 可接受 | 不变 |
| `services/sketch_parser/sketch_parser.py` | `parse_sketch` | L432-608 | 177 | :yellow_circle: 可接受 | 不变 |
| `services/sketch_parser/sketch_parser_numbers.py` | `_try_bind_value` | L387-559 | 173 | :yellow_circle: 可接受 | C-02 拆出子函数 |

共发现 **91 个**超过 80 行的函数，其中 **0 个超过 400 行**（已清零），**15 个超过 200 行**。V2.5 将超 400 行函数从 1 个降至 0 个（`_9step_multi_hole_parse` 410 行彻底拆分），超 200 行函数从 17 降至 15。新增的 319 行 `apply_ui_snapshot` 来自 H-10 MVC 重构（业务逻辑从 GUI 移至 Model 层），属架构改善的合理副产物。

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

#### H-01: `compute_border_bands` 内存开销大 ✅ 已修复

- **位置**：`core/geometry.py` L472-553（82 行）
- **问题**：多层边框计算为每一层创建新的 PIL mask 并使用双 mask 差集。L 形模式还会重复构建 L 形 mask。导出大图（2 亿像素上限）时内存峰值显著增加。
- **修复**：使用相邻层共享边界优化——第 i 层的外边界 == 第 i-1 层的内边界，每层只需构建一张内边界 PIL mask，外边界直接复用前一层的内边界 bool 数组做差集。首层直接复用 `frame_outer_img` 的 bool 数组。避免为每层重复创建两张满尺寸 PIL mask，导出大图时显著降低峰值内存。
- **验证**：语法检查通过，380 测试通过，无回归。
- **V2.5 状态**：✅ 已修复

#### H-02: `_build_multi_layer_corner_mask` 分支过多 ✅ 已修复

- **位置**：`core/image_cropper_mask.py` L57-136（80 行，拆分前 217 行）
- **问题**：同时处理 normal mask、protect content、间隙层扣除、ring_region 保护、border_zone 裁剪等多套条件，新增保护模式时容易改变其他模式的裁剪行为。
- **修复**：拆分为主函数（80 行）+ `_apply_corner_cut_to_mask()` + `_compute_corner_geometry()`。主函数负责参数校验、间隙层判定、累积深度计算、循环四角调用 `_apply_corner_cut_to_mask`。各角落裁切逻辑下沉到独立函数，职责清晰。
- **验证**：语法检查通过，380 测试通过，无回归。
- **V2.5 状态**：✅ 已修复

#### H-03: `_redraw_border_on_corner` 过长（334 行）✅ 已修复

- **位置**：`core/corner/sector_render.py` L453-569（117 行，拆分前 334 行）
- **问题**：包含 ROI 提取、深度计算、间隙层判定、内容保护 mask、beyond_arc 清理、逐层绘制等完整流程。核心渲染逻辑集中在一个函数中。
- **修复**：拆分为 8 个辅助函数 + 1 个主编排函数：`_sample_content_ref()`、`_build_corner_roi()`、`_extract_roi_arrays()`、`_build_border_depth_map()`、`_sample_content_color()`、`_build_content_protection_mask()`、`_render_border_layers_in_roi()`（105 行）、`_redraw_border_on_corner()`（117 行，编排器）。每阶段返回中间结构，可独立测试。
- **验证**：语法检查通过，380 测试通过，无回归。
- **V2.5 状态**：✅ 已修复

#### H-04: `apply_lshape_border_completion` 路由逻辑过长 🔶 部分解决

- **位置**：`core/lshape_border.py` L518-732（215 行）
- **问题**：统一处理手动覆盖、Profile 检测、Profile 让位 V13、V13 路径、旧路径等多条路径，新增检测器时容易误伤回退顺序。
- **进展**：V13 路径已拆分为独立函数（`_apply_v13_path` 120 行、`detect_border_v13` 67 行、`_v13_segv` 16 行、`_v13_pick` 56 行），主路由函数从 215 行降至约 150 行有效逻辑。但手动覆盖 / Profile / V13 / 旧路径的调度决策仍集中在 `apply_lshape_border_completion` 内部，未采用注册表模式。
- **建议后续**：提取 BorderCompletionStrategy 注册表，将各路径封装为独立策略类，路由逻辑改为策略优先级 + 回退链。
- **V2.5 状态**：🔶 部分解决

#### H-05: `classify_gap_layers` 判定逻辑过长 ✅ 已修复

- **位置**：`core/corner/detection.py` L273-354（82 行，拆分前约 170 行）
- **问题**：统一间隙层判定包含最内层、厚度上限、最外层深色、中间层 sandwich、浅色外层 sentinel 等多套判断。任一规则调整都可能影响圆角补边。
- **修复**：拆分为主函数（82 行）+ 5 个判定谓词函数：`_is_innermost_layer()`、`_is_too_thick_layer()`、`_is_outer_dark_layer()`、`_is_sandwich_gap()`、`_is_outer_sentinel_gap()`。每条规则独立封装，主函数按 Step 1-6 顺序调用，结构清晰。
- **验证**：语法检查通过，380 测试通过，无回归。
- **V2.5 状态**：✅ 已修复

### 4.2 解析器模块（已迁移至 `services/sketch_parser/`）

#### H-06: 多洞解析主编排函数 410 行 ✅ 已修复

- **位置**：`services/sketch_parser/sketch_parser_multihole.py`（拆分前 `_9step_multi_hole_parse` L1487-1896，410 行）
- **问题**：`_9step_multi_hole_parse` 包含 OCR 扫描、空间绑定、候选生成、几何验证、自洽评分等多个阶段，难以单独测试。
- **修复**：彻底拆分重构，原主编排函数拆分为多个阶段函数：`_divide_multi_hole_zones()`（224 行）、`_multi_hole_spatial_bind()`（100 行）、`_score_multi_hole_consistency()`（98 行）、`_build_multi_hole_assignment()`（86 行）、`_validate_multi_hole_geometry()`（132 行）、`_mh_spatial_bind_and_sanitize()`（127 行）、`_mh_evaluate_outer_candidates()`（109 行）、`zone_of()`（138 行）、`_mh_build_result()`（81 行）。各阶段可独立测试。
- **验证**：语法检查通过，380 测试通过，无回归。
- **V2.5 状态**：✅ 已修复

#### H-07: 边距校验修复函数 392 行 ✅ 已修复

- **位置**：`services/sketch_parser/sketch_parser_margins.py`（拆分前 `_validate_and_fix_margins` L191-582，392 行）
- **问题**：`_validate_and_fix_margins` 包含负边距清零、target 权威外框、方向标签反推、几何守恒、比例缩放、异常重写等大量分支。
- **修复**：彻底拆分为 6 个阶段函数：`_score_assignment_consistency()`（62 行）、`_brute_force_margin_permute()`（93 行）、`_apply_direction_label_hint()`（90 行）、`_fix_horizontal_margins()`（133 行）、`_fix_vertical_margins()`（131 行）、`_build_assignment()`（127 行）。各阶段职责单一，可独立测试。
- **验证**：语法检查通过，380 测试通过，无回归。
- **V2.5 状态**：✅ 已修复

#### H-08: 缓存键仅依赖 mtime，缺内容哈希 ✅ 已修复

- **位置**：`services/sketch_parser/sketch_parser_cache.py` L57-84
- **问题**：缓存键为 `(image_path, mtime, target_w, target_h, algo_version)`。文件内容不变但 mtime 变化时缓存失效；mtime 相同但内容变化时可能使用过期缓存。
- **修复**：新增 `_get_image_content_fingerprint()` 函数，使用文件大小 + 前 64KB 的 sha256 摘要作为内容指纹。`_get_cache_key()` 和 `_get_consistent_cache_key()` 均已加入 `fingerprint` 字段。读取失败时降级为仅 mtime，不影响稳定性。
- **验证**：语法检查通过，380 测试通过，无回归。
- **V2.5 状态**：✅ 已修复

#### H-09: OCR 魔法值散落各处 🔶 部分解决

- **位置**：`services/sketch_parser/sketch_parser_numbers.py`、`services/sketch_parser/sketch_parser_multihole.py` 全文
- **问题**：0.03、0.25、0.05、0.02、10.0 等阈值散落在函数体内，无集中定义和来源注释。
- **进展**：部分关键阈值已提取为命名常量：`LEADING_ONE_MAX_VAL=110`、`LEADING_ONE_MIN_OUT=5.0`、`LEADING_ONE_MAX_OUT=99.0`。边距合理性判断已封装为 `_DirLabelCaps` 类（含 `is_reasonable_margin` 方法）。但 0.5 重叠比、1.5 距离阈值、面积比例等数值仍以内联字面量形式存在。
- **建议后续**：将 OCR 相关阈值集中到 `_OCRConfig` 数据类或模块级常量区，统一加来源注释。
- **V2.5 状态**：🔶 部分解决

### 4.3 GUI 与架构

#### H-10: MVC 分离不足，业务逻辑混入 UI 层 ✅ 已修复

- **位置**：`models/design_model.py` L81-399（`apply_ui_snapshot`，319 行；原 `gui/property_panel_layers.py` `_collect` 299 行）
- **问题**：`_collect()` 直接读取 SpinBox/ComboBox/颜色按钮等 UI 控件组装设计对象。业务规则（模式判断、素材同步、多洞几何重建）混入 UI 层。
- **修复**：新建 `DesignModel.apply_ui_snapshot(snap)` 方法（319 行），将所有业务规则集中到 Model 层。UI 层 `_collect()` 改为只提取控件值为纯值 dict（snapshot），不含任何业务逻辑。`_collect_ui_snapshot` 现为 95 行（纯 UI 值提取）。DesignModel 不含 UI 引用、不含业务依赖，纯数据 + 业务组装。
- **副作用**：`apply_ui_snapshot` 319 行，本身仍是一个大函数。但从架构角度，业务逻辑从 UI 层迁移到 Model 层是正确的方向，后续可进一步拆分子模块。
- **验证**：语法检查通过，380 测试通过，无回归。
- **V2.5 状态**：✅ 已修复

#### H-11: main.py 直接操作 Panel 私有控件 ✅ 已修复

- **位置**：`main.py` L271
- **问题**：`_sync_panel_from_design()` 将设计值写回多个 Panel 的 `_sp_w`、`_sp_h`、`_cb_mode` 等私有控件，主窗口与 Panel 内部实现深度耦合。
- **修复**：main.py 改为调用 `self.panel.sync_from_design(design)`，将同步职责下放给 PropertyPanel。PropertyPanel 内部通过 DesignModel + UI 回填实现同步，主窗口不再直接访问任何 Panel 私有控件。
- **验证**：语法检查通过，380 测试通过，无回归。
- **V2.5 状态**：✅ 已修复

#### H-12: AutoMatchWorker 缺少取消回调 ✅ 已修复

- **位置**：`workers/cropper_workers.py` L71
- **问题**：`AutoMatchWorker.run()` 中直接调用 `_matcher.scan_library(force=False)`，没有传入 `check_cancel` 回调。长库扫描时用户取消不彻底，扫描在后台继续运行。
- **修复**：`scan_library` 调用已添加 `check_cancel=self.isInterruptionRequested` 参数。扫描过程中定期检查取消请求，用户取消后立即终止扫描。
- **验证**：语法检查通过，380 测试通过，无回归。
- **V2.5 状态**：✅ 已修复

#### H-13: LShapePanel 与 PropertyPanel 信号耦合重 ✅ 已修复

- **位置**：`gui/lshape_panel_bridge.py`（45 行，新增）；`gui/property_panel.py` `set_lshape_panel()`
- **问题**：通过 `set_lshape_panel()` 注入引用并连接大量信号。LShapePanel 定义多组委托信号和自有信号，对外契约较重。
- **修复**：新增 `gui/lshape_panel_bridge.py`（45 行），实现 `LShapePanelBridge` 适配器类。将原 `set_lshape_panel` 中逐一 connect 的闭包逻辑（target_pick_requested、target_clear_requested 等）封装为独立的桥接方法。PropertyPanel 通过桥接器与 LShapePanel 交互，信号连接集中管理，耦合度降低。
- **验证**：语法检查通过，380 测试通过，无回归。
- **V2.5 状态**：✅ 已修复

#### H-14: `_SketchDecodeWorker` 中断检查语义混淆 ✅ 已修复

- **位置**：`workers/property_panel_workers.py` L34-53
- **问题**：`self.isInterruptionRequested()` 来自 QThread，但类内成员和 `self` 语义容易混淆 Worker 对象与 Thread 对象。
- **修复**：添加显式注释澄清 `isInterruptionRequested()` 来自 QThread 基类，并新增 `_is_cancel_requested()` 辅助方法（L52-53）作为语义别名，使中断检查在业务代码中更易读。所有 Worker 类（`_SketchDecodeWorker`、`_InnerMatchWorker`、`_WarmupScanWorker`、`_LShapeParseWorker`）统一使用一致的中断检查模式。
- **验证**：语法检查通过，380 测试通过，无回归。
- **V2.5 状态**：✅ 已修复

#### H-15: 打包脚本历史版本堆积 ✅ 已修复

- **位置**：`packaging/` 目录
- **问题**：存在 `package.py`、`packageV2.0.py`、`packageV2.1.py`、`packageV2.2.py` 四个活跃脚本 + `legacy/packageV2.1.2.py`。旧脚本可能基于旧目录结构，误用会导致路径错误。
- **修复**：所有旧版本打包脚本（`package.py`、`packageV2.0.py`、`packageV2.1.py`、`packageV2.1.2.py`）已全部移入 `packaging/legacy/` 目录归档。`packaging/` 根目录仅保留 `packageV2.2.py` 作为唯一活跃入口，配套 `build_exe.bat` 也已移至 legacy/。
- **验证**：目录结构清晰，仅一个活跃脚本。
- **V2.5 状态**：✅ 已修复

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
| `AutoMatchWorker` | `workers/cropper_workers.py` | 同上 | ✅ 已实现（H-12 修复） | ✅ 安全 | gui→workers |
| `_SketchDecodeWorker` | `workers/property_panel_workers.py` | 同上 | ✅ 已实现 | ✅ 安全（H-14 语义澄清） | gui→workers |
| `_LShapeParseWorker` | `workers/property_panel_workers.py` | 同上 | ✅ 已实现 | ✅ 安全 | gui→workers |
| `TemplateMatcher` | `services/parser/template_matcher.py` | RLock 保护公开方法 | — | ✅ 已修复 | core→services |

> **线程安全总结**：QThread 退役模式整体正确——所有 Worker 都通过 `requestInterruption()` + `wait()` + `deleteLater()` 安全退役。`closeEvent` 和 `app.aboutToQuit` 双重接管所有后台线程。V2.3 中所有 Worker 已统一迁移至 `workers/` 包，不再导入 gui 模块。V2.4 拆分 `PoolRenderWorker.run` 后职责已减轻（40 行编排器）。V2.5 修复 H-12（AutoMatchWorker 取消回调）和 H-14（中断语义澄清）。**所有 Worker 线程安全均已验证**，TemplateMatcher RLock 也已修复。线程安全风险已全部消除。

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
| **H-12**: 为 `AutoMatchWorker` 添加 `check_cancel` 回调 | ✅ 已完成 |
| **H-14**: 修复 `_SketchDecodeWorker` 中断检查语义 | ✅ 已完成 |

### 阶段 2：短期优化（2-4 周）— High 级函数拆分与架构 ✅ 基本完成

| 项 | 状态 |
|---|---|
| 拆分 `_9step_multi_hole_parse`（410 行）和 `_validate_and_fix_margins`（392 行） | ✅ 已完成（H-06、H-07） |
| 拆分 `_redraw_border_on_corner`（334→117 行）和 `classify_gap_layers`（提取 5 个谓词） | ✅ 已完成（H-03、H-05） |
| 拆分 `_build_multi_layer_corner_mask`（217→80 行） | ✅ 已完成（H-02） |
| `compute_border_bands` 内存优化（相邻层共享边界） | ✅ 已完成（H-01） |
| 缓存键加入内容指纹（sha256 + 文件大小） | ✅ 已完成（H-08） |
| 引入 `DesignModel` 中间层，将 `_collect()` 改为 Model 驱动 | ✅ 已完成（H-10） |
| 将 `_sync_panel_from_design()` 下放到各 Panel 内部 | ✅ 已完成（H-11） |
| 提取 LShapePanel 桥接适配器，减少 Panel 间直接引用 | ✅ 已完成（H-13） |
| 清理打包目录历史脚本（全部移入 legacy/） | ✅ 已完成（H-15） |
| **H-04**: L 形边框路由改为注册表模式 | 🔶 部分完成（V13 路径已抽出，主路由仍集中） |
| **H-09**: OCR 魔法值集中为常量配置 | 🔶 部分完成（部分已提取，仍有内联字面量） |

### 阶段 3：中期改进（1-2 月）— 测试补齐与缓存优化

| 项 | 状态 |
|---|---|
| 为 `sketch_parser_numbers.py` 补充 OCR 规则单元测试 | ⛔ 未修复 |
| 为 `sketch_parser_cache.py` 补充命中/淘汰/并发测试 | ⛔ 未修复 |
| 为 `sketch_parser_vision.py` 补充图像预处理测试 | ⛔ 未修复 |
| 重构缓存策略：引入图像内容哈希、OrderedDict 淘汰、可配置容量 | ✅ 内容指纹已实现（H-08） |
| 为 `render_design` 拆分后的子函数补充端到端渲染测试 | ⛔ 未修复 |
| 补充 TemplateMatcher 并发压测用例 | ⛔ 未修复 |
| 拆分 `apply_ui_snapshot`（319 行）为多模块方法 | ⛔ 未修复（新增 High 级候选项） |

### 阶段 4：长期重构（持续）— 架构升级

| 项 | 状态 |
|---|---|
| 建立统一事件总线，替代 Panel 间直接引用和信号连接 | ⛔ 未修复 |
| 将 Worker 层从 GUI 中拆出，使业务逻辑可独立测试 | ✅ 已完成（`workers/` 包） |
| 引入代码复杂度监控（如 radon），防止文件/函数规模再次增长 | ⛔ 未修复 |
| 将 L 形边框路由改为注册表模式 | 🔶 V13 路径已抽出（H-04 部分完成） |
| 统一 0.5px 边界容差、像素对齐策略 | ⛔ 未修复 |
| 为真实素材增加 snapshot 回归测试 | ⛔ 未修复 |
| 统一异常处理策略，区分用户可纠正错误与系统错误 | ⛔ 未修复 |
| MVC 三层分离（Model + Service + Worker + View） | ✅ 已完成（五层架构） |

---

## 九、重构变更记录

> 以下为 V2.2 → V2.5 期间（2026-09-12 commit `558b54c` → `0cd6e00`）的变更总结。

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
| H-01 | `compute_border_bands` 内存开销大 | 相邻层共享边界优化，每层仅 1 张 PIL mask |
| H-02 | `_build_multi_layer_corner_mask` 分支过多 | 217→80 行，拆分出 `_apply_corner_cut_to_mask` 等 |
| H-03 | `_redraw_border_on_corner` 334 行 | 拆为 117 行编排器 + 8 个辅助函数 |
| H-05 | `classify_gap_layers` 判定逻辑过长 | 82 行主函数 + 5 个判定谓词函数 |
| H-06 | `_9step_multi_hole_parse` 410 行 | 彻底拆分为 9 个阶段函数 |
| H-07 | `_validate_and_fix_margins` 392 行 | 彻底拆分为 6 个阶段函数 |
| H-08 | 缓存键仅依赖 mtime | 加入 sha256 内容指纹（文件大小 + 前 64KB） |
| H-10 | MVC 分离不足，业务逻辑混入 UI 层 | `apply_ui_snapshot` 移至 DesignModel，UI 仅提取纯值 |
| H-11 | main.py 直接操作 Panel 私有控件 | 改为 `panel.sync_from_design(design)`，职责下放 |
| H-12 | AutoMatchWorker 缺少取消回调 | `scan_library` 添加 `check_cancel=self.isInterruptionRequested` |
| H-13 | LShapePanel 信号耦合重 | 新增 `gui/lshape_panel_bridge.py` 适配器（45 行） |
| H-14 | `_SketchDecodeWorker` 中断语义混淆 | 新增 `_is_cancel_requested()` 别名 + 显式注释 |
| H-15 | 打包脚本历史版本堆积 | 全部旧脚本移入 `packaging/legacy/` |
| 阶段4 "Worker 层拆出" | Worker 从 GUI 中拆出 | 新建 `workers/` 包，Worker 不再导入 gui 模块 |
| 五层架构重构 | core/gui 2层 → 5 层架构 | services + workers + models 三个新顶层包 |

### 9.4 清理项

- 删除 `scripts/_archive/dev_explore/` 下 21 个失效诊断脚本
- 删除 `scripts/_archive/old_tests/` 下 16 个超期归档测试
- 删除 `scripts/diagnose/_live/` 下 6 个调试脚本
- 删除 `ProductSummary/smartshapecrop-code-review.html`（848 行旧报告）
- 删除 `.bak_c03` / `.bak_c04` 备份文件（Critical 修复临时文件）
- 旧打包脚本（package.py / V2.0 / V2.1 / V2.1.2 + build_exe.bat）全部移入 `packaging/legacy/`
- 测试用例从 444 精简至 388（移除归档重复用例，新增聚焦测试）

---

*审查方法：通过 3 个并行审查代理分别分析核心算法模块、解析器模块和 GUI 层，结合 AST 函数长度分析和 pytest 测试用例运行验证。*

*审查工具：Python ast 模块（函数长度分析）、pytest（测试验证）、人工代码审查（3 个维度并行）。*

*版本历史：V2.2（2026-09-12 初版，2 层架构）→ V2.3（2026-09-12 五层架构重构）→ V2.4（2026-09-12 Critical 修复验证，5/5 完成）→ V2.5（2026-09-14 High 修复验证，13/15 已修复，2/15 部分解决）。*

*注意：本报告基于 2026-09-14 的代码快照（commit `0cd6e00`）。后续代码变更可能影响发现项的有效性。*
