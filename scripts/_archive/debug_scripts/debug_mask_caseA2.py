import os
import numpy as np
from PIL import Image, ImageDraw
from core.image_cropper import (
    apply_border_only_corners, _get_border_layers_robust,
    _build_multi_layer_corner_mask, _estimate_outer_background,
)
from core.corner.algorithm import CORNER_ANGLES

DPI = 150
w, h = 1200, 1000
img = Image.new('RGB', (w, h), (0, 0, 0))
draw = ImageDraw.Draw(img)
margin = 60
draw.rectangle([margin, margin, w - margin - 1, h - margin - 1], fill=(255, 255, 255))
border = 12
draw.rectangle([margin, margin, w - margin - 1, h - margin - 1], outline=(0, 0, 0), width=border)

border_layers = _get_border_layers_robust(img, (255,255,255))
print("raw border_layers:", border_layers)
# Simulate filter
outer_bg = _estimate_outer_background(img)
first_color, first_t = border_layers[0]
color_dist = float(np.linalg.norm(np.array(first_color, dtype=np.float64) - np.array(outer_bg, dtype=np.float64)))
threshold = max(30, int(min(img.size) * 0.03))
if color_dist < 25.0 and first_t > threshold:
    border_layers = border_layers[1:]
    if not border_layers:
        border_layers = []
print("filtered border_layers:", border_layers)

corners_px = {'bl': int(round(1.5*DPI/2.54)), 'br': int(round(1.5*DPI/2.54))}
print("corners_px:", corners_px)

mask = _build_multi_layer_corner_mask(
    w, h, corners_px, border_layers,
    protect_content={'bl': False, 'br': False},
    bg_color=(255,255,255), content_ref_arr=None,
)
mask_arr = np.array(mask)

for ck, r in corners_px.items():
    if ck == 'bl':
        cx, cy = r, h - r
    else:
        cx, cy = w - r, h - r
    ang_min, ang_max = CORNER_ANGLES[ck]
    y1, x1 = max(0, cy - r), max(0, cx - r)
    y2, x2 = min(h, cy + r), min(w, cx + r)
    yy, xx = np.mgrid[y1:y2, x1:x2]
    dx = xx - cx
    dy = yy - cy
    dist = np.sqrt(dx*dx + dy*dy)
    angle = np.degrees(np.arctan2(dy, dx)) % 360
    valid_angle = (angle >= ang_min) & (angle <= ang_max)
    outside = valid_angle & (dist > r)
    outside_vals = mask_arr[y1:y2, x1:x2][outside]
    print(f"{ck}: outside_pixels={outside_vals.size}, mask255={np.sum(outside_vals>0)}, mask0={np.sum(outside_vals==0)}")

res = apply_border_only_corners(img, {'bl':1.5, 'br':1.5}, dpi=DPI, bg_color=(255,255,255))
res.save("debug_output/caseA_debug2.jpg", quality=95)
print("saved")
