# 2026-09-05 T2：P0 失效断言修复与防复发机制

## 概述

用户要求：「修 17 个失效断言 → 移出 test_gui_sim.py 消除收集期副作用 → 实跑测试 → pytest.ini 加防复发规则」。全部 4 步完成，共 9 个文件改动 (+191 / -111)。

***

## 步骤 1：17 处失效断言全部清理

### 真正假通过的（6 处）：`return X` → `assert X, "..."`

| 文件 | 处数 | 内容 |
|---|---|---|
| `tests/border/test_complex_pattern_safety.py` | 3 | 圆角间隙、多层混合色、极端颜色抗锯齿 |
| `tests/border/test_gap_fix_verification.py` | 2 | 其中 1 处是"观察型"——`test_small_radius_preserves_content` 函数体内本就 0 assert，保留观察模式但不返回 True |
| `tests/integration/test_final_verification.py` | 4 | 边框圆角、间隙填充、内部花纹、功能兼容性 |
| `tests/integration/test_fix_validation.py` | 3 | name_parser、方向修正、边距校验 |

### return 多余的（11 处）：函数体内已有真实 assert，末尾 `return True` 冗余，直接删除

| 文件 | 处数 |
|---|---|
| `tests/border/test_user_reported_cases.py` | 4（CASE1-CASE4 玛利亚玫瑰/复古花丛/婉卉/闲叙） |

### main() 消费返回值的

将 `results.append((label, func()))` 改为 `func()`，失败由 assert 抛出自然中断。

***

## 步骤 2：test_gui_sim.py 移出 tests/

- 用 `git mv tests/gui/test_gui_sim.py scripts/diagnose/_gui_sim_diag.py`（保留历史）
- 改为下划线前缀后，pytest 默认不收集
- 消除 `sys.path.insert(0, '.')`，改为用 `Path(__file__).parents[2]` 定位项目根
- 头部加 docstring 说明移出原因与运行方式：`python scripts/diagnose/_gui_sim_diag.py`

***

## 步骤 3：实跑测试——意外收获

暴露出 **7 个被 return 掩盖的真实回归**：

| # | 测试 | 现象 |
|---|---|---|
| 1 | test_complex_pattern_with_gaps | 间隙区域 241 个错误黑色填充 |
| 2 | test_multilayer_border_with_mixed_colors | tr 与 br 间隙保持白色失败 |
| 3 | test_extreme_colors_and_anti_aliasing | 右下角间隙白色失败 + 133 异常像素 |
| 4 | test_corner_smoothness_no_gap_fill | 80% 缺口率 |
| 5 | test_border_smoothness | 80% 缺口率 |
| 6 | test_no_wrong_gap_fill | 56.9% 黑色占比 |
| 7 | test_name_parser | 3 个边界 case 误判长宽方向 |

这些正是"return True/False → 假通过 → 历史修复实际上没真正生效"的回归。保留失败态，等用户决定下一步（详见 T3 文档）。

***

## 步骤 4：pytest.ini 防复发规则

- `filterwarnings = error::pytest.PytestReturnNotNoneWarning` —— 任何测试函数再用 return 立即整套挂掉

**踩坑**：`collect_ignore` / `collect_ignore_glob` 在 pytest 9.x 的 pytest.ini 里被报 `PytestConfigWarning: Unknown config option`，这两个选项必须在 conftest.py 里以模块级变量赋值。

改用：
- `tests/conftest.py` 维护 `collect_ignore`（已存在，新增移除 test_gui_sim.py 条目）
- 项目根 `conftest.py` 加防御性 `collect_ignore_glob = ["scripts/**/test_*.py", ...]` 兜底（覆盖 21 个 scripts/_archive 下的历史归档 test_*.py）

***

## 收尾验证

- `pytest tests/ --collect-only`：304 用例全收，无任何 PytestConfigWarning
- `pytest tests/` 实跑：7 failed / 292 passed / 5 skipped（exit=0，55s）—— 失败的 7 个即步骤 3 列出的真实回归
