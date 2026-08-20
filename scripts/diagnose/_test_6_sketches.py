"""测试6张草图的完整解析流程，验证建议1-8的修复效果。

用法: python scripts/diagnose/_test_6_sketches.py
"""
import sys, os, logging, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

logger = logging.getLogger('core.pool_designer.sketch_parser')
logger.setLevel(logging.WARNING)

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from core.pool_designer.sketch_parser import parse_sketch

def create_sketch(out_path, out_w_cm, out_h_cm, in_w_cm, in_h_cm,
                  mt, mb, ml, mr):
    """创建合成草图 (宽×高 格式: out_w=宽, out_h=高)"""
    scale = 8
    W = int(out_w_cm * scale)
    H = int(out_h_cm * scale)
    ml_px = int(ml * scale)
    mr_px = int(mr * scale)
    mt_px = int(mt * scale)
    mb_px = int(mb * scale)
    iw_px = int(in_w_cm * scale)
    ih_px = int(in_h_cm * scale)
    pad = 50
    img_w = W + 2 * pad
    img_h = H + 2 * pad

    img_pil = Image.new('RGB', (img_w, img_h), 'white')
    draw = ImageDraw.Draw(img_pil)
    ox, oy = pad, pad

    draw.rectangle([ox, oy, ox + W, oy + H], outline='red', width=3)
    ix = ox + ml_px
    iy = oy + mt_px
    draw.rectangle([ix, iy, ix + iw_px, iy + ih_px], outline='blue', width=3)

    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except:
        font = ImageFont.load_default()

    # 外框总高 (左侧)
    draw.text((ox - 45, oy + H // 2 - 10), str(out_h_cm), fill='black', font=font)
    # 外框总宽 (底部)
    draw.text((ox + W // 2 - 15, oy + H + 10), str(out_w_cm), fill='black', font=font)
    # 内框宽
    draw.text((ix + iw_px // 2 - 10, iy + 10), str(in_w_cm), fill='black', font=font)
    # 内框高
    draw.text((ix + iw_px // 2 - 15, iy + ih_px // 2), str(in_h_cm), fill='black', font=font)
    # 左边距
    draw.text((ox + ml_px // 2 - 15, iy + ih_px // 2 - 10), str(ml), fill='black', font=font)
    # 右边距
    draw.text((ix + iw_px + mr_px // 2 - 15, iy + ih_px // 2 - 10), str(mr), fill='black', font=font)
    # 上边距
    draw.text((ox + W // 2 - 5, oy + mt_px // 2 - 10), str(mt), fill='black', font=font)
    # 下边距
    draw.text((ox + W // 2 - 8, iy + ih_px + mb_px // 2 - 10), str(mb), fill='black', font=font)

    os.makedirs(os.path.dirname(out_path) if os.path.dirname(out_path) else '.', exist_ok=True)
    img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
    cv2.imwrite(out_path, img_cv)
    return out_path

out_dir = os.path.dirname(os.path.abspath(__file__))

# 6个测试场景 (按 用户描述: 第一个数=高, 第二个数=宽)
# 但 create_sketch 参数是 (宽, 高)，所以需要调换
tests_raw = [
    # (名称, 用户格式 "高x宽", 外高, 外宽, 内高, 内宽, 上, 下, 左, 右)
    ('T1: 60×150',   60.0, 150.0, 40.0, 70.0,  10.0, 10.0, 43.5, 36.5),
    ('T2: 58×120',   58.0, 120.0, 42.0, 57.0,   6.0, 10.0, 10.0, 53.0),
    ('T3: 234×60',   60.0, 234.0, 45.0, 86.0,   6.0,  9.0, 36.0, 112.0),
    ('T4: 60.5×133', 60.5, 133.0, 44.5, 76.0,   6.0, 10.0, 14.6, 42.4),
    ('T5: 58×78',    58.0,  78.0, 40.5, 58.5,   9.5,  8.0, 12.0,  7.5),
    ('T6: 110×400', 110.0, 400.0, 45.0,120.0,  25.0, 40.0, 24.0,256.0),
]

print("=" * 70)
print("生成6张测试草图...")
print("=" * 70)

test_cases = []
for i, (name, oh, ow, ih, iw, mt, mb, ml, mr) in enumerate(tests_raw):
    fname = os.path.join(out_dir, f'_test_6sketch_{i+1}.png')
    create_sketch(fname, ow, oh, iw, ih, mt, mb, ml, mr)
    test_cases.append((name, fname, ow, oh, iw, ih, mt, mb, ml, mr))
    print(f"  {name}: {fname}")

print("\n" + "=" * 70)
print("运行解析测试（使用最新代码）...")
print("=" * 70)

results_summary = []
for name, path, ow, oh, iw, ih, mt, mb, ml, mr in test_cases:
    print(f"\n{'='*60}")
    print(f"测试 {name}: {path}")
    print(f"{'='*60}")

    t0 = time.time()
    result = parse_sketch(path, target_outer_w_cm=ow, target_outer_h_cm=oh)
    elapsed = time.time() - t0

    ok_ow = abs(result.outer_w_cm - ow) < 2.0
    ok_oh = abs(result.outer_h_cm - oh) < 2.0
    ok_iw = abs(result.inner_w_cm - iw) < 2.5
    ok_ih = abs(result.inner_h_cm - ih) < 2.5
    ok_mt = abs(result.margin_top_cm - mt) < 2.0
    ok_mb = abs(result.margin_bottom_cm - mb) < 2.0
    ok_ml = abs(result.margin_left_cm - ml) < 2.5
    ok_mr = abs(result.margin_right_cm - mr) < 2.5

    all_ok = all([ok_ow, ok_oh, ok_iw, ok_ih, ok_mt, ok_mb, ok_ml, ok_mr])

    print(f"  耗时: {elapsed:.1f}s  方法: {result.method}")
    print(f"  外框: {result.outer_w_cm:.1f}×{result.outer_h_cm:.1f} (期望 {ow:.1f}×{oh:.1f}) {'OK' if ok_ow and ok_oh else 'FAIL'}")
    print(f"  内框: {result.inner_w_cm:.1f}×{result.inner_h_cm:.1f} (期望 {iw:.1f}×{ih:.1f}) {'OK' if ok_iw and ok_ih else 'FAIL'}")
    print(f"  边距: 上{result.margin_top_cm:.1f}/下{result.margin_bottom_cm:.1f}/左{result.margin_left_cm:.1f}/右{result.margin_right_cm:.1f}")
    print(f"  期望: 上{mt:.1f}/下{mb:.1f}/左{ml:.1f}/右{mr:.1f}")
    print(f"  对比: 上{'OK' if ok_mt else 'FAIL'} 下{'OK' if ok_mb else 'FAIL'} 左{'OK' if ok_ml else 'FAIL'} 右{'OK' if ok_mr else 'FAIL'}")
    if hasattr(result, 'message') and result.message:
        print(f"  消息: {result.message}")
    if hasattr(result, 'ocr_values') and result.ocr_values:
        vals = ', '.join([f"{v['direction']}={v['value']:.1f}(c={v['confidence']:.0f})" for v in result.ocr_values])
        print(f"  OCR: {vals}")
    print(f"  总结: {'ALL CORRECT' if all_ok else 'HAS DEVIATIONS'}")

    results_summary.append({
        'name': name, 'all_ok': all_ok, 'elapsed': elapsed,
        'ow': result.outer_w_cm, 'oh': result.outer_h_cm,
        'iw': result.inner_w_cm, 'ih': result.inner_h_cm,
        'mt': result.margin_top_cm, 'mb': result.margin_bottom_cm,
        'ml': result.margin_left_cm, 'mr': result.margin_right_cm,
        'ok_ow': ok_ow, 'ok_oh': ok_oh, 'ok_iw': ok_iw, 'ok_ih': ok_ih,
        'ok_mt': ok_mt, 'ok_mb': ok_mb, 'ok_ml': ok_ml, 'ok_mr': ok_mr,
    })

print("\n" + "=" * 70)
print("汇总结果")
print("=" * 70)
for r in results_summary:
    flag = "✅" if r['all_ok'] else "❌"
    issues = []
    if not r['ok_mt']: issues.append(f"上{r['mt']:.1f}")
    if not r['ok_mb']: issues.append(f"下{r['mb']:.1f}")
    if not r['ok_ml']: issues.append(f"左{r['ml']:.1f}")
    if not r['ok_mr']: issues.append(f"右{r['mr']:.1f}")
    issue_str = f" 问题: {', '.join(issues)}" if issues else ""
    print(f"  {flag} {r['name']}: {r['elapsed']:.1f}s{issue_str}")

total_ok = sum(1 for r in results_summary if r['all_ok'])
print(f"\n通过: {total_ok}/{len(results_summary)}")
