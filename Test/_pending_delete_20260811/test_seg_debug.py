# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from PIL import Image, ImageDraw

def make_mask(size):
    return Image.new('L', size, 0)

def _draw_rounded_seg(mask_img, cx, cy, radius, corner_key, fill_val):
    draw = ImageDraw.Draw(mask_img)
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
    if corner_key == 'tl':     start, end = 180, 270
    elif corner_key == 'tr':   start, end = 270, 360
    elif corner_key == 'bl':   start, end = 90, 180
    else:                      start, end = 0, 90
    draw.pieslice(bbox, start=start, end=end, fill=fill_val)

# 测试右下角
W, H = 2421, 3248
cx, cy = 1889, 2716
R = 505

img = make_mask((W, H))
_draw_rounded_seg(img, cx, cy, R, 'br', 255)
arr = np.array(img)

print(f"圆心: ({cx}, {cy}), R={R}")
print(f"bbox: [{cx-R}, {cy-R}, {cx+R}, {cy+R}]")
print(f"start=0, end=90 (右下角)")

# 检查关键位置
print(f"\n关键位置检查:")
# 圆心右边 (应该在扇形内，True)
print(f"  ({cx+10}, {cy}) -> {arr[cy, cx+10]}  (应为 255)")
# 圆心下边 (应该在扇形内，True)
print(f"  ({cx}, {cy+10}) -> {arr[cy+10, cx]}  (应为 255)")
# 45° 方向
import math
x45 = int(cx + R * 0.707)
y45 = int(cy + R * 0.707)
if 0 <= y45 < H and 0 <= x45 < W:
    print(f"  ({x45}, {y45}) dist≈{R*0.707:.0f} -> {arr[y45, x45]}  (应为 255)")

# 检查扇形内的 True 像素数量
total_true = np.sum(arr > 0)
print(f"\n非零像素总数: {total_true}")
print(f"预期的扇形面积: {3.14159 * R * R / 4:.0f} ≈ {3.14159 * R * R / 4:.0f}")

# 检查是否在正确的象限
# 右下角的扇形应该在 cx+R 方向 (右) 和 cy+R 方向 (下)
right_count = np.sum(arr[cy:cy+R+1, cx:cx+R+1] > 0)
left_count = np.sum(arr[cy-R:cy+1, cx-R:cx+1] > 0)
print(f"\n右下象限 (应主要在此): {right_count} 个 True")
print(f"左上象限 (应在此为 0): {left_count} 个 True")

# 可视化：保存一个小的切片
small = arr[cy-50:cy+R+50, cx-50:cx+R+50]
# 放大显示
small_img = Image.fromarray(small)
out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'test_cropper_output', 'test_seg_debug.png')
small_img.save(out_path)
print(f"\n调试图片保存到: {out_path}")

# 额外测试：用更简单的方式画扇形
img2 = make_mask((200, 200))
draw = ImageDraw.Draw(img2)
# 在小画布中心画
draw.pieslice([10, 10, 110, 110], start=0, end=90, fill=255)
arr2 = np.array(img2)
print(f"\n简单测试 (200x200 画布, bbox=[10,10,110,110], 0-90°):")
print(f"  非零像素: {np.sum(arr2 > 0)}")
print(f"  圆心(60,60)右边(70,60): {arr2[60, 70]}")
print(f"  圆心(60,60)下边(60,70): {arr2[70, 60]}")
print(f"  45°方向(95,95): {arr2[95, 95]}")
# 应该在右下角象限
print(f"  右下角象限 (60-110, 60-110) 非零: {np.sum(arr2[60:111, 60:111] > 0)}")
# 不应该在左上角象限
print(f"  左上角象限 (10-60, 10-60) 非零: {np.sum(arr2[10:61, 10:61] > 0)}")
