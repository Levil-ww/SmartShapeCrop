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

    边框层感知（border-only 模式）：
    - 计算边框总厚度 T
    - 当 R > T 时，内层矩形的角半径为 R_inner = R - T
      内层 L 形区域（相对于同一圆心）也需要裁剪，以确保内层为直角
    - 当 R <= T 时，整个圆弧区域都在边框厚度内，无需额外裁剪

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

    mask_arr = np.ones((h, w), dtype=np.uint8) * 255

    for corner_key, r in valid_corners.items():
        if r <= 0:
            continue

        r = min(r, max(1, min(w, h) // 2))
        if r <= 0:
            continue

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

        # 外层 L 形裁剪：dist > r（严格大于，弧线上像素保留）
        # 对于 tl/tr/bl/br 各角，此条件裁剪圆弧外侧的 L 形区域
        # 弧线上的像素 (dist == r) 保持在遮罩内（值 255）
        # 内层区域（dist < r）保持原样——边框层由 _redraw_border_on_corner 绘制
        outer_cut = (angle >= ang_min) & (angle < ang_max) & (dist > r)
        mask_arr[y1:y2, x1:x2][outer_cut] = 0

    mask = Image.fromarray(mask_arr, mode='L')
    return mask


def apply_border_only_corners(img: Image.Image, corners: dict[str, float],
                               dpi: int = 150, bg_color: tuple = (255, 255, 255),
                               border_width_cm: float = _DEFAULT_BORDER_WIDTH_CM,
                               pre_detected_layers: list[tuple[tuple[int, int, int], int]] = None) -> Image.Image:
    """
    仅对边框区域应用圆角，内部保持直角。
    裁剪后会在圆角弧线上重新绘制边框层，确保边框线跟随圆弧轮廓。

    [新版实现] 使用多层边框动态圆角算法：
    - 检测图像的边框层（颜色、厚度）
    - 每层有效圆角半径：R_eff_i = max(0, R_total - cumulative_i)
    - 半径耗尽的层自动变为直角
    - 内部区域（超过所有边框层累计深度）始终保持直角

    Args:
        img: 输入图片（RGB 模式）
        corners: 四角圆角半径(cm)字典，键为 tl/tr/bl/br
        dpi: DPI
        bg_color: 圆角处背景色
        border_width_cm: 边框宽度(cm)（用于 fallback，当检测不到边框层时使用）
        pre_detected_layers: 预检测的边框层列表 [(color, thickness_px), ...]，
                            如果提供则使用这些数据而不是重新检测

    Returns:
        应用边框圆角后的图片
    """
    w, h = img.size

    # ---- 步骤 1: 获取边框层信息 ----
    if pre_detected_layers:
        # 使用预检测的边框层（从源图检测并按比例缩放）
        border_layers = pre_detected_layers
    else:
        # 从当前图像检测边框层
        border_layers = _get_border_layers_robust(img, bg_color)
    total_border_thickness = sum(t for _, t in border_layers) if border_layers else 0

    # 计算最大圆角半径（像素）
    max_r = 0
    for radius_cm in corners.values():
        if radius_cm > max_r:
            max_r = radius_cm
    max_r_px = max(1, int(round(max_r * dpi / 2.54)))

    # 构建 corners_px 字典
    corners_px = {}
    for corner_key, radius_cm in corners.items():
        if radius_cm <= 0:
            continue
        corners_px[corner_key] = max(1, int(round(radius_cm * dpi / 2.54)))

    # ---- 安全检查：边框深度不能超过图像一半 ----
    # 使用检测到的边框层总厚度或默认边框宽度
    effective_border_depth = max(total_border_thickness,
                                 int(round(border_width_cm * dpi / 2.54)))

    # 如果边框深度超过图像一半，退化为整体圆角
    # 注意：对于预检测的边框层，跳过此检查（因为预检测的厚度是准确的）
    if not pre_detected_layers and (effective_border_depth * 2 >= w or effective_border_depth * 2 >= h):
        logger.warning(
            "apply_border_only_corners: effective_border_depth=%d 超过半图（w=%d,h=%d），"
            "退化为整体圆角。检测层数=%d, 总检测厚度=%dpx",
            effective_border_depth, w, h, len(border_layers), total_border_thickness,
        )
        return apply_rounded_corners(img, corners, dpi, bg_color)

    # ---- 步骤 2: 构建多层边框动态圆角遮罩 ----
    if corners_px:
        # 使用新的多层动态圆角算法
        final_mask = _build_multi_layer_corner_mask(
            w, h, corners_px, border_layers
        )
    else:
        # Fallback：没有检测到边框层或没有圆角时，使用原逻辑
        logger.info("apply_border_only_corners: 未检测到边框层或无圆角，使用 fallback 逻辑")
        border_w_px = max(1, int(round(border_width_cm * dpi / 2.54)))

        if border_w_px < max_r_px:
            border_w_px = max_r_px

        full_mask = Image.new('L', (w, h), 255)
        carve_corner_on_mask(full_mask, (0, 0, w, h), corners_px, canvas_size=(w, h))

        inner_mask = Image.new('L', (w, h), 0)
        inner_draw = ImageDraw.Draw(inner_mask)
        inner_x1 = max(0, min(border_w_px, w - 2))
        inner_y1 = max(0, min(border_w_px, h - 2))
        inner_x2 = max(inner_x1 + 1, min(w - 1, w - border_w_px))
        inner_y2 = max(inner_y1 + 1, min(h - 1, h - border_w_px))
        inner_draw.rectangle([inner_x1, inner_y1, inner_x2, inner_y2], fill=255)

        zero_img = Image.new('L', (w, h), 0)
        border_region_mask = Image.composite(zero_img, full_mask, inner_mask)
        final_mask = Image.composite(inner_mask, border_region_mask, inner_mask)

    # ---- 步骤 3: 应用遮罩 ----
    result = Image.new('RGB', (w, h), bg_color)
    result.paste(img, mask=final_mask)

    # ---- 步骤 4: 在圆角弧线上重新绘制边框层 ----
    if border_layers and corners_px:
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
