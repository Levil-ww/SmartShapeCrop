# scripts/ — 人工调试与诊断脚本目录

> **重要约定**：本目录 **不进 CI**，也不被 pytest 收集（`pytest.ini` 的 `testpaths = tests` 已锁定）。
> 所有脚本仅供开发人员**手动运行**，用于复现 Bug、验证修复、诊断图像问题。

## 目录结构

```
scripts/
├── README.md                    # 本文件（开发约定必读）
├── diagnose/                    # 案例诊断脚本（复现 Bug、生成诊断图）
│   ├── _diag_e2e_three_cases.py    # 墨上花开/花野/婉卉 三案例端到端合成诊断
│   ├── _diag_mask.py               # Mask 几何正确性诊断
│   ├── _diag_real.py               # 真实图像合成 + 圆角 pipeline 诊断
│   ├── _diag_root_cause.py         # 通用圆角问题根因分析
│   ├── _diagnose_corner_issues.py  # 四角独立问题综合诊断（输出 diag_*.png）
│   ├── _diagnose_huayang_5cm.py    # 花漾之约 38.5x186cm BL+BR 5cm 专项
│   ├── _diagnose_huayang_compat.py # 花漾之约兼容性/回归诊断
│   ├── _diagnose_multi_layer_border.py  # 多层边框检测/重绘专项
│   ├── _diagnose_sainashiguang_4cm.py   # 塞纳时光 78.5x128.5cm 4cm 专项
│   ├── _diagnose_thick_black_border.py  # 粗黑边框补绘专项
│   └── _diagnose_user_report.py         # 用户报告用例一键诊断（输出到 test_cropper_output/）
│
├── verify/                      # 代码自检/快速验证脚本（跑全部/部分修复后手动运行）
│   ├── _selfcheck_syntax.py        # 语法自检（core/gui/入口脚本 py_compile 通扫）
│   ├── _analyze_border.py          # 指定图片边框厚度/颜色分析
│   ├── _refactor_selfcheck.py      # 重构前后运行结果自检
│   ├── _verify_fix.py              # 修复验证 1
│   ├── _verify_fix2.py             # 修复验证 2
│   ├── _verify_full.py             # 全流程端到端验证
│   ├── _verify_mask.py             # Mask 像素级验证
│   └── _verify_mask_shear.py       # Mask 剪切策略验证
│
└── _archive/                     # 归档脚本（不运行，仅留痕，30 天清理窗口）
    ├── README.md                   # 归档政策 + 回滚说明
    ├── dev_explore/                # 开发期探索脚本（Test/_test_*，非正式 pytest）
    ├── old_tests/                  # 旧 Test/ 目录下的 test_*.py（已被 tests/ 正式单测替代）
    └── _trash/                     # 一次性临时脚本 + 根位置 pending_delete 归档
```

## 命名约定（强制）

| 命名形式 | 位置 | 生命周期 | 说明 |
|---|---|---|---|
| `_diag_<案例>_<细节>.py` | `diagnose/` | 长期保留 | 可复用的专项诊断脚本 |
| `_verify_<细节>.py` | `verify/` | 长期保留 | 可复用的验证/自检脚本 |
| `YYYYMMDD_diag_xxx.py` | `diagnose/` 或 `verify/` | **自动 30 天清理** | 带日期前缀的一次性脚本，到月清理日删除 |
| `_test_*.py`（非正式 pytest） | `_archive/dev_explore/` | 仅归档，30 天后删除 | 历史探索脚本，**不要再新增** |
| `test_*.py`（不含 pytest fixture） | `_archive/old_tests/` | 仅归档，30 天后删除 | 历史伪单测，**正式 pytest 必须写在 tests/ 下** |

## 新增脚本 SOP

1. **放到 diagnose/ 或 verify/，不要放根目录或 Test/。**
2. **顶部必须带 PROJECT_ROOT auto-inject 代码段**（复制已有脚本顶部即可），保证脚本无论从哪里运行都能正确导入 `core.*` 并定位 `psd_demo/`、`Test/output/`、`logs/`。
3. **如果是一次性脚本**，文件名必须加当天日期前缀 `YYYYMMDD_`，一眼可知新旧，方便清理。
4. **不要引用绝对路径 `D:\SmartShapeCrop\...`**，统一用 `_os.path.join(_D, "psd_demo", "xxx.jpg")` 这种形式。
5. **输出图片统一写到 `logs/` 子目录或 `Test/output/`**，不要写在 `psd_demo/` 素材库里。

## 怎么运行

```powershell
# 从项目根目录运行（推荐）
cd d:\SmartShapeCrop
python scripts\verify\_selfcheck_syntax.py
python scripts\diagnose\_diagnose_user_report.py

# 直接双击也能运行（scripts/ 下脚本已自动计算正确的 PROJECT_ROOT）
python d:\SmartShapeCrop\scripts\diagnose\_diagnose_huayang_5cm.py
```

## 清理与回滚

- **每月 1 号维护日**：清理 `scripts/_archive/_trash/`、带日期前缀且超过 30 天的脚本。
- **7 天观察窗口（2026-08-11 ~ 2026-08-18）**：本次清理前的根目录和 Test/ 散乱脚本都在：
  - `Test/_pending_delete_20260811/`（原 Test/*.py 49 个脚本）
  - `scripts/_archive/_trash/_pending_delete_root/`（原根目录 15 个 `_*.py` 脚本）
- **回滚方法**：把这两个目录中的脚本分别 Move 回原位置即可。
- **观察窗口结束确认无回归**：7 天后可安全物理删除两个 pending_delete 目录。
