# -*- coding: utf-8 -*-
"""单独测试 PIL ImageDraw.pieslice 的行为（确定它是否按预期只填充扇形区域）"""
import numpy as np
from PIL import Image, ImageDraw

# 做一个小画布，模拟 Layer6
W, H = 200, 200
mask = Image.new('L', (W, H), 255)
draw = ImageDraw.Draw(mask)

# Layer6 rect 相当于缩小版：(22, 46, 178, 160) 小一点
x1, y1, x2, y2 = 22, 46, 178, 160
rw, rh = x2-x1, y2-y1  # 156, 114
r = 57  # min(502, min(156,114)//2=57) → safe_r=57

# 1. 先挖正方形
sq = [x2 - r, y2 - r, x2, y2]
print(f"sq={sq}")
draw.rectangle(sq, fill=0)

# 2. 填回 sector（0°~90°，右下 1/4 圆）
pb = [x2 - 2*r, y2 - 2*r, x2, y2]
print(f"pieslice_bbox={pb}, start=0, end=90")
draw.pieslice(pb, start=0, end=90, fill=255)

arr = np.array(mask)
# 中心：cx = x2-r = 178-57=121, cy=y2-r=160-57=103
cx, cy = x2-r, y2-r
print(f"扇形圆心=({cx},{cy}), 半径={r}")

# 挑几个测试点（包括 sector 内/外的）
test_points = [
    ('正方形外 左上', 100, 80, None),
    ('正方形内 扇形内 dx=30,dy=20', cx+30, cy+20, True),
    ('正方形内 扇形外 dx=r=57,dy=r=57 顶点', cx+57, cy+57, False),
    ('正方形内 扇形外 dx=56,dy=20', cx+56, cy+20, None),  # 距离=√(56²+20²)=59.46>57 → False
    ('正方形内 扇形外 dx=20,dy=56', cx+20, cy+56, None),  # 距离=√(20²+56²)=59.46>57 → False
]

print("\n各点像素值（0=被挖，255=被填回/保留）:")
for desc, x, y, expect_in_sector in test_points:
    dx, dy = x-cx, y-cy
    dist = (dx*dx + dy*dy)**0.5
    in_sq = sq[0] <= x < sq[2] and sq[1] <= y < sq[3]
    v = int(arr[y, x])
    mark = ''
    if expect_in_sector is True:
        mark = ' (期望=255)'
    elif expect_in_sector is False:
        mark = ' (期望=0，因为扇形外)'
    print(f"  ({x},{y}) {desc:<40} in_sq={in_sq} dx={dx} dy={dy} dist={dist:.1f} val={v}{mark}")

# 画为可视文件：0→红，255→白
vis = np.zeros((H, W, 3), dtype=np.uint8)
vis[arr == 0] = [200, 30, 30]  # 被挖掉=红色
vis[arr == 255] = [255, 255, 255]  # 保留=白色
Image.fromarray(vis).save(r'd:\SmartShapeCrop\test_cropper_output\debug_pieslice_visual.png')
print('\n保存可视化：debug_pieslice_visual.png  (红色=被挖=mask=0)')
