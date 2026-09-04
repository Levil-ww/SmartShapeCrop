# packaging 目录说明

**唯一打包入口：`packageV2.1.2.py`**（当前 V2.1.2 生产打包脚本）。

其余文件均为历史归档，请勿使用：

| 文件 | 说明 |
|---|---|
| `package.py` | 初版打包脚本（历史归档） |
| `packageV2.0.py` | V2.0 打包脚本（历史归档） |
| `packageV2.1.py` | V2.1 打包脚本（历史归档） |
| `build_exe.bat` | 旧版一键打包入口（历史归档） |
| `specs/` | PyInstaller spec 历史版本（V2.1 / V2.1.2 等） |

> 注意：历史归档脚本的 `PROJECT_ROOT` 基于旧目录结构（`__file__.parent` 指向项目根），
> 移动后未改逻辑。若误用旧脚本打包，`PROJECT_ROOT` 会指向 `packaging/` 而非项目根，导致失败。
> 请始终使用 `packageV2.1.2.py`（其 `PROJECT_ROOT` 已修正为 `.parent.parent`）。
