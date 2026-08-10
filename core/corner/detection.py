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
    BORDER_MAX_SINGLE_LAYER_CM,
    BORDER_MAX_TOTAL_CM,
    CM_PER_INCH,
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


def _enforce_border_thickness_caps(
    layers: list[tuple[tuple[int, int, int], int]],
    dpi: int = 150,
) -> list[tuple[tuple[int, int, int], int]]:
    """
    对检测到的边框层施加厚度硬上限，防止把内容区/花纹误判为超厚边框。

    [Fix E/S5 延伸]：解决 _detect_border_layers 最末层吃掉 300px 扫描深度的问题。
    规则：
      1. 若某单层厚度 > BORDER_MAX_SINGLE_LAYER_CM → 截断到该上限
         （单层太厚几乎可确定是"内容区 + 边框"混在一起被误判）
      2. 若所有层累计总厚度 > BORDER_MAX_TOTAL_CM → 从最末层开始丢弃，
         直到总厚度 <= 上限。最末层通常是受内容区污染最大的。
      3. 丢弃厚度 < BORDER_MIN_LAYER_THICKNESS_PX 的残差层。

    Args:
        layers: 原始检测层列表 [(color, thickness_px), ...]
        dpi: 图像 DPI（用于厘米↔像素换算）

    Returns:
        厚度合规的层列表
    """
    if not layers:
        return layers

    # 常量（像素）
    px_per_cm = dpi / CM_PER_INCH  # *not* /2.54; CM_PER_INCH=2.54 already
    MAX_SINGLE_PX = int(round(BORDER_MAX_SINGLE_LAYER_CM * px_per_cm))
    MAX_TOTAL_PX = int(round(BORDER_MAX_TOTAL_CM * px_per_cm))
    MIN_PX = BORDER_MIN_LAYER_THICKNESS_PX

    # Step 1: 单层厚度上限（截断过厚的层）
    step1: list[tuple[tuple[int, int, int], int]] = []
    for color, t in layers:
        if t > MAX_SINGLE_PX:
            # 超过单层上限：截断。过厚部分几乎可以确定是内容区污染
            step1.append((color, MAX_SINGLE_PX))
        else:
            step1.append((color, t))
    layers = step1

    # Step 2: 总厚度上限（从最末层开始丢弃，因为最末层最常受内容区污染）
    total = sum(t for _, t in layers)
    while total > MAX_TOTAL_PX and layers:
        # 丢弃最末层
        last_color, last_t = layers.pop()
        total -= last_t
        # 若仅仅是超出一点点，则截断最末层而不是全丢
        if layers and total > MAX_TOTAL_PX:
            # 继续循环丢弃
            continue
        if not layers:
            break
        # 剩下的最后一层如果仍超出，截断它
        last_c2, last_t2 = layers[-1]
        if total > MAX_TOTAL_PX:
            new_t = max(MIN_PX, last_t2 - (total - MAX_TOTAL_PX))
            layers[-1] = (last_c2, new_t)
            total = sum(t for _, t in layers)
            break
        # total <= MAX_TOTAL_PX，退出
        break

    # 再次循环确认（处理极端情况：逐层丢弃后的总厚度）
    total = sum(t for _, t in layers)
    if total > MAX_TOTAL_PX and layers:
        # 若仍超，直接截断最末层到刚好凑满
        excess = total - MAX_TOTAL_PX
        last_c, last_t = layers[-1]
        new_t = max(MIN_PX, last_t - excess)
        layers[-1] = (last_c, new_t)

    # Step 3: 清理厚度小于最小阈值的层
    layers = [(c, t) for c, t in layers if t >= MIN_PX]

    return layers


def _get_border_layers_robust(img: Image.Image, bg_color: tuple = (255, 255, 255)) -> list[tuple[tuple[int, int, int], int]]:
    """
    获取边框层列表，带 fallback 逻辑。
    先尝试 _detect_border_layers，如果失败则从边缘采样寻找非背景色像素。

    [Fix P0-2] 将 bg_color 透传给 _detect_border_layers，使其能执行背景色过滤。

    Args:
        img: 输入图片（RGB）
        bg_color: 背景色（与背景色相似的颜色不作为边框）

    Returns:
        边框层列表 [(color, thickness_px), ...]
    """
    # 先尝试正常检测（透传 bg_color，启用背景色过滤 + 相邻同色合并）
    layers = _detect_border_layers(img, bg_color=bg_color)

    # [Fix E/S5 延伸] 无论检测来自哪条路径，一律施加厚度硬上限，
    # 防止 4.98cm 超厚边框把内容区吞掉并造成纯色填充遮挡。
    layers = _enforce_border_thickness_caps(layers)

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

        # [Fix E/S5] 估算边框厚度：从边缘向内扫描直到颜色变为背景色
        # 添加严格上限，防止把背景/花纹误判为厚边框
        fallback_max_thickness = max(
            BORDER_MIN_LAYER_THICKNESS_PX * 2,
            min(BORDER_SCAN_MAX_DEPTH_PX // 3, max(30, min(w, h) // 20))
        )
        thickness = 0
        for dy in range(min(50, h // 4, fallback_max_thickness + 5)):
            y = h - 1 - dy
            color = tuple(arr[y, w // 2, :])
            dist_to_bg = np.sqrt(sum((a - b) ** 2 for a, b in zip(color, bg_color)))
            if dist_to_bg <= bg_threshold:
                break
            thickness += 1
            # [Fix E/S5] 超过上限立即停止，避免把内层花纹当成边框
            if thickness >= fallback_max_thickness:
                break

        if thickness >= BORDER_MIN_LAYER_THICKNESS_PX:
            return [(best_color, thickness)]

    return []


def _detect_border_layers(img: Image.Image, max_scan_depth_px: int = BORDER_SCAN_MAX_DEPTH_PX,
                          bg_color: tuple[int, int, int] | None = None) -> list[tuple[tuple[int, int, int], int]]:
    """
    检测图像的边框层：从多个方向和位置向内扫描颜色变化，识别边框的颜色和厚度。
    使用颜色距离阈值代替精确匹配，提高鲁棒性。

    [Fix P0-2 / 三处修复之①②]
    - ① 新增 bg_color 参数 + 背景色过滤：与背景色相似的层被剔除，防止 (250,245,230) 等
      近背景色被误判为边框层
    - ② 新增相邻同色合并：颜色相近的相邻层自动合并，防止抗锯齿/渐变制造假层数

    [性能优化] 扫描序列提取 + 平滑 + 距离检测全部 numpy 向量化，
    避免逐像素 Python 循环导致的 300DPI 大图卡顿。

    Args:
        img: 输入图片（RGB）
        max_scan_depth_px: 最大扫描深度（像素），默认引用 config.BORDER_SCAN_MAX_DEPTH_PX
        bg_color: 背景色，与该颜色距离 <= BORDER_BG_SIMILARITY_THRESHOLD 的层被视为背景并过滤

    Returns:
        边框层列表 [(color, thickness_px), ...]，从外到内排序
        如果无法检测，返回包含最边缘颜色的单一层列表
    """
    w, h = img.size
    arr = np.array(img)

    COLOR_DIFF_THRESHOLD = BORDER_COLOR_DISTANCE_THRESHOLD
    MIN_LAYER_THICKNESS = BORDER_MIN_LAYER_THICKNESS_PX
    BG_THRESHOLD = BORDER_BG_SIMILARITY_THRESHOLD

    # --- 向量化扫描：从 4 条边的中点各取一条色序列 ---
    scan_configs = [
        ('bottom', w // 2, None),
        ('top', w // 2, None),
        ('left', h // 2, None),
        ('right', h // 2, None),
    ]

    all_color_seqs = []

    for edge, pos, _ in scan_configs:
        if edge in ('bottom', 'top'):
            depth = min(max_scan_depth_px, h // 4)
            if depth < 10:
                continue
            if edge == 'bottom':
                y_indices = np.arange(h - 1, h - 1 - depth, -1)
            else:
                y_indices = np.arange(depth)
            seq = arr[y_indices, pos, :].astype(np.float64)
        else:
            depth = min(max_scan_depth_px, w // 4)
            if depth < 10:
                continue
            if edge == 'left':
                x_indices = np.arange(depth)
            else:
                x_indices = np.arange(w - 1, w - 1 - depth, -1)
            seq = arr[pos, x_indices, :].astype(np.float64)

        if len(seq) >= 10:
            all_color_seqs.append(seq)

    if not all_color_seqs:
        return []

    # 主用底边扫描
    main_colors = all_color_seqs[0]
    if len(main_colors) < 10:
        return []

    # --- 向量化平滑：滑动窗口均值 ---
    window_size = 3
    pad = window_size // 2
    padded = np.pad(main_colors, ((pad, pad), (0, 0)), mode='edge')
    smoothed = np.zeros_like(main_colors)
    for j in range(3):
        smoothed[:, j] = np.convolve(padded[:, j], np.ones(window_size) / window_size, mode='valid')

    # --- 向量化颜色距离检测 ---
    diffs = np.sqrt(np.sum(np.diff(smoothed, axis=0) ** 2, axis=1))
    change_mask = diffs > COLOR_DIFF_THRESHOLD
    change_indices = np.where(change_mask)[0]

    # 构建层列表
    layers = []
    starts = np.concatenate(([0], change_indices + 1, [len(smoothed)]))
    for k in range(len(starts) - 1):
        s, e = int(starts[k]), int(starts[k + 1])
        thickness = e - s
        if thickness >= MIN_LAYER_THICKNESS:
            avg_color = np.mean(smoothed[s:e], axis=0)
            color_tuple = tuple(int(round(v)) for v in avg_color)
            layers.append((color_tuple, thickness))

    # [Fix P0-2 / 修复② 相邻同色合并]
    if len(layers) >= 2:
        merged: list[tuple[tuple[int, int, int], int]] = []
        cur_col, cur_t = layers[0]
        for col, t in layers[1:]:
            d = float(np.sqrt(sum((a - b) ** 2 for a, b in zip(col, cur_col))))
            if d <= COLOR_DIFF_THRESHOLD:
                total_t = cur_t + t
                if total_t > 0:
                    cur_col = tuple(int(round((cur_col[j] * cur_t + col[j] * t) / total_t)) for j in range(3))
                cur_t = total_t
            else:
                merged.append((cur_col, cur_t))
                cur_col, cur_t = col, t
        merged.append((cur_col, cur_t))
        layers = merged

    # [Fix P0-2 / 修复① 背景色过滤]
    if bg_color is not None and layers:
        filtered: list[tuple[tuple[int, int, int], int]] = []
        for col, t in layers:
            d = float(np.sqrt(sum((a - b) ** 2 for a, b in zip(col, bg_color))))
            if d <= BG_THRESHOLD:
                continue
            filtered.append((col, t))
        layers = filtered

    # 如果没有检测到层，使用 fallback
    if not layers:
        edge_color = tuple(arr[h - 1, w // 2, :])
        est_thickness = 0
        for dy in range(min(50, h // 4)):
            y = h - 1 - dy
            color = tuple(arr[y, w // 2, :])
            dist = float(np.sqrt(sum((a - b) ** 2 for a, b in zip(color, edge_color))))
            if dist > COLOR_DIFF_THRESHOLD * 2:
                break
            est_thickness += 1
        if est_thickness >= MIN_LAYER_THICKNESS:
            layers.append((edge_color, est_thickness))

    # [Fix E/S5] 统一施加厚度硬上限
    layers = _enforce_border_thickness_caps(layers)

    return layers


def _scan_edge_boundaries(img_arr: np.ndarray,
                          edge: str,
                          max_depth_pct: float = 0.45) -> list[int]:
    """
    从某一条边向内扫描，检测颜色突变的边界位置。

    [性能优化] 使用 numpy 数组操作代替逐像素 Python 循环。

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

    max_depth = max(max_depth, 20)

    # 取中线 ±10% 范围内 3 条采样线
    perp_mid = perp_len // 2
    perp_span = max(1, int(perp_len * 0.1))
    sample_lines = [perp_mid - perp_span, perp_mid, perp_mid + perp_span]
    sample_lines = [max(_EDGE_IGNORE_PX, min(perp_len - 1 - _EDGE_IGNORE_PX, s)) for s in sample_lines]

    # 构造扫描索引序列
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

    # --- 向量化：对所有采样线一次提取所有像素 ---
    idx_arr = np.array(indices)
    n_lines = len(sample_lines)
    n_indices = len(idx_arr)
    all_diff = np.zeros(n_indices, dtype=np.float64)

    for line_pos in sample_lines:
        if edge in ('top', 'bottom'):
            pixels = img_arr[idx_arr, line_pos, :].astype(np.float64)
        else:
            pixels = img_arr[line_pos, idx_arr, :].astype(np.float64)
        values = pixels.sum(axis=1)
        if len(values) > 1:
            diff = np.abs(np.diff(values))
            diff = np.concatenate([diff, [0.0]])
            all_diff += diff

    all_diff /= n_lines

    # --- 向量化找峰值 ---
    threshold = _BORDER_COLOR_DIFF_THRESHOLD * 3
    if len(all_diff) < 3:
        return []
    prev_vals = all_diff[:-2]
    curr_vals = all_diff[1:-1]
    next_vals = all_diff[2:]
    peak_mask = (curr_vals >= threshold) & (curr_vals >= prev_vals) & (curr_vals >= next_vals)
    peak_indices = np.where(peak_mask)[0] + 1  # +1 因为 curr_vals 从索引 1 开始

    # --- 转换为像素坐标，去重+合并过近的边界 ---
    boundaries_px: list[int] = []
    seen: set[int] = set()
    for pi in peak_indices:
        actual = int(idx_arr[pi])
        too_close = any(abs(actual - b) < _BORDER_MIN_GAP_PX for b in boundaries_px)
        if not too_close and actual not in seen:
            boundaries_px.append(actual)
            seen.add(actual)

    # 统一排序：按"距最外边缘"由近到远
    if edge in ('top', 'left'):
        boundaries_px.sort()
    else:  # bottom, right
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
