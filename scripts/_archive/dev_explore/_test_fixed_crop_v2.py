"""
测试修复后的圆角裁剪效果
使用源图预检测边框层
"""
import sys
import numpy as np

sys.path.insert(0, r"D:\SmartShapeCrop")

from PIL import Image
from core.image_cropper import crop_image, CropConfig
from core.config import DEFAULT_BG_COLOR

src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
output_path = r"D:\SmartShapeCrop\Test\output\test_corner_fixed_v2.jpg"
dpi = 150
bg_color = DEFAULT_BG_COLOR

print("=" * 60)
print("运行修复后的圆角裁剪测试 (v2 - 使用源图预检测边框层)")
print("=" * 60)

# 创建裁剪配置
config = CropConfig(
    src_path=src_path,
    target_w_cm=35.5,
    target_h_cm=256,
    dpi=dpi,
    mode='cover',
    bg_color=bg_color,
    corners={'bl': 3.5, 'br': 3.5},  # 左下角和右下角 3.5cm 圆角
    output_path=output_path,
)

# 执行裁剪
result = crop_image(config)
print(f"\n裁剪成功!")
w, h = result.size
print(f"结果图片尺寸: ({w}, {h})")

result_arr = np.array(result)

# 检查右下角圆角区域
print("\n[结果图右下角检查]")
for depth in [0, 50, 100, 200, 300, 400, 500]:
    x = w - 1 - depth
    y = h - 1
    if 0 <= x < w and 0 <= y < h:
        pixel = tuple(result_arr[y, x].tolist())
        print(f"  深度 {depth}px: 像素 {pixel}")

# 检查左下角圆角区域
print("\n[结果图左下角检查]")
for depth in [0, 50, 100, 200, 300, 400, 500]:
    x = 0
    y = h - 1 - depth
    if 0 <= x < w and 0 <= y < h:
        pixel = tuple(result_arr[y, x].tolist())
        print(f"  深度 {depth}px: 像素 {pixel}")

# 检查右下角圆角弧线
print("\n[结果图右下角圆角弧线检查]")
R = int(round(3.5 * dpi / 2.54))  # 3.5cm at 150dpi
cx = w - R
cy = h - R
import math
for angle_deg in [270, 280, 290, 300, 310, 320, 330, 340, 350, 359]:
    angle_rad = math.radians(angle_deg)
    x = int(round(cx + R * math.cos(angle_rad)))
    y = int(round(cy + R * math.sin(angle_rad)))
    if 0 <= x < w and 0 <= y < h:
        pixel = tuple(result_arr[y, x].tolist())
        print(f"  角度{angle_deg}°: 位置({x}, {y}), 像素 {pixel}")

# 检查直线边框区域颜色
print("\n[直线边框区域颜色检查]")
# 右下角直线区域（垂直边）
print("  右下角垂直边:")
for depth in [0, 100, 200, 300, 400, 500]:
    x = w - 1
    y = h - 1 - depth
    if 0 <= x < w and 0 <= y < h:
        pixel = tuple(result_arr[y, x].tolist())
        print(f"    深度 {depth}px: 像素 {pixel}")

# 右下角直线区域（水平边）
print("  右下角水平边:")
for depth in [0, 100, 200, 300, 400, 500]:
    x = w - 1 - depth
    y = h - 1
    if 0 <= x < w and 0 <= y < h:
        pixel = tuple(result_arr[y, x].tolist())
        print(f"    深度 {depth}px: 像素 {pixel}")

print(f"\n输出文件已保存到: {output_path}")
print("\n" + "=" * 60)
