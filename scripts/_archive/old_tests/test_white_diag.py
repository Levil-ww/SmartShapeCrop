# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from core.geometry import CropDesign, BorderLayer
from core.image_ops import render_design

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'test_cropper_output')
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
R_px = int(round(8.5 * DPI / 2.54))
T_total = cm2px(8.0) - cm2px(0.5)
R_inner = max(0, R_px - T_total)
cx, cy = ox2 - R_px, oy2 - R_px

def is_white(p, thr=250):
    return int(p[0]) >= thr and int(p[1]) >= thr and int(p[2]) >= thr

offsets = [cm2px(l.offset_cm) for l in make_design(8.5).borders]
cum = 0
cum_offsets = []
for o in offsets:
    cum_offsets.append(cum)
    cum += o

print(f"R={R_px}, T_total={T_total}, R_inner={R_inner}")
print(f"cum_offsets={cum_offsets}")
print(f"sum offsets={sum(offsets)}, diff from T_total={T_total - sum(offsets)}")

# 扫描环扇形区域，记录白色像素的距离分布
white_by_dist = {}
for dy in range(0, R_px, 2):
    for dx in range(0, R_px, 2):
        x, y = ox2 - dx, oy2 - dy
        if x < 0 or y < 0:
            continue
        dist = ((x-cx)**2 + (y-cy)**2) ** 0.5
        if R_inner <= dist <= R_px:
            if is_white(arr[y, x]):
                d_int = int(dist)
                if d_int not in white_by_dist:
                    white_by_dist[d_int] = 0
                white_by_dist[d_int] += 1

print(f"\n白色像素距离分布（{len(white_by_dist)} 个不同距离值）:")
for d in sorted(white_by_dist.keys()):
    # 检查这个距离对应哪一层的边界
    layer_info = ""
    for i, co in enumerate(cum_offsets):
        r_outer = R_px - co
        r_inner = R_px - co - offsets[i]
        if abs(d - r_outer) <= 3:
            layer_info += f" [层{i}外边界 r={r_outer}]"
        if abs(d - r_inner) <= 3:
            layer_info += f" [层{i}内边界 r={r_inner}]"
    print(f"  dist={d}: {white_by_dist[d]} 个白色像素{layer_info}")

# 检查层边界处的像素
print("\n层边界检查:")
for i, co in enumerate(cum_offsets):
    r_outer = R_px - co
    r_inner = R_px - co - offsets[i]
    # 在 45° 方向检查
    x_outer = int(cx + r_outer * 0.707)
    y_outer = int(cy + r_outer * 0.707)
    x_inner = int(cx + r_inner * 0.707)
    y_inner = int(cy + r_inner * 0.707)
    if 0 <= y_outer < H and 0 <= x_outer < W:
        p = arr[y_outer, x_outer]
        print(f"  层{i} 外边界 r={r_outer} ({x_outer},{y_outer}): {arr[y_outer,x_outer]} white={is_white(p)}")
    if 0 <= y_inner < H and 0 <= x_inner < W:
        p = arr[y_inner, x_inner]
        print(f"  层{i} 内边界 r={r_inner} ({x_inner},{y_inner}): {arr[y_inner,x_inner]} white={is_white(p)}")
