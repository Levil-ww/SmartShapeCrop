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

    L 形挖角产生两条新边缘：
      - 一条是 cut 矩形的某条边（水平或垂直），位于 cut 区域的边界
      - 边框层应向 cut 区域内部延伸 total_border_thickness_px 像素

    返回 bbox 的坐标系与 outer_rect 一致（画布像素坐标）。
    """
    if total_border_thickness_px <= 0.5:
        return []

    bx = total_border_thickness_px  # 向 cut 区延伸的总厚度

    ox, oy = outer_rect.x, outer_rect.y
    ow, oh = outer_rect.w, outer_rect.h
    oright = outer_rect.right
    obottom = outer_rect.bottom

    edges: list[tuple[float, float, float, float]] = []

    if cut_corner == 'br':
        # cut 区: x ∈ [oright - cut_w, oright], y ∈ [obottom - cut_h, obottom]
        # 新边缘 1（水平）: y = obottom - cut_h, x ∈ [oright - cut_w, oright]
        #   边框向下（+y）延伸到 cut 区内部
        y_top = obottom - cut_h
        edges.append((
            oright - cut_w - 0.5,          # x0: cut 区左边界（含 L 形拐角点）
            y_top - 0.5,                   # y0: 边缘线位置（像素中心）
            oright + 0.5,                  # x1: cut 区右边界
            min(obottom + 0.5, y_top + bx + 0.5),  # y1: 向下延伸到 cut 区
        ))
        # 新边缘 2（垂直）: x = oright - cut_w, y ∈ [obottom - cut_h, obottom]
        #   边框向右（+x）延伸到 cut 区内部
        x_left = oright - cut_w
        edges.append((
            x_left - 0.5,                  # x0: 边缘线位置
            obottom - cut_h - 0.5,         # y0: cut 区上边界
            min(oright + 0.5, x_left + bx + 0.5),  # x1: 向右延伸
            obottom + 0.5,                 # y1: cut 区下边界
        ))

    elif cut_corner == 'tl':
        # cut 区: x ∈ [ox, ox + cut_w], y ∈ [oy, oy + cut_h]
        # 新边缘 1（水平）: y = oy + cut_h, x ∈ [ox, ox + cut_w]
        #   边框向上（-y）延伸？不，边缘在 cut 区底部，边框应向 cut 区（上方）延伸 → +y 方向不对
        #   cut 区在角落 (ox, oy)，这条水平边是 cut 区的底边，方向从左到右
        #   边框应向上延伸（进入 cut 区内部）
        y_bottom = oy + cut_h
        edges.append((
            ox - 0.5,                      # x0: cut 区左边界
            max(oy - 0.5, y_bottom - bx - 0.5),   # y0: 向上延伸
            ox + cut_w + 0.5,              # x1: cut 区右边界
            y_bottom + 0.5,                # y1: 边缘线位置
        ))
        # 新边缘 2（垂直）: x = ox + cut_w, y ∈ [oy, oy + cut_h]
        #   边框向左（-x）延伸（进入 cut 区内部）
        x_right = ox + cut_w
        edges.append((
            max(ox - 0.5, x_right - bx - 0.5),   # x0: 向左延伸
            oy - 0.5,                      # y0: cut 区上边界
            x_right + 0.5,                 # x1: 边缘线位置
            oy + cut_h + 0.5,              # y1: cut 区下边界
        ))

    elif cut_corner == 'tr':
        # cut 区: x ∈ [oright - cut_w, oright], y ∈ [oy, oy + cut_h]
        # 新边缘 1（水平）: y = oy + cut_h, x ∈ [oright - cut_w, oright]
        #   边框向上延伸（进入 cut 区内部，cut 区在角落上方）
        y_bottom = oy + cut_h
        edges.append((
            oright - cut_w - 0.5,          # x0: cut 区左边界
            max(oy - 0.5, y_bottom - bx - 0.5),   # y0: 向上延伸
            oright + 0.5,                  # x1: cut 区右边界
            y_bottom + 0.5,                # y1: 边缘线位置
        ))
        # 新边缘 2（垂直）: x = oright - cut_w, y ∈ [oy, oy + cut_h]
        #   边框向右延伸（进入 cut 区内部）
        x_left = oright - cut_w
        edges.append((
            x_left - 0.5,                  # x0: 边缘线位置
            oy - 0.5,                      # y0: cut 区上边界
            min(oright + 0.5, x_left + bx + 0.5),  # x1: 向右延伸
            oy + cut_h + 0.5,              # y1: cut 区下边界
        ))

    elif cut_corner == 'bl':
        # cut 区: x ∈ [ox, ox + cut_w], y ∈ [obottom - cut_h, obottom]
        # 新边缘 1（水平）: y = obottom - cut_h, x ∈ [ox, ox + cut_w]
        #   边框向下延伸（进入 cut 区内部）
        y_top = obottom - cut_h
        edges.append((
            ox - 0.5,                      # x0: cut 区左边界
            y_top - 0.5,                   # y0: 边缘线位置
            ox + cut_w + 0.5,              # x1: cut 区右边界
            min(obottom + 0.5, y_top + bx + 0.5),  # y1: 向下延伸
        ))
        # 新边缘 2（垂直）: x = ox + cut_w, y ∈ [obottom - cut_h, obottom]
        #   边框向左延伸（进入 cut 区内部）
        x_right = ox + cut_w
        edges.append((
            max(ox - 0.5, x_right - bx - 0.5),   # x0: 向左延伸
            obottom - cut_h - 0.5,         # y0: cut 区上边界
            x_right + 0.5,                 # x1: 边缘线位置
            obottom + 0.5,                 # y1: cut 区下边界
        ))

    # 裁剪到画布内（由调用方传入完整 outer_rect 已保证在画布内，
    # 但 bx 延伸可能超界）
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
    border_layers: list[tuple[tuple[int, int, int], int]],
) -> None:
    """
    向 canvas_arr（H×W×3 uint8）写入边框层。

    每条 edge_bbox 代表一个"边框绘制区域"矩形。边框层按顺序从外到内
    依次绘制：最外层颜色贴在边缘线上，内层向内填充。

    实现：对每条 edge 矩形，按 layer thickness 依次填充。
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

        # 绘制各层：从外到内
        # 注意：边框层从外到内有序，最外层应贴在"边缘线"一侧
        # 对于水平边缘（长方形 wider than tall）：
        #   - cut_corner in {br, bl}: 边缘在 y 较小处，边框向 +y 方向延伸
        #     最外层 border_layers[0] 在 y=y0, 内层在 y=y0+t1+t2...
        #   - cut_corner in {tl, tr}: 边缘在 y 较大处，边框向 -y 方向延伸
        #     最外层 border_layers[0] 在 y=y1, 内层在 y=y1-t1-t2...
        # 对于垂直边缘同理。
        #
        # 为保持简洁统一：我们从最外层开始填充，每一层占用 thickness_px，
        # 按"从 edge 的一侧向内"的顺序依次填入。
        # 由于 edge_bbox 本身就是"从边缘线到 cut 区内部"的矩形，
        # 我们直接把整个 bbox 区域按层切分即可。

        if is_horizontal:
            # 水平长条：沿 y 方向分层
            # edge_bbox 的 y 范围就是从边缘线到 cut 区内部
            remaining_h = edge_h
            cur_y = y0
            for color, thickness in border_layers:
                t = max(1, int(thickness))
                if remaining_h <= 0:
                    break
                actual_t = min(t, remaining_h)
                canvas_arr[cur_y: cur_y + actual_t, x0:x1, :] = color
                cur_y += actual_t
                remaining_h -= actual_t
        else:
            # 垂直长条：沿 x 方向分层
            remaining_w = edge_w
            cur_x = x0
            for color, thickness in border_layers:
                t = max(1, int(thickness))
                if remaining_w <= 0:
                    break
                actual_t = min(t, remaining_w)
                canvas_arr[y0:y1, cur_x: cur_x + actual_t, :] = color
                cur_x += actual_t
                remaining_w -= actual_t


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
) -> bool:
    """
    L 形挖角边框补全：检测素材图边框层 → 计算 cut 区域新边缘的 bbox → 绘制。

    Args:
        canvas_arr: 画布 numpy 数组 (H, W, 3) uint8，会被原地修改
        material_img: 加载后的池素材 PIL Image（adapt_pool_material 之前的原图）
        outer_rect: outer 矩形像素坐标
        cut_corner: 'tl'|'tr'|'bl'|'br'
        cut_w_px, cut_h_px: 挖角尺寸（像素）
        dpi: 当前渲染 DPI
        bg_color: 背景色参考（用于边框检测过滤）

    Returns:
        bool: 是否成功补全（素材无边框时返回 False，不影响后续渲染）
    """
    if material_img is None:
        return False

    # Step 1: 检测边框层
    border_layers = detect_pool_material_borders(material_img, bg_color)
    if not border_layers:
        logger.info("[LShapeBorder] 素材无边框层，跳过补全")
        return False

    # Step 2: 计算总边框厚度（像素）
    total_t = sum(t for _, t in border_layers)
    logger.info(
        "[LShapeBorder] 检测到 %d 层边框，总厚度 %.1f px: %s",
        len(border_layers), total_t, border_layers,
    )

    # Step 3: 计算 cut 边缘的绘制 bbox
    edge_bboxes = compute_cut_edge_bboxes(
        outer_rect, cut_corner, cut_w_px, cut_h_px, total_t,
    )
    if not edge_bboxes:
        return False

    # Step 4: 绘制
    draw_border_layers_on_cut_edges(canvas_arr, edge_bboxes, border_layers)
    return True
