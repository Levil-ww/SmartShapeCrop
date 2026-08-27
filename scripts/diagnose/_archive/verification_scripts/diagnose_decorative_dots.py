"""
验证花漾之约场景：间隙区域有白色点状装饰时，修复能否保护装饰元素
"""
import numpy as np
from PIL import Image
import sys
sys.path.insert(0, "d:\\SmartShapeCrop")

from core.image_cropper import apply_border_only_corners
from core.corner.algorithm import CORNER_ANGLES

# 构造花漾之约风格测试图
# 米色间隙区域有白色点状装饰
w, h = 800, 1600
img_arr = np.zeros((h, w, 3), dtype=np.uint8)

# 底色：米黄色
img_arr[:, :] = [245, 235, 220]

# 第1层：深色边框 ~6px
img_arr[0:6, :, :] = [40, 35, 30]
img_arr[:, 0:6, :] = [40, 35, 30]
img_arr[h-6:h, :, :] = [40, 35, 30]
img_arr[:, w-6:w, :] = [40, 35, 30]

# 第2层：米色间隙 ~12px 带白色点状装饰
img_arr[6:18, :, :] = [245, 235, 220]
img_arr[:, 6:18, :] = [245, 235, 220]
img_arr[h-18:h-6, :, :] = [245, 235, 220]
img_arr[:, w-18:w-6, :] = [245, 235, 220]

# 在间隙区域添加白色点状装饰
np.random.seed(42)
# 上边间隙
for x in range(100, w-100, 25):
    y = 6 + np.random.randint(2, 10)
    img_arr[y:y+2, x:x+2, :] = [255, 255, 255]
# 左边间隙
for y in range(200, h-200, 25):
    x = 6 + np.random.randint(2, 10)
    img_arr[y:y+2, x:x+2, :] = [255, 255, 255]
# 下边间隙
for x in range(100, w-100, 25):
    y = h - 18 + np.random.randint(2, 10)
    img_arr[y:y+2, x:x+2, :] = [255, 255, 255]
# 右边间隙
for y in range(200, h-200, 25):
    x = w - 18 + np.random.randint(2, 10)
    img_arr[y:y+2, x:x+2, :] = [255, 255, 255]

# 第3层：浅色边框 ~30px
img_arr[18:48, :, :] = [200, 180, 150]
img_arr[:, 18:48, :] = [200, 180, 150]
img_arr[h-48:h-18, :, :] = [200, 180, 150]
img_arr[:, w-48:w-18, :] = [200, 180, 150]

test_img = Image.fromarray(img_arr, 'RGB')

bg_color = (255, 255, 255)

# 运行裁剪
corners = {'tl': 8.0, 'tr': 8.0, 'bl': 8.0, 'br': 8.0}
dpi = 150

result = apply_border_only_corners(test_img, corners, dpi, bg_color)
result_arr = np.array(result)
rw, rh = result.size

r_px = int(round(8.0 * dpi / 2.54))
r_cap = max(1, min(rw, rh) // 2)
r_actual = min(r_px, r_cap)

# 检查间隙层区域
# 间隙层深度：6-18px (gap region)
total_gap = 0
total_dots = 0

for corner_key in ['tl', 'tr', 'bl', 'br']:
    if corner_key == 'tl':
        cx, cy = r_actual, r_actual
    elif corner_key == 'tr':
        cx, cy = rw - r_actual, r_actual
    elif corner_key == 'bl':
        cx, cy = r_actual, rh - r_actual
    else:
        cx, cy = rw - r_actual, rh - r_actual
    
    ang_min, ang_max = CORNER_ANGLES[corner_key]
    
    roi_x1 = max(0, cx - r_actual)
    roi_y1 = max(0, cy - r_actual)
    roi_x2 = min(rw, cx + r_actual + 1)
    roi_y2 = min(rh, cy + r_actual + 1)
    
    yy, xx = np.mgrid[roi_y1:roi_y2, roi_x1:roi_x2].astype(np.float64)
    dx = xx - float(cx)
    dy = yy - float(cy)
    dist = np.sqrt(dx*dx + dy*dy)
    depth = float(r_actual) - dist
    angle = np.degrees(np.arctan2(dy, dx))
    angle = np.mod(angle, 360.0)
    
    if ang_max == 360:
        valid_angle = (angle >= ang_min) | (angle < 1)
    else:
        valid_angle = (angle >= ang_min) & (angle <= ang_max)
    valid_region = valid_angle & (dist <= r_actual + 2.0)
    
    # 间隙层深度范围 [6, 18)
    gap_pixels = valid_region & (depth >= 6) & (depth < 18)
    count = np.sum(gap_pixels)
    total_gap += count
    
    if count == 0:
        continue
    
    yy_g, xx_g = np.where(gap_pixels)
    global_y = yy_g + roi_y1
    global_x = xx_g + roi_x1
    actual_colors = result_arr[global_y, global_x, :].astype(np.float64)
    
    # 白色点检查
    white = np.array([255,255,255], dtype=np.float64)
    d_white = np.sqrt(np.sum((actual_colors - white) ** 2, axis=1))
    near_white = np.sum(d_white < 10)
    total_dots += near_white
    
    # 米色检查
    cream = np.array([245, 235, 220], dtype=np.float64)
    d_cream = np.sqrt(np.sum((actual_colors - cream) ** 2, axis=1))
    near_cream = np.sum(d_cream < 30)
    
    print(f'  {corner_key}: {count}px gap, {near_white}px near_white(点), {near_cream}px near_cream(底)')

print(f'\n总间隙像素: {total_gap}')
print(f'  白色点保留: {total_dots}')

if total_dots > 0:
    print('\n✓ 花漾之约测试通过！白色点状装饰被正确保留。')
else:
    print('\n✗ 花漾之约测试失败！白色点状装饰被清除了。')