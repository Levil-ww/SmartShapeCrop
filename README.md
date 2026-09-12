# SmartShapeCrop — 智能形状裁剪设计器 V2.2

> 面向印刷行业定制尺寸成品图的桌面设计工具：等比缩放 + 圆角裁剪 + 多层边框处理 + 水池设计器草图 OCR 智能识别 + 多洞嵌套 + L 形挖角独立设计 + L 形挖角素材边框自动补全。

**最后验证**：2026-09-12（架构重构后全面复验）  
**更新触发**：目录结构变更、模块迁移、依赖变更、测试基线变更时须同步更新本文件

## 项目简介

SmartShapeCrop 是一款面向印刷/定制设计行业的 Windows 桌面工具（PyQt5），采用**参数化设计 + 图像智能识别**双模式，核心解决三大需求：

1. **圆角裁剪工具**：将已有成品图（JPG/PSD）按目标尺寸等比缩放，并自动/手动对四角施加圆角裁剪。支持从文件名自动解析尺寸与圆角参数、模板库匹配源图、多层边框自动检测与圆角重绘。
2. **水池设计器**：参数化生成矩形嵌套、椭圆挖孔等设计稿，支持**手绘草图上传自动识别尺寸**（7 步串行流程）、多层边框、素材填充、边框文字环绕，导出印刷级 JPG。支持**多洞嵌套挖洞**与逐洞独立边距。
3. **L 形挖角设计器**（独立面板）：承载 L 形挖角的参数设置、草图上传与生成。支持草图自动识别挖角方向（tl/tr/bl/br）、挖角尺寸、外框完整尺寸，含 OCR 降级路径；新增**素材边框自动补全**——对自带边框的池素材图，在 L 形挖角产生的新边缘上按素材原始边框层次重绘，使成品呈完整 L 形外框。

### 核心特性

- **厘米级精度**：所有尺寸以厘米为单位输入，按 DPI 自动换算像素
- **四角独立圆角**：每个角可独立设置圆角半径（0 = 直角），支持单角/双角/四角组合
- **多层边框自动检测**：颜色距离 + 亮度突变双算法识别嵌套边框层，圆角处自动重绘
- **深色外层边框保护**：最外层深色边框（max RGB ≤ 150）永不判为间隙，确保黑色边框线完整
- **仅最外层圆角化**：圆角处仅绘制最外层边框圆弧，内层花纹保持直角，避免多余弧线/过厚/色差
- **圆弧外白底清除保护**：圆角裁剪时清理圆弧外侧超出素材范围的白色底噪（`beyond_arc` 区域），保留素材原有花纹/文字内容
- **文件名智能解析**：从中文文件名提取产品名、尺寸、方向（横版/竖版）、圆角参数，支持全角/特殊字符容错
- **模板库匹配**：根据目标文件名自动匹配模板库中的最佳源图（形状+方向关键词严格匹配）
- **PSD 分层支持**：读取 PSD 图层，自动裁剪透明边距，合成扁平 JPG
- **印刷切割损耗补偿**：自动为目标尺寸加 1cm 扫描余量，圆角半径加 0.5cm 切割损耗
- **LANCZOS 高质量缩放**：默认 `simple_resize` 模式，不裁剪不留白，最小质量损失
- **草图智能识别**（水池设计器）：上传手绘草图 → 7 步串行流程（矩形检测→区域划分→多尺度 OCR→小数修复→方向标签锁定→空间映射→几何校验）→ 自动回填外框/内挖/上下左右边距 8 字段
- **多洞嵌套挖洞**：支持矩形嵌套多洞（包络盒消除 + 逐洞独立边距 + 逐洞 10px 黑色边框）
- **L 形挖角独立识别**：两矩形减法推断法 + OCR 兜底，自动检测挖角方向与尺寸；OCR 不可用时纯 CV 几何降级
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

**向后兼容**：`core/compat/` 通过 `sys.modules` 别名注册旧导入路径（如 `core.parser` → `services.parser`），旧代码无需改动即可运行。旧 `core/parser/`、`core/pool_designer/`、`core/psd/` 已降级为兼容 shim，实际实现迁移至 `services/`。`gui/property_panel_workers.py` 同样降级为 shim，实际实现迁移至 `workers/property_panel_workers.py`。

---

## 目录结构

```
SmartShapeCrop/
├── main.py                         # 应用入口（PyQt5 主窗口 + 3 标签页 + 模板预设 + 全局异常 crash.log）
├── process_image.py                # 命令行批处理脚本（等比缩放 + 圆角，示例/批处理）
├── conftest.py                     # pytest 全局 fixture + 防御性收集忽略
├── requirements.txt                # Python 依赖（带版本约束）
├── pytest.ini                      # 测试配置（PytestReturnNotNoneWarning 升为 ERROR）
├── 智能裁剪设计器V2.2.spec          # PyInstaller spec（与 packaging/specs/ 同源副本）
├── crash.log                        # 全局 excepthook 崩溃日志
│
├── core/                           # 核心业务逻辑层
│   ├── __init__.py                 #   公共 API 总入口（聚合子包对外名称）
│   ├── config.py                   #   统一配置管理（阈值、单位换算、切割损耗、PathResolver 跨平台路径）
│   ├── geometry.py                 #   参数化形状定义 + Mask 生成 + compute_inner_corner_radii 模式区分
│   ├── image_ops.py                #   图像操作（加载/缩放/平铺/边框合成/文字/导出 + L 形挖角边框补全集成）
│   ├── image_cropper.py            #   裁剪服务主编排层（协调裁剪/边框检测/圆角处理/背景色填充）
│   ├── image_cropper_border.py     #   裁剪边框逻辑（从 image_cropper 拆分）
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
│   ├── parser/                     #   [兼容 shim] → services.parser
│   ├── pool_designer/               #   [兼容 shim] → services.sketch_parser
│   └── psd/                        #   [兼容 shim] → services.psd
│
├── services/                       # 服务层（外部能力封装，不含 UI 引用）
│   ├── __init__.py                 #   包入口（parser / sketch_parser / psd 三个子包）
│   │
│   ├── parser/                     #   文件名解析 + 模板库匹配
│   │   ├── __init__.py             #     子包入口（导出 ParsedFilename / TemplateMatcher 等）
│   │   ├── name_parser.py          #     文件名解析（尺寸/方向/圆角/产品名，6 层容错）
│   │   └── template_matcher.py    #     模板库扫描与匹配引擎 v2（mtime 缓存 + 信号槽预热 + pickle 缓存 + 倒排索引）
│   │
│   ├── sketch_parser/              #   草图尺寸解析（单洞 / 多洞 / L 形）
│   │   ├── __init__.py             #     子包入口（导出单洞/L 形/多洞解析符号）
│   │   ├── sketch_parser.py        #     单洞草图解析主编排层（7 步串行流程 + 全局 OCR + 位置映射 + 稳定性投票）
│   │   ├── sketch_parser_base.py   #     草图解析基类与公共工具
│   │   ├── sketch_parser_cache.py #     OCR 结果缓存（避免重复调用 Tesseract）
│   │   ├── sketch_parser_margins.py#     边距识别与自洽校验
│   │   ├── sketch_parser_numbers.py#     数字 Token 合并与小数修复
│   │   ├── sketch_parser_vision.py#     矩形检测 / 区域划分 / 方向标签扫描
│   │   ├── sketch_parser_multihole.py#   多洞识别（Phase A-E 消包络盒 + OCR 加权众数投票）
│   │   └── lshape_sketch_parser.py#     L 形挖角草图识别（两矩形减法推断 + OCR 兜底 + 几何降级）
│   │
│   └── psd/                        #   PSD 分层文件处理
│       ├── __init__.py             #     子包入口（导出 PsdLayer / PsdLoadError / 加载与导出函数）
│       └── loader.py               #     PSD 读取/裁剪/合成
│
├── workers/                        # 线程调度层（所有 QThread Worker 集中管理）
│   ├── __init__.py                 #   包入口（声明三类 Worker 职责）
│   ├── canvas_workers.py           #   画布渲染 Worker（PreviewRenderWorker + ExportSaveWorker）
│   ├── cropper_workers.py          #   裁剪 Worker（CropWorker + AutoMatchWorker）
│   └── property_panel_workers.py   #   水池设计 Worker（PoolRenderWorker / _SketchParseWorker / _WarmupScanWorker / _LShapeParseWorker 等）
│
├── models/                         # 数据模型层（纯数据结构，不含 UI 和业务逻辑）
│   ├── __init__.py                 #   包入口
│   └── design_model.py             #   DesignModel：CropDesign 薄包装器（同步更新/克隆快照/属性读写）
│
├── gui/                            # PyQt5 界面层
│   ├── __init__.py                 #   包入口（导出 PreviewCanvas / PropertyPanel / CropperPanel / LShapePanel）
│   ├── canvas_widget.py            #   预览画布（渲染线程 + LOD 降采样 + ExportSaveWorker 后台导出）
│   ├── cropper_panel.py            #   圆角裁剪面板（上传/识别/预览/导出 + 历史记录 TARGET_SRC_CROPPER）
│   ├── lshape_panel.py             #   L 形挖角独立设计面板（草图上传 + 一键生成 + TARGET_SRC_LSHAPE）
│   ├── property_panel.py           #   水池设计器属性面板主入口（Facade，聚合子模块 + TARGET_SRC_POOL）
│   ├── property_panel_widgets.py   #   自定义控件（ColorButton / _SketchDropLabel 草图拖拽等）
│   ├── property_panel_workers.py   #   [兼容 shim] → workers.property_panel_workers
│   ├── property_panel_dialogs.py   #   对话框（L 形挖角确认 / 数据回填 / 图层编辑）
│   ├── property_panel_generate.py  #   生成预览逻辑（_GenerateMixin）
│   ├── property_panel_layers.py   #   多层边框编辑 UI（_LayersMixin）
│   └── property_panel_poolbox.py   #   多洞参数面板 + 草图识别与边距回填调度（_PoolBoxMixin）
│
├── tests/                          # 单元测试（pytest，以实跑结果为准；2026-09-12 重构后实测 380 passed / 7 skipped）
│   ├── conftest.py                 #   pytest 收集忽略列表（排除混入的非测试脚本）
│   ├── __init__.py
│   ├── core/                       #   核心模块测试（圆角/裁剪/文件名解析/模板匹配/L 形渲染/草图解析/边框补全/CropDesign 校验）
│   │   ├── test_rounded_corner.py
│   │   ├── test_lshape_render.py
│   │   ├── test_lshape_sketch_parser.py
│   │   ├── test_lshape_border.py
│   │   ├── test_lshape_border_route.py
│   │   ├── test_image_cropper.py
│   │   ├── test_image_ops_p2.py
│   │   ├── test_name_parser.py
│   │   ├── test_template_matcher.py
│   │   └── test_crop_design_validate.py
│   ├── integration/                #   集成测试（F1-F19 修复验证 / 水池-L 形流程 / 配置）
│   ├── sketch/                     #   草图识别测试（多洞 / 特征化 / 输入校验 / 逻辑函数）
│   ├── border/                     #   边框测试（边框修复 / 复杂花纹安全 / 用户案例）
│   └── gui/                        #   GUI 层测试（离屏运行，56 个用例）
│       ├── conftest.py             #     离屏 QApplication / 设置隔离 / 线程清理夹具
│       ├── test_gui_smoke.py       #     构造冒烟与线程卫生
│       ├── test_main_window.py     #     主窗口装配与信号-槽接线
│       ├── test_signals_contract.py#     面板信号契约与跨面板注入
│       ├── test_property_panel.py  #     水池设计器面板
│       ├── test_cropper_panel.py   #     圆角裁剪工具面板
│       └── test_lshape_panel.py    #     L 形挖角设计面板
│
│   注 1：混入 tests/ 的诊断脚本已于 2026-09-11 全部移至 scripts/diagnose/_diag_*.py。
│   注 2：GUI 测试以 QT_QPA_PLATFORM=offscreen 离屏运行，不弹真实窗口。
│
├── scripts/                        # 人工诊断/验证脚本（不进 CI）
│   ├── README.md                   #   脚本组织规范与命名约定
│   ├── diagnose/                   #   案例诊断脚本（_diag_*.py + _live/ 实时诊断 + _archive/ 归档）
│   ├── verify/                     #   修复验证脚本（语法自检 / 像素验证 / 端到端）
│   └── split_*.py                  #   模块拆分辅助脚本（sketch_parser / property_panel）
│
├── packaging/                      # PyInstaller 打包
│   ├── packageV2.2.py             #   【当前】V2.2 打包脚本（单文件默认，内嵌 Tesseract）
│   ├── legacy/                     #   历史打包脚本归档
│   ├── build_exe.bat              #   历史打包批处理
│   └── specs/                     #   .spec 归档（V2.1 / V2.1.2 / V2.2）
│
├── _archive/                       # 归档备份（备份快照 / 调试输出 / 临时脚本，不进 Git）
│
├── dist/                           # 打包产物（智能裁剪设计器V2.2.exe）
├── build/                          # PyInstaller 中间构建产物
├── images/                         # 应用图标（SmartShapeCrop.ico / logo.png）
├── logs/                           # 运行日志 + OCR 诊断截图（自动生成）
└── ProductSummary/                 # 每日程序优化工作总结
    ├── 月度总结/                   #   跨模块任务分类整理总结（按日期聚合）
    ├── 圆角裁剪工具/               #   圆角裁剪工作总结
    ├── 水池设计器/                 #   水池设计器工作总结
    ├── L形挖角设计器/              #   L 形挖角修改总结（阶段 1-5 演进文档）
    └── SmartShapeCrop分析报告/      #   项目分析报告（含 V2.2 全面检测/回归诊断/回归收尾报告）
```

---

## 快速开始

### 环境要求

- Python 3.10+（开发环境实测 Python 3.13）
- Windows 10/11（主要目标平台，支持 PyInstaller 打包 exe）
- **可选（水池设计器/L 形挖角草图 OCR）**：Tesseract-OCR 引擎（需含 `chi_sim` + `eng` 语言包）
  - 源码模式：PathResolver 自动探测安装路径（`C:\Program Files\Tesseract-OCR`、用户目录、环境变量 `TESSERACT_PATH` 等）
  - 打包模式：V2.2 默认将 Tesseract **内嵌进 exe**，用户机器免安装即可使用草图 OCR
  - 未安装 Tesseract 时：水池设计器草图尺寸识别将无法完成（7 步法依赖 OCR 数值识别）；L 形挖角识别降级为纯 CV 几何推断（可用但精度略降）；圆角裁剪功能本身不依赖 OCR

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

### 启动 GUI

```bash
python main.py
```

启动后界面分两部分：

- **左侧**：预览画布（水池设计器渲染 / 圆角裁剪预览 / L 形挖角预览 / 草图直接显示）
- **右侧标签页**：
  - **圆角裁剪工具**：上传成品图 → 自动识别/手动输入参数 → 预览 → 导出
  - **水池设计器**：参数化设计（矩形嵌套/椭圆 + 多层边框）或手绘草图上传 → QThread 后台异步解析 → 自动回填 → 生成预览
  - **L形挖角设计**：独立承载 L 形挖角参数设置 + 草图上传 + 一键生成

> V2.2 打包版：双击 `dist/智能裁剪设计器V2.2.exe` 即可运行（首次启动需解压内嵌资源，等待 5-15 秒）。

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
# 全部测试（实测 380 passed / 7 skipped，约 45 秒）
python -m pytest tests/ -q

# 仅圆角测试
python -m pytest tests/core/test_rounded_corner.py -v

# 草图相关测试
python -m pytest tests/sketch/ -v

# L 形挖角测试（渲染 + 草图解析 + 边框补全 + Profile 路由）
python -m pytest tests/core/test_lshape_render.py tests/core/test_lshape_sketch_parser.py tests/core/test_lshape_border.py tests/core/test_lshape_border_route.py -v

# 集成测试（F1-F19 修复验证）
python -m pytest tests/integration/ -v
```

> 说明：pytest.ini 将 `PytestReturnNotNoneWarning` 升为 ERROR，防止 `return <bool>` 替代 assert 导致断言失效。conftest.py 防御性屏蔽 `scripts/`、`_archive/` 下的 `test_*.py` 命名文件，防止误收集。

### 打包发布

```bash
# 使用当前 V2.2 打包入口，生成单文件 exe（默认）
python packaging/packageV2.2.py

# 目录模式（更稳定）
python packaging/packageV2.2.py --onedir

# 调试模式（带控制台窗口）
python packaging/packageV2.2.py --debug

# 清理旧构建后打包
python packaging/packageV2.2.py --clean

# 不内嵌 Tesseract（默认已内嵌，用户免安装 OCR）
python packaging/packageV2.2.py --no-tesseract
```

打包要点（V2.2）：

- 产物：`dist/智能裁剪设计器V2.2.exe`（单文件，双击运行）
- 自动内嵌本机 Tesseract-OCR 到 exe 内部，用户机器免安装即可使用草图 OCR
- 已在 hidden imports 中显式声明 V2.2 全部模块（含 `core.lshape_border` / `core.lshape_border_route` / `gui.lshape_panel` / `services.*` / `workers.*`），脚本与 `packaging/specs/智能裁剪设计器V2.2.spec` 配置同源
- 打包失败时 onefile 自动回退 onedir；崩溃时在 exe 同目录生成 `crash.log` 便于排障

---

## 版本演进

### V2.2 架构重构（2026-09-12）

**五层分离架构**：将原本集中在 `core/` 下的子包按职责拆分迁移：

| 旧路径 | 新路径 | 变化 |
|---|---|---|
| `core/parser/` | `services/parser/` | 文件名解析 + 模板匹配迁移至服务层 |
| `core/pool_designer/` | `services/sketch_parser/` | 草图解析迁移至服务层 |
| `core/psd/` | `services/psd/` | PSD 加载迁移至服务层 |
| `gui/property_panel_workers.py` | `workers/property_panel_workers.py` | Worker 迁移至线程调度层 |
| — | `models/design_model.py` | 新增数据模型层（DesignModel 包装器） |

- **向后兼容**：`core/compat/__init__.py` 通过 `sys.modules` 别名注册全部旧路径，旧导入无需改动；旧子目录降级为 shim
- **Worker 解耦**：Worker 不再导入任何 `gui/` 模块，仅通过信号转发结果
- **新增 `gui/__init__.py`**：暴露 `PreviewCanvas` / `PropertyPanel` / `CropperPanel` / `LShapePanel` 四个公共 API
- **requirements.txt 加版本约束**：所有依赖标注最低版本要求

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

**打包**：`packaging/packageV2.2.py` + `packaging/specs/智能裁剪设计器V2.2.spec`，补全 V2.2 新模块 hidden imports，默认内嵌 Tesseract。

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

#### L 形挖角草图识别（services/sketch_parser/lshape_sketch_parser.py）

- 两矩形减法推断法：识别两个嵌套矩形相减得出 L 形挖角区域
- 自动方向检测：四象限白色像素比例判定挖角方向（tl/tr/bl/br）
- 挖角尺寸像素→厘米换算（含 1cm 材料损耗补偿）；外框完整尺寸识别
- OCR 兜底路径：Tesseract 不可用时降级为纯 CV 几何推断
- **三级硬约束筛选**：cut_ratio ∈ [0.03, 0.75]、距离 bbox 角 < 40% 对角线，评分 base_score × proximity_factor × ratio_factor + balance_factor 多候选消歧
- **精度修复**：删除 MORPH_CLOSE（避免数字注记与 L 形轮廓合并导致假凹角）、凸包差法 + 大 bbox 过滤

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

### 8. 水池模式素材图渲染（core/image_ops.py）

**渲染执行顺序**（确保四边边框完整）：保存非白色素材像素 → 白色填充内孔区域 → 恢复素材像素 → 绘制 10px 黑色边框线（最后绘制确保最上层）→ **L 形挖角素材边框补全**（rect_lshape + 池素材 + 非 tile 时触发）。

**素材适配模式**：水池模式默认使用 stretch 模式（直接拉伸到目标尺寸不裁剪，避免 cover 模式因方向不匹配裁剪边框）；系统优先匹配方向一致的素材。

**预览渲染优化**：quality 参数区分 preview（BILINEAR，快 3-5×）与 export（LANCZOS）；复用 inner_mask 省去一次 mask 计算；LOD 智能降采样（高细节素材 scale=0.5 + BILINEAR）。

**rect_hole 性能优化**：EDT 距离变换用圆角矩形差集替代（数学等价，33×加速）；compute_border_bands 单 mask 两步 PIL 绘制（7×加速）；carve_corner_on_mask 支持 fill_value/inverse；L 形凹角和椭圆偏移降级为形态学腐蚀；**连通分量向量化**（`np.isin` 替代 Python 循环，395 连通分量从 12.8s 降至毫秒级）。

### 9. 模板库缓存预热（services/parser/template_matcher.py）

- 目录 mtime 持久化到磁盘缓存，未变化时快速跳过（2ms）
- 主线程不阻塞预热：信号槽触发 worker
- 形状+方向关键词严格匹配 + 有方向比例匹配
- TemplateMatcher 公共方法使用 threading.RLock 保证线程安全
- pickle 缓存 + 尺寸比例分桶 + 倒排索引优化

### 10. 统一配置（core/config.py）

业务核心常量（DPI、切割损耗、像素上限、Tesseract 路径、PathResolver）集中在 `config.py` 单一来源；部分算法阈值（边框检测/草图识别/GAP 补偿）仍分散于 `core/corner/detection.py`、`services/sketch_parser/` 各模块，渐进收敛中。

### 11. 历史记录功能（core/app_settings.py）

- 3 天保留策略（含今天）+ 每日 50 条上限 + 同日同名去重置顶
- QSettings/JSON 双通道存储
- 三源物理隔离：圆角裁剪工具 `TARGET_SRC_CROPPER` / 水池设计器 `TARGET_SRC_POOL` / L 形挖角设计 `TARGET_SRC_LSHAPE`

### 12. 日志系统（core/log_setup.py）

- 控制台 + 滚动文件双输出，默认 INFO（调试设 `LOG_LEVEL=DEBUG`）
- 幂等保护；日志路径 `logs/smartshapecrop.log`（5MB 滚动，保留 3 个旧文件）
- 崩溃日志：exe 同目录 `crash.log`（全局 excepthook 写 traceback）
- 启动时 `artifact_cleanup` 自动清理 logs/ 与 debug_output/ 中的过期调试产物（F19，失败不阻断启动）

### 13. 数据模型层（models/design_model.py）

- `DesignModel`：`CropDesign` 的薄包装器
- 提供同步更新、克隆快照、属性读写统一接口
- 模型层不含 UI 引用和业务逻辑，纯数据结构

### 14. 线程调度层（workers/）

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

1. 选择形状模式（矩形嵌套 / 椭圆挖孔）
2. 设置画布尺寸、DPI、外边距
3. 配置多层边框（颜色/厚度/素材填充）
4. 可选：设置边框文字、背景素材
5. 点击"生成预览"渲染 → 菜单 → 文件 → 导出 JPG

### 水池设计器（草图识别模式）

1. 点击「上传草图」选择手绘草图 PNG/JPG
2. 画布立即显示草图 → QThread 后台异步解析 → 7 步流程进度反馈
3. 解析完成：红色矩形框显示识别数据（外框/内挖/上下左右边距）
4. 识别数据自动回填至「内挖边距」面板 → 可手动微调
5. 点击「生成预览」渲染 → 菜单 → 文件 → 导出 JPG

### L 形挖角设计（独立面板）

1. 切换到「L形挖角设计」标签页
2. 设置挖角参数：挖角方向（tl/tr/bl/br 四向）、挖角宽高（含 1cm 损耗）、外框宽高、边角圆角半径
3. 或点击「上传草图」自动识别挖角方向与尺寸（OCR 不可用时降级为几何推断）
4. 输入目标文件名（可选，用于素材匹配与历史记录）
5. 点击「一键生成」渲染 L 形挖角设计图（自带边框的池素材会自动补全 L 形新边缘的边框层次）
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
- 渲染路径极端参数防御机制，防止 GUI 线程挂起
- L 形挖角边框补全静默失败兜底，不影响后续渲染

### 一致性保证

- 圆角处理统一委托 `core.corner.algorithm.carve_corner_on_mask`，在 `geometry.py` / `image_cropper.py` / `process_image.py` 三入口完全一致
- 间隙层判定统一为 `classify_gap_layers` 单一来源
- L 形挖角边框补全通过 `apply_lshape_border_completion` 单一入口，三级路由向后兼容
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

`scripts/diagnose/` 包含案例诊断脚本（复现 Bug、生成诊断图），`scripts/verify/` 包含修复验证脚本（语法自检 / 像素验证 / 端到端）。脚本约定见 `scripts/README.md`（不进 CI、命名约定、30 天归档窗口）。

### 测试

测试位于 `tests/` 目录，按模块分子目录组织，使用 pytest 框架。实测基线（2026-09-12，架构重构后复验）：**380 passed / 7 skipped / 2 failed**（2 个失败为 PyQt5 未安装导致的环境问题，非代码缺陷）。

```bash
# 全部测试
python -m pytest tests/ -q

# 特定测试类
python -m pytest tests/core/test_rounded_corner.py::TestApplyRoundedCorners -v

# 草图相关测试
python -m pytest tests/sketch/test_sketch_parser_logic.py -v

# L 形挖角测试（渲染 + 草图解析 + 边框补全 + Profile 路由）
python -m pytest tests/core/test_lshape_render.py tests/core/test_lshape_sketch_parser.py tests/core/test_lshape_border.py tests/core/test_lshape_border_route.py -v

# 集成测试
python -m pytest tests/integration/ -v
```

> 维护约定：测试必须使用 `assert` 而非 `return <bool>`（PytestReturnNotNoneWarning 已升为 ERROR）；GUI 层推荐使用 `QT_QPA_PLATFORM=offscreen` 模拟。

### 添加新案例

1. 将源图放入指定素材目录（GUI 选择，或 `process_image.py --src` 指定）
2. 按命名规则命名目标文件（见下方文件名解析示例）
3. 运行过程序后，若遇问题参考 `scripts/diagnose/` 编写调试代码
4. 针对反复出现的问题，补充 `tests/` 下的回归测试用例

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

---

## ProductSummary 文档索引

工作总结文档按模块和日期分类整理：

- **月度分类整理总结**（`ProductSummary/月度总结/`，跨模块按日期聚合）
- **水池设计器文档**：`ProductSummary/水池设计器/`，按日期子目录组织（20260813 起）
- **L 形挖角设计器文档**：`ProductSummary/L形挖角设计器/`，按阶段演进组织（阶段 1-5）
- **圆角裁剪工具文档**：`ProductSummary/圆角裁剪工具/`，按日期命名（20260803 起）
- **分析报告**：`ProductSummary/SmartShapeCrop分析报告/`，含 V2.2 全面检测报告、V2.2 回归诊断报告、V2.2 回归收尾报告

---

## 文档维护

> **真相来源**：本 README 是项目结构、架构、模块说明的**唯一权威参考**。其他文档（ProductSummary 下工作总结、分析报告等）为历史记录，不作为当前架构的真相来源。如有冲突，以本文件为准。

### 新鲜度信号

| 项目 | 值 |
|---|---|
| 最后验证 | 2026-09-12 |
| 验证方式 | 全量测试复验 + 目录结构人工核对 + 源码导入路径验证 |
| 生命周期阶段 | 维护期（V2.2 架构重构完成） |

### 更新触发器

以下变更发生时，**必须**同步更新本 README：

| 触发事件 | 需更新章节 |
|---|---|
| 新增/删除/迁移模块或目录 | 目录结构、架构概览、核心模块说明 |
| 测试数量变化 | 快速开始（运行测试）、开发指南（测试） |
| 依赖包变更 | 快速开始（依赖清单） |
| 新增功能或特性 | 核心特性、核心模块说明 |
| 打包配置变更 | 快速开始（打包发布） |
| 架构层调整 | 架构概览、目录结构 |

### 旧路径迁移指引

重构后旧导入路径仍可使用（通过 `core/compat` 的 `sys.modules` 别名），但新代码应优先使用新路径：

| 旧路径 | 新路径（推荐） |
|---|---|
| `core.parser.name_parser` | `services.parser.name_parser` |
| `core.parser.template_matcher` | `services.parser.template_matcher` |
| `core.pool_designer.*` | `services.sketch_parser.*` |
| `core.psd.loader` | `services.psd.loader` |
| `gui.property_panel_workers` | `workers.property_panel_workers` |

---

## 已知问题与后续规划

- **worker 生命周期回归**：当前仅验证"线程可被停止"，未验证"取消后不回写 UI"。需补充 `CropWorker` / `PoolRenderWorker` / `_WarmupScanWorker` 的退役协议回归测试。
- **L 形挖角端到端冒烟**：`dist/智能裁剪设计器V2.2.exe` 已构建，建议对 L 形挖角在发布版 exe 上做一次端到端冒烟验证。
- **历史脚本归档**：`packaging/legacy/` 下旧版本脚本仅保留参考，当前入口为 `packaging/packageV2.2.py`，勿混用。
- **兼容 shim 清理**：当所有调用方迁移到新路径后，可删除 `core/compat/`、`core/parser/`、`core/pool_designer/`、`core/psd/` shim 及 `gui/property_panel_workers.py` shim，无需改动业务代码。

---

## 许可证

本项目为内部工具，未公开许可。
