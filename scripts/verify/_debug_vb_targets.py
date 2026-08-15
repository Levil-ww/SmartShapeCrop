"""
测试：正/反向 target 下数值穷举分配结果对比
找到 _value_based_assignment 未能返回正确组合的原因
"""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

logging.basicConfig(level=logging.INFO, format='%(levelname).1s %(name)s: %(message)s')

from core.pool_designer.sketch_parser import (
    _score_assignment_consistency,
    _value_based_assignment,
)

ox, oy, ow, oh = 0, 0, 485, 332
ix, iy, iw, ih = 80, 60, 300, 220
outer_rect = (ox, oy, ow, oh)
inner_rect = (ix, iy, iw, ih)
ocr_hits = [
    (133.0,  240,  10,  0.95),
    (60.5,   10,   170, 0.95),
    (76.0,   240,  100, 0.90),
    (44.5,   240,  210, 0.90),
    (6.0,    240,  40,  0.90),
    (10.0,   240,  300, 0.90),
    (14.6,   40,   170, 0.90),
    (42.4,   450,  170, 0.90),
]

CORRECT = {
    'total_w': (133.0, 0), 'total_h': (60.5, 0),
    'inner_w': (76.0, 0),  'inner_h': (44.5, 0),
    'margin_top': (6.0, 0),   'margin_bottom': (10.0, 0),
    'margin_left': (14.6, 0), 'margin_right': (42.4, 0),
}
print("正确组合（草图标注）的 sc =", _score_assignment_consistency(CORRECT))
print("  几何：w_sum=133-76 =", 133-76, "; ml+mr = 14.6+42.4 =", 14.6+42.4)
print("       h_sum=60.5-44.5 =", 60.5-44.5, "; mt+mb = 6+10 =", 16)
print()

for label, tw, th in [
    ("反向 target（用户真实首次调用）", 60.5, 133),
    ("正向 target（修正后）",         133, 60.5),
]:
    print(f"=== {label}: target={tw}×{th} ===")
    res = _value_based_assignment(ocr_hits, outer_rect, inner_rect, tw, th)
    sc = _score_assignment_consistency(res)
    vals = {k: v[0] for k, v in res.items()}
    print(f"  sc = {sc:.3f}")
    print(f"  total = {vals['total_w']:.2f} × {vals['total_h']:.2f}")
    print(f"  inner = {vals['inner_w']:.2f} × {vals['inner_h']:.2f}")
    print(f"  margins = 上{vals['margin_top']:.2f} / 下{vals['margin_bottom']:.2f} / "
          f"左{vals['margin_left']:.2f} / 右{vals['margin_right']:.2f}")
    h = vals['total_w'] - vals['inner_w'] - (vals['margin_left'] + vals['margin_right'])
    v = vals['total_h'] - vals['inner_h'] - (vals['margin_top'] + vals['margin_bottom'])
    print(f"  几何残差：水平 |Δ|={abs(h):.2f}，垂直 |Δ|={abs(v):.2f}")
    # 比较是否与正确值方向一致（允许整体交换方向）
    ok_norm = (
        abs(vals['total_w']-133)<0.1 and abs(vals['total_h']-60.5)<0.1 and
        abs(vals['inner_w']-76)<0.1 and abs(vals['inner_h']-44.5)<0.1 and
        abs(vals['margin_top']-6)<0.1 and abs(vals['margin_bottom']-10)<0.1 and
        abs(vals['margin_left']-14.6)<0.1 and abs(vals['margin_right']-42.4)<0.1
    )
    ok_swap = (
        abs(vals['total_w']-60.5)<0.1 and abs(vals['total_h']-133)<0.1 and
        abs(vals['inner_w']-44.5)<0.1 and abs(vals['inner_h']-76)<0.1 and
        abs(vals['margin_top']-14.6)<0.1 and abs(vals['margin_bottom']-42.4)<0.1 and
        abs(vals['margin_left']-6)<0.1 and abs(vals['margin_right']-10)<0.1
    )
    print(f"  等于草图标注（正方向）: {ok_norm}，（全交换反方向）: {ok_swap}")
    print()
