"""Trace which border path is actually used for the black marble source image."""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

logging.basicConfig(level=logging.INFO, format='%(name)s %(levelname)s: %(message)s')

import numpy as np
from PIL import Image
from core.lshape_border import (
    detect_border_v13, apply_lshape_border_completion,
    _detect_lshape_border_auto, _try_apply_v13,
)
from core.geometry import RectShape

src_path = r'C:\Users\Administrator\Desktop\吸水皮革-定制-裁剪有图-黑色大理石;74x188.5CM裁剪有图.jpg'
img = Image.open(src_path)
print(f"Source image size: {img.size}")

# 1. V13 detection
v13 = detect_border_v13(img)
print(f"\nV13 detection result: {v13}")

# 2. Auto routing
profile_layers, v13_result, v13_computed, v13_preferred = _detect_lshape_border_auto(img)
print(f"\nAuto routing:")
print(f"  profile_layers: {profile_layers}")
print(f"  v13_result: {v13_result}")
print(f"  v13_computed: {v13_computed}")
print(f"  v13_preferred: {v13_preferred}")

# 3. Simulate actual L-shape border completion with scale=1.0
# Create a canvas from the image
canvas_arr = np.array(img.convert('RGB'))
H, W = canvas_arr.shape[:2]

# Simulate a single L-shape cut at 'tr' corner
cut_w = int(W * 0.3)
cut_h = int(H * 0.3)
outer_rect = RectShape(0, 0, W, H)

print(f"\nSimulating L-shape border completion:")
print(f"  Canvas: {W}x{H}")
print(f"  Cut: tr, {cut_w}x{cut_h}")
print(f"  Scale: 1.0, 1.0")

# Try V13 path directly
print("\n--- Trying V13 path directly ---")
v13_ok = _try_apply_v13(
    canvas_arr=canvas_arr.copy(),
    material_img=img,
    outer_rect=outer_rect,
    cut_corner='tr',
    cut_w_px=cut_w,
    cut_h_px=cut_h,
    src_material_img=img,
    scale_x=1.0,
    scale_y=1.0,
    edge_px=v13_result[0] if v13_result else None,
    band_px=v13_result[1] if v13_result else None,
    band_color=v13_result[3] if v13_result else None,
    edge_color=v13_result[2] if v13_result else None,
)
print(f"  V13 path result: {v13_ok}")

# 4. Try full completion
print("\n--- Full completion ---")
canvas2 = np.array(img.convert('RGB'))
result = apply_lshape_border_completion(
    canvas_arr=canvas2,
    material_img=img,
    outer_rect=outer_rect,
    cut_corner='tr',
    cut_w_px=cut_w,
    cut_h_px=cut_h,
    src_material_img=img,
    scale_x=1.0,
    scale_y=1.0,
)
print(f"  Full completion result: {result}")

# 5. Check what the output looks like at the edges
# Scan from the right edge at mid-height to see border thickness
mid_y = H // 2
row = canvas2[mid_y, :, :]
# Find where the black border ends (scanning from right to left)
right_col = W - 1
for x in range(W - 1, W - 100, -1):
    r, g, b = row[x]
    brightness = (int(r) + int(g) + int(b)) / 3
    if brightness > 60:
        print(f"\n  Right edge black border ends at x={x} (width={W-x}px)")
        break

# Scan from top edge at mid-width
mid_x = W // 2
col = canvas2[:, mid_x, :]
for y in range(0, 100):
    r, g, b = col[y]
    brightness = (int(r) + int(g) + int(b)) / 3
    if brightness > 60:
        print(f"  Top edge black border ends at y={y} (width={y}px)")
        break
