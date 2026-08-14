"""Quick test of value-based assignment logic."""
import sys
sys.path.insert(0, '.')
from core.pool_designer.sketch_parser import (
    _score_assignment_consistency,
    _value_based_assignment,
    _enumerate_inner_margin_assignments,
    _validate_and_fix_margins
)

# 模拟 OCR 结果: 76.0, 42.4, 10.0
ocr_hits = [
    (76.0, 425.0, 285.3, 0.95),
    (42.4, 742.3, 322.7, 0.95),
    (10.0, 402.7, 508.7, 0.95)
]
outer_rect = (78, 118, 644, 448)
inner_rect = (244, 271, 363, 248)

# 先测试评分函数
test_assignments = [
    {'total_w': (133.0, 5), 'total_h': (60.5, 5), 'inner_w': (76.0, 8), 'inner_h': (42.4, 8), 'margin_top': (0.0, 0), 'margin_bottom': (10.0, 7), 'margin_left': (0.0, 0), 'margin_right': (0.0, 0)},
    {'total_w': (133.0, 5), 'total_h': (60.5, 5), 'inner_w': (76.0, 8), 'inner_h': (10.0, 8), 'margin_top': (0.0, 0), 'margin_bottom': (42.4, 7), 'margin_left': (0.0, 0), 'margin_right': (0.0, 0)},
    {'total_w': (133.0, 5), 'total_h': (60.5, 5), 'inner_w': (42.4, 8), 'inner_h': (76.0, 8), 'margin_top': (0.0, 0), 'margin_bottom': (10.0, 7), 'margin_left': (0.0, 0), 'margin_right': (0.0, 0)},
    {'total_w': (133.0, 5), 'total_h': (60.5, 5), 'inner_w': (76.0, 8), 'inner_h': (0.0, 0), 'margin_top': (0.0, 0), 'margin_bottom': (10.0, 7), 'margin_left': (0.0, 0), 'margin_right': (42.4, 7)},
]

print("=== Score test ===")
for i, a in enumerate(test_assignments):
    s = _score_assignment_consistency(a)
    iw = a['inner_w'][0]
    ih = a['inner_h'][0]
    mb = a['margin_bottom'][0]
    mr = a['margin_right'][0]
    print(f'Scheme {i+1}: score={s:.3f} - iw={iw}, ih={ih}, mb={mb}, mr={mr}')

print("\n=== Inner-margin enumeration ===")
sorted_hits = sorted([(76.0, 9), (42.4, 9), (10.0, 9)], key=lambda x: x[0], reverse=True)
candidates = _enumerate_inner_margin_assignments(sorted_hits, 133.0, 60.5)
print(f"Generated {len(candidates)} candidates")
for i, c in enumerate(candidates):
    s = _score_assignment_consistency(c)
    iw = c['inner_w'][0]
    ih = c['inner_h'][0]
    mt = c['margin_top'][0]
    mb = c['margin_bottom'][0]
    ml = c['margin_left'][0]
    mr = c['margin_right'][0]
    print(f'  Candidate {i+1}: score={s:.3f} iw={iw:.1f} ih={ih:.1f} mt={mt:.1f} mb={mb:.1f} ml={ml:.1f} mr={mr:.1f}')

# 最佳候选
best = max(candidates, key=_score_assignment_consistency)
print(f"\nBest candidate:")
for k, v in best.items():
    print(f"  {k}: {v[0]:.2f} (conf={v[1]})")

print("\n=== Full value-based assignment ===")
result = _value_based_assignment(ocr_hits, outer_rect, inner_rect, 133.0, 60.5)
for k, v in result.items():
    print(f"  {k}: {v[0]:.2f} (conf={v[1]})")
