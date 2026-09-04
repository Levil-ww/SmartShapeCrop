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
    # [V13 集成 2026-09-04 新增] 手动边框参数覆盖（None=自动检测/原有路径）
    manual_edge_px: int | None = None,
    manual_band_px: int | None = None,
    manual_band_color: tuple[int, int, int] | None = None,
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
        manual_edge_px: [V13] 手动指定黑描边宽（素材原始像素坐标）。
                       None=自动；与 manual_band_*/color 任一非 None 即走 V13 路径。
        manual_band_px: [V13] 手动指定主色带宽（0=无主带，None=自动）。
        manual_band_color: [V13] 手动指定主色带 RGB（band>0 时必须）。

    Returns:
        bool: 是否成功补全（素材无边框时返回 False，不影响后续渲染）

    路径选择（向后兼容）：
        - 全部 manual_* = None（纯自动）：
            * [2026-09-04] 先试 V13 detect_border_v13（对黑描边+主带结构可靠）
            * V13 返回 None → 回退原有 detect_pool_material_borders 路径
            * 两者都无 → 返回 False 跳过补全
        - 任一 manual_* 非 None：
            * 跳过自动检测，直接用 V13 路径
            * 缺失项用 detect_border_v13 自动补齐
            * patch_lshape_cut 沿切边在保留区一侧补「黑描边+主带」
              （黑带沿 L 形轮廓连续 + 主带直角衔接 + 杜绝白色竖线）
    """
    # ===== [V13 集成] 手动覆盖路径：任一 manual_* 非 None → 走 V13 路径 =====
    manual_active = (
        manual_edge_px is not None
        or manual_band_px is not None
        or manual_band_color is not None
    )
    if manual_active:
        return _apply_v13_path(
            canvas_arr=canvas_arr,
            material_img=material_img,
            outer_rect=outer_rect,
            cut_corner=cut_corner,
            cut_w_px=cut_w_px,
            cut_h_px=cut_h_px,
            src_material_img=src_material_img,
            scale_x=scale_x,
            scale_y=scale_y,
            manual_edge_px=manual_edge_px,
            manual_band_px=manual_band_px,
            manual_band_color=manual_band_color,
        )

    # Step 1: 边框检测
    # 优先使用原始素材图（未拉伸）→ 更精确，不会受 adapt_pool_material 畸变影响
    detect_img = src_material_img if src_material_img is not None else material_img
    if detect_img is None:
        return False

    # ===== [2026-09-04 检测路由调整] V13 优先 =====
    # 根因：原 detect_pool_material_borders 对「黑描边 + 主色带」结构素材
    # （克罗印花/凯特玫瑰等）会误检出 1 层 ~2px 近黑细线，并被 _is_real_border
    # 误判为「真边框」放行 → 只在缺口边缘画一条几乎不可见的细线
    # （即用户看到的「缺边框」）；而原 V13 兜底仅在「检测返回空」时触发，
    # 遇到这种假阳性根本不会执行。
    # 修复：自动路径改为「先试 V13 detect_border_v13」——它用 1D 段分析，
    # 对黑描边+主带结构更可靠；V13 检测不到黑描边（返回 None）时再回退旧的
    # detect_pool_material_borders（对纯黑框等结构保持兼容）。
    v13 = detect_border_v13(detect_img)
    if v13 is not None:
        logger.info(
            "[LShapeBorder] V13 检测命中: edge=%dpx band=%dpx color=%s，走 V13 路径",
            v13[0], v13[1], v13[2],
        )
        return _apply_v13_path(
            canvas_arr=canvas_arr,
            material_img=material_img,
            outer_rect=outer_rect,
            cut_corner=cut_corner,
            cut_w_px=cut_w_px,
            cut_h_px=cut_h_px,
            src_material_img=src_material_img,
            scale_x=scale_x,
            scale_y=scale_y,
            manual_edge_px=v13[0],
            manual_band_px=v13[1],
            manual_band_color=v13[2],
        )

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


# ===========================================================================
# [V13 集成 2026-09-04] V13 路径辅助函数
# ===========================================================================

def _apply_v13_path(
    *,
    canvas_arr: np.ndarray,
    material_img: Image.Image,
    outer_rect: RectShape,
    cut_corner: str,
    cut_w_px: float,
    cut_h_px: float,
    src_material_img: Image.Image | None,
    scale_x: float,
    scale_y: float,
    manual_edge_px: int | None,
    manual_band_px: int | None,
    manual_band_color: tuple[int, int, int] | None,
) -> bool:
    """V13 路径：手动覆盖或原检测兜底时使用。

    流程：
      1. 用 manual_* 优先，缺失项用 detect_border_v13 自动补齐
      2. 厚度按 scale 换算到画布坐标系（预览 LOD 时 scale 相应变小，自动适配）
      3. patch_lshape_cut（确认 V13 交付模块 lshape_border_module.py 移植）：
         沿两条切边在**保留区一侧**补齐「黑描边 + 主色带」，
         内凹角 max(dx,dy) 几何分层，黑带沿 L 形轮廓连续。
    """
    detect_img = src_material_img if src_material_img is not None else material_img
    if detect_img is None:
        return False

    # —— 1) 解析边框参数（手动优先，缺失用 V13 自动检测）——
    edge_src: int | None = manual_edge_px
    band_src: int | None = manual_band_px
    color_src: tuple[int, int, int] | None = manual_band_color

    need_auto = (edge_src is None) or (band_src is None) or (
        band_src > 0 and color_src is None
    )
    if need_auto:
        v13 = detect_border_v13(detect_img)
        if v13 is not None:
            if edge_src is None:
                edge_src = v13[0]
            if band_src is None:
                band_src = v13[1]
            if color_src is None and band_src and band_src > 0:
                color_src = v13[2]
        # V13 检测失败时保留手动值（若仅有 manual_band_color 缺失则用黑色兜底）

    # 兜底：edge 仍 None → 视为无边框，跳过
    if edge_src is None or edge_src <= 0:
        logger.info("[LShapeBorder/V13] edge 缺失或 ≤0，跳过 V13 路径")
        return False
    # band 缺失视为 0（无主带）；color 缺失但 band>0 → 用黑色兜底（视觉上接近"无主带"）
    if band_src is None:
        band_src = 0
    if band_src > 0 and color_src is None:
        color_src = (0, 0, 0)
    elif band_src == 0:
        color_src = (0, 0, 0)  # 占位，不会被绘制

    # —— 2) 厚度换算到画布坐标系（预览 LOD 时 scale 相应变小，自动适配）——
    scale_avg = (scale_x + scale_y) / 2.0 if (scale_x > 0 and scale_y > 0) else 1.0
    scale_avg = max(scale_avg, 0.1)

    edge_canvas = float(edge_src) * scale_avg
    band_canvas = float(band_src) * scale_avg

    logger.info(
        "[LShapeBorder/V13] edge=%.1fpx band=%.1fpx color=%s (scale=%.2f×)",
        edge_canvas, band_canvas, color_src, scale_avg,
    )

    # —— 3) V13 patch 补边（确认 V13 交付模块 lshape_border_module.py 移植）——
    # 绘制方向：沿两条切边在**保留区一侧**补齐「黑描边 + 主色带」：
    #   - 垂直切边：黑带全高（与素材顶边黑描边衔接），主带从 edge 起不压顶边黑
    #   - 水平切边：黑带全宽（覆盖外缘，杜绝白缝），主带避外缘 edge
    #   - 内凹角：max(dx, dy) 几何分层，黑带沿 L 形轮廓连续
    # 在 outer_rect 子图上操作（缺口贴子图角落，与渲染 mask 同源），
    # 翻转到 tr 由 patch_lshape_cut 内部处理（图案方向不变）。
    H, W = canvas_arr.shape[:2]
    ox = max(0, int(round(outer_rect.x)))
    oy = max(0, int(round(outer_rect.y)))
    oright = min(W, int(round(outer_rect.right)))
    obottom = min(H, int(round(outer_rect.bottom)))
    ow_r, oh_r = oright - ox, obottom - oy
    if ow_r < 4 or oh_r < 4:
        logger.info("[LShapeBorder/V13] outer_rect 子图过小，跳过 patch")
        return False

    # 缺口矩形（子图像素坐标，贴 outer_rect 对应角 —— 与渲染 mask 同源；
    # 取 int 保证 x0+cw == 子图宽 的贴角校验精确成立）
    cw_r = int(round(min(float(cut_w_px), float(ow_r))))
    ch_r = int(round(min(float(cut_h_px), float(oh_r))))
    if cw_r < 1 or ch_r < 1:
        logger.info("[LShapeBorder/V13] 挖角尺寸过小，跳过 patch")
        return False
    if cut_corner == 'tl':
        bx0, by0 = 0, 0
    elif cut_corner == 'tr':
        bx0, by0 = ow_r - cw_r, 0
    elif cut_corner == 'bl':
        bx0, by0 = 0, oh_r - ch_r
    else:  # 'br'
        bx0, by0 = ow_r - cw_r, oh_r - ch_r

    sub = canvas_arr[oy:obottom, ox:oright, :]
    try:
        patched = patch_lshape_cut(
            sub, cut_corner, bx0, by0, cw_r, ch_r,
            max(1, int(round(edge_canvas))),
            max(0, int(round(band_canvas))),
            color_src,
        )
    except ValueError as e:
        logger.info("[LShapeBorder/V13] patch 跳过: %s", e)
        return False
    canvas_arr[oy:obottom, ox:oright, :] = patched
    return True


# ===========================================================================
# [V13 集成 2026-09-04] —— Additive V13 L 形挖角功能（不修改上面任何原有函数）
#
# 来源：E:\ima-测试L型挖角输出\确认V13\lshape_crop.py（v13 固化版）
#       E:\ima-测试L型挖角输出\确认V13\lshape_border_module.py（交付版 patch 移植）
# 集成方式：纯加性新增
#   - detect_border_v13(): V13 的 1D 段分析边框检测，对"黑描边 + 主色带"结构素材
#     （克罗印花/凯特玫瑰/巴洛克之星/中古雨林等）比 _get_border_layers_robust 更可靠
#   - detect_border(): 交付版公开 API（失败抛 ValueError）
#   - patch_lshape_cut() / _fill_vertical_horizontal(): 交付版切边补齐，
#     沿切边在**保留区一侧**补「黑描边 + 主色带」
#     （黑带沿 L 形轮廓连续 + 主带直角衔接 + 杜绝白色竖线）
#
# 触发条件（在 apply_lshape_border_completion 内部路由）：
#   - [2026-09-04] 自动路径 V13 优先：detect_border_v13 命中 → V13 patch 路径
#   - V13 返回 None → 回退原有 detect_pool_material_borders 路径（向后兼容）
#   - 手动覆盖启用（design.lshape_manual_* 任一非 None）→ 直接走 V13 路径
#
# 向后兼容：design 默认无 lshape_manual_* 字段 → 自动检测路由，无边框素材不受影响。
# ===========================================================================


def _v13_segv(arr: np.ndarray, tol: int = 12):
    """V13 的 1D 段切分：相邻像素 L1 距离 > tol 即断段。

    返回 [(start_idx, end_idx, representative_color_tuple), ...]。
    representative_color 取该段起始像素的 RGB（与 V13 原版一致）。
    """
    out = []
    prev = tuple(int(v) for v in arr[0])
    s = 0
    for i in range(1, len(arr)):
        c = tuple(int(v) for v in arr[i])
        if abs(c[0] - prev[0]) + abs(c[1] - prev[1]) + abs(c[2] - prev[2]) > tol:
            out.append((s, i - 1, prev))
            s, prev = i, c
    out.append((s, len(arr) - 1, prev))
    return out


def _v13_pick(segs):
    """V13 段选择：最外暗色段=黑描边，其后第一个 ≥40px 彩色段=主色带。

    返回 (black_width, band_width, band_color) 或 None。
    band_width=0 表示只有黑边无主带（如 庄园秘境/戏蝶/中古大花 纯黑宽边素材）。
    """
    MIN_BAND = 40
    i = 0
    while i < len(segs) and (segs[i][1] - segs[i][0] + 1) <= 2:
        i += 1
    if i >= len(segs):
        return None
    if max(segs[i][2]) < 90:
        bw = segs[i][1] - segs[i][0] + 1
        i += 1
    else:
        return None
    for j in range(i, len(segs)):
        w = segs[j][1] - segs[j][0] + 1
        c = segs[j][2]
        if w >= MIN_BAND and max(c) >= 60 and max(c) <= 235:
            return (bw, w, c)
    return (bw, 0, (0, 0, 0))


def detect_border_v13(src_img: Image.Image) -> tuple[int, int, tuple[int, int, int]] | None:
    """V13 自动边框检测：返回 (黑描边宽 px, 主带宽 px, 主带色 RGB) 或 None。

    规则（V13 原版）：
      - 检测以素材顶边（中心列）为准；左剖面仅校正黑边宽度
      - 最外暗色段 = 黑描边（宽度自动）
      - 黑描边后第一个 ≥40px 的彩色段（排除近白/近黑的花纹底色）= 主色带
      - 黑边后只有窄浅色过渡（≤30px）→ 视为无主带（BW=0）

    Args:
        src_img: 原始素材 PIL Image（建议未拉伸，避免 adapt_pool_material 畸变）

    Returns:
        (edge_px, band_px, (r, g, b)) 或 None（未检测到黑描边）
    """
    if src_img is None:
        return None
    arr = np.array(src_img.convert('RGB') if src_img.mode != 'RGB' else src_img)
    H, W = arr.shape[:2]
    if H < 20 or W < 20:
        return None

    # 顶边：取上 1/3 中心列；左边：取左 1/3 中心行（仅用于校正黑边宽）
    pt = _v13_pick(_v13_segv(arr[:min(H // 3, 400), W // 2]))
    pl = _v13_pick(_v13_segv(arr[H // 2, :min(W // 3, 400)]))
    if pt is None:
        return None
    edge = round((pt[0] + (pl[0] if pl else pt[0])) / 2)
    return (edge, pt[1], pt[2])


def detect_border(img: Image.Image) -> tuple[int, int, tuple[int, int, int]]:
    """V13 交付版边框检测公开 API（lshape_border_module.py 同名函数）。

    返回 (黑描边宽px, 主带宽px, 主带色RGB)；
    未检测到最外黑描边时抛 ValueError（与交付模块行为一致）。
    项目内部路由请用 detect_border_v13（返回 None 便于回退）。
    """
    v13 = detect_border_v13(img)
    if v13 is None:
        raise ValueError('未检测到最外黑描边')
    return v13


# ---------------------------------------------------------------------------
# [V13 交付版移植 2026-09-04] 切边补齐 —— 沿切边在保留区一侧补「黑描边+主带」
# 来源：E:\ima-测试L型挖角输出\确认V13\lshape_border_module.py（原样移植）
# ---------------------------------------------------------------------------

def _fill_vertical_horizontal(b, xc, yc, edge, band, color, black):
    """
    在"缺口贴右上角"的画布 b 上补齐切边边框。
    xc = 垂直切边 x(缺口左边界); yc = 水平切边 y(缺口下边界);
    产品位于 x<xc 与 y>yc。缺口区(x>=xc 且 y<yc)保持原样(已是背景)。
    """
    H, W = b.shape[:2]
    T = edge + band
    # 垂直切边: 黑带全高(与素材顶边黑描边衔接), 主带从 y=edge 起(不压顶边黑)
    b[0:yc, xc-edge:xc] = black
    if band > 0:
        b[edge:yc, xc-T:xc-edge] = color
    # 水平切边: 黑带全宽(覆盖右缘, 防白缝), 主带避右缘
    b[yc:yc+edge, xc:W] = black
    if band > 0:
        b[yc+edge:yc+T, xc:W-edge] = color
    # 内凹角 (xc, yc): 距角点几何 L 分层, 黑带/主带沿角直角连续
    for yy in range(yc, min(H, yc+T)):
        for xx in range(xc-T, xc):
            d = max(xc-xx, yy-yc)
            if d <= edge:
                b[yy, xx] = black
            elif band > 0 and d <= T:
                b[yy, xx] = color


def patch_lshape_cut(canvas, corner, x0, y0, cw, ch,
                     edge, band, color, black=(0, 0, 0)):
    """
    在已裁切的最终画布上补齐缺口切边边框。

    canvas: np.ndarray (H,W,3) 最终渲染结果(缺口区已是洞色/白/透明底)
    corner : 'tl'|'tr'|'bl'|'br'
    x0,y0,cw,ch : 缺口矩形(画布像素), 与渲染 mask 同源!
    edge/band/color : detect_border 结果或手动指定
    返回补边后的数组(不修改入参)。
    """
    a = np.asarray(canvas).copy()
    if a.dtype != np.uint8:
        a = a.astype(np.uint8)
    H, W = a.shape[:2]
    # 翻转使缺口贴到右上角(flip 对称, 处理完翻回, 图案方向不变)
    flipx = corner in ('tl', 'bl')
    flipy = corner in ('bl', 'br')
    b = a
    if flipx:
        b = np.fliplr(b)
    if flipy:
        b = np.flipud(b)
    # 缺口矩形在翻转图中的新位置(应贴右上)
    nx0 = (W - x0 - cw) if flipx else x0
    ny0 = (H - y0 - ch) if flipy else y0
    # 翻转后缺口右/下边界
    nx1 = nx0 + cw
    ny1 = ny0 + ch
    # 垂直切边 = 缺口左边界 nx0; 水平切边 = 缺口下边界 ny1
    if not (nx1 == b.shape[1] and ny0 == 0):
        raise ValueError('缺口矩形不在画布角落, 请检查 x0/y0/cw/ch 与翻转角的一致性')
    _fill_vertical_horizontal(b, nx0, ny1, edge, band, color, black)
    # 翻回
    if flipy:
        b = np.flipud(b)
    if flipx:
        b = np.fliplr(b)
    return b
