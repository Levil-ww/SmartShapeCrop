# packaging 目录说明

**唯一打包入口：`packageV2.2.2.py`**（当前 V2.2.2 生产打包脚本，位于 `packaging/` 根目录）。
配套 spec：`packaging/specs/智能裁剪设计器V2.2.2.spec`。

```bash
python packaging/packageV2.2.2.py                 # 单文件模式（默认，内嵌 Tesseract）
python packaging/packageV2.2.2.py --onedir        # 目录模式（更稳定）
python packaging/packageV2.2.2.py --debug         # 调试模式（显示控制台）
python packaging/packageV2.2.2.py --clean         # 清理旧构建后打包
python packaging/packageV2.2.2.py --no-tesseract  # 不内嵌 Tesseract
```

产物：`dist/智能裁剪设计器V2.2.2.exe`（单文件，约 202 MB，内嵌 Tesseract-OCR 约 115 MB）。

## 目录组织

| 路径 | 说明 |
|---|---|
| `packageV2.2.2.py` | **当前打包入口**（V2.2.2，唯一在用） |
| `legacy/` | 历史打包脚本归档（V2.0 / V2.1 / V2.1.2 / V2.2 / V2.2.1 等），请勿使用 |
| `specs/` | PyInstaller spec 归档（`SmartShapeCrop` / V2.1 / V2.1.2 / V2.2 / V2.2.2） |

### legacy/ 归档清单

| 文件 | 说明 |
|---|---|
| `package.py` | 初版打包脚本（历史归档） |
| `packageV2.0.py` | V2.0 打包脚本（历史归档） |
| `packageV2.1.py` | V2.1 打包脚本（历史归档） |
| `packageV2.1.2.py` | V2.1.2 打包脚本（历史归档） |
| `packageV2.2.py` | V2.2 打包脚本（历史归档，2026-09-16 移入） |
| `packageV2.2.1.py` | V2.2.1 打包脚本（历史归档，2026-09-16 移入） |
| `build_exe.bat` | 旧版一键打包入口（历史归档） |

> ⚠️ **注意**：历史归档脚本的 `PROJECT_ROOT` 基于旧目录结构（`__file__.parent` 指向项目根）。
> 移入 `legacy/` 后**未改逻辑**，若误用旧脚本打包，`PROJECT_ROOT` 会指向 `packaging/legacy/`
> 而非项目根，导致打包失败。**请始终使用 `packageV2.2.2.py`**（其 `PROJECT_ROOT` 为 `.parent.parent`）。

## 维护约定

- **新版发布时**：新建 `packageV<新版本>.py`（复制上一版改 `APP_NAME` 与 `HIDDEN_IMPORTS`），
  旧版脚本移入 `legacy/`，自动生成的 `智能裁剪设计器V<版本>.spec` 从项目根移入 `specs/`。
- **hidden-import 有「同名双包」陷阱**：`core/psd/`、`core/parser/`、`core/pool_designer/`
  均为**目录占位 shim**（只有 `__init__.py`），真实实现已迁至 `services/`，由 `core.compat`
  用 `sys.modules` 别名重定向。因此**不要**声明 `core.psd.loader` 之类的子路径
  （PyInstaller 查不到文件会报 `Hidden import not found`），应声明 `services.psd.loader`。
- **出包后校验时效**：确认 `dist/*.exe` 时间戳 ≥ 最新源码（`core`/`services`/`gui`/`workers`/`models`/`main.py`）。
