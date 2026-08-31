"""图片裁剪服务 —— 掩码构建 / 圆角扇形内容分析层（由 image_cropper.py 拆分而来，facade 模式）。

原文件 core/image_cropper.py 为编排层 facade，
本模块只包含 掩码构建 / 圆角扇形内容分析层 相关的实现，逻辑与原文件完全一致。
"""

from __future__ import annotations
import os
import logging
from dataclasses import dataclass
import numpy as np
from PIL import Image, ImageDraw

from .image_ops import load_image_rgb, fit_image_to_rect
from .psd.loader import is_psd_file, load_psd_flattened
from .corner.algorithm import (
    CORNER_ANGLES,
    carve_corner_on_mask,
    get_corner_square,
    get_corner_pieslice_bbox,
)
# 从 corner 子包导入检测与重绘函数（与 image_cropper.py 原头部一致）
from .corner.detection import (
    _BORDER_SCAN_STEP,
    _BORDER_COLOR_DIFF_THRESHOLD,
    _BORDER_MIN_GAP_PX,
    _BORDER_MAX_LAYERS,
    _EDGE_IGNORE_PX,
    _detect_border_layers,
    _get_border_layers_robust,
    _scan_edge_boundaries,
    detect_nested_rect_layers,
    classify_gap_layers,
    get_solid_border_colors,
    GAP_MAX_THICKNESS_GLOBAL,
    GAP_NEIGHBOR_MIN_DIST_GLOBAL,
    GAP_BG_DIST_GLOBAL,
    GAP_CONTENT_DIST_GLOBAL,
    SENTINEL_OUTER_DARK_MAX_RGB,
)
from .corner.sector_render import (
    _build_border_sector_mask,
    _sample_border_color,
    _redraw_border_on_corner,
)
from .config import (
    DEFAULT_BORDER_WIDTH_CM,
    BORDER_TOTAL_DEPTH_CM,
    DEFAULT_DPI,
    DEFAULT_BG_COLOR,
    DEFAULT_CROP_MODE,
    DEFAULT_MAX_CROP_RATIO,
)

logger = logging.getLogger(__name__)


def _build_multi_layer_corner_mask(
    w: int, h: int,
    corners_px: dict[str, int],
    border_layers: list[tuple[tuple[int, int, int], int]],
    nested_rects: list[tuple[int, int, int, int]] | None = None,
    protect_content: dict[str, bool] | bool = False,
    bg_color: tuple[int, int, int] = (255, 255, 255),
    content_ref_arr: np.ndarray | None = None,
) -> Image.Image:
    """
    构建多层边框动态圆角遮罩。[升级：嵌套矩形层感知 + 内容区保护]

    [关键不变量 S1/S4 + L1]
      每层嵌套矩形边框 k 有独立的有效圆角半径：
        R_eff(k, corner) = max(0, R_total - D(k, corner))
      其中 D(k, corner) 是该层矩形距图像外边缘的距离：
        - TL: D = max(rect.x1, rect.y1)
        - TR: D = max(w-1-rect.x2, rect.y1)
        - BL: D = max(rect.x1, h-1-rect.y2)
        - BR: D = max(w-1-rect.x2, h-1-rect.y2)

    [内容区保护模式]
      当 protect_content=True 时，只有位于边框条带内的扇形外部像素会被裁切，
      内容区（花纹、图案）保持原图直角形式。边框条带定义为两个边框条的并集：
        tl: 顶边框条(y<=T) ∪ 左边框条(x<=T)
        tr: 顶边框条(y<=T) ∪ 右边框条(x>=W-1-T)
        bl: 底边框条(y>=H-1-T) ∪ 左边框条(x<=T)
        br: 底边框条(y>=H-1-T) ∪ 右边框条(x>=W-1-T)
      其中 T = raw_depth + 4px 容差。

    Args:
        w, h: 图像宽高（像素）
        corners_px: 四角圆角半径（像素）
        border_layers: 边框层列表 [(color, thickness_px), ...]
        nested_rects: 可选，预检测的嵌套矩形（从 detect_nested_rect_layers 得到），
                      None 则在内部自动检测
        protect_content: 是否保护内容区（仅裁切边框区域），默认 False
        bg_color: 背景色 tuple(r,g,b)，用于 classify_gap_layers 间隙判定
        content_ref_arr: 内容参考色 np.ndarray(3,) float64，用于 classify_gap_layers；
                         None 则使用默认近似值

    Returns:
        L 模式遮罩（255=保留原图，0=裁掉/背景色）
    """
    valid_corners = {k: v for k, v in corners_px.items() if v > 0}
    if not valid_corners:
        return Image.new('L', (w, h), 255)

    # ---- 检测嵌套矩形层 ----
    if nested_rects is None:
        try:
            from .corner.detection import detect_nested_rect_layers
            # 构造临时假图用于检测（基于 mask_arr 无法直接检测，需要真实图像）
            # 注意：此函数在调用方 apply_border_only_corners 中已传入 img，
            # 但此处没有，所以先 fallback 为空列表；调用方应优先传入预检测结果。
            nested_rects = []  # 调用方会通过新参数传入
        except Exception as e:
            logger.warning(f"嵌套矩形层检测导入失败: {e}")
            nested_rects = []

    raw_depth = sum(t for _, t in border_layers) if border_layers else 0

    # [Fix INV-1 2026-08-27] 使用调用方传入的实际 bg_color 和 content_ref_arr
    #
    # 修复前：硬编码 DEFAULT_BG_COLOR=(255,255,255) 和 content_ref_arr=None，
    #   与 sector_render._redraw_border_on_corner() 的调用参数不一致，
    #   导致 classify_gap_layers 判定结果反转（间隙层↔实心层），
    #   出现"边框线有的多了有的不准确"的现象。
    #
    # 修复后：使用调用方传入的 bg_color 和 content_ref_arr，
    #   确保 _build_multi_layer_corner_mask 与 _redraw_border_on_corner 判定一致。
    is_gap_layer = classify_gap_layers(border_layers, bg_color=bg_color, content_ref_arr=content_ref_arr)
    gap_layer_indices = {i for i, ig in enumerate(is_gap_layer) if ig}

    # [Fix v5] 计算累积深度（包含间隙层），用于精确计算径向位置
    cumulative_depths = [0]
    for _, t in border_layers:
        cumulative_depths.append(cumulative_depths[-1] + t)
    total_border_depth = cumulative_depths[-1]

    # 计算实心边框层的累积厚度（排除间隙层）
    solid_border_depths = [0]
    for i, (_, t) in enumerate(border_layers):
        if i in gap_layer_indices:
            solid_border_depths.append(solid_border_depths[-1])  # 间隙层不增加厚度
        else:
            solid_border_depths.append(solid_border_depths[-1] + t)
    solid_total_depth = solid_border_depths[-1]

    mask_arr = np.ones((h, w), dtype=np.uint8) * 255

    for corner_key, r in valid_corners.items():
        if r <= 0:
            continue

        r = min(r, max(1, min(w, h) // 2))
        if r <= 0:
            continue

        if corner_key == 'tl':
            cx, cy = r, r
        elif corner_key == 'tr':
            cx, cy = w - r, r
        elif corner_key == 'bl':
            cx, cy = r, h - r
        else:  # br
            cx, cy = w - r, h - r

        x1 = max(0, cx - r)
        y1 = max(0, cy - r)
        x2 = min(w, cx + r)
        y2 = min(h, cy + r)

        if x2 <= x1 or y2 <= y1:
            continue

        yy, xx = np.mgrid[y1:y2, x1:x2].astype(np.float64)
        dx = xx - float(cx)
        dy = yy - float(cy)
        dist = np.sqrt(dx * dx + dy * dy)
        angle = np.degrees(np.arctan2(dy, dx))
        angle = np.mod(angle, 360.0)
        ang_min, ang_max = CORNER_ANGLES[corner_key]

        # ===== [A) 基础 outer L-cut + 智能 ring 保护（仅实心边框）] =====
        # Step 1: 标准 L 形裁切（外层半径 r）
        # 当 protect_content=True 时，只在边框条带内裁切（内容区保持直角）
        # 当 protect_content=False 时，裁掉整个扇形外部（正常圆角）
        # 支持 per-corner dict 或 全局 bool
        if isinstance(protect_content, dict):
            corner_protect = protect_content.get(corner_key, False)
        else:
            corner_protect = protect_content

        # 基础裁切区域：扇形外部（dist > r）
        # [Fix INV-5] 使用 <= ang_max 而非 < ang_max，确保边界像素被裁切，
        # 防止花漾之约等案例出现白色三角伪影
        base_cut = (angle >= ang_min) & (angle <= ang_max) & (dist > r)

        if corner_protect and raw_depth > 0:
            # 保护模式：只在边框条带内裁切，但弧线外侧（dist > r）必须无条件裁切。
            # [Fix 2026-08-27] 旧逻辑把弧线外侧也限制在 border_zone 内，
            # 导致非边框条带区的弧线外侧保留了原图背景色，形成背景色弧形缺口
            # （中古雨林黑色弧线、塞纳时光米色弧线）。
            # 修正后：弧线外侧无条件裁切；弧线内侧只在 border_zone 内裁切，
            # 确保内容区（花纹/图案）保持直角。
            T_plus = raw_depth + 2
            if corner_key == 'tl':
                border_zone = (xx <= T_plus) | (yy <= T_plus)
            elif corner_key == 'tr':
                border_zone = (((w - 1) - xx) <= T_plus) | (yy <= T_plus)
            elif corner_key == 'bl':
                border_zone = (xx <= T_plus) | (((h - 1) - yy) <= T_plus)
            else:
                border_zone = (((w - 1) - xx) <= T_plus) | (((h - 1) - yy) <= T_plus)
            inner_cut = (dist <= r) & border_zone
            outer_cut = base_cut | inner_cut
        else:
            outer_cut = base_cut

        # Step 2: 构建实心边框保护区域（仅保护实心边框层，间隙层必须裁切）
        # [Fix v5] 核心修复：重建 ring_region 逻辑，确保间隙区域完全不被保护。
        #
        # 旧逻辑缺陷：
        #   ring_region 基于 solid_border_depths 计算 inner_bound，但间隙层的
        #   扣除逻辑（gap_protect_removed）在某些情况下失效，导致间隙像素残留。
        #   特别是当 r 较大时，保护范围可能覆盖间隙层。
        #
        # 新逻辑：
        #   1. 计算基础保护区域（从 r - total_border_depth 到 r + 2）
        #   2. 如果间隙层在最外层或最内层，强制从基础保护中扣除
        #   3. 确保间隙区域（gap_regions）的径向范围完全被排除
        if total_border_depth > 0:
            # 基础保护区域的内边界
            # 使用 total_border_depth（所有层厚度）确保覆盖所有边框和间隙
            if r <= total_border_depth * 2.0:
                # r 较小：保护厚度限制在 r 以内
                max_protect = max(0, r - 2)
            else:
                max_protect = total_border_depth + 2  # 总厚度 + 2px 容差

            if max_protect > 0:
                ring_inner_bound = max(0.0, float(r) - float(max_protect))
                # [Fix 2026-08-31] dist 上界改为 r+1.5，与 Step A d_region 对齐。
                # 旧值 r+2 会保护 dist ∈ [r+0.5, r+2] 的像素，但 Step A 的 d_region
                # 只覆盖 dist ≤ r+0.5 → 这些被保护但不被重绘的像素会保持原图颜色，
                # 在图像边缘（上边/左边）形成黑色直角尾巴。
                # 0.5 太小，整数像素在圆弧上的 dist 可偏离精确 R 达 ±1.4px（√2），
                # 所以用 r+1.5 容差：既能覆盖圆弧上所有整数像素，又和 Step A 完全对齐。
                ring_region = (angle >= ang_min) & (angle <= ang_max) & \
                              (dist >= ring_inner_bound) & (dist <= float(r) + 1.5)
            else:
                ring_region = np.zeros_like(base_cut, dtype=bool)

            # [Fix v7] 强制扣除所有间隙层的径向范围
            # 无论间隙层在哪个位置（最外层、中间、最内层），都必须从保护中移除
            # 使用精确的间隙范围（不过度扩展，避免清除内容像素）
            if gap_layer_indices:
                gap_protect_removed = np.zeros_like(ring_region, dtype=bool)
                for g_idx in gap_layer_indices:
                    if g_idx < len(border_layers):
                        g_thickness = border_layers[g_idx][1]
                        # 使用原始 cumulative_depths（包含间隙层）来计算径向位置
                        cum_before = cumulative_depths[g_idx]  # 间隙前的累积深度
                        cum_after = cum_before + g_thickness  # 间隙后的累积深度
                        # 间隙层在圆弧上的径向范围：
                        gap_dist_near = float(r) - float(cum_before)  # 靠近外弧的一侧
                        gap_dist_far = float(r) - float(cum_after)    # 靠近内弧的一侧
                        # 确保方向正确：靠近外弧的 dist 更大
                        if gap_dist_near > gap_dist_far:
                            # [Fix v7] 使用精确范围，不过度扩展
                            # 仅清除间隙层本身，不影响相邻边框和内容
                            gap_mask = ring_region & (dist >= gap_dist_far - 0.5) & (dist <= gap_dist_near + 0.5)
                            gap_protect_removed = gap_protect_removed | gap_mask
                # 将间隙区域从 ring_region 中移除
                ring_region = ring_region & (~gap_protect_removed)
        else:
            ring_region = np.zeros_like(base_cut, dtype=bool)

        outer_cut = outer_cut & (~ring_region)

        mask_local = mask_arr[y1:y2, x1:x2]
        mask_local[outer_cut] = 0

        # ===== [B) 嵌套矩形层感知：逐层恢复被误切区域] =====
        # [Fix 2026-08-17] 保护模式下跳过 nested_rects 处理：
        #   当 corner_protect=True（即 r <= 2*raw_depth）时，只裁剪边框条带，
        #   内部图案完全保持直角。nested_rects 处理可能因检测误差（抗锯齿等）
        #   导致 Dk 计算不准，从而错误地恢复或裁切内部区域。
        #   因此在保护模式下直接跳过此段逻辑，确保内部图案不受影响。
        if corner_protect:
            pass  # 保护模式：内部图案完全保持直角，不进行 nested_rects 处理
        elif nested_rects:
            # [Fix P2] nested_rects 伪层过滤（花野 10 层伪层 → 3 层）。
            # 根因：_scan_edge_boundaries 会把花纹的颜色突变也识别为矩形边界，
            # 导致 rects 层数远超实际边框层数。例如花野实际边框 3 层，
            # 扫描检测出 10 层 → 多出来的 7 层都是花纹区的"假矩形"，
            # 它们的 R_eff 都很大，叠加恢复后把不该恢复的花纹尖角也保留了，
            # 导致花野案例圆角处"多层杂乱"。
            # 修复：如果有 border_layers，rects 最多处理 len(border_layers) 层
            # （最外层的几层才是真实边框），内层的花纹伪矩形直接跳过。
            max_rects_from_layers = max(2, len(border_layers) + 1) if border_layers else 4
            effective_rects = nested_rects[:max(2, min(len(nested_rects), max_rects_from_layers))]

            # [Fix P1 IN_PAD 真正应用] 修复：旧版 IN_PAD=4 定义了但完全没用到
            # （bx1,by1,bx2,by2 全等于 rx1,ry1,rx2,ry2，IN_PAD 形同虚设）。
            # 这会导致内层矩形的恢复区域正好卡在检测边界上，而真实边框可能
            # 比检测 rect 稍宽/稍窄 2-4px（抗锯齿、渐变、扫描步长误差），
            # 结果内层边框的一部分仍被误切 → 婉卉案例内层黑色小弧形缺口。
            # 修复：按注释，把矩形「靠近本角的一端」向中心方向推 IN_PAD 像素，
            # 远端保持不变（远端远离本角，不会被本角的 L-cut 影响）。
            for rect_k in effective_rects[1:]:
                rx1, ry1, rx2, ry2 = rect_k
                # 坐标合法性
                if rx2 - rx1 < 20 or ry2 - ry1 < 20:
                    continue
                # 计算该层矩形到图像外边缘的距离 D_k（本角）
                if corner_key == 'tl':
                    Dk = max(rx1, ry1)
                elif corner_key == 'tr':
                    Dk = max((w - 1) - rx2, ry1)
                elif corner_key == 'bl':
                    Dk = max(rx1, (h - 1) - ry2)
                else:  # br
                    Dk = max((w - 1) - rx2, (h - 1) - ry2)

                # 如果该层矩形已超出边框厚度范围（即属于内部图案/花纹区域），
                # 则强制保持直角，不进行圆角裁剪
                if raw_depth > 0 and Dk > raw_depth:
                    R_eff_k = 0
                else:
                    R_eff_k = max(0, r - int(round(Dk)))
                # 钳制有效半径 <= min(rect_w, rect_h)//2（局部半径不变量）
                local_max = max(1, min(rx2 - rx1, ry2 - ry1) // 2)
                R_eff_k = min(R_eff_k, local_max)

                if R_eff_k >= r:
                    # 此层有效半径与外层相同或更大（理论上不发生），无需恢复
                    continue

                IN_PAD = 4
                # [Fix P1 真正应用 IN_PAD]
                # 朝中心扩张规则：只把矩形的「角近端」向中心推 IN_PAD
                #   tl: 近端 = (rx1, ry1) → 向内 = x+, y+ → rx1+IN_PAD, ry1+IN_PAD
                #   tr: 近端 = (rx2, ry1) → 向内 = x-, y+ → rx2-IN_PAD, ry1+IN_PAD
                #   bl: 近端 = (rx1, ry2) → 向内 = x+, y- → rx1+IN_PAD, ry2-IN_PAD
                #   br: 近端 = (rx2, ry2) → 向内 = x-, y- → rx2-IN_PAD, ry2-IN_PAD
                # 远端不变。
                if corner_key == 'tl':
                    bx1, by1 = rx1 + IN_PAD, ry1 + IN_PAD
                    bx2, by2 = rx2, ry2
                elif corner_key == 'tr':
                    bx1, by1 = rx1, ry1 + IN_PAD
                    bx2, by2 = rx2 - IN_PAD, ry2
                elif corner_key == 'bl':
                    bx1, by1 = rx1 + IN_PAD, ry1
                    bx2, by2 = rx2, ry2 - IN_PAD
                else:  # br
                    bx1, by1 = rx1, ry1
                    bx2, by2 = rx2 - IN_PAD, ry2 - IN_PAD
                # 安全内敛：整体区间 clip 到 [0, W-1] × [0, H-1]
                in_x = (xx >= max(0, bx1)) & (xx <= min(w - 1, bx2))
                in_y = (yy >= max(0, by1)) & (yy <= min(h - 1, by2))
                in_rect_k = in_x & in_y

                # 本层需要恢复的区域（严格不越界）：
                # [Fix INV-5] 使用 <= ang_max 与 base_cut 保持一致，
                # 防止花漾之约等案例在 ang_max 边界出现白色扇形角伪影
                in_ang = (angle >= ang_min) & (angle <= ang_max)
                dist_in_r = (dist > float(R_eff_k)) & (dist <= float(r))  # 严格 <= r，杜绝 > r 的伪保留
                if R_eff_k <= 0:
                    # 本层及内层应保持直角 → dist ∈ [0, r] 的 in_rect_k 像素全恢复
                    restore_k = in_ang & in_rect_k & (dist <= float(r))
                else:
                    # 本层及内层：dist ∈ (R_eff_k, r] 属于被外层误切的区域
                    restore_k = in_ang & in_rect_k & dist_in_r

                # 应用恢复
                if np.any(restore_k):
                    mask_local[restore_k] = 255

        mask_arr[y1:y2, x1:x2] = mask_local

    mask = Image.fromarray(mask_arr, mode='L')
    return mask



def _analyze_corner_sector_content(
    img: Image.Image,
    corner_key: str,
    r: int,
    raw_depth: int,
    bg_color: tuple = (255, 255, 255),
) -> bool:
    """
    分析角落扇形区域是否包含需要保护的内容（花纹、图案等）。

    通过在扇形区域（圆角外侧）采样像素的颜色复杂度来判断：
    - 颜色种类多 / 方差高 → 有实际内容（如森夜私语的叶子花纹）→ 应保护
    - 颜色单一 / 方差低 → 仅为背景色（如安妮森林的纯色角落） → 应完全裁切

    Args:
        img: 原图（RGB）
        corner_key: 角标记 ('tl','tr','bl','br')
        r: 圆角半径（像素）
        raw_depth: 边框总厚度（像素）
        bg_color: 背景色（用于判断"内容"与"背景"的差异）

    Returns:
        True = 该角存在内容，需要保护（只裁边框条带）
        False = 该角为纯色背景，应完全裁掉整个扇形
    """
    w, h = img.size
    r = min(r, max(1, min(w, h) // 2))
    if r <= 0:
        return False

    # 扇形区域中心
    if corner_key == 'tl':
        cx, cy = r, r
    elif corner_key == 'tr':
        cx, cy = w - r, r
    elif corner_key == 'bl':
        cx, cy = r, h - r
    else:
        cx, cy = w - r, h - r

    # 计算扇形区域（L 形 → 两个矩形）
    # tl 角：矩形A = [0, r] x [0, r]（完整的角方块）
    # 扇形 = 角方块内 dist > r 的部分
    # 为了高效分析，我们在扇形区域采样像素

    sample_step = max(2, r // 15)  # 自适应采样步长
    pixels = []

    if corner_key == 'tl':
        for y in range(0, r, sample_step):
            for x in range(0, r, sample_step):
                dx, dy = x - r, y - r
                if dx * dx + dy * dy > r * r:  # dist > r (outside circle)
                    pixels.append(img.getpixel((x, y)))
    elif corner_key == 'tr':
        for y in range(0, r, sample_step):
            for x in range(w - r, w, sample_step):
                dx, dy = x - (w - r), y - r
                if dx * dx + dy * dy > r * r:
                    pixels.append(img.getpixel((x, y)))
    elif corner_key == 'bl':
        for y in range(h - r, h, sample_step):
            for x in range(0, r, sample_step):
                dx, dy = x - r, y - (h - r)
                if dx * dx + dy * dy > r * r:
                    pixels.append(img.getpixel((x, y)))
    else:  # br
        for y in range(h - r, h, sample_step):
            for x in range(w - r, w, sample_step):
                dx, dy = x - (w - r), y - (h - r)
                if dx * dx + dy * dy > r * r:
                    pixels.append(img.getpixel((x, y)))

    if len(pixels) < 10:
        return False

    # 分析颜色复杂度
    pixels_arr = np.array(pixels, dtype=np.float32)

    # 1. 计算背景色相似度：与 bg_color 距离小于30的像素占比
    bg_diff = np.sqrt(np.sum((pixels_arr - np.array(bg_color, dtype=np.float32)) ** 2, axis=1))
    bg_ratio = np.mean(bg_diff < 30)

    # 如果超过 85% 的像素接近背景色 → 认为无内容
    if bg_ratio > 0.85:
        return False

    # 2. 计算唯一颜色数（量化到 20 个 bin）
    quantized = (pixels_arr // 20).astype(np.int32)
    unique_colors = len(set(map(tuple, quantized)))

    # 如果唯一颜色数多 → 有内容（花纹、渐变等）
    if unique_colors >= 8:
        return True

    # 3. 计算像素间方差（高方差 = 有图案细节）
    variance = np.mean(np.var(pixels_arr, axis=0))

    # 高方差 → 有内容
    if variance > 800:
        return True

    return False



def _estimate_outer_background(img: Image.Image, ring_px: int = 5) -> tuple[int, int, int]:
    """
    估算图像最外层背景色。

    从四边最外 ring_px 像素采样并取中位数，得到图像实际的外围背景色。
    该颜色用于区分"产品外背景"与"真正的边框层"，避免把外背景误判为
    需要保留/重绘的边框。
    """
    arr = np.array(img, dtype=np.float64)
    h, w = arr.shape[:2]
    ring = max(1, min(ring_px, min(w, h) // 20))

    top = arr[:ring, :, :]
    bottom = arr[-ring:, :, :]
    left = arr[ring:-ring, :ring, :]
    right = arr[ring:-ring, -ring:, :]

    samples = np.concatenate([
        top.reshape(-1, 3),
        bottom.reshape(-1, 3),
        left.reshape(-1, 3),
        right.reshape(-1, 3),
    ], axis=0)
    if samples.shape[0] == 0:
        return (255, 255, 255)
    return tuple(int(round(v)) for v in np.median(samples, axis=0))



def _corner_sector_has_content(
    img: Image.Image,
    corner_key: str,
    r_px: int,
    border_depth_px: int,
) -> bool:
    """
    判断某个圆角对应的外侧扇形区域（排除边框条带后）是否包含需要保护的内容。

    与旧的 _analyze_corner_sector_content 不同：
      - 使用图像真实外背景色作为参考，而不是输出背景色
      - 只采样"边框条带之外"的像素，避免把边框本身误判为内容
      - 当外侧区域与外背景一致（无内容）时返回 False，允许完整裁切扇形

    返回 True 表示该区域有图案/花纹，需要启用保护模式（只裁边框条带）。
    """
    w, h = img.size
    r = min(r_px, max(1, min(w, h) // 2))
    if r <= 0:
        return False

    outer_bg = np.array(_estimate_outer_background(img), dtype=np.float64)

    if corner_key == 'tl':
        cx, cy = r, r
        x0, y0, x1, y1 = 0, 0, r, r
    elif corner_key == 'tr':
        cx, cy = w - r, r
        x0, y0, x1, y1 = w - r, 0, w, r
    elif corner_key == 'bl':
        cx, cy = r, h - r
        x0, y0, x1, y1 = 0, h - r, r, h
    else:  # br
        cx, cy = w - r, h - r
        x0, y0, x1, y1 = w - r, h - r, w, h

    step = max(2, r // 30)
    # 只采样弧线外侧紧邻区域（窄带），避免深入内容区导致误判
    band = max(12, min(r // 4, 40))
    outer_r = r + band
    tol = max(4, border_depth_px + 4)
    samples: list[tuple[int, int, int]] = []

    for y in range(y0, min(y1 + band, h), step):
        for x in range(x0, min(x1 + band, w), step):
            dx = x - cx
            dy = y - cy
            dist_sq = dx * dx + dy * dy
            # 仅保留弧线外侧紧邻窄带内的像素
            if dist_sq <= r * r or dist_sq > outer_r * outer_r:
                continue
            if corner_key == 'tl':
                d_edge = min(x - x0, y - y0)
            elif corner_key == 'tr':
                d_edge = min((x1 - 1) - x, y - y0)
            elif corner_key == 'bl':
                d_edge = min(x - x0, (y1 - 1) - y)
            else:
                d_edge = min((x1 - 1) - x, (y1 - 1) - y)
            if d_edge <= tol:
                continue
            samples.append(img.getpixel((x, y)))

    if len(samples) < 12:
        return False

    samples_arr = np.array(samples, dtype=np.float64)
    dist_to_bg = np.sqrt(np.sum((samples_arr - outer_bg) ** 2, axis=1))
    non_bg_ratio = float(np.mean(dist_to_bg > 25.0))
    # 提高阈值：窄带内少量噪点不应触发保护模式
    if non_bg_ratio < 0.20:
        return False

    quantized = (samples_arr // 18).astype(np.int32)
    unique_colors = len(set(map(tuple, quantized)))
    if unique_colors >= 6:
        return True

    variance = float(np.mean(np.var(samples_arr, axis=0)))
    return variance > 700



def _build_border_paint_mask(
    w: int, h: int,
    corners_px: dict[str, int],
    border_depth: int,
) -> Image.Image:
    """
    构建边框重绘专用的 validity_mask。

    validity_mask 使用完整边框厚度，确保间隙层也能被重绘逻辑覆盖。
    内层直角保护由 mask (border_zone) 控制，与 validity_mask 无关。

    Args:
        w, h: 图像宽高（像素）
        corners_px: 四角圆角半径（像素）
        border_depth: 边框总厚度（像素）

    Returns:
        L 模式遮罩（255=允许重绘，0=不允许重绘）
    """
    valid_corners = {k: v for k, v in corners_px.items() if v > 0}
    if not valid_corners:
        return Image.new('L', (w, h), 0)

    paint_arr = np.zeros((h, w), dtype=np.uint8)

    for corner_key, r in valid_corners.items():
        if r <= 0:
            continue

        r = min(r, max(1, min(w, h) // 2))
        if r <= 0:
            continue

        # [Fix 边框线自动匹配] validity_mask 使用精确边框厚度 + 少量抗锯齿容差
        # 原 +8px 容差导致 validity_mask 延伸至内容区，可能引发过绘
        # 修复：仅保留 2px 抗锯齿容差，确保边框区与内容区精确分界
        effective_depth = border_depth

        # 计算边框重绘的有效深度（精确边框厚度 + 2px 抗锯齿容差）
        paint_depth = min(effective_depth + 2, r)

        if corner_key == 'tl':
            cx, cy = r, r
        elif corner_key == 'tr':
            cx, cy = w - r, r
        elif corner_key == 'bl':
            cx, cy = r, h - r
        else:  # br
            cx, cy = w - r, h - r

        x1 = max(0, cx - r)
        y1 = max(0, cy - r)
        x2 = min(w, cx + r)
        y2 = min(h, cy + r)

        if x2 <= x1 or y2 <= y1:
            continue

        yy, xx = np.mgrid[y1:y2, x1:x2].astype(np.float64)
        dx = xx - float(cx)
        dy = yy - float(cy)
        dist = np.sqrt(dx * dx + dy * dy)
        angle = np.degrees(np.arctan2(dy, dx))
        angle = np.mod(angle, 360.0)
        ang_min, ang_max = CORNER_ANGLES[corner_key]

        # 在扇形区域内，覆盖从外边缘到 paint_depth 的所有像素
        # 使用完整边框厚度，确保间隙层也能被重绘逻辑覆盖
        # [Fix 图三] 使用 <= ang_max 包含边界像素，确保角落接缝处的间隙像素被清理
        ring_inner = float(r) - float(paint_depth)
        if ang_max == 360:
            paint_region = ((angle >= ang_min) | (angle < 1)) & \
                           (dist >= ring_inner) & (dist <= float(r) + 2.0)
        else:
            paint_region = (angle >= ang_min) & (angle <= ang_max) & \
                           (dist >= ring_inner) & (dist <= float(r) + 2.0)

        paint_local = paint_arr[y1:y2, x1:x2]
        paint_local[paint_region] = 255
        paint_arr[y1:y2, x1:x2] = paint_local

    mask = Image.fromarray(paint_arr, mode='L')
    return mask



def _post_cleanup_gap_regions(
    result_img: Image.Image,
    src_img: Image.Image,
    corners_px: dict[str, int],
    border_layers: list[tuple[tuple[int, int, int], int]],
    validity_mask: Image.Image,
    bg_color: tuple = (255, 255, 255),
) -> None:
    """
    [Fix 塞纳时光米黄弧线 0812v2] 后处理兜底清扫间隙区域。

    这是对 Step A (sector_render._redraw_border_on_corner) 的补充：
    - Step A 通过 gap_regions 检测主动清理间隙区域
    - 本函数通过像素颜色判断被动兜底，确保间隙区域无残留米黄色弧线

    工作原理：
    1. 计算 content_ref（与 sector_render.py 一致的 15%-85% 密集采样中值）
    2. 计算边框层累积深度，确定每个间隙区域的深度区间
    3. 对每个角，在可见扇形区中扫描深度位于间隙区域的像素
    4. 若像素颜色接近 content_ref（间隙色）且不接近任何边框色 → 清空为 bg_color

    安全性保证：
    - 不修改最外层边框（depth=0）和最内层内容（depth≥total_border_depth）
    - 只清理同时满足"间隙色 + 非边框色"条件的像素
    - 白色点状间隙（花漾之约）：已是 bg_color，距离阈值内跳过
    """
    w, h = result_img.size
    arr = np.array(result_img, dtype=np.uint8)

    # 计算 content_ref
    src_arr = np.array(src_img, dtype=np.float64)
    x_start, x_end = int(w * 0.15), int(w * 0.85)
    y_start, y_end = int(h * 0.15), int(h * 0.85)
    STEPS = 21
    xs = np.linspace(x_start, x_end, STEPS, dtype=np.int64).clip(0, w - 1)
    ys = np.linspace(y_start, y_end, STEPS, dtype=np.int64).clip(0, h - 1)
    gx, gy = np.meshgrid(xs, ys)
    samples = src_arr[gy, gx, :].reshape(-1, 3)
    content_ref = np.median(samples, axis=0)
    CONTENT_COLOR_DIST = 60.0  # 放宽阈值：覆盖浅色间隙与内容色的差异
    BORDER_COLOR_DIST = 20.0

    # 边框层累积深度
    cumulative_depths = [0]
    for _, thickness in border_layers:
        cumulative_depths.append(cumulative_depths[-1] + thickness)
    total_border_depth = cumulative_depths[-1]

    # 边框颜色数组
    border_colors_arr = np.array(
        [np.array(c, dtype=np.float64) for c, _ in border_layers]
    )

    # [Smart Gap Check v3] 使用 classify_gap_layers 统一判定
    # 修复前：此函数手写第5套间隙检测逻辑，60行代码与其他4处不一致。
    # 修复后：统一调用 classify_gap_layers，透传 bg_color 和 content_ref，
    #   确保 4 处调用方结果完全一致。
    is_gap_layer_cleanup = classify_gap_layers(border_layers, bg_color=bg_color,
                                                content_ref_arr=content_ref)
    solid_border_colors_arr = np.array(
        [np.array(c, dtype=np.float64) for (c, _), ig in zip(border_layers, is_gap_layer_cleanup) if not ig]
    )

    # 背景色数组
    bg_arr = np.array(bg_color, dtype=np.float64)

    # 计算间隙区域（与 sector_render.py 一致：逐层判定）
    gap_regions = []
    for i, is_gap in enumerate(is_gap_layer_cleanup):
        if is_gap:
            gap_regions.append((cumulative_depths[i], cumulative_depths[i + 1]))

    if not gap_regions:
        return

    for corner_key, r in corners_px.items():
        if r <= 0:
            continue
        r = min(r, max(1, min(w, h) // 2))
        if r <= 0:
            continue

        if corner_key == 'tl':
            cx, cy = r, r
        elif corner_key == 'tr':
            cx, cy = w - r, r
        elif corner_key == 'bl':
            cx, cy = r, h - r
        else:
            cx, cy = w - r, h - r

        roi_x1 = max(0, cx - r)
        roi_y1 = max(0, cy - r)
        roi_x2 = min(w, cx + r + 1)
        roi_y2 = min(h, cy + r + 1)

        if roi_x2 <= roi_x1 or roi_y2 <= roi_y1:
            continue

        yy, xx = np.mgrid[roi_y1:roi_y2, roi_x1:roi_x2].astype(np.float64)
        dx = xx - float(cx)
        dy = yy - float(cy)
        dist = np.sqrt(dx * dx + dy * dy)
        depth = float(r) - dist
        angle = np.degrees(np.arctan2(dy, dx))
        angle = np.mod(angle, 360.0)
        ang_min, ang_max = CORNER_ANGLES[corner_key]

        if ang_max == 360:
            valid_angle = (angle >= ang_min) | (angle < 1)
        else:
            valid_angle = (angle >= ang_min) & (angle <= ang_max)
        valid_region = valid_angle & (dist <= r + 2.0)

        if validity_mask is not None:
            mask_arr = np.array(validity_mask, dtype=bool)
            local_validity = mask_arr[roi_y1:roi_y2, roi_x1:roi_x2]
            valid_region = valid_region & local_validity

        for (gap_start, gap_end) in gap_regions:
            gap_pixels = valid_region & (depth >= float(gap_start)) & (depth < float(gap_end))
            count = np.sum(gap_pixels)
            if count == 0:
                continue

            yy_g, xx_g = np.where(gap_pixels)
            global_y = yy_g + roi_y1
            global_x = xx_g + roi_x1
            pixel_colors = arr[global_y, global_x, :].astype(np.float64)

            # [Smart Gap Check] 区分"均匀间隙"与"装饰间隙"
            # 与 sector_render.py 一致：在直边方向采样间隙层颜色
            # 排除接近 content_ref 的像素后计算标准差
            # [Fix v7] 使用适中阈值，兼顾识别精度
            COLOR_STD_THRESH = 10.0

            # 在直边方向采样间隙层
            straight_samples = []
            if corner_key == 'tl':
                for d in range(gap_start, min(gap_end, gap_start + 20)):
                    for px in range(cx + r + 5, min(cx + r + 55, w)):
                        straight_samples.append((d, px))
                for d in range(gap_start, min(gap_end, gap_start + 20)):
                    for py in range(cy + r + 5, min(cy + r + 55, h)):
                        straight_samples.append((py, d))
            elif corner_key == 'tr':
                for d in range(gap_start, min(gap_end, gap_start + 20)):
                    for px in range(max(0, cx - r - 55), cx - r - 5):
                        straight_samples.append((d, px))
                for d in range(gap_start, min(gap_end, gap_start + 20)):
                    for py in range(cy + r + 5, min(cy + r + 55, h)):
                        straight_samples.append((py, w - 1 - d))
            elif corner_key == 'bl':
                for d in range(gap_start, min(gap_end, gap_start + 20)):
                    for px in range(cx + r + 5, min(cx + r + 55, w)):
                        straight_samples.append((h - 1 - d, px))
                for d in range(gap_start, min(gap_end, gap_start + 20)):
                    for py in range(max(0, cy - r - 55), cy - r - 5):
                        straight_samples.append((py, d))
            else:  # br
                for d in range(gap_start, min(gap_end, gap_start + 20)):
                    for px in range(max(0, cx - r - 55), cx - r - 5):
                        straight_samples.append((h - 1 - d, px))
                for d in range(gap_start, min(gap_end, gap_start + 20)):
                    for py in range(max(0, cy - r - 55), cy - r - 5):
                        straight_samples.append((py, w - 1 - d))

            if len(straight_samples) > 20:
                straight_arr = np.array(straight_samples, dtype=np.int64)
                straight_colors = src_arr[straight_arr[:, 0], straight_arr[:, 1], :]
                dist_to_content_s = np.sqrt(
                    np.sum((straight_colors - content_ref.reshape(1, 3)) ** 2, axis=1)
                )
                gap_only = straight_colors[dist_to_content_s > 25.0]
                if len(gap_only) > 10:
                    straight_std = float(np.mean(np.std(gap_only, axis=0)))
                else:
                    straight_std = 0.0
            else:
                straight_std = 0.0

            if straight_std < COLOR_STD_THRESH:
                # [Fix v7] 均匀间隙: 精准清理间隙色像素
                # 核心不变量INV-1: 间隙像素 → 背景色
                d_bg = np.sqrt(np.sum((pixel_colors - bg_arr.reshape(1, 3)) ** 2, axis=1))
                is_not_bg = d_bg > 5.0

                # 排除接近边框色的像素（使用适中阈值）
                not_border_like = np.ones(count, dtype=bool)
                if len(solid_border_colors_arr) > 0:
                    for bc_arr in solid_border_colors_arr:
                        d_border = np.sqrt(np.sum((pixel_colors - bc_arr.reshape(1, 3)) ** 2, axis=1))
                        not_border_like &= (d_border > 12.0)

                # [Fix v7] 匹配间隙层颜色的像素 → 清除
                # 使用适中阈值，精准识别间隙
                is_gap_color_match = np.zeros(count, dtype=bool)
                for gc_tuple, ig in zip(border_layers, is_gap_layer_cleanup):
                    if not ig:
                        continue
                    gc_arr = np.array(gc_tuple[0], dtype=np.float64)
                    d_to_gc = np.sqrt(np.sum((pixel_colors - gc_arr.reshape(1, 3)) ** 2, axis=1))
                    is_gap_color_match |= (d_to_gc < 25.0)

                # 精准清除条件：
                # 1. 间隙色且不是边框色 → 清除
                # 2. 间隙色且不是背景色 → 清除（冗余但安全）
                # 3. 不是间隙色的内容/装饰 → 保留
                to_clear = is_gap_color_match & is_not_bg & not_border_like

                if np.any(to_clear):
                    clear_y = global_y[to_clear]
                    clear_x = global_x[to_clear]
                    arr[clear_y, clear_x, :] = bg_arr.reshape(1, 3).astype(np.uint8)
            else:
                # [Fix v7] 装饰间隙: 仅清除匹配间隙层颜色的像素
                # 保留所有具有独特颜色的装饰像素
                d_bg = np.sqrt(np.sum((pixel_colors - bg_arr.reshape(1, 3)) ** 2, axis=1))
                is_not_bg = d_bg > 5.0

                # [Fix v7] 匹配间隙层颜色的像素 → 清除
                matches_gap_color = np.zeros(count, dtype=bool)
                for gc_tuple, ig in zip(border_layers, is_gap_layer_cleanup):
                    if not ig:
                        continue
                    gc_arr = np.array(gc_tuple[0], dtype=np.float64)
                    d_to_gc = np.sqrt(np.sum((pixel_colors - gc_arr.reshape(1, 3)) ** 2, axis=1))
                    matches_gap_color |= (d_to_gc < 25.0)

                not_border_like = np.ones(count, dtype=bool)
                if len(solid_border_colors_arr) > 0:
                    for bc_arr in solid_border_colors_arr:
                        d_border = np.sqrt(np.sum((pixel_colors - bc_arr.reshape(1, 3)) ** 2, axis=1))
                        not_border_like &= (d_border > 12.0)

                # [Fix v7] 精准清除：间隙色且非边框色 → 清除
                # 所有其他像素（装饰、内容、花纹）→ 保留
                to_clear = matches_gap_color & is_not_bg & not_border_like

                if np.any(to_clear):
                    clear_y = global_y[to_clear]
                    clear_x = global_x[to_clear]
                    arr[clear_y, clear_x, :] = bg_arr.reshape(1, 3).astype(np.uint8)
                # 具有独特装饰颜色的像素 → 保留
                # （文字、花纹等非间隙色元素）

    result_img.paste(Image.fromarray(arr, 'RGB'))



def _clear_inner_arc_to_bg(
    result_img: Image.Image,
    corners_px: dict[str, int],
    outermost_border_thickness: int,
    validity_mask: Image.Image,
    bg_color: tuple = (255, 255, 255),
) -> None:
    """
    清除每个角弧内区域（最外层边框以内）为背景色。

    这确保圆角弧线上只有最外层边框线条可见，内层间隙、装饰、花纹等
    全部被清除为背景色，与 Photoshop 人工效果一致（图二）。

    算法：
      对每个角，在弧线区域内，将距离圆心 < (R - T_outermost) 的像素
      全部设为 bg_color。T_outermost 是最外层边框厚度。

    Args:
        result_img: 结果图（原地修改）
        corners_px: 四角圆角半径（像素）
        outermost_border_thickness: 最外层边框厚度（像素）
        validity_mask: 有效性遮罩（只清理 mask 允许的区域）
        bg_color: 背景色
    """
    w, h = result_img.size
    arr = np.array(result_img, dtype=np.uint8)

    for corner_key, r in corners_px.items():
        if r <= 0:
            continue
        r = min(r, max(1, min(w, h) // 2))
        if r <= 0:
            continue

        if corner_key == 'tl':
            cx, cy = r, r
        elif corner_key == 'tr':
            cx, cy = w - r, r
        elif corner_key == 'bl':
            cx, cy = r, h - r
        else:
            cx, cy = w - r, h - r

        inner_r = max(0, r - outermost_border_thickness)

        roi_x1 = max(0, cx - r)
        roi_y1 = max(0, cy - r)
        roi_x2 = min(w, cx + r + 1)
        roi_y2 = min(h, cy + r + 1)

        if roi_x2 <= roi_x1 or roi_y2 <= roi_y1:
            continue

        yy, xx = np.mgrid[roi_y1:roi_y2, roi_x1:roi_x2].astype(np.float64)
        dx = xx - float(cx)
        dy = yy - float(cy)
        dist = np.sqrt(dx * dx + dy * dy)
        angle = np.degrees(np.arctan2(dy, dx))
        angle = np.mod(angle, 360.0)

        ang_min, ang_max = CORNER_ANGLES[corner_key]

        if ang_max == 360:
            valid_angle = (angle >= ang_min) | (angle < 1)
        else:
            valid_angle = (angle >= ang_min) & (angle <= ang_max)

        clear_region = valid_angle & (dist <= float(inner_r))

        if validity_mask is not None:
            mask_arr = np.array(validity_mask, dtype=bool)
            local_validity = mask_arr[roi_y1:roi_y2, roi_x1:roi_x2]
            clear_region = clear_region & local_validity

        if np.any(clear_region):
            yy_c, xx_c = np.where(clear_region)
            global_y = yy_c + roi_y1
            global_x = xx_c + roi_x1
            arr[global_y, global_x, :] = np.array(bg_color, dtype=np.uint8).reshape(1, 3)

    result_img.paste(Image.fromarray(arr, 'RGB'))

