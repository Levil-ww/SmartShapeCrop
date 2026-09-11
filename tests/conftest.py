"""
tests/conftest.py
收集忽略列表：把混入 tests/ 目录的"诊断/调试脚本"排除出 pytest 收集。

背景（2026-08-26）：
- test_gui_sim.py 等脚本在模块顶层直接调用 parse_sketch 等重逻辑，
  导入即执行，会导致 pytest 收集阶段卡死/超时，进而使整个测试套件无法全绿。
- 这些脚本没有 def test_ / class Test，本就不该被当作测试用例收集。
- 用 collect_ignore 排除后，它们仍可用 `python tests/xxx.py` 直接运行，
  同时满足"测试套件不收集、不导入诊断脚本"的隔离要求。

2026-09-05 更新：
- test_gui_sim.py 已通过 git mv 移至 scripts/diagnose/_gui_sim_diag.py
  （双保险：1. 文件不在 tests/ 下；2. 下划线前缀不匹配 test_*.py 规则），
  故从本忽略列表中移除。

2026-09-11 更新：
- 剩余 5 个脚本已全部通过 git mv 移至 scripts/diagnose/，本列表清空：
    tests/border/test_gap_detail_analysis.py  -> scripts/diagnose/_diag_border_gap_detail.py
    tests/core/test_corner_analysis_simple.py -> scripts/diagnose/_diag_corner_analysis_simple.py
    tests/sketch/test_diagnose.py             -> scripts/diagnose/_diag_sketch_recognition.py
    tests/sketch/test_sketch_fix.py           -> scripts/diagnose/_diag_sketch_fix.py
    tests/sketch/test_verify.py               -> scripts/diagnose/_diag_sketch_verify.py
- 列表保留为空列表（而非删除本文件）：若将来再有诊断脚本混入 tests/，
  在此追加文件名即可立即生效，无需重新查证该机制。
"""
collect_ignore = []
