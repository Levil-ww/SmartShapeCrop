"""
core/geometry.py
参数化形状定义 + 生成 PIL 掩膜（mask）
单位：像素（渲染时用）；UI 输入单位：厘米（通过 DPI 转换）
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal
import numpy as np
from PIL import Image, ImageDraw


# ---------- 基础形状 ----------

@dataclass
class RectShape:
    """矩形（含圆角）"""
    x: float = 0.0           # 左上角 x（像素）
    y: float = 0.0           # 左上角 y
    w: float = 1000.0        # 宽度
    h: float = 1000.0        # 高度
    corner_r: float = 0.0    # 圆角半径

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def to_int_tuple(self) -> tuple[int, int, int, int]:
        return (int(round(self.x)), int(round(self.y)),
                int(round(self.right)), int(round(self.bottom)))


@dataclass
class EllipseShape:
    """椭圆/圆形"""
    cx: float = 500.0
    cy: float = 500.0
    rx: float = 300.0
    ry: float = 200.0


@dataclass
class LShape:
    """
    L 形 = 大矩形 - 角落小矩形
    用于图 2 / 图 4 的挖角效果
    """
    outer: RectShape
    corner: Literal['tl', 'tr', 'bl', 'br'] = 'br'  # 哪个角被挖掉
    cut_w: float = 300.0   # 挖掉的宽度
    cut_h: float = 200.0   # 挖掉的高度

    def cut_rect(self) -> RectShape:
        """返回被挖掉的小矩形"""
        ox, oy, ow, oh = self.outer.x, self.outer.y, self.outer.w, self.outer.h
        cw, ch = self.cut_w, self.cut_h
        if self.corner == 'tl':
            return RectShape(ox, oy, cw, ch)
        elif self.corner == 'tr':
            return RectShape(ox + ow - cw, oy, cw, ch)
        elif self.corner == 'bl':
            return RectShape(ox, oy + oh - ch, cw, ch)
        else:  # br
            return RectShape(ox + ow - cw, oy + oh - ch, cw, ch)


# ---------- 边框层定义 ----------

@dataclass
class BorderLayer:
    """一层边框：向内收缩 offset，填充颜色（纯色 或 素材图路径）"""
    offset_cm: float = 0.0          # 从外轮廓向内的距离（厘米，输入用）
    offset_px: float = 0.0          # 像素（渲染用，由 DPI 换算）
    fill_type: Literal['solid', 'image', 'tile'] = 'solid'
    color: tuple[int, int, int] = (0, 0, 0)   # RGB 纯色
    image_path: str | None = None             # 素材图（JPG）作为填充
    tile_mode: bool = False                   # True=平铺，False=缩放填充


# ---------- 文字装饰 ----------

@dataclass
class BorderText:
    """沿矩形边框排列的文字（图 1 四周的英文句子）"""
    text: str = "Cross the stars over the moon to meet your better self."
    font_name: str = "arial.ttf"
    font_size_px: int = 30
    color: tuple[int, int, int] = (0, 0, 0)
    include_top: bool = True
    include_right: bool = True
    include_bottom: bool = True
    include_left: bool = True
    mirror_bottom: bool = True   # 底部文字是否镜像翻转（图 1 需要）


# ---------- 裁剪设计（整体描述） ----------

@dataclass
class CropDesign:
    """一个完整的裁剪设计描述"""
    # 画布尺寸（像素 = 厘米 × DPI）
    canvas_w_cm: float = 50.0
    canvas_h_cm: float = 70.0
    dpi: int = 150

    # 模式：'rect_hole' 矩形嵌套(图1/5) | 'rect_lshape' L形(图2/4) | 'ellipse_hole' 椭圆(图3)
    mode: Literal['rect_hole', 'rect_lshape', 'ellipse_hole'] = 'rect_hole'

    # 外轮廓（模式都用）
    outer_margin_cm: float = 1.0   # 外框留白边

    # —— mode == rect_hole / rect_lshape 时的内挖矩形 ——
    inner_margin_top_cm: float = 8.0
    inner_margin_bottom_cm: float = 8.0
    inner_margin_left_cm: float = 8.0
    inner_margin_right_cm: float = 8.0

    # —— mode == rect_lshape 额外参数 ——
    l_corner: Literal['tl', 'tr', 'bl', 'br'] = 'br'
    l_cut_w_cm: float = 15.0
    l_cut_h_cm: float = 10.0

    # —— 四个角的圆角半径（厘米），0 表示无圆角 ——
    corner_tl_cm: float = 0.0
    corner_tr_cm: float = 0.0
    corner_bl_cm: float = 0.0
    corner_br_cm: float = 0.0

    # —— mode == ellipse_hole 额外参数（相对中心的比例） ——
    ellipse_rx_ratio: float = 0.35   # 占画布宽度的比例
    ellipse_ry_ratio: float = 0.30   # 占画布高度的比例

    # 多层边框（从外向内，offset 为该层的厚度）
    borders: list[BorderLayer] = field(default_factory=lambda: [
        BorderLayer(offset_cm=0.3, fill_type='solid', color=(0, 0, 0)),
        BorderLayer(offset_cm=0.2, fill_type='solid', color=(255, 255, 255)),
        BorderLayer(offset_cm=0.3, fill_type='solid', color=(0, 0, 0)),
    ])

    # 背景色（水池边框内部 = 挖去的洞的背景，若无素材则为纯色）
    hole_bg_color: tuple[int, int, int] = (250, 245, 230)
    hole_bg_image: str | None = None   # 可选：JPG 素材填充内部区域

    # 外背景（画布最外层，通常是图 1/3/5 的黑色边）
    outer_bg_color: tuple[int, int, int] = (0, 0, 0)
    outer_bg_image: str | None = None

    # 边框文字（可选）
    border_text: BorderText | None = None

    # —— 辅助：像素级尺寸换算 ——
    def cm2px(self, cm: float) -> float:
        return cm * self.dpi / 2.54

    @property
    def canvas_w_px(self) -> int:
        return int(round(self.cm2px(self.canvas_w_cm)))

    @property
    def canvas_h_px(self) -> int:
        return int(round(self.cm2px(self.canvas_h_cm)))

    # 像素级坐标计算
    def outer_rect_px(self) -> RectShape:
        m = self.cm2px(self.outer_margin_cm)
        return RectShape(m, m,
                         self.canvas_w_px - 2 * m,
                         self.canvas_h_px - 2 * m)

    def inner_rect_px(self) -> RectShape:
        outer = self.outer_rect_px()
        mt = self.cm2px(self.inner_margin_top_cm)
        mb = self.cm2px(self.inner_margin_bottom_cm)
        ml = self.cm2px(self.inner_margin_left_cm)
        mr = self.cm2px(self.inner_margin_right_cm)
        return RectShape(outer.x + ml, outer.y + mt,
                         outer.w - ml - mr,
                         outer.h - mt - mb)

    def ellipse_px(self) -> EllipseShape:
        cw, ch = self.canvas_w_px, self.canvas_h_px
        return EllipseShape(cx=cw / 2, cy=ch / 2,
                            rx=cw * self.ellipse_rx_ratio,
                            ry=ch * self.ellipse_ry_ratio)

    def l_shape_px(self) -> LShape:
        return LShape(
            outer=self.inner_rect_px(),
            corner=self.l_corner,
            cut_w=self.cm2px(self.l_cut_w_cm),
            cut_h=self.cm2px(self.l_cut_h_cm),
        )

    @property
    def corners_px(self) -> dict[str, float]:
        """返回四个角的像素级圆角半径字典"""
        return {
            'tl': self.cm2px(self.corner_tl_cm),
            'tr': self.cm2px(self.corner_tr_cm),
            'bl': self.cm2px(self.corner_bl_cm),
            'br': self.cm2px(self.corner_br_cm),
        }


# ---------- 掩膜（mask）生成 ----------

def make_mask(size: tuple[int, int]) -> Image.Image:
    """创建一张全黑的 L 模式图（0=被挖掉，255=保留）"""
    return Image.new('L', size, 0)


def fill_rect_mask(mask: Image.Image, rect: RectShape, value: int = 255) -> None:
    """在 mask 上填充一个矩形区域为 value"""
    d = ImageDraw.Draw(mask)
    if rect.corner_r > 0:
        d.rounded_rectangle(rect.to_int_tuple(),
                            radius=int(rect.corner_r), fill=value)
    else:
        d.rectangle(rect.to_int_tuple(), fill=value)


def fill_ellipse_mask(mask: Image.Image, e: EllipseShape, value: int = 255) -> None:
    d = ImageDraw.Draw(mask)
    box = (int(e.cx - e.rx), int(e.cy - e.ry),
           int(e.cx + e.rx), int(e.cy + e.ry))
    d.ellipse(box, fill=value)


def fill_lshape_mask(mask: Image.Image, l: LShape, value: int = 255) -> None:
    """L 形：outer 矩形填充，然后挖掉 corner 小矩形"""
    fill_rect_mask(mask, l.outer, value)
    # 再把 cut_rect 区域设为 0
    fill_rect_mask(mask, l.cut_rect(), value=0 if value == 255 else 255)


# 圆角处理统一委托给 core.corner.algorithm 模块，确保与 image_cropper.py 完全一致。
# 历史的 _CORNER_PIESLICE_PARAMS 表已删除，单一来源为
# core.corner.algorithm.CORNER_ANGLES / carve_corner_on_mask。
from .corner.algorithm import carve_corner_on_mask as _carve_corner_on_mask


def apply_rounded_corners_to_mask(mask_img: Image.Image, inner_rect: RectShape,
                                   corners: dict[str, float]) -> None:
    """
    在 mask 上应用圆角：对内部矩形的四个角，先挖正方形再填回 1/4 圆。
    切掉的是 L 形（正方形减去 1/4 圆），即只切掉尖角，保留圆弧。
    直接修改 mask_img (L 模式)。

    圆角算法统一委托给 core.rounded_corner.carve_corner_on_mask，
    确保与 image_cropper.py 的 apply_rounded_corners 完全一致。
    """
    _carve_corner_on_mask(
        mask_img,
        (inner_rect.x, inner_rect.y, inner_rect.w, inner_rect.h),
        corners,
        canvas_size=mask_img.size,
    )


def compute_inner_corner_radii(outer_rect: RectShape, inner_rect: RectShape,
                                outer_corners: dict[str, float]) -> dict[str, float]:
    """
    计算内层矩形的有效圆角半径，基于每个角落到外层矩形的实际距离。

    正确算法：对每个角落，计算内层矩形到外层矩形在 x 和 y 方向的距离，
    取较大值作为缩减量，确保内层裁剪区域被外层完全包含。

    示例：左上角有效半径 = max(0, R - max(T_left, T_top))
    """
    T_left = inner_rect.x - outer_rect.x
    T_right = outer_rect.right - inner_rect.right
    T_top = inner_rect.y - outer_rect.y
    T_bottom = outer_rect.bottom - inner_rect.bottom

    inner_corners = {}
    for ck in ('tl', 'tr', 'bl', 'br'):
        R = outer_corners.get(ck, 0.0)
        if R <= 0:
            inner_corners[ck] = 0.0
            continue
        
        if ck == 'tl':
            dist = max(T_left, T_top)
        elif ck == 'tr':
            dist = max(T_right, T_top)
        elif ck == 'bl':
            dist = max(T_left, T_bottom)
        else:  # br
            dist = max(T_right, T_bottom)
        
        inner_corners[ck] = max(0.0, R - dist)
    
    return inner_corners


def compute_border_bands(design: CropDesign) -> list[tuple[np.ndarray, BorderLayer]]:
    """
    计算每层边框的掩膜（numpy bool 数组，True 表示该层区域）
    返回 [(layer_mask, layer_def), ...]  从外向内

    核心思路：
      每层边框带 = 同心圆角矩形差集
      - 外层：距 outer_rect 偏移 t_outer 的圆角矩形（圆角半径 max(0, R - t_outer)）
      - 内层：距 outer_rect 偏移 t_outer + t_layer 的圆角矩形（圆角半径 max(0, R - t_outer - t_layer)）
      - band = 外层 & ~内层

      这样每层在直线部分是矩形带，在角落是同心环扇形，
      不会出现白色覆盖，边框自身颜色和线条完整保留。
    """
    w, h = design.canvas_w_px, design.canvas_h_px
    outer = design.outer_rect_px()
    corners = design.corners_px
    inner_rect = design.inner_rect_px()

    # 1. 计算 frame_mask（总边框带）：外层圆角矩形 - 内层圆角矩形
    outer_solid = make_mask((w, h))
    fill_rect_mask(outer_solid, outer, 255)
    apply_rounded_corners_to_mask(outer_solid, outer, corners)

    inner_solid = make_mask((w, h))
    fill_rect_mask(inner_solid, inner_rect, 255)
    # 使用正确的算法计算内层圆角半径（每个角落独立计算）
    inner_corners = compute_inner_corner_radii(outer, inner_rect, corners)
    if any(r > 0 for r in inner_corners.values()):
        apply_rounded_corners_to_mask(inner_solid, inner_rect, inner_corners)

    frame_mask = np.array(outer_solid, dtype=bool) & ~np.array(inner_solid, dtype=bool)

    # 2. 按每层边框 offset 切分 band
    bands: list[tuple[np.ndarray, BorderLayer]] = []
    cumulative_offset = 0

    for layer in design.borders:
        layer.offset_px = design.cm2px(layer.offset_cm)
        t_layer = int(round(max(1, layer.offset_px)))
        t_outer = cumulative_offset
        cumulative_offset += t_layer

        # 外层：距 outer_rect 偏移 t_outer 的圆角矩形
        outer_rect_i = RectShape(
            x=outer.x + t_outer, y=outer.y + t_outer,
            w=outer.w - 2 * t_outer, h=outer.h - 2 * t_outer,
            corner_r=max(0, corners.get('br', 0.0) - t_outer)
        )
        outer_radii_i = {ck: max(0, corners.get(ck, 0.0) - t_outer) for ck in ('tl', 'tr', 'bl', 'br')}
        outer_mask_i = make_mask((w, h))
        fill_rect_mask(outer_mask_i, outer_rect_i, 255)
        if any(r > 0 for r in outer_radii_i.values()):
            apply_rounded_corners_to_mask(outer_mask_i, outer_rect_i, outer_radii_i)

        # 内层：距 outer_rect 偏移 t_outer + t_layer 的圆角矩形
        t_inner = t_outer + t_layer
        inner_rect_i = RectShape(
            x=outer.x + t_inner, y=outer.y + t_inner,
            w=outer.w - 2 * t_inner, h=outer.h - 2 * t_inner,
            corner_r=max(0, corners.get('br', 0.0) - t_inner)
        )
        inner_radii_i = {ck: max(0, corners.get(ck, 0.0) - t_inner) for ck in ('tl', 'tr', 'bl', 'br')}
        inner_mask_i = make_mask((w, h))
        fill_rect_mask(inner_mask_i, inner_rect_i, 255)
        if any(r > 0 for r in inner_radii_i.values()):
            apply_rounded_corners_to_mask(inner_mask_i, inner_rect_i, inner_radii_i)

        band = np.array(outer_mask_i, dtype=bool) & ~np.array(inner_mask_i, dtype=bool)
        bands.append((band, layer))

    # 3. 处理剩余区域
    all_bands = np.zeros((h, w), dtype=bool)
    for b, _ in bands:
        all_bands = all_bands | b
    remaining = frame_mask & (~all_bands)
    if remaining.any():
        extra = BorderLayer(fill_type='solid', color=design.hole_bg_color)
        bands.append((remaining, extra))

    return bands


def _draw_rounded_seg(mask_img, cx, cy, radius, corner_key, fill_val):
    """在 mask 上画一个扇形（用于构建角落的环扇形）"""
    from PIL import ImageDraw
    draw = ImageDraw.Draw(mask_img)
    # 扇形的 bounding box
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
    # 根据角落确定起始和结束角度（PIL screen 坐标系：0=右，90=下，180=左，270=上）
    if corner_key == 'tl':     start, end = 180, 270
    elif corner_key == 'tr':   start, end = 270, 360
    elif corner_key == 'bl':   start, end = 90, 180
    else:                      start, end = 0, 90
    draw.pieslice(bbox, start=start, end=end, fill=fill_val)


def _erode_mask(mask_bool: np.ndarray, px: int) -> np.ndarray:
    """形态学腐蚀：把 True 区域向内收缩 px 像素"""
    if px <= 0:
        return mask_bool.copy()
    try:
        import cv2
        kernel = np.ones((px * 2 + 1, px * 2 + 1), dtype=np.uint8)
        u8 = mask_bool.astype(np.uint8) * 255
        eroded = cv2.erode(u8, kernel, iterations=1)
        return eroded > 127
    except ImportError:
        # 无 opencv 降级：用 PIL 的 MinFilter 近似
        from PIL import ImageFilter
        img = Image.fromarray((mask_bool * 255).astype(np.uint8))
        r = max(1, px)
        img = img.filter(ImageFilter.MinFilter(r * 2 + 1))
        return np.array(img) > 127
