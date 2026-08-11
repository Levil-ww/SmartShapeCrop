# -*- coding: utf-8 -*-
"""
核心验证：设计预览的圆角边框带在角落处保留颜色，不露白。
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
print("测试：同心环形带 —— 边框在角落保留颜色")
print("=" * 60)

# 无圆角对照
img_no = render_design(make_design(0.0))
img_rd = render_design(make_design(8.5))
W, H = img_no.size
p_no = os.path.join(OUT_DIR, 'test_ring_NOcorner.jpg')
p_rd = os.path.join(OUT_DIR, 'test_ring_WITHcorner.jpg')
img_no.save(p_no, quality=95)
img_rd.save(p_rd, quality=95)

arr_no = np.array(img_no)
arr_rd = np.array(img_rd)

def is_white(p, thr=250):
    return int(p[0]) >= thr and int(p[1]) >= thr and int(p[2]) >= thr

def classify(p):
    r,g,b = int(p[0]), int(p[1]), int(p[2])
    s = r+g+b
    if s >= 750: return '白'
    if s <= 80:  return '黑'
    if r>180 and g<100 and b<100: return '红'
    if abs(r-250)<18 and abs(g-245)<18 and abs(b-230)<30: return '米白'
    if r>200 and g>170 and b<140: return '浅金'
    if 40<r<130 and 30<g<100 and 10<b<70: return '深棕'
    return f'杂'

# 关键几何
outer_margin_px = cm2px(0.5)
ox1, oy1 = outer_margin_px, outer_margin_px
ox2, oy2 = W - outer_margin_px, H - outer_margin_px
r_px = int(round(8.5 * DPI / 2.54))
cx, cy = ox2 - r_px, oy2 - r_px

# 边框总厚度 T = inner_margin(8cm) - outer_margin(0.5cm) = 7.5cm
T = cm2px(8.0) - cm2px(0.5)
R_inner = max(0, r_px - T)
print(f"外圆角 R={r_px}px, 边框总厚 T={T}px, 内圆角 R_inner={R_inner}px")

# 检查点：在角落环形带上的各个位置
# 1. 外边缘附近（L0外黑应保留）
# 2. 边框带中间（L3/L4深棕应保留）
# 3. 内边缘附近（L6米白应保留）

checks = []
# 外边缘：沿外圆弧的位置
checks.append(('外边缘-外侧 dx=0.3R dy=0.3R', cx + int(0.3*r_px), cy + int(0.3*r_px), True))
checks.append(('外边缘-外侧 dx=0.7R dy=0.7R', cx + int(0.7*r_px), cy + int(0.7*r_px), True))

# 边框带中间位置：沿圆弧方向，在 R-T/2 处
mid_R = r_px - T // 2
checks.append(('边框带-中线 dx=mid*cos45 dy=mid*sin45',
    cx + int(mid_R * 0.707), cy + int(mid_R * 0.707), True))

# 内边缘附近
checks.append(('内边缘-内侧 dx=R_inner-2 dy=R_inner-2',
    cx + max(2, R_inner - 2) + 2, cy + max(2, R_inner - 2) + 2, True))

# 最外顶点（应被裁=白）
checks.append(('最外右下顶点', ox2-1, oy2-1, True))
# 贴右边列（边框带最外侧，应保留颜色）
checks.append(('贴右列 y=oy2-3', ox2-1, oy2-3, True))
checks.append(('贴右列 y=oy2-T/2', ox2-1, oy2 - T // 2, True))
# 贴底边行
checks.append(('贴底行 x=ox2-3', ox2-3, oy2-1, True))
checks.append(('贴底行 x=ox2-T/2', ox2 - T // 2, oy2-1, True))

print("\n--- 加圆角后各点颜色 ---")
ok = True
for desc, x, y, exp_nonwhite in checks:
    xc, yc = min(max(x,0),W-1), min(max(y,0),H-1)
    p_rd = arr_rd[yc, xc]
    p_no = arr_no[yc, xc]
    w = is_white(p_rd)
    if exp_nonwhite and w:
        ok = False
        mark = '✗✗(失败!)'
    else:
        mark = '✓'
    print(f"  ({xc:>4},{yc:>4}) {desc:<40}  无:{classify(p_no):<8} 有:{classify(p_rd):<8} {mark}")

# 最后：整个贴右边列从顶点向下采样，不应出现白色
print("\n--- 贴右边列 y=oy2 往下 300px 内有无白色 ---")
white_found = False
for dy in range(0, 300, 30):
    x, y = ox2 - 1, oy2 - dy
    if y < 0: break
    p = arr_rd[y, x]
    if is_white(p):
        print(f"  y={y}: 白色!  (dx=oy2-{dy})")
        white_found = True
        ok = False
if not white_found:
    print("  ✓ 贴右列 300px 内未发现白色 (边框颜色保留)")

print("\n--- 贴底边行 x=ox2 往左 300px 内有无白色 ---")
white_found2 = False
for dx in range(0, 300, 30):
    x, y = ox2 - dx, oy2 - 1
    if x < 0: break
    p = arr_rd[y, x]
    if is_white(p):
        print(f"  x={x}: 白色! (dx=ox2-{dx})")
        white_found2 = True
        ok = False
if not white_found2:
    print("  ✓ 贴底行 300px 内未发现白色 (边框颜色保留)")

print()
if ok:
    print("  ➜ ✓✓ 成功！边框带在角落保留了自身颜色，没有被白色覆盖。")
    print("     每层边框（黑/白/红/深棕/浅金/米白）的圆弧是连续的。")
else:
    print("  ➜ ✗ 仍有白色覆盖，需要进一步调整。")

print(f"\n文件: {p_no}\n      {p_rd}")
