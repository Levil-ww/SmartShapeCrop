"""
专门验证圆角角落的裁剪效果
"""
import os
import sys

sys.path.insert(0, r"D:\SmartShapeCrop")

from PIL import Image
from core.image_cropper import (
    load_source_image,
    apply_border_only_corners,
    _get_border_layers_robust,
)
from core.config import DEFAULT_BG_COLOR

src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
output_dir = r"D:\SmartShapeCrop\test_cropper_output"
dpi = 150
bg_color = DEFAULT_BG_COLOR

print("=" * 60)
print("圆角角落细节验证")
print("=" * 60)

# 加载源图
src = load_source_image(src_path)

# 缩放到目标尺寸
target_w_px = 2096  # 35.5cm @ 150dpi
target_h_px = 15118  # 256cm @ 150dpi

print(f"\n[步骤 1] 缩放源图...")
cropped = src.resize((target_w_px, target_h_px), Image.LANCZOS)

# 应用圆角（左下角和右下角）
corner_r_cm = 3.5
corners = {'tl': 0, 'tr': 0, 'bl': corner_r_cm, 'br': corner_r_cm}
print(f"[步骤 2] 应用 {corner_r_cm}cm 圆角（bl, br）...")
result = apply_border_only_corners(cropped, corners, dpi, bg_color)

# 保存完整结果
output_path = os.path.join(output_dir, "test_bl_br_full.jpg")
result.save(output_path, 'JPEG', quality=95, dpi=(dpi, dpi))
print(f"[保存] {output_path}")

# 裁剪左下角区域进行放大检查
r_px = int(round(corner_r_cm * dpi / 2.54))
bl_crop_size = r_px * 3
bl_region = result.crop((0, target_h_px - bl_crop_size, bl_crop_size, target_h_px))
bl_region = bl_region.resize((bl_region.width * 2, bl_region.height * 2), Image.LANCZOS)
bl_output = os.path.join(output_dir, "test_bl_corner_zoom.jpg")
bl_region.save(bl_output, 'JPEG', quality=95)
print(f"[保存] 左下角放大: {bl_output}")

# 裁剪右下角区域进行放大检查
br_region = result.crop((target_w_px - bl_crop_size, target_h_px - bl_crop_size, target_w_px, target_h_px))
br_region = br_region.resize((br_region.width * 2, br_region.height * 2), Image.LANCZOS)
br_output = os.path.join(output_dir, "test_br_corner_zoom.jpg")
br_region.save(br_output, 'JPEG', quality=95)
print(f"[保存] 右下角放大: {br_output}")

# 检查角落区域的颜色分布
import numpy as np
result_arr = np.array(result)

# 检查左下角 r_px x r_px 区域
bl_region_arr = result_arr[target_h_px - r_px:target_h_px, 0:r_px]
print(f"\n[左下角 {r_px}x{r_px} 区域颜色分析]")
print(f"  非白色像素比例: {(bl_region_arr.sum(axis=2) < 750).mean() * 100:.1f}%")

# 检查右下角 r_px x r_px 区域
br_region_arr = result_arr[target_h_px - r_px:target_h_px, target_w_px - r_px:target_w_px]
print(f"\n[右下角 {r_px}x{r_px} 区域颜色分析]")
print(f"  非白色像素比例: {(br_region_arr.sum(axis=2) < 750).mean() * 100:.1f}%")

print("\n" + "=" * 60)
print("验证完成！")
print("=" * 60)
