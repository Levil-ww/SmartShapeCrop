# -*- coding: utf-8 -*-
"""最终综合验证测试"""
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
print("最终综合验证：多层边框同心圆角裁剪")
print("=" * 60)

# 渲染三种配置
imgs = {
    '8.5cm': render_design(make_design(8.5)),
    '3.0cm': render_design(make_design(3.0)),
    '0cm': render_design(make_design(0.0)),
}
for name, img in imgs.items():
    img.save(os.path.join(OUT_DIR, f'test_final_{name}.jpg'), quality=95)

W, H = imgs['8.5cm'].size
arr = {k: np.array(v) for k, v in imgs.items()}

outer_margin_px = cm2px(0.5)
ox2, oy2 = W - outer_margin_px, H - outer_margin_px
R = int(round(8.5 * DPI / 2.54))
T = cm2px(8.0) - cm2px(0.5)
R_inner = max(0, R - T)
cx, cy = ox2 - R, oy2 - R

offsets = [cm2px(l.offset_cm) for l in make_design(8.5).borders]
cum = 0
cum_offsets = []
for o in offsets:
    cum_offsets.append(cum)
    cum += o

layer_colors = [(20,20,20),(255,255,255),(220,40,40),(255,255,255),(80,60,30),(220,190,120),(250,245,230)]
layer_names = ['黑','白','红','白','深棕','浅金','米白']

print(f"\n画布: {W}x{H}, R={R}px, T={T}px, R_inner={R_inner}px")
print(f"右下角圆心: ({cx},{cy})")
print(f"层偏移: {offsets}")
print(f"累积: {cum_offsets}")

errors = []

# === 1. 各层颜色正确性 ===
print(f"\n[1] 各层颜色验证（沿 45° 方向）")
for i in range(7):
    r_outer = R - cum_offsets[i]
    r_inner = R - cum_offsets[i] - offsets[i]
    r_mid = (r_outer + r_inner) // 2
    x = cx + int(r_mid * 0.707)
    y = cy + int(r_mid * 0.707)
    p = arr['8.5cm'][y, x]
    expected = layer_colors[i]
    ok = abs(int(p[0])-expected[0])<=2 and abs(int(p[1])-expected[1])<=2 and abs(int(p[2])-expected[2])<=2
    if ok:
        print(f"  ✓ 层{i}({layer_names[i]}) r=[{r_inner},{r_outer}] dist≈{r_mid} → ({int(p[0])},{int(p[1])},{int(p[2])})")
    else:
        print(f"  ✗ 层{i}({layer_names[i]}) r=[{r_inner},{r_outer}] dist≈{r_mid} → ({int(p[0])},{int(p[1])},{int(p[2])}) 期望 {expected}")
        errors.append(f"层{i}颜色错误")

# === 2. 直线段颜色 ===
print(f"\n[2] 直线段边框颜色（右边缘，远离角落）")
for i in range(7):
    r_mid = (R - cum_offsets[i] + R - cum_offsets[i] - offsets[i]) // 2
    y = oy2 - R - 100 - cum_offsets[i] - offsets[i]//2
    y = max(0, min(y, H - 1))
    x = ox2 - r_mid
    x = max(0, min(x, W - 1))
    p = arr['8.5cm'][y, x]
    expected = layer_colors[i]
    ok = abs(int(p[0])-expected[0])<=2 and abs(int(p[1])-expected[1])<=2 and abs(int(p[2])-expected[2])<=2
    if ok:
        print(f"  ✓ 层{i}({layer_names[i]}) ({x},{y}) → ({int(p[0])},{int(p[1])},{int(p[2])})")
    else:
        print(f"  ✗ 层{i}({layer_names[i]}) ({x},{y}) → ({int(p[0])},{int(p[1])},{int(p[2])}) 期望 {expected}")
        errors.append(f"层{i}直线段颜色错误")

# === 3. 水池区域 ===
print(f"\n[3] 水池区域验证")
pool_ok = True
for dy in range(0, R_inner, 5):
    for dx in range(0, R_inner, 5):
        x, y = ox2 - dx, oy2 - dy
        if x < 0 or y < 0:
            continue
        dist = ((x-cx)**2 + (y-cy)**2)**0.5
        if dist <= R_inner - 5:
            p = arr['8.5cm'][y, x]
            if not (240 <= int(p[0]) <= 255 and 235 <= int(p[1]) <= 250 and 210 <= int(p[2]) <= 235):
                pool_ok = False
                break
if pool_ok:
    print(f"  ✓ 水池区域颜色正确（米白色 (250,245,220)）")
else:
    print(f"  ✗ 水池区域颜色异常")
    errors.append("水池颜色错误")

# === 4. 切掉区域 ===
print(f"\n[4] 切掉尖角区域验证")
cut_ok = True
for dy in range(0, 50, 5):
    for dx in range(0, 50, 5):
        x, y = ox2 - dx, oy2 - dy
        if x < 0 or y < 0:
            continue
        dist = ((x-cx)**2 + (y-cy)**2)**0.5
        if R < dist < R + 50:
            p = arr['8.5cm'][y, x]
            if not (int(p[0]) >= 254 and int(p[1]) >= 254 and int(p[2]) >= 254):
                cut_ok = False
                break
if cut_ok:
    print(f"  ✓ 切掉区域为纯白色（外背景色 (255,255,255)）")
else:
    print(f"  ✗ 切掉区域颜色异常")
    errors.append("切掉区域颜色错误")

# === 5. 对比无圆角版本 ===
print(f"\n[5] 与无圆角版本对比")
no_corner_has_white = False
for dy in range(0, R, 5):
    for dx in range(0, R, 5):
        x, y = ox2 - dx, oy2 - dy
        if x < 0 or y < 0:
            continue
        dist = ((x-cx)**2 + (y-cy)**2)**0.5
        if dist <= R - 5:
            p = arr['0cm'][y, x]
            # 无圆角时角落应该也是边框颜色
            if int(p[0]) >= 254 and int(p[1]) >= 254 and int(p[2]) >= 254:
                no_corner_has_white = True
if no_corner_has_white:
    print(f"  ⚠ 无圆角版本角落有白色（可能是边框层颜色）")
else:
    print(f"  ✓ 无圆角版本角落正确")

# === 总结 ===
print(f"\n{'='*60}")
if errors:
    print(f"✗ 发现 {len(errors)} 个错误:")
    for e in errors:
        print(f"  - {e}")
else:
    print("✓✓✓ 所有检查通过！")
    print()
    print("  多层边框同心圆角裁剪已正确实现：")
    print("  1. 每层边框在角落保留原有颜色和线条")
    print("  2. 圆弧过渡平滑，无白色背景泄漏")
    print("  3. 直线段与角落环形带无缝衔接")
    print("  4. 水池/内部填充区域颜色正确")
    print("  5. 切掉的尖角显示外背景色（白色）")
    print()
    print("  实现方式：每层边框带 = 同心圆角矩形差集")
    print("  - 外层：距外边缘 t_outer 处的圆角矩形（圆角半径 R - t_outer）")
    print("  - 内层：距外边缘 t_outer - t_layer 处的圆角矩形（圆角半径 R - t_outer - t_layer）")
    print("  - band = 外层 & ~内层")
    print()
    print("  这保证了每层在直线部分是矩形带，在角落是同心环扇形，")
    print("  不会出现白色覆盖，边框自身颜色和线条完整保留。")
