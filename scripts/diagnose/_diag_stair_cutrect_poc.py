# -*- coding: utf-8 -*-
"""一期验收：手写 CutRect 列表 → build_lshape_mask 渲染两级台阶，
与真实阶梯形状做逐行 + IoU 比对（对照 2026-09-16 实测版报告的真实样本）。
"""
import sys
sys.path.insert(0, r'F:/SmartShapeCrop')
import numpy as np
from PIL import Image, ImageDraw
from core.geometry import RectShape, CutRect, build_lshape_mask

PXCM = 1166 / 93.5
W, H = 1166, 543

# ---------- A. 真实阶梯（与 _diag_stair_render_compare.py 同一多边形）----------
x75 = int(round(75 * PXCM)); x85 = int(round(85 * PXCM))
y65 = int(round(6.5 * PXCM)); y10 = int(round(10 * PXCM))
true_m = Image.new('L', (W, H), 0)
d = ImageDraw.Draw(true_m)
d.polygon([(0, 0), (x75, 0), (x75, y65), (x85, y65),
           (x85, y10), (W, y10), (W, H), (0, H)], fill=255)
true_a = np.array(true_m, dtype=bool)

# ---------- B. 手写 CutRect 列表（厘米）→ 像素 dict 规格 → build_lshape_mask ----------
cut_rects_cm = [CutRect('tr', 0, 0, 18.5, 6.5), CutRect('tr', 0, 6.5, 8.5, 3.5)]
Z = {'tl': 0, 'tr': 0, 'bl': 0, 'br': 0}
cuts_px = [{'corner': c.anchor, 'cut_w': c.w_cm * PXCM, 'cut_h': c.h_cm * PXCM,
            'offset_x': c.offset_x_cm * PXCM, 'offset_y': c.offset_y_cm * PXCM}
           for c in cut_rects_cm]
cur_m = build_lshape_mask((W, H), RectShape(0, 0, W, H), 'tr',
                          cut_rects_cm[0].w_cm * PXCM, cut_rects_cm[0].h_cm * PXCM,
                          Z, 255, cuts=cuts_px)
cur_a = np.array(cur_m, dtype=bool)

print('=== 逐行最右保留像素 x ===')
bad = 0
for yy in (20, 60, 100, 140, 200, 400, 520):
    t = np.where(true_a[yy])[0]; c = np.where(cur_a[yy])[0]
    tv = int(t.max()) if len(t) else -1
    cv = int(c.max()) if len(c) else -1
    flag = 'OK' if abs(tv - cv) <= 2 else '<<< 不符'
    if flag != 'OK':
        bad += 1
    print('  %5d | %8d | %8d | %s' % (yy, tv, cv, flag))

inter = (true_a & cur_a).sum(); union = (true_a | cur_a).sum()
iou = inter / union
print()
print('=== 面积/IoU ===')
print('  真实保留面积 :', int(true_a.sum()))
print('  CutRect 渲染 :', int(cur_a.sum()))
print('  IoU          : %.6f  (1.0=完全一致; 旧模型实测 0.9791)' % iou)
print()
if iou >= 0.999 and bad == 0:
    print('[PASS] 一期验收通过：手写 CutRect 列表渲染出正确两级台阶')
else:
    print('[FAIL] 一期验收未通过')
    sys.exit(1)
