from PIL import Image
import numpy as np
from core.image_cropper import _corner_sector_has_content, _estimate_outer_background
from debug_issue_repro import make_sainaishiguang

img = make_sainaishiguang()
w, h = img.size

# Monkey-patch to print internals
import core.image_cropper as ic
orig = ic._corner_sector_has_content

def patched(img, corner_key, r_px, border_depth_px):
    print(f"\n--- patched {corner_key} ---")
    w, h = img.size
    r = min(r_px, max(1, min(w, h) // 2))
    outer_bg = np.array(_estimate_outer_background(img), dtype=np.float64)
    print("outer_bg:", outer_bg)
    if corner_key == 'tl':
        cx, cy = r, r
        x0, y0, x1, y1 = 0, 0, r, r
    elif corner_key == 'tr':
        cx, cy = w - r, r
        x0, y0, x1, y1 = w - r, 0, w, r
    elif corner_key == 'bl':
        cx, cy = r, h - r
        x0, y0, x1, y1 = 0, h - r, r, h
    else:
        cx, cy = w - r, h - r
        x0, y0, x1, y1 = w - r, h - r, w, h
    step = max(2, r // 20)
    tol = max(4, border_depth_px + 4)
    print("tol:", tol, "step:", step)
    samples = []
    for y in range(y0, y1, step):
        for x in range(x0, x1, step):
            dx = x - cx
            dy = y - cy
            if dx * dx + dy * dy <= r * r:
                continue
            if corner_key == 'tl':
                d_edge = min(x - x0, y - y0)
            elif corner_key == 'tr':
                d_edge = min((x1 - 1) - x, y - y0)
            elif corner_key == 'bl':
                d_edge = min(x - x0, (y1 - 1) - y)
            else:
                d_edge = min((x1 - 1) - x, (y1 - 1) - y)
            if d_edge <= tol:
                continue
            samples.append(img.getpixel((x, y)))
    print("num samples:", len(samples))
    if samples:
        sarr = np.array(samples, dtype=np.float64)
        dist_to_bg = np.sqrt(np.sum((sarr - outer_bg)**2, axis=1))
        print("dist_to_bg min/max/mean:", dist_to_bg.min(), dist_to_bg.max(), dist_to_bg.mean())
        non_bg_ratio = float(np.mean(dist_to_bg > 25.0))
        print("non_bg_ratio:", non_bg_ratio)
    return orig(img, corner_key, r_px, border_depth_px)

ic._corner_sector_has_content = patched

from core.image_cropper import apply_border_only_corners
img = make_sainaishiguang()
r = int(round(5.0*150/2.54))
apply_border_only_corners(img, {'tl':5.0,'tr':5.0,'bl':5.0,'br':5.0}, dpi=150, bg_color=(255,255,255))
