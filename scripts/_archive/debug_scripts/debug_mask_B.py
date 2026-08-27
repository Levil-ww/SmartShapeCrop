from PIL import Image
import numpy as np
from core.image_cropper import (
    apply_border_only_corners, _get_border_layers_robust,
    _build_multi_layer_corner_mask, _estimate_outer_background,
    _corner_sector_has_content,
)
from debug_issue_repro import make_sainaishiguang
from core.corner.algorithm import CORNER_ANGLES

img = make_sainaishiguang()
w, h = img.size

border_layers = _get_border_layers_robust(img, (255,255,255))
print("raw:", border_layers)
outer_bg = _estimate_outer_background(img)
first_color, first_t = border_layers[0]
color_dist = float(np.linalg.norm(np.array(first_color, dtype=np.float64) - np.array(outer_bg, dtype=np.float64)))
threshold = max(30, int(min(img.size) * 0.03))
if color_dist < 25.0 and first_t > threshold:
    border_layers = border_layers[1:]
print("filtered:", border_layers)

r = 295
corners_px = {'tl': r, 'tr': r, 'bl': r, 'br': r}
protect = {ck: _corner_sector_has_content(img, ck, r, sum(t for _,t in border_layers)) for ck, r in corners_px.items()}
print("protect:", protect)

mask = _build_multi_layer_corner_mask(
    w, h, corners_px, border_layers,
    protect_content=protect,
    bg_color=(255,255,255), content_ref_arr=None,
)
mask_arr = np.array(mask)
print("mask min/max:", mask_arr.min(), mask_arr.max())

# Save mask as image
Image.fromarray(mask_arr).save("debug_output/mask_B.png")

# Count mask=255 outside each arc
for name, cx, cy in [('tl', r, r), ('tr', w-r, r), ('bl', r, h-r), ('br', w-r, h-r)]:
    cnt255 = 0
    total = 0
    for y in range(max(0, cy-r-20), min(h, cy+r+21)):
        for x in range(max(0, cx-r-20), min(w, cx+r+21)):
            dx = x - cx
            dy = y - cy
            d = np.sqrt(dx*dx + dy*dy)
            if d > r:
                total += 1
                if mask_arr[y,x] > 0:
                    cnt255 += 1
    print(f"{name} outside arc: total={total}, mask255={cnt255}")
