"""
诊断左下角和右下角圆角处理问题
目标：双面格-定制-定制尺寸-花幔 40x160CM，左下角右下角3.6cm圆角
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

print("=" * 70)
print("左下角/右下角圆角诊断")
print("=" * 70)
print(f"目标尺寸: {target_w_px} x {target_h_px} px ({target_w_cm} x {target_h_cm} cm @ {dpi} DPI)")
print(f"圆角半径: {corner_r_px} px ({corner_r_cm} cm)")

# ============ 1. 创建测试图 ============
# 创建一个有边框的测试图（模拟花幔的结构）
w, h = target_w_px, target_h_px
test_img = Image.new('RGB', (w, h), (245, 240, 230))  # 米色背景
arr = np.array(test_img)

# 绘制外边框（黑色 + 米色间隙 + 棕色三层结构）
# 这是一个典型的双面格边框结构
border_width_cm = 1.5  # 每边边框总宽
border_px = int(round(border_width_cm * dpi / 2.54))

# 外层黑框（约 0.5cm）
black_w = int(round(0.5 * dpi / 2.54))
# 中间米色间隙（约 0.5cm）
gap_w = int(round(0.5 * dpi / 2.54))
# 内层棕框（约 0.5cm）
brown_w = int(border_px - black_w - gap_w)

# 绘制边框 - 使用正确的像素坐标
# 注意：这里的坐标是从图像边缘开始的
# 外层黑框
arr[0:black_w, :, :] = 15  # 顶
arr[h-black_w:h, :, :] = 15  # 底
arr[:, 0:black_w, :] = 15  # 左
arr[:, w-black_w:w, :] = 15  # 右

# 内层棕框
inner_start = int(black_w + gap_w)
inner_start2 = int(inner_start + brown_w)
arr[inner_start:inner_start2, inner_start:w-inner_start, :] = 139  # 棕色
arr[h-inner_start2:h-inner_start, inner_start:w-inner_start, :] = 139
arr[inner_start:h-inner_start2, inner_start:inner_start2, :] = 139
arr[inner_start:h-inner_start2, w-inner_start2:w-inner_start, :] = 139

test_img = Image.fromarray(arr, 'RGB')
print(f"\n[测试图] 尺寸: {w} x {h} px")
print(f"  外层黑框: {black_w}px, 间隙: {gap_w}px, 内层棕框: {brown_w}px")
print(f"  边框总宽: {black_w + gap_w + brown_w}px")

# ============ 2. 检测边框层 ============
border_layers = _get_border_layers_robust(test_img, bg_color)
print(f"\n[边框检测] 检测到 {len(border_layers)} 层边框:")
for i, (color, thickness) in enumerate(border_layers):
    cm = thickness * 2.54 / dpi
    print(f"  第{i+1}层: 厚度={thickness}px ({cm:.2f}cm), 颜色={color}")

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

# ============ 4. 应用圆角 ============
corners = {'tl': 0, 'tr': 0, 'bl': corner_r_cm, 'br': corner_r_cm}
print(f"\n[圆角配置] {corners}")

# 先缩放再应用圆角（模拟实际流程）
result = apply_border_only_corners(test_img, corners, dpi, bg_color)
print(f"\n[结果图] 尺寸: {result.size[0]} x {result.size[1]} px")

# ============ 5. 详细分析 ============
print("\n" + "=" * 70)
print("详细分析 - 左下角 (bl)")
print("=" * 70)

# 分析左下角区域
bl_cx = corner_r_px
bl_cy = h - corner_r_px
print(f"\n左下角圆心: ({bl_cx}, {bl_cy})")
print(f"角度范围: {CORNER_ANGLES['bl']}°")

# 检查左下角关键像素点
bl_result_arr = np.array(result)
bl_test_points = [
    # (x, y, 描述)
    (0, h-1, "左下角角点"),
    (corner_r_px, h-1, "左下角正下方弧线上"),
    (0, h-corner_r_px, "左下角正左方弧线上"),
    (int(corner_r_px * 0.707), int(h - corner_r_px * 0.707), "左下角45°弧线上"),
    (black_w, h-1, "底边上黑框位置"),
    (0, h-black_w, "左边上黑框位置"),
    (black_w + gap_w, h-1, "底边上间隙位置"),
]

print("\n左下角关键像素检查:")
for px, py, desc in bl_test_points:
    if 0 <= px < w and 0 <= py < h:
        pixel = bl_result_arr[py, px, :]
        dist_to_white = np.sqrt(np.sum((pixel.astype(float) - 255) ** 2))
        dist_to_black = np.sqrt(np.sum((pixel.astype(float) - 15) ** 2))
        dist_to_brown = np.sqrt(np.sum((pixel.astype(float) - 139) ** 2))
        min_dist = min(dist_to_white, dist_to_black, dist_to_brown)
        
        if min_dist == dist_to_white:
            classification = "白色/背景"
        elif min_dist == dist_to_black:
            classification = "黑色边框"
        else:
            classification = "棕色边框"
        
        print(f"  ({px:5d}, {py:5d}) [{desc:20s}] RGB=({pixel[0]:3d},{pixel[1]:3d},{pixel[2]:3d}) → {classification}")

# 分析左下角弧形区域
print("\n左下角弧形区域分析 (沿弧线采样):")
for angle_deg in [90, 112.5, 135, 157.5, 180]:
    rad = np.radians(angle_deg)
    # 注意：在屏幕坐标系中，arctan2(y-down, x-right)
    # 所以 angle 90° 指向下方，180° 指向左方
    px = int(round(bl_cx + corner_r_px * np.cos(rad)))
    py = int(round(bl_cy + corner_r_px * np.sin(rad)))
    if 0 <= px < w and 0 <= py < h:
        pixel = bl_result_arr[py, px, :]
        dist_to_white = np.sqrt(np.sum((pixel.astype(float) - 255) ** 2))
        dist_to_black = np.sqrt(np.sum((pixel.astype(float) - 15) ** 2))
        min_dist = min(dist_to_white, dist_to_black)
        
        if min_dist == dist_to_white and dist_to_white < 30:
            classification = "白色/背景 (异常!)"
        elif min_dist == dist_to_black:
            classification = "黑色边框"
        else:
            classification = f"其他 (dist_black={dist_to_black:.1f})"
        
        print(f"  角度{angle_deg:5.1f}° → 像素({px:5d}, {py:5d}) RGB=({pixel[0]:3d},{pixel[1]:3d},{pixel[2]:3d}) → {classification}")

# ============ 6. 检查mask构建逻辑 ============
print("\n" + "=" * 70)
print("mask构建逻辑分析")
print("=" * 70)

from core.image_cropper import _build_multi_layer_corner_mask

corners_px = {'bl': corner_r_px, 'br': corner_r_px}
mask = _build_multi_layer_corner_mask(w, h, corners_px, border_layers, nested_rects=nested_rects)
mask_arr = np.array(mask)

print(f"\nmask尺寸: {mask.size}")
print(f"mask中255(保留)像素数: {np.sum(mask_arr == 255)}")
print(f"mask中0(裁掉)像素数: {np.sum(mask_arr == 0)}")

# 分析左下角mask
print("\n左下角mask检查 (角点区域):")
bl_mask_points = [
    (0, h-1, "左下角角点"),
    (1, h-2, "角点附近"),
    (corner_r_px, h-1, "正下方弧线上"),
    (0, h-corner_r_px, "正左方弧线上"),
    (int(corner_r_px*0.5), int(h-corner_r_px*0.5), "对角线上弧内"),
    (int(corner_r_px*1.1), int(h-corner_r_px*0.1), "弧外下方"),
    (int(corner_r_px*0.1), int(h-corner_r_px*1.1), "弧外左方"),
]

for px, py, desc in bl_mask_points:
    if 0 <= px < w and 0 <= py < h:
        val = mask_arr[py, px]
        status = "保留(255)" if val == 255 else "裁掉(0)"
        print(f"  ({px:5d}, {py:5d}) [{desc:20s}] mask={val} → {status}")

# ============ 7. 保存诊断图像 ============
output_dir = r"D:\SmartShapeCrop\debug_output"
os.makedirs(output_dir, exist_ok=True)

# 保存结果
output_path = os.path.join(output_dir, "debug_bl_br_result.jpg")
result.save(output_path, 'JPEG', quality=95, dpi=(dpi, dpi))
print(f"\n[诊断结果] 已保存: {output_path}")

# 保存左下角放大图
bl_region = result.crop((0, h - 500, 500, h))
bl_zoom_path = os.path.join(output_dir, "debug_bl_zoom.jpg")
bl_region.save(bl_zoom_path, 'JPEG', quality=95)
print(f"  左下角放大图: {bl_zoom_path}")

# 保存右下角放大图
br_region = result.crop((w - 500, h - 500, w, h))
br_zoom_path = os.path.join(output_dir, "debug_br_zoom.jpg")
br_region.save(br_zoom_path, 'JPEG', quality=95)
print(f"  右下角放大图: {br_zoom_path}")

# 保存mask
mask_path = os.path.join(output_dir, "debug_mask.png")
mask.save(mask_path)
print(f"  mask图: {mask_path}")

print("\n" + "=" * 70)
print("诊断完成")
print("=" * 70)
