"""
测试修复后的边框颜色采样逻辑
"""
import os
import sys
import numpy as np

sys.path.insert(0, r"D:\SmartShapeCrop")

from PIL import Image
from core.image_cropper import (
    load_source_image,
    _get_border_layers_robust,
)
from core.config import DEFAULT_BG_COLOR

src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
dpi = 150
bg_color = DEFAULT_BG_COLOR

print("=" * 60)
print("验证修复后的边框颜色映射")
print("=" * 60)

# 加载源图
src = load_source_image(src_path)
w, h = src.size
src_arr = np.array(src)

# 检测边框层
border_layers = _get_border_layers_robust(src, bg_color)
print(f"\n[边框检测] 检测到 {len(border_layers)} 层边框:")
cumulative = 0
for i, (color, thickness) in enumerate(border_layers):
    cm = thickness * 2.54 / dpi
    print(f"  第{i+1}层: 深度[{cumulative}, {cumulative+thickness})px, 厚度={thickness}px ({cm:.2f}cm), 颜色={color}")
    cumulative += thickness

# 测试右下角
R_total = 207  # 3.5cm at 150dpi
print(f"\n[圆角设置] 半径={R_total}px ({R_total * 2.54 / dpi:.2f}cm)")

# 构建累积厚度
cumulative_depths = [0]
for _, thickness in border_layers:
    cumulative_depths.append(cumulative_depths[-1] + thickness)
total_border_depth = cumulative_depths[-1]

# 计算每层的有效圆角半径
layer_effective_radii = []
for cum in cumulative_depths[:-1]:
    R_eff = max(0, R_total - cum)
    layer_effective_radii.append(R_eff)
    print(f"  第{len(layer_effective_radii)}层有效半径: {R_eff}px")

# 构建颜色采样映射表
depth_mapping = {}
for d in range(min(R_total + 1, total_border_depth + 100)):
    src_depth = d
    for i, cum in enumerate(cumulative_depths[:-1]):
        thickness = border_layers[i][1]
        d_start = cum
        d_end = cum + thickness
        R_eff = layer_effective_radii[i]
        
        if d_start <= d < d_end:
            if R_eff <= 0:
                src_depth = min(d_end, total_border_depth)
            elif d_start + R_eff <= d_end:
                if d < d_start + R_eff:
                    src_depth = min(d_start + R_eff, total_border_depth)
                else:
                    src_depth = min(d, total_border_depth)
            else:
                src_depth = min(d, total_border_depth)
            break
    
    depth_mapping[d] = src_depth

# 验证映射结果
print(f"\n[颜色映射验证]")
print(f"  结果图深度 -> 原图采样深度 -> 采样颜色所属层")
print(f"  ----------------------------------------")

# 对几个关键深度验证
for d in [0, 50, 100, 150, 157, 170, 187, 200, 207]:
    if d >= len(depth_mapping):
        continue
    
    src_d = depth_mapping[d]
    
    # 确定 src_d 所属的层
    layer_info = "背景"
    for i, (_, thickness) in enumerate(border_layers):
        cum = cumulative_depths[i]
        if cum <= src_d < cum + thickness:
            layer_info = f"第{i+1}层"
            break
        if i == len(border_layers) - 1 and src_d >= cumulative_depths[-1]:
            layer_info = "背景"
    
    # 采样原图颜色
    if src_d < total_border_depth:
        # 采样右下角的颜色
        x = w - 1 - src_d
        y = h - 1 - src_d
        if 0 <= x < w and 0 <= y < h:
            pixel = tuple(src_arr[y, x].tolist())
            print(f"  d={d:3d}px -> src_d={src_d:3d}px -> {layer_info}, 颜色={pixel}")
        else:
            print(f"  d={d:3d}px -> src_d={src_d:3d}px -> {layer_info}, 位置无效")
    else:
        print(f"  d={d:3d}px -> src_d={src_d:3d}px -> {layer_info}")

# 关键验证：在结果图中深度 0-157px 的区域应该显示第2层的颜色
print(f"\n[关键验证]")
print(f"  结果图中深度 0-157px 的区域应该显示第2层的颜色(米色)")
print(f"  结果图中深度 157-207px 的区域应该显示第3层的颜色(浅米色)")

# 检查映射是否正确
mapped_colors = set()
for d in range(min(157, len(depth_mapping))):
    src_d = depth_mapping[d]
    # 确定 src_d 所属的层
    for i, (_, thickness) in enumerate(border_layers):
        cum = cumulative_depths[i]
        if cum <= src_d < cum + thickness:
            mapped_colors.add(f"d={d}->{src_d}px:第{i+1}层")
            break

print(f"\n  深度 0-157px 的映射结果:")
# 只显示几个典型值
for d in [0, 50, 100, 150, 156]:
    if d < len(depth_mapping):
        src_d = depth_mapping[d]
        for i, (_, thickness) in enumerate(border_layers):
            cum = cumulative_depths[i]
            if cum <= src_d < cum + thickness:
                print(f"    d={d}px -> src_d={src_d}px (第{i+1}层)")
                break

print("\n" + "=" * 60)
