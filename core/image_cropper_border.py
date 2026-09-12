"""图片裁剪服务 —— 边框重绘 / 边框角处理层（由 image_cropper.py 拆分而来，facade 模式）。

原文件 core/image_cropper.py 为编排层 facade，
本模块只包含 边框重绘 / 边框角处理层 相关的实现，逻辑与原文件完全一致。
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

from .image_cropper_mask import _build_border_paint_mask
from .image_cropper_mask import _build_multi_layer_corner_mask
from .image_cropper_mask import _estimate_outer_background
from .image_cropper_mask import _post_cleanup_gap_regions


_DEFAULT_BORDER_WIDTH_CM = DEFAULT_BORDER_WIDTH_CM



def _redraw_outer_border_on_corners(
    result_img: Image.Image,
    src_img: Image.Image,
    corners_px: dict[str, int],
    border_layers: list[tuple[tuple[int, int, int], int]],
    validity_mask: Image.Image,
    bg_color: tuple = (255, 255, 255),
    skip_outside_arc: bool = False,
    only_outermost: bool = False,
) -> None:
    """
    圆角最外轮廓细边的安全补绘（V1.0 风格简化版）。

    简化策略：
    - 保留 V1.0 的 _edge_sample 直边采样特性（正确处理多层边框）
    - 简化安全判定逻辑，直接绘制边框颜色到目标区域
    - 确保边框厚度与原图一致
    - only_outermost=True 时只补绘最外层边框，避免把内层装饰/间隙带进圆角
    """
    if not corners_px:
        return

    w, h = result_img.size
    arr = np.array(result_img, dtype=np.uint8)
    src_arr = np.array(src_img, dtype=np.uint8)
    mask_arr = np.array(validity_mask, dtype=np.uint8) if validity_mask is not None else None

    OUTER_BAND = 5

    for corner_key, r in corners_px.items():
        if r <= 0:
            continue
        r = min(r, max(1, min(w, h) // 2))
        if r <= 0:
            continue

        # 圆心
        if corner_key == 'tl':
            cx, cy = r, r
        elif corner_key == 'tr':
            cx, cy = w - r, r
        elif corner_key == 'bl':
            cx, cy = r, h - r
        else:
            cx, cy = w - r, h - r

        outer_px = r + 2
        inner_px = max(0, r - OUTER_BAND)
        x1 = max(0, cx - outer_px)
        y1 = max(0, cy - outer_px)
        x2 = min(w, cx + outer_px + 1)
        y2 = min(h, cy + outer_px + 1)

        if x2 <= x1 or y2 <= y1:
            continue

        yy, xx = np.mgrid[y1:y2, x1:x2].astype(np.float64)
        dx = xx - float(cx)
        dy = yy - float(cy)
        dist = np.sqrt(dx * dx + dy * dy)
        angle = np.degrees(np.arctan2(dy, dx))
        angle = np.mod(angle, 360.0)
        ang_min, ang_max = CORNER_ANGLES[corner_key]

        # 只在角度范围 + 外轮廓薄层 + validity_mask 允许区域
        # [Fix 白色竖线] 使用 _angle_in_corner_sector 处理 0°/360° 绕接，
        # 避免 BR/TR 角在右/上边缘接缝处漏补形成白线。
        in_angle = _angle_in_corner_sector(angle, corner_key, tol=2.0)
        in_band = (dist >= float(inner_px)) & (dist <= float(r) + 1.5)

        # [Fix 非保护模式] Skip outside_arc pixels (dist > r) when requested
        if skip_outside_arc:
            in_band = (dist >= float(r) - float(OUTER_BAND)) & (dist <= float(r))

        if mask_arr is not None:
            local_valid = mask_arr[y1:y2, x1:x2] > 0
            should = in_angle & in_band & local_valid
        else:
            should = in_angle & in_band

        if not np.any(should):
            continue

        ys, xs = np.where(should)
        gy = ys + y1
        gx = xs + x1

        # depth_v: distance from arc boundary (positive = inside arc)
        depth_v = float(r) - dist[ys, xs]
        d_int_v = np.clip(np.round(depth_v).astype(np.int32), 0, OUTER_BAND + 1)

        # === V1.0 风格保留：从直边采样边框颜色 ===
        # 这是 V1.0 的重要特性，能正确处理多层边框
        def _edge_sample(ck, depth_arr, n):
            cols = []
            for edge_k in range(2):
                if ck == 'tl':
                    if edge_k == 0:  # top
                        sy = np.clip(depth_arr, 0, h - 1)
                        xm = np.linspace(w * 0.25, w * 0.75, 5, dtype=np.int64).clip(0, w - 1)
                        for xi in xm:
                            cols.append(src_arr[np.clip(sy, 0, h - 1), np.full_like(sy, xi), :])
                    else:  # left
                        sx = np.clip(depth_arr, 0, w - 1)
                        ym = np.linspace(h * 0.25, h * 0.75, 5, dtype=np.int64).clip(0, h - 1)
                        for yi in ym:
                            cols.append(src_arr[np.full_like(sx, yi), np.clip(sx, 0, w - 1), :])
                elif ck == 'tr':
                    if edge_k == 0:  # top
                        sy = np.clip(depth_arr, 0, h - 1)
                        xm = np.linspace(w * 0.25, w * 0.75, 5, dtype=np.int64).clip(0, w - 1)
                        for xi in xm:
                            cols.append(src_arr[np.clip(sy, 0, h - 1), np.full_like(sy, xi), :])
                    else:  # right
                        sx = np.clip(w - 1 - depth_arr, 0, w - 1)
                        ym = np.linspace(h * 0.25, h * 0.75, 5, dtype=np.int64).clip(0, h - 1)
                        for yi in ym:
                            cols.append(src_arr[np.full_like(sx, yi), np.clip(sx, 0, w - 1), :])
                elif ck == 'bl':
                    if edge_k == 0:  # left
                        sx = np.clip(depth_arr, 0, w - 1)
                        ym = np.linspace(h * 0.25, h * 0.75, 5, dtype=np.int64).clip(0, h - 1)
                        for yi in ym:
                            cols.append(src_arr[np.full_like(sx, yi), np.clip(sx, 0, w - 1), :])
                    else:  # bottom
                        sy = np.clip(h - 1 - depth_arr, 0, h - 1)
                        xm = np.linspace(w * 0.25, w * 0.75, 5, dtype=np.int64).clip(0, w - 1)
                        for xi in xm:
                            cols.append(src_arr[np.clip(sy, 0, h - 1), np.full_like(sy, xi), :])
                else:  # br
                    if edge_k == 0:  # bottom
                        sy = np.clip(h - 1 - depth_arr, 0, h - 1)
                        xm = np.linspace(w * 0.25, w * 0.75, 5, dtype=np.int64).clip(0, w - 1)
                        for xi in xm:
                            cols.append(src_arr[np.clip(sy, 0, h - 1), np.full_like(sy, xi), :])
                    else:  # right
                        sx = np.clip(w - 1 - depth_arr, 0, w - 1)
                        ym = np.linspace(h * 0.25, h * 0.75, 5, dtype=np.int64).clip(0, h - 1)
                        for yi in ym:
                            cols.append(src_arr[np.full_like(sx, yi), np.clip(sx, 0, w - 1), :])
            if not cols:
                return np.tile(np.array([[0, 0, 0]], dtype=np.uint8), (n, 1))
            stacked = np.stack(cols, axis=0)
            med = np.median(stacked.reshape(-1, stacked.shape[-2], 3), axis=0)
            return med.astype(np.uint8)

        N = len(gy)
        rep_colors = _edge_sample(corner_key, d_int_v, N)

        # === V1.0 风格简化：直接绘制边框颜色 ===
        # 简化安全判定：只检查当前像素是否需要补绘
        # 移除复杂的多重安全判定逻辑
        cur_colors = arr[gy, gx, :]
        rep_f = rep_colors

        # 简单判定：如果代表色不是背景色（即有边框颜色），就绘制
        bg_arr = np.array(bg_color, dtype=np.uint8)
        is_border_color = np.any(rep_f != bg_arr, axis=1)

        # 当只处理最外层边框时，只补绘与最外层颜色接近的像素，
        # 防止把内层装饰/间隙色带入圆角弧线区域。
        if only_outermost and border_layers:
            outermost_color = np.array(border_layers[0][0], dtype=np.float64)
            color_diff = np.sqrt(
                np.sum((rep_f.astype(np.float64) - outermost_color.reshape(1, 3)) ** 2, axis=1)
            )
            is_border_color = is_border_color & (color_diff <= 30.0)

        if not np.any(is_border_color):
            continue

        # 只绘制有边框颜色的像素
        ay = gy[is_border_color]
        ax = gx[is_border_color]
        fill = rep_colors[is_border_color, :]
        arr[ay, ax, :] = fill

    result_img.paste(Image.fromarray(arr, 'RGB'))



def apply_border_only_corners(img: Image.Image, corners: dict[str, float],
                               dpi: int = 150, bg_color: tuple = (255, 255, 255),
                               border_width_cm: float = _DEFAULT_BORDER_WIDTH_CM,
                               pre_detected_layers: list[tuple[tuple[int, int, int], int]] = None) -> Image.Image:
    """
    仅对边框区域应用圆角，内部保持直角。

    [关键修正 v3]：只重绘最外层边框线条为圆角
    - 清除弧内区域（最外层边框以内）为背景色
    - 只重绘最外层边框层在圆角弧线上
    - 确保圆角处只有最外层边框可见，与 Photoshop 效果一致（图二）

    [关键修正 v2]：使用 _build_multi_layer_corner_mask 构建正确的遮罩
    - 每个角独立控制圆角半径，支持 tl=tr=0, bl=br>0 等不对称场景
    - 生成圆角 Mask 裁切原图
    - 在圆角边缘重新绘制边框层，确保边框在圆角处连续
    """
    w, h = img.size

    # 获取边框层信息
    if pre_detected_layers:
        border_layers = pre_detected_layers
    else:
        border_layers = _get_border_layers_robust(img, bg_color)

    # [Fix 2026-08-27] 过滤最外层外背景伪边框
    # _get_border_layers_robust 从图像边缘向内扫描，经常把大面积外背景误判为
    # 最外层边框层（如中古雨林黑色背景73px、塞纳时光米色背景41px、
    # 青芜漫野绿色背景118px）。该层不是真实边框，会导致：
    #   1. 保护模式下 border_zone 过宽，弧线外侧保留背景色形成弧形缺口
    #   2. 圆角处重绘过粗的伪边框线
    #   3. 真实边框层被吞没在"背景层"内无法识别
    # 修复：用 _estimate_outer_background 估算真实外背景色，若最外层颜色
    # 与外背景接近且厚度过大，则移除最外层。
    if border_layers:
        outer_bg = _estimate_outer_background(img)
        first_color, first_t = border_layers[0]
        color_dist_to_outer_bg = float(
            np.linalg.norm(np.array(first_color, dtype=np.float64) - np.array(outer_bg, dtype=np.float64))
        )
        # 颜色接近外背景，且厚度明显超出普通边框（>2cm 或 >30px）
        outer_bg_thickness_threshold = max(30, int(min(img.size) * 0.03))
        # [Fix 2026-08-27] 先将 remaining 初始化为 None，避免未绑定局部变量
        # 当外背景剔除条件不成立时，remaining 不会被赋值，
        # 原代码直接引用 remaining 触发 UnboundLocalError。
        remaining = None
        # [Fix 2026-09-01] 增加 outer_bg 必须是浅色的额外约束。
        # 当源图没有外背景（边缘直接是深棕/米色装饰边框）时，
        # _estimate_outer_background 会把装饰边框色当作"外背景"返回，
        # 导致颜色距离很小，错误地把唯一的装饰边框层也过滤掉 → border_layers=[]
        # → Step A 不执行 → 圆弧处变成白色背景 → 白色弧线 bug。
        # 真实的外背景几乎总是白色/浅色（mean > 200），
        # 装饰边框（深棕、米色、黑色等）不会满足这个条件，
        # 从根本上排除了"装饰边框被误判为外背景"的可能。
        outer_bg_mean = float(np.mean(outer_bg))
        if color_dist_to_outer_bg < 25.0 and first_t > outer_bg_thickness_threshold and outer_bg_mean > 200:
            remaining = border_layers[1:]
        if remaining:
            border_layers = remaining
            print("[FILTER] removed outer-bg layer, remaining:", border_layers)
        elif remaining is None:
            # 外背景剔除条件未触发 → 保留原 border_layers，不做变更。
            pass
        else:
            # 若移除后为空，说明整张图只有外背景一层，按空列表处理。
            # 后续逻辑会仅做简单圆角裁切，不会重绘任何边框，从而消除
            # 外背景被误判为边框导致的背景色弧形缺口。
            border_layers = []
            print("[FILTER] removed only outer-bg layer, empty result")

    # [Fix 2026-09-12 N-P1-03] 嵌套矩形检测已移除（死代码清理）。
    # 原因：corner_protect_map 对所有圆角角恒为 True（r_px > 0），
    #   导致 image_cropper_mask.py:297 保护模式恒跳过 nested_rects 处理段（B 段），
    #   所有嵌套矩形扫描结果（含逐层 R_eff 递减逻辑）永不被执行，白耗内存与 CPU。
    #   README:270-273 宣称的"R_eff 逐层递减"实际未接线，属文档漂移。
    # 移除后统一走保护模式：只裁剪边框条带，内部图案保持直角。

    # 构建 corners_px 字典
    corners_px = {}
    r_cap = max(1, min(w, h) // 2)
    for corner_key, radius_cm in corners.items():
        if radius_cm <= 0:
            continue
        r_raw = max(1, int(round(radius_cm * dpi / 2.54)))
        corners_px[corner_key] = min(r_raw, r_cap)

    if not corners_px:
        return img

    # 计算 content_ref_arr（从图像中心采样内容参考色，与 sector_render 保持一致）
    content_ref_arr = None
    if img is not None:
        # [Fix 2026-09-12 N-P1-02] 降采样避免全图 float64（2亿像素≈4.8GB）。
        # 策略：最长边>200px时缩放到200px级别再转float64；中位色稳定。
        MAX_SIDE = 200
        w_img, h_img = img.size
        if max(w_img, h_img) > MAX_SIDE:
            scale = MAX_SIDE / max(w_img, h_img)
            img_small = img.resize(
                (max(1, int(w_img * scale)), max(1, int(h_img * scale))),
                Image.BILINEAR,
            )
            img_f = np.array(img_small, dtype=np.float64)
        else:
            img_f = np.array(img, dtype=np.float64)
        h_arr, w_arr = img_f.shape[:2]
        cx_start = int(w_arr * 0.15)
        cx_end = int(w_arr * 0.85)
        cy_start = int(h_arr * 0.15)
        cy_end = int(h_arr * 0.85)
        STEPS = 21
        xs = np.linspace(cx_start, cx_end, STEPS, dtype=np.int64).clip(0, w_arr - 1)
        ys = np.linspace(cy_start, cy_end, STEPS, dtype=np.int64).clip(0, h_arr - 1)
        gx, gy = np.meshgrid(xs, ys)
        samples = img_f[gy, gx, :].reshape(-1, 3)
        if samples.shape[0] > 0:
            content_ref_arr = np.median(samples, axis=0)

    # apply_border_only_corners 语义：仅对外层边框带应用圆角，内部永远保持直角。
    # 因此所有角统一启用内容区保护模式，不再依赖 _corner_sector_has_content 的
    # 自动判断（旧自动判断在大半径、纯色背景扇形时会关闭保护，导致内层矩形框、
    # 装饰带也被圆角化，与“仅边框圆角”语义冲突）。
    #
    # [Fix 2026-09-07] 只使用最外层边框构建 mask / validity_mask / 重绘：
    #   若传入完整 border_layers，raw_depth 会把内层文字带/装饰带也算进
    #   border_zone，导致大半径时内层内容（如右侧文字带、花纹）被圆角化。
    #   border_only 模式下只需处理最外层边框，内层图案一律保持直角。
    outermost_layers = [border_layers[0]] if border_layers else []
    raw_depth = outermost_layers[0][1] if outermost_layers else 0
    corner_protect_map: dict[str, bool] = {
        ck: (r_px > 0) for ck, r_px in corners_px.items()
    }

    # 生成裁切 mask（per-corner protect_content）
    # 仅使用最外层边框层定义 border_zone，确保内层图案/矩形框/文字带保持直角。
    # [Fix INV-1] 传递实际 bg_color 和 content_ref_arr，确保 classify_gap_layers 判定一致
    mask = _build_multi_layer_corner_mask(
        w, h, corners_px, outermost_layers,
        protect_content=corner_protect_map,
        bg_color=bg_color,
        content_ref_arr=content_ref_arr,
    )

    # 生成独立的 validity_mask 用于边框重绘，只覆盖最外层边框带
    has_any_protect = any(corner_protect_map.values())
    if has_any_protect and raw_depth > 0:
        validity_mask = _build_border_paint_mask(w, h, corners_px, raw_depth)
    else:
        validity_mask = mask

    # 应用遮罩
    result = Image.new('RGB', (w, h), bg_color)
    result.paste(img, mask=mask)

    # [Fix 安妮森林 v3 — 彻底移除内部过清空]
    #
    # 历史问题：之前版本先"暴力清空弧内"再"只重绘最外层边框"，只适用于
    #   极少数"纯边框+纯白内部"的模板（中古花园边框打印版）。
    #
    #   但对于安妮森林（文字带边框）、墨上花开（花纹+米色间隙）、花幔
    #   （多层同心边框）等绝大多数产品，边框检测只识别外沿的 1~2 层薄边
    #   （~15px 总厚度），导致 inner_r = R - 15 ≈ 整圆内部被清空为白色，
    #   边框带内的文字/花纹/装饰全部被抹掉（安妮森林右下角红框 bug 的根因）。
    #
    # 新策略：完全移除"清空弧内"这一步骤 (_clear_inner_arc_to_bg 不再调用)。
    # 依靠：
    #   1. mask.paste(img)              → 弧内正确保留原图内容
    #   2. _redraw_border_on_corner    → 边框同心圆弧的结构感知重绘
    #   3. Smart Gap Check v2          → 真空间隙清空 / 装饰间隙保留
    #
    # 结果：
    #   ✅ 安妮森林文字带 → 保留原图内容（非间隙）不被误删
    #   ✅ 墨上花开米色间隙 → Smart v2 识别为真空间隙，正确清空
    #   ✅ 花幔多层彩色边框 → 完整的多层边框重绘，弧线衔接正确
    #
    # 注意：若将来需要支持"边框打印 + 内部强制纯白"的严格场景，建议新增
    #   参数显式开启强清空模式，而不是默认破坏绝大多数产品。

    # Step A: 只重绘最外层边框在圆弧上
    #
    # apply_border_only_corners 的设计语义是“仅处理最外层圆角边框线”，
    # 不再把内层装饰/间隙/深色带也沿圆弧重绘，避免产生紧贴黑色外框的深色弧形。
    if border_layers and corners_px:
        for corner_key, r_px in corners_px.items():
            if r_px <= 0:
                continue
            _redraw_border_on_corner(
                result, corner_key, r_px, outermost_layers,
                src_img=img, validity_mask=validity_mask,
                only_outermost=True,  # 仅绘制最外层边框
                bg_color=bg_color,
                paint_inside_arc=False,
            )

    # Step B: 安全补绘外轮廓
    # [Fix 2026-08-27] 仅当检测到真实边框层时才补绘。
    # 若边框层为空（如外背景被过滤后无剩余层），此时补绘会从原图边缘
    # 采样外背景色并误绘到弧线区域，形成背景色弧形缺口（中古雨林黑弧）。
    # [Fix 2026-09-10] skip_outside_arc=False 时补绘外轮廓，
    # 避免 arc 外侧 border_zone 内被 mask 裁为 bg 色的像素残留白色。
    if corners_px and border_layers:
        _redraw_outer_border_on_corners(
            result, img, corners_px, outermost_layers, validity_mask, bg_color,
            skip_outside_arc=False,  # 补绘所有 border_zone 内像素，确保圆弧处边框连续
            only_outermost=True,
        )

    # Step C: [Fix 多余边框弧线 v2] 兜底间隙清理
    # 这是对 Step A (sector_render) 的补充：
    #   扫描圆角区域，清除任何残留的间隙色像素
    #   使用增强的间隙检测策略确保无遗漏
    if corners_px and border_layers:
        _post_cleanup_gap_regions(
            result, img, corners_px, outermost_layers, validity_mask, bg_color,
        )

    return result

