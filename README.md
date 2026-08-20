# SmartShapeCrop — 智能形状裁剪设计器

> 矩形 / L形 / 椭圆 挖水池裁剪设计器，面向印刷行业定制尺寸成品图的等比缩放 + 圆角裁剪 + 多层边框处理 + 水池设计器草图OCR智能识别。

## 项目简介

SmartShapeCrop 是一款面向印刷/定制设计行业的桌面工具，核心解决两大需求：

1. **水池设计器**：参数化生成矩形嵌套、L形挖角、椭圆挖孔等设计稿，支持**手绘草图上传自动识别尺寸**、多层边框、素材填充、边框文字环绕，导出印刷级 JPG。
2. **圆角裁剪工具**：将已有成品图（JPG/PSD）按目标尺寸等比缩放，并自动/手动对四角施加圆角裁剪。支持从文件名自动解析尺寸与圆角参数、模板库匹配源图、多层边框自动检测与圆角重绘。

### 核心特性

- **厘米级精度**：所有尺寸以厘米为单位输入，按 DPI 自动换算像素
- **四角独立圆角**：每个角可独立设置圆角半径（0 = 直角），支持单角/双角/四角组合
- **多层边框自动检测**：通过颜色距离 + 亮度突变双算法识别嵌套边框层，圆角处自动重绘
- **文件名智能解析**：从中文文件名提取产品名、尺寸、方向（横版/竖版）、圆角参数，支持全角/特殊字符容错
- **模板库匹配**：根据目标文件名自动匹配模板库中的最佳源图（形状+方向关键词严格匹配）
- **PSD 分层支持**：读取 PSD 图层，自动裁剪透明边距，合成扁平 JPG
- **印刷切割损耗补偿**：自动为目标尺寸加 1cm 扫描余量，圆角半径加 0.5cm 切割损耗
- **LANCZOS 高质量缩放**：默认 `simple_resize` 模式，不裁剪不留白，最小质量损失
- **大图性能优化**：圆角重绘采用 ROI（仅处理角区域）+ 向量化运算，支持 1-2 亿像素印刷级大图
- **GUI 不阻塞**：大图裁剪/导出运行于 QThread 后台线程，带进度反馈，避免界面冻结
- **草图智能识别**（水池设计器）：上传手绘草图 → 全局OCR+位置映射 → 自动回填外框/内挖/上下左右边距8字段；方向标签与无标签双矩形双模式支持

---

## 目录结构

```
SmartShapeCrop/
├── main.py                    # 应用入口（PyQt5 主窗口 + 模板预设 + 全局异常crash.log）
├── process_image.py           # 命令行批处理脚本（等比缩放 + 圆角）
├── requirements.txt           # Python 依赖（PyQt5/Pillow/numpy/psd-tools/opencv-python/pytest）
├── pytest.ini                 # 测试配置
├── run_test.bat               # 快速运行圆角测试
├── package.py / 智能裁剪设计器.spec  # PyInstaller 打包配置
│
├── core/                      # 核心业务逻辑
│   ├── config.py              # 统一配置管理（阈值、单位换算、黄金值、硬上限单源管理）
│   ├── geometry.py            # 参数化形状定义 + Mask 生成
│   ├── image_ops.py           # 图像操作（加载/缩放/平铺/边框合成/文字/导出）
│   ├── image_cropper.py       # 裁剪服务（缩放 + 圆角 + 多层边框重绘 + 内层花纹保护）
│   ├── log_setup.py           # 统一日志配置（控制台 + 滚动文件，默认INFO级别）
│   │
│   ├── corner/                # 圆角处理子包
│   │   ├── algorithm.py       #   单步扇形切割算法（carve_corner_on_mask 核心几何）
│   │   ├── detection.py       #   边框层自动检测（颜色距离 + 亮度突变，4层硬上限+跳变检测）
│   │   └── sector_render.py   #   圆角弧线多层边框重绘（间隙色填充+三层清理遍历）
│   │
│   ├── parser/                # 文件名解析子包
│   │   ├── name_parser.py     #   文件名解析（尺寸/方向/圆角/产品名，6层容错）
│   │   └── template_matcher.py#   模板库扫描与匹配引擎（形状+方向关键词严格匹配）
│   │
│   ├── psd/                   # PSD 分层文件处理
│   │   └── loader.py          #   PSD 读取/裁剪/合成
│   │
│   ├── pool_designer/         # 水池设计器子包
│   │   ├── __init__.py        #   模块导出
│   │   └── sketch_parser.py   #   草图尺寸识别（全局OCR+位置映射/几何校验/方向标签/Token合并）
│   │
│   └── compat/                # 向后兼容层
│       └── __init__.py
│
├── gui/                       # PyQt5 界面
│   ├── canvas_widget.py       # 预览画布（全分辨率渲染 + 缩放显示 + 草图直接显示）
│   ├── cropper_panel.py       # 圆角裁剪面板（上传/识别/预览/导出/QThread后台线程）
│   └── property_panel.py      # 水池设计器属性面板（草图上传/异步解析/红色框识别状态/预览回调）
│
├── tests/                     # 单元测试（共10个测试文件，146项用例）
│   ├── test_rounded_corner.py #   圆角裁剪测试
│   ├── test_image_cropper.py  #   裁剪服务测试
│   ├── test_name_parser.py    #   文件名解析测试
│   ├── test_template_matcher.py#  模板匹配测试
│   ├── test_border_fix.py     #   边框修复测试
│   ├── test_config.py         #   配置测试
│   ├── test_fix_validation.py #   修复验证测试（方向矫正/边距验证/名称解析）
│   ├── test_parse_sketch_characterization.py # 草图识别特性测试
│   ├── test_sketch_input_validation.py       # 草图输入合法性验证
│   └── test_sketch_parser_logic.py           # 草图解析逻辑单元测试（几何/一致性/约束）
│
├── scripts/                   # 诊断/调试脚本（开发用）
│   ├── diagnose/              #   圆角缺陷/OCR问题诊断脚本
│   ├── verify/                #   修复验证脚本
│   └── _archive/              #   归档的旧脚本
│
├── images/                    # 应用图标与Logo
├── logs/                      # 运行日志 + OCR诊断截图（自动生成）
├── debug_output/              # 调试中间图像输出
└── ProductSummary/            # 每日程序优化工作总结（圆角裁剪工具/水池设计器分类）
```

---

## 快速开始

### 环境要求

- Python 3.10+（推荐 3.12/3.13）
- Windows 10/11（主要目标平台，支持 PyInstaller 打包 exe）
- **可选（水池设计器草图OCR）**：Tesseract-OCR 引擎（Windows 默认安装路径 `C:\Program Files\Tesseract-OCR`，自动检测并设置 `TESSDATA_PREFIX`）
  - 未安装 Tesseract 时草图识别自动回退到几何比例计算，不影响圆角裁剪功能

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
| opencv-python | 形态学运算 + 草图矩形检测 + EDT距离变换 |
| pytesseract | 水池设计器草图OCR数字识别（可选，需系统Tesseract引擎） |
| scipy | 水池设计器内孔边框距离变换（精确10px边线宽度） |
| pytest | 单元测试（开发环境） |

### 启动 GUI

```bash
python main.py
```

启动后界面分两部分：
- **左侧**：预览画布（水池设计器渲染 / 圆角裁剪预览 / 草图直接显示）
- **右侧标签页**：
  - **圆角裁剪工具**：上传成品图 → 自动识别/手动输入参数 → 预览 → 导出
  - **水池设计器**：
    1. 参数化设计（矩形/L形/椭圆 + 多层边框）
    2. 手绘草图上传 → QThread后台异步解析 → 红色框显示识别数据（外框/内挖/上下左右）→ 自动回填面板 → 生成预览

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
# 全部测试（146项）
python -m pytest tests/ -v

# 仅圆角测试
python -m pytest tests/test_rounded_corner.py -v

# 仅水池设计器草图识别
python -m pytest tests/test_sketch_parser_logic.py tests/test_parse_sketch_characterization.py tests/test_sketch_input_validation.py -v

# 或使用快捷脚本
run_test.bat
```

---

## 核心模块说明

### 1. 圆角裁剪算法（core/corner/）

圆角处理是本项目最复杂的子系统，采用**单步扇形切割算法**（拒绝先挖方再填色的两步法，避免中心区域被重复着色）：

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

**内层花纹保护（corner_protect双mask机制）**：
- 当圆角半径 ≤ 2×总边框厚度时 → **仅圆角边框区域**（L形条带并集），内层花纹保持直角
- 半径 ≥ 4.0cm 时圆角所有嵌套层，但内层仍按2×阈值保持直角
- 裁剪mask（边框带限制）与边框重绘有效性mask（完整扇形）**分离**，防止圆角处边框变薄

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
- **边界完整性**：极坐标→离散像素映射留 2px 容差；角度边界包含两端 + TR 角 360°环绕处理；OUTER_BAND 缩减至 3px，弧形边界操作限制 ±2px

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
- **水池设计器黄金值**（固定规格草图毫秒级快速通道，单源管理）
- 边距SpinBox上限200cm、OCR ROI几何过滤系数、自洽判定阈值等

### 7. 日志系统（core/log_setup.py）

- 控制台 + 滚动文件双输出
- 默认 **INFO** 级别（调试时设 `LOG_LEVEL=DEBUG`）
- 幂等保护，重复调用不重复添加 handler
- 日志路径：`logs/smartshapecrop.log`（5MB 滚动，保留 3 个旧文件）
- 崩溃日志：exe 同目录 `crash.log`（全局 excepthook 写 traceback，PyInstaller无控制台时排障关键）

### 8. 水池设计器草图识别（core/pool_designer/sketch_parser.py）

水池设计器核心子系统。从用户上传的手绘草图自动识别**8项关键数值**：外框宽/高(total_w/total_h)、内孔宽/高(inner_w/inner_h)、上下左右边距(margin_top/bottom/left/right)。支持**方向标签图**和**无标签双矩形图**两种输入场景。

#### 核心识别管道（6步简化管道）
```
目标尺寸提取 → Otsu二值化 + 4掩码策略(Otsu/Canny/Adaptive/HighThr)
   → 嵌套矩形检测（面积3-97%过滤 + 边界伪矩形剔除 + 内框暗色区域回退）
   → 全局OCR扫描（无白名单正则后置提取 + 相邻数字Token合并）
   → 物理位置映射 + 方向标签合并（合理性三重门+50%差异阈值）
   → 几何一致性校验（inner = outer - margin_sum，5%偏差强制修正）
```

#### 9项增强机制（2026-08-18 ~ 2026-08-19连续迭代）

| # | 机制 | 解决问题 |
|---|---|---|
| 1 | **全局OCR扫描 + 位置分区映射** | 替代间隙窄带扫描，不漏方向标签附近数字；去除字符白名单→正则后置提取（支持£6/#53带符号） |
| 2 | **相邻数字Token合并** | `"左1"+"2"→12`、`"7"+".5"→7.5`；正则支持 `.5` 小数开头 |
| 3 | **方向标签合理性三重门** | 百位数检查 + 数量级差异(10×) + 差异>50%放弃标签→防止右53→453类误覆盖 |
| 4 | **内框可靠性评级** | 面积<5%或纵横比>15→`unreliable`→放弃几何反推→改用OCR边距反推内框 |
| 5 | **几何值50%上限拦截** | 边距几何值>外框对应边×50%直接拒绝→防止36.5→87.3覆盖 |
| 6 | **OCR边距自洽检测** | OCR左右(上下)和内推内框∈[10%,90%]外框→优先采用OCR值（78×58大理石2号案例） |
| 7 | **几何OCR交叉验证** | 差>15%且>5cm→触发该字段聚焦OCR重扫 |
| 8 | **嵌套矩形面积过滤器3-97% + 几何一致性5%强制修正** | 拦截0.68%极小伪内框（234×60/133×60.5案例） |
| 9 | **_geometry_driven_parse同步增强** | 几何驱动路径同样具备Token合并/50%上限/优先级逻辑，清除__pycache__后生效 |

#### GUI交互
- 草图上传后**直接显示在主画布**（消除悬浮缩略图）
- QThread**后台异步解析**（立即显示草图，不阻塞UI）
- 解析完成后红色矩形框立即显示8字段识别结果（外框/内挖/上下左右+辅助提示）
- 生成完成后自动加载预览图+兜底补显示
- `debug["direction_margins"]`和`geometry_margins`存储供GUI对比显示

#### OCR硬依赖处理
- 自动检测Tesseract-OCR安装路径（Windows默认路径 `C:\Program Files\Tesseract-OCR`）并设置`TESSDATA_PREFIX`
- `from PIL import Image as PILImage` 显式导入（避免NameError静默吞OCR结果）
- 缺引擎时自动回退几何比例，不崩溃

---

## GUI 使用流程

### 圆角裁剪工具

1. **上传源图**：点击"选择源图"按钮，支持 JPG/PNG/PSD
2. **自动匹配**（可选）：输入目标文件名，自动从模板库匹配源图
3. **参数识别**：自动从文件名解析尺寸和圆角参数，也可手动调整
4. **预览**：点击"预览"查看裁剪效果
5. **导出**：点击"导出 JPG"保存印刷级图片

### 水池设计器（参数化模式）

1. 选择形状模式（矩形嵌套 / L形挖角 / 椭圆挖孔）
2. 设置画布尺寸、DPI、外边距
3. 配置多层边框（颜色/厚度/素材填充）
4. 可选：设置边框文字、背景素材
5. 点击"生成预览"渲染
6. 菜单 → 文件 → 导出 JPG

### 水池设计器（草图识别模式）

1. 点击**「上传草图」**选择手绘草图PNG/JPG
2. 画布立即显示草图 → QThread后台异步解析 → 12阶段进度反馈
3. 解析完成：**红色矩形框显示识别数据**（外框/内挖/上下左右边距）
4. 识别数据自动回填至「内挖边距」面板 → 可手动微调
5. 点击「生成预览」渲染水池设计图 → 画布显示预览
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
- 内孔边框使用 **scipy EDT 距离变换** 保证精确10px黑色边线宽度，防止对角延伸毛刺

### 安全机制

- `Image.MAX_IMAGE_PIXELS = 200_000_000`（2 亿像素上限，防御解压缩炸弹）
- 圆角半径限制：`min(radius, min(w, h) // 2)`，防止中心区域被着色
- 边框厚度硬上限：单层 ≤ 2cm，总厚度 ≤ 3cm（防止内容区/花纹被误判为边框）
- 边框层数硬限制：最多 4 层 + 薄边框跳变检测 + 花纹周期截断
- OCR数值范围严格校验：0.3-500cm，过滤异常值
- OCR全局唯一性检查：差值<0.15跳过，防止单值占据多字段

### 一致性保证

圆角处理逻辑在三个入口保持完全一致：
- `geometry.py`（设计器渲染）
- `image_cropper.py`（裁剪服务）
- `process_image.py`（命令行脚本）

统一委托给 `core.corner.algorithm.carve_corner_on_mask`，单一来源。

水池设计器双路径等价：
- 经典管道（OCR→几何→自洽）与 `_geometry_driven_parse`（几何驱动路径）同步增强多Token合并/50%上限/优先级

---

## 开发指南

### 调试

```bash
# 启用 DEBUG 日志
set LOG_LEVEL=DEBUG
python main.py

# 查看日志
# logs/smartshapecrop.log
# logs/*.png （草图识别OCR诊断截图：外框/内框/上下左右边距间隙）
```

### 测试

测试位于 `tests/` 目录，使用 pytest 框架（当前 **146 项通过 / 5 项跳过 / 共 151 项**，较 8.15 版本新增 test_fix_validation / test_parse_sketch_characterization / test_sketch_input_validation / test_sketch_parser_logic 共4个测试文件、约34个用例）：

```bash
# 全部测试
python -m pytest tests/ -v

# 特定测试类
python -m pytest tests/test_rounded_corner.py::TestApplyRoundedCorners -v

# 草图相关测试
python -m pytest tests/test_sketch_parser_logic.py -v
```

`scripts/diagnose/` 目录下包含诊断脚本（`diagnose_*.py`、`debug_*.py`、`_diag_*.py`），用于特定案例的圆角缺陷诊断 / OCR识别问题 / 草图解析根因定位。

### 添加新案例

1. 将源图放入 `psd_demo/` 或指定目录
2. 按命名规则命名目标文件（见上方文件名解析示例）
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

## 程序检测报告（2026-08-20）

### 语法与导入检测

| 项目 | 结果 |
|---|---|
| 核心17文件语法检查（AST parse） | ✅ 全部通过（main.py, process_image.py, config.py, geometry.py, image_cropper.py, image_ops.py, log_setup.py, corner×3, parser×2, psd/loader, pool_designer/sketch_parser, gui×3） |
| 核心模块导入检查（import×12） | ✅ 全部正常（config/geometry/image_cropper/image_ops/log_setup/corner×3/parser×2/psd/sketch_parser） |
| sketch_parser.parse_sketch 入口函数 | ✅ 存在 |

### 功能验证

| 项目 | 结果 |
|---|---|
| 单元测试套件 | ✅ **146 passed / 5 skipped / 共 151 项**（149项测试耗时约5分钟） |
| 圆角裁剪算法 | ✅ 单步扇形切割，四角角度映射正确（TL/TR/BL/BR） |
| 多层边框检测 | ✅ 双算法并行（颜色距离15 + 亮度突变25）+ 4层硬上限+跳变检测 |
| 圆角重绘清理遍历 | ✅ 三层清理（间隙像素/外弧区域/深度超限）解决花幔/蔓生花/塞纳时光/青芜漫野/路易花坊多轮专项 |
| 内层花纹双mask保护 | ✅ 半径≤2×总边框厚度时仅切边框条带并集，边框重绘mask独立防变薄 |
| 文件名解析 | ✅ 6 层容错策略，全角/特殊字符兼容 |
| 模板匹配 | ✅ 形状 + 方向关键词严格匹配（弧形/圆形/矩形 + 横版/竖版） |
| PSD 分层读取 | ✅ 自动裁剪透明边 + 合成 RGB |
| GUI 后台线程 | ✅ 大图裁剪/导出不阻塞主界面；草图解析QThread异步+12阶段进度反馈 |
| **水池设计器草图识别6步管道** | ✅ 简化重构完成，代码精简37.6%；去除暴力搜索/黄金注入反模式 |
| 草图全局OCR+位置映射 | ✅ 去除白名单/正则后置/£6#53支持；修复PILImage缺失NameError；边界伪矩形过滤 |
| 相邻数字Token合并 | ✅ "1"+"2"→12、"7"+".5"→7.5；正则支持小数开头 |
| 方向标签三重门合并 | ✅ 百位数/数量级/50%差异，解决53→453类误覆盖；纵横比对齐解决内框颠倒 |
| 内框可靠性评级+过滤 | ✅ 面积<5%/纵横比>15判unreliable；几何值≤50%外框边上限 |
| OCR边距自洽检测 | ✅ 内推内框10-90%范围优先OCR |
| 嵌套矩形3-97%面积过滤 | ✅ 拦截0.68%极小伪内框（234×60/133×60.5案例） |
| 几何一致性5%强制修正 | ✅ inner=outer-margin_sum偏差>5%覆盖 |
| GUI草图识别状态显示 | ✅ _on_sketch_parsed红色框显示8字段；_on_pool_finished_ok预览加载+兜底；debug存储direction_margins |
| 几何驱动路径同步增强 | ✅ _geometry_driven_parse具备同等Token合并/50%拦截/优先级 |
| 内孔边框EDT距离变换 | ✅ 10px精确宽度无对角毛刺 |
| 画布尺寸交换（水池设计竖横方向） | ✅ file_w=parsed.height_cm, file_h=parsed.width_cm正确映射 |
| 边距输入字段上限 | ✅ QDoubleSpinBox 200cm（兼容OCR大值） |

### 代码质量检测

| 类别 | 状态 | 说明 |
|---|---|---|
| 单元测试覆盖 | ✅ 良好 | 从8.15的117项扩展到151项，新增4个草图相关测试文件34+用例 |
| 异常处理 | ⚠️ 低风险 | 核心代码带日志记录或用户反馈，GUI崩溃统一写入crash.log |
| 资源管理 | ⚠️ 中风险 | 核心代码存在少量 `Image.open()` 未使用上下文管理器，超大图（>5000px）建议排查 |
| 类型注解 | ✅ 良好 | 核心模块函数均带类型标注（`__future__ annotations`） |
| 日志配置 | ✅ 良好 | 统一 `log_setup.py`，滚动文件 + 控制台双输出，默认INFO级别 |
| 配置集中 | ✅ 良好 | 常量统一在 `core/config.py`，阈值/黄金值/硬上限单源管理 |
| 打包支持 | ✅ 良好 | main.py PyInstaller资源路径处理 + crash.log全局钩子 + .spec文件齐备 |

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
| 草图识别暴力搜索/黄金注入导致OCR结果扭曲 | 6步管道化重构，反模式移除 | `sketch_parser.py::parse_sketch` |
| OCR字符白名单导致£6/#53丢失+PIL导入缺失静默失败 | 无白名单正则后置提取+显式PIL导入 | `sketch_parser.py::_find_and_read_numbers` |
| 方向标签右距53→453无条件覆盖正确OCR值 | 合理性三重门+50%差异阈值+内框纵横比对齐 | `sketch_parser.py::_merge_direction_labels` |
| 嵌套矩形0.68%极小伪内框导致234×60全字段错误 | 面积3-97%过滤+4掩码+几何一致性5%强制修正 | `sketch_parser.py::_filter_nested_rectangles` |
| 内框不可靠时几何值87.3覆盖OCR正确值36.5 | 内框评级+50%边上限+OCR边距反推降级 | `sketch_parser.py::_rate_inner_rect_reliability` |
| 左距2/右距47.5（应为12/7.5）拆分识别 | 相邻Token合并+50%拦截+交叉验证聚焦重扫 | `sketch_parser.py::_merge_adjacent_number_tokens` |
| GUI识别状态+预览在修改后消失 | _on_sketch_parsed红框显示+_on_pool_finished_ok兜底+debug存储 | `gui/property_panel.py::_on_sketch_parsed` |
| 几何驱动路径重解析值不变 | _geometry_driven_parse同步增强+__pycache__清除提示 | `sketch_parser.py::_geometry_driven_parse` |
| 圆角裁剪多余米色弧线/白色方块（青芜漫野/蔓生花/路易花坊） | 三层清理遍历+内层色保护+边框带并集 | `sector_render.py` 三层L460-L548 |

### 2026-08-18 ~ 2026-08-19 变更记录（两日迭代总结）

#### 圆角裁剪工具（8.18 连续三轮专项修复）
修改文件：[core/image_cropper.py](file:///d:/SmartShapeCrop/core/image_cropper.py) + [core/corner/sector_render.py](file:///d:/SmartShapeCrop/core/corner/sector_render.py)

| 变更 | 说明 |
|---|---|
| 基础4项修复（弧线/缺口/直角/粗细） | 最外层边框厚度限定+OUTER_BAND=3px±2px边界；间隙层原间隙色填充+相邻层颜色对比防误判 |
| 第一轮3模板（花幔/蔓生花/塞纳时光） | 角度条件修正L829-L834；三层清理遍历L460-L548（间隙像素/外弧区域/深度超限） |
| 第二轮3模板（青芜漫野/蔓生花/路易花坊） | sector_render L369-372/380/405/474-485/778-784 内层色保护+多段顺序；image_cropper L117/199-201/385-388/1276-1279 白区裁剪+顺序 |

#### 水池设计器（8.18~8.19 8大类19子问题）
修改文件：[core/pool_designer/sketch_parser.py](file:///d:/SmartShapeCrop/core/pool_designer/sketch_parser.py) + [gui/property_panel.py](file:///d:/SmartShapeCrop/gui/property_panel.py) + [core/config.py](file:///d:/SmartShapeCrop/core/config.py)

| 日期 | 类别 | 关键变更 |
|---|---|---|
| 08-18 | 识别流程简化重构 | 移除T0暴力枚举/黄金注入/多层状态机；6步管道化；代码-37.6% |
| 08-18 | 数值识别与方向标签 | 实际间隙ROI替代固定比例；边距置信度优先；标签差异50%阈值；可疑值回退None |
| 08-19 | 方向标签合并+内框修复 | 合理性三重门；内框纵横比对齐；边距联合几何重算（57×42颠倒/右距53→453案例） |
| 08-19 | 矩形检测+几何修正 | 面积3-97%过滤；OCR区域>80%集中警告；4掩码策略；几何一致性>5%强制覆盖；内框暗色区回退 |
| 08-19 | 全局OCR+位置映射 | 去白名单正则后置；PIL导入修复；全图扫描+物理位置映射；边界伪矩形剔除 |
| 08-19 | GUI状态+预览修复 | _on_sketch_parsed红框显示8字段；_on_pool_finished_ok预览+兜底；debug存direction_margins |
| 08-19 | 可靠性检测+几何过滤 | 面积<5%/纵横比>15判unreliable；50%边上限拦截；不可靠降级OCR边距反推 |
| 08-19 | OCR自洽+Token合并 | 10-90%优先OCR；相邻数字合并(12/7.5)；50%+交叉验证；_geometry_driven_parse同步 |

---

## ProductSummary 目录文档索引

水池设计器子问题文档（8.18-8.19）均位于 `ProductSummary/水池设计器/`，命名格式「日期-解决同类问题」。分类整理汇总位于 `ProductSummary/20260818-19-任务分类整理总结.md`。

---

## 许可证

本项目为内部工具，未公开许可。
