"""
测试修复后的圆角裁剪效果
"""
import sys
import os

sys.path.insert(0, r"D:\SmartShapeCrop")

from core.image_cropper import crop_image, CropConfig
from core.config import DEFAULT_BG_COLOR

# 目标文件
src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
output_path = r"D:\SmartShapeCrop\Test\output\test_corner_fixed.jpg"

# 确保输出目录存在
os.makedirs(os.path.dirname(output_path), exist_ok=True)

# 裁剪配置
config = CropConfig(
    src_path=src_path,
    target_w_cm=35.5,
    target_h_cm=256,
    dpi=150,
    mode='cover',
    bg_color=DEFAULT_BG_COLOR,
    corners={
        'tl': 0.0,
        'tr': 0.0,
        'bl': 3.5,
        'br': 3.5,
    },
    output_path=output_path,
)

print("=" * 60)
print("运行修复后的圆角裁剪测试")
print("=" * 60)

try:
    result = crop_image(config)
    print(f"\n裁剪成功!")
    print(f"结果图片尺寸: {result.size}")
    
    # 检查结果图的右下角像素
    import numpy as np
    
    result_arr = np.array(result)
    h, w = result_arr.shape[:2]
    
    # 检查右下角区域
    print(f"\n[结果图右下角检查]")
    for depth in [0, 50, 100, 150, 200, 250]:
        x = w - 1 - depth
        y = h - 1 - depth
        if 0 <= x < w and 0 <= y < h:
            pixel = tuple(result_arr[y, x].tolist())
            print(f"  深度 {depth}px: 像素 {pixel}")
    
    # 检查左下角区域
    print(f"\n[结果图左下角检查]")
    for depth in [0, 50, 100, 150, 200, 250]:
        x = depth
        y = h - 1 - depth
        if 0 <= x < w and 0 <= y < h:
            pixel = tuple(result_arr[y, x].tolist())
            print(f"  深度 {depth}px: 像素 {pixel}")
    
    # 检查圆角弧线
    print(f"\n[结果图右下角圆角弧线检查]")
    R = int(round(3.5 * 150 / 2.54))  # 3.5cm at 150dpi
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
    
    print(f"\n输出文件已保存到: {output_path}")
    
except Exception as e:
    print(f"\n裁剪失败: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
