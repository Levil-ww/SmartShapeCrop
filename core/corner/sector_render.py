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
import math
import numpy as np
from PIL import Image

from .algorithm import CORNER_ANGLES


def _angle_bottom(corner_key: str, R: np.ndarray | float, depth: np.ndarray | float) -> np.ndarray | float:
    """
    计算指定深度 depth 处，bottom/top 边的直线与圆弧交点的角度（度）。
    支持 numpy 数组，向量化。

    历史用途：早期版本使用本函数计算随深度收窄的角度，但会导致内层边框在圆弧上
    无法覆盖直线到圆弧的连接区域。当前 _build_border_sector_mask 改用固定角度范围
    （CORNER_ANGLES），本函数仅作为内部辅助保留，未被外部调用。
    """
    ratio = (np.asarray(R, dtype=np.float64) - np.asarray(depth, dtype=np.float64)) / np.asarray(R, dtype=np.float64)
    ratio = np.clip(ratio, -1.0, 1.0)
    arcsin = np.degrees(np.arcsin(ratio))
    if corner_key == 'bl':
        return 180.0 - arcsin
    elif corner_key == 'br':
        return arcsin
    elif corner_key == 'tr':
        return 360.0 - arcsin
    else:  # tl
        return 180.0 + arcsin


def _angle_side(corner_key: str, R: np.ndarray | float, depth: np.ndarray | float) -> np.ndarray | float:
    """
    计算指定深度 depth 处，left/right 边的直线与圆弧交点的角度（度）。
    支持 numpy 数组，向量化。

    历史用途：同 _angle_bottom，当前未在固定角度方案中使用。
    """
    ratio = (np.asarray(R, dtype=np.float64) - np.asarray(depth, dtype=np.float64)) / np.asarray(R, dtype=np.float64)
    ratio = np.clip(ratio, -1.0, 1.0)
    if corner_key == 'bl':
        return np.degrees(np.arccos(-ratio))
    elif corner_key == 'br':
        return np.degrees(np.arccos(ratio))
    elif corner_key == 'tr':
        return 360.0 - np.degrees(np.arccos(ratio))
    else:  # tl
        return 180.0 + np.degrees(np.arccos(ratio))


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
    # 各角的固定角度范围（统一从 core.corner.algorithm.CORNER_ANGLES 取得，
    # 确保与 carve_corner_on_mask 的 pieslice 角度完全一致）
    ang_min, ang_max = CORNER_ANGLES[corner_key]

    # 只计算角落 ROI 以加速
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
    d_p = float(R) - r  # 每个像素在该层坐标系下的深度

    # 条件 1: 径向属于该层（在该层的环形区域内）
    cond_r = (d_p >= d_outer) & (d_p < d_inner)

    # 条件 2: 角度在固定范围内（确保直线到圆弧的完整连接）
    # 用 atan2 计算角度 (dy, dx)，转为 0~360 度
    angle_p = np.degrees(np.arctan2(dy, dx))
    angle_p = np.mod(angle_p, 360.0)

    # 使用角度偏移法统一处理所有角（包括 tr 的 270°→360° 跨 0° 情况）
    # 将角度平移到以 ang_min 为起点的 [0°, 360°) 范围
    shifted_angle = np.mod(angle_p - ang_min, 360.0)
    angular_span = ang_max - ang_min
    cond_angle = shifted_angle < angular_span

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
        （若采样对面两条边，会采到图内部花纹/背景色，污染中位数，
         造成 TL/TR 偏灰、BL/BR 偏黑的色差现象。）
      - 从该两条边的厚度中心（d_mid）取 ±35% 厚度范围的像素行/列，
        避开抗锯齿过渡带（跳过最外层 2px）。
      - 对所有采样像素取中位数（median），对抗离群抗锯齿像素。
      - 采样位置：距角点 ≥ 1/4 边长的中段纯直线区域。
    """
    arr = np.array(src_img)
    thickness = max(1.0, float(d_thickness))
    samples: list[np.ndarray] = []

    # 采样深度：边框厚度中心 ±35% 的范围（避开最外层 2px 抗锯齿带）
    d_min = int(max(0, round(d_mid - thickness * 0.35)))
    d_max = int(min(max(d_min + 1, round(d_mid + thickness * 0.35)), max(w, h)))

    # 水平方向采样范围（顶/底边）：左右各留 25%，取中间 50%
    h_x0 = max(0, w * 1 // 4)
    h_x1 = min(w, w * 3 // 4)
    if h_x1 - h_x0 < 10:
        h_x0, h_x1 = max(0, w * 3 // 10), min(w, w * 7 // 10)

    # 垂直方向采样范围（左/右边）：上下各留 25%，取中间 50%
    v_y0 = max(0, h * 1 // 4)
    v_y1 = min(h, h * 3 // 4)
    if v_y1 - v_y0 < 10:
        v_y0, v_y1 = max(0, h * 3 // 10), min(h, h * 7 // 10)

    # 仅采样与该角相邻的两条直线边（物理上圆弧只与这两条边衔接）
    # TL: top + left    TR: top + right
    # BL: bottom + left  BR: bottom + right
    if corner_key == 'tl':
        sample_edges = ('top', 'left')
    elif corner_key == 'tr':
        sample_edges = ('top', 'right')
    elif corner_key == 'bl':
        sample_edges = ('bottom', 'left')
    else:  # br
        sample_edges = ('bottom', 'right')

    for edge in sample_edges:
        for d in range(d_min, d_max):
            if edge == 'top':
                # 距顶边 y=d 的行
                if 0 <= d < h and h_x1 > h_x0:
                    samples.append(arr[d, h_x0:h_x1, :])
            elif edge == 'bottom':
                # 距底边 y=h-1-d 的行
                y_pos = h - 1 - d
                if 0 <= y_pos < h and h_x1 > h_x0:
                    samples.append(arr[y_pos, h_x0:h_x1, :])
            elif edge == 'left':
                # 距左边 x=d 的列
                if 0 <= d < w and v_y1 > v_y0:
                    samples.append(arr[v_y0:v_y1, d, :])
            else:  # right
                # 距右边 x=w-1-d 的列
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

    # 取中位数（最鲁棒，对抗抗锯齿离群像素）
    median_color = np.median(all_pixels, axis=0)
    return tuple(int(round(v)) for v in median_color.tolist())


def _redraw_border_on_corner(
    result_img: Image.Image, corner_key: str,
    corner_radius_px: int,
    border_layers: list[tuple[tuple[int, int, int], int]],
    src_img: Image.Image | None = None,
    validity_mask: Image.Image | None = None,
) -> None:
    """
    在圆角区域重新绘制边框颜色。

    修复设计（解决折叠感与边框不对齐）：
    1. 只在边框厚度范围内绘制边框颜色，超出边框层的深度保留原图内容
       （避免将内层图案/背景覆盖为边框颜色，消除"折叠"视觉）
    2. 使用 validity_mask 限制绘制区域，禁止向裁剪区（L形区域）绘制
       （确保裁剪区为背景色，边框只在圆弧可见区域内）
    3. 角度边界与 mask 完全一致（angle < ang_max），消除边界像素偏差

    Args:
        result_img: 结果图片（原地修改）
        corner_key: 角落标识 ('tl','tr','bl','br')
        corner_radius_px: 总圆角半径像素 (R_total)
        border_layers: 边框层 [(color_fallback, thickness), ...]
        src_img: 原图（用于采样颜色）
        validity_mask: 可选，L模式。非零像素才允许修改
    """
    w, h = result_img.size
    if corner_radius_px <= 0 or not border_layers:
        return

    R_total = corner_radius_px

    # 计算该角点的圆心位置
    if corner_key == 'tl':
        cx, cy = R_total, R_total
    elif corner_key == 'tr':
        cx, cy = w - R_total, R_total
    elif corner_key == 'bl':
        cx, cy = R_total, h - R_total
    else:  # br
        cx, cy = w - R_total, h - R_total

    result_arr = np.array(result_img)

    # 构建 validity mask 数组（如果提供）
    validity_arr = None
    if validity_mask is not None:
        validity_arr = np.array(validity_mask, dtype=bool)

    # 构建各边框层的累积厚度
    cumulative_depths = [0]
    for _, thickness in border_layers:
        cumulative_depths.append(cumulative_depths[-1] + thickness)
    total_border_depth = cumulative_depths[-1]

    # 构建颜色映射表（仅映射到边框厚度范围）
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

    # 各角的角度范围（与 carve_corner_on_mask / _build_multi_layer_corner_mask 完全一致）
    ang_min, ang_max = CORNER_ANGLES[corner_key]

    # 计算处理区域（圆心附近的 R×R 区域）
    x1 = max(0, cx - R_total)
    y1 = max(0, cy - R_total)
    x2 = min(w, cx + R_total + 1)
    y2 = min(h, cy + R_total + 1)

    if x2 <= x1 or y2 <= y1:
        return

    # 提取区域坐标网格
    yy, xx = np.mgrid[y1:y2, x1:x2].astype(np.float64)

    # 计算每个像素到圆心的距离和角度
    dx = xx - float(cx)
    dy = yy - float(cy)
    dist = np.sqrt(dx * dx + dy * dy)
    angle = np.degrees(np.arctan2(dy, dx))
    angle = np.mod(angle, 360.0)

    # 计算每个像素的深度（到弧线的距离，弧线上 depth=0）
    depth = float(R_total) - dist

    # 确定该角的有效绘制区域
    # 条件：角度在范围内 + 在圆弧内部或弧线上（dist <= R_total）
    # 注意：不扩展到圆弧外侧（dist > R_total），因为裁剪区应由 mask 控制
    valid_region = (angle >= ang_min) & (angle < ang_max) & (dist <= R_total)

    # 如果有 validity_mask，进一步限制为 mask 中非零像素
    if validity_arr is not None:
        local_validity = validity_arr[y1:y2, x1:x2]
        valid_region = valid_region & local_validity

    # 只在边框厚度范围内应用边框颜色
    # 超出边框厚度的深度保留原图内容
    for d in range(total_border_depth):
        d_region = valid_region & (depth >= d) & (depth < d + 1)
        if not np.any(d_region):
            continue

        color_idx = depth_mapping.get(d, 0)
        color = border_layers[color_idx][0] if color_idx < len(border_layers) else (255, 255, 255)

        color_arr = np.array(color, dtype=result_arr.dtype)
        local_coords = np.where(d_region)
        global_y = local_coords[0] + y1
        global_x = local_coords[1] + x1
        result_arr[global_y, global_x, :] = color_arr[np.newaxis, :]

    # 回写结果
    new_img = Image.fromarray(result_arr.astype(np.uint8), mode='RGB')
    result_img.paste(new_img)


def _sample_line_border_color(
    src_arr: np.ndarray, corner_key: str,
    w: int, h: int, depth: int,
    thickness: int,
) -> tuple[int, int, int]:
    """
    从原图的直线边框区域采样指定深度的颜色。

    Args:
        src_arr: 原图的 numpy 数组
        corner_key: 角落标识
        w: 图像宽度
        h: 图像高度
        depth: 采样深度（像素）
        thickness: 边框厚度（用于确定采样范围）

    Returns:
        采样的颜色 (R, G, B)
    """
    # 确定采样边（与该角相邻的两条直线边）
    if corner_key == 'tl':
        edges = ['top', 'left']
    elif corner_key == 'tr':
        edges = ['top', 'right']
    elif corner_key == 'bl':
        edges = ['bottom', 'left']
    else:  # br
        edges = ['bottom', 'right']

    samples = []
    
    for edge in edges:
        if edge == 'top':
            y = depth
            if 0 <= y < h:
                # 采样水平方向中间部分
                x_start = w // 4
                x_end = w * 3 // 4
                if x_end > x_start:
                    samples.append(src_arr[y, x_start:x_end, :])
        elif edge == 'bottom':
            y = h - 1 - depth
            if 0 <= y < h:
                x_start = w // 4
                x_end = w * 3 // 4
                if x_end > x_start:
                    samples.append(src_arr[y, x_start:x_end, :])
        elif edge == 'left':
            x = depth
            if 0 <= x < w:
                y_start = h // 4
                y_end = h * 3 // 4
                if y_end > y_start:
                    samples.append(src_arr[y_start:y_end, x, :])
        else:  # right
            x = w - 1 - depth
            if 0 <= x < w:
                y_start = h // 4
                y_end = h * 3 // 4
                if y_end > y_start:
                    samples.append(src_arr[y_start:y_end, x, :])

    if not samples:
        return (0, 0, 0)

    all_pixels = np.concatenate([s.reshape(-1, 3) for s in samples if s.size > 0], axis=0)
    if all_pixels.shape[0] == 0:
        return (0, 0, 0)

    # 取中位数作为代表颜色
    median_color = np.median(all_pixels, axis=0)
    return tuple(int(round(v)) for v in median_color.tolist())
