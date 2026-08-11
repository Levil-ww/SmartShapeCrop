# scripts/_archive/ — 归档政策

本目录存放**历史脚本**，仅作为留痕和回滚用途，**不建议运行**。清理时间一到会物理删除。

## 子目录说明

| 目录 | 内容 | 清理窗口 | 清理日期（初估） |
|---|---|---|---|
| `dev_explore/` | 2026-08 之前在 `Test/` 下的开发期探索脚本（`_test_*` 共 23 个）。不具 pytest 结构、依赖本地 JPG、非正式单测。 | 30 天 | 2026-09-10 |
| `old_tests/` | 2026-08 之前在 `Test/` 下的旧 `test_*.py`（16 个），已被 `tests/` 下正式 pytest 单测替代，保留用于对比。 | 30 天 | 2026-09-10 |
| `_trash/` | 2026-08 目录清理时确认的一次性脚本（`_tmp_*`、`_trace_*`）。 | 7 天 | 2026-08-18 |
| `_trash/_pending_delete_root/` | 2026-08-11 清理时从项目**根目录**移过来的 15 个 `_*.py`。如发现遗漏、运行失败要回滚，把文件 Move 回项目根目录即可。 | **观察窗口 7 天** | 2026-08-18（确认无回归后删） |

⚠️ `Test/_pending_delete_20260811/`（49 个旧脚本）同级存于 `Test/` 下而非此处，同样遵循 7 天观察窗口。

## 回滚命令（2026-08-18 之前可一键回滚）

```powershell
# 回滚根目录 15 个脚本
Move-Item d:\SmartShapeCrop\scripts\_archive\_trash\_pending_delete_root\*.py d:\SmartShapeCrop\

# 回滚 Test/ 目录 49 个脚本
Move-Item d:\SmartShapeCrop\Test\_pending_delete_20260811\*.py d:\SmartShapeCrop\Test\
```

## 清理检查清单（每月 1 号维护日执行）

- [ ] 物理删除 `_trash/` 中超过 7 天的临时脚本
- [ ] 物理删除 `dev_explore/` 中超过 30 天的归档（如无保留理由）
- [ ] 物理删除 `old_tests/` 中超过 30 天的归档（如无保留理由）
- [ ] 扫描 `diagnose/` + `verify/` 下带 `YYYYMMDD_` 前缀的脚本，凡 > 30 天的移到 `_trash/`
- [ ] 清理完成后运行 `python scripts/verify/_selfcheck_syntax.py`，确保无误
