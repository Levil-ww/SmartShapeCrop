"""
core/geometry.py
参数化形状定义 + 生成 PIL 掩膜（mask）
单位：像素（渲染时用）；UI 输入单位：厘米（通过 DPI 转换）
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal
import copy
import logging
import numpy as np
from PIL import Image, ImageDraw

from .config import CM_PER_INCH  # [N-P2-08] 集中换算常量

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
        # F1 守卫：负宽/负高矩形折叠为零面积退化矩形（x1/y1 归一到 x0/y0），
        # 不再产生 x1<x0 的反转 box，避免 PIL ImageDraw 抛 ValueError
        x0, y0 = int(round(self.x)), int(round(self.y))
        x1, y1 = int(round(self.right)), int(round(self.bottom))
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
class CutRect:
    """阶梯 L 形的单级挖角矩形（厘米）。

    anchor 为锚定角；offset_x_cm / offset_y_cm 是挖角矩形靠近锚定角的
    那一角相对两条锚定边向内收缩的距离。旧 (corner, cut_w, cut_h)
    等价于 offset ≡ 0 的 CutRect。
    """
    anchor: Literal['tl', 'tr', 'bl', 'br'] = 'tr'
    offset_x_cm: float = 0.0
    offset_y_cm: float = 0.0
    w_cm: float = 0.0
    h_cm: float = 0.0


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
    # 多角扩展：单位仍为像素；为空时完全等价于旧单角字段。
    cuts: list[dict] = field(default_factory=list)
    # 阶梯扩展：偏移感知 cut 列表（像素 dict：corner/cut_w/cut_h/offset_x/offset_y）；
    # 非空时优先于 cuts 表达几何。
    cut_rects: list[dict] = field(default_factory=list)

    def cut_specs(self) -> list[tuple[str, float, float]]:
        """返回本 L 形实际使用的挖角列表，兼容旧单角对象与阶梯 cut_rects。"""
        if self.cuts:
            return [(str(c['corner']), float(c['cut_w']), float(c['cut_h']))
                    for c in self.cuts]
        if self.cut_rects:
            # 阶梯 CutRect 的边界近似（丢弃 offset），供 3 元组解包的下游使用
            return [(str(c['corner']), float(c['cut_w']), float(c['cut_h']))
                    for c in self.cut_rects]
        return [(self.corner, self.cut_w, self.cut_h)]

    def cut_rect_specs(self) -> list[dict]:
        """偏移感知 cut 列表（像素 dict），供 build_lshape_mask 阶梯渲染。"""
        if self.cut_rects:
            return [dict(c) for c in self.cut_rects]
        return [{'corner': ck, 'cut_w': cw, 'cut_h': ch, 'offset_x': 0.0, 'offset_y': 0.0}
                for ck, cw, ch in self.cut_specs()]

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
    outer_margin_cm: float = 0.0   # 外框留白边（水池模式默认不额外留白，花纹素材本身就是外框）

    # —— mode == rect_hole / rect_lshape 时的内挖矩形 ——
    inner_margin_top_cm: float = 8.0
    inner_margin_bottom_cm: float = 8.0
    inner_margin_left_cm: float = 8.0
    inner_margin_right_cm: float = 8.0

    # —— mode == rect_lshape 额外参数 ——
    l_corner: Literal['tl', 'tr', 'bl', 'br'] = 'br'
    l_cut_w_cm: float = 15.0
    l_cut_h_cm: float = 10.0
    # 多角 L 形：[{"corner": "tr", "cut_w_cm": 28, "cut_h_cm": 8}, ...]
    # 空列表表示沿用旧的 l_corner/l_cut_w_cm/l_cut_h_cm。
    l_cuts_cm: list[dict] = field(default_factory=list)
    # 单边阶梯 L 形：CutRect 列表（厘米）；非空时优先于 l_cuts_cm 表达几何。
    l_cut_rects: list[CutRect] = field(default_factory=list)

    # —— 四个角的圆角半径（厘米），0 表示无圆角 ——
    corner_tl_cm: float = 0.0
    corner_tr_cm: float = 0.0
    corner_bl_cm: float = 0.0
    corner_br_cm: float = 0.0

    # —— mode == ellipse_hole 椭圆直径（厘米） ——
    # 0 表示自动按四边距计算；大于 0 时作为用户手动输入的直径。
    ellipse_diameter_w_cm: float = 0.0
    ellipse_diameter_h_cm: float = 0.0
    # 旧比例字段保留以兼容旧设计文件，但不再参与几何计算。
    ellipse_rx_ratio: float = 0.35
    ellipse_ry_ratio: float = 0.30

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
    pool_inner_material_image: str | None = None  # 水池内挖素材图：空白模式下为None，素材填充模式下为匹配路径

    # [Fix 2026-08-26] 水池素材原始设计方向尺寸（文件名解析的 w×h，未经 oriented 交换）
    # 用于渲染时判断素材图是否需要旋转90度后再等比缩放（避免 cover 过度裁剪 / stretch 变形）
    # 例：文件名 "中古大花:58x121CM" → w=58, h=121 (竖版设计)；画布交换后为 122×51 横版
    # → 素材应先旋转90度 (变成横版内容布局) 再按物理等比缩放到画布
    pool_material_design_w_cm: float = 0.0
    pool_material_design_h_cm: float = 0.0

    # ===== [多洞 Add-On 2026-08-29] 纯加性字段；默认值保持旧行为零变化 =====
    # 当 sketch 识别为 is_multi_hole=True 时，PoolWorker 会填充：
    #   pool_is_multi_hole = True
    #   pool_holes_cm = list[dict] 每个洞的画布相对坐标（厘米）{x_cm, y_cm, w_cm, h_cm}
    #   pool_holes_gaps_cm = list[float] N-1 个洞间隙（仅 UI 展示/调试用）
    #
    # image_ops._get_inner_pixel_mask 会在 mode=='rect_hole' 且 len(pool_holes_cm)>=2 时
    # 走 Add-On 的 UNION mask 分支，完全不触碰下面的单洞 inner_rect_px 逻辑。
    pool_is_multi_hole: bool = False
    pool_holes_cm: list = field(default_factory=list)
    pool_holes_gaps_cm: list = field(default_factory=list)

    # ===== [V13 集成 2026-09-04] L 形挖角手动边框覆盖（纯加性，默认 None 保留旧行为）=====
    # 来源：E:\ima-测试L型挖角输出\确认V13\lshape_crop.py 的 --edge / --band / --band-color
    # 触发逻辑（在 lshape_border.apply_lshape_border_completion 内部新增分支，未修改原有 if 链）：
    #   - 任一字段非 None → 走 V13 路径（手动值优先，缺失项用 V13 detect_border_v13 自动检测补齐）
    #   - 全部 None → 走原有 detect_pool_material_borders 路径（向后兼容）
    # 字段语义：
    #   lshape_manual_edge_px:    黑描边宽（像素，原始素材坐标系）。
    #                             None=自动检测；0=无黑描边（极少见，仅纯主带素材）。
    #   lshape_manual_band_px:     主色带宽（像素，原始素材坐标系）。
    #                             None=自动检测；0=无主色带（如 庄园秘境/戏蝶/中古大花 纯黑宽边）。
    #   lshape_manual_band_color:  主色带颜色 RGB。
    #                             None=自动检测；band_px>0 时必须能拿到颜色（手动或自动）。
    # 厚度换算：V13 检测值基于原始素材图，渲染时按 scale_x/scale_y 换算到画布坐标系
    #          （与现有 detect_pool_material_borders 同一处理路径）。
    lshape_manual_edge_px: int | None = None
    lshape_manual_band_px: int | None = None
    lshape_manual_band_color: tuple[int, int, int] | None = None

    # —— 渲染加速：Worker 预加载的模板图缓存 ——
    # [Fix 2026-09-04] _cached_outer_src 记录缓存图对应的素材路径。
    #   此前缓存一经写入便永不失效：用户更换素材时主线程只改标量字段
    #   （见 canvas_widget.py:195 的设计说明），渲染仍取到旧图，
    #   表现为"素材残留"——该问题被修复 5 次以上仍反复复发。
    #   现在渲染时校验来源，路径不符即视为缓存失效，回退从磁盘加载。
    _cached_outer_image: Image.Image | None = None
    _cached_outer_src: str | None = None

# —— 辅助：像素级尺寸换算 ——
    # [N-P2-08] 集中换算说明：
    #   本方法返回 float（不取整、无 max(1,) 下限），供内部精确几何计算
    #   （含 1px 以下小数值的乘加运算，被 image_ops.py 约 20 处依赖）。
    #   对外/显示型取整换算请用 core.config.cm_to_px / px_to_cm。
    def cm2px(self, cm: float) -> float:
        return cm * self.dpi / CM_PER_INCH

    _VALID_MODES = frozenset({'rect_hole', 'rect_lshape', 'ellipse_hole'})
    _VALID_CORNERS = frozenset({'tl', 'tr', 'bl', 'br'})

    def validate(self) -> None:
        """校验设计参数，非法值抛出 ValueError。"""
        if self.canvas_w_cm <= 0:
            raise ValueError(f"canvas_w_cm 必须为正数，当前值: {self.canvas_w_cm}")
        if self.canvas_h_cm <= 0:
            raise ValueError(f"canvas_h_cm 必须为正数，当前值: {self.canvas_h_cm}")
        if self.dpi <= 0:
            raise ValueError(f"dpi 必须为正整数，当前值: {self.dpi}")
        if self.mode not in self._VALID_MODES:
            raise ValueError(f"mode 无效: {self.mode!r}，有效值: {sorted(self._VALID_MODES)}")
        if self.outer_margin_cm < 0:
            raise ValueError(f"outer_margin_cm 不能为负数，当前值: {self.outer_margin_cm}")
        if self.inner_margin_top_cm < 0:
            raise ValueError(f"inner_margin_top_cm 不能为负数，当前值: {self.inner_margin_top_cm}")
        if self.inner_margin_bottom_cm < 0:
            raise ValueError(f"inner_margin_bottom_cm 不能为负数，当前值: {self.inner_margin_bottom_cm}")
        if self.inner_margin_left_cm < 0:
            raise ValueError(f"inner_margin_left_cm 不能为负数，当前值: {self.inner_margin_left_cm}")
        if self.inner_margin_right_cm < 0:
            raise ValueError(f"inner_margin_right_cm 不能为负数，当前值: {self.inner_margin_right_cm}")
        if self.ellipse_diameter_w_cm < 0:
            raise ValueError(f"ellipse_diameter_w_cm 不能为负数，当前值: {self.ellipse_diameter_w_cm}")
        if self.ellipse_diameter_h_cm < 0:
            raise ValueError(f"ellipse_diameter_h_cm 不能为负数，当前值: {self.ellipse_diameter_h_cm}")
        for name in ('corner_tl_cm', 'corner_tr_cm', 'corner_bl_cm', 'corner_br_cm'):
            v = getattr(self, name)
            if v < 0:
                raise ValueError(f"{name} 不能为负数，当前值: {v}")
            half = min(self.canvas_w_cm, self.canvas_h_cm) / 2.0
            if v > half:
                raise ValueError(f"{name}={v}cm 超过画布尺寸的一半 ({half:.1f}cm)")
        if self.mode == 'rect_lshape':
            if self.l_corner not in self._VALID_CORNERS:
                raise ValueError(f"l_corner 无效: {self.l_corner!r}，有效值: {sorted(self._VALID_CORNERS)}")
            if self.l_cut_w_cm <= 0:
                raise ValueError(f"l_cut_w_cm 必须为正数，当前值: {self.l_cut_w_cm}")
            if self.l_cut_h_cm <= 0:
                raise ValueError(f"l_cut_h_cm 必须为正数，当前值: {self.l_cut_h_cm}")
            if len(self.l_cuts_cm) > 4:
                raise ValueError("l_cuts_cm 最多支持 4 个挖角")
            seen = set()
            for cut in self.l_cuts_cm:
                corner = cut.get('corner')
                if corner not in self._VALID_CORNERS:
                    raise ValueError(f"l_cuts_cm corner 无效: {corner!r}")
                if corner in seen:
                    raise ValueError(f"l_cuts_cm 不允许重复角位: {corner!r}")
                seen.add(corner)
                if float(cut.get('cut_w_cm', 0)) <= 0 or float(cut.get('cut_h_cm', 0)) <= 0:
                    raise ValueError("l_cuts_cm 的宽高必须为正数")

            # l_cut_rects 非空时是唯一几何来源，其「不重叠 + 不越界」约束
            # 已由 _validate_l_cut_rects 接管；旧边约束仅作用于 l_cuts_cm 路径
            if not self.l_cut_rects:
                cuts = self.l_cuts_cm or [{
                    'corner': self.l_corner,
                    'cut_w_cm': self.l_cut_w_cm,
                    'cut_h_cm': self.l_cut_h_cm,
                }]
                cut_by_corner = {cut['corner']: cut for cut in cuts}
                inner_w_cm = self.canvas_w_cm - 2 * self.outer_margin_cm \
                    - self.inner_margin_left_cm - self.inner_margin_right_cm
                inner_h_cm = self.canvas_h_cm - 2 * self.outer_margin_cm \
                    - self.inner_margin_top_cm - self.inner_margin_bottom_cm
                edge_clearance_cm = 0.5
                edge_limits = (
                    ('上边', ('tl', 'tr'), 'cut_w_cm', inner_w_cm),
                    ('下边', ('bl', 'br'), 'cut_w_cm', inner_w_cm),
                    ('左边', ('tl', 'bl'), 'cut_h_cm', inner_h_cm),
                    ('右边', ('tr', 'br'), 'cut_h_cm', inner_h_cm),
                )
                for edge_name, corners, size_key, edge_length_cm in edge_limits:
                    # F1 守卫：内矩形退化（边距之和超过画布）时边长为负，
                    # 边长校验对任何挖角都会误报超限，交给渲染层退化守卫处理
                    if edge_length_cm <= 0:
                        continue
                    edge_sum_cm = sum(
                        float(cut_by_corner[corner][size_key])
                        for corner in corners
                        if corner in cut_by_corner
                    )
                    if edge_sum_cm > edge_length_cm - edge_clearance_cm:
                        raise ValueError(
                            f"L 形挖角在{edge_name}上的尺寸和 {edge_sum_cm:g}cm "
                            f"必须小于外边长度 {edge_length_cm:g}cm 减 {edge_clearance_cm:g}cm 余量"
                        )

            if self.l_cut_rects:
                self._validate_l_cut_rects()

    def _validate_l_cut_rects(self) -> None:
        """阶梯 l_cut_rects 校验：同角 ≤3 级、正宽高、非负偏移、不越界、两两不重叠。"""
        per_anchor: dict[str, int] = {}
        for i, cut in enumerate(self.l_cut_rects):
            if cut.anchor not in self._VALID_CORNERS:
                raise ValueError(f"l_cut_rects[{i}] anchor 无效: {cut.anchor!r}")
            n = per_anchor.get(cut.anchor, 0) + 1
            per_anchor[cut.anchor] = n
            if n > 3:
                raise ValueError(f"l_cut_rects 同角位 {cut.anchor!r} 最多支持 3 级阶梯")
            if cut.w_cm <= 0 or cut.h_cm <= 0:
                raise ValueError(f"l_cut_rects[{i}] 宽高必须为正数")
            if cut.offset_x_cm < 0 or cut.offset_y_cm < 0:
                raise ValueError(f"l_cut_rects[{i}] offset 不能为负数")

        inner_w_cm = self.canvas_w_cm - 2 * self.outer_margin_cm \
            - self.inner_margin_left_cm - self.inner_margin_right_cm
        inner_h_cm = self.canvas_h_cm - 2 * self.outer_margin_cm \
            - self.inner_margin_top_cm - self.inner_margin_bottom_cm
        edge_clearance_cm = 0.5
        # F1 守卫同款：内矩形退化（边长 ≤ 0）时跳过越界/重叠校验，交给渲染层退化守卫
        if inner_w_cm <= 0 or inner_h_cm <= 0:
            return
        boxes = []
        for i, cut in enumerate(self.l_cut_rects):
            if cut.offset_x_cm + cut.w_cm > inner_w_cm - edge_clearance_cm:
                raise ValueError(f"l_cut_rects[{i}] 超出外框可用宽度")
            if cut.offset_y_cm + cut.h_cm > inner_h_cm - edge_clearance_cm:
                raise ValueError(f"l_cut_rects[{i}] 超出外框可用高度")
            box = _rect_from_anchor_offset(
                RectShape(0.0, 0.0, inner_w_cm, inner_h_cm),
                cut.anchor, cut.offset_x_cm, cut.offset_y_cm, cut.w_cm, cut.h_cm)
            boxes.append((box.x, box.y, box.x + box.w, box.y + box.h))
        eps = 1e-6
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                ax0, ay0, ax1, ay1 = boxes[i]
                bx0, by0, bx1, by1 = boxes[j]
                ox = min(ax1, bx1) - max(ax0, bx0)
                oy = min(ay1, by1) - max(ay0, by0)
                if ox > eps and oy > eps:
                    raise ValueError(f"l_cut_rects[{i}] 与 l_cut_rects[{j}] 挖角区域重叠")

    @property
    def canvas_w_px(self) -> int:
        return int(round(self.cm2px(self.canvas_w_cm)))

    @property
    def canvas_h_px(self) -> int:
        return int(round(self.cm2px(self.canvas_h_cm)))

    # 像素级坐标计算
    def outer_rect_px(self) -> RectShape:
        m = self.cm2px(self.outer_margin_cm)
        # F1 守卫：外边距爆炸时 clamp 宽高到非负，避免产生负宽/负高矩形
        return RectShape(m, m,
                         max(0.0, self.canvas_w_px - 2 * m),
                         max(0.0, self.canvas_h_px - 2 * m))

    def inner_rect_px(self) -> RectShape:
        outer = self.outer_rect_px()
        mt = self.cm2px(self.inner_margin_top_cm)
        mb = self.cm2px(self.inner_margin_bottom_cm)
        ml = self.cm2px(self.inner_margin_left_cm)
        mr = self.cm2px(self.inner_margin_right_cm)
        # F1 守卫：内边距之和超过外框可用空间时 clamp 宽高到非负
        return RectShape(outer.x + ml, outer.y + mt,
                         max(0.0, outer.w - ml - mr),
                         max(0.0, outer.h - mt - mb))

    def ellipse_px(self) -> EllipseShape:
        outer_x_cm = self.outer_margin_cm
        outer_y_cm = self.outer_margin_cm
        inner_x_cm = outer_x_cm + self.inner_margin_left_cm
        inner_y_cm = outer_y_cm + self.inner_margin_top_cm
        # 画布比草图外框多出的 1cm 损耗只向右/向下延展椭圆，
        # 不改变草图定义的左、上、右、下边距。这样直径增加 1cm，
        # 半径增加 0.5cm，但输出画布四边到椭圆的边距保持原值。
        inner_w_cm = max(
            0.0,
            self.canvas_w_cm - 2 * self.outer_margin_cm
            - self.inner_margin_left_cm - self.inner_margin_right_cm,
        )
        inner_h_cm = max(
            0.0,
            self.canvas_h_cm - 2 * self.outer_margin_cm
            - self.inner_margin_top_cm - self.inner_margin_bottom_cm,
        )
        inner_x = self.cm2px(inner_x_cm)
        inner_y = self.cm2px(inner_y_cm)
        inner_w = self.cm2px(inner_w_cm)
        inner_h = self.cm2px(inner_h_cm)
        diameter_w_cm = self.ellipse_diameter_w_cm or inner_w_cm
        diameter_h_cm = self.ellipse_diameter_h_cm or inner_h_cm
        diameter_w = self.cm2px(max(0.0, diameter_w_cm))
        diameter_h = self.cm2px(max(0.0, diameter_h_cm))
        return EllipseShape(
            cx=inner_x + diameter_w / 2,
            cy=inner_y + diameter_h / 2,
            rx=diameter_w / 2,
            ry=diameter_h / 2,
        )

    def l_shape_px(self) -> LShape:
        return LShape(
            outer=self.inner_rect_px(),
            corner=self.l_corner,
            cut_w=self.cm2px(self.l_cut_w_cm),
            cut_h=self.cm2px(self.l_cut_h_cm),
        )

    def l_shapes_px(self) -> LShape:
        """返回含多角/阶梯 cut 列表的 LShape；旧字段仍作为兼容主 cut 保留。"""
        shape = self.l_shape_px()
        if self.l_cut_rects:
            shape.cut_rects = [
                {
                    'corner': cut.anchor,
                    'cut_w': self.cm2px(float(cut.w_cm)),
                    'cut_h': self.cm2px(float(cut.h_cm)),
                    'offset_x': self.cm2px(float(cut.offset_x_cm)),
                    'offset_y': self.cm2px(float(cut.offset_y_cm)),
                }
                for cut in self.l_cut_rects
            ]
        elif self.l_cuts_cm:
            shape.cuts = [
                {
                    'corner': cut['corner'],
                    'cut_w': self.cm2px(float(cut['cut_w_cm'])),
                    'cut_h': self.cm2px(float(cut['cut_h_cm'])),
                }
                for cut in self.l_cuts_cm[:4]
            ]
        return shape

    @property
    def corners_px(self) -> dict[str, float]:
        """返回四个角的像素级圆角半径字典"""
        return {
            'tl': self.cm2px(self.corner_tl_cm),
            'tr': self.cm2px(self.corner_tr_cm),
            'bl': self.cm2px(self.corner_bl_cm),
            'br': self.cm2px(self.corner_br_cm),
        }

    def clone(self) -> 'CropDesign':
        """深拷贝独立快照，供后台渲染线程使用（F4 并发防护）。

        - 标量字段：值拷贝（天然独立）
        - borders / border_text 等可变字段：深拷贝，互不影响
        - _cached_outer_image：共享只读引用（避免每次渲染复制大图）
        - _cached_outer_src：来源路径，随缓存一同共享，用于渲染时校验失效
        """
        c = copy.deepcopy(self)
        # 大图模板缓存只读共享，不复制像素数据
        c._cached_outer_image = self._cached_outer_image
        c._cached_outer_src = self._cached_outer_src
        return c


# ---------- 掩膜（mask）生成 ----------

def make_mask(size: tuple[int, int]) -> Image.Image:
    """创建一张全黑的 L 模式图（0=被挖掉，255=保留）"""
    return Image.new('L', size, 0)


def fill_rect_mask(mask: Image.Image, rect: RectShape, value: int = 255) -> None:
    """在 mask 上填充一个矩形区域为 value"""
    # F1 守卫：退化矩形（负宽/负高/零面积）跳过，避免 PIL ValueError 崩溃
    if rect.w <= 0 or rect.h <= 0:
        return
    d = ImageDraw.Draw(mask)
    if rect.corner_r > 0:
        d.rounded_rectangle(rect.to_int_tuple(),
                            radius=int(rect.corner_r), fill=value)
    else:
        d.rectangle(rect.to_int_tuple(), fill=value)


def fill_ellipse_mask(mask: Image.Image, e: EllipseShape, value: int = 255) -> None:
    # F1 守卫：负/零半径跳过（PIL ellipse 对反转 box 会抛 ValueError）
    if e.rx <= 0 or e.ry <= 0:
        return
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
    w_i = right_i - x_i
    h_i = bottom_i - y_i
    # F1 守卫：退化矩形（负宽/负高）跳过，避免 PIL ValueError
    if w_i <= 0 or h_i <= 0:
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
    if design.mode == 'ellipse_hole' and design.borders:
        # F9：椭圆模式不支持多层边框 —— 渲染层告警，行为保持不变
        logger.warning('[geometry] 椭圆模式不支持多层边框，仅按现有逻辑产出边框带（行为不变）')
    if design.mode == 'rect_lshape':
        return compute_lshape_border_bands(design)
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

# 2. 按每层边框 offset 切分 band。
    #    [H-01] 内存优化：相邻层共享边界 —— 第 i 层的外边界 == 第 i-1 层的内边界
    #    (t_inner_{i-1} = cumulative + t_layer == t_outer_i)，因此每层只需构建一张
    #    内边界 PIL mask，外边界直接复用前一层的内边界 bool 数组做差集，避免为每层
    #    重复创建两张满尺寸 PIL mask（导出大图时显著降低峰值内存）。
    bands: list[tuple[np.ndarray, BorderLayer]] = []
    cumulative_offset = 0
    # 首层外边界 t=0 即 outer 本身，与 frame_outer 参数完全一致，直接复用其 bool 数组
    prev_inner_bool: np.ndarray = np.array(frame_outer_img, dtype=bool)

    for layer in design.borders:
        layer.offset_px = design.cm2px(layer.offset_cm)
        t_layer = int(round(max(1, layer.offset_px)))
        t_outer = cumulative_offset
        cumulative_offset += t_layer

        # 内层：距 outer_rect 偏移 t_outer + t_layer 的圆角矩形
        t_inner = t_outer + t_layer
        inner_rect_i = RectShape(
            x=outer.x + t_inner, y=outer.y + t_inner,
            w=outer.w - 2 * t_inner, h=outer.h - 2 * t_inner,
            corner_r=0.0
        )
        inner_radii_i = {ck: max(0, corners.get(ck, 0.0) - t_inner) for ck in ('tl', 'tr', 'bl', 'br')}

        band_inner_img = make_mask((w, h))
        fill_rect_mask(band_inner_img, inner_rect_i, 255)
        if any(r > 0 for r in inner_radii_i.values()):
            apply_rounded_corners_to_mask(band_inner_img, inner_rect_i, inner_radii_i, fill_value=255)
        band_inner_bool = np.array(band_inner_img, dtype=bool)

        # band = 本层外边界(复用前层内边界) - 本层内边界
        band = prev_inner_bool & ~band_inner_bool
        bands.append((band, layer))
        prev_inner_bool = band_inner_bool

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

    # [Fix 2026-09-12 N1-01] 钳制 cw/ch 到可用宽/高，防止负坐标导致 numpy 负索引静默切错
    avail_w = max(0.0, new_right - new_x)
    avail_h = max(0.0, new_bottom - new_y)
    cw = max(0.0, min(cut_w - offset, avail_w))
    ch = max(0.0, min(cut_h - offset, avail_h))

    if corner_key == 'tl':
        return RectShape(new_x, new_y, cw, ch)
    elif corner_key == 'tr':
        return RectShape(new_right - cw, new_y, cw, ch)
    elif corner_key == 'bl':
        return RectShape(new_x, new_bottom - ch, cw, ch)
    else:  # br
        return RectShape(new_right - cw, new_bottom - ch, cw, ch)


def _rect_from_anchor_offset(outer_rect: RectShape, anchor: str,
                             offset_x: float, offset_y: float,
                             w: float, h: float) -> RectShape:
    """
    按「锚定角 + 相对两条锚定边的内缩偏移」计算单级挖角矩形（像素）。

    offset_x/offset_y 是挖角矩形靠近锚定角的那一角向内收缩的距离；
    offset ≡ 0 时与 _get_lshape_cut_rect_at_offset(outer_rect, anchor, w, h, 0) 等价。
    [N1-01 同款钳制] cw/ch 钳制到可用宽/高，防止负坐标导致 numpy 负索引静默切错。
    """
    avail_w = max(0.0, outer_rect.w - offset_x)
    avail_h = max(0.0, outer_rect.h - offset_y)
    cw = max(0.0, min(w, avail_w))
    ch = max(0.0, min(h, avail_h))

    if anchor == 'tl':
        return RectShape(outer_rect.x + offset_x, outer_rect.y + offset_y, cw, ch)
    elif anchor == 'tr':
        return RectShape(outer_rect.right - offset_x - cw, outer_rect.y + offset_y, cw, ch)
    elif anchor == 'bl':
        return RectShape(outer_rect.x + offset_x, outer_rect.bottom - offset_y - ch, cw, ch)
    else:  # br
        return RectShape(outer_rect.right - offset_x - cw,
                         outer_rect.bottom - offset_y - ch, cw, ch)


def build_lshape_mask(size: tuple[int, int],
                       outer_rect: RectShape, corner_key: str,
                       cut_w: float, cut_h: float,
                       radii: dict[str, float],
                       fill_value: int = 255,
                       cuts: list[tuple[str, float, float] | dict] | None = None) -> Image.Image:
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

    raw_specs = cuts or [(corner_key, cut_w, cut_h)]
    valid_cuts = []
    for spec in raw_specs:
        if isinstance(spec, dict):
            ck = str(spec['corner'])
            cw = float(spec['cut_w'])
            ch = float(spec['cut_h'])
            cut = _rect_from_anchor_offset(outer_rect, ck,
                                           float(spec.get('offset_x', 0.0)),
                                           float(spec.get('offset_y', 0.0)), cw, ch)
        else:
            ck, cw, ch = spec
            cut = _get_lshape_cut_rect_at_offset(outer_rect, ck, cw, ch, 0)
        valid_cuts.append((ck, cw, ch, cut))
    valid_cuts = [(ck, cw, ch, cut) for ck, cw, ch, cut in valid_cuts
                  if cut.w > 0.5 and cut.h > 0.5]
    has_cut = bool(valid_cuts)

    if has_cut:
        # Step 2: 挖掉 cut 区域
        for _, _, _, cut in valid_cuts:
            fill_rect_mask(m, cut, other)

        # Step 3: 圆角处理 outer_rect 的 3 个非 cut 角
        for ck in ('tl', 'tr', 'bl', 'br'):
            if any(ck == cut_corner for cut_corner, _, _, _ in valid_cuts):
                continue
            r = radii.get(ck, 0.0)
            if r > 0.5:
                corner_radii = {k: (r if k == ck else 0.0) for k in ('tl', 'tr', 'bl', 'br')}
                apply_rounded_corners_to_mask(m, outer_rect, corner_radii, fill_value=fill_value)

        # Step 4: 圆角处理内部 L 形拐角（cut_rect 的对角）
        opposite_map = {'br': 'tl', 'tr': 'bl', 'bl': 'tr', 'tl': 'br'}
        for cut_corner, _, _, cut in valid_cuts:
            internal_key = opposite_map[cut_corner]
            internal_r = radii.get(cut_corner, 0.0)
            if internal_r > 0.5:
                corner_radii = {k: (internal_r if k == internal_key else 0.0)
                                for k in ('tl', 'tr', 'bl', 'br')}
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
    lshape = design.l_shapes_px()
    cut_specs = lshape.cut_specs()
    cut_corner = lshape.corner
    cut_w = lshape.cut_w
    cut_h = lshape.cut_h
    corners = design.corners_px

    # 1. 计算 frame_mask（总边框带）
    frame_outer_img = build_lshape_mask(
        (w, h), outer, cut_corner, cut_w, cut_h, corners,
        fill_value=255, cuts=cut_specs)

    inner_corners = compute_inner_corner_radii(outer, inner, corners)
    frame_inner_img = build_lshape_mask(
        (w, h), inner, cut_corner, cut_w, cut_h, inner_corners,
        fill_value=255, cuts=cut_specs)

    frame_mask = np.array(frame_outer_img, dtype=bool) & ~np.array(frame_inner_img, dtype=bool)

# 2. 按每层 border offset 切分 band
    #    [H-01] 内存优化：与 rect_hole 同理，第 i 层外边界 == 第 i-1 层内边界，
    #    每层只构建一张内边界 L 形 mask，外边界复用前一层的内边界 bool 数组。
    bands: list[tuple[np.ndarray, BorderLayer]] = []
    cumulative_offset = 0
    # 首层外边界 t=0 即 outer，与 frame_outer 参数完全一致，直接复用其 bool 数组
    prev_inner_bool: np.ndarray = np.array(frame_outer_img, dtype=bool)

    for layer in design.borders:
        layer.offset_px = design.cm2px(layer.offset_cm)
        t_layer = int(round(max(1, layer.offset_px)))
        t_outer = cumulative_offset
        cumulative_offset += t_layer

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
            inner_radii_i, fill_value=255,
            cuts=[(ck, max(0.0, cw - t_inner), max(0.0, ch - t_inner))
                   for ck, cw, ch in cut_specs])
        band_inner_bool = np.array(band_inner_img, dtype=bool)

        # band = 本层外边界(复用前层内边界) - 本层内边界
        band = prev_inner_bool & ~band_inner_bool
        bands.append((band, layer))
        prev_inner_bool = band_inner_bool

    # 3. 处理剩余区域
    all_bands = np.zeros((h, w), dtype=bool)
    for b, _ in bands:
        all_bands = all_bands | b
    remaining = frame_mask & (~all_bands)
    if remaining.any():
        extra = BorderLayer(fill_type='solid', color=design.hole_bg_color)
        bands.append((remaining, extra))

    return bands
