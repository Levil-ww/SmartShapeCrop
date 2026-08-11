# -*- coding: utf-8 -*-
"""
综合测试：验证多层边框在大圆角下的同心环形带效果。
测试点已更新：直线段用远离角落的位置，角落用环扇形区域。
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
print("综合测试：多层边框同心环形带（修正版）")
print("=" * 60)

# 无圆角对照
img_no = render_design(make_design(0.0))
img_rd = render_design(make_design(8.5))
img_small_rd = render_design(make_design(3.0))

W, H = img_no.size
arr_no = np.array(img_no)
arr_rd = np.array(img_rd)
arr_sm = np.array(img_small_rd)

# 保存对比图
img_no.save(os.path.join(OUT_DIR, 'test_final_NOcorner.jpg'), quality=95)
img_rd.save(os.path.join(OUT_DIR, 'test_final_8.5cm.jpg'), quality=95)
img_small_rd.save(os.path.join(OUT_DIR, 'test_final_3cm.jpg'), quality=95)

# 关键几何
outer_margin_px = cm2px(0.5)
ox1, oy1 = outer_margin_px, outer_margin_px
ox2, oy2 = W - outer_margin_px, H - outer_margin_px
R_px = int(round(8.5 * DPI / 2.54))
T_total = cm2px(8.0) - cm2px(0.5)
R_inner = max(0, R_px - T_total)
cx, cy = ox2 - R_px, oy2 - R_px  # 右下角圆心

def is_white(p, thr=250):
    return int(p[0]) >= thr and int(p[1]) >= thr and int(p[2]) >= thr

def color_name(p):
    r,g,b = int(p[0]), int(p[1]), int(p[2])
    s = r+g+b
    if s >= 750: return '白'
    if s <= 80:  return '黑'
    if r>180 and g<100 and b<100: return '红'
    if abs(r-250)<18 and abs(g-245)<18 and abs(b-230)<30: return '米白'
    if r>200 and g>170 and b<140: return '浅金'
    if 40<r<130 and 30<g<100 and 10<b<70: return '深棕'
    return f'杂({r},{g},{b})'

# 累积偏移（像素）
offsets = [cm2px(l.offset_cm) for l in make_design(8.5).borders]
cum = 0
cum_offsets = []
for o in offsets:
    cum_offsets.append(cum)
    cum += o

print(f"\n参数：R={R_px}px, T_total={T_total}px, R_inner={R_inner}px")
print(f"右下角圆心: ({cx}, {cy})")
print(f"画布尺寸：{W}x{H}")
print(f"各层累积偏移: {cum_offsets}")

# 测试点 — 8.5cm 圆角
# 1. 直线段（远离子角落）— 右边缘，y 距底 > R
# 2. 环扇形区域 — dist 在 [R_inner, R] 之间
# 3. 水池内部 — dist < R_inner
# 4. 角落被切掉区域 — dist > R（应为白色/背景色）

tests_85 = [
    # --- 直线段（右边缘，距底远大于 R） ---
    ('直线段-右边缘(远)', ox2-1, oy2-R_px-50, True),
    ('直线段-右边缘(远2)', ox2-1, oy2-R_px-200, True),
    ('直线段-底边缘(远)', ox2-R_px-50, oy2-1, True),
    
    # --- 环扇形区域（右下角，45°方向） ---
    ('环扇形-靠外(45°)', cx + int((R_px-5)*0.707), cy + int((R_px-5)*0.707), True),
    ('环扇形-中线(45°)', cx + int((R_px - T_total//2)*0.707), cy + int((R_px - T_total//2)*0.707), True),
    ('环扇形-靠内(45°)', cx + int(max(R_inner+5,1)*0.707), cy + int(max(R_inner+5,1)*0.707), True),
    
    # --- 环扇形区域（水平方向，right edge 附近） ---
    ('环扇形-水平靠外', cx + R_px - 5, cy + 50, True),
    ('环扇形-水平靠内', cx + R_inner + 5, cy + 50, True),
    
    # --- 水池内部 ---
    ('水池-中心偏外', cx + int(R_inner*0.5*0.707), cy + int(R_inner*0.5*0.707), True),
    ('水池-远离角落', ox2 - T_total - 100, oy2 - T_total - 100, True),
    
    # --- 角落切掉区域（应该是白色=外背景色） ---
    ('切掉区-外顶点', ox2-1, oy2-1, False),  # 应为白色
]

print("\n--- 8.5cm 圆角测试 ---")
ok = True
for desc, x, y, expect_nonwhite in tests_85:
    xc, yc = min(max(x,0),W-1), min(max(y,0),H-1)
    p = arr_rd[yc, xc]
    white = is_white(p)
    if expect_nonwhite and white:
        ok = False
        mark = '✗✗ 白色!'
    elif not expect_nonwhite and not white:
        ok = False
        mark = '✗✗ 应为白色!'
    else:
        mark = '✓'
    dist = ((xc-cx)**2 + (yc-cy)**2)**0.5
    print(f"  ({xc:>4},{yc:>4}) {desc:<25}  dist={dist:>6.0f}  颜色:{color_name(p):<15} {mark}")

# 扫描角落 L 形区域：检查环扇形内是否有白色
print("\n--- 环扇形区域白色扫描 ---")
ring_white_count = 0
ring_total = 0
scan_step = max(1, R_px // 40)
for dy in range(0, R_px, scan_step):
    for dx in range(0, R_px, scan_step):
        x, y = ox2 - dx, oy2 - dy
        if x < 0 or y < 0:
            continue
        dist = ((x-cx)**2 + (y-cy)**2) ** 0.5
        if R_inner <= dist <= R_px:
            ring_total += 1
            if is_white(arr_rd[y, x]):
                ring_white_count += 1

if ring_white_count == 0:
    print(f"  ✓ 环扇形区域内（{ring_total} 采样点）无白色像素")
else:
    print(f"  ✗ 环扇形区域内有 {ring_white_count}/{ring_total} 个白色像素！")
    ok = False

# 扫描水池区域（dist < R_inner）
print("\n--- 水池区域白色扫描 ---")
pool_white_count = 0
pool_total = 0
for dy in range(0, R_inner, max(1, R_inner // 20)):
    for dx in range(0, R_inner, max(1, R_inner // 20)):
        x, y = ox2 - dx, oy2 - dy
        if x < 0 or y < 0:
            continue
        dist = ((x-cx)**2 + (y-cy)**2) ** 0.5
        if dist <= R_inner:
            pool_total += 1
            if is_white(arr_rd[y, x]):
                pool_white_count += 1

if pool_white_count == 0:
    print(f"  ✓ 水池区域内（{pool_total} 采样点）无白色像素")
else:
    print(f"  ✗ 水池区域内有 {pool_white_count}/{pool_total} 个白色像素！")
    ok = False

# 3cm 小圆角对照
print("\n--- 3cm 小圆角对照 ---")
R3 = int(round(3.0 * DPI / 2.54))
cx3, cy3 = ox2 - R3, oy2 - R3
tests_3cm = [
    ('直线段-右边缘', ox2-1, oy2-R3-50, True),
    ('直线段-底边缘', ox2-R3-50, oy2-1, True),
    ('环扇形-中线(45°)', cx3 + int((R3 - T_total//2)*0.707), cy3 + int((R3 - T_total//2)*0.707), True),
    ('水池-内部', ox2-T_total-50, oy2-T_total-50, True),
]
for desc, x, y, expect_nonwhite in tests_3cm:
    xc, yc = min(max(x,0),W-1), min(max(y,0),H-1)
    p_sm = arr_sm[yc, xc]
    p_no = arr_no[yc, xc]
    print(f"  ({xc:>4},{yc:>4}) {desc:<25}  无:{color_name(p_no):<12} 3cm:{color_name(p_sm)}")

print()
if ok:
    print("  ➜ ✓✓✓ 成功！所有检查通过。")
    print("     同心环形带正确生成：每层边框在角落保持颜色，无白色覆盖。")
else:
    print("  ➜ ✗ 仍有问题需要修复。")

print(f"\n输出文件:")
print(f"  {os.path.join(OUT_DIR, 'test_final_NOcorner.jpg')}")
print(f"  {os.path.join(OUT_DIR, 'test_final_8.5cm.jpg')}")
print(f"  {os.path.join(OUT_DIR, 'test_final_3cm.jpg')}")
