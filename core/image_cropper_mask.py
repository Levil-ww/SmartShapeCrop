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
    _angle_in_corner_sector,
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
    protect_content: dict[str, bool] | bool = False,
    bg_color: tuple[int, int, int] = (255, 255, 255),
    content_ref_arr: np.ndarray | None = None,
) -> Image.Image:
    """
    构建多层边框动态圆角遮罩。[内容区保护模式]

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
        # [Fix 绕接] 0°/360° 绕接处纳入 sector，避免右边缘直边边框被误切
        base_cut = _angle_in_corner_sector(angle, corner_key, tol=2.0) & (dist > r)

        # [Fix 深色弧形缺口] border_zone 无条件定义，用于后续 ring_region 限制
        T_plus = max(raw_depth + 2, 4)
        if corner_key == 'tl':
            border_zone = (xx <= T_plus) | (yy <= T_plus)
        elif corner_key == 'tr':
            border_zone = (((w - 1) - xx) <= T_plus) | (yy <= T_plus)
        elif corner_key == 'bl':
            border_zone = (xx <= T_plus) | (((h - 1) - yy) <= T_plus)
        else:
            border_zone = (((w - 1) - xx) <= T_plus) | (((h - 1) - yy) <= T_plus)

        if corner_protect and raw_depth > 0:
            # [Fix 白色竖线] inner_cut 只裁切边框环带 [r - raw_depth - 2, r]，
            # 不裁切内容区（dist < r - raw_depth - 2）。
            # 旧逻辑 inner_cut = (dist <= r) & border_zone 切了 border_zone 内 dist<=r
            # 的全部像素——包括内容区——这些像素在 ring_region 的保护范围之外，
            # 被 mask 切白后形成紧贴直边的白色竖线（庄园秘境、有细边框的产品）。
            border_ring_inner = max(0.0, float(r) - float(raw_depth) - 2.0)
            inner_cut = (dist <= r) & (dist >= border_ring_inner) & border_zone
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
                ring_region = _angle_in_corner_sector(angle, corner_key, tol=2.0) & \
                              (dist >= ring_inner_bound) & (dist <= float(r) + 1.5)
                # [Fix 深色弧形缺口] dist > r 的像素必须在 border_zone 内才保护。
                # 弧线外侧（dist > r 且不在 border_zone）必须被 base_cut 切为白色，
                # 不能保留原图深色边框色形成弧形缺口线（蔓生花、素锦）。
                # border_zone 内的直边边框像素仍需保护，保证边框连续。
                ring_region = ring_region & (~(dist > float(r)) | border_zone)
                # [Fix 0°/90° 接缝] 弧线内侧（dist <= r）的边框条带像素一律保护，
                # 避免角落扇区角度边界把右/底直边边框条带误切出白点。
                # 弧线外侧仍由上方条件约束：必须在 sector 内且位于 border_zone。
                ring_region = ring_region | (
                    border_zone & (dist >= ring_inner_bound) & (dist <= float(r))
                )
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

        # [Fix 2026-09-12 N-P1-03] 嵌套矩形恢复段已删除（死代码清理）。
        # 原因：调用方 image_cropper_border.py 的 corner_protect_map 对所有圆角角
        #   恒为 True（r_px > 0），故保护模式恒启用，原 B 段 90+ 行逻辑永不被执行。
        #   统一走保护模式：只裁剪边框条带，内部图案保持直角。

        mask_arr[y1:y2, x1:x2] = mask_local

    mask = Image.fromarray(mask_arr, mode='L')
    return mask

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
        # [Fix 绕接] 统一用 _angle_in_corner_sector 处理 0°/360° 绕接
        ring_inner = float(r) - float(paint_depth)
        # [Fix 白色竖线] tol 与 sector_render 保持一致（2.0），避免边界像素
        # 在 validity_mask 中为 0 却被 redraw 判定覆盖，从而被误清成背景白色。
        paint_region = _angle_in_corner_sector(angle, corner_key, tol=2.0) & \
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

    # 计算 content_ref（降采样避免全图 float64；2亿像素≈4.8GB）
    MAX_SIDE = 200
    w_src, h_src = src_img.size
    if max(w_src, h_src) > MAX_SIDE:
        scale = MAX_SIDE / max(w_src, h_src)
        img_small = src_img.resize(
            (max(1, int(w_src * scale)), max(1, int(h_src * scale))),
            Image.BILINEAR,
        )
        src_f = np.array(img_small, dtype=np.float64)
    else:
        src_f = np.array(src_img, dtype=np.float64)
    h_f, w_f = src_f.shape[:2]
    x_start, x_end = int(w_f * 0.15), int(w_f * 0.85)
    y_start, y_end = int(h_f * 0.15), int(h_f * 0.85)
    STEPS = 21
    xs = np.linspace(x_start, x_end, STEPS, dtype=np.int64).clip(0, w_f - 1)
    ys = np.linspace(y_start, y_end, STEPS, dtype=np.int64).clip(0, h_f - 1)
    gx, gy = np.meshgrid(xs, ys)
    samples = src_f[gy, gx, :].reshape(-1, 3)
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

    validity_mask_arr = None
    if validity_mask is not None:
        validity_mask_arr = np.array(validity_mask, dtype=bool)

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

        # [Fix 白色竖线] 统一用 _angle_in_corner_sector 处理 0°/360° 绕接
        valid_angle = _angle_in_corner_sector(angle, corner_key, tol=2.0)
        valid_region = valid_angle & (dist <= r + 2.0)

        if validity_mask_arr is not None:
            local_validity = validity_mask_arr[roi_y1:roi_y2, roi_x1:roi_x2]
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
                # [Fix 2026-09-12 P1-01] 钳制索引到 [0,h-1]×[0,w-1]，防止小图负索引越界
                # 并修复 src_arr 悬空引用（N-P1-02 降采样后变量名已改为 src_f/arr）
                straight_arr[:, 0] = np.clip(straight_arr[:, 0], 0, h - 1)
                straight_arr[:, 1] = np.clip(straight_arr[:, 1], 0, w - 1)
                straight_colors = arr[straight_arr[:, 0], straight_arr[:, 1], :].astype(np.float64)
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
