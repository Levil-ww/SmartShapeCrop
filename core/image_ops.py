"""
core/image_ops.py
图像层面操作：素材加载/缩放/平铺填充/边框合成/文字环绕/导出 JPG
核心原则：预览显示仅缩放，渲染与保存始终使用 canvas_w_px × canvas_h_px 全尺寸。
"""
from __future__ import annotations
import os
import logging
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

# 提高像素上限：业务常处理印刷级超大图（如 EPS 栅格化后超过 1 亿像素），
# 默认 89478485 像素会触发 DecompressionBombWarning。
# 改为 2 亿像素上限（约 14142 × 14142），既能覆盖最大印刷图，又能防御恶意超大图。
Image.MAX_IMAGE_PIXELS = 200_000_000

from .geometry import CropDesign, compute_border_bands


# ---------- 素材加载与适配 ----------

def load_image_rgb(path: str) -> Image.Image:
    """加载素材图为 RGB 模式（JPG 通常无 alpha，转 RGB 方便合成）"""
    img = Image.open(path)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    return img


def fit_image_to_rect(src_img: Image.Image,
                      target_w: int, target_h: int,
                      mode: str = 'cover',
                      bg_color: tuple[int, int, int] = (255, 255, 255),
                      quality: str = 'export') -> Image.Image:
    """
    将素材图适配到目标矩形尺寸。
    mode:
      - 'cover': 按比例缩放并居中裁剪，填满目标（不拉伸，用于边框花纹）
      - 'contain': 按比例缩放放入目标，剩余区域用 bg_color 补齐
      - 'stretch': 直接拉伸到目标尺寸（慎用）
      - 'tile': 平铺重复填满目标区域（用于瓷砖花纹）
    quality:
      - 'export' (默认): LANCZOS 重采样，最终导出用，质量最高
      - 'preview': BILINEAR 重采样，预览刷新用，3-5× 加速，肉眼差异可忽略
    """
    resample = Image.BILINEAR if quality == 'preview' else Image.LANCZOS
    sw, sh = src_img.size
    if sw <= 0 or sh <= 0:
        return Image.new('RGB', (target_w, target_h), bg_color)

    if mode == 'tile':
        return _tile_fill(src_img, target_w, target_h)
    if mode == 'stretch':
        return src_img.resize((target_w, target_h), resample)

    scale = max(target_w / sw, target_h / sh) if mode == 'cover' \
        else min(target_w / sw, target_h / sh)
    nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
    resized = src_img.resize((nw, nh), resample)

    if mode == 'cover':
        # 居中裁剪
        left = (nw - target_w) // 2
        top = (nh - target_h) // 2
        return resized.crop((left, top, left + target_w, top + target_h))
    else:  # contain
        canvas = Image.new('RGB', (target_w, target_h), bg_color)
        canvas.paste(resized, ((target_w - nw) // 2, (target_h - nh) // 2))
        return canvas


def _tile_fill(src_img: Image.Image, tw: int, th: int) -> Image.Image:
    """平铺素材填满目标区域"""
    out = Image.new('RGB', (tw, th))
    sw, sh = src_img.size
    for y in range(0, th, sh):
        for x in range(0, tw, sw):
            out.paste(src_img, (x, y))
    return out


def load_and_fit(path: str, tw: int, th: int, mode: str = 'cover',
                 quality: str = 'export') -> Image.Image:
    """加载 + 适配 二合一（带错误保护，素材丢失时返回纯色占位）"""
    try:
        if not os.path.isfile(path):
            return Image.new('RGB', (tw, th), (220, 220, 220))
        return fit_image_to_rect(load_image_rgb(path), tw, th, mode, quality=quality)
    except Exception as e:
        logger.warning(f"素材加载适配失败 path={path}: {e}")
        return Image.new('RGB', (tw, th), (220, 220, 220))


# ---------- 核心渲染 ----------

def render_design(design: CropDesign, quality: str = 'export') -> Image.Image:
    """
    按 CropDesign 完整渲染一张全尺寸画布（RGB）。
    返回 PIL.Image，大小 = design.canvas_w_px × design.canvas_h_px

    quality:
      - 'export' (默认): LANCZOS 重采样，最终保存导出用
      - 'preview': BILINEAR 重采样，GUI 实时预览刷新用，显著降低大图重采样耗时
    """
    W, H = design.canvas_w_px, design.canvas_h_px
    # 1. 整体背景（最外层）
    #    水池模式优先：如果 pool_outer_material_image 设置了（匹配到的花纹图），整幅铺满
    # [Fix 2026-08-21] 水池模式使用 stretch 模式适配素材（等同 simple_resize）
    #   确保素材图四周边框线完整保留，避免 cover 模式裁剪边框
    #   与圆角裁剪工具默认行为一致（simple_resize 直接拉伸，不裁剪不留白）
    if design.pool_outer_material_image and os.path.isfile(design.pool_outer_material_image):
        canvas = load_and_fit(design.pool_outer_material_image, W, H,
                              mode='tile' if _looks_like_tile(design.pool_outer_material_image) else 'stretch',
                              quality=quality)
    elif design.outer_bg_image and os.path.isfile(design.outer_bg_image):
        canvas = load_and_fit(design.outer_bg_image, W, H,
                              mode='tile' if _looks_like_tile(design.outer_bg_image) else 'cover',
                              quality=quality)
    else:
        canvas = Image.new('RGB', (W, H), design.outer_bg_color)

    canvas_arr = np.array(canvas, dtype=np.uint8)

    # 2. 渲染边框 band（水池模式且有素材图时跳过——素材本身就是外框）
    is_pool_with_material = (design.pool_hole_transparent
                             and design.pool_outer_material_image
                             and os.path.isfile(design.pool_outer_material_image))
    if not is_pool_with_material:
        bands = compute_border_bands(design)
        for band_mask, layer in bands:
            # 该层的填充颜色/图像
            if layer.fill_type == 'image' and layer.image_path and os.path.isfile(layer.image_path):
                mode = 'tile' if layer.tile_mode else 'cover'
                fill_img = load_and_fit(layer.image_path, W, H, mode=mode, quality=quality)
                fill_arr = np.array(fill_img, dtype=np.uint8)
            else:
                fill_arr = np.full((H, W, 3), layer.color, dtype=np.uint8)
            # 把 band_mask=True 的像素写入 canvas
            canvas_arr[band_mask] = fill_arr[band_mask]

    # 3. 挖洞后的内部区域（内矩形/椭圆/L形内部）填背景色或素材
    inner_fill = _render_inner_area(design, quality=quality)
    inner_fill_arr = np.array(inner_fill, dtype=np.uint8)
    inner_mask = _get_inner_pixel_mask(design)
    
    # 池模式：素材图的边框花纹保留在 inner_mask 外部
    # 不需要保存/恢复 inner_mask 内部的像素
    # 白色填充只作用于 inner_mask 内部，外部的素材图花纹自然保留
    
    # 白色填充内部挖空区域
    canvas_arr[inner_mask] = inner_fill_arr[inner_mask]

    # 3.5 在挖空区域边缘绘制统一的10像素黑色边框线
    # 热点①优化：rect_hole 模式用圆角矩形差集替代 EDT（精确等价，33×加速）
    # L形/椭圆模式降级为形态学腐蚀（快于 EDT，视觉差异可忽略）
    BORDER_WIDTH_PX = 10
    BLACK_RGB = (0, 0, 0)

    if design.mode == 'rect_hole':
        from .geometry import (make_mask, fill_rect_mask, apply_rounded_corners_to_mask,
                               compute_inner_corner_radii, RectShape)
        inner_rect = design.inner_rect_px()
        outer = design.outer_rect_px()
        corners = design.corners_px

        # 复用前一步 _get_inner_pixel_mask 已算好的 inner_mask 作为 mask_A
        # （内挖圆角区域），省一次 make_mask + fill_rect_mask + apply_rounded_corners_to_mask
        # （含 numpy 距离场 meshgrid，大半径下耗时显著）
        inner_corners = compute_inner_corner_radii(
            outer, inner_rect, corners,
            direct=design.pool_hole_transparent,
        )

        shrunk_w = max(0, inner_rect.w - 2 * BORDER_WIDTH_PX)
        shrunk_h = max(0, inner_rect.h - 2 * BORDER_WIDTH_PX)
        has_shrunk = shrunk_w > 0 and shrunk_h > 0

        # 双 mask 差集：mask_A = inner_mask（已含圆角），mask_B = 内缩 10px 的圆角矩形
        # border = mask_A \ mask_B
        if has_shrunk:
            shrunk = RectShape(
                x=inner_rect.x + BORDER_WIDTH_PX,
                y=inner_rect.y + BORDER_WIDTH_PX,
                w=shrunk_w, h=shrunk_h,
                corner_r=0.0,
            )
            shrunk_corners = {ck: max(0.0, r - BORDER_WIDTH_PX)
                              for ck, r in inner_corners.items()}
            mask_b_img = make_mask((W, H))
            fill_rect_mask(mask_b_img, shrunk, 255)
            if any(r > 0 for r in shrunk_corners.values()):
                apply_rounded_corners_to_mask(
                    mask_b_img, shrunk, shrunk_corners, fill_value=255)

            border_mask = inner_mask & ~np.array(mask_b_img, dtype=bool)
        else:
            border_mask = inner_mask
    else:
        from .geometry import _erode_mask
        eroded = _erode_mask(inner_mask, BORDER_WIDTH_PX)
        border_mask = inner_mask & ~eroded

    if border_mask.any():
        canvas_arr[border_mask] = BLACK_RGB

    # 4. 边框文字
    if design.border_text is not None:
        pil = Image.fromarray(canvas_arr, 'RGB')
        _draw_border_text(pil, design)
        canvas_arr = np.array(pil, dtype=np.uint8)

    return Image.fromarray(canvas_arr, 'RGB')


def _looks_like_tile(path: str) -> bool:
    """简单启发式：文件名包含 tile/花砖 则默认平铺"""
    n = os.path.basename(path).lower()
    return any(k in n for k in ('tile', 'pattern', 'hua', 'zhuan', '花砖'))


def _get_inner_pixel_mask(design: CropDesign) -> np.ndarray:
    """返回挖洞区域（即内部填充区域）的 bool mask，与边框带的同心圆角保持一致。"""
    from .geometry import (make_mask, fill_rect_mask, fill_ellipse_mask, fill_lshape_mask, 
                           apply_rounded_corners_to_mask, compute_inner_corner_radii)
    W, H = design.canvas_w_px, design.canvas_h_px
    m = make_mask((W, H))

    inner_rect = design.inner_rect_px()
    outer = design.outer_rect_px()
    corners = design.corners_px

    # 使用正确的算法计算内层圆角半径（每个角落独立计算）
    # 水池模式：direct=True 跳过边距缩减，圆角设置直接作用于内挖区域
    inner_corners = compute_inner_corner_radii(outer, inner_rect, corners,
                                                direct=design.pool_hole_transparent)

    if design.mode == 'rect_hole':
        fill_rect_mask(m, inner_rect, 255)
        if any(r > 0 for r in inner_corners.values()):
            apply_rounded_corners_to_mask(m, inner_rect, inner_corners)
        return np.array(m, dtype=bool)

    elif design.mode == 'rect_lshape':
        fill_rect_mask(m, inner_rect, 255)
        cut = design.l_shape_px().cut_rect()
        cut_mask = make_mask((W, H))
        fill_rect_mask(cut_mask, cut, 255)
        m_arr = np.array(m, dtype=bool) & ~np.array(cut_mask, dtype=bool)

        cut_corner = design.l_shape_px().corner
        for ck in ('tl', 'tr', 'bl', 'br'):
            if ck == cut_corner:
                continue
            if inner_corners[ck] > 0:
                corner_mask = make_mask((W, H))
                fill_rect_mask(corner_mask, inner_rect, 255)
                apply_rounded_corners_to_mask(corner_mask, inner_rect, {ck2: (inner_corners[ck2] if ck2 == ck else 0) for ck2 in ('tl', 'tr', 'bl', 'br')})
                m_arr = m_arr | np.array(corner_mask, dtype=bool)
        return m_arr.astype(bool)

    else:  # ellipse_hole
        fill_ellipse_mask(m, design.ellipse_px(), 255)
        return np.array(m, dtype=bool)


def _render_inner_area(design: CropDesign, quality: str = 'export') -> Image.Image:
    """渲染内部填充（纯色 或 适配素材图 或 水池挖空=纯白）"""
    W, H = design.canvas_w_px, design.canvas_h_px
    # 水池模式：内部挖空留白 = 纯白色（JPG 不支持透明，白色即显示为"空"）
    if design.pool_hole_transparent:
        return Image.new('RGB', (W, H), (255, 255, 255))
    if design.hole_bg_image and os.path.isfile(design.hole_bg_image):
        return load_and_fit(design.hole_bg_image, W, H,
                            mode='tile' if _looks_like_tile(design.hole_bg_image) else 'cover',
                            quality=quality)
    return Image.new('RGB', (W, H), design.hole_bg_color)


# ---------- 边框文字 ----------

def _draw_border_text(img: Image.Image, design: CropDesign) -> None:
    """在 img 上沿外矩形四边绘制环绕文字（就地修改 img）"""
    bt = design.border_text
    if bt is None:
        return
    outer = design.outer_rect_px()
    # 估算字体大小：按内边距的 60%
    font_size = max(12, int(design.cm2px(0.5)))
    try:
        font = ImageFont.truetype(bt.font_name, font_size)
    except (OSError, IOError):
        # 找不到字体时退化到默认
        font = ImageFont.load_default()

    draw = ImageDraw.Draw(img)
    text = bt.text

    # 可用区域：稍微向内缩进 1% 避免贴边
    pad = max(4, int(min(outer.w, outer.h) * 0.01))
    x1, y1 = int(outer.x) + pad, int(outer.y) + pad
    x2, y2 = int(outer.right) - pad, int(outer.bottom) - pad
    inner_w = x2 - x1
    inner_h = y2 - y1

    if bt.include_top:
        _draw_text_line(draw, font, text, x1, y1, inner_w, 'top', bt.color, mirror=False)
    if bt.include_bottom:
        _draw_text_line(draw, font, text, x1, y2, inner_w, 'bottom', bt.color, mirror=bt.mirror_bottom)
    if bt.include_left:
        _draw_text_side(draw, font, text, x1, y1, inner_h, 'left', bt.color)
    if bt.include_right:
        _draw_text_side(draw, font, text, x2, y1, inner_h, 'right', bt.color)


def _repeat_text(text: str, length_px: int, font: ImageFont.ImageFont) -> str:
    """把 text 重复拼接直到 ≥ length_px 宽度（实际绘制时再裁剪）"""
    try:
        tw = font.getlength(text)
    except AttributeError:
        tw = font.getsize(text)[0]
    if tw <= 0:
        return text
    need = max(1, int(length_px / tw) + 2)
    return (text + ' ') * need


def _draw_text_line(draw: ImageDraw.ImageDraw, font, text, x, y, w, side, color, mirror):
    """绘制顶/底一行文字；底部可选镜像"""
    full = _repeat_text(text, w, font)
    # 先画到临时图，方便做镜像
    tmp = Image.new('RGB', (w, max(12, font.size + 4)), (255, 255, 255))
    td = ImageDraw.Draw(tmp)
    # 把内部背景色透明化（先转 RGBA 合成回去）：更简单做法——临时图用透明，再贴回
    tmp_rgba = Image.new('RGBA', (w, max(12, font.size + 4)), (0, 0, 0, 0))
    td_rgba = ImageDraw.Draw(tmp_rgba)
    td_rgba.text((0, 0), full, fill=(*color, 255), font=font)
    if mirror:
        tmp_rgba = tmp_rgba.transpose(Image.FLIP_TOP_BOTTOM).transpose(Image.FLIP_LEFT_RIGHT)
    # 按长度裁剪
    if tmp_rgba.width > w:
        tmp_rgba = tmp_rgba.crop((0, 0, w, tmp_rgba.height))
    # 贴回原图
    base = draw._image.convert('RGBA')
    base.alpha_composite(tmp_rgba, dest=(x, y))
    draw._image.paste(base.convert('RGB'), (0, 0))


def _draw_text_side(draw: ImageDraw.ImageDraw, font, text, x, y, h, side, color):
    """绘制左/右竖排文字：先横向画到临时图再旋转 90°"""
    full = _repeat_text(text, h, font)
    line_h = max(12, font.size + 4)
    tmp_rgba = Image.new('RGBA', (h, line_h), (0, 0, 0, 0))
    td = ImageDraw.Draw(tmp_rgba)
    td.text((0, 0), full, fill=(*color, 255), font=font)
    if tmp_rgba.width > h:
        tmp_rgba = tmp_rgba.crop((0, 0, h, line_h))
    # 左边：逆时针 90°；右边：顺时针 90°
    if side == 'left':
        tmp_rgba = tmp_rgba.rotate(90, expand=True)
        dest = (x - line_h, y)
    else:
        tmp_rgba = tmp_rgba.rotate(-90, expand=True)
        dest = (x, y)
    base = draw._image.convert('RGBA')
    base.alpha_composite(tmp_rgba, dest=dest)
    draw._image.paste(base.convert('RGB'), (0, 0))


# ---------- 保存 JPG ----------

def save_jpg(img: Image.Image, out_path: str, quality: int = 95, dpi: int | None = None) -> None:
    """保存为 JPG，可选写入 DPI 元数据（印刷用）"""
    ext = os.path.splitext(out_path)[1].lower()
    if ext not in ('.jpg', '.jpeg'):
        out_path = os.path.splitext(out_path)[0] + '.jpg'
    save_kwargs = {'quality': quality, 'optimize': True}
    if dpi is not None:
        save_kwargs['dpi'] = (dpi, dpi)
    img.save(out_path, 'JPEG', **save_kwargs)


# ---------- 素材图自动裁剪：用于把任意 JPG 素材铺满到目标区域 ----------

def prepare_material_for_rect(material_path: str,
                              target_w: int, target_h: int,
                              mode: str = 'cover') -> Image.Image:
    """
    对外暴露的工具函数：把一张素材 JPG/PSD 预处理到目标尺寸。
    mode: cover（裁剪填满，推荐）/ contain（留白）/ tile（平铺）/ stretch（拉伸）
    """
    if not material_path or not os.path.isfile(material_path):
        return Image.new('RGB', (target_w, target_h), (255, 255, 255))
    ext = os.path.splitext(material_path)[1].lower()
    if ext in ('.psd', '.psb'):
        from .psd.loader import load_psd_flattened
        img = load_psd_flattened(material_path)
    else:
        img = load_image_rgb(material_path)
    return fit_image_to_rect(img, target_w, target_h, mode=mode)
