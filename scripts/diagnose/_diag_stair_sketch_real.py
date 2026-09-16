# -*- coding: utf-8 -*-
"""诊断：真实「单边阶梯 L 形」草图在现有识别层的表现（只读，不改源码）。"""
import sys
sys.path.insert(0, r'F:/SmartShapeCrop')
import numpy as np
import cv2
from PIL import Image

P = r'E:\智能裁剪设计器\测试草图文件-L型挖角\吸水皮革-定制-裁剪有图-安妮森林;55x93.5CM裁剪有图.png'

img = Image.open(P).convert('L')
g = np.array(img)
print('草图尺寸:', img.size, ' 灰度范围:', g.min(), g.max())

bw = (g < 200).astype(np.uint8) * 255
cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
c = cnts[0]
area = cv2.contourArea(c)
x, y, w, h = cv2.boundingRect(c)
print('最大轮廓: area=%.0f bbox=(%d,%d,%d,%d)' % (area, x, y, w, h))

peri = cv2.arcLength(c, True)
print()
print('--- approxPolyDP 多尺度顶点数 ---')
for eps in (0.005, 0.01, 0.02, 0.03):
    ap = cv2.approxPolyDP(c, eps * peri, True)
    pts = ap.reshape(-1, 2)
    print('  eps=%-6s 顶点数=%d' % (eps, len(pts)))
    if len(pts) <= 14:
        print('      ', [tuple(int(v) for v in pt) for pt in pts])

print()
print('--- 现有 _detect_lshape_geometry 结果 ---')
from services.sketch_parser.lshape_sketch_parser import _detect_lshape_geometry
geo = _detect_lshape_geometry(cv2, g)
if geo:
    print('  corner=%s  cut_w_px=%.1f  cut_h_px=%.1f'
          % (geo['corner'], geo['cut_w_px'], geo['cut_h_px']))
    print('  outer_w_px=%.1f  outer_h_px=%.1f' % (geo['outer_w_px'], geo['outer_h_px']))
    print('  n_verts=%d  n_detected=%d' % (geo['n_verts'], geo['n_detected']))
    for ac in geo['all_corners']:
        print('    -> corner=%-3s cut_w_px=%-8.1f cut_h_px=%-8.1f concave=%s score=%.2f'
              % (ac['corner'], ac['cut_w_px'], ac['cut_h_px'], ac['concave'], ac['score']))
    print('  verts:', geo['verts'])
else:
    print('  !! 几何检测返回 None')

print()
print('--- 真实几何：按 75/6.5/10/3.5/8.5/45 标注还原的顶点序列 ---')
print('  (0,0) (75,0) (75,6.5) (85,6.5) (85,10) (93.5,10) (93.5,55) (0,55)')
print('  校验: 6.5+3.5=10 → 55-10=45 (右边✓)   75+10+8.5=93.5 (下边✓)')
