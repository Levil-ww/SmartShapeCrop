"""
调试花漾之约极差判断
"""
import numpy as np
from PIL import Image
import sys
sys.path.insert(0, "d:\\SmartShapeCrop")

# 构造花漾之约风格测试图
w, h = 800, 1600
img_arr = np.zeros((h, w, 3), dtype=np.uint8)
img_arr[:, :] = [245, 235, 220]  # 米色底

# 添加边框层
img_arr[0:6, :, :] = [40, 35, 30]
img_arr[:, 0:6, :] = [40, 35, 30]
img_arr[h-6:h, :, :] = [40, 35, 30]
img_arr[:, w-6:w, :] = [40, 35, 30]

img_arr[6:18, :, :] = [245, 235, 220]
img_arr[:, 6:18, :] = [245, 235, 220]
img_arr[h-18:h-6, :, :] = [245, 235, 220]
img_arr[:, w-18:w-6, :] = [245, 235, 220]

# 白色点状装饰
np.random.seed(42)
for x in range(100, w-100, 25):
    y = 6 + np.random.randint(2, 10)
    img_arr[y:y+2, x:x+2, :] = [255, 255, 255]
for y in range(200, h-200, 25):
    x = 6 + np.random.randint(2, 10)
    img_arr[y:y+2, x:x+2, :] = [255, 255, 255]

img_arr[18:48, :, :] = [200, 180, 150]
img_arr[:, 18:48, :] = [200, 180, 150]
img_arr[h-48:h-18, :, :] = [200, 180, 150]
img_arr[:, w-48:w-18, :] = [200, 180, 150]

test_img = Image.fromarray(img_arr, 'RGB')

# 模拟 _redraw_border_on_corner 中的计算
bg_color = (255, 255, 255)
bg_arr = np.array(bg_color, dtype=np.float64)

# 模拟 border_layers
border_layers = [
    ((40, 35, 30), 6),      # 黑边框 6px
    ((245, 235, 220), 12),  # 米色间隙 12px
    ((200, 180, 150), 30),  # 浅棕边框 30px
]

cumulative_depths = [0, 6, 18, 48]
total_border_depth = 48

# content_ref 模拟
content_ref = np.array([245, 235, 220], dtype=np.float64)

# border_colors_arr
border_colors_arr = np.array(
    [np.array(c, dtype=np.float64) for c, _ in border_layers]
)

# 计算 is_gap_layer
GAP_COLOR_DIST = 30.0
is_gap_layer = [
    float(np.sqrt(np.sum((np.array(c, dtype=np.float64) - content_ref) ** 2))) < GAP_COLOR_DIST
    for c, _ in border_layers
]
print(f"is_gap_layer: {is_gap_layer}")

# gap_regions
gap_regions = []
for i, is_gap in enumerate(is_gap_layer):
    if is_gap:
        gap_regions.append((cumulative_depths[i], cumulative_depths[i + 1]))
print(f"gap_regions: {gap_regions}")

# 检查 tl 角的间隙区域
R_total = 400
corner_key = 'tl'
cx, cy = R_total, R_total
x1, y1 = max(0, cx - R_total), max(0, cy - R_total)
x2, y2 = min(w, cx + R_total + 1), min(h, cy + R_total + 1)

yy, xx = np.mgrid[y1:y2, x1:x2].astype(np.float64)
dx = xx - float(cx)
dy = yy - float(cy)
dist = np.sqrt(dx * dx + dy * dy)
depth = float(R_total) - dist

# CORNER_ANGLES
CORNER_ANGLES = {
    'tl': (180, 270),
    'tr': (270, 360),
    'bl': (90, 180),
    'br': (0, 90),
}
ang_min, ang_max = CORNER_ANGLES[corner_key]
angle = np.degrees(np.arctan2(dy, dx))
angle = np.mod(angle, 360.0)
valid_region = (angle >= ang_min) & (angle <= ang_max) & (dist <= R_total + 2.0)

# 模拟 src_arr
src_arr = np.array(test_img, dtype=np.float64)

for (gap_start, gap_end) in gap_regions:
    gap_paint_mask = valid_region & (depth >= float(gap_start)) & (depth < float(gap_end))
    n_gap = np.sum(gap_paint_mask)
    print(f"\ngap [{gap_start}, {gap_end}): {n_gap} px")
    
    gap_ys_check, gap_xs_check = np.where(gap_paint_mask)
    global_y_check = gap_ys_check + y1
    global_x_check = gap_xs_check + x1
    check_colors = src_arr[global_y_check, global_x_check, :]
    
    # 排除边框过渡像素
    not_border_like = np.ones(n_gap, dtype=bool)
    for bc_arr in border_colors_arr:
        d_border = np.sqrt(np.sum((check_colors - bc_arr.reshape(1, 3)) ** 2, axis=1))
        not_border_like &= (d_border > 15.0)
    
    n_non_transition = np.sum(not_border_like)
    print(f"  非过渡像素: {n_non_transition}/{n_gap}")
    
    non_transition_colors = check_colors[not_border_like]
    if len(non_transition_colors) > 10:
        channel_ranges = np.ptp(non_transition_colors, axis=0)
        mean_range = float(np.mean(channel_ranges))
        print(f"  R极差={channel_ranges[0]:.1f}, G极差={channel_ranges[1]:.1f}, B极差={channel_ranges[2]:.1f}")
        print(f"  mean_range={mean_range:.2f}")
        print(f"  DECORATION_RANGE_THRESH=8.0")
        print(f"  判定: {'装饰间隙 (保留)' if mean_range > 8.0 else '均匀间隙 (清空)'}")
    else:
        print(f"  非过渡像素太少，mean_range=0")
    
    # 额外检查：有多少白色像素
    white_ref = np.array([255, 255, 255], dtype=np.float64)
    d_white = np.sqrt(np.sum((check_colors - white_ref.reshape(1, 3)) ** 2, axis=1))
    n_white = np.sum(d_white < 10)
    print(f"  白色像素数: {n_white}/{n_gap}")