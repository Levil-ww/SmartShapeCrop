# -*- coding: utf-8 -*-
"""测试 pieslice 边界像素是否被填充"""
import numpy as np
from PIL import Image, ImageDraw

W, H = 2421, 3248

# 测试层0的 inner_mask：矩形 offset=47，圆角半径=455
# 矩形：x=77, y=77, w=2267, h=3094
# 右下角圆角中心: (1889, 2716), 半径=455
rect_x, rect_y, rect_w, rect_h = 77, 77, 2267, 3094
r = 455
cx, cy = rect_x + rect_w - r, rect_y + rect_h - r  # = (1889, 2716)

# 创建 mask
mask = Image.new('L', (W, H), 0)
draw = ImageDraw.Draw(mask)

# 1. 填充矩形
draw.rectangle([rect_x, rect_y, rect_x + rect_w, rect_y + rect_h], fill=255)

# 2. 挖掉角落正方形
sq = [rect_x + rect_w - r, rect_y + rect_h - r, rect_x + rect_w, rect_y + rect_h]
draw.rectangle(sq, fill=0)

# 3. 填回 pieslice (bbox 以圆心为中心)
# 正确的 bbox: [cx - r, cy - r, cx + r, cy + r]
bbox = [cx - r, cy - r, cx + r, cy + r]
draw.pieslice(bbox, start=0, end=90, fill=255)

arr = np.array(mask)

# 检查关键像素
test_points = [
    (cx + r, cy, "0°边界 (right)"),      # (2344, 2716)
    (cx, cy + r, "90°边界 (bottom)"),    # (1889, 3171)
    (cx + int(r*0.707), cy + int(r*0.707), "45°边界"),  # (2210, 3037)
    (cx + int(r*0.5), cy + int(r*0.866), "60°边界"),  # 半径 r 处
    (cx + r - 1, cy, "0° 内1像素"),      # (2343, 2716)
    (cx + r + 1, cy, "0° 外1像素"),      # (2345, 2716) - 应该为0
    (cx + int((r-1)*0.707), cy + int((r-1)*0.707), "45° 内1像素"),  # 半径 r-1
    (cx + int((r+1)*0.707), cy + int((r+1)*0.707), "45° 外1像素"),  # 半径 r+1
]

print(f"圆心: ({cx}, {cy}), 半径: {r}")
print(f"bbox: {bbox}")
print(f"\n测试像素:")
for px, py, desc in test_points:
    v = arr[py, px]
    print(f"  ({px},{py}) {desc}: value={v}, is_filled={v>0}")

# 关键问题：当前 _CORNER_PIESLICE_PARAMS 使用的 bbox 格式
# 'br': lambda x, y, w, h, r: ([x + w - 2*r, y + h - 2*r, x + w, y + h], 0, 90)
# 这里 x, y 是矩形左上角，w, h 是矩形宽高
# 对于 rect_x=77, rect_y=77, rect_w=2267, rect_h=3094, r=455:
# bbox = [77+2267-910, 77+3094-910, 77+2267, 77+3094] = [1434, 2261, 2344, 3171]
# 中心 = ((1434+2344)/2, (2261+3171)/2) = (1889, 2716) ✓
# 半径 = (2344-1434)/2 = 455 ✓

# 测试当前的 bbox 格式
print(f"\n--- 对比当前 bbox 格式 ---")
bbox_current = [rect_x + rect_w - 2*r, rect_y + rect_h - 2*r, rect_x + rect_w, rect_y + rect_h]
print(f"当前 bbox: {bbox_current}")
print(f"中心: ({(bbox_current[0]+bbox_current[2])/2}, {(bbox_current[1]+bbox_current[3])/2})")
print(f"半径: {(bbox_current[2]-bbox_current[0])/2}")

mask2 = Image.new('L', (W, H), 0)
draw2 = ImageDraw.Draw(mask2)
draw2.rectangle([rect_x, rect_y, rect_x + rect_w, rect_y + rect_h], fill=255)
draw2.rectangle(sq, fill=0)
draw2.pieslice(bbox_current, start=0, end=90, fill=255)
arr2 = np.array(mask2)

print(f"\n使用当前 bbox 的像素值:")
for px, py, desc in test_points:
    v = arr2[py, px]
    print(f"  ({px},{py}) {desc}: value={v}, is_filled={v>0}")

# 检查 pieslice 是否真的填充了边界
print(f"\n--- 精确检查边界 ---")
# 对于 0° 方向（右边界），检查 x=cx+r 处的列
print(f"0° 方向（垂直右边界列 x={cx+r}）:")
for dy in range(-2, 3):
    y = cy + dy
    if 0 <= y < H:
        v = arr[y, cx+r]
        v2 = arr2[y, cx+r]
        print(f"  y={y} (dy={dy}): direct_bbox={v}, current_bbox={v2}")

# 对于 45° 方向，检查半径 r 和 r±1
print(f"\n45° 方向边界:")
for dr in range(-2, 3):
    rr = r + dr
    x = cx + int(rr * 0.707)
    y = cy + int(rr * 0.707)
    if 0 <= x < W and 0 <= y < H:
        v = arr[y, x]
        v2 = arr2[y, x]
        actual_dist = ((x-cx)**2 + (y-cy)**2)**0.5
        print(f"  dr={dr}, dist={actual_dist:.1f}: direct_bbox={v}, current_bbox={v2}")

# 保存对比图
vis = np.zeros((H, W, 3), dtype=np.uint8)
vis[arr > 0] = [0, 255, 0]  # 绿色：直接 bbox 填充
vis[arr2 > 0] = [255, 0, 0]  # 红色：当前 bbox 填充
vis_path = "d:/SmartShapeCrop/test_cropper_output/test_pieslice_debug.png"
Image.fromarray(vis).save(vis_path)
print(f"\n可视化保存到: {vis_path}")
