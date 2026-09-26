# AGENTS.md

## 项目目标

SmartShapeCrop 是一个面向印刷/定制设计场景的 Python/PyQt5 桌面工具，用于圆角裁剪、水池/嵌套挖洞、草图 OCR、椭圆/多洞/L 形挖角生成与素材边框补全。

## 命令

- 安装依赖：

```powershell
python -m pip install -r requirements.txt
```

- 启动：

```powershell
python main.py
```

- 命令行示例：

```powershell
python process_image.py
```

- 测试：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:CODEBUDDY_SAFE_DELETE_ENABLED='0'
python -m pytest tests/ -q -p no:cacheprovider --basetemp=.pytest_tmp
```

  `CODEBUDDY_SAFE_DELETE_ENABLED=0` 用于规避宿主 `sitecustomize.py` 的批量删除护栏
  （按单轮工具调用累计删除数计，阈值 50，命中即 `SystemExit(1)` 并级联 pytest 内部错误，
  表现为大量 `errors` 的伪失败）。判据：**同一文件单独跑通过、全量报错 = 环境伪失败**。

- 按改动范围跑针对性测试（日常改动用这条，别每次都跑全量）：

  全量 820 条、耗时约 4 分钟；针对性跑通常几秒到几十秒。**先按下方映射选目录/文件，
  有疑虑再退回全量。**

  | 改动位置 | 建议跑的测试 | 用例数 |
  |---|---|---|
  | `core/geometry.py`、`core/image_ops.py`、`core/corner/**` | `tests/core/` + `tests/border/` | 438 + 10 |
  | `core/lshape_border*.py` | `tests/core/test_lshape_border*.py` | 59 |
  | `services/sketch_parser/**` | `tests/sketch_parser/` + `tests/sketch/` | 57 + 64 |
  | `services/psd/**`、`services/parser/**` | `tests/integration/` | 101 |
  | `models/design_model.py` | `tests/models/` + `tests/core/` | 27 + 438 |
  | `gui/**`、`workers/**` | `tests/gui/`（需 `QT_QPA_PLATFORM=offscreen`） | 119 |
  | 跨层/架构改动、改公共数据结构 | **全量 `tests/`** | 820 |

  用法：把表里的路径直接替换命令中的 `tests/`，例如

  ```powershell
  python -m pytest tests/core tests/border -q -p no:cacheprovider --basetemp=.pytest_tmp
  ```

  注意：`tests/core/` 体量最大（438 条），改 `core/` 公共逻辑时它才是主战场；
  只改单个功能模块时优先按文件名精确选取（如 `tests/core/test_lshape_border*.py`），
  比整个 `tests/core/` 快一个量级。

- GUI 测试：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest tests/gui/ -v
```

- Lint：

```text
当前未发现项目级 lint 命令或配置；不要自行引入新 lint 工具。
```

- 打包：

```powershell
python packaging/packageV2.2.3.py
```

  当前打包入口是 `packaging/packageV2.2.3.py`（配 `packaging/specs/智能裁剪设计器V2.2.3.spec`）。
  `packaging/packageV2.2.2.py` 保留备查，**不要再用于出包**（其 `PROJECT_ROOT` 关系与版本号
  均指向 V2.2.2）。`packaging/legacy/` 已不存在，无需再避让。

## 当前状态

- 基线时间：2026-09-26，本地 Python 3.13.14，pytest 9.1.1，PyInstaller 6.22.3。
- 测试命令：日常按上文「按改动范围跑针对性测试」选目录；发版前或跨层改动跑全量。
- **当前基线：`820 passed`，0 failed / 0 error / 0 skipped**（2026-09-26 实跑，耗时 3m58s）。
  演进：501 → 671 → 680 → 704 → 732 → 805 → 813 → 818 → **820**。
- 不带 `--basetemp` 时临时目录落 `%TEMP%`，耗时约 225s 且更易受权限影响；两次数字**不可纵向比**。
- 当前没有已确认的功能性测试失败。
- 当前未发现项目级 lint 命令或配置，未运行 lint。
- 打包命令会写入 `build/`、`dist/` 等产物；在用户要求不改文件时不要运行。
- 最新交付物：`dist/智能裁剪设计器V2.2.3.exe`（218.4 MB，2026-09-26 09:45）。
  **时效铁律**：出包后必须确认 exe 时间戳 ≥ 最新源码时间戳。

## 代码风格

- 语言/框架：Python + PyQt5。
- 包管理：`requirements.txt` + `pip`。
- 测试框架：pytest，配置见 `pytest.ini`。
- 优先沿用现有架构边界：`core/` 放纯业务逻辑，`services/` 放外部能力封装，`gui/` 放 UI，`workers/` 放 QThread 调度，`models/` 放数据模型。
- `workers/` 不应导入 `gui/`。
- 测试必须使用 `assert`，不要用 `return <bool>` 代替断言。
- 保持兼容 shim，除非用户明确要求迁移旧导入路径。
- 不要硬编码本机绝对路径，例如 `D:\SmartShapeCrop\...`。
- 提交信息风格当前未发现强制规范；如需提交，先保持与仓库既有提交风格一致。

## 禁止

- 不要修改用户未要求的文件；改动前先看 `git status`，不要回退已有未提交改动。
- 不要安装 `python-qt5`，它会与 `PyQt5` 冲突并可能破坏 Windows DLL 加载。
- 不要把正式 pytest 测试放进 `scripts/`、`_archive/`、`packaging/` 或 `ProductSummary/`。
- 不要用 `packaging/packageV2.2.2.py` 出包；当前打包入口是 `packaging/packageV2.2.3.py`。
- 不要随意清理或改写 `logs/`、`build/`、`dist/` 生成物，除非任务明确涉及诊断、构建或打包。
- 不要改 `.venv/`、`.git/`、`.idea/`、`.pytest_cache/`、`.workbuddy/`。
- 涉及依赖安装、打包环境、OCR/Tesseract 路径、删除文件、数据库/持久化格式迁移、批量移动历史文档或更改测试基线时，先问用户。

## 主要目录

- `core/`: 几何、图像渲染、裁剪、圆角、配置、日志、L 形边框补全。
- `services/`: 文件名解析、模板匹配、草图/OCR 解析、PSD 加载。
- `gui/`: PyQt5 面板、对话框、画布和界面逻辑。
- `workers/`: 后台线程 worker。
- `models/`: 数据模型。
- `tests/`: pytest 测试。
- `scripts/`: 人工诊断和验证脚本，不进 CI。
- `packaging/`: PyInstaller 打包入口与 spec。
- `ProductSummary/`: 历史和产品文档。
