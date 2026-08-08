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
    在圆角裁剪后的结果上，重新绘制最外层的边框线，确保边框在圆角处连续。
    
    Args:
        result_img: 裁剪后的结果图像 (将被原地修改)
        src_img: 原始图像 (用于获取边框颜色)
        corners_px: 四角圆角半径字典
        border_layers: 边框层列表
        validity_mask: 有效性遮罩
        bg_color: 背景色
    """
    if not border_layers or not corners_px:
        return
    
    w, h = result_img.size
    arr = np.array(result_img, dtype=np.uint8)
    mask_arr = np.array(validity_mask, dtype=np.uint8)
    
    # 只处理最外层的边框
    outer_color, outer_thickness = border_layers[0]
    
    # 检查边框颜色是否与背景色相似（如果相似，说明可能是误检测，不绘制）
    color_dist = np.sqrt(sum((a - b) ** 2 for a, b in zip(outer_color, bg_color)))
    if color_dist < 30:
        return
    
    # 检查边框厚度是否合理（如果太厚，说明可能是误检测，不绘制）
    # 边框厚度不应超过图像短边的 10%
    max_reasonable_thickness = min(w, h) * 0.1
    if outer_thickness > max_reasonable_thickness:
        return
    
    outer_color_arr = np.array(outer_color, dtype=np.uint8)
    
    for corner_key, r_px in corners_px.items():
        if r_px <= 0:
            continue
        
        # 计算该角的圆心位置
        if corner_key == 'tl':
            cx, cy = r_px, r_px
        elif corner_key == 'tr':
            cx, cy = w - r_px, r_px
        elif corner_key == 'bl':
            cx, cy = r_px, h - r_px
        else:  # br
            cx, cy = w - r_px, h - r_px
        
        # 计算处理区域（扩展到圆角外边缘 + 边框厚度）
        x1 = max(0, cx - r_px - outer_thickness - 5)
        y1 = max(0, cy - r_px - outer_thickness - 5)
        x2 = min(w, cx + r_px + outer_thickness + 5)
        y2 = min(h, cy + r_px + outer_thickness + 5)
        
        if x2 <= x1 or y2 <= y1:
            continue
        
        # 创建坐标网格
        yy, xx = np.mgrid[y1:y2, x1:x2].astype(np.float64)
        dx = xx - float(cx)
        dy = yy - float(cy)
        dist = np.sqrt(dx * dx + dy * dy)
        
        # 计算角度
        angle = np.degrees(np.arctan2(dy, dx))
        angle = np.mod(angle, 360.0)
        
        ang_min, ang_max = CORNER_ANGLES[corner_key]
        
        # 条件1: 在圆角的角度范围内（扩展5度以确保覆盖到直边连接点）
        in_angle = (angle >= ang_min - 5) & (angle <= ang_max + 5)
        
        # 条件2: 在最外层边框的环形区域
        # 外边: dist <= r_px + border_thickness + 3 (圆角外缘，考虑边框厚度)
        # 内边: dist >= r_px - outer_thickness - 1 (最外层边框的内缘)
        outer_bound = r_px + outer_thickness + 3
        inner_bound = max(0, r_px - outer_thickness - 1)
        in_ring = (dist <= outer_bound) & (dist >= inner_bound)
        
        # 条件3: 在有效性遮罩内，或者在圆角外缘附近（允许绘制到边缘）
        valid_region = mask_arr[y1:y2, x1:x2] > 0
        
        # 圆角外缘附近的区域（允许绘制，不受mask限制）
        near_outer_edge = (dist >= r_px - outer_thickness - 1) & (dist <= r_px + outer_thickness + 3)
        
        # 综合条件：在mask内 OR 在圆角外缘附近
        should_draw = in_angle & in_ring & (valid_region | near_outer_edge)
        
        if np.any(should_draw):
            # 绘制最外层边框颜色
            arr[y1:y2, x1:x2][should_draw] = outer_color_arr
    
    # 回写结果
    result_img.paste(Image.fromarray(arr, 'RGB'))


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

    # 在圆角边缘重新绘制最外层边框，确保边框在圆角处连续
    if border_layers and corners_px:
        _redraw_outer_border_on_corners(
            result, img, corners_px, border_layers, mask, bg_color
        )

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
    
    [关键修正]：采用 Mask 剪切策略 + 重绘最外层边框
    - 生成圆角 Mask 裁切原图
    - 在圆角边缘重新绘制最外层边框，确保边框在圆角处连续
    - 内部线条保持直线，被圆角自然截断
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

    # 创建一个全黑的遮罩 (0 = 全透明)
    mask = Image.new('L', (w, h), 0)
    draw = ImageDraw.Draw(mask)

    # 确定各角半径
    tl_r = corners_px.get('tl', 0)
    tr_r = corners_px.get('tr', 0)
    br_r = corners_px.get('br', 0)
    bl_r = corners_px.get('bl', 0)
    
    # 兼容旧版 PIL
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

    # 在圆角边缘重新绘制最外层边框，确保边框在圆角处连续
    if border_layers and corners_px:
        _redraw_outer_border_on_corners(
            result, img, corners_px, border_layers, mask, bg_color
        )

    return result


def _smart_crop(src: Image.Image, target_w_px: int,
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
