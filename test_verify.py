import logging, sys
logging.basicConfig(level=logging.WARNING, stream=sys.stdout, format='%(message)s')
from core.pool_designer.sketch_parser import parse_sketch
import os

sketches = [
    ('图1', 'scripts/diagnose/_test_6sketch_1.png'),
    ('图2', 'scripts/diagnose/_test_6sketch_2.png'),
    ('图3', 'scripts/diagnose/_test_6sketch_3.png'),
    ('图4', 'scripts/diagnose/_test_6sketch_4.png'),
    ('图5', 'scripts/diagnose/_test_6sketch_5.png'),
    ('图6', 'scripts/diagnose/_test_6sketch_6.png'),
]

expected = {
    '图1': (150, 60, None, None, 10, 10, 43.5, 36.5),
    '图2': (120, 58, None, None, 6, 10, 10, 53),
    '图3': (234, 60, None, None, 6, 9, 36, 112),
    '图4': (133, 60.5, None, None, 6, 10, 14.6, 42.4),
    '图5': (78, 58, None, None, 9.5, 8, 12, 7.5),
    '图6': (400, 100, None, None, 25, 40, 24, 256),
}

all_ok = 0
for name, path in sketches:
    full = os.path.abspath(path)
    exp = expected.get(name)
    r = parse_sketch(full)
    sc = r.debug.get('self_consistency', 0)
    
    lhs_w = r.margin_left_cm + r.inner_w_cm + r.margin_right_cm
    lhs_h = r.margin_top_cm + r.inner_h_cm + r.margin_bottom_cm
    ok_w = abs(lhs_w - r.outer_w_cm) < max(1.0, r.outer_w_cm*0.03)
    ok_h = abs(lhs_h - r.outer_h_cm) < max(1.0, r.outer_h_cm*0.03)
    
    match_items = []
    if exp:
        ow_ok = abs(r.outer_w_cm - exp[0]) <= max(2.0, exp[0]*0.05)
        oh_ok = abs(r.outer_h_cm - exp[1]) <= max(2.0, exp[1]*0.05)
        if exp[4] is not None:
            mt_ok = abs(r.margin_top_cm - exp[4]) <= max(1.5, exp[4]*0.15)
            mb_ok = abs(r.margin_bottom_cm - exp[5]) <= max(1.5, exp[5]*0.15)
            ml_ok = abs(r.margin_left_cm - exp[6]) <= max(1.5, exp[6]*0.15)
            mr_ok = abs(r.margin_right_cm - exp[7]) <= max(1.5, exp[7]*0.15)
            match_items.append(('外宽', ow_ok))
            match_items.append(('外高', oh_ok))
            match_items.append(('上', mt_ok))
            match_items.append(('下', mb_ok))
            match_items.append(('左', ml_ok))
            match_items.append(('右', mr_ok))
        else:
            match_items.append(('外宽', ow_ok))
            match_items.append(('外高', oh_ok))
    
    all_match = all(v for _, v in match_items) if match_items else True
    if ok_w and ok_h and all_match:
        all_ok += 1
        icon = 'PASS'
    elif ok_w and ok_h:
        icon = 'PARTIAL'
    else:
        icon = 'FAIL'
    
    print(icon, name + ':')
    print('  外框: %.1fx%.1f (期望 %sx%s)' % (r.outer_w_cm, r.outer_h_cm, exp[0], exp[1]))
    print('  内挖: %.1fx%.1f' % (r.inner_w_cm, r.inner_h_cm))
    print('  边距: 上%.1f 下%.1f 左%.1f 右%.1f' % (r.margin_top_cm, r.margin_bottom_cm, r.margin_left_cm, r.margin_right_cm))
    gw = 'OK' if ok_w else 'NG'
    gh = 'OK' if ok_h else 'NG'
    print('  几何: 横%s 纵%s  sc=%.2f' % (gw, gh, sc))
    if match_items:
        detail = ' '.join([n + ('OK' if v else 'NG') for n, v in match_items])
        print('  实测对比: ' + detail)
    print()

print('===== 全通过率: %d/%d =====' % (all_ok, len(sketches)))