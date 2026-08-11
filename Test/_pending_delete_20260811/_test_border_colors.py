"""
详细分析边框颜色分布
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
print("边框颜色详细分析")
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
    print(f"  第{i+1}层: 深度范围[{cumulative}, {cumulative+thickness})px, 厚度={thickness}px ({cm:.2f}cm)")
    cumulative += thickness

# 分析右下角直线边框区域的颜色分布
print(f"\n[右下角直线边框颜色分布]")
print(f"  深度范围 | 像素颜色")
print(f"  --------- | --------")

# 采样右下角的一列像素
for depth in range(0, min(400, w)):
    x = w - 1 - depth
    y = h - 1 - depth  # 右下角对角线方向
    if 0 <= x < w and 0 <= y < h:
        pixel = src_arr[y, x]
        
        # 确定该深度所属的边框层
        layer = "背景"
        cum = 0
        for i, (color, thickness) in enumerate(border_layers):
            if depth >= cum and depth < cum + thickness:
                layer = f"第{i+1}层边框"
                break
            cum += thickness
        
        if layer != "背景" or depth < 50:  # 只显示边框区域
            print(f"  {depth:3d}px: {pixel} ({layer})")

# 分析各个深度的主要颜色
print(f"\n[边框层主要颜色]")
cumulative = 0
for i, (color, thickness) in enumerate(border_layers):
    d_start = cumulative
    d_end = cumulative + thickness
    
    # 采样该层的颜色
    colors = []
    for depth in range(d_start, min(d_end, w)):
        x = w - 1 - depth
        y = h - 1 - depth
        if 0 <= x < w and 0 <= y < h:
            colors.append(tuple(src_arr[y, x]))
    
    if colors:
        # 统计主要颜色
        color_counts = {}
        for c in colors:
            # 量化到30的间隔
            quantized = (c[0]//30*30, c[1]//30*30, c[2]//30*30)
            color_counts[quantized] = color_counts.get(quantized, 0) + 1
        
        # 找出主要颜色
        main_colors = sorted(color_counts.items(), key=lambda x: -x[1])[:3]
        print(f"\n  第{i+1}层边框 (深度[{d_start}, {d_end})px):")
        for c, count in main_colors:
            print(f"    颜色{c}: {count}次 ({count/len(colors)*100:.1f}%)")
    
    cumulative = d_end

print("\n" + "=" * 60)
