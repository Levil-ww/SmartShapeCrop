"""
core/geometry.py
参数化形状定义 + 生成 PIL 掩膜（mask）
单位：像素（渲染时用）；UI 输入单位：厘米（通过 DPI 转换）
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from typing import Literal
import logging
import numpy as np
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


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
        x0 = int(round(self.x))
        y0 = int(round(self.y))
        x1 = int(round(self.right))
        y1 = int(round(self.bottom))
        # [F1 修复] 防御性归一：保证 x0<=x1 且 y0<=y1，避免把负宽/负高矩形
        # 传给 PIL ImageDraw 时触发 "ValueError: x1 must be greater than or
        # equal to x0"。退化的矩形（w/h<0）被归一为 0 宽/高，绘制时等价于空操作。
        if x1 < x0:
            x1 = x0
        if y1 < y0:
            y1 = y0
        return (x0, y0, x1, y1)


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

    # —— 水池设计器新增字段（默认值保持旧行为）——
    pool_hole_transparent: bool = False           # True=内部挖空留白（纯白色 JPG 背景）
    pool_outer_material_image: str | None = None  # 水池外框素材图：匹配到的花纹图，整幅铺满再挖中间

    # [Fix 2026-08-26] 水池素材原始设计方向尺寸（文件名解析的 w×h，未经 oriented 交换）
    # 用于渲染时判断素材图是否需要旋转90度后再等比缩放（避免 cover 过度裁剪 / stretch 变形）
    # 例：文件名 "中古大花:58x121CM" → w=58, h=121 (竖版设计)；画布交换后为 122×51 横版
    # → 素材应先旋转90度 (变成横版内容布局) 再按物理等比缩放到画布
    pool_material_design_w_cm: float = 0.0
    pool_material_design_h_cm: float = 0.0

    # —— 渲染加速：Worker 预加载的模板图缓存 ——
    _cached_outer_image: Image.Image | None = None

    # —— 辅助：像素级尺寸换算 ——
    def cm2px(self, cm: float) -> float:
        return cm * self.dpi / 2.54

    @property
    def canvas_w_px(self) -> int:
        return int(round(self.cm2px(self.canvas_w_cm)))

    @property
    def canvas_h_px(self) -> int:
        return int(round(self.cm2px(self.canvas_h_cm)))

    # ---------- 线程安全快照 ----------
    def clone(self) -> "CropDesign":
        """返回一份独立的快照，供后台渲染线程安全使用。

        后台 ``PreviewRenderWorker`` 会持有该快照执行 ``render_design``，而在此期间
        主线程仍可能原地修改 ``self.design``（用户改边距、增删 ``borders`` 等）。
        为避免跨线程读写竞争（字段撕裂、``borders`` 列表迭代中变更、渲染结果错乱），
        这里对**可变字段**做深拷贝：

        * 标量 / 字符串 / 元组：本身不可变，按引用复制即可；
        * ``borders`` 列表：逐层复制为新的 ``BorderLayer``；
        * ``border_text``：复制为新的 ``BorderText``；
        * ``_cached_outer_image``：**共享只读引用**——渲染期间不会被原地修改，
          共享可避免每次后台渲染都复制上百 MB 的素材图（F4 修复）。
        """
        new = replace(self)  # 浅拷贝所有字段（列表/嵌套对象仍是同一引用）
        # 断开可变容器的共享，使后台渲染与主线程互不干扰
        new.borders = [replace(b) for b in self.borders]
        if self.border_text is not None:
            new.border_text = replace(self.border_text)
        return new

    # 像素级坐标计算
    def outer_rect_px(self) -> RectShape:
        m = self.cm2px(self.outer_margin_cm)
        w = self.canvas_w_px - 2 * m
        h = self.canvas_h_px - 2 * m
        # [F1 修复] 外边距可能超过画布可用空间，clamp 到非负，
        # 避免负宽/负高导致下游 PIL ImageDraw 抛 "x1 must be >= x0"。
        if w <= 0 or h <= 0:
            logger.warning(
                f"[geometry] outer_rect_px 退化 (w={w:.1f}, h={h:.1f})，"
                f"outer_margin_cm={self.outer_margin_cm} 超过画布可用尺寸，已 clamp 至 0"
            )
            w = max(0.0, w)
            h = max(0.0, h)
        return RectShape(m, m, w, h)

    def inner_rect_px(self) -> RectShape:
        outer = self.outer_rect_px()
        mt = self.cm2px(self.inner_margin_top_cm)
        mb = self.cm2px(self.inner_margin_bottom_cm)
        ml = self.cm2px(self.inner_margin_left_cm)
        mr = self.cm2px(self.inner_margin_right_cm)
        w = outer.w - ml - mr
        h = outer.h - mt - mb
        # [F1 修复] 内边距之和可能超过外框可用空间（边距设置过大、
        # 或 LOD 下采样取整反转），clamp 到非负，避免负宽高拖垮整个渲染链。
        if w <= 0 or h <= 0:
            logger.warning(
                f"[geometry] inner_rect_px 退化 (w={w:.1f}, h={h:.1f})，"
                f"内边距之和超过外框可用尺寸，已 clamp 至 0"
            )
            w = max(0.0, w)
            h = max(0.0, h)
        return RectShape(outer.x + ml, outer.y + mt, w, h)

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
    """在 mask 上填充一个矩形区域为 value。

    [F1 修复] 退化守卫：当 rect 宽或高 <= 0（内/外边距大于可用空间，
    或 LOD 取整导致坐标反转）时直接跳过绘制，避免 PIL ImageDraw 抛
    ValueError 使预览崩溃。圆角半径也会被 clamp 到矩形可容纳的最大值。
    """
    if rect.w <= 0 or rect.h <= 0:
        logger.warning(
            f"[geometry] fill_rect_mask 跳过退化矩形 (w={rect.w}, h={rect.h})，"
            f"疑似边距设置大于画布可用空间"
        )
        return
    x0, y0, x1, y1 = rect.to_int_tuple()
    d = ImageDraw.Draw(mask)
    if rect.corner_r > 0:
        # 圆角半径不得超过矩形短边的一半，否则 PIL 同样会抛异常
        max_r = max(1, min(x1 - x0, y1 - y0) // 2)
        r = min(int(rect.corner_r), max_r)
        if r > 0:
            d.rounded_rectangle((x0, y0, x1, y1), radius=r, fill=value)
        else:
            d.rectangle((x0, y0, x1, y1), fill=value)
    else:
        d.rectangle((x0, y0, x1, y1), fill=value)


def fill_ellipse_mask(mask: Image.Image, e: EllipseShape, value: int = 255) -> None:
    d = ImageDraw.Draw(mask)
    # [F1 修复] 归一椭圆包围盒，避免负半径导致 x0>x1 触发 ValueError
    x0 = int(round(e.cx - e.rx))
    y0 = int(round(e.cy - e.ry))
    x1 = int(round(e.cx + e.rx))
    y1 = int(round(e.cy + e.ry))
    if x1 < x0:
        x1 = x0
    if y1 < y0:
        y1 = y0
    if x1 <= x0 or y1 <= y0:
        return
    d.ellipse((x0, y0, x1, y1), fill=value)


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
                                   corners: dict[str, float],
                                   fill_value: int = 255,
                                   inverse: bool = False) -> None:
    """
    在 mask 上应用圆角：对内部矩形的四个角，先挖正方形再填回 1/4 圆。
    切掉的是 L 形（正方形减去 1/4 圆），即只切掉尖角，保留圆弧。
    直接修改 mask_img (L 模式)。

    圆角算法统一委托给 core.rounded_corner.carve_corner_on_mask，
    确保与 image_cropper.py 的 apply_rounded_corners 完全一致。

    [Fix TR/BR 白色竖线 2026-08-14]
    关键：**圆角切割必须与 fill_rect_mask 使用像素对齐后的同一整数矩形**。
    fill_rect_mask 内部使用 RectShape.to_int_tuple()（即 x/y/right/bottom 全 int(round)）
    生成像素级矩形；如果此处把 float (x,y,w,h) 传给 carve_corner_on_mask，
    当 inner_rect.right / inner_rect.bottom 是小数（常见于 DPI*cm 换算或非整数边距），
    TR/BR 角的 corner square 右/下边界会被 PIL draw.rectangle 默默 int() 截断 0.9px，
    导致最后一(几)列 mask 漏填为 0 → 仍是 255(挖空白) → 视觉上是紧贴右边缘的白竖线。
    TL/BL 因截断方向向内不外露所以看不出来。修复方式是：在此处先把 float 矩形
    对齐到与 fill_rect_mask 完全相同的整数网格，再把整数版 (x_i, y_i, w_i, h_i) 传下去。

    Args:
        fill_value: 圆弧填充值（默认 255）
        inverse: True=反转角落操作，用于内层矩形的精确圆弧挖空
    """
    # 与 fill_rect_mask 用完全相同的整数对齐策略
    x_i = int(round(inner_rect.x))
    y_i = int(round(inner_rect.y))
    right_i = int(round(inner_rect.right))
    bottom_i = int(round(inner_rect.bottom))
    if right_i < x_i:
        right_i = x_i
    if bottom_i < y_i:
        bottom_i = y_i
    w_i = right_i - x_i
    h_i = bottom_i - y_i
    # [F1 修复] 退化矩形（w/h<=0）无可圆角，直接返回，
    # 避免把负宽高传入距离场算法产生几何错乱。
    if w_i <= 0 or h_i <= 0:
        logger.warning(
            f"[geometry] apply_rounded_corners_to_mask 跳过退化矩形 "
            f"(w={w_i}, h={h_i})"
        )
        return
    _carve_corner_on_mask(
        mask_img,
        (x_i, y_i, w_i, h_i),
        corners,
        canvas_size=mask_img.size,
        fill_value=fill_value,
        inverse=inverse,
    )


def compute_inner_corner_radii(outer_rect: RectShape, inner_rect: RectShape,
                                outer_corners: dict[str, float],
                                *,
                                direct: bool = False) -> dict[str, float]:
    """
    计算内层矩形的有效圆角半径。

    direct=False（默认，普通多层边框模式）：
        基于每个角落到外层矩形的实际距离做缩减，确保内层圆角被外层完全包含。
        示例：左上角有效半径 = max(0, R - max(T_left, T_top))

    direct=True（水池设计器模式）：
        跳过边距缩减，直接把 outer_corners 的值作为内层的圆角，
        仅做最小边一半的上界保护，防止半径过大导致中心区域被异常染色。
        1:1 角映射：TL→TL, TR→TR, BL→BL, BR→BR。
    """
    inner_corners = {}
    max_safe = min(inner_rect.w, inner_rect.h) / 2.0
    T_left = inner_rect.x - outer_rect.x
    T_right = outer_rect.right - inner_rect.right
    T_top = inner_rect.y - outer_rect.y
    T_bottom = outer_rect.bottom - inner_rect.bottom

    for ck in ('tl', 'tr', 'bl', 'br'):
        R = max(0.0, outer_corners.get(ck, 0.0))
        if direct:
            inner_corners[ck] = min(R, max_safe)
            continue

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

        inner_corners[ck] = min(max(0.0, R - dist), max_safe)

    return inner_corners


def compute_border_bands(design: CropDesign) -> list[tuple[np.ndarray, BorderLayer]]:
    """
    计算每层边框的掩膜（numpy bool 数组，True 表示该层区域）
    返回 [(layer_mask, layer_def), ...]  从外向内

    核心思路：
      - rect_hole: 同心圆角矩形差集
      - rect_lshape: 同心 L 形差集（每层边框沿 L 形路径等距偏移）
      - ellipse_hole: 椭圆模式暂不支持多层边框

      使用双 mask 独立绘制差集，避免单 mask inverse 模式在角落产生像素异常。
    """
    if design.mode == 'rect_lshape':
        return compute_lshape_border_bands(design)
    # [F9] ellipse_hole 模式不支持多层边框：若设置了 borders，此处会按
    # rect_hole 的矩形带逻辑渲染（视觉上为“矩形洞”边框带，不符合椭圆预期）。
    # 出于“不改变功能逻辑”的约束，不在此处改变渲染；记录 warning 提升可见性，
    # 文档与 UI 说明明确“椭圆模式暂不支持多层边框”。
    if design.mode == 'ellipse_hole' and design.borders:
        logger.warning(
            f"[geometry] 椭圆模式不支持多层边框，将按矩形边框带渲染 "
            f"（borders={len(design.borders)} 层）；如需正确椭圆边框请移除边框设置"
        )
    # —— 以下为 rect_hole 原有逻辑 ——
    w, h = design.canvas_w_px, design.canvas_h_px
    outer = design.outer_rect_px()
    corners = design.corners_px
    inner_rect = design.inner_rect_px()

    # 1. 计算 frame_mask（总边框带）：双 mask 差集
    frame_outer_img = make_mask((w, h))
    fill_rect_mask(frame_outer_img, outer, 255)
    apply_rounded_corners_to_mask(frame_outer_img, outer, corners, fill_value=255)

    inner_corners = compute_inner_corner_radii(outer, inner_rect, corners)
    frame_inner_img = make_mask((w, h))
    fill_rect_mask(frame_inner_img, inner_rect, 255)
    if any(r > 0 for r in inner_corners.values()):
        apply_rounded_corners_to_mask(frame_inner_img, inner_rect, inner_corners, fill_value=255)

    frame_mask = np.array(frame_outer_img, dtype=bool) & ~np.array(frame_inner_img, dtype=bool)

    # 2. 按每层边框 offset 切分 band（双 mask 差集）
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
            corner_r=0.0
        )
        outer_radii_i = {ck: max(0, corners.get(ck, 0.0) - t_outer) for ck in ('tl', 'tr', 'bl', 'br')}

        # 内层：距 outer_rect 偏移 t_outer + t_layer 的圆角矩形
        t_inner = t_outer + t_layer
        inner_rect_i = RectShape(
            x=outer.x + t_inner, y=outer.y + t_inner,
            w=outer.w - 2 * t_inner, h=outer.h - 2 * t_inner,
            corner_r=0.0
        )
        inner_radii_i = {ck: max(0, corners.get(ck, 0.0) - t_inner) for ck in ('tl', 'tr', 'bl', 'br')}

        # 双 mask：外层 255 - 内层 255 = band
        band_outer_img = make_mask((w, h))
        fill_rect_mask(band_outer_img, outer_rect_i, 255)
        if any(r > 0 for r in outer_radii_i.values()):
            apply_rounded_corners_to_mask(band_outer_img, outer_rect_i, outer_radii_i, fill_value=255)

        band_inner_img = make_mask((w, h))
        fill_rect_mask(band_inner_img, inner_rect_i, 255)
        if any(r > 0 for r in inner_radii_i.values()):
            apply_rounded_corners_to_mask(band_inner_img, inner_rect_i, inner_radii_i, fill_value=255)

        band = np.array(band_outer_img, dtype=bool) & ~np.array(band_inner_img, dtype=bool)
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


# ---------- L 形计算辅助 ----------

def _get_lshape_cut_rect_at_offset(outer_rect: RectShape, corner_key: str,
                                   cut_w: float, cut_h: float,
                                   offset: float) -> RectShape:
    """
    当 outer_rect 向内收缩 offset 像素后，计算 cut rect 的新位置和尺寸。
    保持 L 形拓扑：cut 矩形的外边缘始终与收缩后的 outer_rect 外边缘对齐。
    cut 宽/高随 offset 等比缩小，直至 0。
    """
    new_x = outer_rect.x + offset
    new_y = outer_rect.y + offset
    new_right = outer_rect.right - offset
    new_bottom = outer_rect.bottom - offset

    cw = max(0.0, cut_w - offset)
    ch = max(0.0, cut_h - offset)

    if corner_key == 'tl':
        return RectShape(new_x, new_y, cw, ch)
    elif corner_key == 'tr':
        return RectShape(new_right - cw, new_y, cw, ch)
    elif corner_key == 'bl':
        return RectShape(new_x, new_bottom - ch, cw, ch)
    else:  # br
        return RectShape(new_right - cw, new_bottom - ch, cw, ch)


def build_lshape_mask(size: tuple[int, int],
                       outer_rect: RectShape, corner_key: str,
                       cut_w: float, cut_h: float,
                       radii: dict[str, float],
                       fill_value: int = 255) -> Image.Image:
    """
    构建带圆角的 L 形 mask。

    算法：
    1. 填充 outer_rect 为 fill_value
    2. 挖掉 cut_rect（设为相反值）
    3. 对 outer_rect 的 3 个非 cut 角应用圆角（单步扇形切割）
    4. 对 cut_rect 的"对角"（内部 L 形拐角）应用圆角
       - cut 'br' → 内部拐角在 cut_rect.TL → 按 TL 角处理
       - cut 'tr' → 内部拐角在 cut_rect.BL → 按 BL 角处理
       - cut 'bl' → 内部拐角在 cut_rect.TR → 按 TR 角处理
       - cut 'tl' → 内部拐角在 cut_rect.BR → 按 BR 角处理
    5. 当 cut 宽/高 ≤ 0 时退化为纯矩形圆角

    Args:
        size: (W, H) 画布尺寸
        outer_rect: 外轮廓矩形
        corner_key: 被挖掉的角 ('tl'|'tr'|'bl'|'br')
        cut_w, cut_h: 挖角尺寸（像素）
        radii: 4 个角的圆角半径字典
        fill_value: 填充值（255=保留, 0=挖空）

    Returns:
        PIL Image (L mode) — L 形 mask
    """
    W, H = size
    m = make_mask(size)
    other = 0 if fill_value == 255 else 255

    # Step 1: 填充外轮廓
    fill_rect_mask(m, outer_rect, fill_value)

    cut = _get_lshape_cut_rect_at_offset(outer_rect, corner_key, cut_w, cut_h, 0)
    has_cut = cut.w > 0.5 and cut.h > 0.5

    if has_cut:
        # Step 2: 挖掉 cut 区域
        fill_rect_mask(m, cut, other)

        # Step 3: 圆角处理 outer_rect 的 3 个非 cut 角
        for ck in ('tl', 'tr', 'bl', 'br'):
            if ck == corner_key:
                continue
            r = radii.get(ck, 0.0)
            if r > 0.5:
                corner_radii = {k: (r if k == ck else 0.0) for k in ('tl', 'tr', 'bl', 'br')}
                apply_rounded_corners_to_mask(m, outer_rect, corner_radii, fill_value=fill_value)

        # Step 4: 圆角处理内部 L 形拐角（cut_rect 的对角）
        opposite_map = {'br': 'tl', 'tr': 'bl', 'bl': 'tr', 'tl': 'br'}
        internal_key = opposite_map[corner_key]
        internal_r = radii.get(corner_key, 0.0)

        if internal_r > 0.5:
            corner_radii = {k: (internal_r if k == internal_key else 0.0) for k in ('tl', 'tr', 'bl', 'br')}
            apply_rounded_corners_to_mask(m, cut, corner_radii, fill_value=fill_value)
    else:
        # 退化为纯矩形：对所有 4 个角应用圆角
        if any(v > 0.5 for v in radii.values()):
            apply_rounded_corners_to_mask(m, outer_rect, radii, fill_value=fill_value)

    return m


def compute_lshape_border_bands(design: CropDesign) -> list[tuple[np.ndarray, BorderLayer]]:
    """
    L 形边框带计算：每层边框 = 两个同心 L 形 mask 的差集。

    与矩形模式的区别：每层边框的内缩/外扩同时作用于 outer rect 和 cut rect，
    保证 L 形拐弯处的边框厚度与直线段完全一致。
    """
    w, h = design.canvas_w_px, design.canvas_h_px
    outer = design.outer_rect_px()
    inner = design.inner_rect_px()
    lshape = design.l_shape_px()
    cut_corner = lshape.corner
    cut_w = lshape.cut_w
    cut_h = lshape.cut_h
    corners = design.corners_px

    # 1. 计算 frame_mask（总边框带）
    frame_outer_img = build_lshape_mask(
        (w, h), outer, cut_corner, cut_w, cut_h, corners, fill_value=255)

    inner_corners = compute_inner_corner_radii(outer, inner, corners)
    frame_inner_img = build_lshape_mask(
        (w, h), inner, cut_corner, cut_w, cut_h, inner_corners, fill_value=255)

    frame_mask = np.array(frame_outer_img, dtype=bool) & ~np.array(frame_inner_img, dtype=bool)

    # 2. 按每层 border offset 切分 band
    bands: list[tuple[np.ndarray, BorderLayer]] = []
    cumulative_offset = 0

    for layer in design.borders:
        layer.offset_px = design.cm2px(layer.offset_cm)
        t_layer = int(round(max(1, layer.offset_px)))
        t_outer = cumulative_offset
        cumulative_offset += t_layer

        # 外层 L 形（偏移 t_outer）
        outer_at = RectShape(
            x=outer.x + t_outer, y=outer.y + t_outer,
            w=max(1, outer.w - 2 * t_outer),
            h=max(1, outer.h - 2 * t_outer),
            corner_r=0.0
        )
        outer_cut_w = max(0.0, cut_w - t_outer)
        outer_cut_h = max(0.0, cut_h - t_outer)
        outer_radii_i = {ck: max(0, corners.get(ck, 0.0) - t_outer)
                         for ck in ('tl', 'tr', 'bl', 'br')}

        band_outer_img = build_lshape_mask(
            (w, h), outer_at, cut_corner,
            outer_cut_w, outer_cut_h,
            outer_radii_i, fill_value=255)

        # 内层 L 形（偏移 t_outer + t_layer）
        t_inner = t_outer + t_layer
        inner_at = RectShape(
            x=outer.x + t_inner, y=outer.y + t_inner,
            w=max(1, outer.w - 2 * t_inner),
            h=max(1, outer.h - 2 * t_inner),
            corner_r=0.0
        )
        inner_cut_w = max(0.0, cut_w - t_inner)
        inner_cut_h = max(0.0, cut_h - t_inner)
        inner_radii_i = {ck: max(0, corners.get(ck, 0.0) - t_inner)
                         for ck in ('tl', 'tr', 'bl', 'br')}

        band_inner_img = build_lshape_mask(
            (w, h), inner_at, cut_corner,
            inner_cut_w, inner_cut_h,
            inner_radii_i, fill_value=255)

        band = np.array(band_outer_img, dtype=bool) & ~np.array(band_inner_img, dtype=bool)
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
