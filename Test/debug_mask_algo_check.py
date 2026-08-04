# -*- coding: utf-8 -*-
"""验证算法一致性：_carve_L_corners_on_mask（当 rect=(0,0,w,h)，且 r 加扩展补偿时）
与原 apply_rounded_corners 的 mask 完全一致。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from PIL import Image, ImageDraw

from core.image_cropper import (apply_rounded_corners, _CORNER_PARAMS, _CORNER_SQUARE,
                                 BORDER_TOTAL_DEPTH_CM, BORDER_ONLY_THRESHOLD_CM)

DPI = 150
radius_cm = 8.5
W, H = 1200, 900

# 关键：原 apply_rounded_corners 对 >=8.5cm 的角会 +2cm 扩展
actual_cm = radius_cm + (BORDER_TOTAL_DEPTH_CM if radius_cm >= BORDER_ONLY_THRESHOLD_CM else 0)
print(f"[关键] 输入圆角={radius_cm}cm (>=阈值 {BORDER_ONLY_THRESHOLD_CM}cm? {radius_cm>=BORDER_ONLY_THRESHOLD_CM})")
print(f"        实际扩展后圆角 = {actual_cm}cm ( +{BORDER_TOTAL_DEPTH_CM}cm 补偿 = 老代码为了让边框深度也被裁到的 hack )")

# 1. 原 apply_rounded_corners 的 mask
full_black = Image.new('RGB', (W, H), (0, 0, 0))
res_old = apply_rounded_corners(full_black.copy(), {'br': radius_cm}, dpi=DPI)
arr_old = np.array(res_old)
mask_old = 255 - np.all(arr_old == 255, axis=-1).astype(np.uint8) * 255

# 2. 用相同 r 和扩展，用 _carve_debug 的"最外层调用"直接生成 mask
r_px = max(1, int(round(actual_cm * DPI / 2.54)))
print(f"        r_px = {r_px} (基于 {actual_cm}cm × DPI{DPI})")

def _carve_debug(draw, cw, ch, rect, corners_px):
    x1, y1, x2, y2 = rect
    rw, rh = x2 - x1, y2 - y1
    for ck, r in corners_px.items():
        max_r = max(1, min(rw, rh) // 2)
        r = max(0, min(r, max_r))
        if r <= 0: continue
        if ck == 'tl':
            sq = [x1, y1, x1+r, y1+r]
            pb = [x1, y1, x1+2*r, y1+2*r]; s, e = 180, 270
        elif ck == 'tr':
            sq = [x2-r, y1, x2, y1+r]
            pb = [x2-2*r, y1, x2, y1+2*r]; s, e = 270, 360
        elif ck == 'bl':
            sq = [x1, y2-r, x1+r, y2]
            pb = [x1, y2-2*r, x1+2*r, y2]; s, e = 90, 180
        else:
            sq = [x2-r, y2-r, x2, y2]
            pb = [x2-2*r, y2-2*r, x2, y2]; s, e = 0, 90
        sq_s = [max(0,sq[0]), max(0,sq[1]), min(cw,sq[2]), min(ch,sq[3])]
        if sq_s[2] > sq_s[0] and sq_s[3] > sq_s[1]:
            draw.rectangle(sq_s, fill=0)
        pb_s = [max(0,pb[0]), max(0,pb[1]), min(cw,pb[2]), min(ch,pb[3])]
        if pb_s[2] > pb_s[0] and pb_s[3] > pb_s[1]:
            draw.pieslice(pb_s, start=s, end=e, fill=255)

mask_new = Image.new('L', (W, H), 255)
_carve_debug(ImageDraw.Draw(mask_new), W, H, (0, 0, W, H), {'br': r_px})
arr_new = np.array(mask_new)

# 3. 对比
print(f"\n{'位置':<32} {'旧 mask':>8} {'新 mask':>8}  一致?")
checks = [
    ('右下顶点 (1199,899)', 1199, 899),
    ('右边缘 L形 (1198,447)', 1198, 447),
    ('下边缘 L形 (747,898)', 747, 898),
    ('扇形内部 (948,648)', 948, 648),
    ('图中心 (600,450)', 600, 450),
]
all_ok = True
for desc, x, y in checks:
    mo, mn = int(mask_old[y, x]), int(arr_new[y, x])
    ok = mo == mn
    all_ok = all_ok and ok
    print(f"{desc:<32} {mo:>8} {mn:>8}  {'✓' if ok else '✗'}")

diff = np.sum(mask_old != arr_new)
print(f"\n总不同像素: {diff} / {W*H} = {diff*100/(W*H):.4f}%")
print(f"\n算法一致性: {'✓ 完全一致（新挖角函数正确）' if all_ok and diff==0 else '✗ 仍有差异（需要排查）'}")
