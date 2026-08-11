"""
检查圆角遮罩的几何形状
"""
import sys
import numpy as np

sys.path.insert(0, r"D:\SmartShapeCrop")

from PIL import Image, ImageDraw
from core.image_cropper import _build_multi_layer_corner_mask

print("=" * 60)
print("检查圆角遮罩的几何形状")
print("=" * 60)

# 创建测试图像
w, h = 500, 500  # 使用小尺寸方便调试
R = 100  # 圆角半径

# 边框层（简化为单层测试）
border_layers = [
    ((255, 0, 0), 50),  # 第1层: 0-50px, 厚度50px, 红色
    ((0, 255, 0), 30),  # 第2层: 50-80px, 厚度30px, 绿色
    ((0, 0, 255), 20),  # 第3层: 80-100px, 厚度20px, 蓝色
]

# 构建圆角遮罩
corners_px = {'br': R}  # 只处理右下角
mask = _build_multi_layer_corner_mask(w, h, corners_px, border_layers)

# 检查遮罩
mask_arr = np.array(mask)

print(f"遮罩尺寸: {mask.size}")
print(f"遮罩有效区域(255): {np.sum(mask_arr > 0)} 像素")
print(f"遮罩裁剪区域(0): {np.sum(mask_arr == 0)} 像素")

# 可视化检查右下角的遮罩
print(f"\n[右下角遮罩可视化]")
print(f"  (显示 200x200 区域的遮罩值)")
print()

# 计算遮罩在右下角的情况
cx = w - R  # 400
cy = h - R  # 400

# 检查几个关键点
print(f"  圆心位置: ({cx}, {cy})")
print(f"  圆角半径: {R}px")
print()

# 沿对角线方向检查
print(f"  沿对角线方向检查 (从右下角向内):")
for d in range(0, 150, 10):
    x = w - 1 - d
    y = h - 1 - d
    if 0 <= x < w and 0 <= y < h:
        val = mask_arr[y, x]
        dist_to_center = np.sqrt((x - cx)**2 + (y - cy)**2)
        angle = np.degrees(np.arctan2(y - cy, x - cx)) % 360
        print(f"    深度{d:3d}px: 位置({x}, {y}), 遮罩={val:3d}, 距圆心={dist_to_center:.1f}px, 角度={angle:.1f}°")

# 检查直线边方向
print(f"\n  沿底边方向检查 (从右下角向左):")
for d in range(0, 150, 10):
    x = w - 1 - d
    y = h - 1
    if 0 <= x < w and 0 <= y < h:
        val = mask_arr[y, x]
        dist_to_center = np.sqrt((x - cx)**2 + (y - cy)**2)
        angle = np.degrees(np.arctan2(y - cy, x - cx)) % 360
        print(f"    深度{d:3d}px: 位置({x}, {y}), 遮罩={val:3d}, 距圆心={dist_to_center:.1f}px, 角度={angle:.1f}°")

# 检查圆角弧线
print(f"\n  沿圆角弧线检查 (距圆心R处):")
import math
for angle_deg in [270, 280, 290, 300, 310, 320, 330, 340, 350, 359]:
    angle_rad = math.radians(angle_deg)
    x = int(round(cx + R * math.cos(angle_rad)))
    y = int(round(cy + R * math.sin(angle_rad)))
    if 0 <= x < w and 0 <= y < h:
        val = mask_arr[y, x]
        print(f"    角度{angle_deg:3d}°: 位置({x}, {y}), 遮罩={val:3d}")

# 检查裁剪区域（应该是0的区域）
print(f"\n  检查裁剪区域 (应该是遮罩=0的区域):")
print(f"  L形裁剪区域 = r×r正方形 - 1/4圆")
print(f"  对于右下角 (br):")
print(f"    - 正方形: x in [{cx}, {cx+R}], y in [{cy}, {cy+R}]")
print(f"    - 1/4圆: 距圆心 <= R, 角度在 [270, 360]°")
print(f"    - 裁剪区域: 正方形内 - 1/4圆内")

# 验证预期的裁剪区域
expected_cut_pixels = 0
for x in range(cx, min(cx + R, w)):
    for y in range(cy, min(cy + R, h)):
        dist = np.sqrt((x - cx)**2 + (y - cy)**2)
        angle = np.degrees(np.arctan2(y - cy, x - cx)) % 360
        if dist >= R and 270 <= angle <= 360:
            expected_cut_pixels += 1

actual_cut_pixels = np.sum(mask_arr[cy:cy+R, cx:cx+R] == 0)
print(f"  预期裁剪像素数: {expected_cut_pixels}")
print(f"  实际裁剪像素数: {actual_cut_pixels}")

# 创建可视化图
visual = Image.new('RGB', (w, h), (255, 255, 255))
visual_arr = np.array(visual)

# 显示遮罩效果
visual_arr[mask_arr == 0] = [0, 0, 0]  # 裁剪区域显示黑色
visual_arr[mask_arr == 255] = [200, 200, 200]  # 保留区域显示灰色

# 标记圆心
visual_arr[cy, cx] = [255, 0, 0]
visual_arr[cy-1, cx] = [255, 0, 0]
visual_arr[cy+1, cx] = [255, 0, 0]
visual_arr[cy, cx-1] = [255, 0, 0]
visual_arr[cy, cx+1] = [255, 0, 0]

# 标记圆角弧线
for angle_deg in range(270, 360, 5):
    angle_rad = math.radians(angle_deg)
    x = int(round(cx + R * math.cos(angle_rad)))
    y = int(round(cy + R * math.sin(angle_rad)))
    if 0 <= x < w and 0 <= y < h:
        visual_arr[y, x] = [0, 0, 255]

# 保存可视化结果
visual_img = Image.fromarray(visual_arr)
output_path = r"D:\SmartShapeCrop\Test\output\corner_mask_visual.png"
os.makedirs(os.path.dirname(output_path), exist_ok=True)
visual_img.save(output_path)
print(f"\n  可视化图已保存到: {output_path}")

print("\n" + "=" * 60)
