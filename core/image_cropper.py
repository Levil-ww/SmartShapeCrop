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
    mode: str = 'light_cover'             # cover | contain | light_cover | auto
    dpi: int = 300                        # 输出 DPI
    bg_color: tuple[int, int, int] = (255, 255, 255)  # 背景色（留白/圆角处）
    output_path: str = ""                 # 输出路径（空则返回PIL对象）
    max_crop_ratio: float = 0.15          # light_cover 最大裁剪比例（15%）
    # 自动缩放：是否允许放大源图到比原图更大
    allow_upscale: bool = True


# 圆角处理参数
# [实测] PIL 屏幕坐标系（y 向下）pieslice 角度映射（逆时针方向）：
#   0° = 右, 90° = 下, 180° = 左, 270° = 上
# 注：PIL 文档称 "90° is up"，但在屏幕坐标系（y向下）中实际表现为 90°=下
# 思路：mask 初始 255（保留原图），将"要切掉的圆角扇形" fill=0（显示背景色）
# 每个圆角 = 以图片角落顶点为圆心、r 为半径的 1/4 圆（在图片内部那一侧）
_CORNER_CUT_PARAMS = {
    # 左上角顶点(0,0)：切掉右下 1/4 圆（dx>0, dy>0），角度 0(右)→90(下)
    'tl': (0, 90),
    # 右上角顶点(w-1,0)：切掉左下 1/4 圆（dx<0, dy>0），角度 90(下)→180(左)
    'tr': (90, 180),
    # 左下角顶点(0,h-1)：切掉右上 1/4 圆（dx>0, dy<0），角度 270(上)→360(右)
    'bl': (270, 360),
    # 右下角顶点(w-1,h-1)：切掉左上 1/4 圆（dx<0, dy<0），角度 180(左)→270(上)
    'br': (180, 270),
}
_CORNER_VERTEX = {
    'tl': lambda w, h: (0, 0),
    'tr': lambda w, h: (w - 1, 0),
    'bl': lambda w, h: (0, h - 1),
    'br': lambda w, h: (w - 1, h - 1),
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
    对图片应用四角圆角裁剪。
    
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
        
        r = max(1, int(round(radius_cm * dpi / 2.54)))
        
        # 获取角度参数
        angles = _CORNER_CUT_PARAMS.get(corner_key)
        vertex_fn = _CORNER_VERTEX.get(corner_key)
        if angles is None or vertex_fn is None:
            continue
        
        # 角落顶点为圆心，构造 2r x 2r 的 bbox（圆心在 bbox 正中心）
        cx, cy = vertex_fn(w, h)
        bbox = [cx - r, cy - r, cx + r, cy + r]
        start_deg, end_deg = angles
        
        # 直接画 pieslice(fill=0)：即把"要切掉的圆角"设为透明（显示背景色）
        draw.pieslice(bbox, start=start_deg, end=end_deg, fill=0)
    
    # 应用遮罩
    result = Image.new('RGB', (w, h), bg_color)
    result.paste(img, mask=mask)
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
    
    if mode == 'auto':
        cropped = _smart_crop(src, target_w_px, target_h_px, config.max_crop_ratio, bg_color)
    elif mode == 'light_cover':
        cropped = _light_cover(src, target_w_px, target_h_px, config.max_crop_ratio, bg_color)
    elif mode == 'contain':
        cropped = fit_image_to_rect(src, target_w_px, target_h_px, mode='contain', bg_color=bg_color)
    else:  # cover
        cropped = fit_image_to_rect(src, target_w_px, target_h_px, mode='cover', bg_color=bg_color)
    
    # 4. 应用圆角
    if config.corners:
        valid_corners = {k: v for k, v in config.corners.items() if v > 0}
        if valid_corners:
            cropped = apply_rounded_corners(cropped, valid_corners, config.dpi, config.bg_color)
    
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
        'cover': '裁剪填满：裁剪图片填满目标尺寸，可能损失边缘内容',
        'contain': '留白填充：完整显示图片，四周可能留白',
        'light_cover': '轻度裁剪：优先裁剪，裁剪量过大时自动改为留白',
        'auto': '智能模式：自动分析源图和目标比例差异，选择最佳方式',
    }
    return descriptions.get(mode, mode)