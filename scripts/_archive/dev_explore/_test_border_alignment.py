"""
详细验证边框对齐情况
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
print("边框对齐验证")
print("=" * 60)

# 加载源图
src = load_source_image(src_path)
w, h = src.size
print(f"\n[源图] 尺寸: {w} x {h} px")

# 检测边框层
border_layers = _get_border_layers_robust(src, bg_color)
print(f"\n[边框检测] 检测到 {len(border_layers)} 层边框:")
cumulative = 0
for i, (color, thickness) in enumerate(border_layers):
    cm = thickness * 2.54 / dpi
    print(f"  第{i+1}层: 累计厚度={cumulative}px ({cumulative*2.54/dpi:.2f}cm), 厚度={thickness}px ({cm:.2f}cm)")
    cumulative += thickness

# 缩放到目标尺寸
target_w_px = w  # 使用原始尺寸
target_h_px = h

r_cm = 3.5
r_px = int(round(r_cm * dpi / 2.54))
print(f"\n[测试] 圆角半径: {r_cm}cm = {r_px}px")

# 应用圆角（仅右下角）
cropped = src.copy()
corners = {'tl': 0, 'tr': 0, 'bl': 0, 'br': r_cm}
result = apply_border_only_corners(cropped, corners, dpi, bg_color)

result_arr = np.array(result)

print(f"\n[右下角边框验证]")
# 检查右下角的边框层对齐
# 右下角位置
br_x_start = target_w_px - r_px - 100  # 稍微往左
br_x_end = target_w_px
br_y_start = target_h_px - r_px - 100  # 稍微往上
br_y_end = target_h_px

# 检查直线边框区域（右下角左侧）
linear_region = result_arr[target_h_px//4:target_h_px*3//4, target_w_px-200:target_w_px-100]

print(f"  直线边框区域（距右边缘100-200px）:")
print(f"    形状: {linear_region.shape}")

# 分析边框层位置
for depth in range(r_px + 50):
    col = target_w_px - 1 - depth
    if 0 <= col < target_w_px:
        # 检查该列的颜色分布
        col_data = result_arr[:, col]
        # 统计唯一颜色（近似）
        unique_colors = set()
        for pixel in col_data:
            # 量化到 30 的间隔来识别不同颜色
            quantized = (pixel[0]//30*30, pixel[1]//30*30, pixel[2]//30*30)
            unique_colors.add(quantized)
        
        if len(unique_colors) > 1:
            print(f"  深度 {depth}px: {len(unique_colors)} 种颜色")

print(f"\n[圆角弧线分析]")
# 检查圆弧上的边框对齐
# 圆心位置（右下角）
cx = target_w_px - r_px
cy = target_h_px - r_px

# 沿圆弧采样
for angle_deg in [315, 300, 285, 270]:
    angle_rad = np.radians(angle_deg)
    for r in [r_px * 0.3, r_px * 0.6, r_px * 0.9]:
        x = int(round(cx + r * np.cos(angle_rad)))
        y = int(round(cy + r * np.sin(angle_rad)))
        if 0 <= x < target_w_px and 0 <= y < target_h_px:
            pixel = result_arr[y, x]
            depth = r_px - r  # 到边框的深度
            layer = 0
            for i, (_, thickness) in enumerate(border_layers):
                if depth >= cumulative - thickness:
                    layer = i
                    break
                cumulative_val = sum(t for _, t in border_layers[:i+1])
            
            # 检查颜色是否属于预期的边框层
            print(f"  角度{angle_deg}°, 半径{r}px, 深度{depth:.0f}px, 像素值{pixel}, 预期层={layer}")

print("\n" + "=" * 60)
