# SmartShapeCrop — 智能形状裁剪设计器 V2.2

> 矩形 / L形 / 椭圆 挖水池裁剪设计器，面向印刷行业定制尺寸成品图的等比缩放 + 圆角裁剪 + 多层边框处理 + 水池设计器草图 OCR 智能识别 + 多洞嵌套 + L 形挖角独立设计 + L 形挖角素材边框自动补全。

## 项目简介

SmartShapeCrop 是一款面向印刷/定制设计行业的桌面工具，核心解决三大需求：

1. **圆角裁剪工具**：将已有成品图（JPG/PSD）按目标尺寸等比缩放，并自动/手动对四角施加圆角裁剪。支持从文件名自动解析尺寸与圆角参数、模板库匹配源图、多层边框自动检测与圆角重绘。
2. **水池设计器**：参数化生成矩形嵌套、椭圆挖孔等设计稿，支持**手绘草图上传自动识别尺寸**（7 步串行流程）、多层边框、素材填充、边框文字环绕，导出印刷级 JPG。支持**多洞嵌套挖洞**与逐洞独立边距。
3. **L 形挖角设计器**（独立面板）：承载 L 形挖角的参数设置、草图上传与生成。支持草图自动识别挖角方向（tl/tr/bl/br）、挖角尺寸、外框完整尺寸，含 OCR 降级路径；**V2.2 新增素材边框自动补全**——对自带边框的池素材图，在 L 形挖角产生的新边缘上按素材原始边框层次重绘，使成品呈完整 L 形外框。

### 核心特性

- **厘米级精度**：所有尺寸以厘米为单位输入，按 DPI 自动换算像素
- **四角独立圆角**：每个角可独立设置圆角半径（0 = 直角），支持单角/双角/四角组合
- **多层边框自动检测**：通过颜色距离 + 亮度突变双算法识别嵌套边框层，圆角处自动重绘
- **深色外层边框保护**：最外层深色边框（max RGB ≤ 150）永不判为间隙，确保黑色边框线完整
- **仅最外层圆角化**：圆角处仅绘制最外层边框圆弧，内层花纹保持直角，避免产生多余弧线/过厚/色差
- **白色扇形伪影检测**：区分设计白点（散点式）与白色扇形伪影（大面积连续），仅清除伪影
- **文件名智能解析**：从中文文件名提取产品名、尺寸、方向（横版/竖版）、圆角参数，支持全角/特殊字符容错
- **模板库匹配**：根据目标文件名自动匹配模板库中的最佳源图（形状+方向关键词严格匹配）
- **PSD 分层支持**：读取 PSD 图层，自动裁剪透明边距，合成扁平 JPG
- **印刷切割损耗补偿**：自动为目标尺寸加 1cm 扫描余量，圆角半径加 0.5cm 切割损耗
- **LANCZOS 高质量缩放**：默认 `simple_resize` 模式，不裁剪不留白，最小质量损失
- **大图性能优化**：圆角重绘采用 ROI（仅处理角区域）+ 向量化运算，支持 1-2 亿像素印刷级大图
- **GUI 不阻塞**：大图裁剪/导出运行于 QThread 后台线程，带进度反馈，避免界面冻结；UI 参数修改采用防抖渲染（200ms 延迟、800ms 最大等待）
- **草图智能识别**（水池设计器）：上传手绘草图 → 7 步串行流程（矩形检测→区域划分→多尺度 OCR→小数修复→方向标签锁定→空间映射→几何校验）→ 自动回填外框/内挖/上下左右边距 8 字段
- **OCR 稳定性投票机制**：位置聚类 + 众数投票，消除偶发误识别
- **多洞嵌套挖洞**：支持矩形嵌套多洞（包络盒消除 + 逐洞独立边距 + 逐洞 10px 黑色边框）
- **L 形挖角独立识别**：两矩形减法推断法 + OCR 兜底，自动检测挖角方向与尺寸；OCR 不可用时纯 CV 几何降级
- **L 形挖角素材边框自动补全**（V2.2）：对自带边框的池素材图，沿 L 形两条新切边在保留区一侧按素材原始边框层次重绘，内凹角用 `max(dx, dy)` 几何分层保证边框沿 L 形轮廓连续
- **三级边框路由**（V2.2）：Profile 路径 → V13 路径 → 旧 detect_pool_material_borders 路径，任一环节失败自动落到下一环节，向后兼容
- **模板库缓存预热**（V2.2）：目录 mtime 持久化到磁盘缓存，未变化时快速跳过（2ms）；主线程不阻塞预热，信号槽触发 worker
- **历史记录功能**：目标文件名 3 天历史记录，三个面板物理隔离独立存储（圆角裁剪/水池设计器/L 形挖角）
- **预览渲染优化**：预览用 BILINEAR（快 3-5×），导出用 LANCZOS，复用 inner_mask
- **LOD 智能降采样**：高细节素材采用 scale=0.5 + BILINEAR（避免 0.25/NEAREST 产生马赛克伪影）

---

## 目录结构

```
SmartShapeCrop/
├── main.py                         # 应用入口（PyQt5 主窗口 + 3 标签页 + 模板预设 + 全局异常 crash.log）
├── process_image.py                # 命令行批处理脚本（等比缩放 + 圆角）
├── conftest.py                     # pytest 全局 fixture
├── requirements.txt                # Python 依赖（PyQt5/Pillow/numpy/psd-tools/opencv-python-headless/pytesseract/pytest）
├── pytest.ini                      # 测试配置
├── crash.log                       # 全局 excepthook 崩溃日志（PyInstaller 无控制台时排障关键）
│
├── core/                           # 核心业务逻辑
│   ├── config.py                   #   统一配置管理（阈值、单位换算、黄金值、硬上限单源管理）
│   ├── geometry.py                 #   参数化形状定义 + Mask 生成 + compute_inner_corner_radii 模式区分
│   ├── image_ops.py                #   图像操作（加载/缩放/平铺/边框合成/文字/导出/quality 参数 + L 形挖角边框补全集成）
│   ├── image_cropper.py            #   裁剪服务（缩放 + 圆角 + 多层边框重绘 + 内层花纹保护）
│   ├── image_cropper_border.py     #   裁剪边框相关逻辑（从 image_cropper 拆分）
│   ├── image_cropper_mask.py       #   裁剪 mask 相关逻辑（从 image_cropper 拆分）
│   ├── lshape_border.py            #   L 形挖角素材边框补全（apply_lshape_border_completion 入口 + 三级路由）
│   ├── lshape_border_route.py      #   L 形挖角「描边+色带+细边框」Profile 路由（V2.2 新增，剖面扫描 + 三层封顶）
│   ├── log_setup.py                #   统一日志配置（控制台 + 滚动文件，默认 INFO 级别）
│   ├── app_settings.py             #   历史记录存储层（QSettings/JSON 双通道 + 物理隔离）
│   ├── artifact_cleanup.py         #   产物清理（构建/打包后清理临时文件）
│   │
│   ├── corner/                     #   圆角处理子包
│   │   ├── algorithm.py            #     单步扇形切割算法（carve_corner_on_mask + fill_value/inverse 参数）
│   │   ├── detection.py            #     边框层自动检测（颜色距离 + 亮度突变，classify_gap_layers 统一逻辑）
│   │   └── sector_render.py        #     圆角弧线多层边框重绘（仅最外层 + 深色外层保护 + 白色扇形伪影检测）
│   │
│   ├── parser/                     #   文件名解析子包
│   │   ├── name_parser.py          #     文件名解析（尺寸/方向/圆角/产品名，6 层容错）
│   │   └── template_matcher.py     #     模板库扫描与匹配引擎（形状+方向关键词严格匹配 + 有方向比例匹配 + mtime 缓存）
│   │
│   ├── psd/                        #   PSD 分层文件处理
│   │   └── loader.py               #     PSD 读取/裁剪/合成
│   │
│   ├── pool_designer/              #   水池设计器子包（模块化拆分）
│   │   ├── __init__.py             #     模块导出
│   │   ├── sketch_parser.py        #     草图识别入口（7 步串行流程 + 全局 OCR + 位置映射 + 稳定性投票）
│   │   ├── sketch_parser_base.py   #     草图识别基类与公共工具
│   │   ├── sketch_parser_cache.py  #     OCR 结果缓存（避免重复调用 Tesseract）
│   │   ├── sketch_parser_margins.py#     边距识别与自洽校验
│   │   ├── sketch_parser_numbers.py#     数字 Token 合并与小数修复
│   │   ├── sketch_parser_vision.py #     矩形检测 / 区域划分 / 方向标签扫描
│   │   ├── sketch_parser_multihole.py#   多洞识别（Phase A-E 消包络盒 + OCR 加权众数投票）
│   │   └── lshape_sketch_parser.py #     L 形挖角草图识别（两矩形减法推断 + OCR 兜底 + 几何降级）
│   │
│   └── compat/                     #   向后兼容层
│       └── __init__.py
│
├── gui/                            # PyQt5 界面（property_panel 模块化拆分）
│   ├── canvas_widget.py            #   预览画布（全分辨率渲染 + 缩放显示 + 草图直接显示 + quality='preview' + ExportSaveWorker 后台导出）
│   ├── cropper_panel.py            #   圆角裁剪面板（上传/识别/预览/导出/QThread 后台线程 + 历史记录 TARGET_SRC_CROPPER）
│   ├── lshape_panel.py             #   L 形挖角独立设计面板（草图上传 + 一键生成 + 历史记录 TARGET_SRC_LSHAPE）
│   ├── property_panel.py           #   水池设计器属性面板主入口（聚合子模块 + 历史记录 TARGET_SRC_POOL + 防抖渲染）
│   ├── property_panel_widgets.py  #   自定义控件（_SketchDropLabel 草图拖拽等）
│   ├── property_panel_workers.py  #   QThread Worker（草图解析 / L 形解析 / 渲染）
│   ├── property_panel_dialogs.py  #   对话框（L 形挖角确认 / 数据回填）
│   ├── property_panel_generate.py #   生成预览逻辑
│   ├── property_panel_layers.py   #   多层边框编辑 UI
│   └── property_panel_poolbox.py   #   水池模式草图识别与边距回填 + L 形识别按钮调度
│
├── tests/                          # 单元测试（按模块分子目录，299 passed / 5 skipped）
│   ├── conftest.py
│   ├── core/                       #   核心模块测试（圆角/裁剪/文件名解析/模板匹配/L 形渲染/L 形草图解析/L 形边框补全）
│   │   ├── test_rounded_corner.py
│   │   ├── test_lshape_render.py
│   │   ├── test_lshape_sketch_parser.py
│   │   ├── test_lshape_border.py        #   L 形挖角边框补全端到端测试
│   │   ├── test_lshape_border_route.py  #   Profile 路由 / 自动路由集成 / 向后兼容测试（V2.2 新增）
│   │   ├── test_image_cropper.py
│   │   ├── test_name_parser.py
│   │   ├── test_template_matcher.py
│   │   └── test_corner_analysis_simple.py
│   ├── gui/                        #   GUI 模拟测试
│   ├── integration/               #   集成测试（F1-F19 修复验证 / 水池-L 形流程 / 配置）
│   ├── sketch/                     #   草图识别测试（多洞 / 特征 / 输入校验 / 修复 / 诊断 / 验证）
│   └── border/                     #   边框测试（边框修复 / 复杂花纹安全 / 间隙分析 / 用户案例）
│
├── scripts/                        # 诊断/调试脚本（开发用）
│   ├── README.md                   #   脚本目录说明
│   ├── split_*.py                  #   模块拆分辅助脚本（image_cropper/property_panel/sketch_parser）
│   ├── diagnose/                   #   圆角缺陷 / OCR 问题 / 草图解析根因诊断脚本
│   ├── verify/                     #   修复验证脚本
│   └── _archive/                   #   归档的旧脚本
│
├── packaging/                      # PyInstaller 打包脚本与配置（集中管理）
│   ├── packageV2.1.2.py           #   当前打包入口（动态路径 + Tesseract 自动查找）
│   ├── package.py                 #   历史版本（V1/V2.0/V2.1）
│   ├── packageV2.0.py
│   ├── packageV2.1.py
│   ├── build_exe.bat              #   历史打包批处理
│   ├── README.md                   #   打包目录说明
│   └── specs/                     #   失效/历史 .spec 归档
│
├── dist/                           # 打包产物（.exe）
│   └── 智能裁剪设计器V2.1.2.exe
│
├── images/                         # 应用图标与 Logo
├── logs/                           # 运行日志 + OCR 诊断截图（自动生成）
├── debug_output/                   # 调试中间图像输出
└── ProductSummary/                 # 每日程序优化工作总结（按模块/日期分目录）
    ├── 圆角裁剪工具/                 #   圆角裁剪工具工作总结
    ├── 水池设计器/                   #   水池设计器工作总结（按日期子目录）
    ├── L形挖角设计器/                #   L 形挖角设计修改总结（阶段 1-5 演进文档）
    ├── SmartShapeCrop分析报告/       #   项目分析报告与修复补丁（含 V2.2 全面检测报告）
    ├── 2026-08/                     #   8 月任务分类整理总结
    └── 2026-09/                     #   9 月任务分类整理总结
```

---

## 快速开始

### 环境要求

- Python 3.10+（推荐 3.12/3.13）
- Windows 10/11（主要目标平台，支持 PyInstaller 打包 exe）
- **可选（水池设计器/L 形挖角草图 OCR）**：Tesseract-OCR 引擎（Windows 默认安装路径 `C:\Program Files\Tesseract-OCR`，自动检测并设置 `TESSDATA_PREFIX`，需含 `chi_sim` 中文语言包）
  - 未安装 Tesseract 时，水池设计器草图尺寸识别将无法完成（7 步法依赖 OCR 数值识别）；L 形挖角识别会降级为纯 CV 几何推断（可用但精度略降）；圆角裁剪功能本身不依赖 OCR，可正常使用

### 安装依赖

```bash
pip install -r requirements.txt
```

依赖清单：

| 包 | 用途 |
|---|---|
| PyQt5 | GUI 界面 |
| Pillow | 图像处理核心 |
| numpy | 像素级向量化运算 |
| psd-tools | PSD 分层文件读取 |
| opencv-python-headless | 形态学运算 + 草图矩形检测 + cv2.erode 距离变换（headless 版避免与 PyQt5 Qt 插件冲突） |
| pytesseract | 水池设计器/L 形挖角草图 OCR 数字识别（可选，需系统 Tesseract 引擎） |
| pytest | 单元测试（开发环境） |

### 启动 GUI

```bash
python main.py
```

启动后界面分两部分：
- **左侧**：预览画布（水池设计器渲染 / 圆角裁剪预览 / L 形挖角预览 / 草图直接显示）
- **右侧标签页**：
  - **圆角裁剪工具**：上传成品图 → 自动识别/手动输入参数 → 预览 → 导出
  - **水池设计器**：参数化设计（矩形嵌套/椭圆 + 多层边框）或手绘草图上传 → QThread 后台异步解析 → 红色框显示识别数据 → 自动回填面板 → 生成预览
  - **L形挖角设计**：独立承载 L 形挖角参数设置 + 草图上传 + 一键生成；自动识别挖角方向/尺寸/外框

### 命令行批处理

`process_image.py` 通过命令行参数控制（默认取脚本目录下 `psd_demo` 的示例图）：

```bash
# 默认参数直接运行（使用脚本目录 psd_demo 内的示例图）
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
# 全部测试
python -m pytest tests/ -v

# 仅圆角测试
python -m pytest tests/core/test_rounded_corner.py -v

# 草图相关测试
python -m pytest tests/sketch/ -v

# L 形挖角测试（渲染 + 草图解析 + 边框补全）
python -m pytest tests/core/test_lshape_render.py tests/core/test_lshape_sketch_parser.py tests/core/test_lshape_border.py tests/core/test_lshape_border_route.py -v

# 集成测试（F1-F19 修复验证）
python -m pytest tests/integration/ -v
```

### 打包发布

```bash
# 使用当前打包配置生成 exe
python packaging/packageV2.1.2.py
```

> ⚠️ **打包注意事项（V2.2）**：当前打包入口仍为 `packaging/packageV2.1.2.py`。V2.2 新增的 `core/lshape_border.py` 与 `core/lshape_border_route.py` 通过 `core/image_ops.py` 函数内延迟导入，属 PyInstaller 高风险漏收场景——若未加入打包白名单/hidden imports，漏收时只在用户触发 L 形挖角边框补全时才报错，测试期发现不了。打包前请确认这两个模块已被 PyInstaller 收集（可通过 `--collect-submodules core` 或显式 hidden imports 覆盖）。

---

## 核心模块说明

### 1. 圆角裁剪算法（core/corner/）

圆角处理是本项目最复杂的子系统，采用**单步扇形切割算法**（拒绝先挖方再填色的两步法，避免中心区域被重复着色）：

```
步骤1: 把角落 r×r 正方形区域设为 0（切掉尖角）
步骤2: 用 pieslice 把"矩形内部的 1/4 圆"填回 255（保留圆弧）
→ 切掉的是 L 形（正方形减去 1/4 圆），只切尖角，保留圆弧
```

> 注意：mask 创建统一使用 `carve_corner_on_mask`（支持 fill_value 和 inverse 参数，单 mask 两步绘制）。其内部采用纯 numpy 距离场算法（v2，2026-08-14 重写），在 r×r 正方形内先挖掉尖角、再按距离场填回 1/4 圆弧，几何上保证过渡点无 C 形缺口、无过绘；不再依赖 PIL pieslice 栅格化，因此无需额外的边界像素后处理。

**PIL 屏幕坐标系角度映射**（y 轴向下）：

| 角 | 角度范围 | 圆心位置 |
|---|---|---|
| TL（左上） | 180° → 270° | (x+r, y+r) |
| TR（右上） | 270° → 360° | (x+w-r, y+r) |
| BL（左下） | 90° → 180° | (x+r, y+h-r) |
| BR（右下） | 0° → 90° | (x+w-r, y+h-r) |

**多层边框动态圆角**：每层边框的有效半径按累计厚度递减
```
R_eff_i = max(0, R_total - cumulative_thickness_i)
```

**仅最外层圆角化（only_outermost + protect_content 常驻，V2.2 强化）**：
- 圆角处**仅绘制最外层边框圆弧**，内层花纹保持直角
- `protect_content` 常驻开启，确保内层花纹不被误圆角
- `angle_in_corner_sector` 统一判定角扇区，避免内层被误判为需要圆角
- 裁剪 mask（边框带限制）与边框重绘有效性 mask（完整扇形）**分离**，防止圆角处边框变薄

**深色外层边框保护机制**：
- 最外层(i=0)深色边框（max RGB ≤ 150）**永不判为间隙**
- 仅浅色外层（max RGB > 150）可通过邻居差异判定为间隙
- 添加 `is_outermost_solid` 标志确保外层边框始终绘制
- 间隙层判定统一为 `classify_gap_layers` 单一来源，消除多处独立逻辑互相矛盾

**圆角边界白线与深色弧线修复（V2.2）**：
- `tol` 一致性：圆角 mask 边界容差统一 1.0 → 2.0，消除白线残留
- `ring_region` 限制在 `border_zone`（直边区），防止保护到圆弧外侧的深色边框残留
- `inner_cut` 限幅，防止圆角内层误圆角化
- 白色空隙多层结构感知重绘，保证断触处粗细一致

**白色扇形伪影检测规则**：
- 总白像素 <20 → 保留所有
- 白像素 ≥50 且角度跨度 ≥2° 且径向跨度 ≥5px → 清除所有
- 中间情况 → 清除连续 ≥3 像素的簇
- 检测仅检查弧外侧区域（距离 ≥ 半径 + 边框厚度 + 5px），避免误清除设计白点

### 2. 边框层自动检测（core/corner/detection.py）

双算法并行检测：

| 算法 | 原理 | 阈值 | 适用场景 |
|---|---|---|---|
| 颜色距离检测 | RGB 欧氏距离 > 阈值视为不同颜色 | 15 | 逐层颜色识别（黑/白/棕交替边框） |
| 亮度突变检测 | R+G+B 总和一阶差分 > 阈值视为边界 | 25 | 嵌套矩形边界扫描 |

**防误判四重机制**（防止内容区/花纹被误判为边框层）：
- **厚度硬上限**：单层 ≤ 2cm，所有层累计总厚度 ≤ 3cm（超出则截断或丢弃最末层）
- **最大 4 层硬限制**：真实边框通常不超过 4 层，超过则极可能是内容花纹被误判
- **薄边框跳变检测**：薄边框（≤1cm）后出现 3 倍厚度跃变 → 判定为内容区伪边框并丢弃
- **花纹周期截断**：检测到 A↔B 颜色交替重复模式时截断，避免花纹被识别为多层边框

### 3. 圆角弧线边框重绘（core/corner/sector_render.py）

圆角裁剪后，弧线上的多层边框需要重新绘制以保持连续。采用**仅最外层策略**（`only_outermost=True`），并增加三层清理遍历防止多余米色弧线/白色方块：

- **仅绘制最外层边框圆弧**：内层边框与间隙层保持原图状态，避免产生多余弧线/过厚/色差
- **间隙层智能处理**：
  - 相邻层颜色对比防误判（实心边框不会被误判为间隙）
  - 间隙层使用原间隙色填充而非跳过
  - 颜色通道极差法区分间隙类型：极差 ≤8.0 → 均匀间隙（清空为背景色）；>8.0 → 装饰间隙（保留原贴图）
  - 预渲染清理 + 后处理清理 + 深度超限清理**三层清理遍历**
- **有效边框深度限制**：圆角处仅渲染半径 70% 深度范围内的边框层（硬上限 ~3cm），超出视为内容区
- **装饰像素保护**：直边延伸区颜色匹配过滤，对角内区取内容参考色（内容安全区 15%-85% 范围 21×21 均匀采样 → RGB 中值），层颜色与内容参考色欧氏距离 > 15 → 强制绘制
- **边界完整性**：极坐标→离散像素映射留 2px 容差；角度边界包含两端 + TR 角 360° 环绕处理；OUTER_BAND 缩减至 3px，弧形边界操作限制 ±2px

### 4. 文件名解析（core/parser/name_parser.py）

支持从中文文件名提取结构化信息，示例：

```
双面格-定制-定制尺寸-简织;竖版54x41cm右下角圆角半径2cm.jpg
├─ 产品名: 双面格-定制-定制尺寸-简织
├─ 方向:   竖版
├─ 尺寸:   41 x 54 cm（竖版短边在前）
└─ 圆角:   {tl:0, tr:0, bl:0, br:2.0}
```

**支持的圆角格式**：
- 四角：`4个圆角半径2cm` / `四角半径5cm`
- 两角：`左下角和右下角做3cm半径圆弧角`
- 单角：`左下角圆角半径3.1cm` / `右下角半径2cm`
- 口语：`左下角是圆角3.1cm半径`

**尺寸解析容错**：6 层策略从高到低（带单位正则 → 无单位正则 → 宽松正则 → findall → 取前两个数字 → 字符级手动扫描），支持全角/特殊字符/隐藏字符。

### 5. 裁剪模式（core/image_cropper.py）

| 模式 | 说明 |
|---|---|
| `simple_resize` | 简单缩放（默认，LANCZOS 高质量，不裁剪不留白） |
| `cover` | 裁剪填满（裁掉超出部分，可能损失内容） |
| `contain` | 留白填充（完整显示，四周补背景色） |
| `light_cover` | 轻度裁剪（最多裁剪 15%，平衡内容与比例） |
| `auto` | 智能模式（自动选择 cover 或 contain） |

### 6. L 形挖角素材边框补全（core/lshape_border.py + lshape_border_route.py，V2.2）

V2.2 新增子系统。当对带有自绘边框的素材图（如克罗印花的棕色边框+黑色内框、安妮森林的黑色细边框、蔓生花的米色边距+细线）应用 L 形挖角时，挖掉的角落区域的两条新边缘需要绘制与素材图一致的边框层，使 L 形成品在视觉上呈现完整的外框。

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

#### Profile 路径（core/lshape_border_route.py，V2.2 新增）

针对 V13 / 旧路径都失效的素材（最外层不是黑描边，而是米色/白色边距 + 细线 + 点带 + 细框）：

- **1D 颜色剖面扫描**：素材四条边由外向内扫描（多条扫描线取均值抹平点状花纹）
- **锚点对齐**：抗 1~6px 出血白边，保证四边层序一致、投票颜色真实
- **有序层分割**：输出 `[(color, thickness_px), ...]`（外→内，最多 3 层，含外边距层）
- **截断到第二条细线**：描边 + 色带 + 内框线 = 3 层封顶（点带/文字带及其内侧细线不处理）
- **厚度按 scale 换算**到画布坐标系后调用 `patch_lshape_cut_layers`（N 层推广）
- **内凹角几何分层**：用 `max(dx, dy)` 距离映射到层区间 `[offs[k], offs[k+1])`，保证边框沿 L 形轮廓连续

典型素材结构映射：

| 素材 | 剖面结构 | 层数 | 路由 |
|---|---|---|---|
| 克罗印花 | 黑描边 + 棕色带（直通内部，限厚） | 2 | Profile 让位 V13 |
| 蔓生花 | 黑描边 + 米色边距 + 细线 | 3 | Profile（止于最外内框线） |
| 中古雨林 | 黑描边 + 白边距 + 框线 | 3 | Profile（同上） |
| 庄园秘境 | 出血白边（锚点跳过）+ 深黑带 + 米底 | 2 | Profile |

#### 关键约束

- **使用原始素材图检测**：避免 `adapt_pool_material` 拉伸造成的边框像素畸变，用 scale 因子换算到画布坐标系
- **bg_color 用实际素材底色**：白色硬编码会把米色等底色误判为边框层，导致 L 形内环色带色差
- **厚黑首层让位 V13**：首层近黑且厚度 ≥ `_THICK_BLACK_MIN`（50px）时先问 V13，V13 命中则走 V13（已验证路径）
- **无边框跳过补全**：`_is_real_border` 判定（边缘-中心色差 < 50 或总厚 > 短边 30% → 非真实边框）
- **静默失败兜底**：补全过程异常静默跳过，不影响后续渲染

### 7. 水池设计器草图识别（core/pool_designer/）

水池设计器核心子系统。从用户上传的手绘草图自动识别**8 项关键数值**：外框宽/高(total_w/total_h)、内孔宽/高(inner_w/inner_h)、上下左右边距(margin_top/bottom/left/right)。支持**方向标签图**和**无标签双矩形图**两种输入场景。

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

#### 模块化拆分

草图识别按职责拆分为多个子模块，单一来源、单一职责：

| 模块 | 职责 |
|---|---|
| `sketch_parser.py` | 入口（7 步串行编排 + 全局 OCR + 稳定性投票） |
| `sketch_parser_base.py` | 基类与公共工具 |
| `sketch_parser_cache.py` | OCR 结果缓存（避免重复调用 Tesseract） |
| `sketch_parser_margins.py` | 边距识别与自洽校验 |
| `sketch_parser_numbers.py` | 数字 Token 合并与小数修复 |
| `sketch_parser_vision.py` | 矩形检测 / 区域划分 / 方向标签扫描 |
| `sketch_parser_multihole.py` | 多洞识别（Phase A-E 消包络盒 + OCR 加权众数投票） |
| `lshape_sketch_parser.py` | L 形挖角草图识别（两矩形减法推断 + OCR 兜底 + 几何降级） |

#### OCR 稳定性投票机制

解决 OCR 识别不稳定问题，对关键 ROI 区域进行多次识别并取众数：
- `_make_preprocess_variants`：不同预处理产生投票源
- `_vote_by_position`：位置聚类 + 众数投票，票数相同时按置信度总和决胜
- 投票置信度 = 原始最大置信度 × (0.5 + 0.5 × 众数占比)

#### 多洞嵌套挖洞识别（sketch_parser_multihole.py）

支持矩形嵌套多洞场景：
- **Phase A-E 五段算法**：包络盒消除 → 逐洞区域划分 → 多尺度 OCR → 加权众数投票 → 逐洞几何校验
- **逐洞独立边距**：每个洞独立记录上下左右边距（HoleInfo 字段扩展）
- **逐洞 10px 黑色边框**：每洞独立几何差集算四边 10px 环，合并为完整 mask
- **0.5cm 量化加权众数**：消除 OCR 偶发误差

#### L 形挖角草图识别（lshape_sketch_parser.py）

- **两矩形减法推断法**：从草图识别两个嵌套矩形，相减得出 L 形挖角区域
- **自动方向检测**：四象限白色像素比例判定挖角方向（tl/tr/bl/br）
- **挖角尺寸计算**：像素尺度换算为厘米（含 1cm 材料损耗补偿）
- **外框完整尺寸**：不受挖角影响，识别完整矩形外框
- **OCR 兜底路径**：Tesseract 不可用时降级为纯 CV 几何推断（四象限白像素比例 + 像素尺度）
- **几何驱动标签归属**：避免 OCR 小字符 D/C 误判
- **三级硬约束筛选**（V2.2）：cut_ratio ∈ [0.03, 0.75]、距离 bbox 角 < 40% 对角线，增强评分 base_score × proximity_factor × ratio_factor + balance_factor 多候选消歧
- **草图识别精度修复**（V2.2）：删除 MORPH_CLOSE（避免数字注记与 L 形轮廓合并导致假凹角）、凸包差法 + 大 bbox 过滤

#### 关键约束与安全机制

| 机制 | 说明 |
|---|---|
| **33%硬边带约束** | margin_top cy≤H×0.33、margin_bottom cy≥H×0.67、margin_left cx≤W×0.33、margin_right cx≥W×0.67 |
| **边距合理性硬门槛** | 边距不可能大于短边×80% |
| **整数优先排序** | 整数边距在实际应用中更常见 |
| **OCR 自洽检查** | 0.10 ≤ implied_ratio ≤ 0.90 时信任 OCR 值 |
| **内框可靠性评级** | 面积<5%或纵横比>15 → unreliable → 放弃几何反推 → OCR 边距反推 |
| **几何值 50%上限** | 边距几何值 > 外框对应边×50%直接拒绝 |
| **OCR 边距自洽检测** | OCR 左右(上下)和内推内框∈[10%,90%]外框 → 优先采用 OCR 值 |
| **相邻数字 Token 合并** | "左1"+"2"→12、"7"+".5"→7.5 |
| **方向标签三重门** | 百位数检查 + 数量级差异(10×) + 差异>50%放弃标签 |

#### 椭圆形挖洞识别

- 椭圆参数以厘米为单位（44.7×34.8cm 指长和宽）
- 椭圆判断条件：内框面积<外框 50%且宽高比<60%
- 椭圆参数计算：外框尺寸减去两边边距
- 降级路径：单个矩形 + 方向标签时，外框即为椭圆边界

#### GUI 交互

- 草图上传后**直接显示在主画布**（消除悬浮缩略图）
- QThread**后台异步解析**（立即显示草图，不阻塞 UI）
- 解析完成后红色矩形框立即显示 8 字段识别结果（外框/内挖/上下左右 + 辅助提示）
- 生成完成后自动加载预览图 + 兜底补显示
- `debug["direction_margins"]` 和 `geometry_margins` 存储供 GUI 对比显示
- **防抖渲染**（V2.2）：UI 参数修改（如 SpinBox 值）采用 200ms 延迟、800ms 最大等待，防止主线程阻塞

#### OCR 硬依赖处理

- 自动检测 Tesseract-OCR 安装路径（Windows 默认路径 `C:\Program Files\Tesseract-OCR`）并设置 `TESSDATA_PREFIX`
- `from PIL import Image as PILImage` 显式导入（避免 NameError 静默吞 OCR 结果）
- 缺引擎时水池草图尺寸识别将无法完成（无几何降级路径），程序本身不崩溃；L 形挖角可降级为纯 CV 几何推断；圆角裁剪功能不受影响

### 8. 水池模式素材图渲染（core/image_ops.py）

**渲染执行顺序**（确保四边边框完整）：
1. 保存非白色素材像素
2. 白色填充内孔区域
3. 恢复保存的素材像素
4. 绘制 10px 黑色边框线（最后绘制，确保最上层显示）
5. **L 形挖角素材边框补全**（V2.2，rect_lshape + 池素材 + 非 tile 时触发，见第 6 节）

**素材适配模式**：
- 水池模式默认使用 **stretch 模式**（直接拉伸到目标尺寸不裁剪，避免 cover 模式因方向不匹配裁剪边框）
- 系统优先匹配方向一致的素材

**预览渲染优化**：
- 添加 `quality` 参数：`preview` 用 BILINEAR（快 3-5×），`export` 用 LANCZOS
- 复用 `inner_mask` 省去一次 mask 计算和 corner radii 计算
- 保存时重新渲染 LANCZOS 确保导出质量
- **LOD 智能降采样**：高细节素材（如"安妮森林"）采用 scale=0.5 + BILINEAR，避免 scale=0.25 + NEAREST 产生马赛克伪影

**rect_hole 性能优化**：
- EDT 距离变换用圆角矩形差集替代（数学等价，33×加速）
- compute_border_bands 单 mask 两步 PIL 绘制（7×加速）
- carve_corner_on_mask 新增 fill_value 和 inverse 参数
- L 形凹角和椭圆偏移降级为形态学腐蚀
- **向量化连通分量分析**：用 `np.isin` 替代 Python 循环（395 连通分量从 12.8s 降至毫秒级）

**圆角设置独立性**：
- `compute_inner_corner_radii` 通过 `direct` 参数区分水池模式和普通模式
- 零半径角保持直角，非零半径角圆角正确
- 水池模式圆角独立于普通裁剪模式

### 9. 模板库缓存预热（core/parser/template_matcher.py，V2.2）

- **目录 mtime 持久化**：扫描结果缓存到磁盘，根目录 mtime 未变化时快速跳过（2ms）
- **主线程不阻塞**：预热通过信号槽机制触发 worker，不在主线程阻塞等待
- **形状+方向关键词严格匹配**：根据目标文件名自动匹配模板库中的最佳源图
- **有方向比例匹配**：支持有方向素材的比例匹配

### 10. 统一配置（core/config.py）

所有业务常量集中在 `config.py` 单一来源，包括：
- 边框检测阈值（颜色距离、亮度差分、扫描步长、最大层数等）
- 默认值（DPI=150、背景色白色、裁剪模式 simple_resize）
- 切割损耗（尺寸+1cm、圆角+0.5cm）
- 单位换算（cm ↔ px）
- 像素上限（2 亿像素，防御解压缩炸弹）
- 边距 SpinBox 上限 200cm、OCR ROI 几何过滤系数、自洽判定阈值等

### 11. 历史记录功能（core/app_settings.py）

- **3 天保留策略**：保留今天及前 2 天历史记录
- **每日上限**：每天上限 50 条，自动清理过期记录
- **双通道存储**：QSettings/JSON 双通道
- **数据结构**：按日期分组，包含名称、时间戳和来源
- **物理隔离**：三个面板历史记录完全独立存储
  - 圆角裁剪工具：`TARGET_SRC_CROPPER`
  - 水池设计器：`TARGET_SRC_POOL`
  - L 形挖角设计：`TARGET_SRC_LSHAPE`
- **公共 API**：接受 source 参数以区分不同面板

### 12. 日志系统（core/log_setup.py）

- 控制台 + 滚动文件双输出
- 默认 **INFO** 级别（调试时设 `LOG_LEVEL=DEBUG`）
- 幂等保护，重复调用不重复添加 handler
- 日志路径：`logs/smartshapecrop.log`（5MB 滚动，保留 3 个旧文件）
- 崩溃日志：exe 同目录 `crash.log`（全局 excepthook 写 traceback，PyInstaller 无控制台时排障关键）
- 方向字线索日志 + 诊断日志级别调整

### 13. L 形挖角独立设计面板（gui/lshape_panel.py）

把 L 形挖角功能从【水池设计器】拆出，单开一个面板放在【水池设计器】右侧，承载所有 L 形挖角相关 UI 与识别逻辑：

- 独立承载 L 形挖角参数设置（挖角方向 tl/tr/bl/br、挖角宽高、外框宽高、边角圆角半径、素材名、目标名）
- 草图上传 + 目标文件名输入 + 一键生成控件
- 通过信号委托给 PropertyPanel 的同一套实现（共享状态、共享逻辑），两个面板都保留各自的 UI
- 双向同步：PropertyPanel 通过 `set_lshape_params()` / `sync_sketch_to_lshape()` / `sync_target_to_lshape()` 回填本面板 UI
- 独立历史记录源 `TARGET_SRC_LSHAPE`，与圆角裁剪/水池设计器物理隔离
- 解析 Worker 复用 `property_panel_workers._LShapeParseWorker`，确认对话框复用 `property_panel_dialogs._LShapeConfirmDialog`

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
5. 点击"生成预览"渲染
6. 菜单 → 文件 → 导出 JPG
7. **历史记录**：可查看目标文件名 3 天历史记录

### 水池设计器（草图识别模式）

1. 点击**「上传草图」**选择手绘草图 PNG/JPG
2. 画布立即显示草图 → QThread 后台异步解析 → 7 步流程进度反馈
3. 解析完成：**红色矩形框显示识别数据**（外框/内挖/上下左右边距）
4. 识别数据自动回填至「内挖边距」面板 → 可手动微调
5. 点击「生成预览」渲染水池设计图 → 画布显示预览
6. 菜单 → 文件 → 导出 JPG

### L 形挖角设计（独立面板）

1. 切换到「L形挖角设计」标签页
2. 设置挖角参数：挖角方向（tl/tr/bl/br 四向单选）、挖角宽高（含 1cm 损耗）、外框宽高、边角圆角半径
3. 或点击**「上传草图」**自动识别挖角方向与尺寸（OCR 不可用时降级为几何推断）
4. 输入目标文件名（可选，用于素材匹配与历史记录）
5. 点击「一键生成」渲染 L 形挖角设计图（自带边框的池素材会自动补全 L 形新边缘的边框层次）
6. 菜单 → 文件 → 导出 JPG
7. **历史记录**：独立第三源，与水池设计器/圆角裁剪物理隔离

### 内置模板

| 模板 | 说明 |
|---|---|
| 图 矩形嵌套挖洞 | 3 层边框 + 米色背景 + 边框文字 |
| 图 L形挖角 | L 形挖角 + 浅米色 + 简单边框 |
| 图 椭圆嵌套 | 椭圆挖孔 + 3 层边框 + 白色画布 |

---

## 技术要点

### 图像完整性保障

- 缩放统一使用 `Image.LANCZOS` 重采样算法（预览用 BILINEAR）
- 圆角处使用 `validity_mask` 保护透明区域不被重新着色
- 边框颜色采样跳过 2px 抗锯齿过渡带，使用 5% 修剪均值避免色差
- 多层圆角颜色映射使用层索引而非深度值：裁剪区域显示下一层颜色
- 极坐标→离散像素映射留 2px 容差，避免弧线像素被切掉形成 C 形缺口
- mask 创建使用 `carve_corner_on_mask`（支持 fill_value/inverse 参数）替代 PIL `rounded_rectangle`
- 内孔边框使用圆角矩形差集替代 EDT（数学等价，33×加速）保证精确 10px 黑色边线宽度
- L 形挖角边框补全使用原始素材图检测 + scale 换算，避免拉伸畸变

### 安全机制

- `Image.MAX_IMAGE_PIXELS = 200_000_000`（2 亿像素上限，防御解压缩炸弹）
- 圆角半径限制：`min(radius, min(w, h) // 2)`，防止中心区域被着色
- 边框厚度硬上限：单层 ≤ 2cm，总厚度 ≤ 3cm（防止内容区/花纹被误判为边框）
- 边框层数硬限制：最多 4 层 + 薄边框跳变检测 + 花纹周期截断
- 深色外层保护：最外层深色边框（max RGB ≤ 150）永不判为间隙
- 渲染路径极端参数防御机制：防止 GUI 线程挂起
- OCR 数值范围严格校验：0.3-500cm，过滤异常值
- OCR 全局唯一性检查：差值<0.15 跳过，防止单值占据多字段
- 边距合理性硬门槛：边距不可能大于短边×80%
- L 形挖角边框补全静默失败兜底：异常不影响后续渲染

### 一致性保证

圆角处理逻辑在三个入口保持完全一致：
- `geometry.py`（设计器渲染）
- `image_cropper.py`（裁剪服务）
- `process_image.py`（命令行脚本）

统一委托给 `core.corner.algorithm.carve_corner_on_mask`，单一来源。

间隙层判定统一为 `classify_gap_layers` 单一来源，消除多处独立逻辑互相矛盾。

L 形挖角边框补全通过 `apply_lshape_border_completion` 单一入口，三级路由向后兼容。

---

## 开发指南

### 调试

```bash
# 启用 DEBUG 日志
set LOG_LEVEL=DEBUG
python main.py

# 查看日志
# logs/smartshapecrop.log
# logs/*.png （草图识别 OCR 诊断截图：外框/内框/上下左右边距间隙）
```

### 测试

测试位于 `tests/` 目录，按模块分子目录组织，使用 pytest 框架：

```bash
# 全部测试
python -m pytest tests/ -v

# 特定测试类
python -m pytest tests/core/test_rounded_corner.py::TestApplyRoundedCorners -v

# 草图相关测试
python -m pytest tests/sketch/test_sketch_parser_logic.py -v

# L 形挖角测试（渲染 + 草图解析 + 边框补全 + Profile 路由）
python -m pytest tests/core/test_lshape_render.py tests/core/test_lshape_sketch_parser.py tests/core/test_lshape_border.py tests/core/test_lshape_border_route.py -v

# 集成测试
python -m pytest tests/integration/ -v
```

> 提示：若 pytest 退出码非 0 但无 failed 用例，可能是环境批量删除保护干扰收尾清理，用 `--basetemp` 指定独立临时根目录即可拿到干净退出码。

`scripts/diagnose/` 目录下包含诊断脚本（`diagnose_*.py`、`debug_*.py`、`_diag_*.py`），用于特定案例的圆角缺陷诊断 / OCR 识别问题 / 草图解析根因定位。`scripts/verify/` 包含修复验证脚本。

### 添加新案例

1. 将源图放入 `psd_demo/` 或指定目录
2. 按命名规则命名目标文件（见下方文件名解析示例）
3. 运行 `process_image.py` 或通过 GUI 处理
4. 如遇问题，参考 `scripts/diagnose/` 目录下的诊断脚本编写调试代码
5. 针对反复出现的问题，补充 `tests/` 下的回归测试用例

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
| P2 | 矩形检测早停（串行二值掩码生成） | 节省 50%以上 Step1 时间 |
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
| JPG 导出异步化（QThread + 进度对话框 + 可取消） | 消除大图导出 UI 冻结 |
| 草图识别模块化拆分 | 单一职责，可维护性提升 |
| GUI 参数修改防抖渲染（200ms 延迟、800ms 最大等待） | 防止 SpinBox 连续改动阻塞主线程 |
| 模板库 dir_mtime 磁盘缓存 + 信号槽预热 | 未变化时快速跳过（2ms），主线程不阻塞 |
| L 形挖角边框补全三级路由 | Profile/V13/旧路径自动回退，向后兼容 |
| Profile 路径锚点对齐 + 三层封顶 | 抗出血白边，V13/旧路径失效素材可补全 |

所有优化通过像素级一致性验证保证几何等价，草图识别逻辑与功能保持不变。

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
| 墨上花开 | 边框过粗 + 白色弧形缺口 | 白色扇形伪影检测 |
| 花漾之约 | 白色扇形伪影 | 区分设计白点与伪影 |
| 蔓生花 | 边框厚度不一致 | 边框厚度统一 |
| 安妮森林 | 白色边框线 + 米色弧形缺口 | 深色外层保护（max RGB ≤ 150） |

### 9.07 圆角裁剪 T1-T5 修复（V2.2）

| 修复 | 问题 | 修复方式 |
|---|---|---|
| T1 | 圆角处多层边框都被圆角化 | `only_outermost=True` + `protect_content` 常驻 |
| T2 | 圆角白线与内层误圆角 | `angle_in_corner_sector` 统一 + 仅最外层构建 mask |
| T3 | 圆角边界白线残留 | `tol` 一致性 1.0 → 2.0 |
| T4 | 蔓生花/素锦深色弧线 + 南瓜无忧白隙 | `ring_region` 收窄 + `inner_cut` 限幅 |
| T5 | 深色弧线未消除根因 | `ring_region` 限于 `border_zone`（直边区） |

---

## ProductSummary 目录文档索引

工作总结文档按模块和日期分类整理：

- **总分类整理总结**：
  - [20260817-任务分类整理总结.md](ProductSummary/20260817-任务分类整理总结.md)
  - [20260818-19-任务分类整理总结.md](ProductSummary/20260818-19-任务分类整理总结.md)
  - [20260820-21-任务分类整理总结.md](ProductSummary/20260820-21-任务分类整理总结.md)
  - [20260826-28-任务分类整理总结.md](ProductSummary/20260826-28-任务分类整理总结.md)
  - [20260829-任务分类整理总结.md](ProductSummary/20260829-任务分类整理总结.md)
  - [20260831-0902-任务分类整理总结.md](ProductSummary/20260831-0902-任务分类整理总结总结.md)
  - [20260903-任务分类整理总结.md](ProductSummary/2026-09/20260903-任务分类整理总结.md)
  - [20260904-任务分类整理总结.md](ProductSummary/2026-09/20260904-任务分类整理总结.md)
  - [20260905-任务分类整理总结.md](ProductSummary/2026-09/20260905-任务分类整理总结.md)
  - [20260907-任务分类整理总结.md](ProductSummary/2026-09/20260907-任务分类整理总结.md)

- **水池设计器子问题文档**：位于 `ProductSummary/水池设计器/`，按日期子目录组织（20260813-20260905），命名格式「日期-解决同类问题」
- **L 形挖角设计器文档**：位于 `ProductSummary/L形挖角设计器/`，按四阶段演进组织（架构设计期→落地实现期→代码审计期→独立面板期）
- **圆角裁剪工具文档**：位于 `ProductSummary/圆角裁剪工具/`，沿用既有命名规范
- **V2.2 分析报告**：位于 `ProductSummary/SmartShapeCrop分析报告/`，含 V2.2 全面检测报告、回归诊断报告、回归收尾报告

---

## 许可证

本项目为内部工具，未公开许可。
