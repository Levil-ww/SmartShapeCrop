# -*- coding: utf-8 -*-
"""Debug: 直接对比原 apply_rounded_corners 和新 _carve_L_corners_on_mask 生成的 mask 差异"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from PIL import Image, ImageDraw

from core.image_cropper import apply_rounded_corners

DPI = 150
radius_cm = 8.5
r_px = max(0, int(round(radius_cm * DPI / 2.54)))
W, H = 1200, 900

# 1. 原 apply_rounded_corners 生成结果，通过对比原图(全黑)和结果，反推 mask
full_black = Image.new('RGB', (W, H), (0, 0, 0))
res_old = apply_rounded_corners(full_black.copy(), {'br': radius_cm}, dpi=DPI)
res_arr_old = np.array(res_old)
# 白色(255,255,255) 表示被裁掉 (mask=0)，黑色(0,0,0) 表示保留 (mask=255)
mask_old = 255 - np.all(res_arr_old == 255, axis=-1).astype(np.uint8) * 255

# 2. 新算法 _carve_L_corners_on_mask 直接生成 mask
# 先把完整函数复制一遍（独立于 detect_layers）
def _carve_debug(draw, canvas_w, canvas_h, rect, corners_px):
    x1, y1, x2, y2 = rect
    rw = x2 - x1
    rh = y2 - y1
    safe_corners = {}
    for ck, r_px_v in corners_px.items():
        max_r = max(1, min(rw, rh) // 2)
        safe_corners[ck] = max(0, min(r_px_v, max_r))
    for ck, r_px_v in safe_corners.items():
        if r_px_v <= 0: continue
        if ck == 'tl':
            sq = [x1, y1, x1 + r_px_v, y1 + r_px_v]
            pieslice_bbox = [x1, y1, x1 + 2 * r_px_v, y1 + 2 * r_px_v]
            start, end = 180, 270
        elif ck == 'tr':
            sq = [x2 - r_px_v, y1, x2, y1 + r_px_v]
            pieslice_bbox = [x2 - 2 * r_px_v, y1, x2, y1 + 2 * r_px_v]
            start, end = 270, 360
        elif ck == 'bl':
            sq = [x1, y2 - r_px_v, x1 + r_px_v, y2]
            pieslice_bbox = [x1, y2 - 2 * r_px_v, x1 + 2 * r_px_v, y2]
            start, end = 90, 180
        else:
            sq = [x2 - r_px_v, y2 - r_px_v, x2, y2]
            pieslice_bbox = [x2 - 2 * r_px_v, y2 - 2 * r_px_v, x2, y2]
            start, end = 0, 90
        sq_safe = [max(0, sq[0]), max(0, sq[1]), min(canvas_w, sq[2]), min(canvas_h, sq[3])]
        if sq_safe[2] > sq_safe[0] and sq_safe[3] > sq_safe[1]:
            draw.rectangle(sq_safe, fill=0)
        safe_bbox = [max(0, pieslice_bbox[0]), max(0, pieslice_bbox[1]),
                     min(canvas_w, pieslice_bbox[2]), min(canvas_h, pieslice_bbox[3])]
        if safe_bbox[2] > safe_bbox[0] and safe_bbox[3] > safe_bbox[1]:
            draw.pieslice(safe_bbox, start=start, end=end, fill=255)

mask_new = Image.new('L', (W, H), 255)
draw_new = ImageDraw.Draw(mask_new)
full_rect = (0, 0, W, H)  # 画布尺寸语义
_carve_debug(draw_new, W, H, full_rect, {'br': r_px})
arr_new = np.array(mask_new)

# 3. 比较关键位置
print(f"r_px = {r_px}")
print(f"{'位置':<30} {'旧 mask':>10} {'新 mask':>10} 一致?")
checks = [
    ('右下顶点 (1199,899)', 1199, 899),
    ('右边缘 L形 (1198,447)', 1198, 447),
    ('下边缘 L形 (747,898)', 747, 898),
    ('扇形内部 (948,648)', 948, 648),
    ('图中心 (600,450)', 600, 450),
    ('离右侧很近 (1199,500)', 1199, 500),
]
for desc, x, y in checks:
    mo = int(mask_old[y, x])
    mn = int(arr_new[y, x])
    ok = '✓' if mo == mn else '✗'
    print(f"{desc:<30} {mo:>10} {mn:>10} {ok}")

# 统计总差异
diff_mask = mask_old != arr_new
diff_count = int(np.sum(diff_mask))
print(f"\n总不同像素数: {diff_count} / {W*H} = {diff_count*100/(W*H):.4f}%")

if diff_count > 0:
    # 找一个不同点
    ys, xs = np.where(diff_mask)
    for i in range(min(5, len(xs))):
        print(f"  diff[{i}] ({xs[i]},{ys[i]}) old={mask_old[ys[i],xs[i]]} new={arr_new[ys[i],xs[i]]}")
    # 保存差异图
    diff_img = np.zeros((H, W, 3), dtype=np.uint8)
    diff_img[diff_mask] = [255, 0, 0]
    Image.fromarray(diff_img).save(r'd:\SmartShapeCrop\test_cropper_output\debug_mask_diff.png')
    Image.fromarray(mask_old).save(r'd:\SmartShapeCrop\test_cropper_output\debug_mask_old.png')
    Image.fromarray(arr_new).save(r'd:\SmartShapeCrop\test_cropper_output\debug_mask_new.png')
    print('差异图保存到 test_cropper_output/debug_mask_*.png')
