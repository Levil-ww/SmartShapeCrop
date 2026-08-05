"""
core/image_cropper.py
图片裁剪服务：等比缩放 + 圆角裁剪 + 命名输出。

裁剪模式：
- cover: 裁剪填满（裁剪到目标比例，可能损失部分内容）
- contain: 留白填充（完整显示，四周留白）
- light_cover: 轻度裁剪（仅裁剪必要部分，最多裁剪阈值）
- auto: 智能模式（自动选择 cover 或 contain）
"""
from __future__ import annotations
import os
from dataclasses import dataclass
import numpy as np
from PIL import Image, ImageDraw

from .image_ops import load_image_rgb, fit_image_to_rect
from .psd_loader import is_psd_file, load_psd_flattened


@dataclass
class CropConfig:
    """裁剪配置"""
    src_path: str = ""                    # 源图路径
    target_w_cm: float = 0.0              # 目标宽度（厘米）
    target_h_cm: float = 0.0              # 目标高度（厘米）
    corners: dict[str, float] | None = None  # 四角圆角半径(cm)，键: tl/tr/bl/br
    mode: str = 'simple_resize'             # simple_resize | cover | contain | light_cover | auto
    dpi: int = 300                        # 输出 DPI
    bg_color: tuple[int, int, int] = (255, 255, 255)  # 背景色（留白/圆角处）
    output_path: str = ""                 # 输出路径（空则返回PIL对象）
    max_crop_ratio: float = 0.15          # light_cover 最大裁剪比例（15%）
    # 自动缩放：是否允许放大源图到比原图更大
    allow_upscale: bool = True


# 圆角模式阈值：圆角半径 >= 此值时采用"整体圆角"，否则采用"仅边框圆角"
BORDER_ONLY_THRESHOLD_CM = 8.5
# 边框圆角模式下的边框宽度（仅此深度范围内应用圆角）
_DEFAULT_BORDER_WIDTH_CM = 1.5
# 边框总深度（覆盖所有边框层的深度）
# 当 radius >= 阈值时，实际裁剪半径 = radius + 此值，以确保所有边框层都被裁掉
BORDER_TOTAL_DEPTH_CM = 2.0

# 圆角处理参数
# [实测] PIL 屏幕坐标系（y 向下）pieslice 角度映射（逆时针方向）：
#   0° = 右, 90° = 下, 180° = 左, 270° = 上
# 思路（两步法）：
#   1. 先把角落 r×r 正方形设为 0（切掉）
#   2. 再用 pieslice 把"图片内部的 1/4 圆"填回 255（保留）
# 这样切掉的是 L 形（正方形减去 1/4 圆），即只切掉尖角，保留圆弧
# 圆心在正方形的"内角"顶点（即图片内部那个角），bbox 以该圆心为中心
_CORNER_PARAMS = {
    # tl: 正方形 [0,0,r,r]，圆心在 (r,r)，填回左上 1/4 圆 (dx<0,dy<0) → 180°→270°
    'tl': lambda w, h, r: ([0, 0, 2*r, 2*r], 180, 270),
    # tr: 正方形 [w-r,0,w,r]，圆心在 (w-r,r)，填回右上 1/4 圆 (dx>0,dy<0) → 270°→360°
    'tr': lambda w, h, r: ([w-2*r, 0, w, 2*r], 270, 360),
    # bl: 正方形 [0,h-r,r,h]，圆心在 (r,h-r)，填回左下 1/4 圆 (dx<0,dy>0) → 90°→180°
    'bl': lambda w, h, r: ([0, h-2*r, 2*r, h], 90, 180),
    # br: 正方形 [w-r,h-r,w,h]，圆心在 (w-r,h-r)，填回右下 1/4 圆 (dx>0,dy>0) → 0°→90°
    'br': lambda w, h, r: ([w-2*r, h-2*r, w, h], 0, 90),
}
# 各角对应的正方形区域
_CORNER_SQUARE = {
    'tl': lambda w, h, r: (0, 0, r, r),
    'tr': lambda w, h, r: (w - r, 0, w, r),
    'bl': lambda w, h, r: (0, h - r, r, h),
    'br': lambda w, h, r: (w - r, h - r, w, h),
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


def apply_rounded_corners(img: Image.Image, corners: dict[str, float], dpi: int = 300, bg_color: tuple = (255, 255, 255)) -> Image.Image:
    """
    对整张图片应用四角圆角裁剪。
    裁剪半径 = 指定的圆角半径（不做扩展）。
    
    当用于大图（radius >= 8.5cm）时，会同步裁掉所有边框层的角落区域。
    当用于小图（radius < 8.5cm）时，建议使用 apply_border_only_corners。
    
    Args:
        img: 输入图片（RGB 模式）
        corners: 四角圆角半径(cm)字典，键为 tl/tr/bl/br
        dpi: DPI，用于将厘米转为像素
        bg_color: 圆角处背景色
    
    Returns:
        应用圆角后的图片
    """
    w, h = img.size
    mask = Image.new('L', (w, h), 255)  # 全不透明（保留原图）
    draw = ImageDraw.Draw(mask)
    
    for corner_key, radius_cm in corners.items():
        if radius_cm <= 0:
            continue
        
        # 直接使用指定的圆角半径（不做扩展）
        r = max(1, int(round(radius_cm * dpi / 2.54)))
        
        get_params = _CORNER_PARAMS.get(corner_key)
        get_square = _CORNER_SQUARE.get(corner_key)
        if get_params is None or get_square is None:
            continue
        
        # 1. 先把角落 r×r 正方形设为 0（切掉尖角）
        sq = get_square(w, h, r)
        sq_safe = [max(0, sq[0]), max(0, sq[1]), min(w, sq[2]), min(h, sq[3])]
        if sq_safe[2] > sq_safe[0] and sq_safe[3] > sq_safe[1]:
            draw.rectangle(sq_safe, fill=0)
        
        # 2. 用 pieslice 把图片内部的 1/4 圆填回 255（保留圆弧）
        bbox, start_deg, end_deg = get_params(w, h, r)
        safe_bbox = [max(0, bbox[0]), max(0, bbox[1]), min(w, bbox[2]), min(h, bbox[3])]
        if safe_bbox[2] > safe_bbox[0] and safe_bbox[3] > safe_bbox[1]:
            draw.pieslice(safe_bbox, start=start_deg, end=end_deg, fill=255)
    
    # 应用遮罩
    result = Image.new('RGB', (w, h), bg_color)
    result.paste(img, mask=mask)
    return result


def apply_concentric_rounded_corners(img: Image.Image, corners: dict[str, float],
                                      dpi: int = 300, bg_color: tuple = (255, 255, 255)) -> Image.Image:
    """
    漏斗形多层同步圆角裁剪：对整张图片应用圆角，所有层次的边框/装饰都会
    同步被裁掉，且保持同心圆角效果。
    
    核心算法（以右下角为例）：
      设 R 为圆角半径，对于每个像素：
        dx = 距右边缘的像素距离
        dy = 距下边缘的像素距离
        d_max = max(dx, dy)
        d_euclid = sqrt(dx² + dy²)
      裁剪条件：d_max + d_euclid >= R → 裁掉该像素
      
      这个公式的效果：
      - 在角落处裁掉最宽的 L 形区域
      - 裁剪深度随向内的距离线性递减
      - 所有层次的边框都会同步被裁掉（因为它们在角落的"有效半径"递减）
      - 所有层的圆弧都是同心的（共享同一个角点作为圆心）
    
    Args:
        img: 输入图片（RGB 模式）
        corners: 四角圆角半径(cm)字典，键为 tl/tr/bl/br
        dpi: DPI
        bg_color: 圆角处背景色
    
    Returns:
        应用多层同步圆角后的图片
    """
    w, h = img.size
    mask = Image.new('L', (w, h), 255)  # 全不透明（保留原图）
    mask_arr = np.ones((h, w), dtype=np.uint8) * 255
    
    # 创建坐标网格
    y_coords = np.arange(h, dtype=np.float64)
    x_coords = np.arange(w, dtype=np.float64)
    yy, xx = np.meshgrid(y_coords, x_coords, indexing='ij')
    
    for corner_key, radius_cm in corners.items():
        if radius_cm <= 0:
            continue
        
        r_px = max(1, int(round(radius_cm * dpi / 2.54)))
        
        # 计算每个像素到该角的 dx, dy
        if corner_key == 'tl':
            dx = xx  # 距左边缘
            dy = yy  # 距上边缘
        elif corner_key == 'tr':
            dx = w - 1 - xx  # 距右边缘
            dy = yy  # 距上边缘
        elif corner_key == 'bl':
            dx = xx  # 距左边缘
            dy = h - 1 - yy  # 距下边缘
        else:  # br
            dx = w - 1 - xx  # 距右边缘
            dy = h - 1 - yy  # 距下边缘
        
        # 漏斗形裁剪条件：d_max + d_euclid >= R → 裁掉
        d_max = np.maximum(dx, dy)
        d_euclid = np.sqrt(dx**2 + dy**2)
        cut_mask = (d_max + d_euclid) >= r_px
        
        # 裁掉的像素设为 0
        mask_arr[cut_mask] = 0
    
    # 应用遮罩
    result = Image.new('RGB', (w, h), bg_color)
    result.paste(img, mask=Image.fromarray(mask_arr, mode='L'))
    return result


def apply_border_only_corners(img: Image.Image, corners: dict[str, float],
                               dpi: int = 300, bg_color: tuple = (255, 255, 255),
                               border_width_cm: float = _DEFAULT_BORDER_WIDTH_CM) -> Image.Image:
    """
    仅对边框区域应用圆角，内部保持直角。
    
    实现思路：
    1. 创建完整的圆角遮罩（对整个图片应用圆角半径）
    2. 创建内部矩形遮罩（距边缘 border_width 的区域，全不透明）
    3. 合并遮罩：边框区域用圆角遮罩，内部用直角遮罩
    
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
    
    # 安全检查：如果边框宽度超过图像一半，退化为整体圆角
    if border_w_px * 2 >= w or border_w_px * 2 >= h:
        return apply_rounded_corners(img, corners, dpi, bg_color)
    
    # 1. 创建完整的圆角遮罩（现有逻辑）
    full_mask = Image.new('L', (w, h), 255)
    full_draw = ImageDraw.Draw(full_mask)
    
    for corner_key, radius_cm in corners.items():
        if radius_cm <= 0:
            continue
        
        r = max(1, int(round(radius_cm * dpi / 2.54)))
        
        get_params = _CORNER_PARAMS.get(corner_key)
        get_square = _CORNER_SQUARE.get(corner_key)
        if get_params is None or get_square is None:
            continue
        
        # 1.1 切正方形（挖空）
        sq = get_square(w, h, r)
        sq_safe = [max(0, sq[0]), max(0, sq[1]), min(w, sq[2]), min(h, sq[3])]
        if sq_safe[2] > sq_safe[0] and sq_safe[3] > sq_safe[1]:
            full_draw.rectangle(sq_safe, fill=0)
        
        # 1.2 填回 1/4 圆
        bbox, start_deg, end_deg = get_params(w, h, r)
        safe_bbox = [max(0, bbox[0]), max(0, bbox[1]), min(w, bbox[2]), min(h, bbox[3])]
        if safe_bbox[2] > safe_bbox[0] and safe_bbox[3] > safe_bbox[1]:
            full_draw.pieslice(safe_bbox, start=start_deg, end=end_deg, fill=255)
    
    # 2. 创建内部矩形遮罩（距边缘 border_w_px 的区域，全不透明）
    inner_mask = Image.new('L', (w, h), 0)
    inner_draw = ImageDraw.Draw(inner_mask)
    inner_rect = [border_w_px, border_w_px, w - border_w_px, h - border_w_px]
    inner_draw.rectangle(inner_rect, fill=255)
    
    # 3. 计算边框区域遮罩 = 全图 - 内部区域（保留圆角效果）
    zero_img = Image.new('L', (w, h), 0)
    border_region_mask = Image.composite(zero_img, full_mask, inner_mask)
    # inner_mask=255(内部)→用zero(透明); inner_mask=0(边框)→用full_mask(有圆角)
    
    # 4. 最终遮罩：内部区域用直角，边框区域用圆角
    final_mask = Image.composite(inner_mask, border_region_mask, inner_mask)
    # inner_mask=255(内部)→用inner_mask(255,直角); inner_mask=0(边框)→用border_region_mask(有圆角)
    
    # 5. 应用遮罩
    result = Image.new('RGB', (w, h), bg_color)
    result.paste(img, mask=final_mask)
    return result


def _determine_corner_mode(corners: dict[str, float]) -> str:
    """
    根据圆角半径判断圆角模式。
    
    Returns:
        'full' = 整体圆角（radius >= 8.5cm）
        'border_only' = 仅边框圆角（radius < 8.5cm）
    """
    max_radius = max((v for v in corners.values()), default=0)
    if max_radius >= BORDER_ONLY_THRESHOLD_CM:
        return 'full'
    return 'border_only'


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
    
    # 4. 应用圆角（根据半径阈值自动选择模式）
    if config.corners:
        valid_corners = {k: v for k, v in config.corners.items() if v > 0}
        if valid_corners:
            corner_mode = _determine_corner_mode(valid_corners)
            if corner_mode == 'full':
                # 大圆角（半径 >= 8.5cm）：使用多层统一圆角裁剪
                # 自动识别嵌套边框层，对每一层都应用相同圆角，使用 AND 逻辑组合
                cropped = apply_multi_layer_rounded_corners(
                    cropped, valid_corners, config.dpi, config.bg_color)
            else:
                # 小圆角（半径 < 8.5cm）：仅边框区域应用圆角
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


# ============================================================
# 多层边框自动检测 + 统一圆角裁剪（解决大圆角时内层图案露角问题）
# ============================================================

# 多层边框检测参数
_BORDER_SCAN_STEP = 2          # 扫描步长（像素），步长越大越快但越不精确
_BORDER_COLOR_DIFF_THRESHOLD = 25  # 颜色差异阈值（0-255），超过视为边框边界
_BORDER_MIN_GAP_PX = 5         # 相邻两个边界之间的最小间距（小于此值视为同一条边界）
_BORDER_MAX_LAYERS = 10        # 最多检测层数上限，防止误检过多
_EDGE_IGNORE_PX = 2            # 忽略最边缘几个像素（避免最外白边/黑边干扰）


def _scan_edge_boundaries(img_arr: np.ndarray,
                          edge: str,
                          max_depth_pct: float = 0.45) -> list[int]:
    """
    从某一条边向内扫描，检测颜色突变的边界位置。

    Args:
        img_arr: H×W×3 的 numpy 数组（RGB）
        edge: 'top' | 'bottom' | 'left' | 'right'
        max_depth_pct: 最大扫描深度占图片宽/高的比例（默认 45%，避免扫到中心）

    Returns:
        边界位置列表（像素坐标），从外向内排序。
        对于 top/bottom: 坐标是 y 值（行号）
        对于 left/right: 坐标是 x 值（列号）
    """
    H, W = img_arr.shape[:2]
    if edge in ('top', 'bottom'):
        axis_len = H
        perp_len = W
        max_depth = int(axis_len * max_depth_pct)
    else:
        axis_len = W
        perp_len = H
        max_depth = int(axis_len * max_depth_pct)

    max_depth = max(max_depth, 20)  # 至少扫描 20px

    # 取一条垂/水平中线上的像素，计算每一步的颜色变化
    # 为更鲁棒，取中线 ±10% 范围内几条线的平均
    perp_mid = perp_len // 2
    perp_span = max(1, int(perp_len * 0.1))
    sample_lines = [perp_mid - perp_span, perp_mid, perp_mid + perp_span]
    sample_lines = [max(_EDGE_IGNORE_PX, min(perp_len - 1 - _EDGE_IGNORE_PX, s)) for s in sample_lines]

    # 构造扫描顺序：从外到内的索引序列
    if edge == 'top':
        indices = list(range(_EDGE_IGNORE_PX, min(axis_len - _EDGE_IGNORE_PX, _EDGE_IGNORE_PX + max_depth), _BORDER_SCAN_STEP))
    elif edge == 'bottom':
        indices = list(range(axis_len - 1 - _EDGE_IGNORE_PX,
                             max(_EDGE_IGNORE_PX, axis_len - 1 - _EDGE_IGNORE_PX - max_depth),
                             -_BORDER_SCAN_STEP))
    elif edge == 'left':
        indices = list(range(_EDGE_IGNORE_PX, min(axis_len - _EDGE_IGNORE_PX, _EDGE_IGNORE_PX + max_depth), _BORDER_SCAN_STEP))
    else:  # right
        indices = list(range(axis_len - 1 - _EDGE_IGNORE_PX,
                             max(_EDGE_IGNORE_PX, axis_len - 1 - _EDGE_IGNORE_PX - max_depth),
                             -_BORDER_SCAN_STEP))

    if len(indices) < 3:
        return []

    # 对每条采样线计算颜色强度（R+G+B）随深度的变化
    all_diff = np.zeros(len(indices), dtype=np.float64)
    for line_pos in sample_lines:
        values = []
        for idx in indices:
            if edge in ('top', 'bottom'):
                px = img_arr[idx, line_pos, :].astype(np.float64)
            else:
                px = img_arr[line_pos, idx, :].astype(np.float64)
            values.append(px.sum())  # 用亮度总和衡量
        values = np.array(values, dtype=np.float64)
        # 计算一阶差分绝对值
        if len(values) > 1:
            diff = np.abs(np.diff(values))
            # 差分比索引短1，末尾补0对齐
            diff = np.concatenate([diff, [0.0]])
            all_diff += diff

    # 平均化
    all_diff /= len(sample_lines)

    # 寻找显著的差分峰值（颜色突变点 = 边界）
    threshold = _BORDER_COLOR_DIFF_THRESHOLD * 3  # 因为用的是 R+G+B 总和
    peak_indices = []
    for i in range(1, len(all_diff) - 1):
        if (all_diff[i] >= threshold
                and all_diff[i] >= all_diff[i - 1]
                and all_diff[i] >= all_diff[i + 1]):
            peak_indices.append(i)

    # 把峰值对应的"扫描索引位置"转换为实际像素坐标，并按距离外边缘从小到大排序
    boundaries_px = []
    seen = set()
    for pi in peak_indices:
        actual = indices[pi]
        # 与已有的边界距离过近则合并
        too_close = False
        for b in boundaries_px:
            if abs(actual - b) < _BORDER_MIN_GAP_PX:
                too_close = True
                break
        if not too_close and actual not in seen:
            boundaries_px.append(actual)
            seen.add(actual)

    # 统一排序：按"距最外边缘"由近到远
    if edge == 'top':
        boundaries_px.sort()
    elif edge == 'bottom':
        boundaries_px.sort(reverse=True)
    elif edge == 'left':
        boundaries_px.sort()
    else:  # right
        boundaries_px.sort(reverse=True)

    return boundaries_px[:_BORDER_MAX_LAYERS]


def detect_nested_rect_layers(img: Image.Image) -> list[tuple[int, int, int, int]]:
    """
    自动检测图片中嵌套的矩形边框层。

    原理：
      分别从上/下/左/右 4 条边向内扫描颜色突变点（边界），
      然后将 4 条边上的边界位置两两配对，构成嵌套矩形。

    Args:
        img: PIL RGB 图片

    Returns:
        列表：[(x1, y1, x2, y2), ...]  从最外层往最内层排序；
        如果检测失败，返回只包含整图外框的一层。
    """
    w, h = img.size
    arr = np.array(img, dtype=np.uint8)

    top_ys = _scan_edge_boundaries(arr, 'top')
    bottom_ys = _scan_edge_boundaries(arr, 'bottom')
    left_xs = _scan_edge_boundaries(arr, 'left')
    right_xs = _scan_edge_boundaries(arr, 'right')

    # 每一层必须有 4 条边共同对应，因此取最少边数
    n_layers = min(len(top_ys), len(bottom_ys), len(left_xs), len(right_xs))

    # 第 0 层 = 最外层边框（扫描到的第一条边）
    # 第 1 层 = 往里的第二条，……
    rects = []
    for i in range(n_layers):
        x1 = left_xs[i]
        y1 = top_ys[i]
        x2 = right_xs[i]
        y2 = bottom_ys[i]
        # 坐标合法性检查（左<右 且 上<下，且有足够面积）
        if x2 - x1 > 20 and y2 - y1 > 20:
            rects.append((x1, y1, x2, y2))

    # 如果没检测到任何层，退化为整图
    if not rects:
        rects.append((0, 0, w - 1, h - 1))

    return rects


def _carve_L_corners_on_mask(draw: ImageDraw.ImageDraw,
                              canvas_w: int, canvas_h: int,
                              rect: tuple[int, int, int, int],
                              corners_px: dict[str, int]) -> None:
    """
    在一张已有的 L 模式 mask 图上，对指定矩形的四个角刻出 L 形圆角裁掉区（置为 0）。
    使用与全局 apply_rounded_corners 完全一致的两步法算法，直接原地修改。

    算法（单角）：
      1) 先把该角 r×r 正方形 挖空（fill=0）
      2) 再把矩形内部的 1/4 圆 填回（fill=255）
    只修改指定矩形相关的角，不影响 mask 其他区域。
    层层调用此函数 = 层层叠加裁掉区，最终实现所有嵌套矩形的尖角统一圆角。
    """
    x1, y1, x2, y2 = rect
    rw = x2 - x1
    rh = y2 - y1

    safe_corners = {}
    for ck, r_px in corners_px.items():
        max_r = max(1, min(rw, rh) // 2)
        safe_corners[ck] = max(0, min(r_px, max_r))

    for ck, r_px in safe_corners.items():
        if r_px <= 0:
            continue

        if ck == 'tl':
            sq = [x1, y1, x1 + r_px, y1 + r_px]
            pieslice_bbox = [x1, y1, x1 + 2 * r_px, y1 + 2 * r_px]
            start, end = 180, 270
        elif ck == 'tr':
            sq = [x2 - r_px, y1, x2, y1 + r_px]
            pieslice_bbox = [x2 - 2 * r_px, y1, x2, y1 + 2 * r_px]
            start, end = 270, 360
        elif ck == 'bl':
            sq = [x1, y2 - r_px, x1 + r_px, y2]
            pieslice_bbox = [x1, y2 - 2 * r_px, x1 + 2 * r_px, y2]
            start, end = 90, 180
        else:  # br
            sq = [x2 - r_px, y2 - r_px, x2, y2]
            pieslice_bbox = [x2 - 2 * r_px, y2 - 2 * r_px, x2, y2]
            start, end = 0, 90

        # 1. 挖掉 r×r 正方形（设为 0 = 裁掉）
        sq_safe = [max(0, sq[0]), max(0, sq[1]),
                   min(canvas_w, sq[2]), min(canvas_h, sq[3])]
        if sq_safe[2] > sq_safe[0] and sq_safe[3] > sq_safe[1]:
            draw.rectangle(sq_safe, fill=0)

        # 2. 填回矩形内部的 1/4 圆（设为 255 = 保留）
        safe_bbox = [max(0, pieslice_bbox[0]), max(0, pieslice_bbox[1]),
                     min(canvas_w, pieslice_bbox[2]), min(canvas_h, pieslice_bbox[3])]
        if safe_bbox[2] > safe_bbox[0] and safe_bbox[3] > safe_bbox[1]:
            draw.pieslice(safe_bbox, start=start, end=end, fill=255)


def _layer_rounded_mask_arr(canvas_w: int, canvas_h: int,
                            rect_canvas: tuple[int, int, int, int],
                            corners_px: dict[str, int]) -> np.ndarray:
    """
    为一个矩形生成"该层的圆角裁剪 mask（numpy 数组 L，255=保留 0=裁掉）"。

    rect_canvas: 画布尺寸语义 (x1, y1, x2_canvas_size, y2_canvas_size)
                 ——即 x2 = x1 + width，y2 = y1 + height（不包含终点像素索引）。
    算法与全局 apply_rounded_corners 完全一致：正方形挖空→扇形填回。
    """
    x1, y1, x2, y2 = rect_canvas
    rw, rh = x2 - x1, y2 - y1
    safe_corners = {}
    for ck, r_px in corners_px.items():
        max_r = max(1, min(rw, rh) // 2)
        safe_corners[ck] = max(0, min(r_px, max_r))
    mask = Image.new('L', (canvas_w, canvas_h), 255)
    draw = ImageDraw.Draw(mask)
    for ck, r_px in safe_corners.items():
        if r_px <= 0: continue
        if ck == 'tl':
            sq = [x1, y1, x1 + r_px, y1 + r_px]
            pb = [x1, y1, x1 + 2 * r_px, y1 + 2 * r_px]; s, e = 180, 270
        elif ck == 'tr':
            sq = [x2 - r_px, y1, x2, y1 + r_px]
            pb = [x2 - 2 * r_px, y1, x2, y1 + 2 * r_px]; s, e = 270, 360
        elif ck == 'bl':
            sq = [x1, y2 - r_px, x1 + r_px, y2]
            pb = [x1, y2 - 2 * r_px, x1 + 2 * r_px, y2]; s, e = 90, 180
        else:
            sq = [x2 - r_px, y2 - r_px, x2, y2]
            pb = [x2 - 2 * r_px, y2 - 2 * r_px, x2, y2]; s, e = 0, 90
        sq_s = [max(0, sq[0]), max(0, sq[1]), min(canvas_w, sq[2]), min(canvas_h, sq[3])]
        if sq_s[2] > sq_s[0] and sq_s[3] > sq_s[1]:
            draw.rectangle(sq_s, fill=0)
        pb_s = [max(0, pb[0]), max(0, pb[1]), min(canvas_w, pb[2]), min(canvas_h, pb[3])]
        if pb_s[2] > pb_s[0] and pb_s[3] > pb_s[1]:
            draw.pieslice(pb_s, start=s, end=e, fill=255)
    return np.array(mask, dtype=np.uint8)


def apply_multi_layer_rounded_corners(img: Image.Image,
                                      corners_cm: dict[str, float],
                                      dpi: int = 150,
                                      bg_color: tuple = (255, 255, 255),
                                      debug: bool = False) -> Image.Image:
    """
    多层级统一圆角裁剪：自动识别所有嵌套边框层，对每一层都应用相同圆角。

    解决大圆角（如 8cm）时只裁最外层、内层图案漏出尖角的问题（即用户图 1 现象）。

    最终可见区域 = 所有层各自圆角保留区域的 **交集（AND / 逐点 min）**：
      任一层的 L 形尖角区（应裁掉=0）→ 最终图必然裁掉；
      只有所有层都允许保留（=255）的位置，最终图才保留原图色。
    这等价于"裁掉所有层各自的尖角的并集"，恰好符合『每层边框都同步裁圆角』的语义。

    实现步骤：
      1. 初始化 unified_mask = 全 255（全部保留）
      2. 对"整张图外框(0,0,w,h)"先算一层 mask，unified_mask = min(unified_mask, mask)
         ——保证最外层绝对生效（即使检测没扫到最外层黑框）
      3. 自动检测嵌套矩形层（外黑框 / 红框 / 花纹边缘 / 文字框 / 中心黑框…）
      4. 过滤误检：尺寸过小或过于狭长（宽高比失衡，典型是单行文字或细横线误检）
      5. 对每层矩形：
         - 将『像素索引语义』的 x2,y2 各 +1 转为『画布尺寸语义』
         - 生成该层圆角 mask（正方形挖空 + sector 填回）
         - unified_mask = 逐点 min（AND）累加
      6. 应用最终 unified_mask 得到输出图

    Args:
        img: 输入图（RGB）
        corners_cm: 四角圆角（厘米），如 {'br': 8.0}
        dpi: DPI
        bg_color: 背景色
        debug: True 时在输出图上叠加彩色框标出检测到的各层矩形（肉眼核对层数是否准确）

    Returns:
        应用了多层统一圆角的图片
    """
    w, h = img.size
    if w <= 0 or h <= 0:
        return img

    # 转换圆角半径为像素（注意：不再用 BORDER_TOTAL_DEPTH_CM 扩展 hack！
    # 因为我们靠『每层矩形独立裁角 + AND』覆盖了所有层，不再需要扩大最外层半径蒙混）
    corners_px = {}
    for ck, r_cm in corners_cm.items():
        r_px = max(0, int(round(r_cm * dpi / 2.54)))
        corners_px[ck] = r_px
    if all(r <= 0 for r in corners_px.values()):
        return img

    # ---- 1. 初始化统一 mask（全保留） ----
    unified = np.ones((h, w), dtype=np.uint8) * 255

    # 2. 整图外框先裁一轮（保证最外层绝对生效）
    full_rect = (0, 0, w, h)
    unified = np.minimum(unified, _layer_rounded_mask_arr(w, h, full_rect, corners_px))

    # 3. 自动检测嵌套矩形层
    layers = detect_nested_rect_layers(img)

    # 4. 过滤误检：过于狭长（宽高比失衡）或尺寸过小的矩形丢弃
    filtered_layers = []
    for (x1, y1, x2_last_idx, y2_last_idx) in layers:
        bw, bh = x2_last_idx - x1, y2_last_idx - y1
        if bw <= 40 or bh <= 40:
            continue
        ratio = bw / max(1, bh)
        if ratio > 12 or ratio < 1 / 12:
            continue
        filtered_layers.append((x1, y1, x2_last_idx, y2_last_idx))
    layers = filtered_layers

    # ---- 5. 层层 AND：任何一层要裁掉的位置，最终都裁掉 ----
    for (x1, y1, x2_idx, y2_idx) in layers:
        # 像素索引语义 → 画布尺寸语义（x2 = x1 + width）
        rect_x2 = x2_idx + 1
        rect_y2 = y2_idx + 1

        # 计算该层的有效圆角半径：根据该矩形到图片边缘的距离递减
        # 关键修复：使用 max(t_x, t_y) 而非 min(t_x, t_y)！
        #   - 如果用 min：当某边距很小（如5px）另一边距大（如80px），会导致 r_layer 过大，
        #     进而使内层挖掉的正方形在"另一个方向"侵入外层保留区 → 白色扇形角
        #   - 如果用 max：r_layer = R - max(tx, ty)，内层挖掉正方形必然被包在外层挖掉正方形内部，
        #     同时内层真正的尖角顶点（必然在外层保留区内）也会被正确裁掉。
        layer_corners_px = {}
        for ck, r_px in corners_px.items():
            if r_px <= 0:
                layer_corners_px[ck] = 0
                continue
            if ck == 'tl':
                t_x = x1                  # 左边距：图片左边缘 → 内层矩形左边
                t_y = y1                  # 上边距：图片上边缘 → 内层矩形上边
                dist = max(t_x, t_y)
            elif ck == 'tr':
                t_x = w - rect_x2         # 右边距：内层矩形右边 → 图片右边缘
                t_y = y1                  # 上边距
                dist = max(t_x, t_y)
            elif ck == 'bl':
                t_x = x1                  # 左边距
                t_y = h - rect_y2         # 下边距：内层矩形下边 → 图片下边缘
                dist = max(t_x, t_y)
            else:  # br
                t_x = w - rect_x2         # 右边距
                t_y = h - rect_y2         # 下边距
                dist = max(t_x, t_y)
            layer_corners_px[ck] = max(0, r_px - dist)

        rect = (x1, y1, rect_x2, rect_y2)
        unified = np.minimum(unified, _layer_rounded_mask_arr(w, h, rect, layer_corners_px))

    # ---- 6. 应用最终遮罩 ----
    final_mask = Image.fromarray(unified, mode='L')
    if debug:
        debug_img = img.copy()
        d = ImageDraw.Draw(debug_img)
        colors = [(255, 0, 0), (0, 0, 255), (0, 150, 0),
                  (255, 140, 0), (150, 0, 180), (220, 20, 147)]
        for i, (x1, y1, x2_idx, y2_idx) in enumerate(layers):
            c = colors[i % len(colors)]
            lw = max(2, w // 600)
            d.rectangle([x1, y1, x2_idx, y2_idx], outline=c, width=lw)
        result = Image.new('RGB', (w, h), bg_color)
        result.paste(debug_img, mask=final_mask)
        return result

    result = Image.new('RGB', (w, h), bg_color)
    result.paste(img, mask=final_mask)
    return result