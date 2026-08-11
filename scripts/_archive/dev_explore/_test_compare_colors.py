"""
对比原始源图和裁剪后图像的边框颜色
"""
import sys
import numpy as np

sys.path.insert(0, r"D:\SmartShapeCrop")

from PIL import Image

src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
dpi = 150

print("=" * 60)
print("对比原始源图和裁剪后图像的边框颜色")
print("=" * 60)

# 加载源图
src = Image.open(src_path)
sw, sh = src.size
src_arr = np.array(src)
print(f"源图尺寸: {sw} x {sh}")

# 检查源图右下角
print(f"\n[源图右下角颜色分布]")
for depth in range(0, 350):
    x = sw - 1 - depth
    y = sh - 1 - depth
    if 0 <= x < sw and 0 <= y < sh:
        pixel = src_arr[y, x]
        if depth < 50 or depth % 20 == 0:
            print(f"  深度 {depth}px: {pixel}")

# 裁剪到目标尺寸
target_w_px = int(round(35.5 * dpi / 2.54))
target_h_px = int(round(256 * dpi / 2.54))

# 使用 cover 模式裁剪
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
print(f"\n裁剪后尺寸: {w} x {h}")

# 检查裁剪后右下角
print(f"\n[裁剪后右下角颜色分布]")
for depth in range(0, 350):
    x = w - 1 - depth
    y = h - 1 - depth
    if 0 <= x < w and 0 <= y < h:
        pixel = arr[y, x]
        if depth < 50 or depth % 20 == 0:
            print(f"  深度 {depth}px: {pixel}")

# 检查源图和裁剪图在对应位置的颜色对比
print(f"\n[源图和裁剪图颜色对比]")
print(f"  (注意：裁剪使用 cover 模式，可能改变了边框的像素结构)")

# 计算裁剪后的缩放比例和偏移
print(f"  缩放比例: {scale:.4f}")
print(f"  中心裁剪偏移: left={left}, top={top}")

# 在源图中找到裁剪后图像右下角对应的位置
# 裁剪后右下角 = (w-1, h-1)
# 在源图中对应的位置 = ((left + (w-1)) / scale, (top + (h-1)) / scale)
orig_x = (left + (w - 1)) / scale
orig_y = (top + (h - 1)) / scale
print(f"  裁剪后右下角在源图中的位置: ({orig_x:.1f}, {orig_y:.1f})")

# 检查源图在该位置的颜色
orig_x_int = int(round(orig_x))
orig_y_int = int(round(orig_y))
if 0 <= orig_x_int < sw and 0 <= orig_y_int < sh:
    print(f"  源图该位置的颜色: {tuple(src_arr[orig_y_int, orig_x_int].tolist())}")

# 更仔细地检查边框颜色分布
print(f"\n[边框颜色详细分析 - 源图]")
# 采样源图中间的垂直边
mid_y = sh // 2
print(f"  垂直边 (x={sw-1}, y={mid_y}):")
for depth in range(0, 350):
    x = sw - 1 - depth
    if 0 <= x < sw:
        pixel = src_arr[mid_y, x]
        if depth < 100 or depth % 30 == 0:
            print(f"    深度 {depth}px: {pixel}")

print(f"\n[边框颜色详细分析 - 裁剪后图像]")
mid_y = h // 2
print(f"  垂直边 (x={w-1}, y={mid_y}):")
for depth in range(0, 350):
    x = w - 1 - depth
    if 0 <= x < w:
        pixel = arr[mid_y, x]
        if depth < 100 or depth % 30 == 0:
            print(f"    深度 {depth}px: {pixel}")

print("\n" + "=" * 60)
