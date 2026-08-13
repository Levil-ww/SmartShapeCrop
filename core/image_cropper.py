"""
core/image_cropper.py
图片裁剪服务：等比缩放 + 圆角裁剪 + 命名输出。

裁剪模式：
- simple_resize: 简单缩放（默认，高质量 LANCZOS，不裁剪不留白）
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
    bg_color: tuple = (255, 255, 255)
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

    OUTER_BAND = 5  # 只处理外轮廓 ±5px 薄层，绝不碰内层

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
        in_angle = (angle >= ang_min - 2) & (angle <= ang_max + 2)
        in_band = (dist >= float(inner_px)) & (dist <= float(r) + 1.5)
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
        depth_v = float(r) - dist[ys, xs]  # 外轮廓处 depth≈0, 向里≈OUTER_BAND
        d_int_v = np.clip(np.round(depth_v).astype(np.int32), 0, OUTER_BAND + 1)

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
        COLOR_T = 25.0

        # X: 当前像素是否已像边框（跳过，避免覆盖）
        looks_like_border = np.zeros(N, dtype=bool)
        if border_colors:
            for bc in border_colors:
                bcf = np.array(bc, dtype=np.float64).reshape(1, 3)
                d_ = np.sqrt(np.sum((cur_colors - bcf) ** 2, axis=1))
                looks_like_border |= (d_ <= COLOR_T + 5)

        # Y: 代表色是间隙/内容色 → 跳过（保留间隙）
        d_rep_content = np.sqrt(np.sum((rep_f - content_ref_arr.reshape(1, 3)) ** 2, axis=1))
        rep_is_gap = d_rep_content <= COLOR_T

        # A/B: 需要补绘
        d_cur_bg = np.sqrt(np.sum((cur_colors - BG_ARR.reshape(1, 3)) ** 2, axis=1))
        d_cur_content = np.sqrt(np.sum((cur_colors - content_ref_arr.reshape(1, 3)) ** 2, axis=1))
        cur_is_bg = d_cur_bg <= COLOR_T
        cur_is_content = d_cur_content <= COLOR_T + 5
        rep_is_solid = d_rep_content > COLOR_T  # 代表色是有色边框
        need_paint = (cur_is_bg & rep_is_solid) | (cur_is_content & rep_is_solid)

        safe_draw = need_paint & (~looks_like_border) & (~rep_is_gap)

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
                src_img=img, validity_mask=mask
            )

    # Step B: 安全的最外轮廓薄层补绘
    if corners_px:
        _redraw_outer_border_on_corners(
            result, img, corners_px, border_layers, mask, bg_color
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
) -> Image.Image:
    """
    构建多层边框动态圆角遮罩。[升级：嵌套矩形层感知]

    [关键不变量 S1/S4 + L1]
      每层嵌套矩形边框 k 有独立的有效圆角半径：
        R_eff(k, corner) = max(0, R_total - D(k, corner))
      其中 D(k, corner) 是该层矩形距图像外边缘的距离：
        - TL: D = max(rect.x1, rect.y1)
        - TR: D = max(w-1-rect.x2, rect.y1)
        - BL: D = max(rect.x1, h-1-rect.y2)
        - BR: D = max(w-1-rect.x2, h-1-rect.y2)

    修复案例：
    ┌─────────────────────────────────────────────────────────────────────┐
    │ 婉卉案例 (内层黑框 R_eff=0 应保持直角)：                              │
    │   若某层矩形 D ≥ R_total → R_eff=0 → 该层及内层像素被误切的全部恢复， │
    │   消除黑色矩形被切出圆弧缺口线的问题。                                 │
    │                                                                      │
    │ 花野案例 (14cm大半径 → 外→内半径递减)：                               │
    │   外层黑框 R_eff=R, 白框 R_eff=R-T_黑, 花纹 R_eff=R-T_黑-T_白        │
    │   → 每层只在 > 自身有效半径的区域裁切，自然形成同心递减圆弧。          │
    └─────────────────────────────────────────────────────────────────────┘

    Args:
        w, h: 图像宽高（像素）
        corners_px: 四角圆角半径（像素）
        border_layers: 边框层列表 [(color, thickness_px), ...]
        nested_rects: 可选，预检测的嵌套矩形（从 detect_nested_rect_layers 得到），
                      None 则在内部自动检测

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
    mask_arr = np.ones((h, w), dtype=np.uint8) * 255

    for corner_key, r in valid_corners.items():
        if r <= 0:
            continue

        r = min(r, max(1, min(w, h) // 2))
        if r <= 0:
            continue

        # ===== [A) 基础 outer L-cut + 保守 ring 保护] =====
        # ring_lower_bound: 精确等于边框总厚度 + 8px 公差
        # [Fix 克罗印花厚边框圆角弧线变细 0402]：
        #   旧逻辑用 min(raw_depth + 4, r*0.5) — 当边框厚≈圆角半径时(如2cm厚边框+2cm圆角)，
        #   r*0.5 只有边框厚度的一半，导致 validity_mask 只覆盖边框的最外 1cm，
        #   内层边框花纹无法被 sector_render 染成边框色，视觉上弧线比直边细一半。
        #   新逻辑放宽为 min(raw_depth + 8, r - 2)：
        #     - raw_depth + 8：确保整条边框厚度都在 ring_region 内，含抗锯齿容差
        #     - 上限 r-2：允许接近圆心，同时保留 2px 圆心直角区不被污染
        # [Fix 不对称圆角 0811]：当没有边框层时，ring_lower_bound 必须为 0，
        #   否则会过度保护应该被切掉的圆角区域，导致圆角不正确。
        if raw_depth > 0:
            ring_lower_bound = max(0, min(raw_depth + 8, r - 2))
        else:
            ring_lower_bound = 0

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

        # Step 1: 标准 L 形裁切（外层半径 r）
        outer_cut = (angle >= ang_min) & (angle < ang_max) & (dist > r)

        # Step 2: 保守 ring_region 保护外边框条带（防止最外轮廓线缺失）
        # [Fix C-shaped gap 0811] 扩展 ring_region 到弧外 2px，补偿像素离散化误差：
        #   当映射连续极坐标(角度+半径)到离散像素坐标时，实际距离可能略大于理想半径，
        #   导致弧线上的像素被 mask 切掉而未被边框重绘覆盖，形成 C 形缺口。
        #   增加 2px 容差确保弧线上的像素不被误切。
        ring_inner = float(r) - float(ring_lower_bound)
        ring_region = (angle >= ang_min) & (angle < ang_max) & \
                      (dist >= ring_inner) & (dist <= float(r) + 2.0)
        outer_cut = outer_cut & (~ring_region)

        mask_local = mask_arr[y1:y2, x1:x2]
        mask_local[outer_cut] = 0

        # ===== [B) 嵌套矩形层感知：逐层恢复被误切区域] =====
        # 对每个内层矩形 k，计算其有效半径 R_eff_k。
        # 若 R_eff_k < r → 矩形 k 内部 dist ∈ (R_eff_k, r] 的像素
        #   不应被外层 r 的大半径切掉，需要恢复为 mask=255。
        # 当 R_eff_k = 0（矩形离边缘足够远，应完全直角）→ 恢复矩形内全部
        #   dist ∈ (0, r] 的区域，实现"内层直角不被误切"（婉卉案例）。
        if nested_rects:
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
                in_ang = (angle >= ang_min) & (angle < ang_max)  # 不扩张角度
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

    # [Smart Gap Check] 构建不含间隙层的边框颜色数组
    # 用于间隙区域装饰检测，避免间隙层颜色被误判为边框色
    # 与 sector_render.py 保持一致的间隙层判定逻辑
    GAP_MAX_THICKNESS = 30.0
    is_gap_layer_cleanup = []
    for i, (c, t) in enumerate(border_layers):
        dist_to_content = float(np.sqrt(np.sum((np.array(c, dtype=np.float64) - content_ref) ** 2)))
        is_gap = (dist_to_content < CONTENT_COLOR_DIST and t <= GAP_MAX_THICKNESS and i > 0)
        is_gap_layer_cleanup.append(is_gap)
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
            COLOR_STD_THRESH = 8.0

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
                # 均匀间隙: 清理所有非边框、非背景的间隙色像素
                d_bg = np.sqrt(np.sum((pixel_colors - bg_arr.reshape(1, 3)) ** 2, axis=1))
                is_not_bg = d_bg > 5.0

                # 排除接近边框色的像素
                not_border_like = np.ones(count, dtype=bool)
                if len(solid_border_colors_arr) > 0:
                    for bc_arr in solid_border_colors_arr:
                        d_border = np.sqrt(np.sum((pixel_colors - bc_arr.reshape(1, 3)) ** 2, axis=1))
                        not_border_like &= (d_border > 15.0)

                to_clear = not_border_like & is_not_bg

                if np.any(to_clear):
                    clear_y = global_y[to_clear]
                    clear_x = global_x[to_clear]
                    arr[clear_y, clear_x, :] = bg_arr.reshape(1, 3).astype(np.uint8)
            # 装饰间隙: 完全不修改，保留原状态

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

    # 使用原始 border_layers + nested_rects 构建分层感知的 mask
    mask = _build_multi_layer_corner_mask(
        w, h, corners_px, border_layers, nested_rects=nested_rects
    )

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
                src_img=img, validity_mask=mask,
                only_outermost=True,  # [Fix] 仅保留最外层黑色边框（匹配 PS 效果）
            )

    # Step B: 安全补绘外轮廓
    if corners_px:
        _redraw_outer_border_on_corners(
            result, img, corners_px, border_layers, mask, bg_color
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
        'simple_resize': '简单缩放：直接缩放到目标尺寸，不裁剪不留白，保持图片完整性（推荐）',
        'cover': '裁剪填满：裁剪图片填满目标尺寸，可能损失边缘内容',
        'contain': '留白填充：完整显示图片，四周可能留白',
        'light_cover': '轻度裁剪：优先裁剪，裁剪量过大时自动改为留白',
        'auto': '智能模式：自动分析源图和目标比例差异，选择最佳方式',
    }
    return descriptions.get(mode, mode)
