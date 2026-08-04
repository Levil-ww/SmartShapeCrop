"""测试圆角裁剪函数 - 四角全面验证"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw
from core.image_cropper import apply_rounded_corners

# 创建测试图像
w, h = 1000, 800
img = Image.new('RGB', (w, h), (200, 100, 50))
draw = ImageDraw.Draw(img)
draw.rectangle([10, 10, w-10, h-10], outline=(255, 255, 255), width=2)

# 四角都设置圆角
corners = {'tl': 10.0, 'tr': 10.0, 'bl': 10.0, 'br': 10.0}
dpi = 300

print(f"测试 apply_rounded_corners (修复后):")
print(f"  输入尺寸: {w}x{h}")
print(f"  圆角参数: {corners}")
print(f"  DPI: {dpi}")

result = apply_rounded_corners(img, corners, dpi, bg_color=(255, 255, 255))

print(f"  输出尺寸: {result.size}")

r = int(round(10.0 * 300 / 2.54))
print(f"  圆角半径(像素): {r}")

# 验证各角 - 检查关键位置
print("\n===== 验证各角 =====")

def check_corner(name, corner_key, check_points):
    print(f"\n--- {name} ({corner_key}) ---")
    for x, y, desc, expect_white in check_points:
        if 0 <= x < w and 0 <= y < h:
            p = result.getpixel((x, y))
            is_white = (p == (255, 255, 255))
            status = "✓ PASS" if is_white == expect_white else "✗ FAIL"
            expected = "白色(背景)" if expect_white else "非白色(原图)"
            print(f"  ({x:4d}, {y:4d}) - {desc}: {p} {status} (期望: {expected})")

# 左上角检查点
tl_checks = [
    (0, 0, "左上角顶点", True),
    (r-10, 0, "左上角右侧圆角上", True),
    (0, r-10, "左上角下方圆角上", True),
    (r+5, r+5, "圆角内侧(圆心附近)", False),
    (2*r, 5, "左上角右侧圆角外", False),
    (5, 2*r, "左上角下方圆角外", False),
]
check_corner("左上角", "tl", tl_checks)

# 右上角检查点
tr_checks = [
    (w-1, 0, "右上角顶点", True),
    (w-r+10, 0, "右上角左侧圆角上", True),
    (w-1, r-10, "右上角下方圆角上", True),
    (w-r-5, r+5, "圆角内侧(圆心附近)", False),
    (w-2*r-5, 5, "右上角左侧圆角外", False),
    (w-5, 2*r, "右上角下方圆角外", False),
]
check_corner("右上角", "tr", tr_checks)

# 左下角检查点
bl_checks = [
    (0, h-1, "左下角顶点", True),
    (r-10, h-1, "左下角右侧圆角上", True),
    (0, h-r+10, "左下角上方圆角上", True),
    (r+5, h-r-5, "圆角内侧(圆心附近)", False),
    (2*r, h-5, "左下角右侧圆角外", False),
    (5, h-2*r-5, "左下角上方圆角外", False),
]
check_corner("左下角", "bl", bl_checks)

# 右下角检查点
br_checks = [
    (w-1, h-1, "右下角顶点", True),
    (w-r+10, h-1, "右下角左侧圆角上", True),
    (w-1, h-r+10, "右下角上方圆角上", True),
    (w-r-5, h-r-5, "圆角内侧(圆心附近)", False),
    (w-2*r-5, h-5, "右下角左侧圆角外", False),
    (w-5, h-2*r-5, "右下角上方圆角外", False),
]
check_corner("右下角", "br", br_checks)

# 检查中心点应为原图颜色
center_p = result.getpixel((w//2, h//2))
print(f"\n--- 中心区域 ---")
print(f"  ({w//2}, {h//2}) - 图像中心: {center_p} {'✓ 正确(非白色)' if center_p != (255,255,255) else '✗ 错误(是白色)'}")

# 保存结果
output_path = os.path.join(os.path.dirname(__file__), 'test_corner_result.png')
result.save(output_path)
print(f"\n结果已保存到: {output_path}")