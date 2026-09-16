# -*- coding: utf-8 -*-
"""诊断4：把真实识别结果送进几何层渲染，与真实阶梯形状做像素级比对（只读）。

关键：验证「success=True + g1_blocked=False」的结果实际渲染成什么。
"""
import sys
sys.path.insert(0, r'F:/SmartShapeCrop')
import numpy as np
from PIL import Image, ImageDraw
from core.geometry import RectShape, CropDesign

PXCM = 1166 / 93.5          # 草图实测比例
W, H = 1166, 543

# ---------- A. 真实阶梯形状（按标注精确绘制）----------
true_m = Image.new('L', (W, H), 0)
d = ImageDraw.Draw(true_m)
x75 = int(round(75 * PXCM))
x85 = int(round(85 * PXCM))
y65 = int(round(6.5 * PXCM))
y10 = int(round(10 * PXCM))
d.polygon([(0, 0), (x75, 0), (x75, y65), (x85, y65),
           (x85, y10), (W, y10), (W, H), (0, H)], fill=255)
true_a = np.array(true_m, dtype=bool)

# ---------- B. 现有识别结果渲染 ----------
from core.geometry import build_lshape_mask, fill_rect_mask
Z = {'tl': 0, 'tr': 0, 'bl': 0, 'br': 0}
outer = RectShape(0, 0, W, H)
# parse 结果: cuts_cm = [tr 10x6.5, tr 8.5x3.5]（厘米），且主角 cut_w=18.5 cut_h=10
# 注意 cuts_cm 里第二个也是 tr
cuts_cm = [('tr', 10.0, 6.5), ('tr', 8.5, 3.5)]
cuts_px = [(c, cw * PXCM, ch * PXCM) for c, cw, ch in cuts_cm]
cur_m = build_lshape_mask((W, H), outer, 'tr', 10.0 * PXCM, 6.5 * PXCM, Z, 255, cuts=cuts_px)
cur_a = np.array(cur_m, dtype=bool)

print('=== 形状对照（逐行最右保留像素 x）===')
print('  y_px | 真实阶梯 | 现有渲染 | 差异')
for yy in (20, 60, 100, 140, 200, 400, 520):
    t = np.where(true_a[yy])[0]
    c = np.where(cur_a[yy])[0]
    tv = int(t.max()) if len(t) else -1
    cv = int(c.max()) if len(c) else -1
    flag = 'OK' if abs(tv - cv) <= 2 else '<<< 不符'
    print('  %5d | %8d | %8d | %s' % (yy, tv, cv, flag))

print()
print('=== 面积对照 ===')
print('  真实保留面积  :', true_a.sum())
print('  现有渲染面积  :', cur_a.sum())
print('  差异          :', int(cur_a.sum()) - int(true_a.sum()))
inter = (true_a & cur_a).sum()
union = (true_a | cur_a).sum()
print('  IoU           : %.4f  (1.0=完全一致)' % (inter / union))
print()
print('=== 判定 ===')
if inter / union < 0.999:
    print('  ✗ 现有管线渲染结果与真实阶梯形状【不一致】')
    print('  ✗ 但 parse_lshape_sketch 返回 success=True, g1_blocked=False')
    print('  → 用户看到「识别成功」，拿到的是错误形状，且无任何警告')
