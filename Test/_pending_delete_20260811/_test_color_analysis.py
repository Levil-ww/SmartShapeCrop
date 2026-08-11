"""
详细分析圆角裁剪后的颜色分布
"""
import os
import sys
import numpy as np

sys.path.insert(0, r"D:\SmartShapeCrop")

from PIL import Image
from core.image_cropper import (
    load_source_image,
    apply_border_only_corners,
)
from core.config import DEFAULT_BG_COLOR

src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
dpi = 150
bg_color = DEFAULT_BG_COLOR  # (255, 255, 255) 白色

print("=" * 60)
print("圆角裁剪颜色分析")
print("=" * 60)

# 加载源图
src = load_source_image(src_path)

# 缩放到目标尺寸
target_w_px = 2096  # 35.5cm @ 150dpi
target_h_px = 15118  # 256cm @ 150dpi
r_cm = 3.5
r_px = int(round(r_cm * dpi / 2.54))

# 应用圆角
cropped = src.resize((target_w_px, target_h_px), Image.LANCZOS)
corners = {'tl': 0, 'tr': 0, 'bl': r_cm, 'br': r_cm}
result = apply_border_only_corners(cropped, corners, dpi, bg_color)

result_arr = np.array(result)

# 分析左下角区域
print(f"\n[左下角 {r_px}x{r_px} 区域详细分析]")
bl_region = result_arr[target_h_px - r_px:target_h_px, 0:r_px]

# 统计颜色分布
total_pixels = bl_region.shape[0] * bl_region.shape[1]
color_buckets = {
    '白色 (255,255,255)': 0,
    '近白色 (>250)': 0,
    '浅色 (>200)': 0,
    '中等 (100-200)': 0,
    '深色 (<100)': 0,
}

for y in range(bl_region.shape[0]):
    for x in range(bl_region.shape[1]):
        r, g, b = bl_region[y, x]
        if r > 250 and g > 250 and b > 250:
            color_buckets['白色 (255,255,255)'] += 1
        elif r > 250 and g > 250:
            color_buckets['近白色 (>250)'] += 1
        elif r > 200:
            color_buckets['浅色 (>200)'] += 1
        elif r > 100:
            color_buckets['中等 (100-200)'] += 1
        else:
            color_buckets['深色 (<100)'] += 1

for bucket, count in color_buckets.items():
    pct = count / total_pixels * 100
    print(f"  {bucket}: {count} 像素 ({pct:.1f}%)")

# 检查角落边缘（应该是圆角裁剪的地方）
print(f"\n[左下角角落边缘像素采样]")
edge_samples = []
for dy in range(0, r_px, max(1, r_px // 10)):
    for dx in range(0, r_px, max(1, r_px // 10)):
        y_idx = target_h_px - 1 - dy
        x_idx = dx
        if 0 <= y_idx < target_h_px and 0 <= x_idx < target_w_px:
            edge_samples.append((dx, dy, tuple(result_arr[y_idx, x_idx])))

# 分析角落的几何位置
print(f"\n[左下角几何分析]")
# 圆心位置
cx_bl = r_px  # 左下角圆心
cy_bl = target_h_px - r_px

# 检查L形裁剪区域
cut_count = 0
bg_count = 0
for dy in range(r_px):
    for dx in range(r_px):
        y_idx = target_h_px - 1 - dy
        x_idx = dx
        if 0 <= y_idx < target_h_px and 0 <= x_idx < target_w_px:
            pixel = result_arr[y_idx, x_idx]
            # 计算到圆心的距离
            dist = np.sqrt((dx - cx_bl)**2 + (dy - r_px)**2)
            if dist > r_px:
                # 在裁剪区域内
                if pixel[0] > 250 and pixel[1] > 250 and pixel[2] > 250:
                    bg_count += 1
                else:
                    cut_count += 1

total_cut = cut_count + bg_count
if total_cut > 0:
    print(f"  L形裁剪区域: {total_cut} 像素")
    print(f"  其中边框颜色: {cut_count} 像素 ({cut_count/total_cut*100:.1f}%)")
    print(f"  其中背景色: {bg_count} 像素 ({bg_count/total_cut*100:.1f}%)")

print("\n" + "=" * 60)
