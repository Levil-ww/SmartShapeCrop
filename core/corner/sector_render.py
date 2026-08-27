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
from .detection import (
    classify_gap_layers,
    GAP_MAX_THICKNESS_GLOBAL,
)


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
    # [Fix v7] 使用适中的阈值，兼顾识别精度和抗噪能力
    COLOR_DIST_THRESHOLD = 18.0
    TRANSITION_THRESHOLD = 28.0

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

    # [Fix v7] 统一间隙层判定
    # 删除原先独立的 100+ 行间隙检测逻辑（与 image_cropper.py/diagnose 判定不一致），
    # 统一调用 classify_gap_layers（单一判定来源），确保：
    #   - INV-B4: 最内层永不判间隙
    #   - INV-G1: 最外层深色边框永不判间隙
    #   - INV-G3: 中间层sandwich(两侧色差大)→间隙
    bg_arr_detect = np.array(bg_color, dtype=np.float64)
    is_gap_layer = classify_gap_layers(
        border_layers, bg_color=bg_color, content_ref_arr=content_ref_arr
    )

    gap_regions: list[tuple[int, int]] = []
    for i, is_gap in enumerate(is_gap_layer):
        if is_gap:
            # [Fix v7] 使用精确的间隙区域（不过度扩展）
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

    # === [Fix INV-1/INV-3/INV-5] 处理弧线外侧区域 ===
    # 必须在 d loop 之前运行，确保所有弧外侧像素（dist > R_total）
    # 都被填充为背景色，无论首层是否为间隙层。
    # 
    # [Fix 花漾之约] 扩大 beyond_arc 范围：
    #   原缺陷：dist <= R_total + 5.0 限制了仅填充弧外侧 5px 范围，
    #   当 arc 半径较大时，远端像素未被填充 → 白色扇形角。
    #   修复：填充所有 dist > R_total 的像素（ROI 范围内），确保弧外侧干净。
    bg_arr_uint8 = np.array(bg_color, dtype=np.uint8).reshape(1, 1, 3)
    beyond_arc = valid_angle & (dist > R_total)
    if np.any(beyond_arc):
        beyond_coords = np.where(beyond_arc)
        result_arr[beyond_coords[0], beyond_coords[1], :] = bg_arr_uint8

    for d in range(total_border_depth):
        # [Fix INV-2/INV-5] Include pixels at dist == R_total in d_region
        # These pixels are at the arc boundary and must be painted with border color
        # to prevent 1px white gap (white sector / thin border artifacts)
        d_region = valid_region & (depth >= d) & (depth < d + 1) & (dist <= R_total)
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
            # [Fix v6] 间隙层处理：
            #   核心不变量：间隙层像素 → 全部转为背景色 (INV-1)
            #   
            #   问题诊断（素锦案例）：
            #   旧逻辑 should_clear = gap_colored & ~border & ~content
            #   当间隙色 ≈ 内容色时（如素锦米色间隙 ≈ 米色内容），
            #   is_content_colored 为 True，阻止了间隙像素被清除 → 残留米色弧形
            #   
            #   修复策略：
            #   1. 间隙色像素（匹配间隙层颜色）必须清除，不受内容色匹配阻止
            #   2. 保留非间隙色像素（可能是花纹延伸或抗锯齿）
            #   3. 边框色像素永远保留（实心边框层负责绘制）
            #   4. 对所有间隙层（不仅首尾），根据颜色距离判断是否强制清除
            
            bg_arr = np.array(bg_color, dtype=np.uint8).reshape(1, 1, 3)
            target_c_arr = np.array(target_color, dtype=np.float64)
            
            # [Fix v7] 检查间隙层的强制清除条件：
            # 任何间隙层（不仅首尾），如果颜色既不接近背景也不接近实心边框，
            # 则强制清除所有像素（因为这是真正的间隙，不是背景也不是边框）。
            # 注意：必须使用 solid_border_colors_arr（排除间隙层自身颜色），
            # 否则间隙层颜色与自身距离为 0，会永远误判为"接近边框"，
            # 导致 force_clear_gap 永远为 False。
            dist_to_bg = float(np.sqrt(np.sum((target_c_arr - bg_arr.reshape(1, 3)) ** 2)))
            is_near_border_color = False
            for bc_arr in solid_border_colors_arr:
                if np.sqrt(np.sum((target_c_arr - bc_arr) ** 2)) < 20.0:  # [Fix v7] 放宽到20.0
                    is_near_border_color = True
                    break
            
            # 获取当前间隙层厚度（修复：原 t 是删除的循环变量）
            gap_layer_thickness = border_layers[color_idx][1] if color_idx < len(border_layers) else 0
            
            # 强制清除条件：
            # - 间隙层颜色不接近背景（不是背景色）
            # - 间隙层颜色不接近任何边框色（不是边框色）
            # - 间隙层厚度在合理范围
            # [Fix v7] 使用适中阈值，平衡强制清除触发率
            force_clear_gap = (dist_to_bg > 18.0) and (not is_near_border_color) and \
                            (gap_layer_thickness <= GAP_MAX_THICKNESS_GLOBAL)
            
            if force_clear_gap:
                # [Fix 安妮森林/中古花园 v2 + 塞纳时光米黄弧线 0827]
                # 安全强制清除：
                #   触发条件（颜色不接近背景、不接近实心边框、厚度合理）已经确保
                #   当前层是真正的均匀间隙。对于这类间隙，圆弧上的对应深度带
                #   内除边框/背景外的像素都应被清除为背景色。
                #
                #   旧逻辑只清除"匹配检测到的间隙色"的像素，当真实间隙颜色因
                #   压缩/缩放/渐变与检测颜色有偏差时（如塞纳时光米色变浅），
                #   会残留形成米黄色弧线。
                #
                #   新逻辑：保留实心边框色像素（INV-S1）和已经是背景的像素，
                #   其余像素（即真正的间隙像素）全部清除。
                #   注意：此分支仅在触发严格条件时进入，装饰性间隙（含复杂
                #   图案/文字）通常不满足触发条件，不会被误伤。
                if src_arr is not None and len(local_coords[0]) > 0:
                    src_pixels = src_arr[local_coords[0], local_coords[1], :].astype(np.float64)

                    # 检测实心边框色像素：必须保留（INV-S1）
                    is_border_colored = np.zeros(len(local_coords[0]), dtype=bool)
                    for bc_arr in solid_border_colors_arr:
                        dist_to_bc = np.sqrt(np.sum((src_pixels - bc_arr.reshape(1, 3)) ** 2, axis=1))
                        is_border_colored |= (dist_to_bc <= COLOR_DIST_THRESHOLD + 8.0)

                    # 检测背景色像素：已经是背景，无需重复清除
                    dist_to_bg_check = np.sqrt(np.sum((src_pixels - bg_arr.reshape(1, 3)) ** 2, axis=1))
                    is_bg_colored = dist_to_bg_check <= 5.0

                    # 清除非边框、非背景的像素（真正的间隙像素）
                    should_clear = (~is_border_colored) & (~is_bg_colored)

                    if np.any(should_clear):
                        clear_coords = np.where(should_clear)
                        result_arr[local_coords[0][clear_coords], local_coords[1][clear_coords], :] = bg_arr
                        # 保留未清除的像素供后续处理
                        keep_mask = ~should_clear
                        if not np.any(keep_mask):
                            continue
                        local_coords = (local_coords[0][keep_mask], local_coords[1][keep_mask])
                    else:
                        continue
                else:
                    # 无源图像：保守清除，仅清除明显的间隙色像素
                    result_arr[local_coords[0], local_coords[1], :] = bg_arr
                    continue
            
            # 常规间隙层：基于颜色匹配清除（精准策略）
            if src_arr is not None and len(local_coords[0]) > 0:
                src_pixels = src_arr[local_coords[0], local_coords[1], :].astype(np.float64)
                
                # 间隙色匹配：与当前间隙层颜色接近
                dist_to_gap_color = np.sqrt(np.sum((src_pixels - target_c_arr.reshape(1, 3)) ** 2, axis=1))
                is_gap_colored = dist_to_gap_color <= COLOR_DIST_THRESHOLD + 10.0
                
                # 边框色匹配：与任何实心边框色接近
                is_border_colored = np.zeros(len(local_coords[0]), dtype=bool)
                for bc_arr in border_colors_arr:
                    dist_to_bc = np.sqrt(np.sum((src_pixels - bc_arr.reshape(1, 3)) ** 2, axis=1))
                    is_border_colored |= (dist_to_bc <= COLOR_DIST_THRESHOLD + 8.0)
                
                # [Fix v7] 精准清除策略：
                # 1. 间隙色像素 → 清除（这是关键：确保间隙区域干净）
                # 2. 已经是背景色的像素 → 清除
                # 3. 边框色像素 → 保留（由实心边框层负责绘制）
                # 4. 非间隙色、非边框色像素 → 保留（花纹/装饰/内容）
                should_clear = is_gap_colored & (~is_border_colored)
                
                dist_to_bg_check = np.sqrt(np.sum((src_pixels - bg_arr.reshape(1, 3)) ** 2, axis=1))
                is_already_bg = dist_to_bg_check <= 5.0
                should_clear = should_clear | is_already_bg
                
                if np.any(should_clear):
                    clear_coords = np.where(should_clear)
                    result_arr[local_coords[0][clear_coords], local_coords[1][clear_coords], :] = bg_arr
                    # 保留未清除的像素供后续处理
                    keep_mask = ~should_clear
                    if not np.any(keep_mask):
                        continue
                    local_coords = (local_coords[0][keep_mask], local_coords[1][keep_mask])
                else:
                    continue
            else:
                # 无源图像：保守清除，只清除明显的间隙色像素
                result_arr[local_coords[0], local_coords[1], :] = bg_arr
                continue
        else:
            # Solid border layer:
            # [Fix INV-S1/INV-S3] 修复：
            #   外层边框层 (color_idx==0) 永不因 in_gap_region 过滤。
            #   原因：间隙检测可能误判边框深度范围 → 边框像素被过滤 → 边框消失/缺口。
            #   只有颜色匹配的像素才被过滤（真正的间隙色像素），深度范围不再是硬性约束。
            is_outermost_solid = (color_idx == 0)
            
            if np.any(in_gap_region):
                if is_outermost_solid:
                    # [Fix 安妮森林/中古花园] 外层边框：跳过所有间隙过滤
                    # 外层边框是视觉上最重要的边框线，必须完整绘制
                    pass  # 不做任何过滤
                else:
                    # 内层边框：仅过滤真正匹配间隙色的像素
                    # 不再使用 in_gap_region 深度范围做硬性过滤
                    if src_arr is not None:
                        src_pixels = src_arr[local_coords[0], local_coords[1], :].astype(np.float64)
                        
                        # 检测真正的间隙色像素：匹配任一间隙层颜色
                        is_real_gap_pixel = np.zeros(len(local_coords[0]), dtype=bool)
                        for gc_arr in border_colors_arr:
                            # 跳过与当前目标边框色相同的颜色
                            if np.sqrt(np.sum((target_color_arr - gc_arr) ** 2)) < 1.0:
                                continue
                            d_to_gc = np.sqrt(np.sum((src_pixels - gc_arr.reshape(1, 3)) ** 2, axis=1))
                            is_real_gap_pixel |= (d_to_gc <= COLOR_DIST_THRESHOLD)
                        
                        # 也检测是否为背景色（已被清除的像素）
                        bg_arr_check = np.array(bg_color, dtype=np.float64).reshape(1, 3)
                        d_to_bg = np.sqrt(np.sum((src_pixels - bg_arr_check) ** 2, axis=1))
                        is_bg_pixel = d_to_bg <= 5.0
                        
                        keep_mask = ~is_real_gap_pixel & ~is_bg_pixel
                        
                        if not np.any(keep_mask):
                            continue
                        local_coords = (local_coords[0][keep_mask], local_coords[1][keep_mask])
                    # 若无源图像，保留所有像素（保守处理）
            else:
                # 无间隙区域，全部像素参与绘制
                pass

            # beyond_arc 已在 d loop 之前统一处理（确保所有层类型的弧外侧像素都被清除）
            # 此处不再重复填充，避免覆盖已正确绘制的边框像素

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

    # [Fix INV-3/墨上花开] Pass 2: 间隙颜色残留清扫
    # 关键修复：只扫描间隙层径向范围附近的区域，防止内容色被误清除。
    # 原缺陷：扫描 valid_angle 全域 → 当内容色接近间隙色时（如墨上花开米色内容 ≈ 米色间隙），
    # 内容像素被错误清除为白色 → 弧内图案消失。
    # 修复后：仅在每个间隙层的径向距离范围 ±5px 内扫描，精准定位间隙残留。
    bg_uint8 = np.array(bg_color, dtype=np.uint8).reshape(1, 1, 3)
    
    # Pass 1: 确保 outside_arc 区域完全为背景色
    # [Fix INV-2/INV-5/花漾之约] 使用 dist > R_total (严格)，
    # 扩大范围覆盖所有弧外侧像素（ROI 内），防止白色扇形角
    final_beyond = valid_angle & (dist > float(R_total))
    if np.any(final_beyond):
        final_coords = np.where(final_beyond)
        result_arr[final_coords[0], final_coords[1], :] = bg_uint8
    
    # Pass 2: 间隙颜色残留清扫（仅限间隙层径向范围附近）
    # 对每个间隙层，精确扫描其径向距离附近的像素
    # [Fix 2026-08-27] 增加边框像素保护：在清除间隙残留前检查像素是否为边框色，
    # 防止黑色边框弧线被误清除（安妮森林案例 Bug 3）
    gap_colors_list = [np.array(c, dtype=np.float64) for (c, _), ig in zip(border_layers, is_gap_layer) if ig]
    # 实心边框颜色列表（用于保护边框像素不被间隙清除误杀）
    solid_border_colors_arr = np.array(
        [np.array(c, dtype=np.float64) for (c, _), ig in zip(border_layers, is_gap_layer) if not ig]
    )
    if gap_colors_list and gap_regions:
        # 合并所有间隙层的径向范围
        for gap_start, gap_end in gap_regions:
            # 间隙层在圆弧上的径向距离：
            # gap_dist_near: 靠近弧外侧（dist 更大）
            # gap_dist_far: 靠近弧内侧（dist 更小）
            gap_dist_near = float(R_total) - float(gap_start)
            gap_dist_far = float(R_total) - float(gap_end)
            # 确保方向正确
            if gap_dist_near > gap_dist_far:
                # 扫描范围：间隙层径向范围 ± 5px 容差
                scan_region_gap = valid_angle & \
                    (dist >= gap_dist_far - 5.0) & (dist <= gap_dist_near + 5.0)
                if np.any(scan_region_gap):
                    scan_coords = np.where(scan_region_gap)
                    scan_colors = result_arr[scan_coords[0], scan_coords[1], :].astype(np.float64)
                    
                    # [Fix] 检查哪些像素是边框色（需要保护）
                    is_border_pixel = np.zeros(len(scan_colors), dtype=bool)
                    if len(solid_border_colors_arr) > 0:
                        for bc_arr in solid_border_colors_arr:
                            d_to_bc = np.sqrt(np.sum((scan_colors - bc_arr.reshape(1, 3)) ** 2, axis=1))
                            is_border_pixel |= (d_to_bc <= COLOR_DIST_THRESHOLD + 5.0)
                    
                    # [Fix 2026-08-27] 单遍 union 匹配替代渐进式过滤：
                    # 一次性收集所有匹配任意间隙色的像素（并保护边框像素），
                    # 避免多轮 remaining 同步过滤带来的脆弱性与可维护性风险。
                    any_gap_match = np.zeros(len(scan_colors), dtype=bool)
                    for gc in gap_colors_list:
                        dist_to_gc = np.sqrt(np.sum((scan_colors - gc.reshape(1, 3)) ** 2, axis=1))
                        any_gap_match |= (dist_to_gc < 20.0)
                    # 边框像素保护：已识别为边框色的像素不得被间隙清扫误杀
                    any_gap_match = any_gap_match & (~is_border_pixel)
                    if np.any(any_gap_match):
                        idx = np.where(any_gap_match)[0]
                        result_arr[scan_coords[0][idx], scan_coords[1][idx], :] = bg_uint8
    elif gap_colors_list:
        # 没有 gap_regions 信息但有间隙层（兜底），仅扫描弧边界附近
        # 这是保守策略，仅扫描 dist in [R_total-5, R_total+3] 的窄带
        scan_region = valid_angle & (dist >= float(R_total) - 5.0) & (dist <= float(R_total) + 3.0)
        if np.any(scan_region):
            scan_coords = np.where(scan_region)
            scan_colors = result_arr[scan_coords[0], scan_coords[1], :].astype(np.float64)
            
            # [Fix] 检查哪些像素是边框色（需要保护）
            is_border_pixel = np.zeros(len(scan_colors), dtype=bool)
            if len(solid_border_colors_arr) > 0:
                for bc_arr in solid_border_colors_arr:
                    d_to_bc = np.sqrt(np.sum((scan_colors - bc_arr.reshape(1, 3)) ** 2, axis=1))
                    is_border_pixel |= (d_to_bc <= COLOR_DIST_THRESHOLD + 5.0)
            
            # [Fix 2026-08-27] 单遍 union 匹配替代渐进式过滤（同路径1一致化）
            any_gap_match = np.zeros(len(scan_colors), dtype=bool)
            for gc in gap_colors_list:
                dist_to_gc = np.sqrt(np.sum((scan_colors - gc.reshape(1, 3)) ** 2, axis=1))
                any_gap_match |= (dist_to_gc < 20.0)
            any_gap_match = any_gap_match & (~is_border_pixel)
            if np.any(any_gap_match):
                idx = np.where(any_gap_match)[0]
                result_arr[scan_coords[0][idx], scan_coords[1][idx], :] = bg_uint8

    # === [Fix INV-5/花漾之约] 边界角度白色像素清扫 ===
    # 核心修复：区分"设计白点"与"白色扇形伪影"
    #   - 设计白点：边框设计有意保留的间隙（如点状边框中的白点），通常为孤立小簇
    #   - 扇形伪影：弧外侧/弧边界形成的大面积连续白色区域
    # 策略：仅清除"在径向+角度两个方向都连续跨越多个像素"的白色簇（即扇形），
    #       保留散点式的设计白点（如花漾之约的点状边框间隙）。
    # 安全约束：深色边框像素不受影响。
    bg_uint8_final = np.array(bg_color, dtype=np.uint8).reshape(1, 1, 3)

    # 扩展角度范围：在ang_max两侧各5度（超出valid_angle以覆盖弧-直边过渡区）
    ang_min_ext = max(0, ang_min - 5)
    ang_max_ext = min(360, ang_max + 5)

    # 构建扩展角度带mask
    if ang_max == 360 or ang_max_ext > 360:
        ext_angle = (angle >= ang_min_ext) | (angle <= ang_max_ext % 360)
    else:
        ext_angle = (angle >= ang_min_ext) & (angle <= ang_max_ext)

    # 覆盖深度范围：从弧外侧( beyond arc )到弧内侧间隙区域
    cleanup_inner_depth = max(total_border_depth + 15, 50)
    cleanup_inner_dist = float(R_total) - float(cleanup_inner_depth)
    cleanup_outer_dist = float(R_total) + 5.0

    cleanup_depth_range = (dist >= cleanup_inner_dist) & (dist <= cleanup_outer_dist)

    boundary_cleanup_region = ext_angle & cleanup_depth_range

    if np.any(boundary_cleanup_region) and len(solid_border_colors_arr) > 0:
        cleanup_coords = np.where(boundary_cleanup_region)
        cleanup_colors = result_arr[cleanup_coords[0], cleanup_coords[1], :].astype(np.float64)

        # 白色像素检测：三通道都>240
        is_white = np.all(cleanup_colors > 240, axis=1)

        # 边框色检测：与任一实心边框色匹配
        is_border_like = np.zeros(len(cleanup_coords[0]), dtype=bool)
        for bc in solid_border_colors_arr:
            d_to_bc = np.sqrt(np.sum((cleanup_colors - bc.reshape(1, 3)) ** 2, axis=1))
            is_border_like |= (d_to_bc <= 25.0)

        # 仅考虑白色且非边框的候选
        white_candidate_mask = is_white & (~is_border_like)

        if np.any(white_candidate_mask):
            # === 扇形伪影判定：基于连通域的尺寸/形状分析 ===
            # 真正的白色扇形伪影特征：
            #   1. 像素数 >= 50（足够大面积）
            #   2. 角度跨度 >= 2°（跨越多个角度带）
            #   3. 径向跨度 >= 5px（有明显的径向深度）
            # 孤立白点/小点簇（设计间隙）不满足这些条件，予以保留。
            white_indices = np.where(white_candidate_mask)[0]
            candidate_y = cleanup_coords[0][white_indices]
            candidate_x = cleanup_coords[1][white_indices]
            candidate_dist = dist[cleanup_coords[0][white_indices], cleanup_coords[1][white_indices]]
            candidate_angle = angle[cleanup_coords[0][white_indices], cleanup_coords[1][white_indices]]

            total_white = len(white_indices)

            # 快速路径：白色像素总数极少（< 20），不可能形成扇形
            if total_white >= 20:
                # 角度跨度
                ang_span = float(np.max(candidate_angle) - np.min(candidate_angle))
                # 径向跨度
                dist_span = float(np.max(candidate_dist) - np.min(candidate_dist))

                is_likely_sector = (total_white >= 50) and (ang_span >= 2.0) and (dist_span >= 5.0)

                if is_likely_sector:
                    # 典型扇形伪影：全部清除
                    result_arr[candidate_y, candidate_x, :] = bg_uint8_final
                else:
                    # 非典型：仅清除"在完整径向带连续"的白色
                    # 规则：若某像素附近3px范围内在径向上有连续>=3个白色像素，则清除该像素
                    #       否则视为设计白点保留
                    # 计算每个白像素沿径向的局部连续长度
                    should_clean = np.zeros(total_white, dtype=bool)
                    unique_dists = np.unique(np.round(candidate_dist).astype(np.int64))
                    if len(unique_dists) >= 1:
                        # 为每个唯一距离值分组
                        for d_val in unique_dists:
                            mask_b = np.abs(candidate_dist - float(d_val)) < 0.5
                            idxs_b = np.where(mask_b)[0]
                            if len(idxs_b) >= 3:
                                ang_sorted = np.sort(candidate_angle[idxs_b])
                                if len(ang_sorted) >= 3 and (ang_sorted[-1] - ang_sorted[0]) >= 1.5:
                                    should_clean[idxs_b] = True
                    # 只清除满足条件的像素，其余保留
                    if np.any(should_clean):
                        clean_y = candidate_y[should_clean]
                        clean_x = candidate_x[should_clean]
                        result_arr[clean_y, clean_x, :] = bg_uint8_final
            # 若 total_white < 20，全部视为设计白点保留

    # 回写结果
    new_img = Image.fromarray(result_arr.astype(np.uint8), mode='RGB')
    result_img.paste(new_img, (x1, y1))
