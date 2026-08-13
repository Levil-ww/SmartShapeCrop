# SmartShapeCrop — 智能形状裁剪设计器

> 矩形 / L形 / 椭圆 挖水池裁剪设计器，面向印刷行业定制尺寸成品图的等比缩放 + 圆角裁剪 + 多层边框处理。

## 项目简介

SmartShapeCrop 是一款面向印刷/定制设计行业的桌面工具，核心解决两大需求：

1. **水池设计器**：参数化生成矩形嵌套、L形挖角、椭圆挖孔等设计稿，支持多层边框、素材填充、边框文字环绕，导出印刷级 JPG。
2. **圆角裁剪工具**：将已有成品图（JPG/PSD）按目标尺寸等比缩放，并自动/手动对四角施加圆角裁剪。支持从文件名自动解析尺寸与圆角参数、模板库匹配源图、多层边框自动检测与圆角重绘。

### 核心特性

- **厘米级精度**：所有尺寸以厘米为单位输入，按 DPI 自动换算像素
- **四角独立圆角**：每个角可独立设置圆角半径（0 = 直角），支持单角/双角/四角组合
- **多层边框自动检测**：通过颜色距离 + 亮度突变双算法识别嵌套边框层，圆角处自动重绘
- **文件名智能解析**：从中文文件名提取产品名、尺寸、方向（横版/竖版）、圆角参数，支持全角/特殊字符容错
- **模板库匹配**：根据目标文件名自动匹配模板库中的最佳源图
- **PSD 分层支持**：读取 PSD 图层，自动裁剪透明边距，合成扁平 JPG
- **印刷切割损耗补偿**：自动为目标尺寸加 1cm 扫描余量，圆角半径加 0.5cm 切割损耗
- **LANCZOS 高质量缩放**：默认 `simple_resize` 模式，不裁剪不留白，最小质量损失
- **大图性能优化**：圆角重绘采用 ROI（仅处理角区域）+ 向量化运算，支持 1-2 亿像素印刷级大图
- **GUI 不阻塞**：大图裁剪/导出运行于 QThread 后台线程，带进度反馈，避免界面冻结

---

## 目录结构

```
SmartShapeCrop/
├── main.py                    # 应用入口（PyQt5 主窗口）
├── process_image.py           # 命令行批处理脚本（等比缩放 + 圆角）
├── requirements.txt           # Python 依赖
├── pytest.ini                 # 测试配置
├── run_test.bat               # 快速运行圆角测试
│
├── core/                      # 核心业务逻辑
│   ├── config.py              # 统一配置管理（常量、阈值、单位换算）
│   ├── geometry.py            # 参数化形状定义 + Mask 生成
│   ├── image_ops.py           # 图像操作（加载/缩放/平铺/边框合成/文字/导出）
│   ├── image_cropper.py       # 裁剪服务（缩放 + 圆角 + 边框重绘）
│   ├── log_setup.py           # 统一日志配置（控制台 + 滚动文件）
│   │
│   ├── corner/                # 圆角处理子包
│   │   ├── algorithm.py       #   单步扇形切割算法（核心几何）
│   │   ├── detection.py       #   边框层自动检测（颜色距离 + 亮度突变）
│   │   └── sector_render.py   #   圆角弧线多层边框重绘
│   │
│   ├── parser/                # 文件名解析子包
│   │   ├── name_parser.py     #   文件名解析（尺寸/方向/圆角/产品名）
│   │   └── template_matcher.py#   模板库扫描与匹配引擎
│   │
│   ├── psd/                   # PSD 分层文件处理
│   │   └── loader.py          #   PSD 读取/裁剪/合成
│   │
│   └── compat/                # 向后兼容层
│       └── __init__.py
│
├── gui/                       # PyQt5 界面
│   ├── canvas_widget.py       # 预览画布（全分辨率渲染 + 缩放显示）
│   ├── cropper_panel.py       # 圆角裁剪面板（上传/识别/预览/导出）
│   └── property_panel.py      # 水池设计器属性面板
│
├── tests/                     # 单元测试
│   ├── test_rounded_corner.py #   圆角裁剪测试（20+ 用例）
│   ├── test_image_cropper.py  #   裁剪服务测试
│   ├── test_name_parser.py    #   文件名解析测试
│   ├── test_template_matcher.py#  模板匹配测试
│   ├── test_border_fix.py     #   边框修复测试
│   └── test_config.py         #   配置测试
│
├── scripts/                   # 诊断/调试脚本（开发用）
│   ├── diagnose/              #   圆角缺陷诊断脚本
│   ├── verify/                #   修复验证脚本
│   └── _archive/              #   归档的旧脚本
│
├── psd_demo/                  # PSD 示例文件
├── logs/                      # 运行日志（自动生成）
└── ProductSummary/            # 每日程序优化工作总结
```

---

## 快速开始

### 环境要求

- Python 3.10+
- Windows / macOS / Linux

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
| opencv-python | 形态学运算（腐蚀/膨胀） |
| pytest | 单元测试（开发环境） |

### 启动 GUI

```bash
python main.py
```

启动后界面分两部分：
- **左侧**：预览画布
- **右侧标签页**：
  - **圆角裁剪工具**：上传成品图 → 自动识别/手动输入参数 → 预览 → 导出
  - **水池设计器**：参数化设计矩形/L形/椭圆挖孔 + 多层边框

### 命令行批处理

编辑 `process_image.py` 中的参数后运行：

```bash
python process_image.py
```

示例配置：
```python
src_path = r"D:\SmartShapeCrop\psd_demo\源图.jpg"
target_w_cm = 41.0    # 目标宽度
target_h_cm = 55.0    # 目标高度
corner_r_cm = 2.0     # 右下角圆角半径
dpi = 150             # 输出 DPI
```

### 运行测试

```bash
# 全部测试
python -m pytest

# 仅圆角测试
python -m pytest tests/test_rounded_corner.py -v

# 或使用快捷脚本
run_test.bat
```

---

## 核心模块说明

### 1. 圆角裁剪算法（core/corner/）

圆角处理是本项目最复杂的子系统，采用**单步扇形切割算法**：

```
步骤1: 把角落 r×r 正方形区域设为 0（切掉尖角）
步骤2: 用 pieslice 把"矩形内部的 1/4 圆"填回 255（保留圆弧）
→ 切掉的是 L 形（正方形减去 1/4 圆），只切尖角，保留圆弧
```

> 注意：mask 创建统一使用 `carve_corner_on_mask`（而非 PIL 的 `rounded_rectangle`，后者在半径相等时会产生边界像素缺失）。大半径（≥9cm）场景额外调用 `_fill_corner_boundary_pixels` 对 `r±1.5px` 范围做边界像素后处理，补偿 pieslice 舍入误差。

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

嵌套矩形层感知（大半径场景）：
```
R_eff(k, corner) = max(0, R_total - D(k, corner))
其中 D(k, corner) = max(横向距离, 纵向距离)  // 该层矩形到图像边缘的距离
```

### 2. 边框层自动检测（core/corner/detection.py）

双算法并行检测：

| 算法 | 原理 | 阈值 | 适用场景 |
|---|---|---|---|
| 颜色距离检测 | RGB 欧氏距离 > 阈值视为不同颜色 | 15 | 逐层颜色识别（黑/白/棕交替边框） |
| 亮度突变检测 | R+G+B 总和一阶差分 > 阈值视为边界 | 25 | 嵌套矩形边界扫描 |

**防误判三重机制**（防止内容区/花纹被误判为边框层）：
- **厚度硬上限**：单层 ≤ 2cm，所有层累计总厚度 ≤ 3cm（超出则截断或丢弃最末层）
- **最大 4 层硬限制**：真实边框通常不超过 4 层，超过则极可能是内容花纹被误判
- **薄边框跳变检测**：薄边框（≤1cm）后出现 3 倍厚度跃变 → 判定为内容区伪边框并丢弃
- **花纹周期截断**：检测到 A↔B 颜色交替重复模式时截断，避免花纹被识别为多层边框

### 3. 圆角弧线边框重绘（core/corner/sector_render.py）

圆角裁剪后，弧线上的多层边框需要重新绘制以保持连续。采用**仅最外层策略**（`only_outermost=True`）：

- **仅绘制最外层边框圆弧**：内层边框与间隙层保持原图状态，避免产生多余弧线/过厚/色差
- **间隙层智能处理**：
  - 构造 `solid_border_colors_arr`（排除间隙层颜色）后再做装饰检测，避免间隙色与边框色相近时检测失效
  - 颜色通道极差法区分间隙类型：极差 ≤8.0 → 均匀间隙（清空为背景色）；>8.0 → 装饰间隙（保留原贴图）
  - 预渲染清理 + 后处理清理双重兜底
- **有效边框深度限制**：圆角处仅渲染半径 70% 深度范围内的边框层（硬上限 ~3cm），超出视为内容区
- **装饰像素保护**：直边延伸区颜色匹配过滤，对角内区取内容参考色（内容安全区 15%-85% 范围 21×21 均匀采样 → RGB 中值），层颜色与内容参考色欧氏距离 > 15 → 强制绘制
- **边界完整性**：极坐标→离散像素映射留 2px 容差；角度边界包含两端 + TR 角 360°环绕处理

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

### 6. 统一配置（core/config.py）

所有业务常量集中在 `config.py` 单一来源，包括：
- 边框检测阈值（颜色距离、亮度差分、扫描步长、最大层数等）
- 默认值（DPI=150、背景色白色、裁剪模式 simple_resize）
- 切割损耗（尺寸+1cm、圆角+0.5cm）
- 单位换算（cm ↔ px）
- 像素上限（2 亿像素，防御解压缩炸弹）

### 7. 日志系统（core/log_setup.py）

- 控制台 + 滚动文件双输出
- 默认 WARNING 级别，调试时设 `LOG_LEVEL=DEBUG`
- 幂等保护，重复调用不重复添加 handler
- 日志路径：`logs/smartshapecrop.log`（5MB 滚动，保留 3 个旧文件）

---

## GUI 使用流程

### 圆角裁剪工具

1. **上传源图**：点击"选择源图"按钮，支持 JPG/PNG/PSD
2. **自动匹配**（可选）：输入目标文件名，自动从模板库匹配源图
3. **参数识别**：自动从文件名解析尺寸和圆角参数，也可手动调整
4. **预览**：点击"预览"查看裁剪效果
5. **导出**：点击"导出 JPG"保存印刷级图片

### 水池设计器

1. 选择形状模式（矩形嵌套 / L形挖角 / 椭圆挖孔）
2. 设置画布尺寸、DPI、外边距
3. 配置多层边框（颜色/厚度/素材填充）
4. 可选：设置边框文字、背景素材
5. 点击"生成预览"渲染
6. 菜单 → 文件 → 导出 JPG

### 内置模板

| 模板 | 说明 |
|---|---|
| 图1 矩形嵌套+文字 | 3层边框 + 米色背景 + 边框文字 |
| 图2/4 L形挖角 | L形挖角 + 浅米色 + 简单边框 |
| 图3 椭圆嵌套 | 椭圆挖孔 + 3层边框 + 白色画布 |
| 图5 瓷砖嵌套 | 4层边框 + 瓷砖花纹填充 |

---

## 技术要点

### 图像完整性保障

- 缩放统一使用 `Image.LANCZOS` 重采样算法
- 圆角处使用 `validity_mask` 保护透明区域不被重新着色
- 边框颜色采样跳过 2px 抗锯齿过渡带，使用 5% 修剪均值避免色差
- 多层圆角颜色映射使用层索引而非深度值：裁剪区域显示下一层颜色
- 极坐标→离散像素映射留 2px 容差，避免弧线像素被切掉形成 C 形缺口
- mask 创建使用 `carve_corner_on_mask` 替代 PIL `rounded_rectangle`，大半径场景叠加 `_fill_corner_boundary_pixels` 后处理

### 安全机制

- `Image.MAX_IMAGE_PIXELS = 200_000_000`（2 亿像素上限，防御解压缩炸弹）
- 圆角半径限制：`min(radius, min(w, h) // 2)`，防止中心区域被着色
- 边框厚度硬上限：单层 ≤ 2cm，总厚度 ≤ 3cm（防止内容区/花纹被误判为边框）
- 边框层数硬限制：最多 4 层 + 薄边框跳变检测 + 花纹周期截断

### 一致性保证

圆角处理逻辑在三个入口保持完全一致：
- `geometry.py`（设计器渲染）
- `image_cropper.py`（裁剪服务）
- `process_image.py`（命令行脚本）

统一委托给 `core.corner.algorithm.carve_corner_on_mask`，单一来源。

---

## 开发指南

### 调试

```bash
# 启用 DEBUG 日志
set LOG_LEVEL=DEBUG
python main.py

# 查看日志
# logs/smartshapecrop.log
```

### 测试

测试位于 `tests/` 目录，使用 pytest 框架（当前 **114 项测试全部通过**）：

```bash
# 全部测试
python -m pytest tests/ -v

# 特定测试类
python -m pytest tests/test_rounded_corner.py::TestApplyRoundedCorners -v
```

`scripts/diagnose/` 目录下包含诊断脚本（`diagnose_*.py`、`debug_*.py`），用于特定案例的圆角缺陷诊断与修复验证。

### 添加新案例

1. 将源图放入 `psd_demo/` 或指定目录
2. 按命名规则命名目标文件（见上方文件名解析示例）
3. 运行 `process_image.py` 或通过 GUI 处理
4. 如遇问题，参考 `scripts/diagnose/` 目录下的诊断脚本编写调试代码

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

## 程序检测报告（2026-08-13）

### 功能验证

| 项目 | 结果 |
|---|---|
| 单元测试 | ✅ 114/114 全部通过 |
| 圆角裁剪算法 | ✅ 单步扇形切割，四角角度映射正确 |
| 多层边框检测 | ✅ 双算法并行（颜色距离 + 亮度突变） |
| 文件名解析 | ✅ 6 层容错策略，全角/特殊字符兼容 |
| 模板匹配 | ✅ 形状 + 方向关键词严格匹配 |
| PSD 分层读取 | ✅ 自动裁剪透明边 + 合成 RGB |
| GUI 后台线程 | ✅ 大图裁剪/导出不阻塞主界面 |

### 代码质量检测

| 类别 | 状态 | 说明 |
|---|---|---|
| 异常处理 | ⚠️ 低风险 | 核心代码 28 处 `except Exception` 均带日志记录或用户反馈，无裸 `except: pass` 静默吞没 |
| 资源管理 | ⚠️ 中风险 | 核心代码存在 **5 处** `Image.open()` 未使用上下文管理器，处理超大图（>5000px）时可能累积文件句柄 |
| 类型注解 | ✅ 良好 | 核心模块函数均带类型标注（`__future__ annotations`） |
| 日志配置 | ✅ 良好 | 统一 `log_setup.py`，滚动文件 + 控制台双输出 |
| 配置集中 | ✅ 良好 | 常量统一在 `core/config.py`，阈值单源管理 |

### 资源泄漏待修复点（建议）

| 文件 | 行号 | 函数 | 修复建议 |
|---|---|---|---|
| [image_ops.py](file:///D:/SmartShapeCrop/core/image_ops.py#L24-L29) | 26 | `load_image_rgb` | 使用 `with Image.open(path) as img:` |
| [loader.py](file:///D:/SmartShapeCrop/core/psd/loader.py#L144) | 144 | `load_psd_flat` fallback | 使用 `with Image.open(path) as img:` + `.copy()` |
| [name_parser.py](file:///D:/SmartShapeCrop/core/parser/name_parser.py#L739) | 739 | `get_image_info` | 使用 `with Image.open(path) as img:` |
| [loader.py](file:///D:/SmartShapeCrop/core/psd/loader.py#L86) | 86 | `load_psd_layers` | `PSDImage.open` 建议确保资源释放 |
| [loader.py](file:///D:/SmartShapeCrop/core/psd/loader.py#L133) | 133 | `load_psd_flat` | `PSDImage.open` 建议确保资源释放 |

### 已修复的历史 P0 问题

| 问题 | 修复方式 | 修复位置 |
|---|---|---|
| 大图逐像素处理导致性能瓶颈 | 改为 ROI 角区域限制 + 向量化索引 | `sector_render.py::_redraw_border_on_corner` |
| GUI 裁剪/导出时界面冻结 | 移入 QThread 后台线程 + 进度对话框 | `cropper_panel.py::CropWorkerThread` |
| 嵌套层圆角半径计算错误（白色扇角） | `max(横距,纵距)` 替代 `min` | `image_cropper.py::_compute_layer_effective_radius` |
| PIL `rounded_rectangle` 边界像素缺失 | 改用 `carve_corner_on_mask` 自制 mask | `corner/algorithm.py` |

---

## 许可证

本项目为内部工具，未公开许可。
