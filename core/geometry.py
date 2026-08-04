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
    dpi: int = 300

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


# 圆角处理参数（与 image_cropper.py 保持一致）
# [实测] PIL 屏幕坐标系（y 向下）pieslice 角度映射：
#   0° = 右, 90° = 下, 180° = 左, 270° = 上
# 两步法：1.挖正方形(fill=0)  2.填回 1/4 圆(fill=255)
# 圆心在正方形的"内角"顶点，bbox 以该圆心为中心
_CORNER_PIESLICE_PARAMS = {
    'tl': lambda x, y, w, h, r: ([x, y, x + 2*r, y + 2*r], 180, 270),
    'tr': lambda x, y, w, h, r: ([x + w - 2*r, y, x + w, y + 2*r], 270, 360),
    'bl': lambda x, y, w, h, r: ([x, y + h - 2*r, x + 2*r, y + h], 90, 180),
    'br': lambda x, y, w, h, r: ([x + w - 2*r, y + h - 2*r, x + w, y + h], 0, 90),
}


def apply_rounded_corners_to_mask(mask_img: Image.Image, inner_rect: RectShape,
                                   corners: dict[str, float]) -> None:
    """
    在 mask 上应用圆角：对内部矩形的四个角，先挖正方形再填回 1/4 圆。
    切掉的是 L 形（正方形减去 1/4 圆），即只切掉尖角，保留圆弧。
    直接修改 mask_img (L 模式)。
    """
    draw = ImageDraw.Draw(mask_img)
    W, H = mask_img.size
    x, y, w, h = inner_rect.x, inner_rect.y, inner_rect.w, inner_rect.h
    
    for corner_key in ('tl', 'tr', 'bl', 'br'):
        r = corners.get(corner_key, 0.0)
        if r <= 0:
            continue
        
        r_px = max(1, int(round(r)))
        get_params = _CORNER_PIESLICE_PARAMS.get(corner_key)
        if get_params is None:
            continue
        
        # 1. 先把角落 r×r 正方形设为 0（切掉尖角）
        if corner_key == 'tl':
            sq = [x, y, x + r_px, y + r_px]
        elif corner_key == 'tr':
            sq = [x + w - r_px, y, x + w, y + r_px]
        elif corner_key == 'bl':
            sq = [x, y + h - r_px, x + r_px, y + h]
        else:  # br
            sq = [x + w - r_px, y + h - r_px, x + w, y + h]
        
        sq_safe = [max(0, sq[0]), max(0, sq[1]), min(W, sq[2]), min(H, sq[3])]
        if sq_safe[2] > sq_safe[0] and sq_safe[3] > sq_safe[1]:
            draw.rectangle(sq_safe, fill=0)
        
        # 2. 用 pieslice 把图片内部的 1/4 圆填回 255（保留圆弧）
        bbox, start_deg, end_deg = get_params(x, y, w, h, r_px)
        safe_bbox = [max(0, bbox[0]), max(0, bbox[1]), min(W, bbox[2]), min(H, bbox[3])]
        if safe_bbox[2] > safe_bbox[0] and safe_bbox[3] > safe_bbox[1]:
            draw.pieslice(safe_bbox, start=start_deg, end=end_deg, fill=255)


def compute_border_bands(design: CropDesign) -> list[tuple[np.ndarray, BorderLayer]]:
    """
    计算每层边框的掩膜（numpy bool 数组，True 表示该层区域）
    返回 [(layer_mask, layer_def), ...]  从外向内
    """
    w, h = design.canvas_w_px, design.canvas_h_px
    outer = design.outer_rect_px()

    # 1. 根据模式得到“水池边框”整体的外多边形 mask：outer_rect - inner_shape
    outer_mask = make_mask((w, h))
    fill_rect_mask(outer_mask, outer, 255)
    outer_arr = np.array(outer_mask, dtype=bool)

    inner_mask = make_mask((w, h))
    if design.mode == 'rect_hole':
        fill_rect_mask(inner_mask, design.inner_rect_px(), 255)
    elif design.mode == 'rect_lshape':
        fill_lshape_mask(inner_mask, design.l_shape_px(), 255)
    else:  # ellipse_hole
        fill_ellipse_mask(inner_mask, design.ellipse_px(), 255)
    inner_arr = np.array(inner_mask, dtype=bool)

    # 水池整体 = 外矩形 - 内挖洞
    frame_mask = outer_arr & (~inner_arr)

    # 2. 按每层边框 offset 向内收缩，切分 band
    bands: list[tuple[np.ndarray, BorderLayer]] = []
    remaining = frame_mask.copy()
    prev_inner = outer_arr.copy()   # 上一层的“内边界掩膜”（初始=outer矩形）

    for layer in design.borders:
        layer.offset_px = design.cm2px(layer.offset_cm)
        # 当前层的“内边”= prev_inner 整体向内收缩 offset_px
        shrink = int(round(max(1, layer.offset_px)))
        current_inner = _erode_mask(prev_inner, shrink)
        # 当前层 band = 剩余区域 且 在 prev_inner 且 不在 current_inner
        band = remaining & prev_inner & (~current_inner)
        bands.append((band, layer))
        remaining = remaining & (~band)
        prev_inner = current_inner

    # 如果还有剩余区域（边框层总厚度 < 水池厚度），自动追加一个使用 hole_bg_color 的填充层
    if remaining.any():
        extra = BorderLayer(fill_type='solid', color=design.hole_bg_color)
        bands.append((remaining, extra))

    return bands


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
