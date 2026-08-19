"""全面诊断草图识别 - 模拟真实GUI运行环境"""
import sys
sys.path.insert(0, '.')
import logging

# 设置详细日志
logging.basicConfig(level=logging.INFO, 
                    format='%(levelname)s %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])

import core.pool_designer.sketch_parser as sp
# 强制清除所有缓存
sp._SKETCH_CACHE.clear()
sp._SKETCH_CONSISTENT_CACHE.clear()

from core.pool_designer.sketch_parser import parse_sketch

def diagnose_sketch(sketch_path, target_w, target_h, expected, label):
    """诊断草图识别"""
    print("=" * 80)
    print(f"测试草图: {label}")
    print(f"文件路径: {sketch_path}")
    print(f"目标尺寸: {target_w} x {target_h} cm")
    print(f"期望值: 外框{expected['outer_w']}x{expected['outer_h']}, 内挖{expected['inner_w']}x{expected['inner_h']}")
    print(f"        上{expected['top']}/下{expected['bottom']}/左{expected['left']}/右{expected['right']}")
    print("=" * 80)
    
    # 清除缓存
    sp._SKETCH_CACHE.clear()
    sp._SKETCH_CONSISTENT_CACHE.clear()
    
    try:
        result = parse_sketch(
            sketch_path,
            target_outer_w_cm=target_w,
            target_outer_h_cm=target_h,
        )
    except Exception as e:
        print(f"\n❌ 解析异常: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # 真实识别值
    actual = {
        'outer_w': result.outer_w_cm,
        'outer_h': result.outer_h_cm,
        'inner_w': result.inner_w_cm,
        'inner_h': result.inner_h_cm,
        'top': result.margin_top_cm,
        'bottom': result.margin_bottom_cm,
        'left': result.margin_left_cm,
        'right': result.margin_right_cm,
    }
    
    # GUI显示值（水池模式 +1cm偏移）
    TRIM_UI = 1.0
    gui_display = {
        'top': actual['top'] + TRIM_UI,
        'bottom': actual['bottom'] + TRIM_UI,
        'left': actual['left'] + TRIM_UI,
        'right': actual['right'] + TRIM_UI,
    }
    
    print("\n📊 识别结果:")
    print(f"  成功: {result.success}")
    print(f"  消息: {result.message}")
    print(f"  方法: {result.method}")
    
    print(f"\n📐 真实识别值 (cm):")
    print(f"  外框: {actual['outer_w']:.2f} x {actual['outer_h']:.2f}")
    print(f"  内框: {actual['inner_w']:.2f} x {actual['inner_h']:.2f}")
    print(f"  上: {actual['top']:.2f}")
    print(f"  下: {actual['bottom']:.2f}")
    print(f"  左: {actual['left']:.2f}")
    print(f"  右: {actual['right']:.2f}")
    
    print(f"\n🖥️  GUI显示值 (含+1cm偏移, 水池模式):")
    print(f"  上: {gui_display['top']:.2f}")
    print(f"  下: {gui_display['bottom']:.2f}")
    print(f"  左: {gui_display['left']:.2f}")
    print(f"  右: {gui_display['right']:.2f}")
    
    # 误差分析
    print(f"\n📈 误差分析 (真实值 vs 期望值):")
    tolerances = {
        'outer_w': 2, 'outer_h': 2,
        'inner_w': 3, 'inner_h': 3,
        'top': 2, 'bottom': 2, 'left': 3, 'right': 3
    }
    
    all_ok = True
    for key in expected:
        ok = abs(actual[key] - expected[key]) <= tolerances[key]
        if not ok:
            all_ok = False
        diff = actual[key] - expected[key]
        diff_pct = (diff / max(0.1, expected[key])) * 100
        status = "✅" if ok else "❌"
        print(f"  {status} {key:10s}: {actual[key]:8.2f} vs {expected[key]:6.1f} (tol={tolerances[key]:2d}, diff={diff:+7.2f}, {diff_pct:+6.1f}%)")
    
    # 几何一致性检查
    print(f"\n🔍 几何一致性检查:")
    h_check = actual['outer_w'] - actual['inner_w'] - actual['left'] - actual['right']
    v_check = actual['outer_h'] - actual['inner_h'] - actual['top'] - actual['bottom']
    h_ok = abs(h_check) <= max(2.0, actual['outer_w'] * 0.05)
    v_ok = abs(v_check) <= max(2.0, actual['outer_h'] * 0.05)
    print(f"  水平方向: 外框{actual['outer_w']:.2f} - 内框{actual['inner_w']:.2f} - 左{actual['left']:.2f} - 右{actual['right']:.2f} = {h_check:+.2f} {'✅' if h_ok else '❌'}")
    print(f"  垂直方向: 外框{actual['outer_h']:.2f} - 内框{actual['inner_h']:.2f} - 上{actual['top']:.2f} - 下{actual['bottom']:.2f} = {v_check:+.2f} {'✅' if v_ok else '❌'}")
    
    # Debug信息
    if result.debug:
        print(f"\n🔧 Debug信息:")
        if 'ocr_values' in result.debug:
            print(f"  OCR原始值: {result.debug['ocr_values']}")
        if 'geo_values' in result.debug:
            print(f"  几何回退值: {result.debug['geo_values']}")
    
    print(f"\n{'='*80}")
    print(f"总体结果: {'✅ 所有检查通过' if all_ok else '❌ 部分检查失败'}")
    print(f"{'='*80}\n")
    
    return all_ok

# 测试草图1
expected1 = {
    'outer_w': 120.0, 'outer_h': 58.0,
    'inner_w': 57.0, 'inner_h': 42.0,
    'top': 6.0, 'bottom': 10.0, 'left': 10.0, 'right': 53.0
}

ok1 = diagnose_sketch(
    'scripts/diagnose/_test_sketch1.png',
    120.0, 58.0,
    expected1,
    "草图1"
)

# 测试草图2
expected2 = {
    'outer_w': 234.0, 'outer_h': 60.0,
    'inner_w': 86.0, 'inner_h': 45.0,
    'top': 6.0, 'bottom': 9.0, 'left': 36.0, 'right': 112.0
}

ok2 = diagnose_sketch(
    'scripts/diagnose/_test_sketch2.png',
    234.0, 60.0,
    expected2,
    "草图2"
)


# 测试草图3-真实草图
expected_real = {
    'outer_w': 133.0, 'outer_h': 60.5,
    'inner_w': 76.0, 'inner_h': 44.5,
    'top': 6.0, 'bottom': 10.0, 'left': 14.6, 'right': 42.4
}

ok_real = diagnose_sketch(
    'C:/Users/Administrator/Desktop/1.png',
    133.0, 60.5,
    expected_real,
    "真实草图"
)

# 总结
print("\n" + "=" * 80)
print("📋 测试总结")
print("=" * 80)
print(f"  草图1: {'✅ 通过' if ok1 else '❌ 失败'}")
print(f"  草图2: {'✅ 通过' if ok2 else '❌ 失败'}")
print(f"\n  总体: {'✅ 所有草图通过' if (ok1 and ok2) else '❌ 部分草图失败'}")

# 如果用户有真实草图，可以在这里添加
expected_real = {...}
ok_real = diagnose_sketch(
    r'C:\Users\Administrator\Desktop\1.png',
    133.0, 60.5,
    expected_real,
    "真实草图"
)
