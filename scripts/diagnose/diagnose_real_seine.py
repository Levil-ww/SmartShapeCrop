"""
端到端诊断脚本：使用真实塞纳时光图像
"""
import numpy as np
from PIL import Image
import sys, os
sys.path.insert(0, "d:\\SmartShapeCrop")

from core.corner.detection import _get_border_layers_robust, detect_nested_rect_layers
from core.image_cropper import apply_border_only_corners, crop_image, CropConfig, load_source_image
from core.corner.algorithm import CORNER_ANGLES

# 尝试加载真实图像
real_path = r"\\192.168.1.199\打印文件\方图库\成品图\5088663_宁波大魔法（店铺端）\双面格-定制-定制尺寸-塞纳时光;80x160.jpg"

if not os.path.isfile(real_path):
    # 尝试另一个路径格式
    alt_path = r"//192.168.1.199/打印文件/方图库/成品图/5088663_宁波大魔法（店铺端）/双面格-定制-定制尺寸-塞纳时光;80x160.jpg"
    if os.path.isfile(alt_path):
        real_path = alt_path
    else:
        print(f'[ERROR] 无法访问网络路径')
        print(f'  尝试路径1: {real_path}')
        print(f'  尝试路径2: {alt_path}')
        sys.exit(1)

print(f'加载源图: {real_path}')
src = load_source_image(real_path)
sw, sh = src.size
print(f'源图尺寸: {sw}x{sh} px, 模式: {src.mode}')

# 裁剪参数 (与用户截图一致: 80x160cm, 8cm圆角, 简单缩放)
target_w_cm = 81.0  # 80 + 1cm cut loss
target_h_cm = 161.0  # 160 + 1cm cut loss
corner_r_cm = 8.0
dpi = 150
bg_color = (255, 255, 255)

target_w_px = int(round(target_w_cm * dpi / 2.54))
target_h_px = int(round(target_h_cm * dpi / 2.54))
print(f'\n目标尺寸: {target_w_px}x{target_h_px} px ({target_w_cm}x{target_h_cm}cm @ {dpi}DPI)')

# 1. 先缩放（simple_resize 模式）
cropped = src.resize((target_w_px, target_h_px), Image.LANCZOS)
cw, ch = cropped.size
print(f'缩放后: {cw}x{ch} px')

# 2. 检测边框层
print('\n===== 边框层检测 =====')
border_layers = _get_border_layers_robust(cropped, bg_color)
print(f'检测到 {len(border_layers)} 层边框:')
for i, (color, thickness) in enumerate(border_layers):
    print(f'  第{i+1}层: 颜色={color}, 厚度={thickness}px')

# 3. 计算 content_ref
arr_f = np.array(cropped).astype(np.float64)
x_start, x_end = int(cw*0.15), int(cw*0.85)
y_start, y_end = int(ch*0.15), int(ch*0.85)
STEPS = 21
xs = np.linspace(x_start, x_end, STEPS, dtype=np.int64).clip(0, cw-1)
ys = np.linspace(y_start, y_end, STEPS, dtype=np.int64).clip(0, ch-1)
gx, gy = np.meshgrid(xs, ys)
samples = arr_f[gy, gx, :].reshape(-1, 3)
content_ref = np.median(samples, axis=0)
print(f'\ncontent_ref = ({content_ref[0]:.0f}, {content_ref[1]:.0f}, {content_ref[2]:.0f})')

# 4. 检查间隙层识别
print('\n===== 间隙层识别 =====')
GAP_COLOR_DIST = 30.0
is_gap_layer = []
for c, thickness in border_layers:
    d = float(np.sqrt(np.sum((np.array(c, dtype=np.float64) - content_ref) ** 2)))
    is_gap = d < GAP_COLOR_DIST
    is_gap_layer.append(is_gap)
    print(f'  层{len(is_gap_layer)-1}: color={c}, thickness={thickness}px, dist_to_content={d:.1f}, is_gap={is_gap}')

cumulative_depths = [0]
for _, thickness in border_layers:
    cumulative_depths.append(cumulative_depths[-1] + thickness)

gap_regions = []
for i, is_gap in enumerate(is_gap_layer):
    if is_gap:
        gap_regions.append((cumulative_depths[i], cumulative_depths[i + 1]))
        print(f'  gap_region: depth [{cumulative_depths[i]}, {cumulative_depths[i+1]})')

if not gap_regions:
    print('  [WARNING] 没有检测到任何间隙层！')

# 5. 运行完整裁剪流程
print('\n===== 应用圆角裁剪 =====')
corners = {'tl': corner_r_cm, 'tr': corner_r_cm, 'bl': corner_r_cm, 'br': corner_r_cm}
result = apply_border_only_corners(cropped, corners, dpi, bg_color)
result_arr = np.array(result)
rw, rh = result.size

r_px = int(round(corner_r_cm * dpi / 2.54))
r_cap = max(1, min(rw, rh) // 2)
r_actual = min(r_px, r_cap)
print(f'实际圆角半径: {r_actual}px')

# 6. 检查每个角的间隙层区域
print('\n===== 验证间隙层区域 =====')
total_gap_pixels = 0
total_near_gap = 0
total_near_bg = 0

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
        print(f'  {corner_key}: ROI invalid')
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
        total_gap_pixels += count
        if count == 0:
            continue
        
        yy_g, xx_g = np.where(gap_pixels)
        global_y = yy_g + roi_y1
        global_x = xx_g + roi_x1
        actual_colors = result_arr[global_y, global_x, :].astype(np.float64)
        
        bg = np.array([255,255,255], dtype=np.float64)
        d_bg = np.sqrt(np.sum((actual_colors - bg) ** 2, axis=1))
        near_bg = np.sum(d_bg < 30)
        total_near_bg += near_bg
        
        # 检查与每个边框层颜色的距离
        for li, (bl_color, _) in enumerate(border_layers):
            bl_arr = np.array(bl_color, dtype=np.float64)
            d_bl = np.sqrt(np.sum((actual_colors - bl_arr) ** 2, axis=1))
            near_bl = np.sum(d_bl < 30)
            if near_bl > 0:
                total_near_gap += near_bl
                print(f'  {corner_key} gap[{gap_start},{gap_end}): {count}px, 其中{near_bl}px 接近层{li}颜色{bl_color}')
                if li == 1:
                    # 这就是间隙层！
                    gap_depths = depth[gap_pixels][d_bg >= 30]
                    if len(gap_depths) > 0:
                        print(f'    → 间隙层泄漏！泄漏像素深度范围: [{gap_depths.min():.0f}, {gap_depths.max():.0f}]')

if total_gap_pixels > 0:
    print(f'\n总计间隙层像素: {total_gap_pixels}')
    print(f'  接近背景色(白<30): {total_near_bg}/{total_gap_pixels} ({100*total_near_bg/total_gap_pixels:.1f}%)')
    print(f'  接近间隙色: {total_near_gap}/{total_gap_pixels} ({100*total_near_gap/total_gap_pixels:.1f}%)')
else:
    print('  没有间隙层像素 (间隙层厚度为0)')

# 7. 保存结果图供检查
output_dir = r"d:\SmartShapeCrop\scripts\diagnose"
os.makedirs(output_dir, exist_ok=True)
output_path = os.path.join(output_dir, "diagnose_seine_result.jpg")
result.save(output_path, 'JPEG', quality=95)
print(f'\n结果图已保存: {output_path}')

print('\n===== 诊断完成 =====')