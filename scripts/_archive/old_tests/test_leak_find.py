# -*- coding: utf-8 -*-
"""精确定位 2 个白色背景泄漏像素"""
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

offsets = [cm2px(l.offset_cm) for l in make_design(8.5).borders]
cum = 0
cum_offsets = []
for o in offsets:
    cum_offsets.append(cum)
    cum += o

# 白色层的径向范围
white_layer_ranges = []
for wi in [1, 3]:
    r_lo = R_px - cum_offsets[wi] - offsets[wi]
    r_hi = R_px - cum_offsets[wi]
    white_layer_ranges.append((r_lo, r_hi, wi))

# 精确扫描
leaks = []
for dy in range(0, R_px):
    for dx in range(0, R_px):
        x, y = ox2 - dx, oy2 - dy
        if x < 0 or y < 0:
            continue
        dist = ((x-cx)**2 + (y-cy)**2) ** 0.5
        if R_inner <= dist <= R_px:
            p = arr[y, x]
            is_white = int(p[0]) >= 254 and int(p[1]) >= 254 and int(p[2]) >= 254
            if is_white:
                # 检查是否在白色层范围
                in_white = False
                for r_lo, r_hi, wi in white_layer_ranges:
                    if r_lo - 1 <= dist <= r_hi + 1:
                        in_white = True
                        break
                if not in_white:
                    leaks.append((x, y, dist, int(p[0]), int(p[1]), int(p[2])))

print(f"找到 {len(leaks)} 个白色背景泄漏像素:")
for x, y, dist, r, g, b in leaks:
    # 检查周围像素
    neighbors = []
    for ddx in [-1, 0, 1]:
        for ddy in [-1, 0, 1]:
            if ddx == 0 and ddy == 0:
                continue
            nx, ny = x + ddx, y + ddy
            if 0 <= nx < W and 0 <= ny < H:
                np_ = arr[ny, nx]
                neighbors.append((ddx, ddy, int(np_[0]), int(np_[1]), int(np_[2])))
    print(f"  ({x},{y}) dist={dist:.1f} rgb=({r},{g},{b})")
    print(f"    周围像素: {[(ddx,ddy,r,g,b) for ddx,ddy,r,g,b in neighbors]}")
    # 检查该距离对应的层
    for i, co in enumerate(cum_offsets):
        r_outer = R_px - co
        r_inner = R_px - co - offsets[i]
        if abs(dist - r_outer) <= 5:
            print(f"    紧邻层{i}外边界 r={r_outer}")
        if abs(dist - r_inner) <= 5:
            print(f"    紧邻层{i}内边界 r={r_inner}")
