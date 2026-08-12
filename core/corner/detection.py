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
      0. 若某层厚度接近上限且是前一层厚度的 3 倍以上 → 判定为内容区伪边框，丢弃
         （花幔/墨上花开等场景：内容区被误判为边框层，厚度接近上限）
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

    # Step 0: 丢弃"内容区伪边框层"
    # 识别规则：如果某层厚度接近上限（>= 85% of MAX_SINGLE_PX），
    # 且是前一层厚度的 3 倍以上，则判定为内容区被误判为边框，直接丢弃。
    # 典型场景：花幔/墨上花开的"黑边框(5px) + 间隙(8px) + 内容区(118px)"
    # 中，内容区(118px)被误判为边框层，需要丢弃。
    if len(layers) >= 2:
        filtered_layers = [layers[0]]  # 保留第一层（最外层边框）
        for i in range(1, len(layers)):
            cur_color, cur_t = layers[i]
            prev_color, prev_t = filtered_layers[-1]
            
            # [Fix Moshang 5cm 圆角过厚] 更敏感的伪边框检测
            # 场景：墨上花开/花幔等有花卉图案的图片，内容区被误判为边框层
            # 规则 A：厚度接近上限(>=85%) 且是前一层3倍以上 → 伪边框
            # 规则 B：当前层是前一层3倍以上 且 前一层是薄边框(<=1cm) → 伪边框
            #   - 薄边框(如黑边框5-10px)之后出现一个3倍厚的层(内容区48px)
            #   - 这几乎可以确定是内容区被误判，因为真正的多层边框厚度是渐进的
            cur_is_fake = False
            
            # 规则 A：原逻辑（保留）
            if cur_t >= MAX_SINGLE_PX * 0.85 and prev_t > 0 and cur_t >= prev_t * 3:
                cur_is_fake = True
            
            # 规则 B：新增 - 薄边框后的3倍跃变
            # 前一层是薄边框(<=1cm)，当前层突然3倍以上 → 内容区伪装
            THIN_BORDER_PX = int(round(1.0 * px_per_cm))  # 1cm in pixels
            if not cur_is_fake and prev_t >= 2 and prev_t <= THIN_BORDER_PX:
                if cur_t >= prev_t * 3 and cur_t >= 3 * BORDER_MIN_LAYER_THICKNESS_PX:
                    cur_is_fake = True
            
            # 规则 C：新增 - 累计深度检查
            # 如果当前累计深度已超过2cm，且当前层厚度与内容参考色接近 → 伪边框
            # （此规则在 _filter_layers_by_content_ref 之后生效）
            
            if not cur_is_fake:
                filtered_layers.append((cur_color, cur_t))
        
        layers = filtered_layers

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
    
    # Step 4: [Fix Moshang] 最大层数限制
    # 真实边框通常不超过 4 层（如 塞纳时光 = 3 层），超过则极可能是内容花纹被误判
    # 保留最外层的层（它们最可能是真实边框）
    MAX_LAYERS_HARD = 4
    if len(layers) > MAX_LAYERS_HARD:
        layers = layers[:MAX_LAYERS_HARD]

    return layers


def _estimate_content_reference(img: Image.Image) -> np.ndarray:
    """
    估算图片的「内容参考色」——从图片中心区域采样的中位色。

    用途：用于区分真正的边框层（黑/棕/蓝等）与背景内容色（米色/白色等）。
    当 bg_color 与图片内容色非常接近时（如都是米色），_detect_border_layers
    无法有效过滤，此时需要用内容参考色来辅助判断。

    Args:
        img: 输入图片

    Returns:
        内容参考色的 RGB 数组 (3,)
    """
    arr = np.array(img, dtype=np.float64)
    h, w = arr.shape[:2]
    # 中心区域 (15%~85%)
    x_start, x_end = int(w * 0.15), int(w * 0.85)
    y_start, y_end = int(h * 0.15), int(h * 0.85)
    if x_end - x_start < 10 or y_end - y_start < 10:
        return np.array([255.0, 255.0, 255.0])
    region = arr[y_start:y_end, x_start:x_end, :].reshape(-1, 3)
    return np.median(region, axis=0)


def _filter_layers_by_content_ref(
    layers: list[tuple[tuple[int, int, int], int]],
    content_ref: np.ndarray,
    dist_threshold: float = 35.0,
) -> list[tuple[tuple[int, int, int], int]]:
    """
    [Fix P0-3 新增] 用内容参考色过滤伪边框层。

    问题场景：当 bg_color 与图片内容色很接近时（如都是米色 #F5EBD7），
    _detect_border_layers 会把内容区也当成边框层。

    解决方案：计算每层颜色与内容参考色的欧氏距离。
    - 若距离 > dist_threshold → 该层是真正的边框色（黑/棕/蓝）→ 保留
    - 若距离 <= dist_threshold → 该层是内容/背景色伪装层 → 过滤掉

    额外：若过滤后全部被移除（极端情况），保留原列表。

    Args:
        layers: 原始边框层列表
        content_ref: 内容参考色 (3,)
        dist_threshold: 颜色距离阈值

    Returns:
        过滤后的边框层列表
    """
    if not layers:
        return layers

    # [Fix P0-6] 结构感知过滤：不要简单地删除内容色相似的层。
    # 若一层与内容参考色相似，但其相邻层是有效边框（与内容色差异大），
    # 则该层是边框系统的间隙层（如两圈黑框之间的白色间隙），应保留。
    # 只有独立存在的、与内容色相似的伪边框层才应被过滤。
    n = len(layers)
    layer_valid = []
    for i, (color, thickness) in enumerate(layers):
        col_arr = np.array(color, dtype=np.float64)
        dist = float(np.sqrt(np.sum((col_arr - content_ref) ** 2)))
        if dist > dist_threshold:
            # 与内容色差异大 → 肯定是有效边框
            layer_valid.append(True)
        else:
            # 与内容色相似 → 检查相邻层
            has_valid_neighbor = False
            for ni in (i - 1, i + 1):
                if 0 <= ni < n:
                    nc, _ = layers[ni]
                    nc_arr = np.array(nc, dtype=np.float64)
                    nd = float(np.sqrt(np.sum((nc_arr - content_ref) ** 2)))
                    if nd > dist_threshold:
                        has_valid_neighbor = True
                        break
            # 有有效边框邻居 → 保留作为间隙层
            layer_valid.append(has_valid_neighbor)

    filtered = [layers[i] for i in range(n) if layer_valid[i]]

    # 若全部被过滤掉（理论上不应发生于有边框的真实图），保留原列表
    # 以避免完全丢失检测结果
    if not filtered:
        return layers

    return filtered


def _get_border_layers_robust(img: Image.Image, bg_color: tuple = (255, 255, 255)) -> list[tuple[tuple[int, int, int], int]]:
    """
    获取边框层列表，带 fallback 逻辑。
    先尝试 _detect_border_layers，如果失败则从边缘采样寻找非背景色像素。

    [Fix P0-3 增强] 新增内容参考色过滤机制：
      1. 估算图片内容参考色（中心区域中位色）
      2. 用内容参考色过滤掉伪装成边框的内容/背景色层
      3. 这解决了米色背景被误判为边框层的根因问题

    Args:
        img: 输入图片（RGB）
        bg_color: 背景色（与背景色相似的颜色不作为边框）

    Returns:
        边框层列表 [(color, thickness_px), ...]
    """
    # Step 1: 估算内容参考色（辅助背景过滤）
    content_ref = _estimate_content_reference(img)

    # Step 2: 正常检测（透传 bg_color，启用背景色过滤 + 相邻同色合并）
    layers = _detect_border_layers(img, bg_color=bg_color)

    # Step 3: [Fix P0-3] 内容参考色过滤 —— 剔除伪装成边框的内容/背景色层
    # 这是解决"米色背景被误判为边框层"的关键一步
    layers = _filter_layers_by_content_ref(layers, content_ref)

    # Step 4: 厚度硬上限，防止超厚边框把内容区吞掉
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

        # 估算边框厚度：从边缘向内扫描直到颜色变为背景色
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
            # 超过上限立即停止，避免把内层花纹当成边框
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

    # 构建层列表：包含所有色段（包括 < MIN_LAYER_THICKNESS 的过渡带）
    # 过渡带（1-2px）是抗锯齿像素，需要合并到相邻的厚层中
    raw_layers = []
    starts = np.concatenate(([0], change_indices + 1, [len(smoothed)]))
    for k in range(len(starts) - 1):
        s, e = int(starts[k]), int(starts[k + 1])
        thickness = e - s
        avg_color = np.mean(smoothed[s:e], axis=0)
        color_tuple = tuple(int(round(v)) for v in avg_color)
        raw_layers.append((color_tuple, thickness))

    # [Fix P0-4 抗锯齿带合并]
    # 将 < MIN_LAYER_THICKNESS 的薄层（抗锯齿过渡带）合并到相邻的厚层中。
    # 关键修复：合并时**不改变主层颜色**，只累加厚度。
    # 原因：抗锯齿像素是前景色和背景色的混合，其颜色不应污染任何一侧的纯色。
    # 正确做法是将过渡带的像素数量加到相邻的纯色层上，保持纯色不变。
    #
    # 这修复了因抗锯齿带被丢弃而导致的边框结构断裂问题：
    #   外层黑(120px) → 抗锯齿(1px) → 背景(30px) → 抗锯齿(1px) → 内层黑(100px)
    # 之前：抗锯齿被丢弃，变成 黑(120) + 背景(30) + 黑(100)  → 背景合并两段黑
    # 现在：抗锯齿合并到邻层，黑(121) + 背景(30) + 黑(101) → 结构正确
    merged_layers = []
    for col, t in raw_layers:
        if t < MIN_LAYER_THICKNESS and merged_layers:
            # 薄层合并到前一个层：只增加厚度，不改变颜色
            prev_col, prev_t = merged_layers[-1]
            merged_layers[-1] = (prev_col, prev_t + t)
        else:
            merged_layers.append((col, t))

    # 再次检查：如果第一个层就是薄层，向后合并
    if merged_layers and merged_layers[0][1] < MIN_LAYER_THICKNESS and len(merged_layers) > 1:
        first_col, first_t = merged_layers[0]
        second_col, second_t = merged_layers[1]
        merged_layers[0] = (second_col, first_t + second_t)
        merged_layers.pop(1)

    layers = [(c, t) for c, t in merged_layers if t >= MIN_LAYER_THICKNESS]

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

    # [Fix P0-7 花纹周期截断]
    # 问题：花野/墨上花开 等带规则重复图案（四叶草/花朵线条）的图片，
    #       _detect_border_layers 会把每行/每朵花纹都识别成独立"边框层"，
    #       产生 10+ 层交替伪层（如 30,30,30 ↔ 220,200,160 循环），
    #       导致 sector_render 的 R_eff 递减逻辑、累计深度映射全部错位，
    #       最终在圆角处渲染出"混乱弧形碎片"（花野截图红框）。
    #
    # 判据 1: 连续交替模式 — 从第 2 层开始若出现 color[i]==color[i-2] (±阈值)
    #         且厚度在相似范围内交替 → 进入花纹重复周期，截断。
    # 判据 2: 层数硬上限 — 超过 BORDER_MAX_LAYERS (默认 8) 的部分丢弃，
    #         并回退到最近的"与内容色距离大"的边界，避免在花纹中间截断。
    # 判据 3: 最小边框系统 — 至少保留前 2 层（外层+间隙），以防误截。
    if len(layers) > 2:
        ALT_COLOR_DIST = 20.0  # 判定"同色回归"的距离阈值
        ALT_THICK_RATIO = 0.5  # 相邻厚度比例 > 此值判定为周期厚度相似
        cut_idx = len(layers)
        for i in range(4, len(layers)):
            ci = np.array(layers[i][0], dtype=np.float64)
            ci2 = np.array(layers[i - 2][0], dtype=np.float64)
            d_alt = float(np.sqrt(np.sum((ci - ci2) ** 2)))
            t_ratio = min(layers[i][1], layers[i - 2][1]) / max(1, max(layers[i][1], layers[i - 2][1]))
            if d_alt <= ALT_COLOR_DIST and t_ratio >= ALT_THICK_RATIO:
                # 已进入 A↔B 交替模式，在此之前截断（但至少保留 2 层）
                cut_idx = max(2, i - 1)
                break
        # 硬上限兜底：截断到 max(2, BORDER_MAX_LAYERS - 2) 层（保留安全余量）
        hard_limit = max(2, _BORDER_MAX_LAYERS - 2)
        cut_idx = min(cut_idx, hard_limit)
        layers = layers[:cut_idx]

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


def detect_nested_rect_layers(
    img: Image.Image,
    border_layers: list[tuple[tuple[int, int, int], int]] | None = None,
) -> list[tuple[int, int, int, int]]:
    """
    自动检测图片中嵌套的矩形边框层。

    [Fix P0-3 增强] 新增基于 border_layers 的厚度推断：
      当边缘扫描（_scan_edge_boundaries）因花纹/抗锯齿干扰而检测不到足够层时，
      使用边框层的累计厚度来推断内层矩形坐标，确保每层边框都有对应的矩形。

    Args:
        img: PIL RGB 图片
        border_layers: 可选，已检测的边框层列表 [(color, thickness_px), ...]

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
    n_layers_from_scan = min(len(top_ys), len(bottom_ys), len(left_xs), len(right_xs))

    # 第 0 层 = 最外层边框（扫描到的第一条边）
    # 第 1 层 = 往里的第二条，……
    rects = []
    for i in range(n_layers_from_scan):
        x1 = left_xs[i]
        y1 = top_ys[i]
        x2 = right_xs[i]
        y2 = bottom_ys[i]
        # 坐标合法性检查（左<右 且 上<下，且有足够面积）
        if x2 - x1 > 20 and y2 - y1 > 20:
            rects.append((x1, y1, x2, y2))

    # [Fix P0-3 新增] 基于 border_layers 的厚度推断
    # 当边缘扫描层数不足时，用边框累计厚度推算内层矩形坐标
    if border_layers and len(border_layers) > 0:
        # 计算边框层的累计厚度
        cumulative_depths = [0]
        for _, thickness in border_layers:
            cumulative_depths.append(cumulative_depths[-1] + thickness)
        total_depth = cumulative_depths[-1]

        # 如果扫描层数不够，尝试用累计厚度推断内层矩形
        n_scanned = len(rects)
        if n_scanned < len(cumulative_depths) - 1:
            # 需要补充的层数
            for k in range(n_scanned, len(cumulative_depths) - 1):
                depth_k = cumulative_depths[k + 1]  # 第 k 层的外边缘距图边的距离
                # 内层矩形坐标 = 边框外边缘往里 depth_k
                x1_k = depth_k
                y1_k = depth_k
                x2_k = w - 1 - depth_k
                y2_k = h - 1 - depth_k
                if x2_k - x1_k > 20 and y2_k - y1_k > 20:
                    rects.append((x1_k, y1_k, x2_k, y2_k))

    # 如果没检测到任何层，退化为整图
    if not rects:
        rects.append((0, 0, w - 1, h - 1))

    return rects
