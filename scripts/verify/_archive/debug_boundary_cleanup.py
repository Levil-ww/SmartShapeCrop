"""
调试边界白色像素清扫逻辑
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
from PIL import Image
from core.image_cropper import apply_border_only_corners
from core.corner.algorithm import CORNER_ANGLES
from core.corner.sector_render import _redraw_border_on_corner

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

print(f"r_px = {r_px}")

# 直接测试 _redraw_border_on_corner
# 先获取 border_layers
from core.corner.detection import _get_border_layers_robust
border_layers = _get_border_layers_robust(img, bg_color)
print(f"\nborder_layers: {border_layers}")

# 创建一个结果图像先做基础裁切
# 先运行完整流程获取中间状态
result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)
result_arr = np.array(result)

# 分析每个角
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
    
    # 扩展角度范围
    ang_min_ext = max(0, ang_min - 5)
    ang_max_ext = min(360, ang_max + 5)
    
    if ang_max == 360 or ang_max_ext > 360:
        ext_angle = (angle >= ang_min_ext) | (angle <= ang_max_ext % 360)
    else:
        ext_angle = (angle >= ang_min_ext) & (angle <= ang_max_ext)
    
    total_border_depth = sum(t for _, t in border_layers) if border_layers else 0
    cleanup_inner_depth = max(total_border_depth + 15, 50)
    cleanup_inner_dist = float(r_px) - float(cleanup_inner_depth)
    cleanup_outer_dist = float(r_px) + 5.0
    
    cleanup_depth_range = (dist >= cleanup_inner_dist) & (dist <= cleanup_outer_dist)
    boundary_cleanup_region = ext_angle & cleanup_depth_range
    
    # 分析这个区域
    cleanup_coords = np.where(boundary_cleanup_region)
    print(f"\n{ck}角:")
    print(f"  扩展角度范围: [{ang_min_ext}, {ang_max_ext}]")
    print(f"  深度范围(dist): [{cleanup_inner_dist}, {cleanup_outer_dist}]")
    print(f"  总边框厚度: {total_border_depth}")
    print(f"  清理区域像素数: {len(cleanup_coords[0])}")
    
    if len(cleanup_coords[0]) > 0:
        cleanup_colors = result_arr[cleanup_coords[0], cleanup_coords[1], :].astype(np.float64)
        
        is_white = np.all(cleanup_colors > 240, axis=1)
        print(f"  白色像素数: {np.sum(is_white)}")
        
        # 边框色检测
        solid_border_colors_arr = np.array(
            [np.array(c, dtype=np.float64) for (c, _) in border_layers]
        )
        print(f"  实心边框色数组: {solid_border_colors_arr}")
        
        is_border_like = np.zeros(len(cleanup_coords[0]), dtype=bool)
        for bc in solid_border_colors_arr:
            d_to_bc = np.sqrt(np.sum((cleanup_colors - bc.reshape(1, 3)) ** 2, axis=1))
            is_border_like |= (d_to_bc <= 25.0)
        
        should_clean = is_white & (~is_border_like)
        print(f"  应清除像素数: {np.sum(should_clean)}")
        
        if np.sum(is_white) > 0:
            # 显示一些白色像素的位置
            white_indices = np.where(is_white)[0][:5]
            for idx in white_indices:
                y, x = cleanup_coords[0][idx], cleanup_coords[1][idx]
                d = dist[y, x]
                a = angle[y, x]
                c = result_arr[y, x]
                print(f"    像素({x},{y}): dist={d:.1f}, angle={a:.1f}, color={c}")
