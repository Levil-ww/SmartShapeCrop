"""
精确追踪墨上花开案例的弧边界像素处理流程
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
from PIL import Image
from core.image_cropper import apply_border_only_corners
from core.corner import sector_render


def trace_boundary_pixel():
    """追踪弧边界像素的完整处理流程"""
    print("=" * 70)
    print("追踪弧边界像素 (墨上花开 tl角)")
    print("=" * 70)

    w, h = 2835, 5670
    bg_color = (255, 255, 255)

    img = Image.new('RGB', (w, h), bg_color)
    arr = np.array(img)

    # 边框结构
    border_color = (25, 20, 18)
    gap_color = (235, 228, 215)
    inner_color = (30, 25, 22)

    arr[:45, :, :] = border_color
    arr[-45:, :, :] = border_color
    arr[:, :45, :] = border_color
    arr[:, -45:, :] = border_color

    arr[45:65, :, :] = gap_color
    arr[-65:-45, :, :] = gap_color
    arr[:, 45:65, :] = gap_color
    arr[:, -65:-45, :] = gap_color

    arr[65:90, :, :] = inner_color
    arr[-90:-65, :, :] = inner_color
    arr[:, 65:90, :] = inner_color
    arr[:, -90:-65, :] = inner_color

    img = Image.fromarray(arr)

    dpi = 72
    r_cm = 10.0
    r_px = int(r_cm * dpi / 2.54)

    # 直接调用 _redraw_border_on_corner 进行追踪
    from core.image_cropper import _get_border_layers_robust, _filter_gap_layers

    border_layers, is_gap_layer, _ = _get_border_layers_robust(img, bg_color=bg_color)
    border_layers = _filter_gap_layers(border_layers, bg_color=bg_color)

    print(f"  检测到的边框层:")
    for i, ((color, thickness), is_gap) in enumerate(zip(border_layers, is_gap_layer)):
        print(f"    Layer {i}: color={color}, thickness={thickness}, is_gap={is_gap}")

    print(f"\n  corner_radius_px = {r_px}")
    print(f"  R_total (inside function) = {r_px}")

    # 计算累积深度
    cumulative_depths = [0]
    for _, thickness in border_layers:
        cumulative_depths.append(cumulative_depths[-1] + thickness)
    total_border_depth = cumulative_depths[-1]
    print(f"  total_border_depth = {total_border_depth}")
    print(f"  cumulative_depths = {cumulative_depths}")

    # 分析坐标：找一个边界像素
    # tl corner: center at (r_px, r_px) = (283, 283)
    # arc boundary pixel at angle 45°:
    #   x = cx + r_px * cos(45°) = 283 + 283 * 0.7071 = 483
    #   y = cy + r_px * sin(45°) = 283 + 283 * 0.7071 = 483
    # 这个像素在弧边界上 (dist = r_px)
    
    cx, cy = r_px, r_px
    angle_deg = 45
    angle_rad = np.radians(angle_deg)
    test_x = int(round(cx + r_px * np.cos(angle_rad)))
    test_y = int(round(cy + r_px * np.sin(angle_rad)))

    print(f"\n  测试像素位置: ({test_x}, {test_y})")
    dx = test_x - cx
    dy = test_y - cy
    dist = np.sqrt(dx**2 + dy**2)
    print(f"  距离中心: {dist:.2f}")
    print(f"  原图颜色: {arr[test_y, test_x]}")

    # 手动计算这个像素会落在哪个 depth band
    pixel_depth = r_px - dist
    print(f"  像素 depth (R_total - dist): {pixel_depth:.2f}")

    # 查找这个 depth 对应的 color_idx
    color_idx = 0
    for i in range(len(border_layers)):
        cum_i = cumulative_depths[i]
        cum_next = cumulative_depths[i + 1]
        if cum_i <= pixel_depth < cum_next:
            color_idx = i
            break
        elif pixel_depth >= cum_next and i == len(border_layers) - 1:
            color_idx = i

    print(f"  color_idx = {color_idx}")
    print(f"  目标颜色: {border_layers[color_idx][0]}")

    # 现在测试实际处理
    corners = {'tl': r_cm}
    result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)
    result_arr = np.array(result)

    print(f"\n  处理后像素颜色: {result_arr[test_y, test_x]}")

    # 统计弧边界区域的结果
    yy, xx = np.mgrid[0:h, 0:w]
    dx_grid = xx - cx
    dy_grid = yy - cy
    dist_grid = np.sqrt(dx_grid**2 + dy_grid**2)
    angle_grid = np.degrees(np.arctan2(dy_grid, dx_grid))
    angle_grid = np.mod(angle_grid, 360.0)

    ang_min, ang_max = sector_render.CORNER_ANGLES['tl']
    valid_angle = (angle_grid >= ang_min) & (angle_grid <= ang_max)

    # 检查 dist=283 (R_total) 处的像素
    exact_boundary = valid_angle & (dist_grid == r_px)
    near_boundary = valid_angle & (dist_grid >= r_px - 2) & (dist_grid <= r_px + 2)

    print(f"\n  精确边界 (dist={r_px}) 像素数: {np.sum(exact_boundary)}")
    if np.any(exact_boundary):
        exact_pixels = result_arr[exact_boundary]
        exact_white = np.sum(np.all(exact_pixels > 250, axis=1))
        exact_black = np.sum(np.all(exact_pixels < 50, axis=1))
        exact_other = len(exact_pixels) - exact_white - exact_black
        print(f"    白色: {exact_white}")
        print(f"    黑色: {exact_black}")
        print(f"    其他: {exact_other}")

    print(f"\n  近边界 (dist∈[{r_px-2},{r_px+2}]) 像素数: {np.sum(near_boundary)}")
    if np.any(near_boundary):
        near_pixels = result_arr[near_boundary]
        near_white = np.sum(np.all(near_pixels > 250, axis=1))
        near_black = np.sum(np.all(near_pixels < 50, axis=1))
        near_other = len(near_pixels) - near_white - near_black
        print(f"    白色: {near_white}")
        print(f"    黑色: {near_black}")
        print(f"    其他: {near_other}")

    # 检查弧内侧的图案像素保留
    inside_arc = valid_angle & (dist_grid < r_px)
    pattern_in_inside = np.all(result_arr == np.array([180, 150, 100]), axis=2) & inside_arc
    orig_pattern_in_inside = np.all(arr == np.array([180, 150, 100]), axis=2) & inside_arc
    print(f"\n  弧内侧图案像素 (原图): {np.sum(orig_pattern_in_inside)}")
    print(f"  弧内侧图案像素 (结果): {np.sum(pattern_in_inside)}")

    # 关键诊断：检查 valid_region 和 d_region 的计算
    print(f"\n  --- 深入诊断 ---")
    print(f"  对于边界像素 (dist={r_px}, depth=0):")
    print(f"    valid_region 条件: valid_angle=True, dist<=R_total={r_px} → True")
    print(f"    d_region 条件 (d=0): depth>=0=True, depth<1=True, dist<=R_total=True → True")
    print(f"    该像素应被 d=0 迭代处理")
    
    # 检查 gap_regions
    gap_regions = []
    for i, is_gap in enumerate(is_gap_layer):
        if is_gap:
            gap_regions.append((cumulative_depths[i], cumulative_depths[i + 1]))
    print(f"    gap_regions: {gap_regions}")
    
    # 检查该像素是否在 gap_region 中
    for gap_start, gap_end in gap_regions:
        if pixel_depth >= gap_start and pixel_depth < gap_end:
            print(f"    !! 警告: depth={pixel_depth:.2f} 在 gap_region [{gap_start},{gap_end}) 内!")
            print(f"       该像素会被间隙层逻辑处理")
    
    return True


if __name__ == '__main__':
    trace_boundary_pixel()
