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
"""
collect_ignore = [
    "test_corner_analysis_simple.py",
    "test_diagnose.py",
    "test_gap_detail_analysis.py",
    "test_sketch_fix.py",
    "test_verify.py",
]
