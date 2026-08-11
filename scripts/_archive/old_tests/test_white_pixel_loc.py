# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from core.geometry import CropDesign, BorderLayer
from core.image_ops import render_design

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'test_cropper_output')
os.makedirs(OUT_DIR, exist_ok=True)
DPI = 150
def cm2px(cm): return int(round(cm * DPI / 2.54))

def make_design(br_cm):
    d = CropDesign()
    d.canvas_w_cm = 41.0; d.canvas_h_cm = 55.0; d.dpi = DPI
    d.mode = 'rect_hole'
    d.outer_margin_cm = 0.5
    d.inner_margin_top_cm = 8.0; d.inner_margin_bottom_cm = 8.0
    d.inner_margin_left_cm = 8.0; d.inner_margin_right_cm = 8.0
    d.borders = [
        BorderLayer(offset_cm=0.8,  fill_type='solid', color=(20, 20, 20)),
        BorderLayer(offset_cm=0.3,  fill_type='solid', color=(255, 255, 255)),
        BorderLayer(offset_cm=0.3,  fill_type='solid', color=(220, 40, 40)),
        BorderLayer(offset_cm=0.2,  fill_type='solid', color=(255, 255, 255)),
        BorderLayer(offset_cm=3.5,  fill_type='solid', color=(80, 60, 30)),
        BorderLayer(offset_cm=0.15, fill_type='solid', color=(220, 190, 120)),
        BorderLayer(offset_cm=2.0,  fill_type='solid', color=(250, 245, 230)),
    ]
    d.outer_bg_color = (255, 255, 255)
    d.hole_bg_color = (250, 245, 220)
    d.corner_br_cm = br_cm
    return d

img = render_design(make_design(8.5))
W, H = img.size
arr = np.array(img)

outer_margin_px = cm2px(0.5)
ox2, oy2 = W - outer_margin_px, H - outer_margin_px
R = int(round(8.5 * DPI / 2.54))
T_total = cm2px(8.0) - cm2px(0.5)
R_inner = max(0, R - T_total)

cx, cy = ox2 - R, oy2 - R  # 右下角的圆心

print(f"圆心: ({cx}, {cy}), R={R}, R_inner={R_inner}")
print(f"扫描区域: {R_inner} < dist < {R}")

# 找到所有白色像素的精确位置
white_pixels = []
for dy in range(0, R+5, 1):
    for dx in range(0, R+5, 1):
        x, y = ox2 - dx, oy2 - dy
        if x < 0 or y < 0 or x >= W or y >= H:
            continue
        dist = ((x - cx)**2 + (y - cy)**2) ** 0.5
        if R_inner <= dist <= R + 3:  # 包含扩大的 3 像素
            p = arr[y, x]
            r, g, b = int(p[0]), int(p[1]), int(p[2])
            if r >= 250 and g >= 250 and b >= 250:
                white_pixels.append((x, y, r, g, b, dist))

print(f"\n白色像素总数: {len(white_pixels)}")
if white_pixels:
    # 按位置分组
    from collections import Counter
    positions = [(x, y) for x, y, _, _, _, _ in white_pixels]
    print(f"唯一位置数: {len(set(positions))}")
    
    # 显示前 20 个
    print("\n前 20 个白色像素:")
    for x, y, r, g, b, dist in white_pixels[:20]:
        print(f"  ({x:>4},{y:>4}) dist={dist:>6.1f}  rgb=({r},{g},{b}) 距右={ox2-x} 距底={oy2-y}")
    
    # 分析分布
    dists = [d for _, _, _, _, _, d in white_pixels]
    xs = [x for x, _, _, _, _, _ in white_pixels]
    ys = [y for _, y, _, _, _, _ in white_pixels]
    print(f"\n距离范围: {min(dists):.1f} ~ {max(dists):.1f}")
    print(f"X 范围: {min(xs)} ~ {max(xs)}")
    print(f"Y 范围: {min(ys)} ~ {max(ys)}")
    
    # 检查是否都在外边缘 (dist ≈ R)
    near_outer = [d for d in dists if d > R - 5]
    near_inner = [d for d in dists if d < R_inner + 5]
    print(f"\n接近外边缘(dist > R-5): {len(near_outer)} 个")
    print(f"接近内边缘(dist < R_inner+5): {len(near_inner)} 个")
    print(f"中间区域: {len(dists) - len(near_outer) - len(near_inner)} 个")
    
    # 检查是否在直线部分交界处
    # 直线部分在角落的 x=ox2-1, y 从 oy2-R 到 oy2
    # 或 y=oy2-1, x 从 ox2-R 到 ox2
    boundary_x = [x for x, y, _, _, _, _ in white_pixels if x >= ox2 - 10]
    boundary_y = [y for x, y, _, _, _, _ in white_pixels if y >= oy2 - 10]
    print(f"\n靠近右边缘(x>={ox2-10}): {len(boundary_x)} 个")
    print(f"靠近底边缘(y>={oy2-10}): {len(boundary_y)} 个")
