"""
测试修复是否真正生效 - 直接比较修复前后的像素
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
from PIL import Image
from core.image_cropper import apply_border_only_corners
from core.corner.algorithm import CORNER_ANGLES

# 创建模拟花漾之约的图片
w, h = 1134, 1701
bg_color = (255, 255, 255)

img = Image.new('RGB', (w, h), bg_color)
arr = np.array(img)

# 黑色外边框 (30px)
arr[:30, :, :] = (15, 15, 15)
arr[-30:, :, :] = (15, 15, 15)
arr[:, :30, :] = (15, 15, 15)
arr[:, -30:, :] = (15, 15, 15)

# 白色点状间隙
for y in range(35, h - 35, 8):
    for x in range(35, w - 35, 8):
        if (x - y) % 16 == 0:
            arr[y, x, :] = (255, 255, 255)

# 内容区域
arr[50:-50, 50:-50] = (250, 248, 245)

img = Image.fromarray(arr)

dpi = 72
r_cm = 3.0
r_px = int(r_cm * dpi / 2.54)
corners = {'tl': r_cm, 'tr': r_cm, 'bl': r_cm, 'br': r_cm}

# 运行圆角裁剪
result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)
result_arr = np.array(result)

# 详细分析每个角
print("=" * 70)
print("详细分析：修复后花漾之约边界白色像素")
print("=" * 70)

for ck in ['tl', 'tr', 'bl', 'br']:
    ang_min, ang_max = CORNER_ANGLES[ck]
    
    if ck == 'tl':
        cx, cy = r_px, r_px
    elif ck == 'tr':
        cx, cy = w - r_px, r_px
    elif ck == 'bl':
        cx, cy = r_px, h - r_px
    else:
        cx, cy = w - r_px, h - r_px
    
    yy, xx = np.mgrid[0:h, 0:w]
    dx = xx.astype(np.float64) - cx
    dy = yy.astype(np.float64) - cy
    dist = np.sqrt(dx**2 + dy**2)
    angle = np.degrees(np.arctan2(dy, dx))
    angle = np.mod(angle, 360.0)
    
    valid_angle = (angle >= ang_min) & (angle <= ang_max)
    
    # 检查所有白色像素（>245）在边界带(ang_max ± 2度)
    boundary_band = valid_angle & (angle >= ang_max - 2) & (angle <= ang_max + 2)
    
    # 也检查边界带扩展(ang_max ± 5度)
    boundary_band_ext = (angle >= ang_max - 5) & (angle <= ang_max + 5)
    
    result_white = np.all(result_arr > 245, axis=2)
    
    # 边界带内的白色像素
    white_in_boundary = result_white & boundary_band
    # 扩展边界带内的白色像素
    white_in_boundary_ext = result_white & boundary_band_ext
    
    # 排除背景色的白色像素（因为背景也是白色）
    bg_arr = np.array(bg_color, dtype=np.float64)
    if np.any(white_in_boundary):
        wb_coords = np.where(white_in_boundary)
        wb_pixels = result_arr[wb_coords[0], wb_coords[1]].astype(np.float64)
        dist_to_bg = np.sqrt(np.sum((wb_pixels - bg_arr.reshape(1, 3)) ** 2, axis=1))
        not_bg = dist_to_bg > 5.0  # 不是背景色
    else:
        not_bg = np.array([])
    
    print(f"\n{ck}角 (ang_min={ang_min}, ang_max={ang_max}):")
    print(f"  边界带(ang_max±2°)白色像素: {np.sum(white_in_boundary)}")
    print(f"  扩展边界带(ang_max±5°)白色像素: {np.sum(white_in_boundary_ext)}")
    print(f"  其中非背景色的白色像素: {np.sum(not_bg)}")
    
    # 分析这些白色像素的深度分布
    if np.any(white_in_boundary):
        wb_dists = dist[wb_coords]
        wb_depths = r_px - wb_dists
        
        # 深度分布
        in_border = (wb_depths >= 0) & (wb_depths < 31)
        in_gap = (wb_depths >= 31) & (wb_depths < 50)
        in_content = wb_depths >= 50
        
        # 位置分布
        outside_arc = wb_dists > r_px
        inside_arc = wb_dists <= r_px
        
        print(f"  深度分布:")
        print(f"    边框区域(depth<31): {np.sum(in_border)}")
        print(f"    间隙区域(31<=depth<50): {np.sum(in_gap)}")
        print(f"    内容区域(depth>=50): {np.sum(in_content)}")
        print(f"  位置分布:")
        print(f"    弧外侧(dist>r): {np.sum(outside_arc)}")
        print(f"    弧内侧(dist<=r): {np.sum(inside_arc)}")
        
        # 检查像素颜色是否与背景完全相同
        wb_colors = result_arr[wb_coords[0], wb_coords[1]]
        all_bg = np.all(wb_colors == np.array(bg_color), axis=1)
        print(f"  完全等于背景色的像素: {np.sum(all_bg)}")
        print(f"  不等于背景色的白色像素: {np.sum(~all_bg)}")
        
        # 显示不等于背景色的像素详情
        if np.any(~all_bg):
            not_bg_idx = np.where(~all_bg)[0][:5]
            for idx in not_bg_idx:
                y, x = wb_coords[0][idx], wb_coords[1][idx]
                d = dist[y, x]
                depth = r_px - d
                c = result_arr[y, x]
                print(f"    像素({x},{y}): dist={d:.1f}, depth={depth:.1f}, color={c}")
