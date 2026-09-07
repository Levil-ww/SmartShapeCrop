"""调试 test_xianxu_corner_clean 的简化脚本。"""
import os
import sys
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.image_cropper import apply_border_only_corners
from core.corner.algorithm import CORNER_ANGLES


def _make_bordered_image(w, h, bg, layers, content_color=(60, 120, 100), content_period=30):
    arr = np.full((h, w, 3), bg, dtype=np.uint8)
    d = 0
    for thickness, color in layers:
        arr[d:d+thickness, d:w-d] = color
        arr[h-d-thickness:h-d, d:w-d] = color
        arr[d:h-d, d:d+thickness] = color
        arr[d:h-d, w-d-thickness:w-d] = color
        d += thickness
    # 内容区花纹
    for y in range(d, h - d):
        for x in range(d, w - d):
            if ((x // content_period) + (y // content_period)) % 2 == 0:
                arr[y, x] = content_color
    return arr


w, h = 900, 600
bg = (255, 255, 255)
orig = _make_bordered_image(w, h, bg, [
    (10, (20, 20, 20)),
    (15, (235, 220, 195)),
    (15, (25, 25, 25)),
], content_color=(60, 120, 100), content_period=30)
img = Image.fromarray(orig, 'RGB')
result = apply_border_only_corners(img, {'tl': 9.0}, dpi=150, bg_color=bg)
result_arr = np.array(result)

out_path = os.path.join(os.path.dirname(__file__), 'test_xianxu_debug.jpg')
result.save(out_path, 'JPEG', quality=95)
print(f'已保存: {out_path}')

r_px = int(9.0 * 150 / 2.54)
ang_min, ang_max = CORNER_ANGLES['tl']
yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
dx = xx - r_px; dy = yy - r_px
dist = np.sqrt(dx*dx + dy*dy)
angle = np.mod(np.degrees(np.arctan2(dy, dx)), 360.0)
valid = (angle >= ang_min) & (angle <= ang_max)
outside = valid & (dist > r_px)
outside_arr = result_arr[outside]
is_bg = np.all(outside_arr == 255, axis=1)
is_dark = np.max(outside_arr, axis=1) < 60
is_bg_or_border = is_bg | is_dark
not_allowed = ~is_bg_or_border
print(f'outside pixels: {len(outside_arr)}')
print(f'not allowed: {np.sum(not_allowed)} ({np.mean(not_allowed):.4f})')

if np.any(not_allowed):
    not_allowed_2d = np.zeros(outside.shape, dtype=bool)
    not_allowed_2d[outside] = not_allowed
    coords = np.where(not_allowed_2d)
    for i in range(min(20, len(coords[0]))):
        y, x = coords[0][i], coords[1][i]
        print(f'  ({x}, {y}): orig={orig[y, x]}, result={result_arr[y, x]}, dist={dist[y,x]:.1f}, angle={angle[y,x]:.1f}')
