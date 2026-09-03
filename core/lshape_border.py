"""
core/lshape_border.py
L 形挖角的素材边框补全。

当对带有自绘边框的素材图（如克罗印花的棕色边框+黑色内框、
安妮森林的黑色细边框）应用 L 形挖角时，挖掉的角落区域
（fill_color / hole_bg_color）的两条新边缘需要绘制与素材图
一致的边框层，使 L 形成品在视觉上呈现完整的外框。

核心流程：
  1. detect_pool_material_borders(src_img, bg_color)
     → 从素材图自动检测边框层 [(color, thickness_px), ...]
     → 过滤掉外背景伪边框（color 接近白色且厚度过大）

  2. compute_cut_edge_bbox(outer_rect, cut_corner, cut_w, cut_h, border_layers)
     → 对每条新边缘，返回"需要绘制边框层"的矩形 bbox
     → 每条边缘两侧分别是 L 形保留区 vs 挖角填充区，
       边框层应绘制在填充区一侧（向 cut 区域内延伸）

  3. draw_border_layers_on_cut_edges(canvas_arr, edge_bboxes, border_layers)
     → 用 numpy 向量操作，按边框层从外到内依次涂色

本模块不改动任何现有渲染逻辑，仅在 rect_lshape + 池素材时
追加一次边框层绘制。
"""
from __future__ import annotations
import logging
import numpy as np
from PIL import Image

from .corner.detection import (
    _get_border_layers_robust,
)
from .image_cropper_mask import _estimate_outer_background
from .geometry import RectShape

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. 边框层检测（针对池素材图的专用包装）
# ---------------------------------------------------------------------------

def _is_real_border(
    src_img: Image.Image,
    border_layers: list[tuple[tuple[int, int, int], int]],
) -> bool:
    """
    判断检测到的边框层是否是"真实边框"，而非花纹/图案的误检。

    判定条件（全部满足才算真实边框）：
      1. 边缘颜色与中心颜色差异足够大 → 素材确实有边缘色带 vs 中心内容区分
      2. 总边框厚度占较小比例（≤ 30% 的短边）→ 排除大面积图案误检

    Args:
        src_img: 素材图（已适配到画布尺寸）
        border_layers: _get_border_layers_robust 返回的原始列表

    Returns:
        True = 真实边框，False = 应跳过补全
    """
    if not border_layers:
        return False

    w, h = src_img.size
    arr = np.array(src_img, dtype=np.float64)
    H, W = arr.shape[:2]
    if H < 20 or W < 20:
        return False

    # 条件 1：边缘区域 vs 中心区域颜色差异
    edge_band = max(5, min(15, int(min(W, H) * 0.02)))
    edge_mask = np.zeros((H, W), dtype=bool)
    edge_mask[:edge_band, :] = True
    edge_mask[-edge_band:, :] = True
    edge_mask[:, :edge_band] = True
    edge_mask[:, -edge_band:] = True
    center_mask = ~edge_mask

    edge_color = arr[edge_mask].mean(axis=0)
    center_color = arr[center_mask].mean(axis=0)
    edge_center_dist = float(np.linalg.norm(edge_color - center_color))

    # 差异阈值：50（经验值，区分"边框 vs 内容" 和 "整体一致的花纹"）
    COLOR_DIFF_THRESHOLD = 50.0
    if edge_center_dist < COLOR_DIFF_THRESHOLD:
        logger.info(
            "[LShapeBorder] 边缘-中心色差 %.1f < %.1f，判定为非边框图案",
            edge_center_dist, COLOR_DIFF_THRESHOLD,
        )
        return False

    # 条件 2：总边框厚度不超过短边的 30%
    total_t = sum(t for _, t in border_layers)
    min_dim = min(W, H)
    THICKNESS_RATIO = 0.30
    if total_t > min_dim * THICKNESS_RATIO:
        logger.info(
            "[LShapeBorder] 总边框厚度 %d px > %.0f%% × %d px，判定为图案误检",
            total_t, THICKNESS_RATIO * 100, min_dim,
        )
        return False

    return True


def _filter_content_layers(
    src_img: Image.Image,
    border_layers: list[tuple[tuple[int, int, int], int]],
) -> list[tuple[tuple[int, int, int], int]]:
    """
    过滤掉那些颜色与素材中心内容过于接近的边框层。

    例如克罗印花素材检测出 [brown(46), cream(12), black(12)] 三层，
    其中 cream(245,235,220) 是素材底色——不是边框，而是边框之间的内容过渡。
    此函数利用素材中心区域的平均颜色来识别并剔除这类"伪边框层"。

    过滤条件：border_color 与 center_color 的色差 < 25 → 丢弃。
    """
    if len(border_layers) <= 1:
        return border_layers

    arr = np.array(src_img, dtype=np.float64)
    H, W = arr.shape[:2]
    # 中心区域（剔除边缘 10% 后的中间 80%）
    m0, m1 = int(H * 0.1), int(H * 0.9)
    n0, n1 = int(W * 0.1), int(W * 0.9)
    if m1 - m0 < 5 or n1 - n0 < 5:
        return border_layers
    center_color = arr[m0:m1, n0:n1].mean(axis=(0, 1))

    filtered: list[tuple[tuple[int, int, int], int]] = []
    COLOR_MATCH_THRESHOLD = 25.0
    for color, thickness in border_layers:
        dist = float(
            np.linalg.norm(
                np.array(color, dtype=np.float64) - center_color
            )
        )
        if dist < COLOR_MATCH_THRESHOLD:
            logger.info(
                "[LShapeBorder] 过滤内容匹配层 color=%s thickness=%.0f (距中心色 %.1f < %.1f)",
                color, thickness, dist, COLOR_MATCH_THRESHOLD,
            )
        else:
            filtered.append((color, thickness))

    # 过滤后至少保留 1 层，否则放弃
    return filtered


def detect_pool_material_borders(
    src_img: Image.Image,
    bg_color: tuple[int, int, int] = (255, 255, 255),
) -> list[tuple[tuple[int, int, int], int]]:
    """
    从池素材图自动检测边框层（颜色 + 像素厚度）。

    与直接调用 _get_border_layers_robust 的区别：
      a. 先做外背景伪边框过滤（避免大面积浅/纯色背景被误判为边框层）
      b. 增加"真实边框"判定（边缘-中心色差 + 厚度比例），过滤花纹误检
      c. 返回空列表时表示"素材无边框"，调用方应跳过补全

    Returns:
        border_layers: [(color, thickness_px), ...] 从外到内有序
    """
    if src_img is None:
        return []

    border_layers = _get_border_layers_robust(src_img, bg_color)
    if not border_layers:
        return []

    # —— 外背景伪边框过滤 ——
    outer_bg = _estimate_outer_background(src_img)
    first_color, first_t = border_layers[0]
    color_dist = float(
        np.linalg.norm(
            np.array(first_color, dtype=np.float64)
            - np.array(outer_bg, dtype=np.float64)
        )
    )
    w, h = src_img.size
    thickness_threshold = max(30, int(min(w, h) * 0.03))
    outer_bg_mean = float(np.mean(outer_bg))

    if (color_dist < 25.0
            and first_t > thickness_threshold
            and outer_bg_mean > 200):
        # 最外层颜色接近外背景 + 厚度过大 + 外背景是浅色 → 伪边框，移除
        border_layers = border_layers[1:]
        if border_layers:
            logger.info(
                "[LShapeBorder] 移除外背景伪边框，剩余 %d 层",
                len(border_layers),
            )

    # —— 真实边框判定（过滤花纹误检）——
    if not _is_real_border(src_img, border_layers):
        return []

    # —— 内容匹配层过滤（剔除与中心色接近的过渡层，如棕→米→黑 中间的米色）——
    border_layers = _filter_content_layers(src_img, border_layers)
    if not border_layers:
        return []

    return border_layers


# ---------------------------------------------------------------------------
# 2. 几何：计算每条新边缘需要绘制的边框 bbox
# ---------------------------------------------------------------------------

def compute_cut_edge_bboxes(
    outer_rect: RectShape,
    cut_corner: str,
    cut_w: float,
    cut_h: float,
    total_border_thickness_px: float,
) -> list[tuple[float, float, float, float]]:
    """
    返回需要绘制边框层的矩形 bbox 列表 [(x0,y0,x1,y1), ...]。

    L 形挖角产生两条新边缘（1 条水平 + 1 条垂直）：
      - 每条 bbox 的一条边贴在 L 形的新边缘线上，
        向 cut 区域内部延伸 total_border_thickness_px 像素。
      - 注意：不同角落的 bbox "边缘贴在哪一端" 不同，
        draw_border_layers_on_cut_edges 会根据 cut_corner 自行判断。

    返回 bbox 的坐标系与 outer_rect 一致（画布像素坐标）。
    """
    if total_border_thickness_px <= 0.5:
        return []

    bx = total_border_thickness_px  # 向 cut 区延伸的总厚度

    ox, oy = outer_rect.x, outer_rect.y
    oright = outer_rect.right
    obottom = outer_rect.bottom

    edges: list[tuple[float, float, float, float]] = []

    if cut_corner == 'tl':
        # cut 区: x ∈ [ox, ox+cut_w], y ∈ [oy, oy+cut_h]
        # 水平边缘: cut 区底部 y = oy+cut_h（保留区在下，cut 在上 → 向内=向上）
        # 垂直边缘: cut 区右部 x = ox+cut_w（保留区在右，cut 在左 → 向内=向左）
        edge_y = oy + cut_h + 0.5   # 水平边缘线（像素中心 +0.5）
        inner_y = max(oy - 0.5, edge_y - bx)
        edges.append((
            ox - 0.5,          # x0 = cut 区左边界
            inner_y,           # y0 = 向内延伸端
            ox + cut_w + 0.5,  # x1 = cut 区右边界
            edge_y,            # y1 = 边缘线 ✓
        ))
        edge_x = ox + cut_w + 0.5   # 垂直边缘线
        inner_x = max(ox - 0.5, edge_x - bx)
        edges.append((
            inner_x,           # x0 = 向内延伸端
            oy - 0.5,          # y0 = cut 区上边界
            edge_x,            # x1 = 边缘线 ✓
            oy + cut_h + 0.5,  # y1 = cut 区下边界
        ))

    elif cut_corner == 'tr':
        # cut 区: x ∈ [oright-cut_w, oright], y ∈ [oy, oy+cut_h]
        # 水平边缘: cut 区底部 y = oy+cut_h（保留区在下，cut 在上 → 向内=向上）
        # 垂直边缘: cut 区左部 x = oright-cut_w（保留区在左，cut 在右 → 向内=向右）
        edge_y = oy + cut_h + 0.5
        inner_y = max(oy - 0.5, edge_y - bx)
        edges.append((
            oright - cut_w - 0.5,  # x0 = cut 区左边界
            inner_y,               # y0 = 向内延伸端
            oright + 0.5,          # x1 = cut 区右边界
            edge_y,                # y1 = 边缘线 ✓
        ))
        edge_x = oright - cut_w + 0.5   # 边缘在 cut 区左边界，向内=向右
        inner_x = min(oright + 0.5, edge_x + bx)
        edges.append((
            edge_x,              # x0 = 边缘线（左）
            oy - 0.5,            # y0 = cut 区上边界
            inner_x,             # x1 = 向内延伸端（右）
            oy + cut_h + 0.5,    # y1 = cut 区下边界
        ))

    elif cut_corner == 'bl':
        # cut 区: x ∈ [ox, ox+cut_w], y ∈ [obottom-cut_h, obottom]
        # 水平边缘: cut 区顶部 y = obottom-cut_h（保留区在上，cut 在下 → 向内=向下）
        # 垂直边缘: cut 区右部 x = ox+cut_w（保留区在右，cut 在左 → 向内=向左）
        edge_y = obottom - cut_h - 0.5
        inner_y = min(obottom + 0.5, edge_y + bx)  # 向内=向下，y1 = edge_y + bx ✓
        edges.append((
            ox - 0.5,
            edge_y,              # y0 = 边缘线
            ox + cut_w + 0.5,
            inner_y,             # y1 = 向内延伸端
        ))
        edge_x = ox + cut_w + 0.5
        inner_x = max(ox - 0.5, edge_x - bx)
        edges.append((
            inner_x,
            obottom - cut_h - 0.5,
            edge_x,
            obottom + 0.5,
        ))

    elif cut_corner == 'br':
        # cut 区: x ∈ [oright-cut_w, oright], y ∈ [obottom-cut_h, obottom]
        # 水平边缘: cut 区顶部 y = obottom-cut_h（保留区在上，cut 在下 → 向内=向下）
        # 垂直边缘: cut 区左部 x = oright-cut_w（保留区在左，cut 在右 → 向内=向右）
        edge_y = obottom - cut_h - 0.5
        inner_y = min(obottom + 0.5, edge_y + bx)
        edges.append((
            oright - cut_w - 0.5,
            edge_y,              # y0 = 边缘线
            oright + 0.5,
            inner_y,             # y1 = 向内延伸端
        ))
        edge_x = oright - cut_w + 0.5
        inner_x = min(oright + 0.5, edge_x + bx)
        edges.append((
            edge_x,              # x0 = 边缘线
            obottom - cut_h - 0.5,
            inner_x,             # x1 = 向内延伸端
            obottom + 0.5,
        ))

    # 裁剪到画布内
    edges_clipped: list[tuple[float, float, float, float]] = []
    for (x0, y0, x1, y1) in edges:
        x0 = max(0.0, x0)
        y0 = max(0.0, y0)
        if x1 <= x0 + 0.5 or y1 <= y0 + 0.5:
            continue
        edges_clipped.append((x0, y0, x1, y1))

    return edges_clipped


# ---------------------------------------------------------------------------
# 3. 绘制边框层到画布
# ---------------------------------------------------------------------------

def draw_border_layers_on_cut_edges(
    canvas_arr: np.ndarray,
    edge_bboxes: list[tuple[float, float, float, float]],
    border_layers: list[tuple[tuple[int, int, int], float]],
    cut_corner: str = 'tl',
) -> None:
    """
    向 canvas_arr（H×W×3 uint8）写入边框层。

    每条 edge_bbox 代表边框绘制区域矩形。bbox 的"边缘侧"贴在 L 形的新边缘线上，
    延伸向 cut 区内部 total_t 像素。

    边框层绘制顺序：
      从边缘侧（bbox 的一条边）向内依次铺层，
      最外层 border_layers[0] 贴在边缘线上，
      内层向内铺开。

    cut_corner 用于确定每条 bbox 的边缘侧：
      - 'tl'/'tr' 水平边缘 → cut 在边缘上方 → edge = y1（较大 y）
      - 'bl'/'br' 水平边缘 → cut 在边缘下方 → edge = y0（较小 y）
      - 'tl'/'bl' 垂直边缘 → cut 在边缘左侧 → edge = x1（较大 x）
      - 'tr'/'br' 垂直边缘 → cut 在边缘右侧 → edge = x0（较小 x）
    """
    if not edge_bboxes or not border_layers:
        return

    H, W = canvas_arr.shape[:2]

    for (ex0, ey0, ex1, ey1) in edge_bboxes:
        x0 = int(round(ex0))
        y0 = int(round(ey0))
        x1 = int(round(ex1))
        y1 = int(round(ey1))

        # 裁剪到画布
        x0 = max(0, min(x0, W - 1))
        y0 = max(0, min(y0, H - 1))
        x1 = max(x0 + 1, min(x1, W))
        y1 = max(y0 + 1, min(y1, H))

        edge_w = x1 - x0
        edge_h = y1 - y0
        if edge_w <= 0 or edge_h <= 0:
            continue

        # 判断边缘是水平还是垂直为主（长边方向）
        is_horizontal = edge_w >= edge_h

        # 根据 cut_corner 确定边缘在 bbox 的哪一端 + 向内方向
        if is_horizontal:
            # 水平 bbox：分层沿 y 方向
            # tl/tr → cut 在边缘上方 → edge_y=y1, inward=-y (向下减小)
            # bl/br → cut 在边缘下方 → edge_y=y0, inward=+y (向下增大)
            if cut_corner in ('tl', 'tr'):
                edge_y = y1
                inward_sign = -1  # 向内 = y 减小
            else:
                edge_y = y0
                inward_sign = +1  # 向内 = y 增大
            remaining = abs(y1 - y0)
            cur_paint_end = edge_y  # 已绘制的内层边界，从边缘开始
            for color, thickness in border_layers:
                t = max(1, int(round(thickness)))
                if remaining <= 0:
                    break
                actual_t = min(t, remaining)
                # 从 cur_paint_end 向内 actual_t 像素
                cur_paint_start = cur_paint_end + inward_sign * actual_t
                lo, hi = sorted([cur_paint_start, cur_paint_end])
                canvas_arr[lo:hi, x0:x1, :] = color
                cur_paint_end = cur_paint_start
                remaining -= actual_t
        else:
            # 垂直 bbox：分层沿 x 方向
            # tl/bl → cut 在边缘左侧 → edge_x=x1, inward=-x (向左)
            # tr/br → cut 在边缘右侧 → edge_x=x0, inward=+x (向右)
            if cut_corner in ('tl', 'bl'):
                edge_x = x1
                inward_sign = -1
            else:
                edge_x = x0
                inward_sign = +1
            remaining = abs(x1 - x0)
            cur_paint_end = edge_x
            for color, thickness in border_layers:
                t = max(1, int(round(thickness)))
                if remaining <= 0:
                    break
                actual_t = min(t, remaining)
                cur_paint_start = cur_paint_end + inward_sign * actual_t
                lo, hi = sorted([cur_paint_start, cur_paint_end])
                canvas_arr[y0:y1, lo:hi, :] = color
                cur_paint_end = cur_paint_start
                remaining -= actual_t


# ---------------------------------------------------------------------------
# 4. 对外统一入口：补全 L 形挖角处的素材边框
# ---------------------------------------------------------------------------

def apply_lshape_border_completion(
    canvas_arr: np.ndarray,
    material_img: Image.Image,
    outer_rect: RectShape,
    cut_corner: str,
    cut_w_px: float,
    cut_h_px: float,
    dpi: int = 150,
    bg_color: tuple[int, int, int] = (255, 255, 255),
    # [2026-09-03 新增] 使用原始素材 + 缩放因子做更精确的边框检测
    src_material_img: Image.Image | None = None,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> bool:
    """
    L 形挖角边框补全：检测素材图边框层 → 计算 cut 区域新边缘的 bbox → 绘制。

    Args:
        canvas_arr: 画布 numpy 数组 (H, W, 3) uint8，会被原地修改
        material_img: 已适配到画布尺寸的素材 PIL Image（主要用于参考）
        outer_rect: 外框矩形像素坐标（通常 = inner_rect_px）
        cut_corner: 'tl'|'tr'|'bl'|'br'
        cut_w_px, cut_h_px: 挖角尺寸（像素）
        dpi: 当前渲染 DPI
        bg_color: 背景色参考（用于边框检测过滤；白色为佳）
        src_material_img: 原始素材图（未拉伸），优先用它检测边框以获得更精确结果
        scale_x, scale_y: 原始图到画布的缩放比例（canvas_w / src_w）

    Returns:
        bool: 是否成功补全（素材无边框时返回 False，不影响后续渲染）
    """
    # Step 1: 边框检测
    # 优先使用原始素材图（未拉伸）→ 更精确，不会受 adapt_pool_material 畸变影响
    detect_img = src_material_img if src_material_img is not None else material_img
    if detect_img is None:
        return False

    border_layers_src = detect_pool_material_borders(detect_img, bg_color)
    if not border_layers_src:
        logger.info("[LShapeBorder] 素材无边框层，跳过补全")
        return False

    # Step 2: 把检测到的厚度按 scale 因子换算到画布坐标系
    # 对于非等比缩放（scale_x ≠ scale_y），按"厚度沿 x 方向用 scale_x、y 方向用 scale_y"处理
    # 实际场景中 scale_x ≈ scale_y（素材 AR 与画布 AR 通常匹配），取平均做统一换算即可
    scale_avg = (scale_x + scale_y) / 2.0 if (scale_x > 0 and scale_y > 0) else 1.0
    scale_avg = max(scale_avg, 0.1)  # 防御性下限

    border_layers_canvas: list[tuple[tuple[int, int, int], float]] = []
    for color, thickness in border_layers_src:
        border_layers_canvas.append((color, float(thickness) * scale_avg))

    total_t = sum(t for _, t in border_layers_canvas)
    logger.info(
        "[LShapeBorder] 检测到 %d 层边框（scale=%.2f×），画布总厚度 %.1f px: %s",
        len(border_layers_canvas), scale_avg, total_t, border_layers_canvas,
    )

    # Step 3: 计算 cut 边缘的绘制 bbox
    edge_bboxes = compute_cut_edge_bboxes(
        outer_rect, cut_corner, cut_w_px, cut_h_px, total_t,
    )
    if not edge_bboxes:
        return False

    # Step 4: 绘制
    draw_border_layers_on_cut_edges(
        canvas_arr, edge_bboxes, border_layers_canvas, cut_corner=cut_corner,
    )
    return True
