"""
测试左下角和右下角同时圆角的效果
模拟用户场景：蔓生花 35.5x256cm 左下角和右下角各3.5cm圆角
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

# 使用已有测试图片
src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
output_dir = r"D:\SmartShapeCrop\test_cropper_output"
dpi = 150
bg_color = DEFAULT_BG_COLOR

print("=" * 60)
print("左下角和右下角圆角裁剪测试")
print("=" * 60)

# 加载源图
src = load_source_image(src_path)
w, h = src.size
print(f"\n[源图] {os.path.basename(src_path)}")
print(f"  尺寸: {w} x {h} px")

# 检测边框层
border_layers = _get_border_layers_robust(src, bg_color)
print(f"\n[边框检测] 检测到 {len(border_layers)} 层边框:")
total_thickness = 0
for i, (color, thickness) in enumerate(border_layers):
    cm = thickness * 2.54 / dpi
    print(f"  第{i+1}层: {thickness}px ({cm:.2f}cm)")
    total_thickness += thickness

# 模拟用户场景：左下角和右下角同时3.5cm圆角
corner_r_cm = 3.5

# 缩放到目标尺寸
target_w_cm = 35.5
target_h_cm = 256.0
target_w_px = int(round(target_w_cm * dpi / 2.54))
target_h_px = int(round(target_h_cm * dpi / 2.54))

print(f"\n[测试] 左下角和右下角圆角: {corner_r_cm}cm")
print(f"  目标尺寸: {target_w_cm}x{target_h_cm}cm = {target_w_px}x{target_h_px}px")

# 缩放源图
cropped = src.resize((target_w_px, target_h_px), Image.LANCZOS)
print(f"  缩放后: {target_w_px} x {target_h_px} px")

# 应用圆角（左下角和右下角）
corners = {'tl': 0, 'tr': 0, 'bl': corner_r_cm, 'br': corner_r_cm}
result = apply_border_only_corners(cropped, corners, dpi, bg_color)

# 保存结果
output_name = "test_bl_br_3.5cm_radius.jpg"
output_path = os.path.join(output_dir, output_name)
os.makedirs(output_dir, exist_ok=True)
result.save(output_path, 'JPEG', quality=95, dpi=(dpi, dpi))

print(f"\n[完成] 已保存: {output_path}")
print(f"  输出尺寸: {result.size[0]} x {result.size[1]} px")

# 验证白色像素数量
result_arr = list(result.getdata())
white_count = 0
total_pixels = len(result_arr)
for pixel in result_arr:
    dist_to_white = (pixel[0] - 255)**2 + (pixel[1] - 255)**2 + (pixel[2] - 255)**2
    if dist_to_white < 30**2:
        white_count += 1

white_pct = white_count / total_pixels * 100
print(f"\n[验证] 白色像素占比: {white_pct:.2f}%")

# 检查角落区域
print("\n[角落区域检查]")
# 检查左下角
bl_region = result.crop((0, target_h_px - 200, 200, target_h_px))
bl_arr = list(bl_region.getdata())
bl_white = sum(1 for p in bl_arr if (p[0]-255)**2 + (p[1]-255)**2 + (p[2]-255)**2 < 30**2)
print(f"  左下角白色像素: {bl_white}/{len(bl_arr)} ({bl_white/len(bl_arr)*100:.1f}%)")

# 检查右下角
br_region = result.crop((target_w_px - 200, target_h_px - 200, target_w_px, target_h_px))
br_arr = list(br_region.getdata())
br_white = sum(1 for p in br_arr if (p[0]-255)**2 + (p[1]-255)**2 + (p[2]-255)**2 < 30**2)
print(f"  右下角白色像素: {br_white}/{len(br_arr)} ({br_white/len(br_arr)*100:.1f}%)")

if white_pct < 1:
    print("\n✓ 修复成功！白色区域符合预期（仅在圆角裁剪处）")
else:
    print("\n⚠ 仍有白色区域过多，需要进一步修复")

print("\n" + "=" * 60)
