"""
端到端诊断脚本：排查塞纳时光圆角裁剪米黄色弧线问题
使用 synthetic test image 模拟真实图像结构
"""
import numpy as np
from PIL import Image
import sys
sys.path.insert(0, "d:\\SmartShapeCrop")

from core.corner.detection import _get_border_layers_robust, detect_nested_rect_layers
from core.image_cropper import apply_border_only_corners
from core.corner.algorithm import CORNER_ANGLES

# ===== 构造与塞纳时光相同结构的测试图 =====
w, h = 800, 1600
img_arr = np.zeros((h, w, 3), dtype=np.uint8)

# 底色：米黄色 (content_ref ≈ 245,235,220)
img_arr[:, :] = [245, 235, 220]

# 第1层：黑色边框 ~7px
img_arr[0:7, :, :] = [25, 22, 20]
img_arr[:, 0:7, :] = [25, 22, 20]
img_arr[h-7:h, :, :] = [25, 22, 20]
img_arr[:, w-7:w, :] = [25, 22, 20]

# 第2层：米色间隙 ~15px  
img_arr[7:22, :, :] = [245, 235, 220]
img_arr[:, 7:22, :] = [245, 235, 220]
img_arr[h-22:h-7, :, :] = [245, 235, 220]
img_arr[:, w-22:w-7, :] = [245, 235, 220]

# 第3层：棕色边框 ~40px
img_arr[22:62, :, :] = [150, 95, 65]
img_arr[:, 22:62, :] = [150, 95, 65]
img_arr[h-62:h-22, :, :] = [150, 95, 65]
img_arr[:, w-62:w-22, :] = [150, 95, 65]

test_img = Image.fromarray(img_arr, 'RGB')

bg_color = (255, 255, 255)
border_layers = _get_border_layers_robust(test_img, bg_color)
print(f'[OK] 检测到 {len(border_layers)} 层边框:')
for i, (color, thickness) in enumerate(border_layers):
    print(f'  第{i+1}层: 颜色={color}, 厚度={thickness}px')

# 检查 content_ref 计算
arr_f = np.array(test_img).astype(np.float64)
x_start, x_end = int(w*0.15), int(w*0.85)
y_start, y_end = int(h*0.15), int(h*0.85)
STEPS = 21
xs = np.linspace(x_start, x_end, STEPS, dtype=np.int64).clip(0, w-1)
ys = np.linspace(y_start, y_end, STEPS, dtype=np.int64).clip(0, h-1)
gx, gy = np.meshgrid(xs, ys)
samples = arr_f[gy, gx, :].reshape(-1, 3)
content_ref = np.median(samples, axis=0)
print(f'\ncontent_ref = ({content_ref[0]:.0f}, {content_ref[1]:.0f}, {content_ref[2]:.0f})')

# 检查间隙层识别
GAP_COLOR_DIST = 30.0
is_gap_layer = []
for c, _ in border_layers:
    d = float(np.sqrt(np.sum((np.array(c, dtype=np.float64) - content_ref) ** 2)))
    is_gap = d < GAP_COLOR_DIST
    is_gap_layer.append(is_gap)
    print(f'  层{len(is_gap_layer)-1}: color={c}, dist_to_content={d:.1f}, is_gap={is_gap}')

cumulative_depths = [0]
for _, thickness in border_layers:
    cumulative_depths.append(cumulative_depths[-1] + thickness)

gap_regions = []
for i, is_gap in enumerate(is_gap_layer):
    if is_gap:
        gap_regions.append((cumulative_depths[i], cumulative_depths[i + 1]))
        print(f'  gap_region: depth [{cumulative_depths[i]}, {cumulative_depths[i+1]})')

# 运行裁剪
corners = {'tl': 8.0, 'tr': 8.0, 'bl': 8.0, 'br': 8.0}
dpi = 150

result = apply_border_only_corners(test_img, corners, dpi, bg_color)
result_arr = np.array(result)
rw, rh = result.size

# 实际半径
r_px = int(round(8.0 * dpi / 2.54))
r_cap = max(1, min(rw, rh) // 2)
r_actual = min(r_px, r_cap)
print(f'\n实际圆角半径: {r_actual}px (原图{w}x{h})')

# 检查每个角
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
    
    if roi_x2 <= roi_x1 or roi_y2 <= roi_y1:
        continue
    
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
    
    for gi, (gap_start, gap_end) in enumerate(gap_regions):
        gap_pixels = valid_region & (depth >= gap_start) & (depth < gap_end)
        count = np.sum(gap_pixels)
        if count == 0:
            print(f'  {corner_key}: gap[{gap_start},{gap_end}) - 0 pixels')
            continue
        
        yy_g, xx_g = np.where(gap_pixels)
        global_y = yy_g + roi_y1
        global_x = xx_g + roi_x1
        actual_colors = result_arr[global_y, global_x, :].astype(np.float64)
        
        bg = np.array([255,255,255], dtype=np.float64)
        gap_color = np.array([245,235,220], dtype=np.float64)
        
        d_bg = np.sqrt(np.sum((actual_colors - bg) ** 2, axis=1))
        d_gap = np.sqrt(np.sum((actual_colors - gap_color) ** 2, axis=1))
        
        near_bg = np.sum(d_bg < 30)
        near_gap = np.sum(d_gap < 30)
        
        print(f'  {corner_key} gap[{gap_start},{gap_end}): {count}px total')
        print(f'    near_bg(白<30): {near_bg}/{count} ({100*near_bg/count:.1f}%)')
        print(f'    near_gap(米黄<30): {near_gap}/{count} ({100*near_gap/count:.1f}%)')
        if near_gap > 0:
            # 看看这些米黄色像素的深度分布
            gap_depths = depth[gap_pixels][d_gap < 30]
            print(f'    米黄色像素深度范围: [{gap_depths.min():.0f}, {gap_depths.max():.0f}]')

print('\n===== 诊断完成 =====')