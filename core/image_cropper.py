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

from .image_cropper_mask import (_analyze_corner_sector_content, _build_border_paint_mask, _build_multi_layer_corner_mask, _corner_sector_has_content, _estimate_outer_background, _post_cleanup_gap_regions)
from .image_cropper_border import (_DEFAULT_BORDER_WIDTH_CM, _redraw_outer_border_on_corners, apply_border_only_corners)

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


