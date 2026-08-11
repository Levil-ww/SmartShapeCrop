"""
详细调试圆角裁剪的角度和颜色映射
"""
import sys
import math
import numpy as np

sys.path.insert(0, r"D:\SmartShapeCrop")

from PIL import Image
from core.image_cropper import crop_image, CropConfig
from core.config import DEFAULT_BG_COLOR

src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
output_path = r"D:\SmartShapeCrop\Test\output\test_debug_v3.jpg"
dpi = 150
bg_color = DEFAULT_BG_COLOR

print("=" * 60)
print("详细调试圆角裁剪")
print("=" * 60)

# 创建裁剪配置
config = CropConfig(
    src_path=src_path,
    target_w_cm=35.5,
    target_h_cm=256,
    dpi=dpi,
    mode='cover',
    bg_color=bg_color,
    corners={'bl': 3.5, 'br': 3.5},
    output_path=output_path,
)

# 执行裁剪
result = crop_image(config)
w, h = result.size
result_arr = np.array(result)
print(f"结果图片尺寸: ({w}, {h})")

R = int(round(3.5 * dpi / 2.54))  # 207px
print(f"圆角半径: {R}px")

# 检查右下角 (br) - 角度范围应为 0-90°
print("\n[右下角 br 圆角弧线检查 - 角度 0-90°]")
cx_br = w - R
cy_br = h - R
print(f"  圆心: ({cx_br}, {cy_br})")
for angle_deg in [0, 10, 20, 30, 40, 50, 60, 70, 80, 89]:
    angle_rad = math.radians(angle_deg)
    x = int(round(cx_br + R * math.cos(angle_rad)))
    y = int(round(cy_br + R * math.sin(angle_rad)))
    if 0 <= x < w and 0 <= y < h:
        pixel = tuple(result_arr[y, x].tolist())
        print(f"  角度{angle_deg:3d}°: 位置({x:4d}, {y:5d}), 像素 {pixel}")

# 检查左下角 (bl) - 角度范围应为 90-180°
print("\n[左下角 bl 圆角弧线检查 - 角度 90-180°]")
cx_bl = R
cy_bl = h - R
print(f"  圆心: ({cx_bl}, {cy_bl})")
for angle_deg in [90, 100, 110, 120, 130, 140, 150, 160, 170, 179]:
    angle_rad = math.radians(angle_deg)
    x = int(round(cx_bl + R * math.cos(angle_rad)))
    y = int(round(cy_bl + R * math.sin(angle_rad)))
    if 0 <= x < w and 0 <= y < h:
        pixel = tuple(result_arr[y, x].tolist())
        print(f"  角度{angle_deg:3d}°: 位置({x:4d}, {y:5d}), 像素 {pixel}")

# 检查左下角(bl)的CUT_ANGLES验证
print("\n[左下角 bl 遮罩验证]")
print(f"  当前 CUT_ANGLES['bl'] = (270, 360)")
print(f"  正确应为 (90, 180)")

# 验证：bl圆心(207, 14911)，左下角像素(0, 15117)
# dx = 0 - 207 = -207, dy = 15117 - 14911 = 206
# angle = atan2(206, -207) = ? 
dx_test = 0 - cx_bl
dy_test = 15117 - cy_bl
angle_test = math.degrees(math.atan2(dy_test, dx_test)) % 360
dist_test = math.sqrt(dx_test**2 + dy_test**2)
print(f"  左下角像素(0, 15117): dx={dx_test}, dy={dy_test}, angle={angle_test:.1f}°, dist={dist_test:.1f}")
print(f"  在(270,360)范围内? {270 <= angle_test < 360}")
print(f"  在(90,180)范围内? {90 <= angle_test < 180}")

# 验证br圆心(1889, 14911)，右下角像素(2095, 15117)
print("\n[右下角 br 遮罩验证]")
print(f"  当前 CUT_ANGLES['br'] = (0, 90)")
dx_test2 = 2095 - cx_br
dy_test2 = 15117 - cy_br
angle_test2 = math.degrees(math.atan2(dy_test2, dx_test2)) % 360
dist_test2 = math.sqrt(dx_test2**2 + dy_test2**2)
print(f"  右下角像素(2095, 15117): dx={dx_test2}, dy={dy_test2}, angle={angle_test2:.1f}°, dist={dist_test2:.1f}")
print(f"  在(0,90)范围内? {0 <= angle_test2 < 90}")

# 检查直线边框颜色（远离圆角区域）
print("\n[直线边框颜色检查 (底部中央)]")
for depth in [0, 50, 100, 150, 200, 250, 300, 400, 500, 700, 1000, 1300]:
    x = w // 2
    y = h - 1 - depth
    if 0 <= y < h:
        pixel = tuple(result_arr[y, x].tolist())
        print(f"  深度 {depth:4d}px: 像素 {pixel}")

print(f"\n输出文件: {output_path}")
