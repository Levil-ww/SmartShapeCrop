"""
诊断弧内侧像素
"""
import os
import sys
import numpy as np
from PIL import Image

sys.path.insert(0, r"D:\SmartShapeCrop")

from core.image_cropper import (
    _get_border_layers_robust,
    detect_nested_rect_layers,
    _build_multi_layer_corner_mask,
    apply_border_only_corners,
)
from core.config import DEFAULT_BG_COLOR
from core.corner.algorithm import CORNER_ANGLES

# ============ 配置 ============
dpi = 150
bg_color = DEFAULT_BG_COLOR
target_w_cm = 40.0
target_h_cm = 160.0
corner_r_cm = 3.6

target_w_px = int(round(target_w_cm * dpi / 2.54))
target_h_px = int(round(target_h_cm * dpi / 2.54))
corner_r_px = int(round(corner_r_cm * dpi / 2.54))

print("=" * 80)
print("弧内侧像素诊断")
print("=" * 80)

# 创建测试图
w, h = target_w_px, target_h_px
test_img = Image.new('RGB', (w, h), (245, 240, 230))
arr = np.array(test_img)

# 绘制边框
border_width_cm = 1.5
border_px = int(round(border_width_cm * dpi / 2.54))
black_w = int(round(0.5 * dpi / 2.54))
gap_w = int(round(0.5 * dpi / 2.54))
brown_w = int(border_px - black_w - gap_w)

arr[0:black_w, :, :] = 15
arr[h-black_w:h, :, :] = 15
arr[:, 0:black_w, :] = 15
arr[:, w-black_w:w, :] = 15

inner_start = int(black_w + gap_w)
inner_start2 = int(inner_start + brown_w)
arr[inner_start:inner_start2, inner_start:w-inner_start, :] = 139
arr[h-inner_start2:h-inner_start, inner_start:w-inner_start, :] = 139
arr[inner_start:h-inner_start2, inner_start:inner_start2, :] = 139
arr[inner_start:h-inner_start2, w-inner_start2:w-inner_start, :] = 139

test_img = Image.fromarray(arr, 'RGB')

# 检测边框
border_layers = _get_border_layers_robust(test_img, bg_color)
raw_depth = sum(t for _, t in border_layers) if border_layers else 0
print(f"\n边框检测结果:")
for i, (color, thickness) in enumerate(border_layers):
    print(f"  第{i+1}层: 厚度={thickness}px, 颜色={color}")
print(f"  总厚度: {raw_depth}px")

# 应用圆角
corners = {'tl': 0, 'tr': 0, 'bl': corner_r_cm, 'br': corner_r_cm}
result = apply_border_only_corners(test_img, corners, dpi, bg_color)
result_arr = np.array(result)

# 构建mask
corners_px = {'bl': corner_r_px, 'br': corner_r_px}
try:
    nested_rects = detect_nested_rect_layers(test_img, border_layers=border_layers)
except Exception as e:
    print(f"[WARN] 嵌套矩形层检测失败: {e}")
    nested_rects = [(0, 0, w - 1, h - 1)]
mask = _build_multi_layer_corner_mask(w, h, corners_px, border_layers, nested_rects=nested_rects)
mask_arr = np.array(mask)

# 分析左下角
bl_cx = corner_r_px
bl_cy = h - corner_r_px
print(f"\n左下角圆心: ({bl_cx}, {bl_cy})")
print(f"ring_lower_bound: {max(0, min(raw_depth + 4, int(corner_r_px * 0.5)))}")

print("\n--- 左下角弧内侧像素详细分析 ---")
print(f"{'距离':>8} {'角度':>6} {'像素坐标':>15} {'Mask':>6} {'RGB':>20} {'分类':>15}")
print("-" * 85)

# 检查不同距离的像素
for dist_offset in [0, -5, -10, -20, -30, -45]:
    check_dist = corner_r_px + dist_offset
    if check_dist <= 0:
        continue
    for angle_deg in [90, 112.5, 135, 157.5, 180]:
        rad = np.radians(angle_deg)
        px = int(round(bl_cx + check_dist * np.cos(rad)))
        py = int(round(bl_cy + check_dist * np.sin(rad)))
        
        if 0 <= px < w and 0 <= py < h:
            pixel = result_arr[py, px, :]
            mask_val = mask_arr[py, px]
            actual_dist = np.sqrt((px - bl_cx)**2 + (py - bl_cy)**2)
            
            dist_to_white = np.sqrt(np.sum((pixel.astype(float) - 255) ** 2))
            dist_to_black = np.sqrt(np.sum((pixel.astype(float) - 15) ** 2))
            dist_to_brown = np.sqrt(np.sum((pixel.astype(float) - 139) ** 2))
            min_dist = min(dist_to_white, dist_to_black, dist_to_brown)
            
            if min_dist == dist_to_white and dist_to_white < 30:
                classification = "白色/背景"
            elif min_dist == dist_to_black:
                classification = "黑色边框"
            elif min_dist == dist_to_brown:
                classification = "棕色边框"
            else:
                classification = f"其他(d={min_dist:.1f})"
            
            print(f"{actual_dist:8.1f} {angle_deg:6.1f}° ({px:5d}, {py:5d}) {mask_val:6d} {pixel[0]:3d},{pixel[1]:3d},{pixel[2]:3d} {classification:>15}")

# 分析右下角
br_cx = w - corner_r_px
br_cy = h - corner_r_px
print(f"\n右下角圆心: ({br_cx}, {br_cy})")

print("\n--- 右下角弧内侧像素详细分析 ---")
print(f"{'距离':>8} {'角度':>6} {'像素坐标':>15} {'Mask':>6} {'RGB':>20} {'分类':>15}")
print("-" * 85)

for dist_offset in [0, -5, -10, -20, -30, -45]:
    check_dist = corner_r_px + dist_offset
    if check_dist <= 0:
        continue
    for angle_deg in [0, 22.5, 45, 67.5, 90]:
        rad = np.radians(angle_deg)
        px = int(round(br_cx + check_dist * np.cos(rad)))
        py = int(round(br_cy + check_dist * np.sin(rad)))
        
        if 0 <= px < w and 0 <= py < h:
            pixel = result_arr[py, px, :]
            mask_val = mask_arr[py, px]
            actual_dist = np.sqrt((px - br_cx)**2 + (py - br_cy)**2)
            
            dist_to_white = np.sqrt(np.sum((pixel.astype(float) - 255) ** 2))
            dist_to_black = np.sqrt(np.sum((pixel.astype(float) - 15) ** 2))
            dist_to_brown = np.sqrt(np.sum((pixel.astype(float) - 139) ** 2))
            min_dist = min(dist_to_white, dist_to_black, dist_to_brown)
            
            if min_dist == dist_to_white and dist_to_white < 30:
                classification = "白色/背景"
            elif min_dist == dist_to_black:
                classification = "黑色边框"
            elif min_dist == dist_to_brown:
                classification = "棕色边框"
            else:
                classification = f"其他(d={min_dist:.1f})"
            
            print(f"{actual_dist:8.1f} {angle_deg:6.1f}° ({px:5d}, {py:5d}) {mask_val:6d} {pixel[0]:3d},{pixel[1]:3d},{pixel[2]:3d} {classification:>15}")

# 保存结果
output_dir = r"D:\SmartShapeCrop\debug_output"
os.makedirs(output_dir, exist_ok=True)

bl_region = result.crop((0, h - 300, 300, h))
bl_path = os.path.join(output_dir, "inner_diag_bl.jpg")
bl_region.save(bl_path, 'JPEG', quality=95)
print(f"\n左下角放大图: {bl_path}")

br_region = result.crop((w - 300, h - 300, w, h))
br_path = os.path.join(output_dir, "inner_diag_br.jpg")
br_region.save(br_path, 'JPEG', quality=95)
print(f"右下角放大图: {br_path}")

print("\n诊断完成")
