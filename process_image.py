"""
图片等比缩放 + 圆角处理脚本
支持根据圆角半径自动选择圆角模式：
  - radius >= 8.5cm: 整体圆角（所有边框线条一起裁掉）
  - radius < 8.5cm: 仅边框圆角（内层装饰保持直角）
"""
import os
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

from core.image_cropper import (
    load_source_image,
    fit_image_to_rect,
    apply_rounded_corners,
    apply_border_only_corners,
    apply_multi_layer_rounded_corners,
    _determine_corner_mode,
    BORDER_ONLY_THRESHOLD_CM,
)

# ============ 参数配置 ============
src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
output_dir = r"D:\SmartShapeCrop\psd_demo"
output_name = "双面格-定制-定制尺寸-简织;竖版55x41cm右下角圆角半径2cm.jpg"

target_w_cm = 41.0      # 目标宽度（厘米，竖版短边）
target_h_cm = 55.0      # 目标高度（厘米，竖版长边）
corner_r_cm = 2.0       # 右下角圆角半径（厘米）
dpi = 300               # DPI

# ============ 计算像素 ============
target_w_px = int(round(target_w_cm * dpi / 2.54))
target_h_px = int(round(target_h_cm * dpi / 2.54))
corner_r_px = int(round(corner_r_cm * dpi / 2.54))

print(f"目标尺寸: {target_w_px} x {target_h_px} px ({target_w_cm} x {target_h_cm} cm @ {dpi} DPI)")
print(f"圆角半径: {corner_r_px} px ({corner_r_cm} cm)")

# ============ 1. 加载源图 ============
src = load_source_image(src_path)
print(f"源图尺寸: {src.size} px")

# ============ 2. Cover 模式等比缩放裁剪 ============
cropped = fit_image_to_rect(src, target_w_px, target_h_px, mode='cover')
print(f"裁剪后尺寸: {cropped.size} px")

# ============ 3. 添加圆角（自动选择模式） ============
corners = {'tl': 0, 'tr': 0, 'bl': 0, 'br': corner_r_cm}
corner_mode = _determine_corner_mode(corners)
print(f"圆角模式: {'整体圆角' if corner_mode == 'full' else '仅边框圆角'}")

if corner_mode == 'full':
    # 大圆角（>=8.5cm）：使用多层统一圆角裁剪
    # 自动识别嵌套边框层，对每一层都应用相同圆角，使用 AND 逻辑组合
    result = apply_multi_layer_rounded_corners(cropped, corners, dpi, (255, 255, 255))
else:
    result = apply_border_only_corners(cropped, corners, dpi, (255, 255, 255))

# ============ 4. 保存 ============
output_path = os.path.join(output_dir, output_name)
result.save(output_path, 'JPEG', quality=95, optimize=True, dpi=(dpi, dpi))
print(f"\n[完成] 已保存: {output_path}")
print(f"输出尺寸: {result.size} px")