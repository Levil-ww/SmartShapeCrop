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
python -m pytest tests/ -q
```

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
python packaging/packageV2.2.2.py
```

## 当前状态

- 基线时间：2026-09-19，本地 Python 3.13.14，pytest 9.1.1。
- 测试命令：`$env:PYTHONDONTWRITEBYTECODE='1'; python -m pytest tests/ -q -p no:cacheprovider`。
- 普通权限下测试会因系统临时目录权限失败：`C:\Users\Administrator\AppData\Local\Temp\pytest-of-Administrator` 无法访问，表现为 `626 passed, 45 errors`。
- 提升权限重跑同一测试命令后：`671 passed in 109.14s`。
- 当前没有已确认的功能性测试失败；上述 45 个错误是环境/权限问题。
- 当前未发现项目级 lint 命令或配置，未运行 lint。
- 打包命令会写入 `build/`、`dist/` 等产物；在用户要求不改文件时不要运行。

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
- 不要使用 `packaging/legacy/` 下的旧打包脚本；当前打包入口是 `packaging/packageV2.2.2.py`。
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
