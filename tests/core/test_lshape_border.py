"""
测试：core/lshape_border.py —— L 形挖角的素材边框补全

背景：本模块于 2026-09-03 新增（512 行），此前**零测试覆盖**。
本文件补齐首批断言，覆盖四个对外/内部函数的稳定契约：

  - compute_cut_edge_bboxes        纯几何（最稳定，优先覆盖）
  - detect_pool_material_borders   素材边框检测（含伪边框过滤）
  - _filter_content_layers         内容匹配层过滤
  - apply_lshape_border_completion 端到端入口（含"无边框应跳过"契约）

设计原则：
  - 几何断言聚焦**契约**（边缘数量、厚度、与 cut 区的相对位置），
    而非硬编码 ±0.5 像素中心偏移，避免实现微调造成假阳性失败。
  - 素材图全部用 PIL 现场合成，不依赖外部图片文件，也不需要 Tesseract。
"""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from core.geometry import RectShape
from core.lshape_border import (
    _filter_content_layers,
    apply_lshape_border_completion,
    compute_cut_edge_bboxes,
    detect_pool_material_borders,
)


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------

# 外框：ox=100, oy=200, oright=1100, obottom=1000
def _outer_rect() -> RectShape:
    return RectShape(x=100, y=200, w=1000, h=800)


CUT_W = 300.0   # 挖角宽（像素）
CUT_H = 150.0   # 挖角高（像素）
BX = 20.0       # 边框总厚度（像素）


def _split_edges(edges):
    """把 bbox 列表分成 (水平边缘, 垂直边缘)：较宽的为水平，较高的为垂直。"""
    horiz = [e for e in edges if (e[2] - e[0]) > (e[3] - e[1])]
    vert = [e for e in edges if (e[3] - e[1]) >= (e[2] - e[0])]
    return horiz, vert


def _make_bordered_material(size=(400, 300), border=20,
                            color=(0, 0, 0), fill=(255, 255, 255)) -> Image.Image:
    """合成"带纯色边框 + 纯色中心"的素材图（模拟克罗印花/安妮森林一类带框素材）。"""
    img = Image.new('RGB', size, fill)
    ImageDraw.Draw(img).rectangle(
        [0, 0, size[0] - 1, size[1] - 1], outline=color, width=border)
    return img


def _make_plain_material(size=(400, 300), fill=(255, 255, 255)) -> Image.Image:
    """合成无边框的纯色素材图。"""
    return Image.new('RGB', size, fill)


# ---------------------------------------------------------------------------
# 1. compute_cut_edge_bboxes —— 纯几何
# ---------------------------------------------------------------------------

class TestComputeCutEdgeBboxes:
    """L 形挖角产生两条新边缘（1 水平 + 1 垂直），每条向 cut 区内延伸 bx 像素。"""

    def test_thickness_below_half_returns_empty(self):
        """厚度 ≤ 0.5px 视为无边框 → 返回空列表（不绘制）。"""
        assert compute_cut_edge_bboxes(_outer_rect(), 'tl', CUT_W, CUT_H, 0.5) == []
        assert compute_cut_edge_bboxes(_outer_rect(), 'tl', CUT_W, CUT_H, 0.0) == []
        assert compute_cut_edge_bboxes(_outer_rect(), 'tl', CUT_W, CUT_H, -5.0) == []

    @pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
    def test_each_corner_returns_two_edges(self, corner):
        """四角各返回 2 条边缘：1 条水平 + 1 条垂直。"""
        edges = compute_cut_edge_bboxes(_outer_rect(), corner, CUT_W, CUT_H, BX)
        assert len(edges) == 2, f'{corner} 应返回 2 条边缘，实际 {edges}'
        horiz, vert = _split_edges(edges)
        assert len(horiz) == 1 and len(vert) == 1, f'{corner} 边缘方向异常: {edges}'

    @pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
    def test_edge_thickness_equals_border_thickness(self, corner):
        """每条边缘的"厚度维"必须等于请求的总边框厚度 bx。"""
        edges = compute_cut_edge_bboxes(_outer_rect(), corner, CUT_W, CUT_H, BX)
        horiz, vert = _split_edges(edges)
        # 水平边缘的厚度在 y 方向；垂直边缘的厚度在 x 方向
        assert (horiz[0][3] - horiz[0][1]) == pytest.approx(BX)
        assert (vert[0][2] - vert[0][0]) == pytest.approx(BX)

    def test_tl_edges_anchor_at_cut_region_bottom_and_right(self):
        """tl 挖角：保留区在右下 → 水平边缘贴 cut 区底部，垂直边缘贴 cut 区右部。"""
        r = _outer_rect()
        edges = compute_cut_edge_bboxes(r, 'tl', CUT_W, CUT_H, BX)
        horiz, vert = _split_edges(edges)

        # 水平边缘：y1 应贴在 cut 区底边 oy + CUT_H（±1px 容差）
        assert horiz[0][3] == pytest.approx(r.y + CUT_H, abs=1.0)
        # 水平边缘沿 x 覆盖整个 cut 区宽度
        assert (horiz[0][2] - horiz[0][0]) == pytest.approx(CUT_W + 1.0, abs=1.0)

        # 垂直边缘：x1 应贴在 cut 区右边 ox + CUT_W
        assert vert[0][2] == pytest.approx(r.x + CUT_W, abs=1.0)
        # 垂直边缘沿 y 覆盖整个 cut 区高度
        assert (vert[0][3] - vert[0][1]) == pytest.approx(CUT_H + 1.0, abs=1.0)

    def test_br_edges_anchor_at_cut_region_top_and_left(self):
        """br 挖角：保留区在左上 → 水平边缘贴 cut 区顶部，垂直边缘贴 cut 区左部。"""
        r = _outer_rect()
        edges = compute_cut_edge_bboxes(r, 'br', CUT_W, CUT_H, BX)
        horiz, vert = _split_edges(edges)

        # 水平边缘：y0 应贴在 cut 区顶边 obottom - CUT_H
        assert horiz[0][1] == pytest.approx(r.bottom - CUT_H, abs=1.0)
        # 垂直边缘：x0 应贴在 cut 区左边 oright - CUT_W
        assert vert[0][0] == pytest.approx(r.right - CUT_W, abs=1.0)

    def test_four_corners_produce_distinct_regions(self):
        """四角结果必须互不相同 —— 防止角落分支写串（复制粘贴类回归）。"""
        seen = set()
        for corner in ('tl', 'tr', 'bl', 'br'):
            edges = compute_cut_edge_bboxes(_outer_rect(), corner, CUT_W, CUT_H, BX)
            seen.add(tuple(round(v, 1) for e in edges for v in e))
        assert len(seen) == 4, '四角产生了重复的 bbox，角落分支可能写串'

    def test_oversized_thickness_clamped_inside_cut_region(self):
        """厚度远超 cut 区尺寸时，bbox 必须被 clamp 在 cut 区内，不越界到保留区。"""
        r = _outer_rect()
        edges = compute_cut_edge_bboxes(r, 'tl', CUT_W, CUT_H, 5000.0)
        assert len(edges) == 2
        for (x0, y0, x1, y1) in edges:
            # 水平方向不超出 cut 区（ox-1 .. ox+CUT_W+1）
            assert x0 >= r.x - 1.0
            assert x1 <= r.x + CUT_W + 1.0
            # 垂直方向不超出 cut 区（oy-1 .. oy+CUT_H+1）
            assert y0 >= r.y - 1.0
            assert y1 <= r.y + CUT_H + 1.0

    def test_negative_coords_clipped_to_zero(self):
        """外框贴边（x=y=0）时，向内延伸产生的负坐标必须被裁剪，且退化 bbox 被丢弃。"""
        r = RectShape(x=0, y=0, w=1000, h=800)
        edges = compute_cut_edge_bboxes(r, 'tl', CUT_W, CUT_H, BX)
        assert len(edges) >= 1
        for (x0, y0, x1, y1) in edges:
            assert x0 >= 0.0 and y0 >= 0.0, f'负坐标未被裁剪: {(x0, y0, x1, y1)}'
            # 裁剪后仍需是有效矩形（否则 draw 阶段会抛错）
            assert x1 > x0 and y1 > y0


# ---------------------------------------------------------------------------
# 2. detect_pool_material_borders —— 素材边框检测
# ---------------------------------------------------------------------------

class TestDetectPoolMaterialBorders:

    def test_none_image_returns_empty(self):
        """None 输入必须返回空列表，不能抛异常。"""
        assert detect_pool_material_borders(None) == []

    def test_plain_image_has_no_border(self):
        """纯色无边框素材 → 返回空列表（调用方据此跳过补全）。"""
        assert detect_pool_material_borders(_make_plain_material()) == []

    def test_bordered_material_detects_dark_layer(self):
        """合成黑边框素材 → 至少检出 1 层，且颜色接近黑色、厚度接近设定值。"""
        border_px = 20
        img = _make_bordered_material(border=border_px)
        layers = detect_pool_material_borders(img, (255, 255, 255))

        assert layers, '带边框素材未检出任何边框层'
        color, thickness = layers[0]
        # 颜色应接近黑色
        assert all(c <= 30 for c in color), f'检出的边框色不是深色: {color}'
        # 厚度允许 ±30% 误差（检测算法带采样与合并逻辑）
        assert thickness == pytest.approx(border_px, rel=0.3), \
            f'边框厚度偏差过大: {thickness} vs {border_px}'

    def test_oversized_border_rejected_as_pattern(self):
        """总厚度超过短边 30% 的"边框"被判定为图案误检 → 返回空列表。"""
        # 400x300 图中画 120px 边框 → 总厚度远超 300*0.30=90
        img = _make_bordered_material(size=(400, 300), border=120)
        assert detect_pool_material_borders(img, (255, 255, 255)) == []


# ---------------------------------------------------------------------------
# 3. _filter_content_layers —— 内容匹配层过滤
# ---------------------------------------------------------------------------

class TestFilterContentLayers:

    def test_single_layer_returned_untouched(self):
        """只有 1 层时直接返回，不过滤（避免把所有层都滤空）。"""
        layers = [((0, 0, 0), 20)]
        assert _filter_content_layers(_make_plain_material(), layers) == layers

    def test_layer_close_to_center_color_is_removed(self):
        """与中心色接近的层（过渡色/底色）应被剔除，只保留真实边框色。"""
        img = _make_plain_material(fill=(255, 255, 255))  # 中心为纯白
        layers = [((0, 0, 0), 20),        # 黑：距白 ~441 → 保留
                  ((250, 250, 250), 10)]  # 近白：距白 ~8.7 → 过滤
        result = _filter_content_layers(img, layers)
        assert result == [((0, 0, 0), 20)], f'未正确过滤内容匹配层: {result}'


# ---------------------------------------------------------------------------
# 4. apply_lshape_border_completion —— 端到端入口
# ---------------------------------------------------------------------------

class TestApplyLshapeBorderCompletion:

    def _canvas(self, w=800, h=600):
        return np.full((h, w, 3), 255, dtype=np.uint8)

    def test_no_border_material_returns_false_and_leaves_canvas_untouched(self):
        """素材无边框 → 返回 False 且画布**不允许**被修改（关键防御契约）。"""
        canvas = self._canvas()
        before = canvas.copy()
        src = _make_plain_material()

        ok = apply_lshape_border_completion(
            canvas,
            src.resize((800, 600)),
            RectShape(x=0, y=0, w=800, h=600),
            'tl', 200.0, 100.0,
            src_material_img=src,
        )
        assert ok is False
        np.testing.assert_array_equal(canvas, before), '无边框素材不应修改画布'

    def test_bordered_material_paints_cut_edges_only(self):
        """带边框素材 → 返回 True，cut 新边缘的**保留区一侧**被涂色。

        [2026-09-04 V2.2 方向修正] V13 patch_lshape_cut 沿切边向保留区内侧补边：
          - 黑描边在最外层（贴切边），粗细 = 素材黑边 × scale
          - 缺口区（cut 内部）保持原样（hole 底色），不被涂色
        本用例：素材黑边 20px，scale=2 → 画布黑带宽 40px。
        tl 挖角：cut 区 x∈[0,200], y∈[0,100] →
          垂直切边 x=200：黑带 x∈[200,240]，y∈[0,100]
          水平切边 y=100：黑带 y∈[100,140]，x∈[0,200]
        """
        canvas = self._canvas()
        src = _make_bordered_material(size=(400, 300), border=20)

        ok = apply_lshape_border_completion(
            canvas,
            src.resize((800, 600)),
            RectShape(x=0, y=0, w=800, h=600),
            'tl', 200.0, 100.0,
            dpi=150,
            src_material_img=src,
            scale_x=2.0,
            scale_y=2.0,
        )
        assert ok is True

        # 垂直切边 x=200 的保留区一侧：黑带 x∈[200,240]（20px 素材黑边 × 2.0 scale）
        assert canvas[50, 220].max() <= 60, \
            f'垂直边缘保留区侧未涂黑: {canvas[50, 220]}'
        # 水平切边 y=100 的保留区一侧：黑带 y∈[100,140]
        assert canvas[120, 100].max() <= 60, \
            f'水平边缘保留区侧未涂黑: {canvas[120, 100]}'
        # 内凹角 (200, 100) 附近：黑带沿 L 形轮廓连续（d=max(dx,dy)≤40 → 黑）
        assert canvas[115, 215].max() <= 60, \
            f'内凹角黑带断头: {canvas[115, 215]}'

        # 缺口区（cut 内部）必须保持原样 —— patch 不碰缺口区
        np.testing.assert_array_equal(canvas[50, 180], [255, 255, 255],
                                      err_msg='垂直边缘向缺口区内涂色了')
        np.testing.assert_array_equal(canvas[80, 100], [255, 255, 255],
                                      err_msg='水平边缘向缺口区内涂色了')

        # 远离边缘的区域（保留区中心）必须保持未涂色
        np.testing.assert_array_equal(canvas[300, 400], [255, 255, 255])

    def test_none_material_returns_false(self):
        """素材为 None（且未传原始素材）→ 返回 False，不抛异常。"""
        canvas = self._canvas()
        ok = apply_lshape_border_completion(
            canvas,
            None,
            RectShape(x=0, y=0, w=800, h=600),
            'tl', 200.0, 100.0,
        )
        assert ok is False
