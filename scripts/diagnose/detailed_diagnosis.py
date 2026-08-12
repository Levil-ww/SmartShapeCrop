"""
详细诊断左下角和右下角圆角处理的C形缺口问题
深入分析每个像素的角度、距离、mask值、最终颜色
"""
import os
import sys
import numpy as np
from PIL import Image

sys.path.insert(0, r"D:\SmartShapeCrop")

from core.image_cropper import (
    load_source_image,
    apply_border_only_corners,
    _get_border_layers_robust,
    detect_nested_rect_layers,
    _build_multi_layer_corner_mask,
)
from core.config import DEFAULT_BG_COLOR
from core.corner.algorithm import CORNER_ANGLES

# ============ 配置 ============
dpi = 150
bg_color = DEFAULT_BG_COLOR
target_w_cm = 40.0
target_h_cm = 160.0
corner_r_cm = 3.6  # 左下角右下角圆角半径

target_w_px = int(round(target_w_cm * dpi / 2.54))
target_h_px = int(round(target_h_cm * dpi / 2.54))
corner_r_px = int(round(corner_r_cm * dpi / 2.54))

print("=" * 80)
print("C形缺口详细诊断")
print("=" * 80)
print(f"目标尺寸: {target_w_px} x {target_h_px} px")
print(f"圆角半径: {corner_r_px} px ({corner_r_cm} cm)")

# ============ 1. 创建测试图（模拟花幔结构） ============
w, h = target_w_px, target_h_px
test_img = Image.new('RGB', (w, h), (245, 240, 230))  # 米色背景
arr = np.array(test_img)

# 绘制边框结构：外层黑框 + 米色间隙 + 内层棕框
border_width_cm = 1.5
border_px = int(round(border_width_cm * dpi / 2.54))
black_w = int(round(0.5 * dpi / 2.54))
gap_w = int(round(0.5 * dpi / 2.54))
brown_w = int(border_px - black_w - gap_w)

# 外层黑框
arr[0:black_w, :, :] = 15
arr[h-black_w:h, :, :] = 15
arr[:, 0:black_w, :] = 15
arr[:, w-black_w:w, :] = 15

# 内层棕框
inner_start = int(black_w + gap_w)
inner_start2 = int(inner_start + brown_w)
arr[inner_start:inner_start2, inner_start:w-inner_start, :] = 139
arr[h-inner_start2:h-inner_start, inner_start:w-inner_start, :] = 139
arr[inner_start:h-inner_start2, inner_start:inner_start2, :] = 139
arr[inner_start:h-inner_start2, w-inner_start2:w-inner_start, :] = 139

test_img = Image.fromarray(arr, 'RGB')

# ============ 2. 检测边框层 ============
border_layers = _get_border_layers_robust(test_img, bg_color)
raw_depth = sum(t for _, t in border_layers) if border_layers else 0
print(f"\n[边框检测] 检测到 {len(border_layers)} 层边框:")
for i, (color, thickness) in enumerate(border_layers):
    cm = thickness * 2.54 / dpi
    print(f"  第{i+1}层: 厚度={thickness}px ({cm:.2f}cm), 颜色={color}")
print(f"  边框总厚度: {raw_depth}px")

# ============ 3. 检测嵌套矩形 ============
try:
    nested_rects = detect_nested_rect_layers(test_img, border_layers=border_layers)
    print(f"\n[嵌套矩形检测] 检测到 {len(nested_rects)} 层:")
    for i, rect in enumerate(nested_rects):
        x1, y1, x2, y2 = rect
        print(f"  第{i+1}层: ({x1}, {y1}) - ({x2}, {y2}), 尺寸: {x2-x1} x {y2-y1}")
except Exception as e:
    print(f"\n[嵌套矩形检测] 异常: {e}")
    nested_rects = [(0, 0, w - 1, h - 1)]

# ============ 4. 构建mask并分析 ============
corners_px = {'bl': corner_r_px, 'br': corner_r_px}
mask = _build_multi_layer_corner_mask(w, h, corners_px, border_layers, nested_rects=nested_rects)
mask_arr = np.array(mask)

print(f"\n[Mask分析]")
print(f"  mask中255(保留)像素数: {np.sum(mask_arr == 255)}")
print(f"  mask中0(裁掉)像素数: {np.sum(mask_arr == 0)}")

# ============ 5. 应用圆角并详细分析 ============
corners = {'tl': 0, 'tr': 0, 'bl': corner_r_cm, 'br': corner_r_cm}
result = apply_border_only_corners(test_img, corners, dpi, bg_color)
result_arr = np.array(result)

# ============ 6. 详细分析左下角 (bl) ============
print("\n" + "=" * 80)
print("左下角 (bl) 详细分析")
print("=" * 80)

bl_cx = corner_r_px
bl_cy = h - corner_r_px
ang_min_bl, ang_max_bl = CORNER_ANGLES['bl']
print(f"\n左下角圆心: ({bl_cx}, {bl_cy})")
print(f"角度范围: {ang_min_bl}° 至 {ang_max_bl}°")
print(f"边框总厚度: {raw_depth}px")
print(f"ring_lower_bound: {max(0, min(raw_depth + 4, int(corner_r_px * 0.5)))}")

# 分析左下角弧形区域
print("\n--- 左下角弧形区域像素详细分析 ---")
print(f"{'角度':>6} {'像素坐标':>15} {'距离':>8} {'Mask值':>8} {'RGB':>20} {'分类':>15}")
print("-" * 80)

for angle_deg in np.arange(85, 185, 5):
    rad = np.radians(angle_deg)
    px = int(round(bl_cx + corner_r_px * np.cos(rad)))
    py = int(round(bl_cy + corner_r_px * np.sin(rad)))
    
    if 0 <= px < w and 0 <= py < h:
        pixel = result_arr[py, px, :]
        mask_val = mask_arr[py, px]
        
        # 计算实际距离
        actual_dist = np.sqrt((px - bl_cx)**2 + (py - bl_cy)**2)
        
        # 计算分类
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
        
        print(f"{angle_deg:6.1f}° ({px:5d}, {py:5d}) {actual_dist:8.1f} {mask_val:8d} {pixel[0]:3d},{pixel[1]:3d},{pixel[2]:3d} {classification:>15}")

# ============ 7. 详细分析右下角 (br) ============
print("\n" + "=" * 80)
print("右下角 (br) 详细分析")
print("=" * 80)

br_cx = w - corner_r_px
br_cy = h - corner_r_px
ang_min_br, ang_max_br = CORNER_ANGLES['br']
print(f"\n右下角圆心: ({br_cx}, {br_cy})")
print(f"角度范围: {ang_min_br}° 至 {ang_max_br}°")

# 分析右下角弧形区域
print("\n--- 右下角弧形区域像素详细分析 ---")
print(f"{'角度':>6} {'像素坐标':>15} {'距离':>8} {'Mask值':>8} {'RGB':>20} {'分类':>15}")
print("-" * 80)

for angle_deg in np.arange(-5, 95, 5):
    rad = np.radians(angle_deg)
    px = int(round(br_cx + corner_r_px * np.cos(rad)))
    py = int(round(br_cy + corner_r_px * np.sin(rad)))
    
    if 0 <= px < w and 0 <= py < h:
        pixel = result_arr[py, px, :]
        mask_val = mask_arr[py, px]
        
        # 计算实际距离
        actual_dist = np.sqrt((px - br_cx)**2 + (py - br_cy)**2)
        
        # 计算分类
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
        
        print(f"{angle_deg:6.1f}° ({px:5d}, {py:5d}) {actual_dist:8.1f} {mask_val:8d} {pixel[0]:3d},{pixel[1]:3d},{pixel[2]:3d} {classification:>15}")

# ============ 8. 检查C形缺口区域 ============
print("\n" + "=" * 80)
print("C形缺口区域检查")
print("=" * 80)

# 检查弧外 1-10px 区域的像素
print("\n--- 左下角弧外区域 (距离 > r) ---")
for dist_offset in [1, 2, 3, 5, 10]:
    check_dist = corner_r_px + dist_offset
    # 检查多个角度
    for angle_deg in [90, 112.5, 135, 157.5, 180]:
        rad = np.radians(angle_deg)
        px = int(round(bl_cx + check_dist * np.cos(rad)))
        py = int(round(bl_cy + check_dist * np.sin(rad)))
        
        if 0 <= px < w and 0 <= py < h:
            pixel = result_arr[py, px, :]
            mask_val = mask_arr[py, px]
            dist_to_white = np.sqrt(np.sum((pixel.astype(float) - 255) ** 2))
            
            if dist_to_white < 30:
                print(f"  距离r+{dist_offset}px, 角度{angle_deg}°, 像素({px},{py}): 白色/背景, mask={mask_val}")

print("\n--- 右下角弧外区域 (距离 > r) ---")
for dist_offset in [1, 2, 3, 5, 10]:
    check_dist = corner_r_px + dist_offset
    for angle_deg in [0, 22.5, 45, 67.5, 90]:
        rad = np.radians(angle_deg)
        px = int(round(br_cx + check_dist * np.cos(rad)))
        py = int(round(br_cy + check_dist * np.sin(rad)))
        
        if 0 <= px < w and 0 <= py < h:
            pixel = result_arr[py, px, :]
            mask_val = mask_arr[py, px]
            dist_to_white = np.sqrt(np.sum((pixel.astype(float) - 255) ** 2))
            
            if dist_to_white < 30:
                print(f"  距离r+{dist_offset}px, 角度{angle_deg}°, 像素({px},{py}): 白色/背景, mask={mask_val}")

# ============ 9. 保存诊断图像 ============
output_dir = r"D:\SmartShapeCrop\debug_output"
os.makedirs(output_dir, exist_ok=True)

# 保存结果
output_path = os.path.join(output_dir, "detailed_diagnosis_result.jpg")
result.save(output_path, 'JPEG', quality=95, dpi=(dpi, dpi))
print(f"\n[诊断结果] 已保存: {output_path}")

# 保存左下角放大图
bl_region = result.crop((0, h - 400, 400, h))
bl_zoom_path = os.path.join(output_dir, "diagnosis_bl_zoom.jpg")
bl_region.save(bl_zoom_path, 'JPEG', quality=95)
print(f"  左下角放大图: {bl_zoom_path}")

# 保存右下角放大图
br_region = result.crop((w - 400, h - 400, w, h))
br_zoom_path = os.path.join(output_dir, "diagnosis_br_zoom.jpg")
br_region.save(br_zoom_path, 'JPEG', quality=95)
print(f"  右下角放大图: {br_zoom_path}")

print("\n" + "=" * 80)
print("诊断完成")
print("=" * 80)
