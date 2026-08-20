"""
core/corner/sector_render.py
圆角弧线上的边框层重绘：多层同心圆弧设计。

从原 core/image_cropper.py 拆分而来，逻辑未变化。

核心设计（与 project_memory 一致）：
  - 所有边框层共享同一个圆心（外层圆角圆心），形成同心圆弧
  - cumulative_i = 所有外层边框的累计厚度
  - R_eff_i = max(0, R_total - cumulative_i)
  - 厚度由径向条件 d_outer <= d_p < d_inner 控制（与角度无关）
  - 角度范围与 carve_corner_on_mask 的 pieslice 角度完全一致（CORNER_ANGLES）

向后兼容：原 core/image_cropper.py 已改为薄重导出 shim，旧导入路径继续可用。
"""
from __future__ import annotations
import numpy as np
from PIL import Image

from .algorithm import CORNER_ANGLES


def _build_border_sector_mask(
    w: int, h: int, corner_key: str, cx: int, cy: int, R: int,
    d_outer: float, d_inner: float
) -> np.ndarray:
    """
    构建单个边框层在圆弧上的精确遮罩（固定角度扇区形状）。

    遮罩原理：对于每个像素 p 在角落区域：
      - r = 到该层圆心距离
      - d_p = R - r （该层自身坐标系下的深度，正值在弧内）
      - 保留条件：
        1) d_outer <= d_p < d_inner （径向范围属于该层）
        2) angle_p 在该角固定角度范围内
           （固定角度 = pieslice 角度，确保直线到圆弧的完整连接）

    注意：cx, cy 已经是该层独立的圆心位置（由调用方根据累计偏移调整），
    R 是该层的有效圆角半径（R_eff = R_total - cumulative_thickness）。
    返回 bool 数组 [H, W]，True 表示绘制区域。

    历史：早期版本使用 _angle_bottom/_angle_side 计算随深度收窄的角度，
    但这导致内层边框在圆弧上无法覆盖直线到圆弧的连接区域，
    产生白色扇形角和背景色漏出。修复为固定角度范围。
    """
    ang_min, ang_max = CORNER_ANGLES[corner_key]

    roi_x0 = max(0, cx - R)
    roi_y0 = max(0, cy - R)
    roi_x1 = min(w, cx + R + 1)
    roi_y1 = min(h, cy + R + 1)
    roi_w = roi_x1 - roi_x0
    roi_h = roi_y1 - roi_y0

    if roi_w <= 0 or roi_h <= 0:
        full_mask = np.zeros((h, w), dtype=bool)
        return full_mask

    yy, xx = np.mgrid[roi_y0:roi_y1, roi_x0:roi_x1].astype(np.float64)
    dx = xx - float(cx)
    dy = yy - float(cy)
    r = np.sqrt(dx * dx + dy * dy)
    d_p = float(R) - r

    cond_r = (d_p >= d_outer) & (d_p < d_inner)

    angle_p = np.degrees(np.arctan2(dy, dx))
    angle_p = np.mod(angle_p, 360.0)

    shifted_angle = np.mod(angle_p - ang_min, 360.0)
    angular_span = ang_max - ang_min
    cond_angle = shifted_angle <= angular_span

    roi_mask = cond_r & cond_angle

    full_mask = np.zeros((h, w), dtype=bool)
    full_mask[roi_y0:roi_y1, roi_x0:roi_x1] = roi_mask
    return full_mask


def _sample_border_color(
    src_img: Image.Image, corner_key: str,
    w: int, h: int, d_mid: float, d_thickness: float,
) -> tuple[int, int, int]:
    """
    从原图直线边框对应深度范围采样平均颜色，降低色差。

    [Fix B/S4 角落感知采样]：
      - 圆弧在某角只物理连接两条直线边（与角相邻的两条），
        因此只从这两条边采样才能得到"真正属于该边框层"的颜色。
      - 从该两条边的厚度中心（d_mid）取 ±35% 厚度范围的像素行/列，
        避开抗锯齿过渡带（跳过最外层 2px）。
      - 对所有采样像素取中位数（median），对抗离群抗锯齿像素。
      - 采样位置：距角点 ≥ 1/4 边长的中段纯直线区域。
    """
    arr = np.array(src_img)
    thickness = max(1.0, float(d_thickness))
    samples: list[np.ndarray] = []

    d_min = int(max(0, round(d_mid - thickness * 0.35)))
    d_max = int(min(max(d_min + 1, round(d_mid + thickness * 0.35)), max(w, h)))

    h_x0 = max(0, w * 1 // 4)
    h_x1 = min(w, w * 3 // 4)
    if h_x1 - h_x0 < 10:
        h_x0, h_x1 = max(0, w * 3 // 10), min(w, w * 7 // 10)

    v_y0 = max(0, h * 1 // 4)
    v_y1 = min(h, h * 3 // 4)
    if v_y1 - v_y0 < 10:
        v_y0, v_y1 = max(0, h * 3 // 10), min(h, h * 7 // 10)

    if corner_key == 'tl':
        sample_edges = ('top', 'left')
    elif corner_key == 'tr':
        sample_edges = ('top', 'right')
    elif corner_key == 'bl':
        sample_edges = ('bottom', 'left')
    else:
        sample_edges = ('bottom', 'right')

    for edge in sample_edges:
        for d in range(d_min, d_max):
            if edge == 'top':
                if 0 <= d < h and h_x1 > h_x0:
                    samples.append(arr[d, h_x0:h_x1, :])
            elif edge == 'bottom':
                y_pos = h - 1 - d
                if 0 <= y_pos < h and h_x1 > h_x0:
                    samples.append(arr[y_pos, h_x0:h_x1, :])
            elif edge == 'left':
                if 0 <= d < w and v_y1 > v_y0:
                    samples.append(arr[v_y0:v_y1, d, :])
            else:
                x_pos = w - 1 - d
                if 0 <= x_pos < w and v_y1 > v_y0:
                    samples.append(arr[v_y0:v_y1, x_pos, :])

    if not samples:
        fallback_y = min(h - 1, max(0, int(round(d_mid))))
        fallback_x = min(w - 1, max(0, int(round(d_mid))))
        return tuple(arr[fallback_y, fallback_x, :].tolist())

    all_pixels = np.concatenate([s.reshape(-1, 3) for s in samples if s.size > 0], axis=0)
    if all_pixels.shape[0] == 0:
        fallback_y = min(h - 1, max(0, int(round(d_mid))))
        fallback_x = min(w - 1, max(0, int(round(d_mid))))
        return tuple(arr[fallback_y, fallback_x, :].tolist())

    median_color = np.median(all_pixels, axis=0)
    return tuple(int(round(v)) for v in median_color.tolist())


def _redraw_border_on_corner(
    result_img: Image.Image, corner_key: str,
    corner_radius_px: int,
    border_layers: list[tuple[tuple[int, int, int], int]],
    src_img: Image.Image | None = None,
    validity_mask: Image.Image | None = None,
    only_outermost: bool = False,
    bg_color: tuple[int, int, int] = (255, 255, 255),
) -> None:
    """
    在圆角区域重新绘制边框颜色。

    [PERF 优化] 使用 ROI（Region of Interest）区域操作，
    仅对角落 r×r 区域进行 numpy 转换和计算，
    避免处理 1-2 亿像素的全图转换开销。

    Args:
        result_img: 结果图片（原地修改）
        corner_key: 角落标识 ('tl','tr','bl','br')
        corner_radius_px: 总圆角半径像素 (R_total)
        border_layers: 边框层 [(color_fallback, thickness), ...]
        src_img: 原图（用于采样颜色 + 判断是否为装饰像素）
        validity_mask: 可选，L模式。非零像素才允许修改
        only_outermost: 若为 True，只绘制最外层边框
        bg_color: 背景色，间隙层使用此色填充创建干净弧线
    """
    w, h = result_img.size
    if corner_radius_px <= 0 or not border_layers:
        return

    R_total = corner_radius_px
    R_total = min(R_total, max(1, min(w, h) // 2))
    if R_total <= 0:
        return

    if corner_key == 'tl':
        cx, cy = R_total, R_total
    elif corner_key == 'tr':
        cx, cy = w - R_total, R_total
    elif corner_key == 'bl':
        cx, cy = R_total, h - R_total
    else:
        cx, cy = w - R_total, h - R_total

    # [PERF] 计算 ROI 区域，仅处理角落关键区域
    x1 = max(0, cx - R_total)
    y1 = max(0, cy - R_total)
    x2 = min(w, cx + R_total + 1)
    y2 = min(h, cy + R_total + 1)

    if x2 <= x1 or y2 <= y1:
        return

    roi_w = x2 - x1
    roi_h = y2 - y1

    # [PERF] 仅提取 ROI 区域的 numpy 数组
    result_roi_img = result_img.crop((x1, y1, x2, y2))
    result_arr = np.array(result_roi_img, dtype=np.uint8)

    src_arr = None
    if src_img is not None:
        if src_img.size == result_img.size:
            src_roi_img = src_img.crop((x1, y1, x2, y2))
            src_arr = np.array(src_roi_img, dtype=np.uint8)
        else:
            src_resized = src_img.resize((w, h), Image.LANCZOS)
            src_roi_img = src_resized.crop((x1, y1, x2, y2))
            src_arr = np.array(src_roi_img, dtype=np.uint8)

    validity_arr = None
    if validity_mask is not None:
        validity_roi_img = validity_mask.crop((x1, y1, x2, y2))
        validity_arr = np.array(validity_roi_img, dtype=bool)

    # ROI 相对坐标的圆心
    cx_roi = cx - x1
    cy_roi = cy - y1

    cumulative_depths = [0]
    for _, thickness in border_layers:
        cumulative_depths.append(cumulative_depths[-1] + thickness)
    total_border_depth = cumulative_depths[-1]

    depth_mapping = {}
    for d in range(total_border_depth + 1):
        color_idx = 0
        for i in range(len(border_layers)):
            cum_i = cumulative_depths[i]
            cum_next = cumulative_depths[i + 1]
            if cum_i <= d < cum_next:
                color_idx = i
                break
            elif d >= cum_next and i == len(border_layers) - 1:
                color_idx = i
        depth_mapping[d] = color_idx

    border_colors_arr = np.array(
        [np.array(c, dtype=np.float64) for c, _ in border_layers]
    )
    COLOR_DIST_THRESHOLD = 15.0
    TRANSITION_THRESHOLD = 25.0

    # [PERF] _sample_content_ref 使用 ROI 切片尺寸
    def _sample_content_ref(arr_roi: np.ndarray, rw: int, rh: int) -> np.ndarray:
        x_start = int(rw * 0.15)
        x_end = int(rw * 0.85)
        y_start = int(rh * 0.15)
        y_end = int(rh * 0.85)
        STEPS = 21
        xs = np.linspace(x_start, x_end, STEPS, dtype=np.int64).clip(0, rw - 1)
        ys = np.linspace(y_start, y_end, STEPS, dtype=np.int64).clip(0, rh - 1)
        gx, gy = np.meshgrid(xs, ys)
        samples = arr_roi[gy, gx, :].reshape(-1, 3).astype(np.float64)
        if samples.shape[0] == 0:
            return np.array([255.0, 255.0, 255.0])
        return np.median(samples, axis=0)

    content_ref_arr: np.ndarray | None = None
    if src_arr is not None:
        content_ref_arr = _sample_content_ref(src_arr, roi_w, roi_h)
    if content_ref_arr is None:
        content_ref_arr = _sample_content_ref(result_arr, roi_w, roi_h)

    GAP_COLOR_DIST = 40.0  # Reduced from 60 to be more strict about gap detection
    GAP_MAX_THICKNESS = 30.0
    MAX_BORDER_DEPTH_RATIO = 2.0
    MAX_BORDER_DEPTH_HARD_PX = 500

    capped_by_ratio = MAX_BORDER_DEPTH_RATIO * R_total
    effective_border_depth = min(MAX_BORDER_DEPTH_HARD_PX, capped_by_ratio, total_border_depth)

    is_gap_layer = []
    for i, (c, t) in enumerate(border_layers):
        dist_to_content = float(np.sqrt(np.sum((np.array(c, dtype=np.float64) - content_ref_arr) ** 2)))
        cum_before = cumulative_depths[i]
        forced_gap = cum_before >= effective_border_depth
        col_arr = np.array(c, dtype=np.float64)

        # === [Fix 图二/图三] 增强型间隙检测 ===
        # 三判定策略，任一满足则为间隙层：
        #   1) 内容匹配: 颜色接近内容参考色 (dist < 60)
        #   2) 夹层判定: 被两个边框层夹住，且与两者颜色差异都大 (>25)
        #   3) 真间隙兜底: 颜色接近背景色，且被边框层夹住

        is_gap = False
        if not forced_gap and i > 0:
            # 判定1: 颜色接近内容参考色
            cond_content = (dist_to_content < GAP_COLOR_DIST and
                            t <= GAP_MAX_THICKNESS)

            # 判定2: 夹层检测 — 被两个非间隙边框层夹住的异色层
            # 这是间隙层的典型特征：边框-间隙-边框 结构
            cond_sandwich = False
            if i > 0 and i < len(border_layers) - 1:
                prev_c = np.array(border_layers[i - 1][0], dtype=np.float64)
                next_c = np.array(border_layers[i + 1][0], dtype=np.float64)
                d_prev = float(np.sqrt(np.sum((col_arr - prev_c) ** 2)))
                d_next = float(np.sqrt(np.sum((col_arr - next_c) ** 2)))
                # 与两侧颜色都差异大 (>25)，且两侧颜色相互接近 (<35)
                d_adjacent = float(np.sqrt(np.sum((prev_c - next_c) ** 2)))
                if (d_prev > 25.0 and d_next > 25.0 and
                    d_adjacent < 35.0 and t <= GAP_MAX_THICKNESS):
                    cond_sandwich = True

            # 判定3: 与相邻层颜色都差异大 (>30)，且夹在两层之间
            cond_neighbor_gap = False
            if i > 0 and i < len(border_layers) - 1 and t <= GAP_MAX_THICKNESS:
                prev_c = np.array(border_layers[i - 1][0], dtype=np.float64)
                next_c = np.array(border_layers[i + 1][0], dtype=np.float64)
                d_prev = float(np.sqrt(np.sum((col_arr - prev_c) ** 2)))
                d_next = float(np.sqrt(np.sum((col_arr - next_c) ** 2)))
                if d_prev > 30.0 and d_next > 30.0:
                    cond_neighbor_gap = True

            is_gap = cond_content or cond_sandwich or cond_neighbor_gap
            
            # DEBUG: 检查每层的间隙判定
            if d == 0:  # 这是外层循环的 d，暂时无效
                pass

            # 反向安全检查：如果与相邻边框层颜色非常接近 (< 15)，
            # 则为实心边框层而非间隙
            if is_gap:
                for ni in (i - 1, i + 1):
                    if 0 <= ni < len(border_layers) and ni != i:
                        nc = np.array(border_layers[ni][0], dtype=np.float64)
                        d_adj = float(np.sqrt(np.sum((col_arr - nc) ** 2)))
                        if d_adj < 15.0:
                            is_gap = False
                            break

        is_gap_layer.append(is_gap)

    gap_regions: list[tuple[int, int]] = []
    for i, is_gap in enumerate(is_gap_layer):
        if is_gap:
            gap_regions.append((cumulative_depths[i], cumulative_depths[i + 1]))

    solid_border_colors_arr = np.array(
        [np.array(c, dtype=np.float64) for (c, _), ig in zip(border_layers, is_gap_layer) if not ig]
    )

    ang_min, ang_max = CORNER_ANGLES[corner_key]

    yy, xx = np.mgrid[0:roi_h, 0:roi_w].astype(np.float64)

    dx = xx - float(cx_roi)
    dy = yy - float(cy_roi)
    dist = np.sqrt(dx * dx + dy * dy)
    angle = np.degrees(np.arctan2(dy, dx))
    angle = np.mod(angle, 360.0)

    depth = float(R_total) - dist

    if ang_max == 360:
        valid_angle = (angle >= ang_min) | (angle < 1)
    else:
        valid_angle = (angle >= ang_min) & (angle <= ang_max)
    # [Fix 边框线粗细] valid_region 包含所有 inside_arc 像素 (dist <= R_total)
    # 这样可以确保边框线的完整厚度被保留
    # dist == R_total 的边界像素在 d_region 中被 & (dist < R_total) 排除
    valid_region = valid_angle & (dist <= R_total)

    if validity_arr is not None:
        valid_region = valid_region & validity_arr

    # === [Fix 图2/图3] 处理弧线外侧的间隙层 ===
    # 只在 dist > R_total 的区域（裁切区域）填充间隙层为白色
    # 内部区域（dist <= R_total）的间隙层保持原色，避免误伤内部内容
    if gap_regions and src_arr is not None:
        outside_arc_gap = valid_angle & (dist > R_total) & (dist <= R_total + 2.0)
        if np.any(outside_arc_gap):
            outside_coords = np.where(outside_arc_gap)
            outside_src = src_arr[outside_coords[0], outside_coords[1], :].astype(np.float64)
            
            # Check if these pixels match any gap layer color
            for gap_idx, (is_gap, (gap_color, _)) in enumerate(zip(is_gap_layer, border_layers)):
                if not is_gap:
                    continue
                gap_color_arr = np.array(gap_color, dtype=np.float64)
                dist_to_gap = np.sqrt(np.sum((outside_src - gap_color_arr.reshape(1, 3)) ** 2, axis=1))
                is_gap_pixel = dist_to_gap < 15.0
                
                if np.any(is_gap_pixel):
                    bg_arr = np.array(bg_color, dtype=np.uint8).reshape(1, 1, 3)
                    idx = np.where(is_gap_pixel)[0]
                    result_arr[outside_coords[0][idx], outside_coords[1][idx], :] = bg_arr
                    # Update outside_src to skip already-filled pixels
                    remaining = ~is_gap_pixel
                    if not np.any(remaining):
                        break
                    outside_coords = (outside_coords[0][remaining], outside_coords[1][remaining])
                    outside_src = outside_src[remaining]

    for d in range(total_border_depth):
        # [Fix 青芜漫野] Exclude pixels at dist == R_total from d_region
        # These pixels belong to beyond_arc region and should be filled with white
        d_region = valid_region & (depth >= d) & (depth < d + 1) & (dist < R_total)
        if not np.any(d_region):
            continue

        color_idx = depth_mapping.get(d, 0)
        is_gap = is_gap_layer[color_idx] if color_idx < len(is_gap_layer) else False

        if only_outermost and color_idx != 0:
            continue

        target_color = border_layers[color_idx][0] if color_idx < len(border_layers) else (255, 255, 255)
        target_color_arr = np.array(target_color, dtype=np.float64)

        local_coords = np.where(d_region)
        if len(local_coords[0]) == 0:
            continue

        pixel_depths = float(R_total) - dist[local_coords]
        in_gap_region = np.zeros(len(local_coords[0]), dtype=bool)
        for gap_start, gap_end in gap_regions:
            in_gap_region |= (pixel_depths >= gap_start) & (pixel_depths < gap_end)
        


        # === 间隙层与实心层分离处理 ===
        if is_gap:
            # [Fix 安妮森林/克罗印花] 间隙层处理：
            # 1. outside_arc 区域（裁切区域）：填充为白色
            # 2. inside_arc 区域（保留区域）：
            #    - 如果间隙层颜色接近背景色，填充为白色（消除米色缺口）
            #    - 如果间隙层颜色接近内容色，保持原色
            gap_color_arr = np.array(target_color, dtype=np.float64)
            dist_to_bg = float(np.sqrt(np.sum((gap_color_arr - np.array(bg_color, dtype=np.float64)) ** 2)))
            dist_to_content = float(np.sqrt(np.sum((gap_color_arr - content_ref_arr) ** 2)))
            
            # 如果间隙层颜色既接近背景色，又在 inside_arc 区域，填充为白色
            if dist_to_bg < 30.0 and dist_to_bg < dist_to_content:
                # 这是背景色间隙，填充为白色
                # 但需要保留内容色间隙
                if np.any(in_gap_region):
                    keep_mask = ~in_gap_region
                    if not np.any(keep_mask):
                        continue
                    local_coords = (local_coords[0][keep_mask], local_coords[1][keep_mask])
                # Fill with white bg_color to eliminate gap notches
                bg_arr = np.array(bg_color, dtype=np.uint8).reshape(1, 1, 3)
                result_arr[local_coords[0], local_coords[1], :] = bg_arr
            else:
                # 内容色间隙，保持原色
                continue
        else:
            # Solid border layer:
            # 1. Skip pixels in classified gap regions
            # 2. Fill all remaining pixels with target color
            # [Fix 边框线粗细] Removed is_solid_pixel check - in non-protection mode,
            # inside_arc border pixels should be fully repainted based on depth/region,
            # not filtered by source color matching which can make borders appear thinner
            if np.any(in_gap_region):
                # [Fix 花幔圆角缺口] 不要直接丢弃所有 in_gap_region 像素
                # 对于直线延伸区域 (in_extension) 的像素，且颜色接近目标边框色，
                # 即使落在 gap_region 内也应绘制，以防止因间隙检测误判造成的缺口
                keep_mask = ~in_gap_region
                
                # 将应当保留的像素重新加入：
                # 1. 处于直线延伸区域的像素
                # 2. 颜色接近目标边框色的像素
                if len(local_coords[0]) > 0:
                    # 从 in_extension_mask (2D) 中提取 local_coords 对应的一维 mask
                    in_ext_for_local = in_extension_mask[local_coords]
                    
                    # [关键改进] 只有颜色匹配目标边框色的 in_extension 像素才被保留
                    # 这可以防止间隙像素被错误绘制为边框颜色
                    if src_arr is not None:
                        src_pixels = src_arr[local_coords[0], local_coords[1], :].astype(np.float64)
                        dist_to_target = np.sqrt(
                            np.sum((src_pixels - target_color_arr.reshape(1, 3)) ** 2, axis=1)
                        )
                        # 只有颜色距离小于阈值的像素才被认为是有效的边框像素
                        color_match = dist_to_target <= COLOR_DIST_THRESHOLD
                        
                        keep_mask = keep_mask | (in_ext_for_local & color_match)
                    else:
                        # 如果没有源图像，保守起见不添加任何像素
                        pass

                if not np.any(keep_mask):
                    continue
                local_coords = (local_coords[0][keep_mask], local_coords[1][keep_mask])
            else:
                # 无间隙区域，全部像素参与绘制
                pass

            # [Fix 图一/图二/图三] Second pass: handle outside_arc pixels.
            # These pixels (dist >= R_total) should be filled with bg_color (white)
            # to ensure clean crop edge. inside_arc pixels (dist < R_total) are
            # handled by the d_region loop above.
            if border_layers:
                r_val = float(corner_radius_px)
                # beyond_arc covers all outside_arc pixels (dist >= R_total)
                # This includes dist == R_total boundary pixels excluded by d_region
                beyond_arc = valid_angle & (dist >= r_val) & (dist <= r_val + 5.0)

                # Only execute once (on first iteration) to avoid redundant work
                if d == 0 and np.any(beyond_arc):
                    beyond_coords = np.where(beyond_arc)
                    # Fill all pixels in the beyond_arc region with bg_color
                    # This ensures the crop edge is clean white
                    bg_arr = np.array(bg_color, dtype=np.uint8).reshape(1, 1, 3)
                    result_arr[beyond_coords[0], beyond_coords[1], :] = bg_arr

            # [Fix 图2/图3] Third pass removed:
            # inner_overflow region (depth >= total_border_depth) contains
            # content pixels that should keep their original color.
            # Only gap pixels in the arc exterior (dist > R_total) should be
            # filled with bg_color, which is handled by the second pass above.

        # ROI 相对坐标
        roi_y = local_coords[0].copy()
        roi_x = local_coords[1].copy()

        match_filter = None
        force_paint = None

        if src_arr is not None:
            src_pixels = src_arr[roi_y, roi_x, :].astype(np.float64)

            dist_to_target = np.sqrt(
                np.sum((src_pixels - target_color_arr.reshape(1, 3)) ** 2, axis=1)
            )

            dists_nearby = [dist_to_target]
            if color_idx + 1 < len(border_colors_arr):
                next_color_arr = border_colors_arr[color_idx + 1]
                dist_to_next = np.sqrt(
                    np.sum((src_pixels - next_color_arr.reshape(1, 3)) ** 2, axis=1)
                )
                dists_nearby.append(dist_to_next)
            if color_idx - 1 >= 0:
                prev_color_arr = border_colors_arr[color_idx - 1]
                dist_to_prev = np.sqrt(
                    np.sum((src_pixels - prev_color_arr.reshape(1, 3)) ** 2, axis=1)
                )
                dists_nearby.append(dist_to_prev)

            min_dist_adjacent = np.min(np.stack(dists_nearby, axis=0), axis=0)
            threshold = TRANSITION_THRESHOLD if len(dists_nearby) > 1 else COLOR_DIST_THRESHOLD
            match_filter = min_dist_adjacent <= threshold

            if not is_gap and len(roi_y) > 0:
                pixel_angles = angle[local_coords]
                ang_a = ang_min
                ang_b = ang_max
                if ang_max == 360:
                    d_to_max = np.minimum(np.abs(pixel_angles - ang_a), pixel_angles)
                else:
                    d_to_max = np.abs(pixel_angles - ang_b)
                d_to_min = np.abs(pixel_angles - ang_a)
                near_boundary = (d_to_min <= 1.5) | (d_to_max <= 1.5)
                match_filter = match_filter | near_boundary

            # === 结构感知的混合策略 ===
            cum_before_i = cumulative_depths[color_idx]
            cum_after_i = cumulative_depths[color_idx + 1]

            # [PERF] 使用 ROI 相对坐标计算直线边区域
            # 首先计算二维的 in_extension_mask (H, W)
            if corner_key == 'tl':
                x_left, x_right = cum_before_i, cum_after_i
                y_top, y_bottom = cum_before_i, cum_after_i
                in_left_strip_mask = (xx >= x_left) & (xx < x_right)
                in_top_strip_mask = (yy >= y_top) & (yy < y_bottom)
                in_extension_mask = in_left_strip_mask | in_top_strip_mask
            elif corner_key == 'tr':
                x_left, x_right = roi_w - cum_after_i, roi_w - cum_before_i
                y_top, y_bottom = cum_before_i, cum_after_i
                in_right_strip_mask = (xx >= x_left) & (xx < x_right)
                in_top_strip_mask = (yy >= y_top) & (yy < y_bottom)
                in_extension_mask = in_right_strip_mask | in_top_strip_mask
            elif corner_key == 'bl':
                x_left, x_right = cum_before_i, cum_after_i
                y_top, y_bottom = roi_h - cum_after_i, roi_h - cum_before_i
                in_left_strip_mask = (xx >= x_left) & (xx < x_right)
                in_bottom_strip_mask = (yy >= y_top) & (yy < y_bottom)
                in_extension_mask = in_left_strip_mask | in_bottom_strip_mask
            else:
                x_left, x_right = roi_w - cum_after_i, roi_w - cum_before_i
                y_top, y_bottom = roi_h - cum_after_i, roi_h - cum_before_i
                in_right_strip_mask = (xx >= x_left) & (xx < x_right)
                in_bottom_strip_mask = (yy >= y_top) & (yy < y_bottom)
                in_extension_mask = in_right_strip_mask | in_bottom_strip_mask

            # 从二维 mask 中提取当前 local_coords 对应的一维结果
            # 这将用于 match_filter 和后续的 force_paint 计算
            if len(local_coords[0]) > 0:
                in_extension = in_extension_mask[local_coords]
            else:
                in_extension = np.array([], dtype=bool)

            # [Fix 花幔圆角缺口] 放宽直线延伸区域的匹配阈值
            # 直线延伸区域的像素应直接对应直边边框
            # 若因颜色混合导致不通过默认阈值，则使用更宽容的阈值
            RELAXED_THRESHOLD = threshold + 15.0  # 增加15的宽容度
            relaxed_match = min_dist_adjacent <= RELAXED_THRESHOLD
            
            # 从 in_extension_mask (2D) 中提取 local_coords 对应的一维 mask
            if len(local_coords[0]) > 0:
                in_ext_for_local = in_extension_mask[local_coords]
                match_filter = match_filter | (in_ext_for_local & relaxed_match)

            diagonal_interior = ~in_extension

            layer_to_content_dist = float(np.sqrt(
                np.sum((target_color_arr - content_ref_arr) ** 2)
            ))
            is_colored_border = layer_to_content_dist > COLOR_DIST_THRESHOLD

            base_force = diagonal_interior if is_colored_border \
                else np.zeros_like(diagonal_interior, dtype=bool)

            # =========== 结构感知修正 ===========
            structure_allow = np.ones_like(base_force, dtype=bool)
            n_force = int(np.sum(base_force))
            if n_force > 0 and src_arr is not None:
                force_idx = np.where(base_force)
                y_f = roi_y[force_idx]
                x_f = roi_x[force_idx]

                # [PERF] 使用 ROI 坐标的圆心
                cxv = cx_roi
                cyv = cy_roi
                dxv = x_f.astype(np.float64) - float(cxv)
                dyv = y_f.astype(np.float64) - float(cyv)
                dist_v = np.sqrt(dxv * dxv + dyv * dyv)
                depth_v = float(R_total) - dist_v
                d_int_v = np.clip(np.round(depth_v).astype(np.int32), 0, total_border_depth)
                N = len(d_int_v)

                DEPTH_BAND = 2
                STRUCT_THRESH = max(COLOR_DIST_THRESHOLD + 5.0, 20.0)
                allow_v = np.zeros(N, dtype=bool)

                for d_off in range(-DEPTH_BAND, DEPTH_BAND + 1):
                    d_band = np.clip(d_int_v + d_off, 0, total_border_depth)
                    for edge_k in range(2):
                        if corner_key == 'tl':
                            if edge_k == 0:
                                sy = np.clip(d_band, 0, roi_h - 1)
                                sx = np.full(N, max(1, roi_w // 3), dtype=np.int64)
                            else:
                                sx = np.clip(d_band, 0, roi_w - 1)
                                sy = np.full(N, max(1, roi_h // 3), dtype=np.int64)
                        elif corner_key == 'tr':
                            if edge_k == 0:
                                sy = np.clip(d_band, 0, roi_h - 1)
                                sx = np.full(N, max(1, roi_w * 2 // 3), dtype=np.int64)
                            else:
                                sx = np.clip(roi_w - 1 - d_band, 0, roi_w - 1)
                                sy = np.full(N, max(1, roi_h // 3), dtype=np.int64)
                        elif corner_key == 'bl':
                            if edge_k == 0:
                                sx = np.clip(d_band, 0, roi_w - 1)
                                sy = np.full(N, max(1, roi_h * 2 // 3), dtype=np.int64)
                            else:
                                sy = np.clip(roi_h - 1 - d_band, 0, roi_h - 1)
                                sx = np.full(N, max(1, roi_w // 3), dtype=np.int64)
                        else:
                            if edge_k == 0:
                                sy = np.clip(roi_h - 1 - d_band, 0, roi_h - 1)
                                sx = np.full(N, max(1, roi_w * 2 // 3), dtype=np.int64)
                            else:
                                sx = np.clip(roi_w - 1 - d_band, 0, roi_w - 1)
                                sy = np.full(N, max(1, roi_h * 2 // 3), dtype=np.int64)

                        edge_colors = src_arr[sy, sx, :].astype(np.float64)
                        d2layer = np.sqrt(np.sum(
                            (edge_colors - target_color_arr.reshape(1, 3)) ** 2, axis=1
                        ))
                        min_adj = d2layer.copy()
                        if color_idx + 1 < len(border_colors_arr):
                            nc = border_colors_arr[color_idx + 1].reshape(1, 3)
                            d2n = np.sqrt(np.sum((edge_colors - nc) ** 2, axis=1))
                            min_adj = np.minimum(min_adj, d2n)
                        if color_idx - 1 >= 0:
                            pc = border_colors_arr[color_idx - 1].reshape(1, 3)
                            d2p = np.sqrt(np.sum((edge_colors - pc) ** 2, axis=1))
                            min_adj = np.minimum(min_adj, d2p)
                        d2cont = np.sqrt(np.sum(
                            (edge_colors - content_ref_arr.reshape(1, 3)) ** 2, axis=1
                        ))
                        is_solid = (min_adj <= STRUCT_THRESH) | (d2cont > STRUCT_THRESH + 5)
                        allow_v |= is_solid

                WIDE_BORDER_THRESH = 35.0
                if not np.all(allow_v):
                    need_fb = ~allow_v
                    fb_y = y_f[need_fb]
                    fb_x = x_f[need_fb]
                    if len(fb_y) > 0:
                        fb_src = src_arr[fb_y, fb_x, :].astype(np.float64)
                        min_d_border = np.full(len(fb_y), 1e9, dtype=np.float64)
                        for bc_arr in border_colors_arr:
                            d_ = np.sqrt(np.sum(
                                (fb_src - bc_arr.reshape(1, 3)) ** 2, axis=1
                            ))
                            min_d_border = np.minimum(min_d_border, d_)
                        d_cont_fb = np.sqrt(np.sum(
                            (fb_src - content_ref_arr.reshape(1, 3)) ** 2, axis=1
                        ))
                        fb_ok = (min_d_border <= WIDE_BORDER_THRESH) & \
                                (d_cont_fb > COLOR_DIST_THRESHOLD)
                        allow_v[need_fb] |= fb_ok

                structure_allow_idx = structure_allow[force_idx]
                structure_allow_idx &= allow_v
                structure_allow[force_idx] = structure_allow_idx

            force_paint = base_force & structure_allow

            if is_colored_border and not is_gap and len(roi_y) > 0:
                pixel_depths = float(R_total) - dist[local_coords]
                d_start_layer = cumulative_depths[color_idx]
                d_end_layer = cumulative_depths[color_idx + 1]
                in_this_layer_depth = (pixel_depths >= d_start_layer) & (pixel_depths < d_end_layer)
                force_paint = force_paint | (diagonal_interior & in_this_layer_depth)

            # 对角内区强制覆盖兜底
            diagonal_interior_idx = np.where(diagonal_interior)[0]
            if len(diagonal_interior_idx) > 0:
                if is_gap:
                    gap_pixel_depths_all = float(R_total) - dist[local_coords]
                    di_depths = gap_pixel_depths_all[diagonal_interior_idx]
                    d_start_layer = cumulative_depths[color_idx]
                    d_end_layer = cumulative_depths[color_idx + 1]
                    in_this_layer = (di_depths >= d_start_layer) & \
                                    (di_depths < d_end_layer)
                    fill_idx = diagonal_interior_idx[in_this_layer]

                    if len(fill_idx) > 0:
                        gap_fill_y = roi_y[fill_idx]
                        gap_fill_x = roi_x[fill_idx]
                        result_arr[gap_fill_y, gap_fill_x, :] = np.array(
                            target_color, dtype=result_arr.dtype
                        ).reshape(1, 3)
                        di_mask_set = set(fill_idx.tolist())
                        keep_bool = np.array(
                            [i not in di_mask_set for i in range(len(roi_y))],
                            dtype=bool
                        )
                        roi_y = roi_y[keep_bool]
                        roi_x = roi_x[keep_bool]
                        if match_filter is not None and len(match_filter) == len(keep_bool):
                            match_filter = match_filter[keep_bool]
                        if force_paint is not None and len(force_paint) == len(keep_bool):
                            force_paint = force_paint[keep_bool]
                        if len(roi_y) == 0:
                            continue
                elif is_colored_border:
                    d_straight = int(round(np.mean(depth[local_coords][diagonal_interior_idx])))
                    d_straight = np.clip(d_straight, 0, total_border_depth - 1)
                    cand_colors = []
                    if corner_key in ('tl', 'tr'):
                        ty = min(max(d_straight, 0), roi_h - 1)
                        tx = min(max(roi_w // 2, 0), roi_w - 1)
                        cand_colors.append(src_arr[ty, tx, :].astype(np.float64))
                    if corner_key in ('bl', 'br'):
                        by = min(max(roi_h - 1 - d_straight, 0), roi_h - 1)
                        bx = min(max(roi_w // 2, 0), roi_w - 1)
                        cand_colors.append(src_arr[by, bx, :].astype(np.float64))
                    if corner_key in ('tl', 'bl'):
                        ly = min(max(roi_h // 2, 0), roi_h - 1)
                        lx = min(max(d_straight, 0), roi_w - 1)
                        cand_colors.append(src_arr[ly, lx, :].astype(np.float64))
                    if corner_key in ('tr', 'br'):
                        ry = min(max(roi_h // 2, 0), roi_h - 1)
                        rx_v = min(max(roi_w - 1 - d_straight, 0), roi_w - 1)
                        cand_colors.append(src_arr[ry, rx_v, :].astype(np.float64))
                    if cand_colors:
                        straight_color = np.median(
                            np.stack(cand_colors, axis=0), axis=0
                        )
                        n_all = len(roi_y)
                        diag_set = set(diagonal_interior_idx.tolist())
                        double_neg = np.zeros(n_all, dtype=bool)
                        if match_filter is not None and force_paint is not None:
                            if len(match_filter) == n_all and len(force_paint) == n_all:
                                for i in range(n_all):
                                    if i in diag_set and not force_paint[i] and not match_filter[i]:
                                        double_neg[i] = True
                                if np.any(double_neg):
                                    dn_y = roi_y[double_neg]
                                    dn_x = roi_x[double_neg]
                                    sc = np.array(
                                        [int(round(v)) for v in straight_color],
                                        dtype=result_arr.dtype
                                    )
                                    result_arr[dn_y, dn_x, :] = sc.reshape(1, 3)

            if match_filter is not None and force_paint is not None:
                if len(match_filter) > len(roi_y):
                    match_filter = match_filter[:len(roi_y)]

            # 最外层边框始终绘制
            if color_idx == 0 and not is_gap:
                final_mask = np.ones(len(roi_y), dtype=bool)
            elif match_filter is not None and force_paint is not None:
                if len(force_paint) >= len(match_filter):
                    final_mask = match_filter | force_paint[:len(match_filter)]
                else:
                    final_mask = match_filter
            else:
                final_mask = np.ones(len(roi_y), dtype=bool)

            if not np.any(final_mask):
                continue

            apply_y = roi_y[final_mask]
            apply_x = roi_x[final_mask]
            color_fill = np.array(target_color, dtype=result_arr.dtype)
            result_arr[apply_y, apply_x, :] = color_fill.reshape(1, 3)

    # [Fix 青芜漫野] Final pass: ensure outside_arc region is completely white
    # Outside_arc pixels (dist >= R_total) may have been overwritten by subsequent operations
    final_beyond = valid_angle & (dist >= float(R_total)) & (dist <= float(R_total) + 5.0)
    if np.any(final_beyond):
        final_coords = np.where(final_beyond)
        bg_arr = np.array(bg_color, dtype=np.uint8).reshape(1, 1, 3)
        result_arr[final_coords[0], final_coords[1], :] = bg_arr

    # 回写结果
    new_img = Image.fromarray(result_arr.astype(np.uint8), mode='RGB')
    result_img.paste(new_img, (x1, y1))
