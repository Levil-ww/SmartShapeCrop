"""
测试边框线在圆角裁剪中是否正确保留。
验证修复后的 _detect_border_layers 和 _redraw_border_on_corner 功能。
"""
import os
import sys
import numpy as np
from PIL import Image

# 添加项目根目录到路径
sys.path.insert(0, r"D:\SmartShapeCrop")

from core.image_cropper import (
    _detect_border_layers,
    _redraw_border_on_corner,
    apply_border_only_corners,
    apply_rounded_corners,
    apply_multi_layer_rounded_corners,
    _determine_corner_mode,
    load_source_image,
)

# ============ 参数配置 ============
src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
output_dir = r"D:\SmartShapeCrop\psd_demo"
dpi = 300

# 测试不同的圆角场景
test_cases = [
    {
        "name": "小圆角-仅边框模式",
        "target_w_cm": 41.0,
        "target_h_cm": 55.0,
        "corners": {"tl": 0, "tr": 0, "bl": 0, "br": 3.0},  # 3cm < 8.5cm
        "expected_mode": "border_only",
    },
    {
        "name": "大圆角-多层模式",
        "target_w_cm": 41.0,
        "target_h_cm": 55.0,
        "corners": {"tl": 0, "tr": 0, "bl": 0, "br": 10.0},  # 10cm >= 8.5cm
        "expected_mode": "full",
    },
]

print("=" * 60)
print("边框线保留功能测试")
print("=" * 60)

# 1. 加载源图
print(f"\n源图: {src_path}")
src = load_source_image(src_path)
print(f"源图尺寸: {src.size[0]} x {src.size[1]} px")

# 2. 检测边框层
print("\n" + "-" * 40)
print("步骤 1: 检测边框层")
border_layers = _detect_border_layers(src, max_scan_depth_px=300)
print(f"检测到 {len(border_layers)} 层边框:")
for i, (color, thickness) in enumerate(border_layers):
    print(f"  层{i+1}: 颜色={color}, 厚度={thickness}px")

if not border_layers:
    print("  ⚠️  未检测到任何边框层！")
else:
    total_thickness = sum(t for _, t in border_layers)
    print(f"  总厚度: {total_thickness}px ({total_thickness * 2.54 / dpi:.2f}cm)")

# 3. 对源图进行简单缩放
target_w_px = int(round(41.0 * dpi / 2.54))
target_h_px = int(round(55.0 * dpi / 2.54))
print(f"\n目标尺寸: {target_w_px} x {target_h_px} px")

cropped = src.resize((target_w_px, target_h_px), Image.LANCZOS)
print(f"缩放后尺寸: {cropped.size[0]} x {cropped.size[1]} px")

# 4. 对缩放后的图重新检测边框层
print("\n" + "-" * 40)
print("步骤 2: 对缩放后的图检测边框层")
border_layers_cropped = _detect_border_layers(cropped, max_scan_depth_px=300)
print(f"检测到 {len(border_layers_cropped)} 层边框:")
for i, (color, thickness) in enumerate(border_layers_cropped):
    print(f"  层{i+1}: 颜色={color}, 厚度={thickness}px")

# 5. 测试不同圆角场景
for case in test_cases:
    print("\n" + "=" * 60)
    print(f"测试场景: {case['name']}")
    print(f"圆角: {case['corners']}")
    
    corners = case['corners']
    corner_mode = _determine_corner_mode(corners)
    print(f"圆角模式: {'整体圆角' if corner_mode == 'full' else '仅边框圆角'}")
    print(f"预期模式: {'整体圆角' if case['expected_mode'] == 'full' else '仅边框圆角'}")
    
    # 执行圆角裁剪
    if corner_mode == 'full':
        result = apply_multi_layer_rounded_corners(cropped, corners, dpi, (255, 255, 255))
    else:
        result = apply_border_only_corners(cropped, corners, dpi, (255, 255, 255))
    
    print(f"输出尺寸: {result.size[0]} x {result.size[1]} px")
    
    # 检查边框线是否保留
    arr = np.array(result)
    w, h = result.size
    
    # 检查右下角圆角区域是否有边框颜色
    r_px = int(round(corners['br'] * dpi / 2.54))
    print(f"右下角圆角半径: {r_px}px")
    
    # 从右下角向内扫描，检查是否有非背景色像素
    scan_colors = []
    for dy in range(min(r_px, 100)):
        y = h - 1 - dy
        for dx in range(min(r_px, 100)):
            x = w - 1 - dx
            if abs(x - (w - r_px)) <= 5 and abs(y - (h - r_px)) <= 5:
                # 在圆弧附近采样
                color = tuple(arr[y, x, :])
                scan_colors.append(color)
    
    # 检查是否有黑色或深色像素（边框颜色）
    dark_pixels = sum(1 for c in scan_colors if sum(c) < 200)
    print(f"圆角区域深色像素数: {dark_pixels} / {len(scan_colors)}")
    
    if dark_pixels > 0:
        print("  ✅ 边框线在圆角区域保留")
    else:
        print("  ❌ 边框线在圆角区域丢失！")
    
    # 保存结果
    output_path = os.path.join(output_dir, f"test_{case['name']}.jpg")
    result.save(output_path, 'JPEG', quality=95, optimize=True, dpi=(dpi, dpi))
    print(f"  已保存: {output_path}")

# 6. 简单测试 apply_rounded_corners
print("\n" + "=" * 60)
print("测试 apply_rounded_corners (整体圆角)")
corners_test = {'tl': 0, 'tr': 0, 'bl': 0, 'br': 2.0}
result_test = apply_rounded_corners(cropped, corners_test, dpi, (255, 255, 255))

r_test = int(round(2.0 * dpi / 2.54))
arr_test = np.array(result_test)
dark_in_corner = 0
total_checked = 0
for dy in range(min(r_test, 50)):
    y = h - 1 - dy
    for dx in range(min(r_test, 50)):
        x = w - 1 - dx
        dist_to_arc = abs(np.sqrt((x - (w - r_test))**2 + (y - (h - r_test))**2) - r_test)
        if dist_to_arc < 3:  # 在圆弧附近
            total_checked += 1
            if sum(arr_test[y, x, :]) < 200:
                dark_in_corner += 1

print(f"圆弧附近深色像素: {dark_in_corner} / {total_checked}")
if dark_in_corner > 0:
    print("  ✅ apply_rounded_corners 边框线保留正常")
else:
    print("  ❌ apply_rounded_corners 边框线丢失！")

print("\n" + "=" * 60)
print("测试完成")