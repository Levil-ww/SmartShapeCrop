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
    在 corner 关联的两条边上的 [d_mid-d_thickness/2, d_mid+d_thickness/2] 深度
    采样并取平均。
    """
    arr = np.array(src_img)
    samples = []
    d0 = max(0, int(math.floor(d_mid - d_thickness * 0.5)))
    d1 = max(d0 + 1, int(math.ceil(d_mid + d_thickness * 0.5)))

    def add_line_samples(indices_0_or_1, axis):
        # axis=0 按 y 方向（竖直边），axis=1 按 x 方向（水平边）
        if axis == 1:  # 水平边：y 固定，扫 x
            if corner_key in ('bl', 'br'):
                y_arr = [h - 1 - d for d in range(d0, d1) if h - 1 - d >= 0]
                for y in y_arr:
                    if 0 <= y < h:
                        x0 = max(0, w * 1 // 3)
                        x1 = min(w, w * 2 // 3)
                        samples.append(arr[y, x0:x1, :])
            else:  # tl/tr
                y_arr = [d for d in range(d0, d1) if d < h]
                for y in y_arr:
                    if 0 <= y < h:
                        x0 = max(0, w * 1 // 3)
                        x1 = min(w, w * 2 // 3)
                        samples.append(arr[y, x0:x1, :])
        else:  # axis=0 竖直边：x 固定，扫 y
            if corner_key in ('tl', 'bl'):
                x_arr = [d for d in range(d0, d1) if d < w]
                for x in x_arr:
                    if 0 <= x < w:
                        y0 = max(0, h * 1 // 3)
                        y1 = min(h, h * 2 // 3)
                        samples.append(arr[y0:y1, x, :])
            else:  # tr/br
                x_arr = [w - 1 - d for d in range(d0, d1) if w - 1 - d >= 0]
                for x in x_arr:
                    if 0 <= x < w:
                        y0 = max(0, h * 1 // 3)
                        y1 = min(h, h * 2 // 3)
                        samples.append(arr[y0:y1, x, :])

    add_line_samples(0, axis=1)  # bottom/top
    add_line_samples(1, axis=0)  # left/right

    if not samples:
        # Fallback: 取边缘像素
        return tuple(arr[h - 1, w // 2, :].tolist())

    all_pixels = np.concatenate([s.reshape(-1, 3) for s in samples if s.size > 0], axis=0)
    if all_pixels.shape[0] == 0:
        return tuple(arr[h - 1, w // 2, :].tolist())

    mean_color = np.mean(all_pixels.astype(np.float64), axis=0)
    return tuple(int(round(v)) for v in mean_color.tolist())


def _redraw_border_on_corner(
    result_img: Image.Image, corner_key: str,
    corner_radius_px: int,
    border_layers: list[tuple[tuple[int, int, int], int]],
    src_img: Image.Image | None = None,
) -> None:
    """
    在圆角弧线上重新绘制边框层（多层同心圆弧设计）。

    核心设计：
    所有边框层共享同一个圆弧圆心（外层圆角圆心），形成同心圆弧：
      - cumulative_i = 所有外层边框的累计厚度
      - R_eff_i = max(0, R_total - cumulative_i)  （该层的有效圆角半径）
      - 圆心固定为外层圆角圆心，不随层数偏移

    这样确保：
    1. 外层边框圆弧半径最大，内层边框圆弧半径递减
    2. 所有层的圆弧同心，自然衔接无错位
    3. 当 R_total <= 某层累计厚度时，该层保持直线（不进行圆角处理）
    4. 边框线完整性：每层在全局深度范围 [cumulative, cumulative+thickness] 内绘制

    Args:
        result_img: 结果图片（原地修改）
        corner_key: 角落标识 ('tl','tr','bl','br')
        corner_radius_px: 总圆角半径像素 (R_total)
        border_layers: 边框层 [(color_fallback, thickness), ...]
        src_img: 原图（可选，用于采样颜色减少色差）
    """
    w, h = result_img.size
    if corner_radius_px <= 0 or not border_layers:
        return

    R_total = corner_radius_px

    # 所有层的圆弧共享同一个圆心（外层圆角圆心）
    # 根据 CAD 图设计，多层边框的圆弧是同心圆弧
    if corner_key == 'tl':
        cx, cy = R_total, R_total
    elif corner_key == 'tr':
        cx, cy = w - R_total, R_total
    elif corner_key == 'bl':
        cx, cy = R_total, h - R_total
    else:  # br
        cx, cy = w - R_total, h - R_total

    result_arr = np.array(result_img)
    cumulative = 0

    for fallback_color, thickness in border_layers:
        # 该层的有效圆角半径：R_eff = max(0, R_total - cumulative)
        # 外层使用完整 R_total，内层使用递减的半径
        R_eff = R_total - cumulative
        if R_eff <= 0:
            # 圆角半径已耗尽，该层保持直线，无需绘制圆弧
            break

        # 该层的深度范围（在全局坐标系中）
        # 外层：深度 0 ~ thickness
        # 内层：深度 cumulative ~ cumulative + thickness
        d_outer_global = float(cumulative)
        d_inner_global = float(cumulative + thickness)

        # 如果该层的有效半径不足以覆盖其完整深度，则截断
        if d_inner_global > float(R_total):
            d_inner_global = float(R_total)

        if d_inner_global <= d_outer_global:
            cumulative += thickness
            continue

        # 1) 构建该层的精确保留遮罩
        # 使用统一圆心 (cx, cy) 和总半径 R_total
        # 深度范围用全局深度（相对于外层圆心）
        mask_bool = _build_border_sector_mask(
            w, h, corner_key, cx, cy, R_total,
            d_outer_global, d_inner_global
        )

        if not np.any(mask_bool):
            cumulative += thickness
            continue

        # 2) 采样真实颜色（优先使用原图，使用该层在原图中的实际深度位置）
        d_mid_original = 0.5 * (d_outer_global + d_inner_global)
        if src_img is not None:
            color = _sample_border_color(
                src_img, corner_key, w, h, d_mid_original, float(thickness)
            )
        else:
            color = fallback_color

        color_arr = np.array(color, dtype=result_arr.dtype)
        result_arr[mask_bool, :] = color_arr[np.newaxis, :]

        cumulative += thickness

    # 回写结果
    new_img = Image.fromarray(result_arr.astype(np.uint8), mode='RGB')
    result_img.paste(new_img)
