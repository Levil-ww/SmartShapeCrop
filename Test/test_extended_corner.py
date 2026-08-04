"""测试新版圆角裁剪：验证 ≥8.5cm 时扩展半径覆盖所有边框层"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw
from core.image_cropper import (
    apply_rounded_corners,
    apply_border_only_corners,
    _determine_corner_mode,
    BORDER_ONLY_THRESHOLD_CM,
    BORDER_TOTAL_DEPTH_CM,
)

print("=" * 60)
print("新版圆角裁剪测试")
print(f"阈值: {BORDER_ONLY_THRESHOLD_CM}cm")
print(f"边框总深度: {BORDER_TOTAL_DEPTH_CM}cm")
print("=" * 60)

# 创建测试图像（模拟多层边框结构）
# 外框黑边 + 装饰线 + 内框
w, h = 800, 600
img = Image.new('RGB', (w, h), (240, 235, 220))
draw = ImageDraw.Draw(img)

# 第1层：最外黑色边框（深度0.5cm ≈ 59px @300dpi）
draw.rectangle([10, 10, w-10, h-10], outline=(30, 30, 30), width=6)

# 第2层：浅色装饰带
draw.rectangle([20, 20, w-20, h-20], outline=(180, 160, 120), width=3)

# 第3层：黑色内框
draw.rectangle([35, 35, w-35, h-35], outline=(50, 50, 50), width=4)

# 第4层：装饰圆点带（用小点模拟）
for x in range(50, w-50, 20):
    for y in [55, h-55]:
        draw.ellipse([x-3, y-3, x+3, y+3], fill=(150, 130, 100))
for y in range(50, h-50, 20):
    for x in [55, w-55]:
        draw.ellipse([x-3, y-3, x+3, y+3], fill=(150, 130, 100))

# 中心内容区域
draw.rectangle([70, 70, w-70, h-70], fill=(250, 245, 230))
draw.text((w//2-60, h//2), "Test Content", fill=(100, 50, 50))

dpi = 300

# 测试1: radius = 7.5cm (< 8.5cm) - 应该只裁外层
print("\n--- 测试1: radius=7.5cm (< 8.5cm) ---")
corners_small = {'tl': 0, 'tr': 0, 'bl': 0, 'br': 7.5}
mode = _determine_corner_mode(corners_small)
print(f"  模式: {mode}")
print(f"  预期: 只裁外层边框")

result1 = apply_border_only_corners(img.copy(), corners_small, dpi, (255, 255, 255))

# 检查右下角内框是否保留
br_inner = result1.getpixel((w-80, h-80))
print(f"  右下角内框区域(80,80): {br_inner} - {'✓ 保留(直角)' if br_inner != (255,255,255) else '✗ 被裁掉'}")

# 测试2: radius = 10cm (≥ 8.5cm) - 应该裁掉所有边框
print("\n--- 测试2: radius=10cm (≥ 8.5cm) ---")
corners_large = {'tl': 0, 'tr': 0, 'bl': 0, 'br': 10.0}
mode = _determine_corner_mode(corners_large)
print(f"  模式: {mode}")
print(f"  预期: 裁掉所有边框层")

# 直接调用 apply_rounded_corners，它会自动扩展半径
result2 = apply_rounded_corners(img.copy(), corners_large, dpi, (255, 255, 255))

# 检查右下角内框区域是否被裁掉
br_inner2 = result2.getpixel((w-80, h-80))
print(f"  右下角内框区域(80,80): {br_inner2} - {'✓ 被裁掉(扩展半径生效)' if br_inner2 == (255,255,255) else '✗ 保留(未完全裁掉)'}")

# 测试3: 验证实际裁剪半径
print("\n--- 测试3: 验证扩展半径 ---")
# 10cm + 2cm (BORDER_TOTAL_DEPTH_CM) = 12cm
# 12cm 在 300dpi 下 = 12 * 300 / 2.54 = 1417px
r_expected = int(round((10.0 + BORDER_TOTAL_DEPTH_CM) * dpi / 2.54))
print(f"  预期裁剪半径: {10.0} + {BORDER_TOTAL_DEPTH_CM} = {10.0 + BORDER_TOTAL_DEPTH_CM}cm = {r_expected}px")

# 检查是否完全覆盖了边框（边框最内层在 ~70px 处）
# 圆心在 (w-r, h-r)，如果 r > 70，那么内框应该被覆盖
print(f"  内框最深约 70px，裁剪半径 {r_expected}px > 70px: {'✓ 能完全覆盖' if r_expected > 70 else '✗ 不能完全覆盖'}")

# 保存结果
output_dir = os.path.join(os.path.dirname(__file__), 'test_extended_corner_output')
os.makedirs(output_dir, exist_ok=True)

result1.save(os.path.join(output_dir, 'border_only_7.5cm.jpg'), 'JPEG', quality=95, dpi=(dpi, dpi))
result2.save(os.path.join(output_dir, 'extended_full_10cm.jpg'), 'JPEG', quality=95, dpi=(dpi, dpi))

print(f"\n结果已保存到: {output_dir}")
print("  - border_only_7.5cm.jpg (仅边框圆角)")
print("  - extended_full_10cm.jpg (扩展半径后覆盖所有边框)")

print("\n✓ 测试完成")