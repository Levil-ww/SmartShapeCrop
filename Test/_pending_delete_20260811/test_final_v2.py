# -*- coding: utf-8 -*-
"""
修正后的测试：验证每层边框颜色正确，没有白色背景泄漏。
"""
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

print("=" * 60)
print("验证：多层边框圆角裁剪 — 每层颜色保留，无白色背景泄漏")
print("=" * 60)

img_rd = render_design(make_design(8.5))
img_no = render_design(make_design(0.0))
W, H = img_rd.size
arr_rd = np.array(img_rd)
arr_no = np.array(img_no)

img_rd.save(os.path.join(OUT_DIR, 'test_final_8.5cm.jpg'), quality=95)
img_no.save(os.path.join(OUT_DIR, 'test_final_NOcorner.jpg'), quality=95)

outer_margin_px = cm2px(0.5)
ox2, oy2 = W - outer_margin_px, H - outer_margin_px
R_px = int(round(8.5 * DPI / 2.54))
T_total = cm2px(8.0) - cm2px(0.5)
R_inner = max(0, R_px - T_total)
cx, cy = ox2 - R_px, oy2 - R_px

offsets = [cm2px(l.offset_cm) for l in make_design(8.5).borders]
cum_offsets = []
cum = 0
for o in offsets:
    cum_offsets.append(cum)
    cum += o

# 每层的预期颜色
layer_colors = [(20,20,20),(255,255,255),(220,40,40),(255,255,255),(80,60,30),(220,190,120),(250,245,230)]

print(f"\n画布: {W}x{H}, R={R_px}, T_total={T_total}, R_inner={R_inner}")
print(f"右下角圆心: ({cx},{cy})")

# --- 检查 1: 每层 band 是否显示正确颜色 ---
print(f"\n--- 检查 1: 各层 band 颜色（45° 方向） ---")
all_ok = True
for i, (co, col) in enumerate(zip(cum_offsets, layer_colors)):
    r_outer = R_px - co
    r_inner = R_px - co - offsets[i]
    r_mid = (r_outer + r_inner) // 2
    x = cx + int(r_mid * 0.707)
    y = cy + int(r_mid * 0.707)
    if 0 <= x < W and 0 <= y < H:
        p = arr_rd[y, x]
        expected = col
        match = (abs(int(p[0])-expected[0]) <= 2 and 
                 abs(int(p[1])-expected[1]) <= 2 and 
                 abs(int(p[2])-expected[2]) <= 2)
        pct = abs(int(p[0])-expected[0]) + abs(int(p[1])-expected[1]) + abs(int(p[2])-expected[2])
        status = "✓" if match else f"✗ (偏差={pct})"
        if not match:
            all_ok = False
        layer_name = ['黑','白','红','白','深棕','浅金','米白'][i]
        print(f"  层{i}({layer_name}) r=[{r_inner},{r_outer}] 中点({x},{y}) dist≈{r_mid} -> ({int(p[0])},{int(p[1])},{int(p[2])}) {status}")

# --- 检查 2: 环扇形区域无白色背景泄漏 ---
# 白色背景 = (255,255,255) 且不是白色层的位置
print(f"\n--- 检查 2: 环扇形区域无白色背景泄漏 ---")
bg_leak_count = 0
total_checked = 0
scan_step = max(1, R_px // 50)
for dy in range(0, R_px, scan_step):
    for dx in range(0, R_px, scan_step):
        x, y = ox2 - dx, oy2 - dy
        if x < 0 or y < 0:
            continue
        dist = ((x-cx)**2 + (y-cy)**2) ** 0.5
        if R_inner <= dist <= R_px:
            total_checked += 1
            p = arr_rd[y, x]
            # 白色背景泄漏: 纯白(255,255,255) 且该位置不属于任何白色层
            # 层1和层3是白色的，它们的半径范围:
            # 层1: r=[437, 455], 层3: r=[407, 419]
            is_white_bg = (int(p[0]) >= 254 and int(p[1]) >= 254 and int(p[2]) >= 254)
            # 检查是否在白色层的径向范围内
            in_white_layer = False
            for wi in [1, 3]:  # 白色层索引
                r_lo = R_px - cum_offsets[wi] - offsets[wi]
                r_hi = R_px - cum_offsets[wi]
                if r_lo <= dist <= r_hi:
                    in_white_layer = True
                    break
            if is_white_bg and not in_white_layer:
                bg_leak_count += 1

if bg_leak_count == 0:
    print(f"  ✓ 环扇形区域（{total_checked} 采样）无白色背景泄漏")
else:
    print(f"  ✗ 发现 {bg_leak_count}/{total_checked} 个白色背景泄漏像素！")
    all_ok = False

# --- 检查 3: 水池区域正确 ---
print(f"\n--- 检查 3: 水池区域（dist < {R_inner}） ---")
pool_ok = True
for dy in range(0, R_inner, max(1, R_inner // 10)):
    for dx in range(0, R_inner, max(1, R_inner // 10)):
        x, y = ox2 - dx, oy2 - dy
        if x < 0 or y < 0:
            continue
        dist = ((x-cx)**2 + (y-cy)**2) ** 0.5
        if dist <= R_inner:
            p = arr_rd[y, x]
            # 水池颜色应为 hole_bg_color (250, 245, 220)
            if not (240 <= int(p[0]) <= 255 and 235 <= int(p[1]) <= 250 and 210 <= int(p[2]) <= 235):
                pool_ok = False
                break
if pool_ok:
    print(f"  ✓ 水池区域颜色正确（米白）")
else:
    print(f"  ✗ 水池区域颜色异常！")
    all_ok = False

# --- 检查 4: 切掉区域是白色（外背景） ---
print(f"\n--- 检查 4: 角落切掉区域（dist > R）为白色 ---")
cut_correct = True
for dy in range(0, min(R_px+50, H-oy2), max(1, (R_px+50)//10)):
    for dx in range(0, min(R_px+50, W-ox2), max(1, (R_px+50)//10)):
        x, y = ox2 - dx, oy2 - dy
        if x < 0 or y < 0:
            continue
        dist = ((x-cx)**2 + (y-cy)**2) ** 0.5
        if dist > R_px and dist < R_px + 50:
            p = arr_rd[y, x]
            if not (int(p[0]) >= 254 and int(p[1]) >= 254 and int(p[2]) >= 254):
                cut_correct = False
                break
if cut_correct:
    print(f"  ✓ 切掉区域为白色（外背景色）")
else:
    print(f"  ✗ 切掉区域颜色异常！")
    all_ok = False

print(f"\n{'='*60}")
if all_ok:
    print("✓✓✓ 所有检查通过！")
    print("  多层边框圆角裁剪正确实现：")
    print("  - 每层边框在角落保持原有颜色和线条")
    print("  - 角落圆弧过渡平滑，无白色背景泄漏")
    print("  - 水池/内部填充区域颜色正确")
    print("  - 切掉的尖角区域显示外背景色")
else:
    print("✗ 仍有问题需要修复。")
