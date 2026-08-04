# -*- coding: utf-8 -*-
"""逐行对比原 apply_rounded_corners 的 mask 生成逻辑 vs 我的函数"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from PIL import Image, ImageDraw

from core.image_cropper import _CORNER_PARAMS, _CORNER_SQUARE, BORDER_TOTAL_DEPTH_CM, BORDER_ONLY_THRESHOLD_CM

DPI = 150
radius_cm = 8.5
W, H = 1200, 900
actual_cm = radius_cm + (BORDER_TOTAL_DEPTH_CM if radius_cm >= BORDER_ONLY_THRESHOLD_CM else 0)
r = max(1, int(round(actual_cm * DPI / 2.54)))
print(f"r = {r} (actual_cm={actual_cm})")

# ============== 方法 A：完全复刻原 apply_rounded_corners 的核心逻辑 ==============
mask_A = Image.new('L', (W, H), 255)
draw_A = ImageDraw.Draw(mask_A)
get_params = _CORNER_PARAMS['br']
get_square = _CORNER_SQUARE['br']
sq_A = get_square(W, H, r)
sqA_safe = [max(0, sq_A[0]), max(0, sq_A[1]), min(W, sq_A[2]), min(H, sq_A[3])]
print(f"方法 A (原逻辑): sq={sq_A}  safe_sq={sqA_safe}")
if sqA_safe[2] > sqA_safe[0] and sqA_safe[3] > sqA_safe[1]:
    draw_A.rectangle(sqA_safe, fill=0)
bbox_A, sA, eA = get_params(W, H, r)
pbA_safe = [max(0, bbox_A[0]), max(0, bbox_A[1]), min(W, bbox_A[2]), min(H, bbox_A[3])]
print(f"                pieslice_bbox={bbox_A} safe={pbA_safe} start={sA} end={eA}")
if pbA_safe[2] > pbA_safe[0] and pbA_safe[3] > pbA_safe[1]:
    draw_A.pieslice(pbA_safe, start=sA, end=eA, fill=255)
arr_A = np.array(mask_A)

# ============== 方法 B：我的 _carve_L_corners_on_mask 同样的 rect=(0,0,w,h) r ==============
mask_B = Image.new('L', (W, H), 255)
draw_B = ImageDraw.Draw(mask_B)
x1,y1,x2,y2 = (0, 0, W, H)
sq_B = [x2 - r, y2 - r, x2, y2]
sqB_safe = [max(0, sq_B[0]), max(0, sq_B[1]), min(W, sq_B[2]), min(H, sq_B[3])]
print(f"方法 B (我的):   sq={sq_B}  safe_sq={sqB_safe}")
if sqB_safe[2] > sqB_safe[0] and sqB_safe[3] > sqB_safe[1]:
    draw_B.rectangle(sqB_safe, fill=0)
pb_B = [x2 - 2*r, y2 - 2*r, x2, y2]
pbB_safe = [max(0, pb_B[0]), max(0, pb_B[1]), min(W, pb_B[2]), min(H, pb_B[3])]
print(f"                pieslice_bbox={pb_B} safe={pbB_safe} start=0 end=90")
if pbB_safe[2] > pbB_safe[0] and pbB_safe[3] > pbB_safe[1]:
    draw_B.pieslice(pbB_safe, start=0, end=90, fill=255)
arr_B = np.array(mask_B)

# ============== 对比 ==============
diff = arr_A != arr_B
count = int(np.sum(diff))
print(f"\n方法A vs 方法B: 不同像素数={count}")
if count > 0:
    ys, xs = np.where(diff)
    for i in range(8):
        x, y = int(xs[i]), int(ys[i])
        # 打印每个不同点，看 A 是 0 还是 255，B 是 0 还是 255
        print(f"  diff[{i}]: ({x},{y})  A={arr_A[y,x]}  B={arr_B[y,x]}")
        # 计算是否在 br 的 sector 保留区
        cx, cy = W - r, H - r  # 圆心
        dx, dy = x - cx, y - cy
        dist2 = dx*dx + dy*dy
        print(f"    圆心=({cx},{cy})  dx={dx} dy={dy}  dist²={dist2}  r²={r*r}  in_sector={dist2 <= r*r and dx>=0 and dy>=0}")
        # 是否在 sq 里
        in_sq = sqA_safe[0] <= x < sqA_safe[2] and sqA_safe[1] <= y < sqA_safe[3]
        print(f"    in_sq? {in_sq}   x in [{sqA_safe[0]},{sqA_safe[2]}) y in [{sqA_safe[1]},{sqA_safe[3]})")
