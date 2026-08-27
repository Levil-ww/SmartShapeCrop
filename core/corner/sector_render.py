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

        # === V1.0 风格简化处理：间隙层清除为背景色 ===
        if is_gap:
            # 间隙层：直接清除为背景色
            bg_arr = np.array(bg_color, dtype=np.uint8).reshape(1, 1, 3)
            result_arr[local_coords[0], local_coords[1], :] = bg_arr
            continue
        else:
            # === V1.0 风格简化：实心边框直接绘制 ===
            # 直接将边框色绘制到该层的整个深度带区域
            # 不再进行复杂的间隙过滤和颜色匹配
            color_fill = np.array(target_color, dtype=result_arr.dtype)
            result_arr[local_coords[0], local_coords[1], :] = color_fill.reshape(1, 3)
            continue

    # === V1.0 风格简化：确保弧外侧区域为背景色 ===
    bg_uint8 = np.array(bg_color, dtype=np.uint8).reshape(1, 1, 3)
    
    # 确保 outside_arc 区域完全为背景色
    final_beyond = valid_angle & (dist > float(R_total))
    if np.any(final_beyond):
        final_coords = np.where(final_beyond)
        result_arr[final_coords[0], final_coords[1], :] = bg_uint8

    # 回写结果
    new_img = Image.fromarray(result_arr.astype(np.uint8), mode='RGB')
    result_img.paste(new_img, (x1, y1))
