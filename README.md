# SmartShapeCrop — 智能形状裁剪设计器 V2.2.3

> 面向印刷行业定制尺寸成品图的桌面设计工具：等比缩放 + 圆角裁剪 + 多层边框处理 + 水池设计器草图 OCR 智能识别 + 多洞嵌套 + 椭圆挖洞 + L 形挖角（单角 / 多角并行 / 单边阶梯）独立设计 + L 形挖角素材边框自动补全。

**最后验证**：2026-09-24（V2.2.3 单边阶梯 L 形、P0 批次正确性/安全修复、P1 批次代码卫生与 `APP_VERSION` 单一来源、P2-7 junction 越界加固，并**完成 V2.2.3 出包**后复验）
**更新触发**：目录结构变更、模块迁移、依赖变更、测试基线变更、打包入口变更时须同步更新本文件
**版本唯一来源**：`core/config.py` 的 `APP_VERSION`（由打包脚本 / 启动日志头 / 关于框三处引用，发版只改这一行）

---

## 项目简介

SmartShapeCrop 是一款面向印刷/定制设计行业的 Windows 桌面工具（PyQt5），采用**参数化设计 + 图像智能识别**双模式，核心解决三大需求：

1. **圆角裁剪工具**：将已有成品图（JPG/PSD）按目标尺寸等比缩放，并自动/手动对四角施加圆角裁剪。支持从文件名自动解析尺寸与圆角参数、模板库匹配源图、多层边框自动检测与圆角重绘。
2. **水池设计器**：参数化生成矩形嵌套、椭圆挖孔等设计稿，支持**手绘草图上传自动识别尺寸**（7 步串行流程）、多层边框、素材填充、边框文字环绕，导出印刷级 JPG。支持**多洞嵌套挖洞**与逐洞独立边距，支持内挖**空白（挖去不留白）**与**素材填充**两种挖空方式。
3. **L 形挖角设计器**（独立面板）：承载 L 形挖角的参数设置、草图上传与生成。支持草图自动识别挖角方向（tl/tr/bl/br）、挖角尺寸、外框完整尺寸，含 OCR 降级路径；支持**多角同时挖角**（最多 4 角，`l_cuts_cm` 列表）；新增**素材边框自动补全**——对自带边框的池素材图，在 L 形挖角产生的新边缘上按素材原始边框层次重绘，使成品呈完整 L 形外框。

### 核心特性

- **厘米级精度**：所有尺寸以厘米为单位输入，按 DPI 自动换算像素
- **四角独立圆角**：每个角可独立设置圆角半径（0 = 直角），支持单角/双角/四角组合
- **多层边框自动检测**：颜色距离 + 亮度突变双算法识别嵌套边框层，圆角处自动重绘
- **深色外层边框保护**：最外层深色边框（max RGB ≤ 150）永不判为间隙，确保黑色边框线完整
- **仅最外层圆角化**：圆角处仅绘制最外层边框圆弧，内层花纹保持直角，避免多余弧线/过厚/色差
- **圆弧外白底清除保护**：圆角裁剪时清理圆弧外侧超出素材范围的白色底噪（`beyond_arc` 区域），保留素材原有花纹/文字内容
- **黑大理石多边框修复**（V2.2.2）：多边框层扫描与黑大理石素材的层结构判定修正，解决深色高纹理素材的边框层误判
- **文件名智能解析**：从中文文件名提取产品名、尺寸、方向（横版/竖版）、圆角参数，支持全角/特殊字符容错
- **模板库匹配**：根据目标文件名自动匹配模板库中的最佳源图（形状+方向关键词严格匹配）
- **PSD 分层支持**：读取 PSD 图层，自动裁剪透明边距，合成扁平 JPG
- **印刷切割损耗补偿**：自动为目标尺寸加 1cm 扫描余量，圆角半径加 0.5cm 切割损耗
- **LANCZOS 高质量缩放**：默认 `simple_resize` 模式，不裁剪不留白，最小质量损失
- **草图智能识别**（水池设计器）：上传手绘草图 → 7 步串行流程（矩形检测→区域划分→多尺度 OCR→小数修复→方向标签锁定→空间映射→几何校验）→ 自动回填外框/内挖/上下左右边距 8 字段
- **多洞嵌套挖洞**：支持矩形嵌套多洞（包络盒消除 + 逐洞独立边距 + 逐洞 10px 黑色边框）
- **椭圆挖洞**：`ellipse_hole` 模式，长径/短径按厘米录入（0 = 依四边距自动推算），支持空白挖去与素材填充
- **L 形挖角独立识别**：两矩形减法推断法 + OCR 兜底，自动检测挖角方向与尺寸；OCR 不可用时纯 CV 几何降级
- **多角 L 形挖角**（V2.2.2）：`l_cuts_cm` 承载最多 4 个 `{corner, cut_w_cm, cut_h_cm}`，渲染走多角并集 mask，与单角 L 形共用同一面板（不新增形状类型字段）
- **OCR 逐角归属**（V2.2.2）：按「最近边 + 凹角分割」把每个数值归入 A/B/C/D/E/F 角色，完全不依赖字母 OCR（字母识别仅作辅助校验）
- **G1 结构一致性闸口**（V2.2.2，不变量常驻）：`notches_detected != notches_consumed` 即拦截并在 message 中告警，不随识别能力提升而移除
- **实时边余量与低置信度警告**（V2.2.2）：面板实时显示四边剩余余量，输入超出外框时红字提示；参数校验失败给出逐边可读提示
- **L 形挖角素材边框自动补全**：沿 L 形两条新切边按素材原始边框层次重绘，内凹角用 `max(dx, dy)` 几何分层保证边框沿 L 形轮廓连续
- **三级边框路由**：Profile 路径 → V13 路径 → 旧 detect_pool_material_borders 路径，任一环节失败自动落到下一环节，向后兼容
- **模板库缓存预热**：目录 mtime 持久化到磁盘缓存，未变化时快速跳过（2ms）；主线程不阻塞预热
- **参数修改即时响应**：尺寸/边距等常用参数改为显式按钮驱动即时生成（`一键生成`/`预览`），避免实时 valueChanged 回调的堆积阻塞（防抖链已整体移除）
- **预览/导出质量区分**：预览用 BILINEAR（快 3-5×），导出用 LANCZOS
- **历史记录功能**：目标文件名 3 天历史记录，三个面板物理隔离独立存储

---

## 架构概览

重构后采用**五层分离架构**，每层职责单一、依赖方向自上而下：

```
┌─────────────────────────────────────────────────┐
│  gui/            UI 层（PyQt5 面板 + 画布）       │
│  ← 信号/槽 → workers/  ·  ← 直接调用 → models/    │
├─────────────────────────────────────────────────┤
│  workers/        线程调度层（QThread Worker）     │
│  启动 → check_cancel → 转发结果 · 不导入 gui/     │
├─────────────────────────────────────────────────┤
│  services/       服务层（OCR / 模板匹配 / 草图解析 / PSD）│
│  外部能力封装，不含 UI 引用                        │
├─────────────────────────────────────────────────┤
│  core/           核心层（几何 / 图像操作 / 圆角 / 裁剪 / 配置）│
│  纯业务逻辑，无 Qt 依赖（config/geometry/image_ops 等）│
├─────────────────────────────────────────────────┤
│  models/         数据模型层（DesignModel）         │
│  纯数据结构，不含 UI 引用和业务逻辑                 │
└─────────────────────────────────────────────────┘
```

**向后兼容**：`core/compat/` 通过 `sys.modules` 别名注册旧导入路径（如 `core.parser` → `services.parser`），旧代码无需改动即可运行。旧 `core/parser/`、`core/pool_designer/`、`core/psd/` 已降级为兼容 shim（目录下仅剩 `__init__.py`），实际实现迁移至 `services/`。`gui/property_panel_workers.py` 同样降级为 shim，实际实现迁移至 `workers/property_panel_workers.py`。

**⚠️ 层依赖现状与上表声明偏差（2026-09-24 实测）**：

- `workers/` → `gui/`：**0 违规** ✅；`services/` → `gui/`、`workers/`：**0 违规** ✅
- **`core ↔ services` 为双向依赖（循环）**：`services/parser/name_parser.py`、`services/sketch_parser/sketch_parser_vision.py` 反向导入 `core`，而 `core/__init__.py` 又导入 `services`
- **`core/app_settings.py:25` 直接依赖 PyQt5**（`QSettings`），与上表「core 无 Qt 依赖」的声明冲突
- `models/design_model.py` 依赖 `core` 属**有意取舍**（业务规则收敛到模型层），非缺陷

> 以上三项的处置方式（修正文档声明 vs 真正拆层）建议单独立项，详见 `ProductSummary/项目审查报告/SmartShapeCrop-项目全面审查报告-20260924.md` §3。

**GUI 面板解耦补充**（V2.2.1 起）：`gui/lshape_panel_bridge.py` 把 `LShapePanel` 对外暴露的 13 个细粒度信号（sketch_* / target_* / lshape_* 等）合并翻译为单一 `lshape_action_requested(action, params)` 粗粒度信号，`PropertyPanel` 只连接这一个信号再按 action 分派；`LShapePanel` 本身零改动，桥接为纯加法、行为等价。

### 代码规模（2026-09-24 实测）

| 层 | 文件数 | 行数 |
|---|---|---|
| `core/` | 20 py | 10,107 |
| `services/` | 16 py | 10,073 |
| `gui/` | 12 py | 6,631 |
| `workers/` | 4 py | 1,250 |
| `models/` | 2 py | 432 |
| `tests/` | 57 py | 13,944 |
| `scripts/` | 38 py（另有 `diagnose/_archive/` 73 py 未计入本表） | 3,912 |
| `packaging/` | 2 py | 1,102 |
| 入口（`main.py` / `process_image.py` / `conftest.py`） | 3 py | 647 |
| **合计** | **154 py** | **≈ 48,098** |

> 口径：`rglob('*.py')` + `read_text().count('\n')`，排除 `.venv` / `_archive`（仓库根） / `.workbuddy` / `.dumate` / `__pycache__` / `build` / `dist`。

---

## 目录结构

```
SmartShapeCrop/
├── main.py                         # 应用入口（PyQt5 主窗口 + 3 标签页 + 模板预设 + 全局异常 crash.log）
├── process_image.py                # 命令行批处理脚本（等比缩放 + 圆角，示例/批处理）
├── conftest.py                     # pytest 全局 fixture + 防御性收集忽略
├── requirements.txt                # Python 依赖（带版本约束）
├── pytest.ini                      # 测试配置（PytestReturnNotNoneWarning 升为 ERROR）
├── crash.log                       # 全局 excepthook 崩溃日志（本地生成，已被 .gitignore 忽略）
│
├── core/                           # 核心业务逻辑层（20 py）
│   ├── __init__.py                 #   公共 API 总入口（聚合子包对外名称 + 触发 compat 别名注册）
│   ├── config.py                   #   统一配置管理（阈值、单位换算、切割损耗、PathResolver 跨平台路径）
│   ├── geometry.py                 #   参数化形状定义 + Mask 生成 + 多角并集 mask + compute_inner_corner_radii
│   ├── image_ops.py                #   图像操作（加载/缩放/平铺/边框合成/文字/导出 + L 形挖角边框补全集成）
│   ├── image_cropper.py            #   裁剪服务主编排层（协调裁剪/边框检测/圆角处理/背景色填充）
│   ├── image_cropper_border.py     #   裁剪边框逻辑（从 image_cropper 拆分；V2.2.2 黑大理石多边框修复落点）
│   ├── image_cropper_mask.py       #   裁剪 mask 逻辑（从 image_cropper 拆分）
│   ├── lshape_border.py            #   L 形挖角素材边框补全（apply_lshape_border_completion 入口 + 三级路由）
│   ├── lshape_border_route.py      #   L 形挖角「描边+色带+细边框」Profile 路由
│   ├── log_setup.py                #   统一日志配置（控制台 + 滚动文件，默认 INFO 级别）
│   ├── app_settings.py             #   历史记录存储层（QSettings/JSON 双通道 + 三源物理隔离）
│   ├── artifact_cleanup.py         #   启动时调试产物自动清理（F19）
│   │
│   ├── compat/                     #   向后兼容层（sys.modules 别名注册）
│   │   └── __init__.py             #     旧路径 → services/ 新子包的重定向映射
│   │
│   ├── corner/                     #   圆角处理子包
│   │   ├── __init__.py             #     子包入口（导出 algorithm/detection/sector_render API）
│   │   ├── algorithm.py            #     单步扇形切割算法（carve_corner_on_mask，numpy 距离场）
│   │   ├── detection.py            #     边框层自动检测（颜色距离 + 亮度突变，classify_gap_layers）
│   │   └── sector_render.py        #     圆角弧线多层边框重绘（仅最外层 + 深色外层保护）
│   │
│   ├── parser/                     #   [兼容 shim] → services.parser（仅 __init__.py）
│   ├── pool_designer/              #   [兼容 shim] → services.sketch_parser（仅 __init__.py）
│   └── psd/                        #   [兼容 shim] → services.psd（仅 __init__.py）
│
├── services/                       # 服务层（外部能力封装，不含 UI 引用；15 py）
│   ├── __init__.py                 #   包入口（parser / sketch_parser / psd 三个子包）
│   │
│   ├── parser/                     #   文件名解析 + 模板库匹配
│   │   ├── __init__.py             #     子包入口（导出 ParsedFilename / TemplateMatcher 等）
│   │   ├── name_parser.py          #     文件名解析（尺寸/方向/圆角/产品名，6 层容错）
│   │   └── template_matcher.py     #     模板库扫描与匹配引擎 v2（mtime 缓存 + 信号槽预热 + pickle 缓存 + 倒排索引）
│   │
│   ├── sketch_parser/              #   草图尺寸解析（单洞 / 多洞 / L 形）
│   │   ├── __init__.py             #     子包入口（导出单洞/多洞/L 形解析符号）
│   │   ├── sketch_parser.py        #     单洞草图解析主编排层（7 步串行 + 全局 OCR + 位置映射 + 稳定性投票）
│   │   ├── sketch_parser_base.py   #     草图解析基类与公共工具
│   │   ├── sketch_parser_cache.py  #     OCR 结果缓存（避免重复调用 Tesseract）
│   │   ├── sketch_parser_margins.py#     边距识别与自洽校验
│   │   ├── sketch_parser_numbers.py#     数字 Token 合并与小数修复
│   │   ├── sketch_parser_vision.py #     矩形检测 / 区域划分 / 方向标签扫描
│   │   ├── sketch_parser_multihole.py #  多洞识别（Phase A-E 消包络盒 + OCR 加权众数投票）
│   │   └── lshape_sketch_parser.py #     L 形草图识别（两矩形减法 + 逐角 OCR 归属 + G1 不变量；V3 算法）
│   │
│   └── psd/                        #   PSD 分层文件处理
│       ├── __init__.py             #     子包入口（导出 PsdLayer / PsdLoadError / 加载与导出函数）
│       └── loader.py               #     PSD 读取/裁剪/合成
│
├── workers/                        # 线程调度层（所有 QThread Worker 集中管理；4 py）
│   ├── __init__.py                 #   包入口（声明三类 Worker 职责）
│   ├── canvas_workers.py           #   画布渲染 Worker（PreviewRenderWorker + ExportSaveWorker）
│   ├── cropper_workers.py          #   裁剪 Worker（CropWorker + AutoMatchWorker）
│   └── property_panel_workers.py   #   水池设计 Worker（PoolRenderWorker / _SketchParseWorker / _WarmupScanWorker / _LShapeParseWorker 等）
│
├── models/                         # 数据模型层（纯数据结构，不含 UI 和业务逻辑；2 py）
│   ├── __init__.py                 #   包入口
│   └── design_model.py             #   DesignModel：CropDesign 薄包装器（apply_ui_snapshot / 克隆快照 / 属性读写）
│
├── gui/                            # PyQt5 界面层（12 py）
│   ├── __init__.py                 #   包入口（导出 PreviewCanvas / PropertyPanel / CropperPanel / LShapePanel）
│   ├── canvas_widget.py            #   预览画布（渲染线程 + LOD 降采样 + ExportSaveWorker 后台导出）
│   ├── cropper_panel.py            #   圆角裁剪面板（上传/识别/预览/导出 + 历史记录 TARGET_SRC_CROPPER）
│   ├── lshape_panel.py             #   L 形挖角独立设计面板（草图上传 + 多角参数 + 一键生成 + TARGET_SRC_LSHAPE）
│   ├── lshape_panel_bridge.py      #   [H-13] LShapePanel 信号桥接适配器（13 细粒度信号 → 1 action 信号）
│   ├── property_panel.py           #   水池设计器属性面板主入口（Facade，聚合子模块 + TARGET_SRC_POOL）
│   ├── property_panel_widgets.py   #   自定义控件（ColorButton / _SketchDropLabel 草图拖拽等）
│   ├── property_panel_workers.py   #   [兼容 shim] → workers.property_panel_workers
│   ├── property_panel_dialogs.py   #   对话框（L 形挖角确认 / 数据回填 / 图层编辑）
│   ├── property_panel_generate.py  #   生成预览逻辑（_GenerateMixin + format_design_validation_error）
│   ├── property_panel_layers.py    #   多层边框编辑 UI（_LayersMixin）
│   └── property_panel_poolbox.py   #   多洞参数面板 + 空挖方式 + 草图识别与边距回填调度（_PoolBoxMixin）
│
├── tests/                          # 单元/集成测试（pytest；2026-09-24 实测 818 passed / 0 failed）
│   ├── conftest.py                 #   全局 fixture + 防御性收集忽略
│   ├── __init__.py
│   ├── run_test.bat
│   ├── test_phase0_multihole.py    #   多洞 Phase 0 回归
│   ├── core/                       #   核心模块测试（22 py）
│   │   ├── test_rounded_corner.py
│   │   ├── test_lshape_render.py / test_lshape_rendering.py
│   │   ├── test_lshape_sketch_parser.py
│   │   ├── test_lshape_border.py / test_lshape_border_route.py
│   │   ├── test_lshape_cutrect.py / test_lshape_cut_rect_anchor_limit.py  # V2.2.3：阶梯 L 形 + 每角上限
│   │   ├── test_g1_invariant.py            # G1 结构一致性不变量
│   │   ├── test_multi_corner_detection.py  # 多角几何检测
│   │   ├── test_multi_corner_ocr.py        # 多角 OCR 逐角归属
│   │   ├── test_stale_decor_guard_behavior.py       # V2.2.3：Stale-Decor 守卫行为锁
│   │   ├── test_debug_residue_removed.py            # V2.2.3：调试残留防回归（AST 断言）
│   │   ├── test_config_tesseract_probe_hardened.py  # V2.2.3：Tesseract 探测加固
│   │   ├── test_disk_cache_unpickler_hardening.py   # V2.2.3：受限 Unpickler
│   │   ├── test_app_version_single_source.py        # V2.2.3：APP_VERSION 单一来源
│   │   ├── test_image_cropper.py / test_image_ops_p2.py
│   │   ├── test_screenshot_lshape.py
│   │   ├── test_name_parser.py / test_template_matcher.py
│   │   └── test_crop_design_validate.py
│   ├── integration/                #   集成测试（10 py：F1-F19 修复验证 / 水池-L 形流程 / LOD 一致性）
│   │   └── test_lod_geometry_consistency.py  # V2.2.3：LOD vs 全分辨率掩膜 IoU（P0-2 防复发）
│   ├── sketch/                     #   草图识别测试（4 py：多洞 / 特征化 / 输入校验 / 逻辑函数）
│   ├── sketch_parser/              #   草图解析测试（2 py：阶梯识别 / 多洞边界）
│   ├── models/                     #   数据模型测试（1 py：design_model）
│   ├── border/                     #   边框测试（3 py：边框修复 / 复杂花纹安全 / 用户案例）
│   └── gui/                        #   GUI 层测试（11 py，离屏运行）
│       ├── conftest.py             #     离屏 QApplication / 设置隔离 / 线程清理夹具
│       ├── test_gui_smoke.py / test_main_window.py / test_signals_contract.py
│       ├── test_property_panel.py / test_property_panel_validation.py
│       ├── test_property_panel_write_paths.py   # V2.2.3：模式回填与旧索引表逐例等价
│       ├── test_lshape_panel.py / test_lshape_panel_staircase.py
│       ├── test_canvas_multihole_overlay.py
│       └── test_cropper_panel.py
│
│   注 1：混入 tests/ 的诊断脚本已于 2026-09-11 全部移至 scripts/diagnose/；最后一个
│         遗留的 tests/core/debug_lshape.py 已于 2026-09-24 迁入 scripts/diagnose/。
│   注 2：GUI 测试以 QT_QPA_PLATFORM=offscreen 离屏运行，不弹真实窗口。
│   注 3：本目录 py 文件数 **56**（排除 __pycache__），2026-09-24 实测；用例基线见"开发指南 → 测试"。
│
├── scripts/                        # 人工诊断/验证脚本（不进 CI，共 38 py）
│   ├── README.md                   #   脚本组织规范与命名约定（2026-09-24 按实测重写）
│   ├── _v13_baseline_render.py     #   V13 边框基线渲染
│   ├── verify_v13_fix.py           #   V13 修复验证
│   ├── split_property_panel.py     #   模块拆分辅助脚本（property_panel）
│   ├── split_sketch_parser.py      #   模块拆分辅助脚本（sketch_parser）
│   ├── diagnose/                   #   案例诊断脚本（25 py）
│   │   ├── _diag_*.py              #     专项诊断（圆角/边框/草图/L 形/阶梯 POC）
│   │   ├── _gui_sim_diag.py        #     GUI 侧模拟诊断
│   │   ├── debug_lshape.py         #     L 形检测调试（2026-09-24 自 tests/core/ 迁入）
│   │   ├── _live/                  #     实时诊断（4 py）
│   │   ├── fix_output/             #     修复前后对照图
│   │   └── _archive/               #     历史脚本归档（73 py：debug_scripts / ocr_scripts / verification_scripts）
│   └── verify/                     #   修复验证/效果演示脚本（5 py）
│
├── packaging/                      # PyInstaller 打包
│   ├── README.md                   #   打包目录说明
│   ├── packageV2.2.3.py            #   【当前唯一入口】V2.2.3 打包脚本，exe 名由 APP_VERSION 派生
│   ├── packageV2.2.2.py            #   上一版入口（保留备查，exe 名硬编码为 V2.2.2，勿再用于出包）
│   └── specs/                      #   .spec 归档（SmartShapeCrop / V2.1 / V2.1.2 / V2.2 / V2.2.2 / V2.2.3）
│
├── _archive/                       # 归档备份（备份快照 / 调试输出 / 临时脚本，不进 Git）
│
├── dist/                           # 打包产物（智能裁剪设计器V2.2.3.exe，218.4 MB，2026-09-24 出包）
├── build/                          # PyInstaller 中间构建产物
├── images/                         # 应用图标（SmartShapeCrop.ico / logo.png）
├── logs/                           # 运行日志 + OCR 诊断截图（自动生成）
└── ProductSummary/                 # 工作与文档沉淀（全部被 Git 跟踪，共 210 个文件）
    ├── 月度总结/                   #   跨模块按日期聚合总览（15 份）
    ├── 圆角裁剪工具/               #   圆角裁剪工作总结（27 份，平铺 YYYYMMDD-主题.md）
    ├── 水池设计器/                 #   水池设计器工作总结（20 份，按 YYYYMMDD/ 子目录）
    ├── L形挖角设计器/              #   L 形挖角演进文档（22 份，阶段 1-6 + 索引）
    ├── SmartShapeCrop分析报告/      #   分析报告（25 份 html/md + assets/ 配图 + patches/ 补丁）
    ├── 项目审查报告/               #   审查类文档主线（8 份，含 2026-09-24 全面审查报告）
    ├── 2026-08/                    #   早期按日期归档（7 份，与"月度总结/"存在重复，见"已知问题"）
    └── 2026-09/                    #   早期按日期归档（4 份，同上）
```

> 根目录另有 `verify_fix_color.jpg`（已被 Git 跟踪的验证产物）、`crash.log`（全局 excepthook 崩溃日志）与 `debug.log`（被输入法进程占用）。
> `项目全面审查报告.md`、`综合形状功能可行性分析报告.md` 已于 2026-09-24 移入 `ProductSummary/项目审查报告/`，详见"已知问题与后续规划"第 9 条。

---

## 快速开始

### 环境要求

- Python 3.10+（开发环境实测 Python 3.13.14）
- Windows 10/11（主要目标平台，支持 PyInstaller 打包 exe）
- **可选（水池设计器/L 形挖角草图 OCR）**：Tesseract-OCR 引擎（需含 `chi_sim` + `eng` 语言包）
  - 源码模式：`core.config.PathResolver` 按以下顺序自动探测，**不硬编码任何盘符**（`TESSERACT_SEARCH_PATH_TEMPLATES`）
    1. `{resource_dir}/tesseract`（资源目录内便携版，最高优先）
    2. `{exe_dir}/tesseract`、`{exe_dir}/_internal/tesseract`（与 exe 同级或 `_internal` 下的便携版）
    3. `C:\Program Files\Tesseract-OCR`、`C:\Program Files (x86)\Tesseract-OCR`
    4. Linux/macOS 常见路径（`/usr/local/opt/tesseract`、`/opt/homebrew/opt/tesseract` 等）
    5. 环境变量 `TESSERACT_PATH`（**非常规安装位置请走此路径显式指定**）
    6. 兜底：`shutil.which('tesseract')` + `tesseract --list-langs` 语言包校验
  - 打包模式：V2.2.3 默认将本机 Tesseract **内嵌进 exe**，用户机器免安装即可使用草图 OCR
  - 未安装 Tesseract 时：水池设计器草图尺寸识别将无法完成（7 步法依赖 OCR 数值识别）；L 形挖角识别降级为纯 CV 几何推断（可用但精度略降）；圆角裁剪功能本身不依赖 OCR

**开发环境实测版本**（2026-09-16，`F:\SmartShapeCrop\.venv`）：

| 组件 | 实测版本 | requirements 约束 |
|---|---|---|
| Python | 3.13.14 | 3.10+ |
| PyQt5 | Qt 5.15.2 | >=5.15.0 |
| Pillow | 12.3.0 | >=9.0.0 |
| numpy | 2.5.3 | >=1.21.0 |
| opencv-python-headless | 5.0.0 | >=4.5.0 |
| psd-tools | 1.19.0 | >=1.9.28 |
| pytesseract | 0.3.13 | >=0.3.10 |
| PyInstaller | 6.22.2 | 打包脚本自动检测/安装 |
| Tesseract-OCR | 由 `TESSERACT_PATH` 指向（含 `chi_sim` + `eng`） | 外部引擎 |

### 安装依赖

```bash
pip install -r requirements.txt
```

依赖清单（带版本约束）：

| 包 | 版本要求 | 用途 |
|---|---|---|
| PyQt5 | >=5.15.0 | GUI 界面 |
| Pillow | >=9.0.0 | 图像处理核心 |
| numpy | >=1.21.0 | 像素级向量化运算 |
| psd-tools | >=1.9.28 | PSD 分层文件读取 |
| opencv-python-headless | >=4.5.0 | 形态学运算 + 草图矩形检测（headless 版避免与 PyQt5 Qt 插件冲突） |
| pytesseract | >=0.3.10 | 草图 OCR 数字识别（需 Tesseract 引擎，缺引擎时 OCR 不可用、其余功能正常） |
| pytest | >=7.0.0 | 单元测试（开发环境） |

> ⚠️ 切勿安装 `python-qt5` 包（与 PyQt5 同名冲突，会破坏 DLL 加载），详见"已知问题"。

### 启动 GUI

```bash
python main.py
```

启动后界面分两部分：

- **左侧**：预览画布（水池设计器渲染 / 圆角裁剪预览 / L 形挖角预览 / 草图直接显示）
- **右侧标签页**（3 个）：
  - **圆角裁剪工具**：上传成品图 → 自动识别/手动输入参数 → 预览 → 导出
  - **水池设计器**：参数化设计（矩形嵌套/椭圆挖孔 + 多层边框）或手绘草图上传 → QThread 后台异步解析 → 自动回填 → 生成预览
  - **L形挖角设计**：独立承载 L 形挖角参数设置（单角/多角）+ 草图上传 + 一键生成

> V2.2.3 打包版（**已出包，2026-09-24**）：双击 `dist/智能裁剪设计器V2.2.3.exe`（218.4 MB）即可运行（首次启动需解压内嵌资源，等待 5-15 秒）。
> 已通过「交付物时效铁律」核对：exe mtime 10:34:11 ≥ 最新源码 10:15:32。`dist/` 中仍保留 V2.2.2 产物备查。

### 命令行批处理

`process_image.py` 是图片等比缩放 + 圆角处理的命令行示例脚本（演示 crop 管线，用 `apply_border_only_corners`）：

```bash
# 使用默认参数（指向 psd_demo 示例素材，如无该目录请先用 --src 指定实际图片）
python process_image.py

# 指定参数
python process_image.py --src "D:\path\to\源图.jpg" --out-dir "D:\path\to\out" `
    --out-name "输出.jpg" --target-w 41.0 --target-h 55.0 --corner-r 2.0 --dpi 150
```

参数说明：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--src` | 脚本目录 `psd_demo` 内示例图 | 源图路径 |
| `--out-dir` | 脚本目录 `psd_demo` | 输出目录 |
| `--out-name` | 示例输出名 | 输出文件名 |
| `--target-w` / `--target-h` | 41.0 / 55.0 | 目标尺寸（厘米） |
| `--corner-r` | 2.0 | 圆角半径（厘米） |
| `--dpi` | 150 | 输出 DPI |

### 运行测试

```bash
# 全部测试（2026-09-24 实测 818 passed / 0 failed / 0 error，217.1 秒）
python -m pytest tests/ -q

# 仅圆角测试
python -m pytest tests/core/test_rounded_corner.py -v

# 草图相关测试
python -m pytest tests/sketch/ -v

# L 形挖角测试（渲染 + 草图解析 + 边框补全 + Profile 路由 + G1 + 多角 + 阶梯）
python -m pytest tests/core/test_lshape_render.py tests/core/test_lshape_sketch_parser.py tests/core/test_lshape_border.py tests/core/test_lshape_border_route.py tests/core/test_g1_invariant.py tests/core/test_multi_corner_detection.py tests/core/test_multi_corner_ocr.py tests/core/test_lshape_cutrect.py -v

# 集成测试（F1-F19 修复验证 + 水池-L 形数据流 + LOD 一致性）
python -m pytest tests/integration/ -v
```

> 说明：`pytest.ini` 将 `PytestReturnNotNoneWarning` 升为 ERROR，防止 `return <bool>` 替代 assert 导致断言失效。根 `conftest.py` 通过 `collect_ignore_glob` 防御性屏蔽 `scripts/`、`_archive/`、`packaging/`、`ProductSummary/` 下的 `test_*.py` 命名文件，防止误收集。
>
> ⚠️ **本机跑全量的固定口径**：宿主会按「单轮累计删除数」拦截批量删除，表现为与代码无关的成批 `error`（曾出现 36 errors 假红）。
> 建议用 `CODEBUDDY_SAFE_DELETE_ENABLED=0 python -m pytest tests/ -q -p no:cacheprovider --basetemp=.pytest_tmp/_bt`。
> 凡出现「与本次改动无因果关系的成批 error」，先用**单文件单独跑**交叉验证，再怀疑环境。

### 打包发布

```bash
# 使用当前 V2.2.3 打包入口，生成单文件 exe（默认）
python packaging/packageV2.2.3.py

# 目录模式（更稳定）
python packaging/packageV2.2.3.py --onedir

# 调试模式（带控制台窗口）
python packaging/packageV2.2.3.py --debug

# 清理旧构建后打包
python packaging/packageV2.2.3.py --clean

# 不内嵌 Tesseract（默认已内嵌，用户免安装 OCR）
python packaging/packageV2.2.3.py --no-tesseract
```

打包要点（V2.2.3）：

- 产物：`dist/智能裁剪设计器V2.2.3.exe`（单文件，**实测 218.4 MB**，其中内嵌 Tesseract-OCR 约 115 MB）
- **exe 名与打包横幅由 `core/config.py` 的 `APP_VERSION` 派生**，脚本内不再硬编码版本号 —— 发版只需改 `APP_VERSION` 一行
- 自动内嵌本机 Tesseract-OCR 到 exe 内部，用户机器免安装即可使用草图 OCR
- hidden imports 声明 `services.*` / `workers.*` / `models.*` / `core.*`；
  ⚠️ `core/psd/`、`core/parser/`、`core/pool_designer/` 均为**只有 `__init__.py` 的兼容 shim**，真实实现在 `services/`，
  **不要**声明 `core.psd.loader` 之类子路径（该文件不存在，会报 `Hidden import not found`），应声明 `services.psd.loader`
- 打包失败时 onefile 自动回退 onedir；崩溃时在 exe 同目录生成 `crash.log` 便于排障
- **交付物时效铁律**：出包后须确认 `dist/*.exe` 时间戳 ≥ 最新源码时间戳
  （✅ **2026-09-24 实测通过**：`智能裁剪设计器V2.2.3.exe` 10:34:11 ≥ 源码 10:15:32；打包工具 PyInstaller 6.22.3）

---

## 版本演进

### V2.2.2 功能版本（2026-09-16）

**新增功能**：

- **多边 L 形挖角（多角同时挖）**：`CropDesign.l_cuts_cm` 承载最多 4 个 `{corner, cut_w_cm, cut_h_cm}`；`LShape.cuts` + `build_lshape_mask(cuts=...)` 生成**多角并集 mask**；渲染（`image_ops` 5 处调用点）与边框补全（`lshape_border`）全部改为遍历 `cut_specs()`。与「单角 L 形」**共用同一面板**——按锚定角推导形状，**不新增 `shape_type` 字段**
- **G1 结构一致性闸口（不变量常驻）**：`_apply_g1_invariant` 覆盖 `parse_lshape_sketch` 全部退出路径，`notches_detected != notches_consumed` 即拦截并在 message 中明确告警，`debug['g1_invariant_applied']` 恒为 True；明确设计为**长期不变量，不随识别能力提升而移除**
- **OCR 逐角归属**：`_attribute_cut_ocr_per_corner` / `_resolve_cut_pair_per_corner` 按「最近边 + 凹角分割」把每个 OCR 数值归入 A/B/C/D/E/F 角色，**完全不依赖字母 OCR**（字母识别仅作辅助校验），消除此前数值漂移（45.8 → 50.5）
- **实时边余量与低置信度警告**：L 形面板实时显示四边剩余余量，输入超出外框时红字提示；低置信度时面板边框高亮
- **逐边参数校验与可读提示**：`CropDesign.validate()` 新增「同一条外边上挖角尺寸之和 + 0.5cm 余量 < 该外边长度」约束；`format_design_validation_error()` 将 `ValueError` 转成「参数校验失败：…请减小该边相邻挖角尺寸，或增大画布尺寸。」
- **水池设计器椭圆挖洞**：`ellipse_hole` 模式支持长径/短径按厘米录入（0 = 依四边距自动推算），支持**空白（挖去不留白）**与**素材填充**两种挖空方式
- **黑大理石多边框修复**：`core/image_cropper_border.py` + `core/corner/detection.py` 的多边框层扫描与黑大理石素材层结构判定修正
- **多边 L 形挖角内凹角线条溢出修复**：`core/lshape_border.py` / `lshape_border_route.py` 的 `inset_top` / `inset_right` 越界修正，避免内凹角线条溢出到非挖角区
- **圆角检测步长修复、L 形挖角重绘避免漏线**

**V2.2.2 打包**：`packaging/packageV2.2.2.py` + `packaging/specs/智能裁剪设计器V2.2.2.spec` 新建并成为唯一入口，V2.2 / V2.2.1 旧脚本移入 `packaging/legacy/`（⚠️ 该目录已于 2026-09-17 整目录删除，见"已知问题"第 12 条）。

> ⚠️ 版本号命名说明：Git 提交信息中 `v2.2.1-*` 与本节的 `V2.2.2` 存在历史错位（多边 L 形挖角的多数量提交按 `v2.2.1-` 前缀记录，最终以 `v2.2.2-打包程序-多边L形挖角设计功能实现+椭圆空白挖洞` 收口）。**功能分版以本节与打包脚本头部注释为准。**

### V2.2.1 质量问题修复版本（2026-09）

**新增/变更**：

- **`gui/lshape_panel_bridge.py`**：新增 LShapePanel 信号桥接适配器（[H-13]），13 个细粒度信号 → 1 个 action 信号
- **架构分层重构**：`core/` 迁移出 `services/`（parser / sketch_parser / psd）、`workers/`（QThread Worker 集中管理）、`models/`（设计数据模型）三个新层；`core/compat` 以 `sys.modules` 别名保持旧导入路径向后兼容
- **全面审查修复**：Critical 级 C-01 ~ C-05 与 High 级 H-01 ~ H-15
- **L 形边框颜色检测修复**、**圆角裁剪抗锯齿修复**、**水池挖洞素材填充失真修复**

### V2.2 功能版本（2026-09）

**新增功能**：

- **L 形挖角素材边框自动补全**：对自带边框的池素材图，L 形挖角后沿两条新切边按素材原始边框层次重绘（`core/lshape_border.py`）
- **三级边框路由**：Profile 路径 → V13 路径 → 旧路径自动回退（`core/lshape_border_route.py`），新增「描边+色带+细边框」结构的 1D 颜色剖面扫描识别
- **L 形挖角独立 GUI 面板**：从水池设计器拆出独立标签页（`gui/lshape_panel.py`），草图上传 + 目标文件名 + 一键生成，独立历史记录源
- **模板库缓存预热**：目录 mtime 持久化，未变化快速跳过；主线程不阻塞
- **参数修改即时响应**：valueChanged 已全部 DISCONNECTED，参数修改改为显式按钮驱动（`一键生成`/`预览`），避免实时回调堆积阻塞；防抖链已整体移除

**核心修复**：

- 圆角裁剪 T1-T5：仅最外层圆角化（`only_outermost` + `protect_content` 常驻）、圆角边界白线（tol 1.0→2.0）、深色弧线残留（ring_region 收窄）
- L 形渲染三层问题：外侧黑框 L 形切割、挖角区米色残留、L 形边框拐点不相交
- L 形草图识别精度：删除 MORPH_CLOSE、凸包差法 + 大 bbox 过滤、三级硬约束筛选
- cut 角 mask 四角坐标独立化（image_ops），修复除 bl 外三角切边错位
- worker 生命周期管理：裁剪面板安全退役/退出链（避免 QThread 销毁崩溃）

**性能优化**：

- 连通分量向量化（`np.isin` 替代 Python 循环）：395 连通分量从 12.8s 降至毫秒级
- LOD 智能降采样（scale=0.5 + BILINEAR）：消除高细节素材马赛克伪影
- JPG 导出异步化（QThread 后台 + 可取消）：消除大图导出 UI 冻结

### V2.1.2

- 水池设计器**多洞功能**：识别并解析草图中多个内挖孔洞（Phase A-E 五阶段算法 + OCR 加权众数投票），逐洞独立边距 + 逐洞 10px 黑色边框
- 草图识别拆分为 9 大子模块（base/cache/margins/multihole/numbers/vision/lshape 等）
- GUI 子模块拆分（对话框/生成器/图层/多洞/控件/工作线程）
- 圆角裁剪 border/mask 独立模块
- 圆角直角尾巴与白色空隙修复（ring_region 与 d_region 上界统一）
- F1-F19 集成修复验证

### V2.1 / V2.0 / V1

- V2.1：文件名解析 6 层容错、模板库匹配、多层边框检测重构
- V2.0：圆角裁剪 + 水池设计器参数化合并为统一 GUI
- V1：命令行圆角裁剪原型

### V2.2.3 功能版本（2026-09-24）

**新增功能**：

- **单边阶梯 L 形挖角**：`CutRect` + `CropDesign.l_cut_rects` + `_validate_l_cut_rects`，渲染走统一掩膜 `_build_design_lshape_mask`（`_draw_staircase_union_layers`）；与多边 L 形**共用同一面板**，仍未新增 `shape_type` 字段
- **每锚定角上限统一**：`MAX_L_CUT_RECTS_PER_ANCHOR` + `limit_l_cut_rects_per_anchor()`，`validate()` 与两个写入端共用同一口径（原为「每角 ≤3」与「总数 `[:3]`」两套口径）

**正确性与安全修复**：

- **LOD 几何字段补齐缩放（P0-2）**：`_make_lod_design` 补缩放 **4 族**字段（`l_cut_rects[].*` / `corner_{tl,tr,bl,br}_cm` / `ellipse_diameter_{w,h}_cm` / `pool_holes_cm[].*`），实测掩膜 IoU 由最低 **0.0000** 升至 **0.9692–0.9946**
- **删除恒真死守卫（P0-3）**：`lshape_cut_w/h` 字段从不存在，两处守卫共 2×2 条恒真合取项**纯删除**（判定逐例等价）
- **受限 Unpickler（P0-4）**：模板缓存反序列化改用白名单受限 Unpickler，消除代码执行面，119/119 存量缓存兼容

**代码卫生与工程化（P1 批次 + 后续批次）**：

- 删除热路径 8 处 `print(flush=True)` 与 `_dbg` 调试开关（`core/image_ops.py` 净 **−61 行**）
- `os.popen` 起 shell → `subprocess.run(list)`（P1-7）
- `core/config.py` 新增 `APP_VERSION`，成为版本号**唯一事实来源**（P2-3）
- 新增 `packaging/packageV2.2.3.py` + `specs/智能裁剪设计器V2.2.3.spec`，exe 名由 `APP_VERSION` 派生
- `core/artifact_cleanup.py` 改为**剪枝遍历**（`_is_link_node()` + `_iter_tree()`）：清理调试产物时不再跟随符号链接与 **Windows junction** 越界删除目录外的真实文件（P2-7）
- 实测基线 **818 passed / 0 failed / 0 error**（217.1s；带 `--basetemp=.pytest_tmp/final_h15`，耗时不可与 ~103s 的口径直接比较）

> 完整审查与整改记录见 `ProductSummary/项目审查报告/SmartShapeCrop-项目全面审查报告-20260924.md`。

---

## 核心模块说明

### 1. 圆角裁剪算法（core/corner/algorithm.py）

圆角处理是本项目最复杂的子系统，采用**单步扇形切割算法**（拒绝先挖方再填色的两步法，避免中心区域被重复着色）：

```
步骤1: 把角落 r×r 正方形区域设为 0（切掉尖角）
步骤2: 把"矩形内部的 1/4 圆"填回 255（保留圆弧）
→ 切掉的是 L 形（正方形减去 1/4 圆），只切尖角，保留圆弧
```

实现为 `carve_corner_on_mask`（支持 fill_value 和 inverse 参数，单 mask 两步绘制），内部采用**纯 numpy 距离场算法**（v2 重写），在 r×r 正方形内先挖掉尖角、再按距离场填回 1/4 圆弧，几何上保证过渡点无 C 形缺口、无过绘，不依赖 PIL pieslice 栅格化。

**PIL 屏幕坐标系角度映射**（y 轴向下）：

| 角 | 角度范围 | 圆心位置 |
|---|---|---|
| TL（左上） | 180° → 270° | (x+r, y+r) |
| TR（右上） | 270° → 360° | (x+w-r, y+r) |
| BL（左下） | 90° → 180° | (x+r, y+h-r) |
| BR（右下） | 0° → 90° | (x+w-r, y+h-r) |

**仅最外层圆角化（only_outermost）**：

- 圆角处仅绘制最外层边框圆弧，内层花纹保持直角
- `protect_content` 常驻开启，确保内层花纹不被误圆角
- `angle_in_corner_sector` 统一判定角扇区，避免内层被误判为需要圆角
- 裁剪 mask（边框带限制）与边框重绘有效性 mask（完整扇形）分离，防止圆角处边框变薄

**深色外层边框保护机制**：

- 最外层(i=0)深色边框（max RGB ≤ 150）永不判为间隙
- 仅浅色外层（max RGB > 150）可通过邻居差异判定为间隙
- 间隙层判定统一为 `classify_gap_layers` 单一来源

**圆角边界白线与深色弧线修复**：

- `tol` 一致性：圆角 mask 边界容差统一 1.0 → 2.0，消除白线残留
- `ring_region` 限制在 `border_zone`（直边区），防止保护到圆弧外侧的深色边框残留
- `inner_cut` 限幅，防止圆角内层误圆角化
- 白色空隙多层结构感知重绘，保证断触处粗细一致

### 2. 边框层自动检测（core/corner/detection.py）

双算法并行检测：

| 算法 | 原理 | 阈值 | 适用场景 |
|---|---|---|---|
| 颜色距离检测 | RGB 欧氏距离 > 阈值视为不同颜色 | 15 | 逐层颜色识别（黑/白/棕交替边框） |
| 亮度突变检测 | R+G+B 总和三线均值差分 > 阈值视为边界 | 25（×3=75 实际生效） | 嵌套矩形边界扫描 |

**防误判机制**（防止内容区/花纹被误判为边框层）：

- **厚度硬上限**：单层 ≤ 2cm，所有层累计总厚度 ≤ 3cm
- **层数硬限制**：真实边框通常不超过 4 层
- **薄边框跳变检测**：薄边框（≤1cm）后出现 3 倍厚度跃变 → 判定为内容区伪边框
- **花纹周期截断**：A↔B 颜色交替重复模式时截断
- **黑大理石层结构判定**（V2.2.2）：深色高纹理素材的多边框层扫描修正

### 3. 圆角弧线边框重绘（core/corner/sector_render.py）

采用**仅最外层策略**（`only_outermost=True`），并增加三层清理遍历：

- 仅绘制最外层边框圆弧，内层边框与间隙层保持原图状态
- 间隙层智能处理：相邻层颜色对比防误判；间隙层用原间隙色填充；颜色通道极差法区分均匀间隙（清空为背景色）与装饰间隙（保留原贴图）；预渲染清理 + 后处理清理 + 深度超限清理三层遍历
- 有效边框深度限制：圆角处仅渲染半径 70% 深度范围内（硬上限 ~3cm）
- 装饰像素保护：直边延伸区颜色匹配过滤，对角内区取内容参考色，与内容参考色欧氏距离 > 15 → 强制绘制
- 边界完整性：极坐标→离散像素映射留 2px 容差；角度边界包含两端 + TR 角 360° 环绕处理

### 4. 文件名解析（services/parser/name_parser.py）

支持从中文文件名提取结构化信息，示例：

```
双面格-定制-定制尺寸-简织;竖版54x41cm右下角圆角半径2cm.jpg
├─ 产品名: 双面格-定制-定制尺寸-简织
├─ 方向:   竖版
├─ 尺寸:   41 x 54 cm（竖版短边在前）
└─ 圆角:   {tl:0, tr:0, bl:0, br:2.0}
```

**支持的圆角格式**：四角（`4个圆角半径2cm`）、两角（`左下角和右下角做3cm半径圆弧角`）、单角（`左下角圆角半径3.1cm`）、口语（`左下角是圆角3.1cm半径`）。

**尺寸解析容错**：6 层策略从高到低（带单位正则 → 无单位正则 → 宽松正则 → findall → 取前两个数字 → 字符级手动扫描），支持全角/特殊字符/隐藏字符。

### 5. 裁剪模式（core/image_cropper.py）

| 模式 | 说明 |
|---|---|
| `simple_resize` | 简单缩放（默认，LANCZOS 高质量，不裁剪不留白） |
| `cover` | 裁剪填满（裁掉超出部分，可能损失内容） |
| `contain` | 留白填充（完整显示，四周补背景色） |
| `light_cover` | 轻度裁剪（最多裁剪 15%，平衡内容与比例） |
| `auto` | 智能模式（自动选择 cover 或 contain） |

### 6. L 形挖角素材边框补全（core/lshape_border.py + lshape_border_route.py）

当对带有自绘边框的素材图应用 L 形挖角时，挖掉的角落区域的两条新边缘需要绘制与素材图一致的边框层，使 L 形成品在视觉上呈现完整的外框。

**多角支持（V2.2.2）**：入口 `apply_lshape_border_completion` 接受 `cuts: list[(corner, cut_w_px, cut_h_px)]`，为空时回退旧单角参数（`cut_corner` / `cut_w` / `cut_h`），对每个 cut 独立计算边 bbox 并补边，向后兼容。

#### 三级边框路由（向后兼容）

入口 `apply_lshape_border_completion` 按以下优先级自动路由，任一环节失败自动落到下一环节：

```
手动参数（manual_*）非 None → V13 路径（黑描边 + 主色带，手动覆盖）
                              │
自动路径：Profile 路径（detect_border_profile）
         ├─ 首层厚黑且 V13 可命中 → 让位 V13 路径（已验证场景）
         ├─ Profile 命中 → patch_lshape_cut_layers（N 层推广）
         └─ 绘制失败 → 回退
                              │
         V13 路径（detect_border_v13）
         ├─ 黑描边 + 主色带结构 → patch_lshape_cut
         └─ 返回 None → 回退
                              │
         旧路径（detect_pool_material_borders）
         └─ 纯黑框等兼容结构 → draw_border_layers_on_cut_edges
```

#### Profile 路径（core/lshape_border_route.py）

针对 V13 / 旧路径都失效的素材（最外层不是黑描边，而是米色/白色边距 + 细线 + 点带 + 细框）：

- **1D 颜色剖面扫描**：素材四条边由外向内扫描（多条扫描线取均值抹平点状花纹）
- **锚点对齐**：抗 1~6px 出血白边，保证四边层序一致、投票颜色真实
- **有序层分割**：输出 `[(color, thickness_px), ...]`（外→内，最多 3 层，含外边距层）
- **截断到第二条细线**：描边 + 色带 + 内框线 = 3 层封顶
- **厚度按 scale 换算**到画布坐标系后调用 `patch_lshape_cut_layers`（N 层推广）
- **内凹角几何分层**：用 `max(dx, dy)` 距离映射到层区间 `[offs[k], offs[k+1])`

典型素材结构映射：

| 素材 | 剖面结构 | 层数 | 路由 |
|---|---|---|---|
| 克罗印花 | 黑描边 + 棕色带（直通内部，限厚） | 2 | Profile 让位 V13 |
| 蔓生花 | 黑描边 + 米色边距 + 细线 | 3 | Profile |
| 中古雨林 | 黑描边 + 白边距 + 框线 | 3 | Profile |
| 庄园秘境 | 出血白边（锚点跳过）+ 深黑带 + 米底 | 2 | Profile |

#### 关键约束

- 使用原始素材图检测（避免拉伸像素畸变），用 scale 因子换算到画布坐标系
- bg_color 用实际素材底色（白色硬编码会把米色等底色误判为边框层）
- 厚黑首层让位 V13（首层近黑且厚度 ≥ 50px 时先问 V13）
- 无边框跳过补全（边缘-中心色差 < 50 或总厚 > 短边 30% 判定非真实边框）
- 补全过程异常静默跳过，不影响后续渲染
- **内凹角线条溢出修复（V2.2.2）**：`inset_top` / `inset_right` 越界修正，避免补边绘制溢出到非挖角区

### 7. 水池设计器草图识别（services/sketch_parser/）

从用户上传的手绘草图自动识别 **8 项关键数值**：外框宽/高、内孔宽/高、上下左右边距。支持**方向标签图**和**无标签双矩形图**两种输入场景。

#### 核心识别管道（7 步串行流程）

```
Step1: 矩形检测（嵌套对选择 + 面积3-97%过滤 + 边界伪矩形剔除 + 内框暗色区域回退 + 凸包差法过滤大bbox）
Step2: 区域划分（8-zone几何分区 + 角点间隙归属基于间隙宽度判定）
Step3: 多尺度OCR扫描（多尺度/PSM/预处理组合 + 相邻数字Token合并）
Step4: 小数修复（3阶段：丢失小数点修复 + 拆分小数点修复 + 严格去重自适应阈值）
Step5: 方向标签锁定（独立扫描8次：2 scales × 2 languages × 2 PSM + 33%硬边带约束）
Step6: 空间映射（物理位置映射 + 圆数过滤100/50/25倍数10%范围）
Step7: 几何校验（inner = outer - margin_sum，5%偏差强制修正 + 自洽评分选优）
```

#### OCR 稳定性投票机制

对关键 ROI 区域多次识别并取众数：`_make_preprocess_variants` 生成投票源；`_vote_by_position` 位置聚类 + 众数投票，票数相同时按置信度总和决胜；投票置信度 = 原始最大置信度 × (0.5 + 0.5 × 众数占比)。

#### 多洞嵌套挖洞识别

- Phase A-E 五段算法：包络盒消除 → 逐洞区域划分 → 多尺度 OCR → 加权众数投票 → 逐洞几何校验
- 逐洞独立边距（HoleInfo 字段扩展）+ 逐洞 10px 黑色边框（几何差集算四边环）
- 0.5cm 量化加权众数，消除 OCR 偶发误差

#### 椭圆挖洞（V2.2.2 完善）

`ellipse_hole` 模式下由 `CropDesign.ellipse_px()` 产出椭圆几何：`ellipse_diameter_w_cm` / `ellipse_diameter_h_cm` 大于 0 时作为用户手动输入的直径直接使用，为 0 时按四边距自动推算；旧比例字段 `ellipse_rx_ratio` / `ellipse_ry_ratio` 保留以兼容旧设计文件，但**不再参与几何计算**。挖空方式由「挖空方式」下拉控制：`空白(挖去不留白)` 置 `pool_hole_transparent=True`（并清理内挖素材残留字段）；`素材填充（花型匹配填充）` 置 `pool_hole_transparent=False`。

#### L 形挖角草图识别（services/sketch_parser/lshape_sketch_parser.py）

- **算法版本**：`_ALGO_VERSION = 3`（2026-09-05 起 cut 尺寸改用 bbox 边界距离，抗数字粘连）
- 两矩形减法推断法：识别两个嵌套矩形相减得出 L 形挖角区域
- 自动方向检测：四象限白色像素比例判定挖角方向（tl/tr/bl/br）
- 挖角尺寸像素→厘米换算（含 1cm 材料损耗补偿）；外框完整尺寸识别
- OCR 兜底路径：Tesseract 不可用时降级为纯 CV 几何推断
- **多角检测（V2.2.2）**：解除识别层三个单角收敛点（`argmax` / `or` 短路 / `max`），`_detect_lshape_geometry` 可同时返回多个凹角
- **OCR 逐角归属（V2.2.2）**：`_assign_labels_by_geometry` 按「最近边 + 凹角分割」把数值归入 A/B/C/D/E/F 角色；`_resolve_cut_pair_per_corner` 逐角解析 E/D 对；**不依赖字母 OCR**
- **G1 结构一致性闸口（V2.2.2，常驻不变量）**：`_apply_g1_invariant` 被所有退出路径调用，`notches_detected != notches_consumed` 即 `g1_blocked=True`，message 追加 `⚠️ G1 闸口：检测到 N 个凹角，当前仅应用 M 个（…），其余角位 […] 需手动补充`
- **三级硬约束筛选**：cut_ratio ∈ [0.03, 0.75]、距离 bbox 角 < 40% 对角线，评分 base_score × proximity_factor × ratio_factor + balance_factor 多候选消歧
- **精度修复**：删除 MORPH_CLOSE（避免数字注记与 L 形轮廓合并导致假凹角）、凸包差法 + 大 bbox 过滤
- **结果字段**：`LSketchParseResult` 含 `corner / outer_w_cm / outer_h_cm / cut_w_cm / cut_h_cm / top_w_cm / right_h_cm / notch_w_cm / notch_h_cm / self_consistency / notches_detected / notches_consumed / cuts_cm / debug`

#### 关键约束与安全机制

| 机制 | 说明 |
|---|---|
| 33%硬边带约束 | margin_top cy≤H×0.33、margin_bottom cy≥H×0.67、margin_left cx≤W×0.33、margin_right cx≥W×0.67 |
| 边距合理性硬门槛 | 边距不可能大于短边×80% |
| 整数优先排序 | 整数边距在实际应用中更常见 |
| OCR 自洽检查 | 0.10 ≤ implied_ratio ≤ 0.90 时信任 OCR 值 |
| 内框可靠性评级 | 面积<5%或纵横比>15 → 不可靠 → 放弃几何反推 → OCR 边距反推 |
| 几何值 50%上限 | 边距几何值 > 外框对应边×50%直接拒绝 |
| OCR 边距自洽检测 | OCR 左右(上下)和内推内框∈[10%,90%]外框 → 优先采用 OCR 值 |
| 相邻数字 Token 合并 | "左1"+"2"→12、"7"+".5"→7.5 |
| 方向标签三重门 | 百位数检查 + 数量级差异(10×) + 差异>50%放弃标签 |
| G1 结构一致性 | 检测凹角数 ≠ 消费凹角数 → 拦截 + 告警（永久不变量） |

### 8. 水池模式素材图渲染（core/image_ops.py）

**渲染执行顺序**（确保四边边框完整）：保存非白色素材像素 → 白色填充内孔区域 → 恢复素材像素 → 绘制 10px 黑色边框线（最后绘制确保最上层）→ **L 形挖角素材边框补全**（rect_lshape + 池素材 + 非 tile 时触发）。

**多角 L 形渲染（V2.2.2）**：`design.l_shapes_px()` → `LShape.cut_specs()` → `build_lshape_mask(..., cuts=...)`。实测调用点分布：

| 位置 | 用途 | 是否传多角 `cuts=` |
|---|---|---|
| `image_ops.py:762` | 外轮廓 L 形 mask | ✅ `cuts=lshape.cut_specs()` |
| `image_ops.py:1175` | 边框带收缩 mask（cut 尺寸按 `border_width_px` 内缩） | ✅ 多角列表推导 |
| `image_ops.py:1523` | 内挖像素 mask（`_get_inner_pixel_mask`） | ✅ `cuts=lshape.cut_specs()` |
| `image_ops.py:812` | 纯内框矩形 mask（`_render_lshape_cut`） | ❌ cut 宽高传 0，仅做圆角 |
| `image_ops.py:1079` | 纯内框矩形 mask（`_fill_lshape_cut_area`） | ❌ cut 宽高传 0，仅做圆角 |

`l_shapes_px()` 另在 `:1253` 取 `cut_specs()` 供 `apply_lshape_border_completion` 逐角补边。**LOD 路径同步按比例缩放 `l_cuts_cm`**（`image_ops.py:544-551`），避免低分辨率预览与全分辨率导出形状不一致。

**素材适配模式**：水池模式默认使用 stretch 模式（直接拉伸到目标尺寸不裁剪，避免 cover 模式因方向不匹配裁剪边框）；系统优先匹配方向一致的素材。

**预览渲染优化**：quality 参数区分 preview（BILINEAR，快 3-5×）与 export（LANCZOS）；复用 inner_mask 省去一次 mask 计算；LOD 智能降采样（高细节素材 scale=0.5 + BILINEAR）。

**rect_hole 性能优化**：EDT 距离变换用圆角矩形差集替代（数学等价，33×加速）；compute_border_bands 单 mask 两步 PIL 绘制（7×加速）；carve_corner_on_mask 支持 fill_value/inverse；L 形凹角和椭圆偏移降级为形态学腐蚀；**连通分量向量化**（`np.isin` 替代 Python 循环，395 连通分量从 12.8s 降至毫秒级）。

### 9. 模板库缓存预热（services/parser/template_matcher.py）

- 目录 mtime 持久化到磁盘缓存，未变化时快速跳过（2ms）
- 主线程不阻塞预热：信号槽触发 worker
- 形状+方向关键词严格匹配 + 有方向比例匹配
- TemplateMatcher 公共方法使用 threading.RLock 保证线程安全
- pickle 缓存 + 尺寸比例分桶 + 倒排索引优化

### 10. 几何模型与参数校验（core/geometry.py）

`CropDesign` 的 mode 为三值 `Literal`：

| mode | 语义 | 关键字段 |
|---|---|---|
| `rect_hole` | 矩形嵌套挖洞（含多洞、椭圆孔以外的矩形语义） | `inner_margin_*` / `pool_holes_cm` |
| `rect_lshape` | L 形挖角（单角 / 多角） | `l_corner` / `l_cut_w_cm` / `l_cut_h_cm` / **`l_cuts_cm`** |
| `ellipse_hole` | 椭圆挖洞 | `ellipse_diameter_w_cm` / `ellipse_diameter_h_cm` |

**多角字段约定**：`l_cuts_cm` 为 `[{"corner": "tr", "cut_w_cm": 28, "cut_h_cm": 8}, ...]`，最多 4 项；**空列表表示沿用旧的单角字段** `l_corner` / `l_cut_w_cm` / `l_cut_h_cm`。`LShape.cut_specs()` 统一两者，保证旧单角对象行为不变。

**`validate()` 校验项**（V2.2.2 扩展）：

- 画布尺寸、DPI、mode、各边距、椭圆直径、四角圆角半径（≥0 且 ≤ min(W,H)/2）均为正/合法
- `rect_lshape`：`l_corner` 合法、`l_cut_w_cm` / `l_cut_h_cm` 为正、`l_cuts_cm` ≤ 4 项且角位不重复、宽高为正
- **逐边约束（V2.2.2 新增）**：上边 `tl.cut_w_cm + tr.cut_w_cm`、下边 `bl + br`、左边 `tl.cut_h_cm + bl.cut_h_cm`、右边 `tr + br`，各自须 `< 对应内框边长 − 0.5cm` 余量，否则抛出明确到边的 `ValueError`

**Mask 生成原语**：`make_mask` / `fill_rect_mask` / `fill_ellipse_mask` / `fill_lshape_mask` / `apply_rounded_corners_to_mask` / `build_lshape_mask(size, outer_rect, corner_key, cut_w, cut_h, radii, fill_value, cuts=None)` / `compute_lshape_border_bands`。

### 11. 统一配置与版本号（core/config.py）

业务核心常量（DPI、切割损耗、像素上限、Tesseract 搜索路径模板、PathResolver）集中在 `core/config.py` 单一来源；部分算法阈值（边框检测/草图识别/GAP 补偿）仍分散于 `core/corner/detection.py`、`services/sketch_parser/` 各模块，渐进收敛中。

**版本号单一事实来源**（V2.2.3 新增）：

```python
APP_VERSION: str = "2.2.3"
APP_DISPLAY_NAME: str = f"智能裁剪设计器V{APP_VERSION}"
```

三个消费方：

| 消费方 | 引用方式 |
|---|---|
| 打包脚本 `packaging/packageV2.2.3.py` | `importlib.util.spec_from_file_location` **按文件路径加载** `core/config.py` |
| 启动日志头 `core/log_setup.py` | 函数内**局部导入** `from .config import APP_VERSION` |
| 「关于」框 `main.py` | `from core.config import px_to_cm, APP_VERSION` |

> ⚠️ **不可直接 `import core.config`**：`core/__init__.py` 会聚合导入 `image_ops` / `psd_tools` / `PyQt5`，
> 在无 GUI 依赖的打包机上会直接 `ImportError`。故打包脚本按文件路径加载、日志头用局部导入规避导入期副作用。
> 回归守护：`tests/core/test_app_version_single_source.py`（「定义点唯一」+「打包脚本内无版本字面量」）。

`PathResolver` 提供跨平台 Tesseract 自动定位，含 `_tesseract_cache` 单次探测缓存与语言包（`chi_sim` + `eng`）校验，**不硬编码任何盘符**（F12 修复）。V2.2.3 起语言包探测由 `os.popen` 改为 `subprocess.run(list)`，不再起 shell。

### 12. 历史记录功能（core/app_settings.py）

- 3 天保留策略（含今天）+ 每日 50 条上限 + 同日同名去重置顶
- QSettings/JSON 双通道存储
- 三源物理隔离：圆角裁剪工具 `TARGET_SRC_CROPPER` / 水池设计器 `TARGET_SRC_POOL` / L 形挖角设计 `TARGET_SRC_LSHAPE`

### 13. 日志系统（core/log_setup.py）

- 控制台 + 滚动文件双输出，默认 INFO（调试设 `LOG_LEVEL=DEBUG`）
- 幂等保护；日志路径 `logs/smartshapecrop.log`（5MB 滚动，保留 3 个旧文件）
- 崩溃日志：exe 同目录 `crash.log`（全局 excepthook 写 traceback）
- 启动时 `artifact_cleanup` 自动清理 logs/ 与 debug_output/ 中的过期调试产物（F19，失败不阻断启动）

### 14. 数据模型层（models/design_model.py）

- `DesignModel`：`CropDesign` 的薄包装器
- `apply_ui_snapshot(snap)`：UI 层只提供纯值 dict，模式判断/素材同步/多洞几何重建等业务规则全部集中在模型层（[H-10]）
- 提供 `sync_from_design()`、`to_design()`（深拷贝快照，防跨线程竞态）、属性读写统一接口
- 模型层不含 UI 引用和业务逻辑，纯数据结构

### 15. 线程调度层（workers/）

所有 QThread Worker 集中管理，与 GUI 层解耦：

| Worker | 所在文件 | 职责 |
|---|---|---|
| `PreviewRenderWorker` | `workers/canvas_workers.py` | 异步渲染预览图 |
| `ExportSaveWorker` | `workers/canvas_workers.py` | 异步保存导出 JPG |
| `CropWorker` | `workers/cropper_workers.py` | 异步裁剪处理 |
| `AutoMatchWorker` | `workers/cropper_workers.py` | 异步扫描模板库匹配源图 |
| `PoolRenderWorker` | `workers/property_panel_workers.py` | 水池设计器异步渲染 |
| `_SketchParseWorker` | `workers/property_panel_workers.py` | 草图解析后台任务 |
| `_WarmupScanWorker` | `workers/property_panel_workers.py` | 模板库预热后台任务 |
| `_LShapeParseWorker` | `workers/property_panel_workers.py` | L 形草图解析后台任务 |

Worker 只做：启动 → check_cancel → 转发结果，不导入任何 `gui/` 模块。

---

## GUI 使用流程

### 圆角裁剪工具

1. **上传源图**：点击"选择源图"按钮，支持 JPG/PNG/PSD
2. **自动匹配**（可选）：输入目标文件名，自动从模板库匹配源图
3. **参数识别**：自动从文件名解析尺寸和圆角参数，也可手动调整
4. **预览**：点击"预览"查看裁剪效果
5. **导出**：点击"导出 JPG"保存印刷级图片（后台线程渲染，界面保持响应）
6. **历史记录**：可查看目标文件名 3 天历史记录

### 水池设计器（参数化模式）

1. 选择形状模式：**矩形挖洞** / **椭圆挖洞**（下拉框，切换时自动显示/隐藏对应的参数组）
2. 设置画布尺寸、DPI、外边距（外边距仅椭圆模式从 SpinBox 读取）
3. 椭圆模式：填写长径/短径（直径，厘米），0 = 自动按四边距推算
4. 配置多层边框（颜色/厚度/素材填充）
5. 设置「挖空方式」：**空白(挖去不留白)** 或 **素材填充（花型匹配填充）**
6. 可选：设置边框文字、背景素材
7. 点击"生成预览"渲染 → 菜单 → 文件 → 导出 JPG

### 水池设计器（草图识别模式）

1. 点击「上传草图」选择手绘草图 PNG/JPG
2. 画布立即显示草图 → QThread 后台异步解析 → 7 步流程进度反馈
3. 解析完成：红色矩形框显示识别数据（外框/内挖/上下左右边距）
4. 识别数据自动回填至「内挖边距」面板 → 可手动微调（多洞时逐洞独立边距）
5. 点击「生成预览」渲染 → 菜单 → 文件 → 导出 JPG

### L 形挖角设计（独立面板）

1. 切换到「L形挖角设计」标签页
2. 填写目标文件名（自动解析出外框尺寸）+ 上传尺寸草图
3. 点击「✂️ 识别L形挖角」：后台多尺度 OCR 解析 → **G1 闸口检查** → 成功直接应用并回填参数
   - 多角草图会被识别为多个角位建议值，逐角回填到面板各行
   - OCR 不可用时降级为几何推断
   - G1 拦截时状态栏给出「检测到 N 个凹角，当前仅应用 M 个」告警，其余角位可手动补充
4. 逐角核对/修正挖角参数：挖角方向（tl/tr/bl/br）、挖角宽/高、外框宽/高、边角圆角半径
   - 面板实时显示**四边余量**，输入超出外框时红字提示
5. 点击「一键生成」渲染 L 形挖角设计图（自带边框的池素材会自动补全 L 形新边缘的边框层次，多角时逐角补全）
6. 菜单 → 文件 → 导出 JPG

### 内置模板

| 模板 | 说明 |
|---|---|
| 图 矩形嵌套挖洞 | 3 层边框 + 米色背景 + 边框文字 |
| 图 L形挖角 | L 形挖角 + 浅米色 + 简单边框 |
| 图 椭圆嵌套 | 椭圆挖孔 + 3 层边框 + 白色画布 |

---

## 技术要点

### 图像完整性保障

- 缩放统一使用 LANCZOS 重采样（预览用 BILINEAR）
- 圆角处使用 validity_mask 保护透明区域不被重新着色
- 边框颜色采样跳过 2px 抗锯齿过渡带，使用 5% 修剪均值
- 多层圆角颜色映射使用层索引而非深度值
- 极坐标→离散像素映射留 2px 容差，避免 C 形缺口
- 内孔边框用圆角矩形差集替代 EDT（数学等价，33×加速）保证精确 10px 黑色边线
- L 形挖角边框补全使用原始素材图检测 + scale 换算，避免拉伸畸变

### 安全机制

- `Image.MAX_IMAGE_PIXELS = 200_000_000`（2 亿像素上限，防御解压缩炸弹）
- 圆角半径限制 `min(radius, min(w, h) // 2)`，防止中心区域被着色
- 边框厚度硬上限（单层 ≤ 2cm、总厚 ≤ 3cm）+ 层数硬限制（≤4 层）
- 深色外层保护（max RGB ≤ 150 永不判为间隙）
- OCR 数值范围严格校验（0.3-500cm）+ 全局唯一性检查（差值<0.15 跳过）
- **多角 L 形逐边约束**（V2.2.2）：同边挖角求和超限直接拒绝，并在 GUI 给出到边的可读提示
- **G1 结构一致性闸口**（V2.2.2）：识别凹角数与消费数不一致即拦截告警
- 渲染路径极端参数防御机制，防止 GUI 线程挂起
- L 形挖角边框补全静默失败兜底，不影响后续渲染

### 一致性保证

- 圆角处理统一委托 `core.corner.algorithm.carve_corner_on_mask`，在 `geometry.py` / `image_cropper.py` / `process_image.py` 三入口完全一致
- 间隙层判定统一为 `classify_gap_layers` 单一来源
- L 形挖角边框补全通过 `apply_lshape_border_completion` 单一入口，三级路由向后兼容
- **多角与单角统一走 `LShape.cut_specs()`**：渲染、边框补全、边框带计算三处同源，不各自维护分支
- 旧导入路径通过 `core/compat` 的 `sys.modules` 别名指向同一模块对象（同源保证）

---

## 开发指南

### 调试

```bash
# 启用 DEBUG 日志
set LOG_LEVEL=DEBUG
python main.py

# 查看日志
# logs/smartshapecrop.log
# logs/*.png （草图识别 OCR 诊断截图）
```

`scripts/diagnose/` 包含案例诊断脚本（复现 Bug、生成诊断图），`scripts/verify/` 包含修复验证脚本。脚本约定见 `scripts/README.md`（不进 CI、命名约定、30 天归档窗口）。

### 测试

测试位于 `tests/` 目录，按模块分子目录组织，使用 pytest 框架。

**实测基线（2026-09-24，`.venv` 实跑）**：

```
818 passed / 0 failed / 0 error / 0 skipped，耗时 217.06s
```

各层用例分布（按 pytest 收集计数）：

| 目录 | 用例数 | 说明 |
|---|---|---|
| `tests/core/` | 436 | 圆角 / 裁剪 / 文件名解析 / 模板匹配 / L 形渲染与边框 / 草图解析 / G1 / 多角 / 阶梯 / 版本单一来源 / 产物清理链接剪枝 / CropDesign 校验 |
| `tests/gui/` | 119 | 离屏 GUI（冒烟 / 主窗口 / 信号契约 / 三面板 / 校验文案 / 模式回填 / 草图解码 Worker 退役） |
| `tests/integration/` | 101 | F1-F19 修复验证 / 配置 / 水池-L 形数据流 / LOD 一致性 |
| `tests/sketch/` | 64 | 多洞 / 特征化 / 输入校验 / 逻辑函数 |
| `tests/sketch_parser/` | 57 | 阶梯识别 / 多洞边界 |
| `tests/models/` | 27 | 数据模型 |
| `tests/border/` | 10 | 边框修复 / 复杂花纹安全 / 用户案例 |
| 根目录 | 4 | `test_phase0_multihole.py` |

```bash
# 全部测试
python -m pytest tests/ -q

# 特定测试类
python -m pytest tests/core/test_rounded_corner.py::TestApplyRoundedCorners -v

# 草图相关测试
python -m pytest tests/sketch/test_sketch_parser_logic.py -v

# L 形挖角测试（渲染 + 草图解析 + 边框补全 + Profile 路由 + G1 + 多角）
python -m pytest tests/core/test_lshape_render.py tests/core/test_lshape_sketch_parser.py tests/core/test_lshape_border.py tests/core/test_lshape_border_route.py tests/core/test_g1_invariant.py tests/core/test_multi_corner_detection.py tests/core/test_multi_corner_ocr.py -v

# 集成测试
python -m pytest tests/integration/ -v
```

> 维护约定：测试必须使用 `assert` 而非 `return <bool>`（PytestReturnNotNoneWarning 已升为 ERROR）；GUI 层推荐使用 `QT_QPA_PLATFORM=offscreen` 模拟。

### 添加新案例

1. 将源图放入指定素材目录（GUI 选择，或 `process_image.py --src` 指定）
2. 按命名规则命名目标文件（见下方文件名解析示例）
3. 运行过程序后，若遇问题参考 `scripts/diagnose/` 编写调试代码
4. 针对反复出现的问题，补充 `tests/` 下的回归测试用例

### 新增几何形状的方法论（重要）

**第一步是写只读 POC 验证「模型能不能表达」，不是设计 UI。** 三步验证（`.venv` 实跑）：

1. 构造顶点序列，**数凹角个数** —— 个数相同不代表语义相同。
2. 凹角代入 `_finalize_lshape_geometry` 公式，看是否推得退化值（如 `cut_w=0` 会被 `build_lshape_mask` 的 `w>0.5` 过滤 → **静默识别为纯矩形且报 success**）。
3. 用现有字段拼目标形状，看是被拦截还是能过但几何错 —— 拦截点直接说明模型缺什么。

**⚠️ 有真实样例就先跑一遍再下结论。** 实例：纯代码推断「识别层重灾区，13–20 天」，真实草图重跑后推翻 —— 识别层完好，缺口只在「凹角列表→形状」的表述，工期降至 8–13 天。**代码推断易把「下游表述不足」误判成「上游识别坏了」，工期差 2–3 倍。**

**⚠️ G1 闸口在阶梯场景形同虚设**：判据 `notches_detected != notches_consumed` 在严格阶梯 `2==2` 时天然通过 —— 检测对了，错在「组装形状」。结果 `success=True` 且 `g1_blocked=False`，但 IoU 仅 0.9791。**同类「下游组装」错误需另加「形状自洽」校验**：反拼轮廓与识别凹角比对，不符则降级。

### 同面板多形状：用分组推导，不加「形状类型」

判据：**按锚定角（anchor）分组** —— 每组各 1 个 → 多边 L 形；某组 ≥2 个 → 该角是阶梯；两者共存 → 混合。

1. **不让用户选形状类型**（形状是参数的几何后果）。
2. **形状是推导结果不是存储状态** —— **绝不新增 `shape_type` 字段**（存了就要同步，是「静默失效」bug 主因之一）。
3. **一个分组函数服务多件事**（形状区分 + 约束分层 + 渲染遍历收敛一处）。

**⚠️ 约束必须分层**：`core/geometry.py` 的「同边 cut 求和 < 边长」对多边 L 形合理，对**阶梯语义错误**。须 `len(group)==1` 走旧规则、`>=2` 走阶梯规则；跨角求和时每角只取最外层。

### 改 `CropDesign.mode` 判断时的注意事项

全工程 **27 处** `CropDesign.mode` 判断（24 处 `==` / **3 处 `!=`**），分布：

| 文件 | 处数 | 行号 |
|---|---|---|
| `core/image_ops.py` | 14 | 752 / 778 / 798 / 923 / 1069 / 1087 / 1110 / 1163 / 1223 / 1242 / 1364 / 1486 / 1515 / 1521 |
| `gui/property_panel_generate.py` | 7 | 226 / 238 / **242** / 292 / **369** / 523 / 566 |
| `core/geometry.py` | 3 | 277 / 581 / 584 |
| `models/design_model.py` | 3 | 110 / **114** / 166 |

**加粗即 3 处 `!=`** —— `gui/property_panel_generate.py:242`、`:369`、`models/design_model.py:114`。**只搜 `==` 必漏**，且禁止字符串级批量替换，须按四类分治：

1. **渲染语义**（`image_ops.py` 14 处）→ 走 mode 白名单判断
2. **分派点**（`core/image_ops.py:1515/1521`、`core/geometry.py:584`）→ **必须新增独立分支**；误改成 membership 会得「并集」而非「差集」（洞被填满）**且不报错**
3. **展示/同步**（`property_panel_generate.py` 7 处 / `design_model.py` 3 处）
4. **参数守卫**（`geometry.py:277` 在 `validate()` 内）

> 计数口径：仅统计 `design.mode` / `d.mode` / `self.design.mode` 等 **CropDesign 语义**的判断。工程内另有 9 处 PIL/PSD 图像域 `.mode`（`img.mode != 'RGB'` 等，见 `image_ops.py:39`、`services/psd/loader.py`、`core/lshape_border*.py`、`gui/cropper_panel.py:38`）与 3 处测试断言，不属于此列。

**两处隐藏耦合（新开面板 / 新增 mode 必查）**：

- `gui/property_panel.py:926` 硬编码索引 `{'rect_hole': 0, 'rect_lshape': 1, 'ellipse_hole': 2}` → 不同步则模板加载**静默回落 `rect_hole`**。应改 `_cb_mode.findData(mode)`
- `core/app_settings.py:317` `if src not in (CROPPER, POOL, LSHAPE): src = CROPPER` → 新增第 4 个历史源**必须同步白名单**

**「集成进现有面板、不新增 mode/历史源」可同时绕过这两坑 —— 集成的隐性收益。**

---

## 命名规范

### 文件名格式

```
{产品名};{方向}{短边}x{长边}cm{圆角描述}.jpg
```

- 方向：竖版加前缀，横版不加
- 尺寸：短边在前，长边在后
- 圆角：无圆角时省略

**示例**：

```
双面格-定制-定制尺寸-简织;竖版41x55cm右下角圆角半径2cm.jpg
双面格-定制-定制尺寸-塞纳时光;78.5x128.5cm4个圆角半径4cm.jpg
双面格-定制-定制尺寸-花漾之约;38.5x186cm左下角和右下角圆角半径5cm.jpg
```

---

## 性能优化记录

### 6 大优化热点（2026-08-21）

| 优先级 | 优化项 | 效果 |
|---|---|---|
| P0 | OCR 合并（统一全图扫描） | Tesseract 调用从 51 次减少到约 10 次 |
| P0 | EDT→cv2.erode 替换（21×21 椭圆核） | 80-90% 性能提升，回退机制到 EDT |
| P1 | 外框枚举剪枝（去重 + 方向预过滤 + 早停） | 组合从 64 次减少到 ≤15 次 |
| P1 | 圆角 ROI 化（隔离 4 角区域） | 内存/计算从 O(W×H) 降至 O(r²) |
| P2 | 矩形检测早停（串行二值掩码生成） | 节省 50% 以上 Step1 时间 |
| P2 | 边框带 mask 增量复用（传递 prev_inner_mask） | 节省 40-50% mask 构建 |

### rect_hole 专项优化

| 优化项 | 效果 |
|---|---|
| EDT 距离变换→圆角矩形差集 | 数学等价，33×加速 |
| compute_border_bands 单 mask 两步 PIL 绘制 | 7×加速/层 |
| 预览用 BILINEAR | 减少 0.4-1.5 秒渲染时间 |
| 复用 inner_mask | 省去一次 mask 计算和 corner radii 计算 |

### V2.2 新增优化（2026-09）

| 优化项 | 效果 |
|---|---|
| 连通分量向量化（np.isin 替代 Python 循环） | 395 连通分量从 12.8s 降至毫秒级 |
| LOD 降采样调整（scale=0.5 + BILINEAR） | 消除高细节素材马赛克伪影 |
| JPG 导出异步化（QThread + 可取消） | 消除大图导出 UI 冻结 |
| 模板库 dir_mtime 磁盘缓存 + 信号槽预热 | 未变化时快速跳过（2ms），主线程不阻塞 |
| L 形挖角边框补全三级路由 | Profile/V13/旧路径自动回退，向后兼容 |
| Profile 路径锚点对齐 + 三层封顶 | 抗出血白边，V13/旧路径失效素材可补全 |
| 五层分离架构重构 | Worker 与 GUI 解耦，职责单一，可维护性提升 |

### V2.2.2 新增优化

| 优化项 | 效果 |
|---|---|
| 多角并集 mask 单次生成 | 以 `cut_specs()` 统一遍历，N 角无额外遍历开销 |
| OCR 逐角归属替代像素比例反推 | 消除数值漂移（45.8 → 50.5），不依赖字母 OCR |
| G1 闸口不变量常驻 | 结构错误提前暴露，不再静默产出错形状 |

---

## 圆角裁剪案例验证记录

### 8.20 花幔图案修复

- "花幔"34.3×207.9CM 左下角 3.3cm 圆角多余缺口问题
- 放宽直线延伸区域颜色匹配阈值 + 间隙判定中保留直线延伸区域像素

### 8.21 六案例修复

| 案例 | 问题 | 修复方式 |
|---|---|---|
| 素锦 | 米黄色间隙层残留 | 间隙检测逻辑改进 |
| 塞纳时光 | 米黄色间隙层残留 | 同上 |
| 中古花园 | 有宽度的弧形缺口 | 深色外层保护 |
| 墨上花开 | 边框过粗 + 白色弧形缺口 | 圆弧外白底清除 + 深色外层保护 |
| 花漾之约 | 白色弧形伪影 | beyond_arc 全清 + content_protect 保护 |
| 蔓生花 | 边框厚度不一致 | 边框厚度统一 |
| 安妮森林 | 白色边框线 + 米色弧形缺口 | 深色外层保护（max RGB ≤ 150） |

### 9.07 圆角裁剪 T1-T5 修复

| 修复 | 问题 | 修复方式 |
|---|---|---|
| T1 | 圆角处多层边框都被圆角化 | `only_outermost=True` + `protect_content` 常驻 |
| T2 | 圆角白线与内层误圆角 | `angle_in_corner_sector` 统一 + 仅最外层构建 mask |
| T3 | 圆角边界白线残留 | `tol` 一致性 1.0 → 2.0 |
| T4 | 蔓生花/素锦深色弧线 + 南瓜无忧白隙 | `ring_region` 收窄 + `inner_cut` 限幅 |
| T5 | 深色弧线未消除根因 | `ring_region` 限于 `border_zone`（直边区） |

### 9.16 黑大理石多边框修复（V2.2.2）

| 问题 | 修复落点 |
|---|---|
| 深色高纹理（黑大理石）素材的多边框层误判 | `core/image_cropper_border.py` + `core/corner/detection.py` 层结构判定 |

---

## ProductSummary 文档索引

工作与文档沉淀按模块与日期分类整理（全部被 Git 跟踪，共 210 个文件）：

| 目录 | 内容 | 组织方式 |
|---|---|---|
| `ProductSummary/月度总结/` | 跨模块按日期聚合总览（15 份） | 平铺 `YYYYMMDD-任务分类整理总结.md` |
| `ProductSummary/圆角裁剪工具/` | 圆角裁剪工具专项（27 份） | 平铺 `YYYYMMDD-主题.md` |
| `ProductSummary/水池设计器/` | 水池设计器专项（20 份） | `YYYYMMDD/` 子目录 + README |
| `ProductSummary/L形挖角设计器/` | L 形挖角演进（22 份） | 阶段 1-6 + 索引 + 核心文件对照表 |
| `ProductSummary/SmartShapeCrop分析报告/` | 项目分析报告（25 份 html/md） | 含 `assets/` 配图 + `patches/` 补丁 |
| `ProductSummary/项目审查报告/` | 审查类文档主线（8 份） | 平铺 `YYYYMMDD` 命名 |
| `ProductSummary/2026-08/`、`2026-09/` | 早期按日期归档（7 + 4 份） | 与"月度总结/"内容重复，见"已知问题" |

**重点文档**：

- `ProductSummary/项目审查报告/SmartShapeCrop-项目全面审查报告-20260924.md` — **V2.2.3 当前权威现状快照**（24 项问题清单 + P0/P1 批次整改记录 + 附录 A–G）
- `ProductSummary/SmartShapeCrop分析报告/patches/patch-06-P1批次增强-代码卫生与版本单一来源.patch` — P1 批次改动留档（可往返验证）
- `ProductSummary/L形挖角设计器/20260915-多角L形三期完整化与数据管线修复.md` — V2.2.2 多角 L 形交付记录
- `ProductSummary/L形挖角设计器/20260914-多角L形挖角（收敛点解除+G1闸口+切边补边越界修复）.md` — G1 闸口与收敛点解除
- `ProductSummary/L形挖角设计器/20260916-单边阶梯L形挖角可行性分析与实现建议.md` — 阶梯 L 形可行性（含真实草图实测）
- `ProductSummary/L形挖角设计器/20260916-单边阶梯与多边L形在同一面板的区分设计.md` — 按锚定角分组的形状推导方案
- `ProductSummary/SmartShapeCrop分析报告/SmartShapeCrop-V2.2-全面审查与建议报告-20260912.html` — 架构重构后全面审查

---

## 文档维护

> **真相来源**：本 README 是项目结构、架构、模块说明的**唯一权威参考**。其他文档（ProductSummary 下工作总结、分析报告等）为历史记录，不作为当前架构的真相来源。如有冲突，以本文件为准。

### 新鲜度信号

| 项目 | 值 |
|---|---|
| 文档版本 | V2.2.3 |
| 最后验证 | 2026-09-24 |
| 验证方式 | 全量测试实跑（`.venv`，**818 passed**）+ 目录结构遍历 + 源码关键符号核对 + **`dist` 产物出包与时间戳比对**（exe 级启动冒烟 + 打包清单核验）+ **缺陷修复的判别力自检**（回退到修复前重跑，确认用例会红） |
| 生命周期阶段 | 维护期（V2.2.3 阶梯 L 形与 P0/P1 批次整改已落地；**V2.2.3 exe 已于 2026-09-24 出包**；`_SketchDecodeWorker` 悬垂引用已修复） |

### 更新触发器

以下变更发生时，**必须**同步更新本 README：

| 触发事件 | 需更新章节 |
|---|---|
| 新增/删除/迁移模块或目录 | 目录结构、架构概览、核心模块说明 |
| 测试数量或基线变化 | 快速开始（运行测试）、开发指南（测试）、新鲜度信号 |
| 依赖包变更 | 快速开始（依赖清单） |
| 新增功能或特性 | 核心特性、核心模块说明、版本演进 |
| 打包入口 / spec 变更 | 快速开始（打包发布）、目录结构、版本演进 |
| 架构层调整 | 架构概览、目录结构 |
| 版本号变更 | 标题、新鲜度信号、版本演进 |

### 旧路径迁移指引

重构后旧导入路径仍可使用（通过 `core/compat` 的 `sys.modules` 别名），但新代码应优先使用新路径：

| 旧路径 | 新路径（推荐） |
|---|---|
| `core.rounded_corner` | `core.corner.algorithm` |
| `core.parser.name_parser` | `services.parser.name_parser` |
| `core.parser.template_matcher` | `services.parser.template_matcher` |
| `core.pool_designer.*` | `services.sketch_parser.*` |
| `core.psd.loader` | `services.psd.loader` |
| `gui.property_panel_workers` | `workers.property_panel_workers` |

---

## 已知问题与后续规划

> 状态核对：**2026-09-24**。完整清单（含严重度、位置与实测证据）见 `ProductSummary/项目审查报告/SmartShapeCrop-项目全面审查报告-20260924.md`。

1. ✅ **已修复：原「1 个测试用例失败」** —— `tests/integration/test_f1_inner_rect_crash.py::test_render_design_lshape_degenerate_no_crash`，由 `core/geometry.py:349-352` 新增的退化守卫解决；2026-09-24 全量实跑 **818 passed / 0 failed / 0 error**。
2. **✅ V2.2.3 已出包（2026-09-24）**：`dist/智能裁剪设计器V2.2.3.exe`（218.4 MB，内嵌 Tesseract），exe mtime 10:34:11 ≥ 最新源码 10:15:32，**时效铁律通过**。已过 exe 级启动冒烟（离屏启动存活 22–25 s、无崩溃日志）与打包清单核验（PYZ 项目模块 **49/49**、PKG 含 **161** 个 Tesseract 条目）。
    **遗留建议**：GUI 端到端「阶梯 L 形**预览 = 导出**」（P0-2 的修复面）建议人工双击 exe 复验一次 —— 源码级几何一致性已由 `tests/integration/test_lod_geometry_consistency.py`（24 条）覆盖。
3. **worker 生命周期回归（部分已覆盖，2026-09-24）**：「线程可被停止」已有覆盖；**`_SketchDecodeWorker` 的退役协议**已由 `tests/gui/test_poolbox_worker_retire.py`（5 条，含 2 条判别力用例）覆盖「C++ 对象已销毁、Python 引用仍在」的悬垂引用场景。仍待补：`CropWorker` / `PoolRenderWorker` / `_WarmupScanWorker` 的「取消后不回写 UI」回归，以及**同模式退役写法全量排查**（见第 15 条）。
4. ✅ **单边阶梯 L 形挖角已实施**（V2.2.3 六期）：`CutRect` + `CropDesign.l_cut_rects` + `_validate_l_cut_rects` + 统一掩膜 `_build_design_lshape_mask`（`_draw_staircase_union_layers`）；与多边 L 形共用同一面板，未新增 `shape_type` 字段。`scripts/diagnose/_diag_stair_*.py` 保留为历史 POC 参考。
5. ✅ **`scripts/README.md` 已按实测重写**（2026-09-24）：更正了「`scripts/_archive/` 与 `scripts/verify/_archive/` 仍存在」等失真描述 —— 归档入口统一在 `scripts/diagnose/_archive/`（73 py）。
6. ✅ **`gui/property_panel.py` 硬编码 mode 索引已修复**：改用 `_cb_mode.findData(mode)`（未知 mode 回落索引 0），由 `tests/gui/test_property_panel_write_paths.py` 守护逐例等价。
7. **`core/app_settings.py` 历史源白名单**：`if src not in (CROPPER, POOL, LSHAPE): src = CROPPER`，新增第 4 个历史源必须同步该白名单。
8. **ProductSummary 目录重复**：`2026-08/`、`2026-09/` 与 `月度总结/` 内容重叠（均被 Git 跟踪，共 210 个文件），历史上曾清理后因 restore 提交复活。**清理必须配套 `git rm --cached` + 提交**，否则会再次复活。
9. **根目录残留文件**：`verify_fix_color.jpg` 已被 Git 跟踪但属验证产物；`crash.log` 为本地生成（已被 `.gitignore` 覆盖）；`debug.log` 被输入法进程占用、暂时删不掉。`项目全面审查报告.md` 与 `综合形状功能可行性分析报告.md` 已于 2026-09-24 移入 `ProductSummary/项目审查报告/`。
10. **环境风险**：切勿安装 `python-qt5`（与 PyQt5 同名冲突，破坏 DLL 加载）；`.venv` 曾因磁盘迁移与黑包安装反复损坏，重建后须复跑全量测试再出包。
11. **兼容 shim 清理**：当所有调用方迁移到新路径后，可删除 `core/compat/`、`core/parser/`、`core/pool_designer/`、`core/psd/` shim 及 `gui/property_panel_workers.py` shim，无需改动业务代码。
    ⚠️ 在 shim 仍存在期间，打包的 hidden-import **只能声明 `services.psd.loader`**，不可声明 `core.psd.loader`（该文件不存在，会报 `Hidden import not found`）。
12. **历史脚本归档**：`packaging/legacy/` 已于 **2026-09-17 整目录删除**（原含 `package*.py` 6 个 + `build_exe.bat`）。当前唯一入口为 `packaging/packageV2.2.3.py`（`packageV2.2.2.py` 保留备查，其 exe 名硬编码为 V2.2.2，**勿再用于出包**）。
13. **⚠️ Git 操作警示**：`git gc` / `git repack` 在本机曾导致 `.git` 被清空、历史全失，此类操作前请先 `cp -r .git .git.bak`。
14. **✅ 已修复：`core/artifact_cleanup.py` 越界删除（2026-09-24）** —— 原用 `Path.rglob('*')` 收集候选：Python 3.13 的 `**` 只对**符号链接**停止递归，而 **Windows junction（目录联接）不是符号链接**（`os.path.islink()` 对它返回 `False`），故会进入其目标目录、联出目录外的**真实文件**并被 `os.remove` 删除（**已复现**）。符号链接（symlink）无此问题：目录链接不被进入、文件链接只删链接自身。
    修复：新增 `_is_link_node()`（`os.path.isjunction()`；Python < 3.12 退回按 `st_reparse_tag` 判定）+ `_iter_tree()` 剪枝遍历（产出与 `Path.rglob('*')` **逐条一致**，保持下游稳定排序的 tie-break 不变）；链接节点**既不递归、也不纳入** `candidates` / `empties`。配套 8 条回归用例见 `tests/core/test_artifact_cleanup_links.py`。
15. **✅ 已修复：`_SketchDecodeWorker` 悬垂引用（2026-09-24）** —— `self._sketch_decode_worker` 只在两处置 `None`，而 Worker 经 `finished.connect(deleteLater)` 在解码线程退出后即销毁底层 C/C++ 对象，Python 包装器却被保留到下一次退役；于是「传草图 A（解码完成）→ 传草图 B / 点清除草图」时 `old.isRunning()` 抛 `RuntimeError: wrapped C/C++ object of type _SketchDecodeWorker has been deleted`。**两处调用点**：`_start_sketch_decode_worker()` L628（**加载草图必经路径**）与 `_pool_clear_sketch()` L1033。**真实复现 ×2**（`crash.log` 2026-09-24 10:34:27 / 10:40:42，均为当时运行的**源码实例**的用户操作，非打包产物）。**非致命**，但用户可感知为「点一下没反应、需再点一次」（间隔越久越易触发）。
    修复：两处 `isRunning()` / `deleteLater()` 加 `try/except RuntimeError` 守卫（`gui/property_panel_poolbox.py` **+26 −4，纯新增，正常路径逐字未动**），把「包装器已失效」按已停止退役处理；配套 5 条回归用例见 `tests/gui/test_poolbox_worker_retire.py`，并做判别力自检（回退修复前 → 3 failed，失败栈命中 `:628`/`:1033`，**与真实崩溃行号一致**）。
    ⚠️ **同模式退役写法在工程内至少 13 处**，本次只修已确认复现的这 2 处（详见第 3 条）。

---

## 许可证

本项目为内部工具，未公开许可。

---

<sub>SmartShapeCrop README · 文档版本 V2.2.3 · 最后验证 2026-09-24</sub>
