"""
简单的圆角处理分析
"""
import numpy as np
import math
from PIL import Image
import sys
sys.path.insert(0, '.')

from core.image_cropper import apply_rounded_corners

# 创建测试图像
w, h = 800, 1000
img = Image.new('RGB', (w, h), (255, 255, 255))
arr = np.array(img)

# 黑色边框 50px
arr[0:50, :] = (0, 0, 0)
arr[-50:, :] = (0, 0, 0)
arr[:, 0:50] = (0, 0, 0)
arr[:, -50:] = (0, 0, 0)

img = Image.fromarray(arr, 'RGB')

# 执行圆角处理
dpi = 150
corners = {'tl': 0, 'tr': 0, 'bl': 3.0, 'br': 0}
result = apply_rounded_corners(img, corners, dpi=dpi)
result_arr = np.array(result)

# 分析左下角圆角
cx, cy = 50, 950
r_px = int(3.0 / 2.54 * dpi)
border_depth = 50  # 边框厚度

print(f"圆角半径: {r_px}px")
print(f"边框厚度: {border_depth}px")
print(f"边框区域: 距中心 0-{border_depth}px")
print(f"圆角边缘: 距中心 {r_px}px")

# 检查不同距离的像素
print("\n各距离位置的像素颜色分布（角度=0°）:")
for dist in [0, 25, 50, 75, 100, 150, r_px - 1, r_px, r_px + 1]:
    x = cx + dist  # 角度=0°
    y = cy
    if 0 <= x < w and 0 <= y < h:
        pixel = result_arr[y, x]
        print(f"  距离={dist:3d}px: 位置=({x},{y}), 颜色={tuple(pixel)}")

# 分析边框区域（0-50px）的颜色分布
print("\n边框区域(0-50px)颜色分布:")
border_pixels = []
for angle_deg in range(0, 91, 10):
    angle_rad = math.radians(angle_deg)
    for dist in range(1, 50):
        x = int(cx + dist * math.cos(angle_rad))
        y = int(cy - dist * math.sin(angle_rad))
        if 0 <= x < w and 0 <= y < h:
            border_pixels.append(tuple(result_arr[y, x]))

if border_pixels:
    black_count = sum(1 for p in border_pixels if p[0] < 50 and p[1] < 50 and p[2] < 50)
    total = len(border_pixels)
    print(f"  总像素数: {total}")
    print(f"  黑色像素: {black_count} ({black_count/total*100:.1f}%)")

# 分析间隙区域（70-100px）的颜色分布
print("\n外部区域(70-100px)颜色分布:")
outer_pixels = []
for angle_deg in range(0, 91, 10):
    angle_rad = math.radians(angle_deg)
    for dist in range(70, min(100, r_px)):
        x = int(cx + dist * math.cos(angle_rad))
        y = int(cy - dist * math.sin(angle_rad))
        if 0 <= x < w and 0 <= y < h:
            outer_pixels.append(tuple(result_arr[y, x]))

if outer_pixels:
    white_count = sum(1 for p in outer_pixels if p[0] > 200 and p[1] > 200 and p[2] > 200)
    black_count = sum(1 for p in outer_pixels if p[0] < 50 and p[1] < 50 and p[2] < 50)
    total = len(outer_pixels)
    print(f"  总像素数: {total}")
    print(f"  白色像素: {white_count} ({white_count/total*100:.1f}%)")
    print(f"  黑色像素: {black_count} ({black_count/total*100:.1f}%)")

# 保存结果
result.save('simple_corner_test.png')
print("\n结果已保存至: simple_corner_test.png")
