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

子包拆分（向后兼容，所有名称在本模块仍可直接导入）：
  - core.corner.algorithm: 单步扇形切割算法
  - core.corner.detection: 边框层自动检测（_detect_border_layers /
    detect_nested_rect_layers / _scan_edge_boundaries /
    _get_border_layers_robust + 内部参数常量）
  - core.corner.sector_render: 圆角弧线上的多层边框重绘
    （_redraw_border_on_corner / _build_border_sector_mask /
    _sample_border_color / _angle_bottom / _angle_side）
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
    _angle_bottom,
    _angle_side,
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
# _DEFAULT_BORDER_WIDTH_CM 保留原名（带下划线），因为 apply_border_only_corners
# 的默认参数仍引用它。
_DEFAULT_BORDER_WIDTH_CM = DEFAULT_BORDER_WIDTH_CM

# 向后兼容别名：旧测试脚本可能导入 _CORNER_PARAMS / _CORNER_SQUARE。
# 内部统一使用 core.corner.algorithm.carve_corner_on_mask，不再使用这两个表。
# 此处从统一模块派生，确保数据单一来源。
_CORNER_PARAMS = {
    k: lambda w, h, r, _k=k: (
        list(get_corner_pieslice_bbox((0, 0, w, h), _k, r)), *CORNER_ANGLES[_k]
    )
    for k in ('tl', 'tr', 'bl', 'br')
}
_CORNER_SQUARE = {
    k: lambda w, h, r, _k=k: get_corner_square((0, 0, w, h), _k, r)
    for k in ('tl', 'tr', 'bl', 'br')
}


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


def _redraw_corners_by_edge_sampling(
    result_img: Image.Image,
    src_img: Image.Image,
    corners_px: dict[str, int],
    validity_mask: Image.Image,
    bg_color: tuple = (255, 255, 255)
) -> None:
    """
    [已废弃] 旧版暴力覆盖算法，会造成：
      1) 点状线边框变实线（消灭间隙） 2) 外层黑边变棕（内层色覆盖外层）
      3) 背景遮内层直角边框 4) 花朵装饰被覆盖。
    保留空壳仅为了旧代码导入不报错。所有调用已改为 _redraw_outer_border_on_corners v2。
    """
    return


def apply_rounded_corners(img: Image.Image, corners: dict[str, float], dpi: int = 150, bg_color: tuple = (255, 255, 255)) -> Image.Image:
    """
    对整张图片应用四角圆角裁剪。
    裁剪半径 = 指定的圆角半径（不做扩展）。
    
    [关键修正]：采用 Mask 剪切策略 + 重绘最外层边框
    - 生成圆角 Mask 裁切原图
    - 在圆角边缘重新绘制最外层边框，确保边框在圆角处连续
    - 内部线条保持直线，被圆角自然截断
    """
    w, h = img.size

    # 检测原图边框层
    border_layers = _get_border_layers_robust(img, bg_color)

    # 创建一个全黑的遮罩 (0 = 全透明)
    mask = Image.new('L', (w, h), 0)
    draw = ImageDraw.Draw(mask)

    # 统一圆角处理：厘米 → 像素
    corners_px = {}
    r_cap = max(1, min(w, h) // 2)
    for corner_key, radius_cm in corners.items():
        if radius_cm <= 0:
            continue
        r_raw = max(1, int(round(radius_cm * dpi / 2.54)))
        corners_px[corner_key] = min(r_raw, r_cap)

    # 确定各角半径
    tl_r = corners_px.get('tl', 0)
    tr_r = corners_px.get('tr', 0)
    br_r = corners_px.get('br', 0)
    bl_r = corners_px.get('bl', 0)
    
    # 如果四角半径相同，直接使用 PIL 的 rounded_rectangle
    if tl_r == tr_r == br_r == bl_r:
        r = tl_r
        if r > 0:
            draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
        else:
            draw.rectangle([0, 0, w - 1, h - 1], fill=255)
    else:
        # 四角半径不同，手动绘制
        inner_x1 = max(tl_r, bl_r)
        inner_y1 = max(tl_r, tr_r)
        inner_x2 = w - 1 - max(tr_r, br_r)
        inner_y2 = h - 1 - max(bl_r, br_r)
        
        # 1. 填充中心区域
        if inner_x2 > inner_x1 and inner_y2 > inner_y1:
            draw.rectangle([inner_x1, inner_y1, inner_x2, inner_y2], fill=255)
            
        # 2. 填充四条边
        if inner_x2 > inner_x1 and inner_y1 >= 0:
            draw.rectangle([inner_x1, 0, inner_x2, inner_y1], fill=255)
        if inner_x2 > inner_x1 and inner_y2 < h:
            draw.rectangle([inner_x1, inner_y2, inner_x2, h - 1], fill=255)
        if inner_y2 > inner_y1 and inner_x1 >= 0:
            draw.rectangle([0, inner_y1, inner_x1, inner_y2], fill=255)
        if inner_y2 > inner_y1 and inner_x2 < w:
            draw.rectangle([inner_x2, inner_y1, w - 1, inner_y2], fill=255)
            
        # 3. 绘制四个圆角
        if tl_r > 0:
            draw.pieslice([0, 0, tl_r * 2, tl_r * 2], start=180, end=270, fill=255)
        if tr_r > 0:
            cx, cy = w - 1 - tr_r, tr_r
            draw.pieslice([cx - tr_r, cy - tr_r, cx + tr_r, cy + tr_r], start=270, end=360, fill=255)
        if br_r > 0:
            cx, cy = w - 1 - br_r, h - 1 - br_r
            draw.pieslice([cx - br_r, cy - br_r, cx + br_r, cy + br_r], start=0, end=90, fill=255)
        if bl_r > 0:
            cx, cy = bl_r, h - 1 - bl_r
            draw.pieslice([cx - bl_r, cy - bl_r, cx + bl_r, cy + bl_r], start=90, end=180, fill=255)

    # 应用遮罩
    result = Image.new('RGB', (w, h), bg_color)
    result.paste(img, mask=mask)

    # [统一重绘流程 v2]
    # Step A: 用 sector_render._redraw_border_on_corner 做"结构感知"的多层边框重绘
    #   → 保留点状线/装饰/间隙，细双线自动掰弯成连续圆弧（解决 C 形缺口）
    if corners_px and border_layers:
        from .corner.sector_render import _redraw_border_on_corner
        for ck, rp in corners_px.items():
            if rp <= 0:
                continue
            _redraw_border_on_corner(
                result, ck, rp, border_layers,
                src_img=img, validity_mask=mask
            )

    # Step B: 安全的最外轮廓薄层补绘 v2（只在外缘±5px，缺才补，不破坏间隙）
    #   → 无论是否有间隙层都安全调用，修复外轮廓缺边/漏绘/不连续
    if corners_px:
        _redraw_outer_border_on_corners(
            result, img, corners_px, border_layers, mask, bg_color
        )

    return result


def apply_border_only_corners(img: Image.Image, corners: dict[str, float],
                               dpi: int = 150, bg_color: tuple = (255, 255, 255),
                               border_width_cm: float = _DEFAULT_BORDER_WIDTH_CM,
                               pre_detected_layers: list[tuple[tuple[int, int, int], int]] = None) -> Image.Image:
    """
    仅对边框区域应用圆角，内部保持直角。

    [关键修正]：使用 _build_multi_layer_corner_mask 构建正确的遮罩
    - 每个角独立控制圆角半径，支持 tl=tr=0, bl=br>0 等不对称场景
    - 统一重绘流程 v2：先多层结构感知重绘，再安全补绘外轮廓薄层
    """
    w, h = img.size

    # 获取边框层信息
    if pre_detected_layers:
        border_layers = pre_detected_layers
    else:
        border_layers = _get_border_layers_robust(img, bg_color)

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

    # 使用 _build_multi_layer_corner_mask 构建正确的遮罩（已升级放宽ring_region）
    mask = _build_multi_layer_corner_mask(w, h, corners_px, border_layers)

    # 应用遮罩
    result = Image.new('RGB', (w, h), bg_color)
    result.paste(img, mask=mask)

    # [统一重绘流程 v2] 与 apply_rounded_corners 完全一致
    # Step A: 多层结构感知重绘 (保留点状线/间隙/花朵装饰, 消灭 C 形缺口)
    if corners_px and border_layers:
        from .corner.sector_render import _redraw_border_on_corner
        for ck, rp in corners_px.items():
            if rp <= 0:
                continue
            _redraw_border_on_corner(
                result, ck, rp, border_layers,
                src_img=img, validity_mask=mask
            )

    # Step B: 始终调用安全的外轮廓补绘 v2
    #   旧逻辑的 has_gap_layers 跳过分支已移除，因为 v2 只在"真正缺边"时才绘制，
    #   且不碰内层间隙，对塞纳时光(有间隙)和花漾之约(无间隙)都安全。
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
) -> Image.Image:
    """
    构建多层边框动态圆角遮罩。

    核心设计：
    1. 初始化遮罩为全不透明（255），保留所有原图内容
    2. 对每个有圆角的角点，识别需要裁剪的 L 形区域并设置为 0
    3. L 形区域 = r×r 正方形 - 1/4 圆（需要裁掉的尖角部分）
    4. 弧线上的像素（dist == r）属于保留区域，使用 dist > r（非 dist >= r）

    边框层感知（border-only 模式）[Fix G/S6 边框条带保留]：
    - 计算边框总厚度 T（所有层累计厚度）
    - 在角点区域内，保留"边框条带"：沿两条直边延伸的边框区域
      （dist 在 [r-T, r] 范围内的像素）
    - 该保留确保边框上的装饰（文字/图案）在圆角裁剪后仍沿直边可见
    - 仅裁剪角部"尖端"区域：dist > r 且不在边框条带内

    Args:
        w: 图像宽度（像素）
        h: 图像高度（像素）
        corners_px: 四角圆角半径像素字典（键为 tl/tr/bl/br）
        border_layers: 边框层列表 [(color, thickness_px), ...]

    Returns:
        L 模式遮罩（255=保留原图，0=裁掉/背景色）
    """
    valid_corners = {k: v for k, v in corners_px.items() if v > 0}
    if not valid_corners:
        return Image.new('L', (w, h), 255)

    # [Fix G/S6 升级] 放宽环带深度，避免内层直角边框线被误裁为背景色。
    # 问题：total_border_depth 来源于检测到的层厚度之和，而检测常因硬上限
    # 或中间层漏检而偏小，导致内层真实边框落在 ring_region 外，L形裁切
    # 把它们切掉变成背景色（塞纳时光内层直角框消失的根因）。
    # 策略：深度下界 = max(检测总深度×扩展系数, 半径×保守比例)，
    # 确保绝大多数实际边框深度都在保留环带内。
    raw_depth = sum(t for _, t in border_layers) if border_layers else 0

    mask_arr = np.ones((h, w), dtype=np.uint8) * 255

    for corner_key, r in valid_corners.items():
        if r <= 0:
            continue

        r = min(r, max(1, min(w, h) // 2))
        if r <= 0:
            continue

        # 每个角独立计算环带下界：检测深度的2倍扩展 vs 半径的85%保守估计
        # 取两者中较大的一个；但不能超过 r-1（否则环带下界<0没意义）
        expanded_depth = int(round(raw_depth * 2.0 + 30))  # 检测×2 + 30px容差
        conservative_depth = int(round(r * 0.85))
        ring_lower_bound = max(raw_depth, expanded_depth, conservative_depth)
        ring_lower_bound = min(r - 1, ring_lower_bound)
        ring_lower_bound = max(0, ring_lower_bound)

        # 计算该角点的圆心位置
        if corner_key == 'tl':
            cx, cy = r, r
        elif corner_key == 'tr':
            cx, cy = w - r, r
        elif corner_key == 'bl':
            cx, cy = r, h - r
        else:  # br
            cx, cy = w - r, h - r

        # 计算处理区域
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

        # L 形裁剪 + 边框条带保留
        outer_cut = (angle >= ang_min) & (angle < ang_max) & (dist > r)

        # 保留深度在 [r - ring_lower_bound, r] 的环带区域
        ring_inner = float(r) - float(ring_lower_bound)
        ring_region = (angle >= ang_min) & (angle < ang_max) & \
                      (dist >= ring_inner) & (dist <= float(r))
        outer_cut = outer_cut & (~ring_region)

        mask_arr[y1:y2, x1:x2][outer_cut] = 0

    mask = Image.fromarray(mask_arr, mode='L')
    return mask


def apply_border_only_corners(img: Image.Image, corners: dict[str, float],
                               dpi: int = 150, bg_color: tuple = (255, 255, 255),
                               border_width_cm: float = _DEFAULT_BORDER_WIDTH_CM,
                               pre_detected_layers: list[tuple[tuple[int, int, int], int]] = None) -> Image.Image:
    """
    仅对边框区域应用圆角，内部保持直角。

    [关键修正]：使用 _build_multi_layer_corner_mask 构建正确的遮罩
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

    # [Fix G/S7] 边框层分离使用：
    # 1. 原始 border_layers（含间隙层）：
    #    - 用于 _build_multi_layer_corner_mask 计算 total_border_depth
    #    - 用于 _redraw_border_on_corner 计算正确的环带边界
    # 2. 间隙层（高亮度）在重绘时特殊处理：用内容色填充，而非边框色
    #    - 这实现了双层边框（黑-间隙-棕）在圆角处的正确分离
    #    - 间隙区域在直边由原图保留，在角部由重绘用内容色填充

    # 使用原始 border_layers（含间隙层）构建 mask
    mask = _build_multi_layer_corner_mask(w, h, corners_px, border_layers)

    # 应用遮罩
    result = Image.new('RGB', (w, h), bg_color)
    result.paste(img, mask=mask)

    # [统一重绘流程 v2] 始终按两步执行：
    if border_layers and corners_px:
        # Step A: sector_render._redraw_border_on_corner 结构感知多层重绘
        #   (保留点状线间隙/花朵装饰/间隙层颜色，细双线对角内区缺口自动填补)
        for corner_key, r_px in corners_px.items():
            if r_px <= 0:
                continue
            _redraw_border_on_corner(
                result, corner_key, r_px, border_layers,
                src_img=img, validity_mask=mask
            )

    # Step B: 始终调用安全补绘 v2（不区分 has_gap_layers）
    #   旧版暴力覆盖算法在"有间隙层"时确实会破坏结构，因此过去跳过。
    #   新版 _redraw_outer_border_on_corners v2:
    #     1) 只触碰外轮廓 ±5px 薄层（不碰内层间隙/装饰）
    #     2) 缺才补 (当前是背景色/内容色，且直边同深度是有色边框才绘)
    #     3) 代表色是间隙色(点状线中间白)时自动跳过 → 保留点状结构
    #   故对「塞纳时光(黑-间隙-棕)」和「花漾之约(厚奶油+圆点线)」均安全。
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
