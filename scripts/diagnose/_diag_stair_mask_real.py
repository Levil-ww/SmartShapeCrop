# -*- coding: utf-8 -*-
"""诊断2：把识别结果喂给几何层，看能否还原真实阶梯形状（只读）。"""
import sys
sys.path.insert(0, r'F:/SmartShapeCrop')
import numpy as np
from PIL import Image
from core.geometry import RectShape, CropDesign

# 识别层给出的两个 cut（像素坐标，concave 点已知）
#   -> corner=tr cut_w=310 cut_h=91  concave=(950,137)
#   -> corner=tr cut_w=142 cut_h=148 concave=(1118,194)
# bbox = (94,46) - (1260,589)  →  W=1166  H=543
# 草图比例: 1166px / 93.5cm = 12.47 px/cm

print('=== 真实阶梯 vs 识别结果 对照 ===')
print()
print('真实顶点(cm):')
print('  (0,0) (75,0) (75,6.5) (85,6.5) (85,10) (93.5,10) (93.5,55) (0,55)')
print('真实结构: 2 个凹角 —— (75,6.5) 与 (85,10)  都是「向右下逐级内收」')
print()

# 现有 corner 语义下，两个 cut 都报 tr
# tr 的 cut_rect = (right - cw, top, cw, ch)
# 两个都贴「上边」→ 第二个的高度 148 会盖住第一个 → 几何错误
print('--- 用现有 corner 语义构造 mask ---')
from core.geometry import build_lshape_mask
W, H = 1166, 543
outer = RectShape(0, 0, W, H)
Z = {'tl': 0, 'tr': 0, 'bl': 0, 'br': 0}

cuts_px = [('tr', 310.0, 91.0), ('tr', 142.0, 148.0)]
m = build_lshape_mask((W, H), outer, 'tr', 310.0, 91.0, Z, 255, cuts=cuts_px)
a = np.array(m, dtype=bool)

print('两个 cut 都是 tr 时，build_lshape_mask 产出:')
for yy in (60, 150, 250, 400, 530):
    row = np.where(a[yy])[0]
    print('   y=%-4d 保留区最右 x=%s' % (yy, int(row.max()) if len(row) else 'N/A'))

print()
print('真实形状在各行应有的最右 x（按 px/cm=12.47 换算）:')
pxcm = 1166 / 93.5
for yy_cm in (2, 8, 12, 30, 50):
    yy = int(yy_cm * pxcm)
    if yy_cm <= 6.5:
        xr = 75 * pxcm
    elif yy_cm <= 10:
        xr = 85 * pxcm
    else:
        xr = 93.5 * pxcm
    print('   y=%2dcm (px %3d) 真实最右 x=%.0f' % (yy_cm, yy, xr))

print()
print('=== 结论 ===')
print('识别层 n_detected=2 说明「两个台阶的凹角都被找到了」——')
print('但两个都标成 corner=tr，而 corner 只决定「cut 贴哪两条外廓边」，')
print('无法表达「第二级比第一级更靠右下」的错位关系。')
