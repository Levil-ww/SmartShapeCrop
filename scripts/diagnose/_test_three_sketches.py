"""单张草图快速测试"""
import sys, os, logging, time
sys.path.insert(0, '.')

logger = logging.getLogger('core.pool_designer.sketch_parser')
logger.setLevel(logging.WARNING)  # Reduce noise

from core.pool_designer.sketch_parser import parse_sketch

tests = [
    ('图1', 'scripts/diagnose/_test_sketch1.png', 120.0, 58.0,
     120.0, 58.0, 57.0, 42.0, 6.0, 10.0, 10.0, 53.0),
    ('图2', 'scripts/diagnose/_test_sketch2.png', 234.0, 60.0,
     234.0, 60.0, 86.0, 45.0, 6.0, 9.0, 36.0, 112.0),
    ('图3', 'scripts/diagnose/_test_sketch3.png', 234.0, 60.0,
     234.0, 60.0, 86.0, 45.0, 6.0, 9.0, 36.0, 112.0),
]

for name, path, tw, th, exp_tw, exp_th, exp_iw, exp_ih, exp_mt, exp_mb, exp_ml, exp_mr in tests:
    print(f"\n{'='*60}")
    print(f"测试 {name}: {path}")
    print(f"{'='*60}")
    
    t0 = time.time()
    result = parse_sketch(path, target_outer_w_cm=tw, target_outer_h_cm=th)
    elapsed = time.time() - t0
    
    ok_tw = abs(result.outer_w_cm - exp_tw) < 1.5
    ok_th = abs(result.outer_h_cm - exp_th) < 1.5
    ok_iw = abs(result.inner_w_cm - exp_iw) < 2.0
    ok_ih = abs(result.inner_h_cm - exp_ih) < 2.0
    ok_mt = abs(result.margin_top_cm - exp_mt) < 1.5
    ok_mb = abs(result.margin_bottom_cm - exp_mb) < 1.5
    ok_ml = abs(result.margin_left_cm - exp_ml) < 2.0
    ok_mr = abs(result.margin_right_cm - exp_mr) < 2.0
    
    all_ok = all([ok_tw, ok_th, ok_iw, ok_ih, ok_mt, ok_mb, ok_ml, ok_mr])
    
    print(f"  耗时: {elapsed:.1f}s")
    print(f"  识别结果:")
    print(f"    外框: {result.outer_w_cm:.1f} x {result.outer_h_cm:.1f} cm")
    print(f"    内挖: {result.inner_w_cm:.1f} x {result.inner_h_cm:.1f} cm")
    print(f"    边距: 上{result.margin_top_cm:.1f}/下{result.margin_bottom_cm:.1f}/左{result.margin_left_cm:.1f}/右{result.margin_right_cm:.1f} cm")
    print(f"  期望值:")
    print(f"    外框: {exp_tw:.1f} x {exp_th:.1f} cm")
    print(f"    内挖: {exp_iw:.1f} x {exp_ih:.1f} cm")
    print(f"    边距: 上{exp_mt:.1f}/下{exp_mb:.1f}/左{exp_ml:.1f}/右{exp_mr:.1f} cm")
    
    print(f"  对比:")
    print(f"    外框宽: {result.outer_w_cm:.1f} vs {exp_tw:.1f} {'OK' if ok_tw else 'FAIL'}")
    print(f"    外框高: {result.outer_h_cm:.1f} vs {exp_th:.1f} {'OK' if ok_th else 'FAIL'}")
    print(f"    内挖宽: {result.inner_w_cm:.1f} vs {exp_iw:.1f} {'OK' if ok_iw else 'FAIL'}")
    print(f"    内挖高: {result.inner_h_cm:.1f} vs {exp_ih:.1f} {'OK' if ok_ih else 'FAIL'}")
    print(f"    上边距: {result.margin_top_cm:.1f} vs {exp_mt:.1f} {'OK' if ok_mt else 'FAIL'}")
    print(f"    下边距: {result.margin_bottom_cm:.1f} vs {exp_mb:.1f} {'OK' if ok_mb else 'FAIL'}")
    print(f"    左边距: {result.margin_left_cm:.1f} vs {exp_ml:.1f} {'OK' if ok_ml else 'FAIL'}")
    print(f"    右边距: {result.margin_right_cm:.1f} vs {exp_mr:.1f} {'OK' if ok_mr else 'FAIL'}")
    
    status = "ALL CORRECT" if all_ok else "HAS DEVIATIONS"
    print(f"  总结: {status}")
