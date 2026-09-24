# packaging 目录说明

**唯一打包入口：`packageV2.2.3.py`**（当前 V2.2.3 生产打包脚本，位于 `packaging/` 根目录）。
配套 spec：`packaging/specs/智能裁剪设计器V2.2.3.spec`。

```bash
python packaging/packageV2.2.3.py                 # 单文件模式（默认，内嵌 Tesseract）
python packaging/packageV2.2.3.py --onedir        # 目录模式（更稳定）
python packaging/packageV2.2.3.py --debug         # 调试模式（显示控制台）
python packaging/packageV2.2.3.py --clean         # 清理旧构建后打包
python packaging/packageV2.2.3.py --no-tesseract  # 不内嵌 Tesseract
```

产物：`dist/智能裁剪设计器V2.2.3.exe`（单文件，约 202 MB，内嵌 Tesseract-OCR 约 115 MB）。

> **版本号不再硬编码在打包脚本里**：`packageV2.2.3.py` 从 `core/config.py` 的
> `APP_VERSION` / `APP_DISPLAY_NAME` 派生 exe 名与打包横幅（单一事实来源）。
> 发版只需改 `core/config.py` 一处，脚本不会滞后于源码。

## 目录组织

| 路径 | 说明 |
|---|---|
| `packageV2.2.3.py` | **当前打包入口**（V2.2.3，唯一在用；版本取自 `core.config.APP_VERSION`） |
| `packageV2.2.2.py` | 上一版入口（保留备查，不建议再用于出包 —— 其 exe 名硬编码为 V2.2.2） |
| `specs/` | PyInstaller spec 归档（`SmartShapeCrop` / V2.1 / V2.1.2 / V2.2 / V2.2.2 / **V2.2.3**） |
| `legacy/` | ⛔ **已于 2026-09-17 整目录删除**（原含 V2.0 / V2.1 / V2.1.2 / V2.2 / V2.2.1 旧脚本与 `build_exe.bat`） |

> ⚠️ **历史脚本的 `PROJECT_ROOT` 陷阱（留作警示）**：早期归档脚本的 `PROJECT_ROOT` 基于旧目录结构
> （`__file__.parent` 指向项目根），一旦被移入子目录就**会指向子目录而非项目根**，导致打包失败。
> 当前入口 `packageV2.2.3.py` 的 `PROJECT_ROOT` 为 `.parent.parent`，是正确的。

## 维护约定

- **新版发布时**：新建 `packageV<新版本>.py`（复制上一版，只改文档串与 `HIDDEN_IMPORTS`；
  版本号已不在此硬编码），并把 `core/config.py` 的 `APP_VERSION` 提升。旧版脚本如无保留
  价值可直接删除，或恢复 `legacy/` 归档；PyInstaller 在项目根生成的
  `智能裁剪设计器V<版本>.spec` 移入 `specs/`。
- **hidden-import 有「同名双包」陷阱**：`core/psd/`、`core/parser/`、`core/pool_designer/`
  均为**目录占位 shim**（只有 `__init__.py`），真实实现已迁至 `services/`，由 `core.compat`
  用 `sys.modules` 别名重定向。因此**不要**声明 `core.psd.loader` 之类的子路径
  （PyInstaller 查不到文件会报 `Hidden import not found`），应声明 `services.psd.loader`。
- **出包后校验时效**：确认 `dist/*.exe` 时间戳 ≥ 最新源码（`core`/`services`/`gui`/`workers`/`models`/`main.py`）。
