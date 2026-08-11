"""
测试蔓生花图片的圆角裁剪效果
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

# 蔓生花图片路径
src_path = r"D:\SmartShapeCrop\psd_demo\蔓生花;35.5x256cm.jpg"
output_dir = r"D:\SmartShapeCrop\test_cropper_output"
dpi = 150
bg_color = DEFAULT_BG_COLOR

# 检查源图是否存在
if not os.path.exists(src_path):
    print(f"[错误] 源图不存在: {src_path}")
    print("请确认文件路径")
    sys.exit(1)

print("=" * 60)
print("蔓生花图片圆角裁剪测试")
print("=" * 60)

# 加载源图
src = load_source_image(src_path)
w, h = src.size
print(f"\n[源图]")
print(f"  尺寸: {w} x {h} px")
print(f"  模式: {src.mode}")

# 检测边框层
border_layers = _get_border_layers_robust(src, bg_color)
print(f"\n[边框检测] 检测到 {len(border_layers)} 层边框:")
total_thickness = 0
for i, (color, thickness) in enumerate(border_layers):
    cm = thickness * 2.54 / dpi
    print(f"  第{i+1}层: 颜色={color}, 厚度={thickness}px ({cm:.2f}cm)")
    total_thickness += thickness
print(f"  边框总厚度: {total_thickness}px ({total_thickness * 2.54 / dpi:.2f}cm)")

# 目标尺寸
target_w_cm = 35.5
target_h_cm = 256.0
corner_r_cm = 3.5  # 圆角半径

# 计算像素
target_w_px = int(round(target_w_cm * dpi / 2.54))
target_h_px = int(round(target_h_cm * dpi / 2.54))

print(f"\n[目标] 尺寸: {target_w_cm}x{target_h_cm}cm = {target_w_px}x{target_h_px}px")
print(f"[圆角] 半径: {corner_r_cm}cm = {int(round(corner_r_cm * dpi / 2.54))}px")

# 缩放源图
print("\n[步骤 1] 缩放到目标尺寸...")
cropped = src.resize((target_w_px, target_h_px), Image.LANCZOS)
print(f"  缩放后: {target_w_px} x {target_h_px} px")

# 应用圆角（左下角和右下角各一个圆角）
print("\n[步骤 2] 应用圆角裁剪...")
corners = {'tl': 0, 'tr': 0, 'bl': corner_r_cm, 'br': corner_r_cm}
result = apply_border_only_corners(cropped, corners, dpi, bg_color)

# 保存结果
output_name = "test_wan_sheng_hua_35.5x256cm_3.5cm_radius.jpg"
output_path = os.path.join(output_dir, output_name)
os.makedirs(output_dir, exist_ok=True)
result.save(output_path, 'JPEG', quality=95, dpi=(dpi, dpi))

print(f"\n[完成] 已保存: {output_path}")
print(f"  输出尺寸: {result.size[0]} x {result.size[1]} px")

# 验证结果
print("\n[验证]")
result_arr = list(result.getdata())
bg_threshold = 30
white_count = 0
total_pixels = len(result_arr)
for pixel in result_arr:
    dist_to_white = (pixel[0] - 255)**2 + (pixel[1] - 255)**2 + (pixel[2] - 255)**2
    if dist_to_white < bg_threshold**2:
        white_count += 1

white_pct = white_count / total_pixels * 100
print(f"  白色像素占比: {white_pct:.2f}%")
if white_pct < 5:
    print("  ✓ 白色区域正常（小于 5%）")
else:
    print("  ⚠ 白色区域过多，可能存在问题")

print("\n" + "=" * 60)
print("测试完成！")
print("=" * 60)
