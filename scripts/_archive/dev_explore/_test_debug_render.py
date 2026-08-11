"""
调试边框重绘逻辑
"""
import os
import sys
import numpy as np

sys.path.insert(0, r"D:\SmartShapeCrop")

from PIL import Image
from core.image_cropper import (
    load_source_image,
    apply_border_only_corners,
    _get_border_layers_robust,
)
from core.config import DEFAULT_BG_COLOR

src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
dpi = 150
bg_color = DEFAULT_BG_COLOR

print("=" * 60)
print("边框重绘逻辑调试")
print("=" * 60)

# 加载源图
src = load_source_image(src_path)
w, h = src.size

# 缩放到目标尺寸
target_w_px = w
target_h_px = h

r_cm = 3.5
r_px = int(round(r_cm * dpi / 2.54))

# 直接调用 _redraw_border_on_corner 并添加调试信息
from core.corner.sector_render import _redraw_border_on_corner

# 创建测试图像
test_img = src.copy()
result = apply_border_only_corners(test_img, {'tl': 0, 'tr': 0, 'bl': 0, 'br': r_cm}, dpi, bg_color)

result_arr = np.array(result)

# 计算右下角圆心
cx = w - r_px
cy = h - r_px

print(f"\n[右下角分析]")
print(f"  圆心: ({cx}, {cy})")
print(f"  圆角半径: {r_px}px")

# 在圆角弧线上采样
print(f"\n  [圆角弧线采样]")
for angle_deg in [280, 290, 300, 310, 320, 330, 340, 350]:
    angle_rad = np.radians(angle_deg)
    # 注意：在屏幕坐标系中，y 轴向下
    # 对于右下角，角度范围是 270°~360°
    x = int(round(cx + r_px * np.cos(angle_rad)))
    y = int(round(cy + r_px * np.sin(angle_rad)))
    
    if 0 <= x < w and 0 <= y < h:
        pixel = result_arr[y, x]
        print(f"    角度{angle_deg}°: 位置({x}, {y}), 像素值{pixel}")

# 检查原图在相同位置的颜色
print(f"\n  [原图采样]")
src_arr = np.array(src)
for angle_deg in [280, 290, 300, 310, 320, 330, 340, 350]:
    angle_rad = np.radians(angle_deg)
    x = int(round(cx + r_px * np.cos(angle_rad)))
    y = int(round(cy + r_px * np.sin(angle_rad)))
    
    if 0 <= x < w and 0 <= y < h:
        pixel = src_arr[y, x]
        print(f"    角度{angle_deg}°: 位置({x}, {y}), 像素值{pixel}")

# 检查直线边框区域
print(f"\n  [直线边框区域]")
# 右下角左侧的直线边框
for depth in [10, 50, 100, 150, 200]:
    x = w - 1 - depth
    y = h - 1 - depth
    if 0 <= x < w and 0 <= y < h:
        src_pixel = src_arr[y, x]
        res_pixel = result_arr[y, x]
        print(f"    深度{depth}px: 原图{src_pixel}, 结果{res_pixel}")

print("\n" + "=" * 60)
