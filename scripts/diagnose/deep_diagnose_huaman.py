"""
深度诊断花幔和墨上花开的间隙层检测和重绘逻辑
"""
import numpy as np
from PIL import Image
import sys
sys.path.insert(0, "d:\\SmartShapeCrop")

from core.corner.detection import _detect_border_layers

def deep_diagnose(name, w, h, layers, bg_color=(255, 255, 255)):
    """深度诊断边框层检测和间隙层处理"""
    print(f"\n===== 深度诊断 {name} =====")
    
    # 构造图像
    img_arr = np.zeros((h, w, 3), dtype=np.uint8)
    img_arr[:, :] = bg_color
    
    cumulative = 0
    for color, thickness in layers:
        img_arr[cumulative:cumulative+thickness, cumulative:w-cumulative, :] = color
        img_arr[h-cumulative-thickness:h-cumulative, cumulative:w-cumulative, :] = color
        img_arr[cumulative:h-cumulative, cumulative:cumulative+thickness, :] = color
        img_arr[cumulative:h-cumulative, w-cumulative-thickness:w-cumulative, :] = color
        cumulative += thickness
    
    inner_color = layers[-1][0]
    inner_thickness = cumulative
    img_arr[inner_thickness:h-inner_thickness, inner_thickness:w-inner_thickness, :] = inner_color
    
    test_img = Image.fromarray(img_arr, 'RGB')
    
    # 1. 检测边框层
    print("\n1. 边框层检测:")
    border_layers = _detect_border_layers(test_img, bg_color=bg_color)
    for i, (color, thickness) in enumerate(border_layers):
        print(f"  层{i}: 颜色={color}, 厚度={thickness}px")
    
    # 2. 计算 content_ref
    arr = np.array(test_img)
    hh, ww = arr.shape[:2]
    x_start = int(ww * 0.15)
    x_end = int(ww * 0.85)
    y_start = int(hh * 0.15)
    y_end = int(hh * 0.85)
    STEPS = 21
    xs = np.linspace(x_start, x_end, STEPS, dtype=np.int64).clip(0, ww - 1)
    ys = np.linspace(y_start, y_end, STEPS, dtype=np.int64).clip(0, hh - 1)
    gx, gy = np.meshgrid(xs, ys)
    samples = arr[gy, gx, :].reshape(-1, 3).astype(np.float64)
    content_ref = np.median(samples, axis=0)
    print(f"\n2. content_ref = ({content_ref[0]:.0f}, {content_ref[1]:.0f}, {content_ref[2]:.0f})")
    
    # 3. 检测间隙层
    GAP_COLOR_DIST = 30.0
    is_gap_layer = []
    for i, (c, t) in enumerate(border_layers):
        d = float(np.sqrt(np.sum((np.array(c, dtype=np.float64) - content_ref) ** 2)))
        is_gap = d < GAP_COLOR_DIST
        is_gap_layer.append(is_gap)
        print(f"  层{i}: color={c}, dist_to_content={d:.1f}, is_gap={is_gap}")
    
    # 4. 计算间隙区域
    cumulative_depths = [0]
    for _, thickness in border_layers:
        cumulative_depths.append(cumulative_depths[-1] + thickness)
    
    gap_regions = []
    for i, ig in enumerate(is_gap_layer):
        if ig:
            gap_regions.append((cumulative_depths[i], cumulative_depths[i + 1]))
    
    print(f"\n3. gap_regions: {gap_regions}")
    
    # 5. 构造不含间隙层的边框颜色数组
    solid_border_colors = [
        np.array(c, dtype=np.float64) for (c, _), ig in zip(border_layers, is_gap_layer) if not ig
    ]
    print(f"\n4. solid_border_colors ({len(solid_border_colors)} 层):")
    for i, bc in enumerate(solid_border_colors):
        print(f"  层{i}: {tuple(bc.astype(int))}")
    
    # 6. 模拟间隙区域检测
    dpi = 150
    corners = {'tl': 5.0, 'tr': 5.0, 'bl': 5.0, 'br': 5.0}
    r_px = int(round(corners['bl'] * dpi / 2.54))
    
    from core.corner.algorithm import CORNER_ANGLES
    from core.corner.sector_render import _build_border_sector_mask
    
    bg_arr = np.array(bg_color, dtype=np.float64)
    
    for corner_key in ['bl', 'br']:
        if corner_key == 'bl':
            cx, cy = r_px, h - r_px
        else:
            cx, cy = w - r_px, h - r_px
        
        R_total = min(r_px, max(1, min(w, h) // 2))
        
        x1 = max(0, cx - R_total)
        y1 = max(0, cy - R_total)
        x2 = min(w, cx + R_total + 1)
        y2 = min(h, cy + R_total + 1)
        
        yy, xx = np.mgrid[y1:y2, x1:x2].astype(np.float64)
        dx = xx - float(cx)
        dy = yy - float(cy)
        dist = np.sqrt(dx * dx + dy * dy)
        depth = float(R_total) - dist
        angle = np.degrees(np.arctan2(dy, dx))
        angle = np.mod(angle, 360.0)
        
        ang_min, ang_max = CORNER_ANGLES[corner_key]
        valid_region = (angle >= ang_min) & (angle <= ang_max) & (dist <= R_total + 2.0)
        
        print(f"\n5. {corner_key}角 间隙区域分析:")
        print(f"  有效区域像素数: {np.sum(valid_region)}")
        
        for (gap_start, gap_end) in gap_regions:
            gap_paint_mask = valid_region & (depth >= float(gap_start)) & (depth < float(gap_end))
            n_gap = np.sum(gap_paint_mask)
            print(f"  gap [{gap_start}, {gap_end}): {n_gap} px")
            
            if n_gap == 0:
                continue
            
            gap_ys, gap_xs = np.where(gap_paint_mask)
            global_y = gap_ys + y1
            global_x = gap_xs + x1
            check_colors = arr[global_y, global_x, :].astype(np.float64)
            
            # 用 solid_border_colors 排除过渡像素
            not_border_like = np.ones(n_gap, dtype=bool)
            for bc_arr in solid_border_colors:
                d_border = np.sqrt(np.sum((check_colors - bc_arr.reshape(1, 3)) ** 2, axis=1))
                not_border_like &= (d_border > 15.0)
            
            n_non_transition = np.sum(not_border_like)
            print(f"    非过渡像素 (排除solid边框): {n_non_transition}/{n_gap}")
            
            if n_non_transition > 10:
                non_transition_colors = check_colors[not_border_like]
                channel_ranges = np.ptp(non_transition_colors, axis=0)
                mean_range = float(np.mean(channel_ranges))
                print(f"    颜色极差: R={channel_ranges[0]:.1f}, G={channel_ranges[1]:.1f}, B={channel_ranges[2]:.1f}")
                print(f"    mean_range={mean_range:.2f}")
                print(f"    DECORATION_RANGE_THRESH=8.0")
                print(f"    判定: {'装饰间隙 (保留)' if mean_range > 8.0 else '均匀间隙 (清空)'}")
                
                # 显示实际颜色分布
                unique_colors, counts = np.unique(non_transition_colors.astype(np.uint8).reshape(-1, 3), axis=0, return_counts=True)
                print(f"    主要颜色:")
                sorted_idx = np.argsort(-counts)[:3]
                for idx in sorted_idx:
                    color = tuple(unique_colors[idx])
                    count = counts[idx]
                    d_to_bg = np.sqrt(np.sum((np.array(color, dtype=np.float64) - bg_arr) ** 2))
                    d_to_content = np.sqrt(np.sum((np.array(color, dtype=np.float64) - content_ref) ** 2))
                    print(f"      {color}: {count}px, d_to_bg={d_to_bg:.1f}, d_to_content={d_to_content:.1f}")

# 花幔
huaman_layers = [
    ((30, 25, 20), 4),
    ((230, 225, 210), 8),
    ((210, 195, 170), 20),
]
deep_diagnose("花幔", 800, 1600, huaman_layers)

# 墨上花开
moshang_layers = [
    ((25, 20, 15), 5),
    ((240, 230, 215), 10),
    ((220, 200, 180), 30),
]
deep_diagnose("墨上花开", 790, 1590, moshang_layers)