"""
core/image_cropper.py
图片裁剪服务：等比缩放 + 圆角裁剪 + 命名输出。

裁剪模式：
- simple_resize: 简单缩放（默认，直接拉伸填满目标尺寸；源图比例不同时图像会变形）
- cover: 裁剪填满（裁剪到目标比例，可能损失部分内容）
- contain: 留白填充（完整显示，四周留白）
- light_cover: 轻度裁剪（仅裁剪必要部分，最多裁剪阈值）
- auto: 智能模式（自动选择 cover 或 contain）

圆角处理统一委托给 core.corner 子包，确保与 geometry.py、
process_image.py 三处圆角逻辑完全一致。

子包拆分：
  - core.corner.algorithm: 单步扇形切割算法
  - core.corner.detection: 边框层自动检测（_detect_border_layers /
    detect_nested_rect_layers / _scan_edge_boundaries /
    _get_border_layers_robust + 内部参数常量）
  - core.corner.sector_render: 圆角弧线上的多层边框重绘
    （_redraw_border_on_corner / _build_border_sector_mask /
    _sample_border_color）
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
# 从 corner 子包导入检测与重绘函数（直接从真实子包导入，不依赖 compat 别名）
# 这些名称在 image_cropper 模块命名空间内仍可被
# `from core.image_cropper import _detect_border_layers` 等方式访问。
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


@dataclass
class CropConfig:
    """裁剪配置"""
    src_path: str = ""                    # 源图路径
    target_w_cm: float = 0.0              # 目标宽度（厘米）
    target_h_cm: float = 0.0              # 目标高度（厘米）
    corners: dict[str, float] | None = None  # 四角圆角半径(cm)，键: tl/tr/bl/br
    mode: str = DEFAULT_CROP_MODE         # simple_resize | cover | contain | light_cover | auto
    dpi: int = DEFAULT_DPI                # 输出 DPI（与 CropDesign / UI 默认值一致）
    bg_color: tuple[int, int, int] = DEFAULT_BG_COLOR  # 背景色（留白/圆角处）
    output_path: str = ""                 # 输出路径（空则返回PIL对象）
    max_crop_ratio: float = DEFAULT_MAX_CROP_RATIO  # light_cover 最大裁剪比例
    # 自动缩放：是否允许放大源图到比原图更大
    allow_upscale: bool = True


# 向后兼容别名：旧测试脚本可能直接 from core.image_cropper import 这些常量。
# 内部统一使用 core.config 的定义，此处只是重导出。
_DEFAULT_BORDER_WIDTH_CM = DEFAULT_BORDER_WIDTH_CM


def load_source_image(path: str) -> Image.Image:
    """
    加载源图，支持 JPG/PNG/PSD。

    Args:
        path: 图片路径

    Returns:
        RGB 模式的 PIL Image
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"源图不存在: {path}")

    if is_psd_file(path):
        return load_psd_flattened(path)
    else:
        return load_image_rgb(path)


def _redraw_outer_border_on_corners(
    result_img: Image.Image,
    src_img: Image.Image,
    corners_px: dict[str, int],
    border_layers: list[tuple[tuple[int, int, int], int]],
    validity_mask: Image.Image,
    bg_color: tuple = (255, 255, 255),
    skip_outside_arc: bool = False,
) -> None:
    """
    [安全升级版本 v2] 圆角最外轮廓细边的安全补绘。

    与旧版 `_redraw_corners_by_edge_sampling`（暴力全环覆盖，已废弃）的区别：
    1. 只在"外轮廓薄层"（dist ∈ [r-3, r+1]）上操作，不动内层，避免破坏间隙/装饰
    2. 补绘前检查：仅当 result 当前像素为背景色/异常色，且 src 直边同深度为边框色时才绘制
       → 若原图是点状线间隙 (白色)，result 中保留白色，不消灭点结构
    3. 优先使用 border_layers[0] 最外层颜色（塞纳时光黑边问题根因：旧算法不分层）
    4. 对多层复杂边框，逐像素从 src 直边采样（但采样的是"真实边框颜色集合匹配"，
       不是单深度一个像素的随机色），避免外层被内层颜色覆盖
    
    Args:
        skip_outside_arc: 非保护模式下跳过 outside_arc 区域（dist > r），
                         因为裁切区域应该完全为白色，不重绘边框
    """
    if not corners_px:
        return

    w, h = result_img.size
    arr = np.array(result_img, dtype=np.uint8)
    src_arr = np.array(src_img, dtype=np.uint8)
    mask_arr = np.array(validity_mask, dtype=np.uint8) if validity_mask is not None else None

    # 边框颜色集合（用于安全判定：什么颜色算边框色）
    border_colors = list({tuple(c) for c, _ in border_layers}) if border_layers else []

    # 内容参考色（15%-85% 密集采样中值，同 sector_render 一致）
    def _content_ref(sarr, ww, hh):
        xs = np.linspace(int(ww * 0.15), int(ww * 0.85), 15, dtype=np.int64).clip(0, ww - 1)
        ys = np.linspace(int(hh * 0.15), int(hh * 0.85), 15, dtype=np.int64).clip(0, hh - 1)
        gx, gy = np.meshgrid(xs, ys)
        samples = sarr[gy, gx, :].reshape(-1, 3).astype(np.float64)
        return np.median(samples, axis=0) if samples.size else np.array([255.0, 255.0, 255.0])

    content_ref_arr = _content_ref(src_arr, w, h)
    BG_ARR = np.array(bg_color, dtype=np.float64)

    # [Fix INV-2/蔓生花/墨上花开] 计算自适应绘制带参数
    # 基于实际边框总厚度动态调整绘制带宽度，确保与原图边框厚度一致
    total_border_depth = sum(t for _, t in border_layers) if border_layers else 0
    
    # [Fix 2026-08-22 v7] 统一实心边框厚度计算
    # 修复前：此处手写第5套间隙判定（仅看"接近背景色"），完全无法识别
    #         素锦/安妮森林等案例的米色中间间隙层（米色不接近背景白色），
    #         导致 solid_border_depth 多算了间隙厚度 → 绘制带过宽，边框变粗。
    # 修复后：使用 classify_gap_layers（单一判定来源）精确识别间隙层，
    #         确保实心厚度与 sector_render、mask构建 三处完全一致。
    solid_border_depth = 0
    if border_layers:
        _is_gap = classify_gap_layers(border_layers, bg_color=bg_color,
                                     content_ref_arr=content_ref_arr)
        for (_c, t), ig in zip(border_layers, _is_gap):
            if not ig:  # 仅实心边框层计入厚度
                solid_border_depth += t
    
    # [Fix v7] 重新设计绘制带参数：
    # 核心不变量INV-2: 圆角边框厚度 = 直边边框厚度
    # 
    # 关键修复：inner_extent 必须匹配实心边框的实际厚度
    # 
    # 问题：当 protect_content=True 时，边框条带由 _build_multi_layer_corner_mask
    #       定义，它会清除间隙区域。但边框重绘需要重建被清除的边框像素。
    #       如果 inner_extent 小于实际边框厚度，就会出现"边框过细"问题。
    # 
    # 解决方案：
    # 1. inner_extent 使用实心边框层厚度（可能包含间隙后的实心部分）
    #    - 对于多层边框：使用 sum(solid_layer_thicknesses)
    #    - 限制范围[2, 12]，确保有足够的绘制厚度
    # 2. outer_extent 设为 1.0px，仅用于抗锯齿过渡
    #    - 不添加额外厚度，但确保边框外缘平滑
    if solid_border_depth > 0:
        # 使用实心边框总厚度作为 inner_extent
        # 加上2px的抗锯齿余量，确保边框完全覆盖
        inner_extent = min(max(2, solid_border_depth + 1), 12)
    elif total_border_depth > 0:
        # fallback: 使用总厚度
        inner_extent = min(max(2, total_border_depth + 1), 12)
    else:
        inner_extent = 3  # 默认 3px
    
    # 绘制带外边界：1.0px用于抗锯齿过渡
    # 不添加额外厚度，仅确保边框外缘平滑
    outer_extent = 1.0

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

        # [Fix INV-2/INV-5] 自适应绘制带：
        # 内边界 = r - inner_extent（保证蔓生花等细边框有足够绘制范围）
        # 外边界 = r + outer_extent（仅1.0px抗锯齿，防止墨上花开边框过粗）
        # [Fix v6] 修复角度范围：精确匹配CORNER_ANGLES，不再±1°扩展
        # 旧逻辑 ang_min-1 ~ ang_max+1 导致角边界外1°范围被错误绘制
        if ang_max == 360:
            in_angle = (angle >= ang_min) | (angle < 1)
        else:
            in_angle = (angle >= ang_min) & (angle <= ang_max)
        in_band = (dist >= float(r) - float(inner_extent)) & (dist <= float(r) + float(outer_extent))
        
        # [Fix 非保护模式] Skip outside_arc pixels (dist > r) when requested
        # 此时外边界收缩到弧边界，不绘制弧外侧像素
        if skip_outside_arc:
            in_band = (dist >= float(r) - float(inner_extent)) & (dist <= float(r))
        
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
        # [Fix 图一] Calculate depth from outer edge (r) for consistent sampling
        # depth_v: distance from arc boundary (positive = inside arc)
        depth_v = float(r) - dist[ys, xs]
        d_int_v = np.clip(np.round(depth_v).astype(np.int32), 0, 3)

        # 从两条相邻直边的同深度中段采样颜色集合
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
            # cols: list of [N,3], 取中值得到该深度的"代表边框颜色"
            if not cols:
                return np.tile(np.array([[0, 0, 0]], dtype=np.uint8), (n, 1))
            stacked = np.stack(cols, axis=0)  # [S, N, 3]
            med = np.median(stacked.reshape(-1, stacked.shape[-2], 3), axis=0)
            return med.astype(np.uint8)

        N = len(gy)
        rep_colors = _edge_sample(corner_key, d_int_v, N)  # [N, 3] uint8

        # 当前 result 颜色和 src 同位置颜色
        cur_colors = arr[gy, gx, :].astype(np.float64)
        src_colors = src_arr[gy, gx, :].astype(np.float64)
        rep_f = rep_colors.astype(np.float64)

        # ========= 安全判定 (只在需要时才绘制) =========
        # 允许绘制的条件 (满足任一即可)：
        #   A) 当前像素是背景色 (与bg_color近) + 代表色不是内容色 → 需要补上边框
        #   B) 当前像素是内容色 (与content_ref近) + 代表色是有色边框 → 缺口需要补
        # 禁止绘制的条件 (满足任一即跳过)：
        #   X) 当前像素与任一边框色近 → 已有正确边框，不要覆盖
        #   Y) 代表色与内容色(间隙)近 → 当前位置是点状线/虚线间隙，留白
        #   Z) [Fix v2] 代表色与任何间隙层颜色匹配 → 不绘制间隙色
        COLOR_T = 25.0

        # [Fix v3] 使用 classify_gap_layers 统一判定：
        # 修复前：此处内部手写第4套间隙检测逻辑，与其他3处不一致。
        # 修复后：统一调用 classify_gap_layers，参数直接透传 bg_color 和本函数已
        #   计算的 content_ref_arr。
        _is_gap = classify_gap_layers(border_layers, bg_color=bg_color,
                                     content_ref_arr=content_ref_arr)
        gap_colors_list = []
        solid_border_colors = []
        for idx, (c, t) in enumerate(border_layers):
            col = tuple(c)
            if idx < len(_is_gap) and _is_gap[idx]:
                gap_colors_list.append(col)
            else:
                solid_border_colors.append(col)
        # 去重
        solid_border_colors = list(set(solid_border_colors))
        gap_colors_list = list(set(gap_colors_list))

        # X: 当前像素是否已像边框（跳过，避免覆盖）
        looks_like_border = np.zeros(N, dtype=bool)
        if solid_border_colors:
            for bc in solid_border_colors:
                bcf = np.array(bc, dtype=np.float64).reshape(1, 3)
                d_ = np.sqrt(np.sum((cur_colors - bcf) ** 2, axis=1))
                looks_like_border |= (d_ <= COLOR_T + 5)

        # Y: 代表色是间隙/内容色 → 跳过（保留间隙）
        d_rep_content = np.sqrt(np.sum((rep_f - content_ref_arr.reshape(1, 3)) ** 2, axis=1))
        rep_is_gap = d_rep_content <= COLOR_T
        
        # Z: [Fix v2] 代表色匹配间隙层颜色 → 跳过
        rep_matches_gap_color = np.zeros(N, dtype=bool)
        if gap_colors_list:
            for gc in gap_colors_list:
                gcf = np.array(gc, dtype=np.float64).reshape(1, 3)
                d_gc = np.sqrt(np.sum((rep_f - gcf) ** 2, axis=1))
                rep_matches_gap_color |= (d_gc <= 20.0)

        # A/B: 需要补绘
        d_cur_bg = np.sqrt(np.sum((cur_colors - BG_ARR.reshape(1, 3)) ** 2, axis=1))
        d_cur_content = np.sqrt(np.sum((cur_colors - content_ref_arr.reshape(1, 3)) ** 2, axis=1))
        cur_is_bg = d_cur_bg <= COLOR_T
        cur_is_content = d_cur_content <= COLOR_T + 5
        rep_is_solid = d_rep_content > COLOR_T  # 代表色是有色边框
        need_paint = (cur_is_bg & rep_is_solid) | (cur_is_content & rep_is_solid)

        safe_draw = need_paint & (~looks_like_border) & (~rep_is_gap) & (~rep_matches_gap_color)

        if not np.any(safe_draw):
            continue
        ay = gy[safe_draw]
        ax = gx[safe_draw]
        fill = rep_colors[safe_draw, :].astype(arr.dtype)
        arr[ay, ax, :] = fill

    result_img.paste(Image.fromarray(arr, 'RGB'))


def apply_rounded_corners(img: Image.Image, corners: dict[str, float], dpi: int = 150, bg_color: tuple = (255, 255, 255)) -> Image.Image:
    """
    对整张图片应用四角圆角裁剪。
    裁剪半径 = 指定的圆角半径（不做扩展）。
    
    [关键修正]：采用 Mask 剪切策略 + 重绘最外层边框
    - 使用 carve_corner_on_mask 创建准确的圆角 mask（避免 PIL rounded_rectangle 的边界缺口）
    - 在圆角边缘重新绘制最外层边框，确保边框在圆角处连续
    - 内部线条保持直线，被圆角自然截断
    
    [Fix C-shaped gap 0812]：
    - 使用 carve_corner_on_mask 替代 rounded_rectangle 创建 mask
    - rounded_rectangle 在圆角边界会产生值为 0 的像素（应该为 255），导致 C 形缺口
    - carve_corner_on_mask 通过挖空正方形 + 填回四分之一圆的方式，确保所有边界像素正确
    """
    w, h = img.size

    # 检测原图边框层
    border_layers = _get_border_layers_robust(img, bg_color)

    # 统一圆角处理：厘米 → 像素
    corners_px = {}
    r_cap = max(1, min(w, h) // 2)
    for corner_key, radius_cm in corners.items():
        if radius_cm <= 0:
            continue
        r_raw = max(1, int(round(radius_cm * dpi / 2.54)))
        corners_px[corner_key] = min(r_raw, r_cap)

    # 创建圆角 mask（使用 carve_corner_on_mask 确保边界像素正确）
    from .corner.algorithm import carve_corner_on_mask

    mask = Image.new('L', (w, h), 255)  # 初始化为全255（保留）
    rect = (0, 0, w, h)
    carve_corner_on_mask(mask, rect, corners_px)

    # 应用遮罩
    result = Image.new('RGB', (w, h), bg_color)
    result.paste(img, mask=mask)

    # [Fix 安妮森林 v3 — 同步修复 apply_rounded_corners]
    #
    # 历史问题：同 apply_border_only_corners — 1) 用 outermost_thickness 计算
    #   inner_r 导致几乎整圆被清空；2) 只传 border_layers[:1] 给重绘。
    #
    # 修复: 1) 移除 _clear_inner_arc_to_bg；2) 传完整 border_layers。
    #   详见 apply_border_only_corners 内的长篇注释 (Fix 安妮森林 v3)。

    # Step A: 重绘所有边框层在圆弧上（完整 border_layers）
    if corners_px and border_layers:
        for ck, rp in corners_px.items():
            if rp <= 0:
                continue
            _redraw_border_on_corner(
                result, ck, rp, border_layers,
                src_img=img, validity_mask=mask,
                bg_color=bg_color,
            )

    # Step B: 安全的最外轮廓薄层补绘
    if corners_px:
        _redraw_outer_border_on_corners(
            result, img, corners_px, border_layers, mask, bg_color,
            skip_outside_arc=True,  # 非保护模式：裁切区域不应重绘边框
        )

    return result


def _filter_gap_layers(
    border_layers: list[tuple[tuple[int, int, int], int]],
    bg_color: tuple[int, int, int] = (255, 255, 255),
) -> list[tuple[tuple[int, int, int], int]]:
    """
    [Fix G/S7] 过滤"间隙层"——位于深色边框层之间的内容色/背景色层。

    通过与背景色比较，过滤掉与背景色相似的层：
      典型场景：「塞纳时光」双层边框 (黑-间隙-棕)
        检测返回: [(25,22,20):17, (245,235,220):15, (150,95,65):45]
        其中 (245,235,220) 是"间隙层"：
          - 与背景色 CREAM (245,235,220) 几乎相同
          - 不是独立的边框色，而是图片内容/底色
        过滤后: [(25,22,20):17, (150,95,65):45]

    Args:
        border_layers: 原始边框层列表
        bg_color: 背景色参考（通常是图片底色）

    Returns:
        过滤后的边框层列表（不含间隙层）
    """
    BG_SIMILARITY = 50.0  # 与背景色距离阈值（与 sector_render.py 的 GAP_COLOR_DIST 对齐）

    filtered: list[tuple[tuple[int, int, int], int]] = []
    for color, thickness in border_layers:
        dist_to_bg = float(np.sqrt(sum((a - b) ** 2 for a, b in zip(color, bg_color))))
        if dist_to_bg < BG_SIMILARITY:
            # 与背景色相似，视为间隙层，跳过
            continue
        filtered.append((color, thickness))
    return filtered


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
                # 基础 ring_region：保护从 ring_inner_bound 到 r+2 的区域
                ring_region = (angle >= ang_min) & (angle <= ang_max) & \
                              (dist >= ring_inner_bound) & (dist <= float(r) + 2.0)
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

        # [Fix 图三] validity_mask 始终使用完整边框厚度
        # 间隙层需要被背景色填充，必须覆盖到间隙层所在深度
        effective_depth = border_depth

        # 计算边框重绘的有效深度（边框厚度，但不超过圆角半径）
        paint_depth = min(effective_depth + 8, r)  # +8px 抗锯齿容差

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
        if color_dist_to_outer_bg < 25.0 and first_t > outer_bg_thickness_threshold:
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

    # ===== [新增] 检测嵌套矩形层（用于逐层有效半径递减 + 内层直角保护）=====
    try:
        nested_rects = detect_nested_rect_layers(img, border_layers=border_layers)
    except Exception as e:
        logger.warning(f"嵌套矩形层检测失败: {e}")
        nested_rects = []
    if not nested_rects:
        nested_rects = [(0, 0, w - 1, h - 1)]

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
        img_arr = np.array(img, dtype=np.float64)
        h_img, w_img = img_arr.shape[:2]
        cx_start = int(w_img * 0.15)
        cx_end = int(w_img * 0.85)
        cy_start = int(h_img * 0.15)
        cy_end = int(h_img * 0.85)
        STEPS = 21
        xs = np.linspace(cx_start, cx_end, STEPS, dtype=np.int64).clip(0, w_img - 1)
        ys = np.linspace(cy_start, cy_end, STEPS, dtype=np.int64).clip(0, h_img - 1)
        gx, gy = np.meshgrid(xs, ys)
        samples = img_arr[gy, gx, :].reshape(-1, 3)
        if samples.shape[0] > 0:
            content_ref_arr = np.median(samples, axis=0)

    # 智能判断每个角是否需要保护内容区
    # [Fix v8 2026-08-27] 基于真实外背景色判断是否启用保护模式
    #
    # 根因分析：
    #   旧逻辑 v6：仅当 r <= 2*raw_depth 时启用保护模式
    #   问题：当圆角半径较大（如 r=25px, raw_depth=5px）时，r > 2*raw_depth，
    #         导致 protect=False，整个内容区被圆角化（庄园秘境/塞纳时光问题）
    #
    #   旧逻辑 v7：始终启用保护模式
    #   问题：当圆角外侧实际上只是纯色外背景（无花纹/图案）时，强行启用保护
    #         会把大面积外背景保留在圆角弧线外侧，形成背景色弧形缺口
    #         （中古雨林黑色弧线、塞纳时光米色弧线、青芜漫野圆角范围异常）。
    #
    # 新策略：基于 _corner_sector_has_content 自动判断
    #   - 外侧扇形区域含有真实内容（花纹/图案）→ 启用保护，只裁边框条带
    #   - 外侧扇形区域与外背景一致（纯色背景）→ 不保护，完整裁切扇形
    #   - 只在边框条带内裁切，内容区保持直角
    #   - 边框条带定义为两个边框条的并集（T = raw_depth + 4px 容差）
    raw_depth = sum(t for _, t in border_layers) if border_layers else 0
    corner_protect_map: dict[str, bool] = {}
    for corner_key, r_px in corners_px.items():
        if r_px <= 0:
            corner_protect_map[corner_key] = False
            continue
        # [Fix v8] 按真实外背景判断是否需要保护内容区
        corner_protect_map[corner_key] = _corner_sector_has_content(
            img, corner_key, r_px, raw_depth
        )

    # 生成裁切 mask（per-corner protect_content）
    # [Fix INV-1] 传递实际 bg_color 和 content_ref_arr，确保 classify_gap_layers 判定一致
    mask = _build_multi_layer_corner_mask(
        w, h, corners_px, border_layers, nested_rects=nested_rects,
        protect_content=corner_protect_map,
        bg_color=bg_color,
        content_ref_arr=content_ref_arr,
    )

    # 生成独立的 validity_mask 用于边框重绘
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

    # Step A: 重绘所有边框层在圆弧上（完整 border_layers，不再只取最外层1层）
    #
    # 由 sector_render.py 的 Smart Gap Check v2 负责智能区分：
    #   - 真空间隙（墨上花开/塞纳时光米色带，颜色均匀纯色）→ 清空为背景色
    #   - 装饰间隙（安妮森林文字带 / 花漾之约点状线，含非均匀装饰）→ 保留原贴图
    #   - 有色边框层 → 结构感知的同心圆弧重绘，保护装饰像素不被覆盖
    if border_layers and corners_px:
        for corner_key, r_px in corners_px.items():
            if r_px <= 0:
                continue
            _redraw_border_on_corner(
                result, corner_key, r_px, border_layers,
                src_img=img, validity_mask=validity_mask,
                only_outermost=False,  # 绘制所有边框层（gap层由Smart Gap Check自动跳过）
                bg_color=bg_color,
            )

    # Step B: 安全补绘外轮廓
    # [Fix 2026-08-27] 仅当检测到真实边框层时才补绘。
    # 若边框层为空（如外背景被过滤后无剩余层），此时补绘会从原图边缘
    # 采样外背景色并误绘到弧线区域，形成背景色弧形缺口（中古雨林黑弧）。
    if corners_px and border_layers:
        _redraw_outer_border_on_corners(
            result, img, corners_px, border_layers, validity_mask, bg_color,
            skip_outside_arc=True,  # 非保护模式：裁切区域不应重绘边框
        )

    # Step C: [Fix 多余边框弧线 v2] 兜底间隙清理
    # 这是对 Step A (sector_render) 的补充：
    #   扫描圆角区域，清除任何残留的间隙色像素
    #   使用增强的间隙检测策略确保无遗漏
    if corners_px and border_layers:
        _post_cleanup_gap_regions(
            result, img, corners_px, border_layers, validity_mask, bg_color,
        )

    return result


def _smart_crop(src: Image.Image, target_w_px: int, target_h_px: int,
                max_crop_ratio: float = 0.15, bg_color: tuple = (255, 255, 255)) -> Image.Image:
    """
    智能裁剪：当源图和目标比例差异较小时用 cover，差异较大时用 contain。

    Args:
        src: 源图
        target_w_px: 目标宽度
        target_h_px: 目标高度
        max_crop_ratio: 最大裁剪比例
        bg_color: 背景色

    Returns:
        裁剪后的图片
    """
    sw, sh = src.size
    src_ratio = sw / sh
    target_ratio = target_w_px / target_h_px

    # 计算裁剪后的比例差异
    if target_ratio > src_ratio:
        # 目标更宽 - 需要裁剪宽度方向
        cover_w = target_w_px
        cover_h = int(round(target_w_px / src_ratio))

        if cover_h > target_h_px:
            # 需要裁剪高度
            crop_amount = (cover_h - target_h_px) / cover_h
            if crop_amount <= max_crop_ratio:
                # 裁剪量在阈值内，用 cover
                return fit_image_to_rect(src, target_w_px, target_h_px, mode='cover')
            else:
                # 裁剪量太大，用 contain
                return fit_image_to_rect(src, target_w_px, target_h_px, mode='contain', bg_color=bg_color)
        else:
            return fit_image_to_rect(src, target_w_px, target_h_px, mode='cover')
    else:
        # 目标更高 - 需要裁剪高度方向
        cover_h = target_h_px
        cover_w = int(round(target_h_px * src_ratio))

        if cover_w > target_w_px:
            crop_amount = (cover_w - target_w_px) / cover_w
            if crop_amount <= max_crop_ratio:
                return fit_image_to_rect(src, target_w_px, target_h_px, mode='cover')
            else:
                return fit_image_to_rect(src, target_w_px, target_h_px, mode='contain', bg_color=bg_color)
        else:
            return fit_image_to_rect(src, target_w_px, target_h_px, mode='cover')


def _light_cover(src: Image.Image, target_w_px: int, target_h_px: int,
                 max_crop_ratio: float = 0.15, bg_color: tuple = (255, 255, 255)) -> Image.Image:
    """
    轻度裁剪：只裁剪必要部分，不超过 max_crop_ratio。
    如果裁剪量超过阈值，则使用 contain 模式并添加背景。
    """
    sw, sh = src.size
    src_ratio = sw / sh
    target_ratio = target_w_px / target_h_px

    # 计算 cover 模式下的裁剪量
    if target_ratio > src_ratio:
        # 目标更宽
        scale = target_w_px / sw
        new_h = int(round(sh * scale))
        crop_amount = abs(new_h - target_h_px) / new_h if new_h > 0 else 1
    else:
        # 目标更高
        scale = target_h_px / sh
        new_w = int(round(sw * scale))
        crop_amount = abs(new_w - target_w_px) / new_w if new_w > 0 else 1

    if crop_amount <= max_crop_ratio:
        # 裁剪量可接受
        return fit_image_to_rect(src, target_w_px, target_h_px, mode='cover', bg_color=bg_color)
    else:
        # 裁剪量太大，改用 contain
        return fit_image_to_rect(src, target_w_px, target_h_px, mode='contain', bg_color=bg_color)


def crop_image(config: CropConfig) -> Image.Image:
    """
    执行完整的裁剪流程。

    Args:
        config: 裁剪配置

    Returns:
        裁剪后的 PIL Image
    """
    # 1. 加载源图
    src = load_source_image(config.src_path)

    # 2. 计算目标像素尺寸
    target_w_px = int(round(config.target_w_cm * config.dpi / 2.54))
    target_h_px = int(round(config.target_h_cm * config.dpi / 2.54))

    # 3. 根据模式选择裁剪方式
    mode = config.mode
    bg_color = config.bg_color

    # 在裁剪前检测边框层（源图上检测更准确）
    pre_detected_layers = None
    # [Fix 2026-08-26 结合8.21版] simple_resize = stretch（直接拉伸，无统一缩放比例），
    #   源图边框按单轴 scale 缩放会失真 → 跳过预检测，改由 apply_border_only_corners
    #   在拉伸后的图上自动检测（8.21版原逻辑，经实测预览正确）。
    #   cover/contain/light_cover/auto 有统一等比 scale，预检测更准确。
    if config.corners and mode != 'simple_resize':
        valid_corners = {k: v for k, v in config.corners.items() if v > 0}
        if valid_corners:
            # 在源图上检测边框层
            src_layers = _get_border_layers_robust(src, bg_color)
            if src_layers:
                # 计算缩放比例（与 fit_image_to_rect 保持一致）
                sw, sh = src.size
                if mode in ('cover', 'auto', 'light_cover'):
                    scale = max(target_w_px / sw, target_h_px / sh)
                elif mode == 'contain':
                    scale = min(target_w_px / sw, target_h_px / sh)
                else:
                    scale = 1.0

                # 按比例缩放边框层厚度
                pre_detected_layers = [
                    (color, max(1, int(round(thickness * scale))))
                    for color, thickness in src_layers
                ]
                logger.info(f"源图边框层检测: {len(src_layers)}层, 缩放比例={scale:.3f}")
                for i, (color, thickness) in enumerate(pre_detected_layers):
                    logger.info(f"  第{i+1}层: 厚度={thickness}px ({thickness * 2.54 / config.dpi:.2f}cm), 颜色={color}")

    if mode == 'simple_resize':
        # [Fix 2026-08-26 结合8.21版] simple_resize = 直接缩放到目标尺寸（stretch）
        # 项目硬约束："simple_resize 不裁剪不留白" → 必须填满目标尺寸，contain 会留白白条
        # stretch 在源图宽高比≠目标时有变形，但这是 simple_resize 模式的设计意图
        # （用户选"简单缩放"即接受直接拉伸；需保持宽高比请用 cover/contain）
        cropped = src.resize((target_w_px, target_h_px), Image.LANCZOS)
    elif mode == 'auto':
        cropped = _smart_crop(src, target_w_px, target_h_px, config.max_crop_ratio, bg_color)
    elif mode == 'light_cover':
        cropped = _light_cover(src, target_w_px, target_h_px, config.max_crop_ratio, bg_color)
    elif mode == 'contain':
        cropped = fit_image_to_rect(src, target_w_px, target_h_px, mode='contain', bg_color=bg_color)
    else:  # cover
        cropped = fit_image_to_rect(src, target_w_px, target_h_px, mode='cover', bg_color=bg_color)

    # 4. 应用圆角（仅边框区域应用圆角，内部保持直角）
    if config.corners:
        valid_corners = {k: v for k, v in config.corners.items() if v > 0}
        if valid_corners:
            cropped = apply_border_only_corners(
                cropped, valid_corners, config.dpi, config.bg_color,
                pre_detected_layers=pre_detected_layers
            )

    # 5. 保存或返回
    if config.output_path:
        from .image_ops import save_jpg
        save_jpg(cropped, config.output_path, quality=95, dpi=config.dpi)

    return cropped


def batch_crop(configs: list[CropConfig]) -> list[tuple[str, bool, str]]:
    """
    批量裁剪多张图片。

    Args:
        configs: 裁剪配置列表

    Returns:
        结果列表 [(output_path, success, message), ...]
    """
    results = []
    for cfg in configs:
        try:
            crop_image(cfg)
            results.append((cfg.output_path, True, "OK"))
        except Exception as e:
            logger.error(f"批量裁剪失败 output_path={cfg.output_path}: {e}")
            results.append((cfg.output_path, False, str(e)))
    return results


def get_corner_name(key: str) -> str:
    """获取角的中文名称"""
    names = {'tl': '左上角', 'tr': '右上角', 'bl': '左下角', 'br': '右下角'}
    return names.get(key, key)


def get_default_corners() -> dict[str, float]:
    """获取默认四角（无圆角）"""
    return {'tl': 0.0, 'tr': 0.0, 'bl': 0.0, 'br': 0.0}


def get_mode_description(mode: str) -> str:
    """获取裁剪模式描述"""
    descriptions = {
        'simple_resize': '简单缩放：直接拉伸填满目标尺寸，不裁剪不留白；源图比例与目标不同时图像会变形，需保持比例请用裁剪填满/留白填充',
        'cover': '裁剪填满：裁剪图片填满目标尺寸，可能损失边缘内容',
        'contain': '留白填充：完整显示图片，四周可能留白',
        'light_cover': '轻度裁剪：优先裁剪，裁剪量过大时自动改为留白',
        'auto': '智能模式：自动分析源图和目标比例差异，选择最佳方式',
    }
    return descriptions.get(mode, mode)
