# -*- coding: utf-8 -*-
"""测试 _safe_dir_val2 和 direction_margins 的数据格式"""
import sys
sys.path.insert(0, '.')
from core.pool_designer.sketch_parser import parse_sketch

paths = [
    ('图1', 'scripts/diagnose/_test_6sketch_1.png'),
    ('图3', 'scripts/diagnose/_test_6sketch_3.png'),
    ('图6', 'scripts/diagnose/_test_6sketch_6.png'),
]

for name, path in paths:
    r = parse_sketch(path)
    print(f"\n=== {name} ===")
    print(f"success={r.success}")
    if hasattr(r, 'debug') and r.debug:
        print(f"debug keys: {list(r.debug.keys())}")
        dm = r.debug.get('direction_margins', {})
        print(f"direction_margins: {dm}")
        for k, v in dm.items():
            print(f"  {k}: type={type(v).__name__}, value={v}, v[0] type={type(v[0]).__name__ if isinstance(v, (tuple, list)) else 'N/A'}")

# 模拟 GUI 中的 _safe_dir_val2
def _safe_dir_val2(raw):
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, (tuple, list)) and len(raw) > 0:
        v = raw[0]
        if isinstance(v, (int, float)):
            return float(v)
    return 0.0

print("\n=== 模拟 GUI 行为 ===")
for name, path in paths:
    r = parse_sketch(path)
    if hasattr(r, 'debug') and r.debug:
        dir_vals = r.debug.get("direction_margins", {})
        if dir_vals:
            dir_mt = _safe_dir_val2(dir_vals.get("margin_top", 0))
            dir_mb = _safe_dir_val2(dir_vals.get("margin_bottom", 0))
            dir_ml = _safe_dir_val2(dir_vals.get("margin_left", 0))
            dir_mr = _safe_dir_val2(dir_vals.get("margin_right", 0))
            print(f"{name}: mt={dir_mt} ({type(dir_mt).__name__}), mb={dir_mb} ({type(dir_mb).__name__}), ml={dir_ml} ({type(dir_ml).__name__}), mr={dir_mr} ({type(dir_mr).__name__})")
            try:
                result = any(v > 0 for v in [dir_mt, dir_mb, dir_ml, dir_mr])
                print(f"  any(v>0) = {result}")
            except Exception as e:
                print(f"  ERROR: {e}")
