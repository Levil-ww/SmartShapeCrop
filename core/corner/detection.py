"""
core/corner/detection.py
边框层自动检测：从图像中识别嵌套矩形边框与多层颜色边框。

从原 core/image_cropper.py 拆分而来，逻辑未变化。

包含两类检测：
1. _detect_border_layers / _get_border_layers_robust：基于颜色距离的逐层检测（阈值 15）
2. _scan_edge_boundaries / detect_nested_rect_layers：基于亮度突变的边界检测（阈值 25）
   两类算法使用不同阈值是有意为之，分别处理不同的边框识别场景，
   合并属于未来工作（见 ProductSummary/程序分析与优化-0806.md §八 P1）。

参数集中管理：所有阈值/步长常量定义在 core/config.py，本模块仅保留带下划线前缀的
别名（_BORDER_SCAN_STEP 等）用于向后兼容旧测试脚本 `from core.image_cropper import ...`。

向后兼容：原 core/image_cropper.py 已改为薄重导出 shim，旧导入路径继续可用。
"""
from __future__ import annotations
import numpy as np
from collections import Counter
from PIL import Image

from ..config import (
    BORDER_COLOR_DISTANCE_THRESHOLD,
    BORDER_LUMINANCE_DIFF_THRESHOLD,
    BORDER_SCAN_STEP_PX,
    BORDER_MIN_GAP_PX,
    BORDER_MAX_LAYERS,
    BORDER_EDGE_IGNORE_PX,
    BORDER_MIN_LAYER_THICKNESS_PX,
    BORDER_BG_SIMILARITY_THRESHOLD,
    BORDER_SCAN_MAX_DEPTH_PX,
)


# ============================================================================
# 多层边框检测参数（向后兼容别名，单一来源为 core/config.py）
# ============================================================================
# 旧调用方可能仍使用 `from core.image_cropper import _BORDER_SCAN_STEP` 等导入路径，
# 因此保留这些带下划线前缀的别名。新代码请直接 from core.config import ...。

_BORDER_SCAN_STEP = BORDER_SCAN_STEP_PX                       # 扫描步长（像素）
_BORDER_COLOR_DIFF_THRESHOLD = BORDER_LUMINANCE_DIFF_THRESHOLD  # 亮度差分阈值（R+G+B 总和）
_BORDER_MIN_GAP_PX = BORDER_MIN_GAP_PX                        # 相邻边界最小间距
_BORDER_MAX_LAYERS = BORDER_MAX_LAYERS                        # 最多检测层数上限
_EDGE_IGNORE_PX = BORDER_EDGE_IGNORE_PX                       # 忽略最边缘像素数


def _get_border_layers_robust(img: Image.Image, bg_color: tuple = (255, 255, 255)) -> list[tuple[tuple[int, int, int], int]]:
    """
    获取边框层列表，带 fallback 逻辑。
    先尝试 _detect_border_layers，如果失败则从边缘采样寻找非背景色像素。
    
    Args:
        img: 输入图片（RGB）
        bg_color: 背景色（与背景色相似的颜色不作为边框）
    
    Returns:
        边框层列表 [(color, thickness_px), ...]
    """
    # 先尝试正常检测
    layers = _detect_border_layers(img)

    if layers:
        return layers

    # Fallback：从图像边缘采样，寻找非背景色的深色像素
    w, h = img.size
    arr = np.array(img)

    # 背景色相似度阈值统一引用 config.BORDER_BG_SIMILARITY_THRESHOLD
    bg_threshold = BORDER_BG_SIMILARITY_THRESHOLD

    # 从4条边采样，寻找最常见的非背景色
    edge_colors = []
    sample_positions = [
        ('bottom', w // 2),
        ('top', w // 2),
        ('left', h // 2),
        ('right', h // 2),
    ]

    for edge, pos in sample_positions:
        if edge in ('bottom', 'top'):
            for dy in range(min(30, h // 4)):
                y = h - 1 - dy if edge == 'bottom' else dy
                color = tuple(arr[y, pos, :])
                dist_to_bg = np.sqrt(sum((a - b) ** 2 for a, b in zip(color, bg_color)))
                if dist_to_bg > bg_threshold:
                    edge_colors.append(color)
                    break
        else:
            for dx in range(min(30, w // 4)):
                x = dx if edge == 'left' else w - 1 - dx
                color = tuple(arr[pos, x, :])
                dist_to_bg = np.sqrt(sum((a - b) ** 2 for a, b in zip(color, bg_color)))
                if dist_to_bg > bg_threshold:
                    edge_colors.append(color)
                    break

    if edge_colors:
        # 取最常见的颜色作为边框颜色
        color_counts = Counter(edge_colors)
        best_color = color_counts.most_common(1)[0][0]

        # 估算边框厚度：从边缘向内扫描直到颜色变为背景色
        thickness = 0
        for dy in range(min(50, h // 4)):
            y = h - 1 - dy
            color = tuple(arr[y, w // 2, :])
            dist_to_bg = np.sqrt(sum((a - b) ** 2 for a, b in zip(color, bg_color)))
            if dist_to_bg <= bg_threshold:
                break
            thickness += 1

        if thickness >= BORDER_MIN_LAYER_THICKNESS_PX:
            return [(best_color, thickness)]

    return []


def _detect_border_layers(img: Image.Image, max_scan_depth_px: int = BORDER_SCAN_MAX_DEPTH_PX) -> list[tuple[tuple[int, int, int], int]]:
    """
    检测图像的边框层：从多个方向和位置向内扫描颜色变化，识别边框的颜色和厚度。
    使用颜色距离阈值代替精确匹配，提高鲁棒性。

    Args:
        img: 输入图片（RGB）
        max_scan_depth_px: 最大扫描深度（像素），默认引用 config.BORDER_SCAN_MAX_DEPTH_PX

    Returns:
        边框层列表 [(color, thickness_px), ...]，从外到内排序
        如果无法检测，返回包含最边缘颜色的单一层列表
    """
    w, h = img.size
    arr = np.array(img)

    # 阈值统一引用 config.BORDER_COLOR_DISTANCE_THRESHOLD（欧氏距离）
    COLOR_DIFF_THRESHOLD = BORDER_COLOR_DISTANCE_THRESHOLD
    # 最小层厚度统一引用 config.BORDER_MIN_LAYER_THICKNESS_PX
    MIN_LAYER_THICKNESS = BORDER_MIN_LAYER_THICKNESS_PX
    
    # 从4条边的中点向内扫描，取平均结果
    scan_configs = [
        ('bottom', w // 2, None),
        ('top', w // 2, None),
        ('left', h // 2, None),
        ('right', h // 2, None),
    ]
    
    all_color_seqs = []
    
    for edge, pos, _ in scan_configs:
        colors = []
        if edge in ('bottom', 'top'):
            depth = min(max_scan_depth_px, h // 4)
            if depth < 10:
                continue
            for dy in range(depth):
                if edge == 'bottom':
                    y = h - 1 - dy
                else:
                    y = dy
                colors.append(tuple(arr[y, pos, :]))
        else:
            depth = min(max_scan_depth_px, w // 4)
            if depth < 10:
                continue
            for dx in range(depth):
                if edge == 'left':
                    x = dx
                else:
                    x = w - 1 - dx
                colors.append(tuple(arr[pos, x, :]))
        
        if len(colors) >= 10:
            all_color_seqs.append(colors)
    
    if not all_color_seqs:
        return []
    
    # 合并所有扫描结果，检测颜色变化
    # 主用底边扫描，辅以其他扫描结果验证
    main_colors = all_color_seqs[0]  # 底边
    
    if len(main_colors) < 10:
        return []
    
    # 对颜色序列进行平滑处理（减少噪声）
    smoothed_colors = []
    window_size = 3
    for i in range(len(main_colors)):
        start = max(0, i - window_size // 2)
        end = min(len(main_colors), i + window_size // 2 + 1)
        window = main_colors[start:end]
        avg_color = tuple(int(round(np.mean([c[j] for c in window]))) for j in range(3))
        smoothed_colors.append(avg_color)
    
    # 使用颜色距离阈值检测层边界
    layers = []
    current_color = smoothed_colors[0]
    current_start = 0
    
    for i in range(1, len(smoothed_colors)):
        # 计算欧几里得颜色距离
        dist = np.sqrt(sum((a - b) ** 2 for a, b in zip(smoothed_colors[i], current_color)))
        if dist > COLOR_DIFF_THRESHOLD:
            thickness = i - current_start
            if thickness >= MIN_LAYER_THICKNESS:
                layers.append((current_color, thickness))
            current_color = smoothed_colors[i]
            current_start = i
    
    # 添加最后一层
    thickness = len(smoothed_colors) - current_start
    if thickness >= MIN_LAYER_THICKNESS:
        layers.append((current_color, thickness))
    
    # 如果没有检测到层，使用 fallback：取最边缘像素颜色作为第一层
    if not layers:
        edge_color = tuple(arr[h - 1, w // 2, :])
        # 估算边框厚度：扫描到第一个明显颜色变化为止
        est_thickness = 0
        for dy in range(min(50, h // 4)):
            y = h - 1 - dy
            color = tuple(arr[y, w // 2, :])
            dist = np.sqrt(sum((a - b) ** 2 for a, b in zip(color, edge_color)))
            if dist > COLOR_DIFF_THRESHOLD * 2:
                break
            est_thickness += 1
        if est_thickness >= MIN_LAYER_THICKNESS:
            layers.append((edge_color, est_thickness))
    
    return layers


def _scan_edge_boundaries(img_arr: np.ndarray,
                          edge: str,
                          max_depth_pct: float = 0.45) -> list[int]:
    """
    从某一条边向内扫描，检测颜色突变的边界位置。

    Args:
        img_arr: H×W×3 的 numpy 数组（RGB）
        edge: 'top' | 'bottom' | 'left' | 'right'
        max_depth_pct: 最大扫描深度占图片宽/高的比例（默认 45%，避免扫到中心）

    Returns:
        边界位置列表（像素坐标），从外向内排序。
        对于 top/bottom: 坐标是 y 值（行号）
        对于 left/right: 坐标是 x 值（列号）
    """
    H, W = img_arr.shape[:2]
    if edge in ('top', 'bottom'):
        axis_len = H
        perp_len = W
        max_depth = int(axis_len * max_depth_pct)
    else:
        axis_len = W
        perp_len = H
        max_depth = int(axis_len * max_depth_pct)

    max_depth = max(max_depth, 20)  # 至少扫描 20px

    # 取一条垂/水平中线上的像素，计算每一步的颜色变化
    # 为更鲁棒，取中线 ±10% 范围内几条线的平均
    perp_mid = perp_len // 2
    perp_span = max(1, int(perp_len * 0.1))
    sample_lines = [perp_mid - perp_span, perp_mid, perp_mid + perp_span]
    sample_lines = [max(_EDGE_IGNORE_PX, min(perp_len - 1 - _EDGE_IGNORE_PX, s)) for s in sample_lines]

    # 构造扫描顺序：从外到内的索引序列
    if edge == 'top':
        indices = list(range(_EDGE_IGNORE_PX, min(axis_len - _EDGE_IGNORE_PX, _EDGE_IGNORE_PX + max_depth), _BORDER_SCAN_STEP))
    elif edge == 'bottom':
        indices = list(range(axis_len - 1 - _EDGE_IGNORE_PX,
                             max(_EDGE_IGNORE_PX, axis_len - 1 - _EDGE_IGNORE_PX - max_depth),
                             -_BORDER_SCAN_STEP))
    elif edge == 'left':
        indices = list(range(_EDGE_IGNORE_PX, min(axis_len - _EDGE_IGNORE_PX, _EDGE_IGNORE_PX + max_depth), _BORDER_SCAN_STEP))
    else:  # right
        indices = list(range(axis_len - 1 - _EDGE_IGNORE_PX,
                             max(_EDGE_IGNORE_PX, axis_len - 1 - _EDGE_IGNORE_PX - max_depth),
                             -_BORDER_SCAN_STEP))

    if len(indices) < 3:
        return []

    # 对每条采样线计算颜色强度（R+G+B）随深度的变化
    all_diff = np.zeros(len(indices), dtype=np.float64)
    for line_pos in sample_lines:
        values = []
        for idx in indices:
            if edge in ('top', 'bottom'):
                px = img_arr[idx, line_pos, :].astype(np.float64)
            else:
                px = img_arr[line_pos, idx, :].astype(np.float64)
            values.append(px.sum())  # 用亮度总和衡量
        values = np.array(values, dtype=np.float64)
        # 计算一阶差分绝对值
        if len(values) > 1:
            diff = np.abs(np.diff(values))
            # 差分比索引短1，末尾补0对齐
            diff = np.concatenate([diff, [0.0]])
            all_diff += diff

    # 平均化
    all_diff /= len(sample_lines)

    # 寻找显著的差分峰值（颜色突变点 = 边界）
    threshold = _BORDER_COLOR_DIFF_THRESHOLD * 3  # 因为用的是 R+G+B 总和
    peak_indices = []
    for i in range(1, len(all_diff) - 1):
        if (all_diff[i] >= threshold
                and all_diff[i] >= all_diff[i - 1]
                and all_diff[i] >= all_diff[i + 1]):
            peak_indices.append(i)

    # 把峰值对应的"扫描索引位置"转换为实际像素坐标，并按距离外边缘从小到大排序
    boundaries_px = []
    seen = set()
    for pi in peak_indices:
        actual = indices[pi]
        # 与已有的边界距离过近则合并
        too_close = False
        for b in boundaries_px:
            if abs(actual - b) < _BORDER_MIN_GAP_PX:
                too_close = True
                break
        if not too_close and actual not in seen:
            boundaries_px.append(actual)
            seen.add(actual)

    # 统一排序：按"距最外边缘"由近到远
    if edge == 'top':
        boundaries_px.sort()
    elif edge == 'bottom':
        boundaries_px.sort(reverse=True)
    elif edge == 'left':
        boundaries_px.sort()
    else:  # right
        boundaries_px.sort(reverse=True)

    return boundaries_px[:_BORDER_MAX_LAYERS]


def detect_nested_rect_layers(img: Image.Image) -> list[tuple[int, int, int, int]]:
    """
    自动检测图片中嵌套的矩形边框层。

    原理：
      分别从上/下/左/右 4 条边向内扫描颜色突变点（边界），
      然后将 4 条边上的边界位置两两配对，构成嵌套矩形。

    Args:
        img: PIL RGB 图片

    Returns:
        列表：[(x1, y1, x2, y2), ...]  从最外层往最内层排序；
        如果检测失败，返回只包含整图外框的一层。
    """
    w, h = img.size
    arr = np.array(img, dtype=np.uint8)

    top_ys = _scan_edge_boundaries(arr, 'top')
    bottom_ys = _scan_edge_boundaries(arr, 'bottom')
    left_xs = _scan_edge_boundaries(arr, 'left')
    right_xs = _scan_edge_boundaries(arr, 'right')

    # 每一层必须有 4 条边共同对应，因此取最少边数
    n_layers = min(len(top_ys), len(bottom_ys), len(left_xs), len(right_xs))

    # 第 0 层 = 最外层边框（扫描到的第一条边）
    # 第 1 层 = 往里的第二条，……
    rects = []
    for i in range(n_layers):
        x1 = left_xs[i]
        y1 = top_ys[i]
        x2 = right_xs[i]
        y2 = bottom_ys[i]
        # 坐标合法性检查（左<右 且 上<下，且有足够面积）
        if x2 - x1 > 20 and y2 - y1 > 20:
            rects.append((x1, y1, x2, y2))

    # 如果没检测到任何层，退化为整图
    if not rects:
        rects.append((0, 0, w - 1, h - 1))

    return rects
