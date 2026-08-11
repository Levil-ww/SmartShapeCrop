"""
检查裁剪后图像的实际边框颜色分布
"""
import sys
import numpy as np

sys.path.insert(0, r"D:\SmartShapeCrop")

from PIL import Image

src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
dpi = 150

print("=" * 60)
print("检查裁剪后图像的实际边框颜色分布")
print("=" * 60)

# 加载源图
src = Image.open(src_path)
sw, sh = src.size
src_arr = np.array(src)

# 裁剪到目标尺寸
target_w_px = int(round(35.5 * dpi / 2.54))
target_h_px = int(round(256 * dpi / 2.54))

# 使用 cover 模式裁剪（类似实际流程）
# 计算裁剪比例
scale = max(target_w_px / sw, target_h_px / sh)
new_w = int(round(sw * scale))
new_h = int(round(sh * scale))
resized = src.resize((new_w, new_h), Image.LANCZOS)

# 中心裁剪
left = (new_w - target_w_px) // 2
top = (new_h - target_h_px) // 2
right = left + target_w_px
bottom = top + target_h_px
cropped = resized.crop((left, top, right, bottom))

w, h = cropped.size
arr = np.array(cropped)

print(f"裁剪后尺寸: {w} x {h}")

# 检查右下角直线边框区域的颜色分布
print(f"\n[右下角直线边框颜色分布]")
print(f"  深度范围 | 像素颜色")
print(f"  --------- | --------")

for depth in range(0, min(350, w)):
    x = w - 1 - depth
    y = h - 1 - depth
    if 0 <= x < w and 0 <= y < h:
        pixel = arr[y, x]
        
        # 只显示特定深度的颜色（间隔5px）
        if depth < 50 or depth % 10 == 0:
            print(f"  {depth:3d}px: {pixel}")

# 检查左下角直线边框区域
print(f"\n[左下角直线边框颜色分布]")
for depth in range(0, min(350, w)):
    x = depth
    y = h - 1 - depth
    if 0 <= x < w and 0 <= y < h:
        pixel = arr[y, x]
        
        if depth < 50 or depth % 10 == 0:
            print(f"  {depth:3d}px: {pixel}")

# 检查圆角弧线上的颜色（假设圆角半径207px）
R = 207
print(f"\n[圆角弧线颜色分布 - 右下角]")
import math

# 圆心位置
cx = w - R
cy = h - R

# 沿弧线采样不同角度
for angle_deg in [270, 280, 290, 300, 310, 320, 330, 340, 350, 359]:
    angle_rad = math.radians(angle_deg)
    x = int(round(cx + R * math.cos(angle_rad)))
    y = int(round(cy + R * math.sin(angle_rad)))
    if 0 <= x < w and 0 <= y < h:
        pixel = arr[y, x]
        print(f"  角度{angle_deg}°, 位置({x}, {y}): {pixel}")

# 分析边框颜色分布（找主要颜色）
print(f"\n[深度区域的主要颜色分析]")
depth_ranges = [(0, 50), (50, 100), (100, 150), (150, 200), (200, 250), (250, 300)]
for d_start, d_end in depth_ranges:
    colors = []
    for d in range(d_start, min(d_end, w)):
        x = w - 1 - d
        y = h - 1 - d
        if 0 <= x < w and 0 <= y < h:
            colors.append(tuple(arr[y, x]))
    
    if colors:
        # 统计主要颜色
        color_counts = {}
        for c in colors:
            quantized = tuple(c[i] // 30 * 30 for i in range(3))
            color_counts[quantized] = color_counts.get(quantized, 0) + 1
        
        main_colors = sorted(color_counts.items(), key=lambda x: -x[1])[:2]
        print(f"  深度[{d_start}, {d_end})px:")
        for c, count in main_colors:
            print(f"    颜色{c}: {count}次 ({count/len(colors)*100:.1f}%)")

print("\n" + "=" * 60)
