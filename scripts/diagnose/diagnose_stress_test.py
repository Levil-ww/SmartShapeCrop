"""
压力测试：模拟 content_ref 计算偏差导致间隙层检测失败的场景
验证 Step C 兜底清扫能否正确工作
"""
import numpy as np
from PIL import Image
import sys
sys.path.insert(0, "d:\\SmartShapeCrop")

from core.image_cropper import apply_border_only_corners
from core.corner.algorithm import CORNER_ANGLES

# 构造测试图
w, h = 800, 1600
img_arr = np.zeros((h, w, 3), dtype=np.uint8)

# 底色：米黄色
img_arr[:, :] = [245, 235, 220]

# 第1层：黑色边框 ~8px
img_arr[0:8, :, :] = [25, 22, 20]
img_arr[:, 0:8, :] = [25, 22, 20]
img_arr[h-8:h, :, :] = [25, 22, 20]
img_arr[:, w-8:w, :] = [25, 22, 20]

# 第2层：米色间隙 ~15px  
img_arr[8:23, :, :] = [245, 235, 220]
img_arr[:, 8:23, :] = [245, 235, 220]
img_arr[h-23:h-8, :, :] = [245, 235, 220]
img_arr[:, w-23:w-8, :] = [245, 235, 220]

# 第3层：棕色边框 ~40px
img_arr[23:63, :, :] = [150, 95, 65]
img_arr[:, 23:63, :] = [150, 95, 65]
img_arr[h-63:h-23, :, :] = [150, 95, 65]
img_arr[:, w-63:w-23, :] = [150, 95, 65]

# 添加大色块装饰，使 content_ref 偏离间隙色（模拟真实场景的花纹干扰）
img_arr[200:400, 200:400, :] = [255, 100, 50]  # 大块红色装饰
img_arr[600:800, 400:600, :] = [50, 200, 100]  # 大块绿色装饰
img_arr[300:500, 500:700, :] = [100, 50, 255]  # 大块蓝色装饰

test_img = Image.fromarray(img_arr, 'RGB')

bg_color = (255, 255, 255)

# 先检查 content_ref 是否偏离了间隙色
arr_f = np.array(test_img).astype(np.float64)
x_start, x_end = int(w*0.15), int(w*0.85)
y_start, y_end = int(h*0.15), int(h*0.85)
STEPS = 21
xs = np.linspace(x_start, x_end, STEPS, dtype=np.int64).clip(0, w-1)
ys = np.linspace(y_start, y_end, STEPS, dtype=np.int64).clip(0, h-1)
gx, gy = np.meshgrid(xs, ys)
samples = arr_f[gy, gx, :].reshape(-1, 3)
content_ref = np.median(samples, axis=0)
gap_color = np.array([245, 235, 220], dtype=np.float64)
dist_to_gap = float(np.sqrt(np.sum((content_ref - gap_color) ** 2)))
print(f'content_ref = ({content_ref[0]:.0f}, {content_ref[1]:.0f}, {content_ref[2]:.0f})')
print(f'content_ref to gap_color distance: {dist_to_gap:.1f}')
print(f'间隙层检测 {"会" if dist_to_gap < 30 else "不会"} 成功 (阈值=30.0)')

# 运行裁剪
corners = {'tl': 8.0, 'tr': 8.0, 'bl': 8.0, 'br': 8.0}
dpi = 150

result = apply_border_only_corners(test_img, corners, dpi, bg_color)
result_arr = np.array(result)
rw, rh = result.size

r_px = int(round(8.0 * dpi / 2.54))
r_cap = max(1, min(rw, rh) // 2)
r_actual = min(r_px, r_cap)
print(f'\n实际圆角半径: {r_actual}px')

# 检查各角间隙层区域
total_gap = 0
total_ok = 0
total_bad = 0

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
    
    # 间隙层深度范围 [8, 23)
    gap_pixels = valid_region & (depth >= 8) & (depth < 23)
    count = np.sum(gap_pixels)
    total_gap += count
    
    if count == 0:
        continue
    
    yy_g, xx_g = np.where(gap_pixels)
    global_y = yy_g + roi_y1
    global_x = xx_g + roi_x1
    actual_colors = result_arr[global_y, global_x, :].astype(np.float64)
    
    bg = np.array([255,255,255], dtype=np.float64)
    d_bg = np.sqrt(np.sum((actual_colors - bg) ** 2, axis=1))
    d_gap = np.sqrt(np.sum((actual_colors - gap_color) ** 2, axis=1))
    
    near_bg = np.sum(d_bg < 30)
    near_gap = np.sum(d_gap < 30)
    total_ok += near_bg
    total_bad += near_gap
    
    status = "✓ PASS" if near_gap == 0 else "✗ FAIL"
    print(f'  {corner_key}: {count}px gap, {near_bg}px near_bg(白), {near_gap}px near_gap(米黄) {status}')

print(f'\n总间隙像素: {total_gap}')
print(f'  正确(白): {total_ok}/{total_gap} ({100*total_ok/total_gap:.1f}%)')
print(f'  错误(米黄): {total_bad}/{total_gap} ({100*total_bad/total_gap:.1f}%)')

if total_bad == 0:
    print('\n✓ 压力测试通过！即使 content_ref 被装饰元素干扰，间隙层也被正确清空。')
else:
    print(f'\n✗ 压力测试失败！有 {total_bad} 个米黄色像素残留。')