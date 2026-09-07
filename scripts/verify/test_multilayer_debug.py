"""调试 test_multilayer_border_with_mixed_colors 的简化脚本。"""
import os
import sys
import math
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.image_cropper import apply_border_only_corners

w, h = 600, 800
img = Image.new('RGB', (w, h), (255, 255, 255))
arr = np.array(img)

# 外层蓝色边框 40px
arr[0:40, :] = (0, 50, 200)
arr[-40:, :] = (0, 50, 200)
arr[:, 0:40] = (0, 50, 200)
arr[:, -40:] = (0, 50, 200)

# 白色间隙 15px (40-55) - 保持白色

# 内层绿色边框 25px (55-80)
arr[55:80, 55:545] = (0, 150, 80)
arr[-80:-55, 55:545] = (0, 150, 80)
arr[55:725, 55:80] = (0, 150, 80)
arr[55:725, -80:-55] = (0, 150, 80)

# 内部花纹 - 密集的小图案
for y in range(150, 650, 20):
    for x in range(150, 550, 20):
        if ((x // 20) + (y // 20)) % 2 == 0:
            arr[y:y+10, x:x+10] = (255, 100, 100)
        else:
            arr[y:y+10, x:x+10] = (100, 100, 255)

img = Image.fromarray(arr, 'RGB')

dpi = 150
corners = {'tl': 2.0, 'tr': 2.0, 'bl': 2.0, 'br': 2.0}

result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=(255, 255, 255))
result_arr = np.array(result)

out_path = os.path.join(os.path.dirname(__file__), 'test_multilayer_debug.jpg')
result.save(out_path, 'JPEG', quality=95)
print(f'已保存: {out_path}')

# 复现检查3
r_px = int(2.0 / 2.54 * 150)
print(f'r_px = {r_px}')
corners_list = [
    ('tl', (r_px, r_px)),
    ('tr', (w - r_px - 1, r_px)),
    ('bl', (r_px, h - r_px - 1)),
    ('br', (w - r_px - 1, h - r_px - 1)),
]
for corner, (cx_offset, cy_offset) in corners_list:
    cx, cy = cx_offset, cy_offset
    gap_count = 0
    white_coords = []
    for angle_deg in range(0, 91, 10):
        angle_rad = math.radians(angle_deg)
        for dist_offset in range(-5, 1):
            dist_px = r_px + dist_offset
            if corner == 'tl':
                x = int(cx - dist_px * math.cos(angle_rad))
                y = int(cy - dist_px * math.sin(angle_rad))
            elif corner == 'tr':
                x = int(cx + dist_px * math.cos(angle_rad))
                y = int(cy - dist_px * math.sin(angle_rad))
            elif corner == 'bl':
                x = int(cx - dist_px * math.cos(angle_rad))
                y = int(cy + dist_px * math.sin(angle_rad))
            else:
                x = int(cx + dist_px * math.cos(angle_rad))
                y = int(cy + dist_px * math.sin(angle_rad))
            if 0 <= x < w and 0 <= y < h:
                pixel = result_arr[y, x]
                if tuple(pixel) == (255, 255, 255):
                    gap_count += 1
                    white_coords.append((corner, angle_deg, dist_offset, x, y))
    print(f'{corner}: {gap_count} 个白色像素')
    for item in white_coords[:5]:
        print('  ', item)
