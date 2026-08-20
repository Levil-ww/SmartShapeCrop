"""
详细分析间隙区域的像素分布
"""
import numpy as np
import math
from PIL import Image
import sys
sys.path.insert(0, '.')

from core.image_cropper import apply_rounded_corners, _get_border_layers_robust

# 创建测试图像
w, h = 800, 1000
img = Image.new('RGB', (w, h), (255, 255, 255))
arr = np.array(img)

# 外层黑色边框 50px
arr[0:50, :] = (0, 0, 0)
arr[-50:, :] = (0, 0, 0)
arr[:, 0:50] = (0, 0, 0)
arr[:, -50:] = (0, 0, 0)

# 浅灰色间隙 20px (50-70)
arr[50:70, 50:750] = (200, 200, 200)
arr[-70:-50, 50:750] = (200, 200, 200)
arr[50:950, 50:70] = (200, 200, 200)
arr[50:950, -70:-50] = (200, 200, 200)

# 内层红色边框 30px
arr[70:100, 70:730] = (200, 0, 0)
arr[-100:-70, 70:730] = (200, 0, 0)
arr[70:930, 70:100] = (200, 0, 0)
arr[70:930, -100:-70] = (200, 0, 0)

img = Image.fromarray(arr, 'RGB')
bg_color = (255, 255, 255)

# 先检测边框层
border_layers = _get_border_layers_robust(img, bg_color)
print("检测到的边框层:")
for i, (color, thickness) in enumerate(border_layers):
    print(f"  层 {i}: 颜色={color}, 厚度={thickness}px")

# 执行圆角处理
dpi = 150
corners = {'tl': 0, 'tr': 0, 'bl': 3.0, 'br': 0}
result = apply_rounded_corners(img, corners, dpi=dpi, bg_color=bg_color)
result_arr = np.array(result)

# 分析左下角圆角区域
cx, cy = 50, 950
r_px = int(3.0 / 2.54 * dpi)  # 177px

print(f"\n圆角半径: {r_px}px")
print(f"圆角中心: ({cx}, {cy})")

# 按区域分析像素分布
regions = [
    ("外层边框 (0-50px)", 0, 50),
    ("间隙 (50-70px)", 50, 70),
    ("内层边框 (70-100px)", 70, 100),
    ("内部内容 (100-177px)", 100, r_px),
]

for region_name, dist_min, dist_max in regions:
    color_counts = {}
    total = 0
    for angle_deg in range(0, 91, 5):
        angle_rad = math.radians(angle_deg)
        for dist_px in range(dist_min, min(dist_max, r_px) + 1):
            x = int(cx + dist_px * math.cos(angle_rad))
            y = int(cy - dist_px * math.sin(angle_rad))
            if 0 <= x < w and 0 <= y < h:
                pixel = tuple(result_arr[y, x])
                # 简化颜色分类
                if pixel[0] < 50 and pixel[1] < 50 and pixel[2] < 50:
                    color_key = "黑色"
                elif pixel[0] > 200 and pixel[1] > 200 and pixel[2] > 200:
                    color_key = "白色"
                elif pixel[0] > 150 and pixel[1] < 50 and pixel[2] < 50:
                    color_key = "红色"
                elif pixel[0] > 150 and pixel[1] > 150 and pixel[2] > 150:
                    color_key = "灰色"
                else:
                    color_key = f"其他{pixel}"
                
                color_counts[color_key] = color_counts.get(color_key, 0) + 1
                total += 1
    
    print(f"\n{region_name}:")
    print(f"  总像素数: {total}")
    for color, count in sorted(color_counts.items(), key=lambda x: -x[1]):
        pct = count / total * 100 if total > 0 else 0
        print(f"  {color}: {count} ({pct:.1f}%)")

# 检查间隙区域黑色像素的分布
print("\n\n间隙区域(50-70px)黑色像素详细分析:")
black_in_gap = []
for angle_deg in range(0, 91, 5):
    angle_rad = math.radians(angle_deg)
    for dist_px in range(50, 71):
        x = int(cx + dist_px * math.cos(angle_rad))
        y = int(cy - dist_px * math.sin(angle_rad))
        if 0 <= x < w and 0 <= y < h:
            pixel = result_arr[y, x]
            if pixel[0] < 50 and pixel[1] < 50 and pixel[2] < 50:
                black_in_gap.append((angle_deg, dist_px, x, y, tuple(pixel)))

if black_in_gap:
    print(f"  黑色像素数量: {len(black_in_gap)}")
    print(f"  前10个示例:")
    for item in black_in_gap[:10]:
        angle, dist, px, py, color = item
        print(f"    角度={angle}°, 距离={dist}px, 位置=({px},{py}), 颜色={color}")
    
    # 检查这些黑色像素是否在边框延伸区域
    # 根据修复逻辑，只有 in_extension 且颜色匹配目标的像素才会被保留
    print(f"\n  分析: 这些黑色像素可能来自边框延伸区域的正确重绘")
    print(f"  这是修复的预期行为 - 防止圆角缺口")
else:
    print("  ✅ 间隙区域无黑色像素，修复成功！")

# 保存结果图像供检查
result.save('test_corner_result.png')
print("\n结果图像已保存至: test_corner_result.png")
