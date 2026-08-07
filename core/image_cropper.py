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


def apply_rounded_corners(img: Image.Image, corners: dict[str, float], dpi: int = 150, bg_color: tuple = (255, 255, 255)) -> Image.Image:
    """
    对整张图片应用四角圆角裁剪。
    裁剪半径 = 指定的圆角半径（不做扩展）。
    裁剪后会在圆角弧线上重新绘制边框层，确保边框线跟随圆弧轮廓。

    圆角 mask 算法统一委托给 core.corner.algorithm.carve_corner_on_mask。

    Args:
        img: 输入图片（RGB 模式）
        corners: 四角圆角半径(cm)字典，键为 tl/tr/bl/br
        dpi: DPI，用于将厘米转为像素
        bg_color: 圆角处背景色

    Returns:
        应用圆角后的图片
    """
    w, h = img.size

    # 检测原图边框层（带 fallback）
    border_layers = _get_border_layers_robust(img, bg_color)

    mask = Image.new('L', (w, h), 255)  # 全不透明（保留原图）

    # 统一圆角处理：厘米 → 像素，整图 rect=(0,0,w,h)
    corners_px = {}
    for corner_key, radius_cm in corners.items():
        if radius_cm <= 0:
            continue
        corners_px[corner_key] = max(1, int(round(radius_cm * dpi / 2.54)))
    carve_corner_on_mask(mask, (0, 0, w, h), corners_px, canvas_size=(w, h))

    # 应用遮罩
    result = Image.new('RGB', (w, h), bg_color)
    result.paste(img, mask=mask)

    # 在圆角弧线上重新绘制边框层（[Fix A/S1] 传入 mask 作为 validity_mask，
    # 绝对不允许把已裁成背景色的尖角区域重新涂色，防止扇形向内折叠+色差）
    if border_layers:
        for corner_key, r_px in corners_px.items():
            _redraw_border_on_corner(
                result, corner_key, r_px, border_layers,
                src_img=img, validity_mask=mask)

    return result


def apply_border_only_corners(img: Image.Image, corners: dict[str, float],
                               dpi: int = 150, bg_color: tuple = (255, 255, 255),
                               border_width_cm: float = _DEFAULT_BORDER_WIDTH_CM) -> Image.Image:
    """
    仅对边框区域应用圆角，内部保持直角。
    裁剪后会在圆角弧线上重新绘制边框层，确保边框线跟随圆弧轮廓。

    实现思路：
    1. 创建完整的圆角遮罩（对整个图片应用圆角半径）—— 统一委托 carve_corner_on_mask
    2. 创建内部矩形遮罩（距边缘 border_width 的区域，全不透明）
    3. 合并遮罩：边框区域用圆角遮罩，内部用直角遮罩
    4. 在圆角弧线上重新绘制边框层

    Args:
        img: 输入图片（RGB 模式）
        corners: 四角圆角半径(cm)字典
        dpi: DPI
        bg_color: 圆角处背景色
        border_width_cm: 边框宽度(cm)，仅此深度范围内应用圆角

    Returns:
        应用边框圆角后的图片
    """
    w, h = img.size
    border_w_px = max(1, int(round(border_width_cm * dpi / 2.54)))

    # 计算最大圆角半径
    max_r = 0
    for radius_cm in corners.values():
        if radius_cm > max_r:
            max_r = radius_cm
    max_r_px = max(1, int(round(max_r * dpi / 2.54)))

    # 如果边框宽度不够容纳圆角，自动扩大边框宽度
    if border_w_px < max_r_px:
        border_w_px = max_r_px

    # 安全检查（第1次）：如果边框宽度超过图像一半，退化为整体圆角
    if border_w_px * 2 >= w or border_w_px * 2 >= h:
        return apply_rounded_corners(img, corners, dpi, bg_color)

    # ---- 步骤 0: 检测原图边框层（带 fallback） ----
    border_layers = _get_border_layers_robust(img, bg_color)
    total_border_thickness = sum(t for _, t in border_layers) if border_layers else 0

    # 确保边框区域足够容纳所有边框层
    if border_w_px < total_border_thickness + max_r_px:
        border_w_px = total_border_thickness + max_r_px

    # [Fix P0-2 / 修复③ 二次安全检查]
    # border_w_px 可能因 total_border_thickness 虚高（例如检测到 142px 的假厚边框）
    # 被极度扩大（如 142+177=319px），直接导致 inner_rect 坐标反转。
    # 扩大后必须再次检查，必要时退化为整体圆角。
    if border_w_px * 2 >= w or border_w_px * 2 >= h:
        logger.warning(
            "apply_border_only_corners: border_w_px=%d 扩大后超过半图（w=%d,h=%d），"
            "退化为整体圆角。检测层数=%d, 总检测厚度=%dpx",
            border_w_px, w, h, len(border_layers), total_border_thickness,
        )
        return apply_rounded_corners(img, corners, dpi, bg_color)

    # ---- 步骤 1: 创建完整的圆角遮罩（统一委托 carve_corner_on_mask） ----
    full_mask = Image.new('L', (w, h), 255)
    corners_px = {}
    for corner_key, radius_cm in corners.items():
        if radius_cm <= 0:
            continue
        corners_px[corner_key] = max(1, int(round(radius_cm * dpi / 2.54)))
    carve_corner_on_mask(full_mask, (0, 0, w, h), corners_px, canvas_size=(w, h))

    # ---- 步骤 2: 创建内部矩形遮罩 ----
    inner_mask = Image.new('L', (w, h), 0)
    inner_draw = ImageDraw.Draw(inner_mask)
    # [Fix P0-2 / 修复③ 防御性钳制]
    # 即使二次安全检查，仍用 clamp 保证 x1<x2, y1<y2，彻底杜绝
    # "ValueError y1 must be greater than or equal to y0" 崩溃。
    inner_x1 = max(0, min(border_w_px, w - 2))
    inner_y1 = max(0, min(border_w_px, h - 2))
    inner_x2 = max(inner_x1 + 1, min(w - 1, w - border_w_px))
    inner_y2 = max(inner_y1 + 1, min(h - 1, h - border_w_px))
    inner_rect = [inner_x1, inner_y1, inner_x2, inner_y2]
    inner_draw.rectangle(inner_rect, fill=255)

    # ---- 步骤 3: 合并遮罩 ----
    zero_img = Image.new('L', (w, h), 0)
    border_region_mask = Image.composite(zero_img, full_mask, inner_mask)
    final_mask = Image.composite(inner_mask, border_region_mask, inner_mask)

    # ---- 步骤 4: 应用遮罩 ----
    result = Image.new('RGB', (w, h), bg_color)
    result.paste(img, mask=final_mask)

    # ---- 步骤 5: 在圆角弧线上重新绘制边框层 ----
    # [Fix A/S1] 传入 final_mask 作为 validity_mask：绝对禁止重绘覆盖到背景透明区，
    # 防止扇形向内折叠+色差。同时 Fix A 也能防止检测到的假厚层过度涂色侵入内层。
    if border_layers:
        for corner_key, r_px in corners_px.items():
            _redraw_border_on_corner(
                result, corner_key, r_px, border_layers,
                src_img=img, validity_mask=final_mask)

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
            cropped = apply_border_only_corners(cropped, valid_corners, config.dpi, config.bg_color)

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
