"""测试边框圆角功能"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw
from core.image_cropper import (
    apply_rounded_corners,
    apply_border_only_corners,
    _DEFAULT_BORDER_WIDTH_CM,
)

print("=" * 60)
print("边框圆角 vs 整体圆角 视觉对比测试")
print("=" * 60)

# 创建测试图像（模拟多层边框）
w, h = 500, 400
img = Image.new('RGB', (w, h), (250, 245, 230))
draw = ImageDraw.Draw(img)

# 外层黑色边框
draw.rectangle([10, 10, w-10, h-10], outline=(0, 0, 0), width=4)
# 内层装饰线
draw.rectangle([25, 25, w-25, h-25], outline=(100, 100, 100), width=2)
# 再内层
draw.rectangle([40, 40, w-40, h-40], outline=(150, 50, 50), width=1)
# 中心圆
draw.ellipse([w//4, h//4, 3*w//4, 3*h//4], outline=(0, 100, 0), width=2)

dpi = 300
corners_small = {"tl": 0, "tr": 0, "bl": 0, "br": 2.0}  # < 8.5cm -> border_only
corners_large = {"tl": 0, "tr": 0, "bl": 0, "br": 9.0}  # >= 8.5cm -> full

# 测试边框圆角模式
print("\n测试边框圆角模式 (radius=2cm, border_width=1.5cm):")
result_border = apply_border_only_corners(img.copy(), corners_small, dpi, (255, 255, 255))

# 检查右下角内部是否保留直角
br_check = result_border.getpixel((w-50, h-50))
print(f"  右下角内部(50,50): {br_check} - {'✓ 保留原图(直角)' if br_check != (255, 255, 255) else '✗ 被圆角裁掉'}")

br_corner = result_border.getpixel((w-1, h-1))
print(f"  右下角顶点: {br_corner} - {'✓ 被裁掉(圆角)' if br_corner == (255, 255, 255) else '✗ 未裁掉'}")

# 测试整体圆角模式
print("\n测试整体圆角模式 (radius=9cm):")
result_full = apply_rounded_corners(img.copy(), corners_large, dpi, (255, 255, 255))

br_check2 = result_full.getpixel((w-50, h-50))
print(f"  右下角内部(50,50): {br_check2} - {'✓ 被裁掉(整体圆角)' if br_check2 == (255, 255, 255) else '✗ 保留原图'}")

br_corner2 = result_full.getpixel((w-1, h-1))
print(f"  右下角顶点: {br_corner2} - {'✓ 被裁掉' if br_corner2 == (255, 255, 255) else '✗ 未裁掉'}")

# 保存结果
output_dir = os.path.join(os.path.dirname(__file__), 'test_border_corner_output')
os.makedirs(output_dir, exist_ok=True)

result_border.save(os.path.join(output_dir, 'border_only_2cm.jpg'), 'JPEG', quality=95, dpi=(dpi, dpi))
result_full.save(os.path.join(output_dir, 'full_9cm.jpg'), 'JPEG', quality=95, dpi=(dpi, dpi))

print(f"\n结果已保存到: {output_dir}")
print("  - border_only_2cm.jpg (边框圆角模式)")
print("  - full_9cm.jpg (整体圆角模式)")

print("\n✓ 测试完成")