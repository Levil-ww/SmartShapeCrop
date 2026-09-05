# -*- coding: utf-8 -*-
"""综合验证：画布尺寸 + 边距识别正确性"""
import sys, os, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.WARNING, format='%(name)s: %(message)s')

def test_name_parser():
    from core.parser.name_parser import parse_filename
    # —— 2026-09-05 校准说明 ——
    # 产品语义（当前代码逻辑，core/parser/name_parser.py:443 + 514-530）：
    #   - 通用模式：无关键词 → 横版（长边=宽、短边=高）
    #   - 显式"竖版"  → 短边=宽、长边=高
    #   - 水池模式与通用模式统一遵循"长边为宽"标准规则（f8beb2d 2026-08-26 起）
    #   - 下游 GUI（gui/property_panel_poolbox.py:222-225）明确：
    #     "58x121cm → width_cm=121, height_cm=58, raw_outer_w=121, raw_outer_h=58"
    #   - 即文件名尺寸字段"宽 x 高"的标注是用户的相对偏好，但程序层统一按长边归一化，
    #     避免草图识别/预览图生成的方向计算漂移。
    # 因此本测试期望值按"长边=宽、短边=高"统一标注：
    tests = [
        ('model_60.5x133CM水池', 133.0, 60.5, '横版'),     # 原期望 60.5x133(竖版)
        ('model_133x60.5CM水池', 133.0, 60.5, '横版'),     # 不变
        ('model_45x45CM水池',    45.0,  45.0,  '横版'),     # 正方形 → 横版
        ('model_100x50CM水池',   100.0, 50.0,  '横版'),     # 不变
        ('model_50x100CM水池',   100.0, 50.0,  '横版'),     # 原期望 50x100(竖版)
        ('model_100x200CM裁剪有图', 200.0, 100.0, '横版'),   # 原期望 100x200(竖版)
    ]
    all_pass = True
    for fname, exp_w, exp_h, exp_layout in tests:
        r = parse_filename(fname)
        w = round(r.width_cm, 1) if r.width_cm else 0
        h = round(r.height_cm, 1) if r.height_cm else 0
        layout = r.layout
        ok = (w == exp_w and h == exp_h and layout == exp_layout)
        status = '✅' if ok else '❌'
        if not ok:
            all_pass = False
        print(f'{status} {fname}: width={w}(exp={exp_w}), height={h}(exp={exp_h}), layout={layout}(exp={exp_layout})')
    print(f'\n=== name_parser: {"ALL PASS" if all_pass else "SOME FAILED"} ===\n')
    # 原为 `return all_pass`：pytest 不校验返回值，导致断言形同虚设（假通过）。
    assert all_pass, "name_parser 存在失败用例，详见上方逐条输出"

def test_direction_correction():
    """模拟方向矫正逻辑"""
    print("=== 方向矫正逻辑测试 ===")
    
    def simulate_direction(outer_w, outer_h, ow, oh, threshold=1.25):
        if outer_w > 0 and outer_h > 0 and ow > 0 and oh > 0:
            ratio_px = ow / oh
            ratio_val = outer_w / outer_h
            px_is_landscape = ratio_px > threshold
            px_is_portrait = ratio_px < 1.0 / threshold
            val_is_landscape = ratio_val > threshold
            val_is_portrait = ratio_val < 1.0 / threshold
            need_swap = False
            if px_is_portrait and val_is_landscape:
                need_swap = True
            elif px_is_landscape and val_is_portrait:
                need_swap = True
            if need_swap:
                outer_w, outer_h = outer_h, outer_w
        return outer_w, outer_h
    
    cases = [
        # (outer_w, outer_h, px_w, px_h, exp_w, exp_h, desc)
        (60.5, 133, 500, 1000, 60.5, 133, "OCR竖版+像素竖版→保持"),
        (60.5, 133, 800, 700, 60.5, 133, "OCR竖版+像素接近1:1→保持(阈值内)"),
        (60.5, 133, 2000, 500, 133, 60.5, "OCR竖版+像素极横版→swap(矛盾)"),
        (133, 60.5, 1000, 500, 133, 60.5, "OCR横版+像素横版→保持"),
        (133, 60.5, 500, 2000, 60.5, 133, "OCR横版+像素极竖版→swap(矛盾)"),
        (44.5, 76, 400, 700, 44.5, 76, "内挖OCR竖版+像素竖版→保持"),
        (44.5, 76, 800, 400, 76, 44.5, "内挖OCR竖版+像素横版(矛盾,超阈值)→swap"),
    ]
    
    all_pass = True
    for outer_w, outer_h, ow, oh, exp_w, exp_h, desc in cases:
        w, h = simulate_direction(outer_w, outer_h, ow, oh)
        ok = (w == exp_w and h == exp_h)
        status = '✅' if ok else '❌'
        if not ok:
            all_pass = False
        print(f'{status} {desc}: got {w}x{h}, exp {exp_w}x{exp_h}')
    
    print(f'\n=== direction_correction: {"ALL PASS" if all_pass else "SOME FAILED"} ===\n')
    assert all_pass, "direction_correction 存在失败用例，详见上方逐条输出"

def test_margin_validation():
    """模拟边距校验逻辑"""
    print("=== 边距校验逻辑测试 ===")
    
    def _validate_pair(a, b, expected_sum, pair_name="test"):
        if expected_sum <= 0:
            return a, b
        upper_cap = expected_sum * 2 + 50
        if a > upper_cap:
            a = 0.0
        if b > upper_cap:
            b = 0.0
        if a > 0 and b <= 0:
            b_new = expected_sum - a
            if 0 <= b_new <= expected_sum * 2:
                return a, b_new
            return a, b
        if b > 0 and a <= 0:
            a_new = expected_sum - b
            if 0 <= a_new <= expected_sum * 2:
                return a_new, b
            return a, b
        return a, b
    
    cases = [
        # (ml, mr, expected, exp_ml, exp_mr, desc)
        (14.6, 42.4, 16.0, 14.6, 42.4, "两边都有值→信任OCR不改"),
        (0, 42.4, 16.0, 0, 42.4, "仅右边有值,expected_sum小→推导为负→保留原值"),
        (0, 10, 100.0, 90.0, 10, "仅右边有小值,expected_sum大→合理推导左边=90"),
        (30, 0, 50.0, 30, 20, "仅左边有值,expected_sum合理→推导右边=20"),
        (0, 0, 16.0, 0, 0, "两边都无值→保持0,交给后续回退"),
        (14.6, 42.4, 1000.0, 14.6, 42.4, "两边有值,expected_sum不影响"),
        (0, 42.4, 100.0, 57.6, 42.4, "仅右边有值,expected_sum=100→推导100-42.4=57.6合理→接受推导值"),
    ]
    
    all_pass = True
    for ml, mr, exp_sum, exp_ml, exp_mr, desc in cases:
        a, b = _validate_pair(ml, mr, exp_sum, '左右边距')
        ok = (abs(a - exp_ml) < 0.1 and abs(b - exp_mr) < 0.1)
        status = '✅' if ok else '❌'
        if not ok:
            all_pass = False
        print(f'{status} {desc}: got ml={a}, mr={b}, exp ml={exp_ml}, mr={exp_mr}')
    
    # 回退逻辑测试
    print("\n--- 边距回退逻辑测试 ---")
    # 场景1: 单边缺失 - 保留OCR值
    ml, mr = 0, 42.4
    outer_w, inner_w = 60.5, 44.5
    exp_sum = outer_w - inner_w
    ml, mr = _validate_pair(ml, mr, exp_sum)
    print(f"场景1(单边缺失): 校验后 ml={ml}, mr={mr} →")
    if ml <= 0 and mr <= 0:
        ml = mr = max(0, exp_sum / 2)
        print(f"  双边均缺失→几何均分: ml=mr={ml}")
    elif ml <= 0 or mr <= 0:
        print(f"  单边缺失→保留已有值,缺失位保持0(不做均分)")
    print(f"  最终 ml={ml}, mr={mr}")
    
    # 场景2: 双边缺失 - 几何均分
    ml, mr = 0, 0
    outer_w, inner_w = 60.5, 44.5
    exp_sum = outer_w - inner_w
    ml, mr = _validate_pair(ml, mr, exp_sum)
    if ml <= 0 and mr <= 0:
        ml = mr = max(0, exp_sum / 2)
        print(f"场景2(双边缺失): 几何回退 ml=mr={ml}")
    else:
        print(f"场景2: ml={ml}, mr={mr}")
    
    # 场景3: 真实案例模拟 - 外框60.5x133, 内挖44.5x76, 边距 14.6/42.4/6/10
    print("\n--- 真实案例模拟 ---")
    outer_w, outer_h = 60.5, 133
    inner_w, inner_h = 44.5, 76
    mt, mb, ml, mr = 6, 10, 14.6, 42.4
    
    mt, mb = _validate_pair(mt, mb, outer_h - inner_h, '上下边距')
    ml, mr = _validate_pair(ml, mr, outer_w - inner_w, '左右边距')
    
    print(f"正确场景: 外框{outer_w}x{outer_h}, 内挖{inner_w}x{inner_h}")
    print(f"  边距: 上={mt}, 下={mb}, 左={ml}, 右={mr}")
    assert abs(mt - 6) < 0.1 and abs(mb - 10) < 0.1, f"上下边距错误: {mt},{mb}"
    assert abs(ml - 14.6) < 0.1 and abs(mr - 42.4) < 0.1, f"左右边距错误: {ml},{mr}"
    print(f"  ✅ 边距正确!")
    
    print(f'\n=== margin_validation: {"ALL PASS" if all_pass else "SOME FAILED"} ===\n')
    assert all_pass, "margin_validation 存在失败用例，详见上方逐条输出"


def _run_as_script(fn):
    """脚本入口辅助：把断言型测试函数转成布尔结果（pytest 走 assert 路径）。"""
    try:
        fn()
        return True
    except AssertionError:
        return False


if __name__ == '__main__':
    r1 = _run_as_script(test_name_parser)
    r2 = _run_as_script(test_direction_correction)
    r3 = _run_as_script(test_margin_validation)
    print(f'\n{"="*50}')
    print(f'综合结果: {"✅ ALL PASSED" if (r1 and r2 and r3) else "❌ SOME FAILED"}')
