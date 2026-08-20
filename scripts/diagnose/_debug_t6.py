"""Diagnose T6 (110×400) issue - understand OCR values and margin assignment."""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# Enable detailed logging
logging.basicConfig(level=logging.DEBUG, format='%(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('core.pool_designer.sketch_parser')
logger.setLevel(logging.DEBUG)

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from core.pool_designer.sketch_parser import parse_sketch

def create_sketch(out_path, out_w_cm, out_h_cm, in_w_cm, in_h_cm,
                  mt, mb, ml, mr):
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

    draw.text((ox - 45, oy + H // 2 - 10), str(out_h_cm), fill='black', font=font)
    draw.text((ox + W // 2 - 15, oy + H + 10), str(out_w_cm), fill='black', font=font)
    draw.text((ix + iw_px // 2 - 10, iy + 10), str(in_w_cm), fill='black', font=font)
    draw.text((ix + iw_px // 2 - 15, iy + ih_px // 2), str(in_h_cm), fill='black', font=font)
    draw.text((ox + ml_px // 2 - 15, iy + ih_px // 2 - 10), str(ml), fill='black', font=font)
    draw.text((ix + iw_px + mr_px // 2 - 15, iy + ih_px // 2 - 10), str(mr), fill='black', font=font)
    draw.text((ox + W // 2 - 5, oy + mt_px // 2 - 10), str(mt), fill='black', font=font)
    draw.text((ox + W // 2 - 8, iy + ih_px + mb_px // 2 - 10), str(mb), fill='black', font=font)

    os.makedirs(os.path.dirname(out_path) if os.path.dirname(out_path) else '.', exist_ok=True)
    img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
    cv2.imwrite(out_path, img_cv)
    
    # Print label positions
    print(f"\n  图像尺寸: {img_w}×{img_h} px")
    print(f"  外框: ({ox},{oy},{W},{H}) px")
    print(f"  内框: ({ix},{iy},{iw_px},{ih_px}) px")
    print(f"  外边距标签: ({ox + ml_px // 2 - 15 + 7}, {iy + ih_px // 2 - 10 + 8}) ≈ {ml}")
    print(f"  内宽标签: ({ix + iw_px // 2 - 10 + 5}, {iy + 10 + 8}) ≈ {in_w_cm}")
    print(f"  内高标签: ({ix + iw_px // 2 - 15 + 7}, {iy + ih_px // 2 + 8}) ≈ {in_h_cm}")
    print(f"  右标签: ({ix + iw_px + mr_px // 2 - 15 + 7}, {iy + ih_px // 2 - 10 + 8}) ≈ {mr}")
    print(f"  上标签: ({ox + W // 2 - 5 + 5}, {oy + mt_px // 2 - 10 + 8}) ≈ {mt}")
    print(f"  下标签: ({ox + W // 2 - 8 + 5}, {iy + ih_px + mb_px // 2 - 10 + 8}) ≈ {mb}")
    
    return out_path

out_dir = os.path.dirname(os.path.abspath(__file__))

# Generate T6 sketch
print("=" * 70)
print("生成 T6 草图 (110×400)...")
fname = os.path.join(out_dir, '_debug_t6_sketch.png')
create_sketch(fname, 400, 110, 120, 45, 25, 40, 24, 256)

print("\n" + "=" * 70)
print("运行解析...")
print("=" * 70)

result = parse_sketch(fname, target_outer_w_cm=400, target_outer_h_cm=110)

print(f"\n  结果:")
print(f"    outer: {result.outer_w_cm}×{result.outer_h_cm} (期望 400×110)")
print(f"    inner: {result.inner_w_cm}×{result.inner_h_cm} (期望 120×45)")
print(f"    margins: top={result.margin_top_cm}, bottom={result.margin_bottom_cm}, left={result.margin_left_cm}, right={result.margin_right_cm}")
print(f"    expected: top=25, bottom=40, left=24, right=256")
print(f"    method: {result.method}")
print(f"    message: {result.message}")

if hasattr(result, 'ocr_values') and result.ocr_values:
    print(f"\n  OCR values:")
    for v in result.ocr_values:
        if isinstance(v, dict):
            print(f"    {v}")

if hasattr(result, 'debug') and result.debug:
    print(f"\n  Debug keys: {list(result.debug.keys())}")
