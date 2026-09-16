# -*- coding: utf-8 -*-
"""诊断5：输出真实阶梯 vs 现有渲染 的可视化比对图（只读源码）。"""
import sys
sys.path.insert(0, r'F:/SmartShapeCrop')
import numpy as np
from PIL import Image, ImageDraw
from core.geometry import RectShape, build_lshape_mask

PXCM = 1166 / 93.5
W, H = 1166, 543
OUT = r'F:/SmartShapeCrop/scripts/diagnose/_stair_vs_current.png'

x75 = int(round(75 * PXCM)); x85 = int(round(85 * PXCM))
y65 = int(round(6.5 * PXCM)); y10 = int(round(10 * PXCM))

# A. 真实阶梯
A = Image.new('RGB', (W, H), (245, 245, 245))
da = ImageDraw.Draw(A)
da.polygon([(0, 0), (x75, 0), (x75, y65), (x85, y65),
            (x85, y10), (W, y10), (W, H), (0, H)], fill=(200, 225, 255),
           outline=(30, 90, 200), width=4)

# B. 现有渲染
Z = {'tl': 0, 'tr': 0, 'bl': 0, 'br': 0}
m = build_lshape_mask((W, H), RectShape(0, 0, W, H), 'tr', 10.0 * PXCM, 6.5 * PXCM, Z, 255,
                      cuts=[('tr', 10.0 * PXCM, 6.5 * PXCM), ('tr', 8.5 * PXCM, 3.5 * PXCM)])
b = np.array(m, dtype=bool)
B = Image.new('RGB', (W, H), (245, 245, 245))
Bb = np.array(B)
Bb[b] = (255, 225, 200)
B = Image.fromarray(Bb)
db = ImageDraw.Draw(B)
# 叠加真实轮廓虚线做参照
pts = [(0, 0), (x75, 0), (x75, y65), (x85, y65), (x85, y10), (W, y10), (W, H), (0, H)]
for i in range(len(pts)):
    p, q = pts[i], pts[(i + 1) % len(pts)]
    # 手绘虚线
    L = max(1, int(np.hypot(q[0] - p[0], q[1] - p[1])))
    for t in range(0, L, 16):
        t2 = min(L, t + 8)
        x1 = p[0] + (q[0] - p[0]) * t / L; y1 = p[1] + (q[1] - p[1]) * t / L
        x2 = p[0] + (q[0] - p[0]) * t2 / L; y2 = p[1] + (q[1] - p[1]) * t2 / L
        db.line([(x1, y1), (x2, y2)], fill=(0, 160, 0), width=3)

# 拼图：上下两幅 + 标签
LBL = 46
canvas = Image.new('RGB', (W, H * 2 + LBL * 2 + 12), (255, 255, 255))
dc = ImageDraw.Draw(canvas)
dc.text((12, 12), 'A. 真实阶梯形状 (75 / 6.5 / 10 / 3.5 / 8.5 / 45)', fill=(0, 0, 0))
canvas.paste(A, (0, LBL))
dc.text((12, LBL + H + 12), 'B. 现有管线渲染结果 (蓝=实际输出, 绿虚线=真实轮廓参照)', fill=(0, 0, 0))
canvas.paste(B, (0, LBL * 2 + H + 12))
canvas.save(OUT)
print('已输出:', OUT)
print()
print('视觉差异说明:')
print('  A 图右上角是「两级台阶」')
print('  B 图只挖了「一个较大的矩形角」—— 第二级台阶的位置/尺寸丢失')
print('  即: 识别报 success, 但渲染出的形状不是用户要的阶梯')
