# 2026-09-04 T2：项目结构整理与卫生治理

## 概述

执行 8/31 §10.5 列的六项整理任务。实测 3 项早已完成，本次实际执行 3 项。全部用 `git mv`，核心源码 `core gui main.py process_image.py` **零改动**。

***

## 六项整理执行结果

| 项 | 实测结果 |
|---|---|
| ProductSummary 归档 | ✅ `20260903-任务分类整理总结.md`（28KB）已存在 |
| debug.log / stderr.log 移出库 | ✅ 全盘 find 均不存在（早已移走） |
| logs 自动回收 | ✅ `log_setup.py` 已用 `RotatingFileHandler`（5MB/3 份） |
| scripts `_archive` 压平 | ⚠️ 本次执行：消除 `old_tests/test_border_corner_output/` 第 3 层，2 jpg 提到 `old_tests/` |
| 日期总结归子目录 | ⚠️ 本次执行：7 md + 近两日总结 → `2026-08/`(7) + `2026-09/`(1) |
| packaging 集中 | ⚠️ 本次执行：四代打包脚本 + 3 spec + bat 集中到 `packaging/` |

***

## packaging 集中细节

- `packageV2.1.2.py`（活入口）→ `packaging/`，PROJECT_ROOT 改 `.parent.parent`（+1 层）
- 历史 `package.py` / `packageV2.0.py` / `packageV2.1.py` / `build_exe.bat` → `packaging/`
- 3 个失效/历史 spec → `packaging/specs/`
- 同步引用：`tests/integration/test_f5_f14_fixes.py:46` 路径 `package/packageV2.1.py` → `packaging/packageV2.1.py`；README 目录树

***

## 验证

- 全部用 `git mv`（17 R + 1 RM），历史保留
- PROJECT_ROOT 静态验证 + 实际解析均指向 `F:\SmartShapeCrop`，main.py/images 可寻
- 全量测试 299 passed / 5 skipped，无回归
- 旧脚本 `package.py` / `packageV2.0.py` 的 PROJECT_ROOT 保持原样（`__file__.parent`），它们是历史归档不再执行，未改其逻辑（遵守"整理不动逻辑"原则）

***

## 补充产出

1. **补 `packaging/README.md`**：说明唯一打包入口是 `packageV2.1.2.py`，其余 7 个文件均为历史归档，并标注旧脚本 PROJECT_ROOT 陷阱。
2. **新增程序当前状态分析报告** `ProductSummary/SmartShapeCrop分析报告/V2.1.2-程序当前状态分析报告.html`（V1.0，7 章节）——结论：止血已完成，进入固本期。
