"""
检查裁剪前后的边框检测结果
"""
import sys
import numpy as np

sys.path.insert(0, r"D:\SmartShapeCrop")

from PIL import Image
from core.image_cropper import _get_border_layers_robust
from core.config import DEFAULT_BG_COLOR

src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
dpi = 150
bg_color = DEFAULT_BG_COLOR

print("=" * 60)
print("检查裁剪前后的边框检测结果")
print("=" * 60)

# 加载源图
src = Image.open(src_path)
sw, sh = src.size
print(f"源图尺寸: {sw} x {sh}")

# 在源图上检测边框
print("\n[源图边框检测]")
layers_src = _get_border_layers_robust(src, bg_color)
for i, (color, thickness) in enumerate(layers_src):
    cm = thickness * 2.54 / dpi
    print(f"  第{i+1}层: 厚度={thickness}px ({cm:.2f}cm), 颜色={color}")

# 裁剪到目标尺寸
target_w_px = int(round(35.5 * dpi / 2.54))
target_h_px = int(round(256 * dpi / 2.54))

from core.image_cropper import fit_image_to_rect
cropped = fit_image_to_rect(src, target_w_px, target_h_px, mode='cover', bg_color=bg_color)
cw, ch = cropped.size
print(f"\n裁剪后尺寸: {cw} x {ch}")

# 在裁剪后的图像上检测边框
print("\n[裁剪后边框检测]")
layers_cropped = _get_border_layers_robust(cropped, bg_color)
for i, (color, thickness) in enumerate(layers_cropped):
    cm = thickness * 2.54 / dpi
    print(f"  第{i+1}层: 厚度={thickness}px ({cm:.2f}cm), 颜色={color}")

# 对比结果
print("\n[对比分析]")
print(f"  源图检测到 {len(layers_src)} 层边框")
print(f"  裁剪后检测到 {len(layers_cropped)} 层边框")

if layers_src:
    total_src = sum(t for _, t in layers_src)
    print(f"  源图边框总厚度: {total_src}px ({total_src * 2.54 / dpi:.2f}cm)")

if layers_cropped:
    total_cropped = sum(t for _, t in layers_cropped)
    print(f"  裁剪后边框总厚度: {total_cropped}px ({total_cropped * 2.54 / dpi:.2f}cm)")

print("\n" + "=" * 60)
