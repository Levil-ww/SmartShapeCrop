"""
第三期渲染出口测试：
- _build_design_lshape_mask helper 函数
- compute_lshape_border_bands 通用收缩公式（阶梯 CutRect）
- _shrink_cut_rect 辅助函数
"""
import numpy as np
import pytest
from core.geometry import (
    CropDesign, RectShape, LShape, CutRect,
    build_lshape_mask, compute_lshape_border_bands,
    compute_inner_corner_radii,
    _shrink_cut_rect, _build_design_lshape_mask,
)


# ── _shrink_cut_rect 通用收缩公式 ──────────────────────────────────

class TestShrinkCutRect:
    """offset' = offset + t,  w' = max(0, w - 2t)"""

    def test_basic_shrink(self):
        rect = {'corner': 'br', 'cut_w': 100.0, 'cut_h': 80.0,
                'offset_x': 0.0, 'offset_y': 0.0}
        s = _shrink_cut_rect(rect, 10.0)
        assert s['offset_x'] == 10.0
        assert s['offset_y'] == 10.0
        assert s['cut_w'] == 80.0
        assert s['cut_h'] == 60.0

    def test_shrink_with_existing_offset(self):
        rect = {'corner': 'br', 'cut_w': 100.0, 'cut_h': 80.0,
                'offset_x': 20.0, 'offset_y': 30.0}
        s = _shrink_cut_rect(rect, 5.0)
        assert s['offset_x'] == 25.0
        assert s['offset_y'] == 35.0
        assert s['cut_w'] == 90.0
        assert s['cut_h'] == 70.0

    def test_shrink_to_zero_width(self):
        rect = {'corner': 'br', 'cut_w': 10.0, 'cut_h': 80.0,
                'offset_x': 0.0, 'offset_y': 0.0}
        s = _shrink_cut_rect(rect, 5.0)
        assert s['cut_w'] == 0.0
        assert s['cut_h'] == 70.0

    def test_shrink_to_zero_both(self):
        rect = {'corner': 'br', 'cut_w': 6.0, 'cut_h': 8.0,
                'offset_x': 0.0, 'offset_y': 0.0}
        s = _shrink_cut_rect(rect, 5.0)
        assert s['cut_w'] == 0.0
        assert s['cut_h'] == 0.0

    def test_shrink_preserves_corner(self):
        rect = {'corner': 'tl', 'cut_w': 100.0, 'cut_h': 80.0,
                'offset_x': 0.0, 'offset_y': 0.0}
        s = _shrink_cut_rect(rect, 10.0)
        assert s['corner'] == 'tl'

    def test_zero_shrink_is_identity(self):
        rect = {'corner': 'br', 'cut_w': 100.0, 'cut_h': 80.0,
                'offset_x': 15.0, 'offset_y': 25.0}
        s = _shrink_cut_rect(rect, 0.0)
        assert s == rect


# ── _build_design_lshape_mask helper ──────────────────────────────

class TestBuildDesignLshapeMask:
    """helper 封装 build_lshape_mask + design 参数提取"""

    def _make_design(self, cut_rects=None):
        d = CropDesign(
            canvas_w_cm=10.0, canvas_h_cm=8.0,
            inner_margin_top_cm=0.5,
            inner_margin_bottom_cm=0.5,
            inner_margin_left_cm=0.5,
            inner_margin_right_cm=0.5,
        )
        d.mode = 'rect_lshape'
        d.l_cut_rects = cut_rects or []
        return d

    def test_simple_lshape_outer(self):
        d = self._make_design()
        d.l_corner = 'br'
        d.l_cut_w_cm = 3.0
        d.l_cut_h_cm = 2.0
        W, H = d.canvas_w_px, d.canvas_h_px
        mask = _build_design_lshape_mask(d, use_outer=True)
        assert mask.shape == (H, W)
        assert mask.dtype == bool
        assert mask.any()

    def test_simple_lshape_inner(self):
        d = self._make_design()
        d.l_corner = 'br'
        d.l_cut_w_cm = 3.0
        d.l_cut_h_cm = 2.0
        W, H = d.canvas_w_px, d.canvas_h_px
        mask = _build_design_lshape_mask(d, use_outer=False)
        assert mask.shape == (H, W)
        assert mask.any()

    def test_staircase_outer(self):
        d = self._make_design(cut_rects=[
            CutRect(anchor='br', offset_x_cm=0, offset_y_cm=0, w_cm=3, h_cm=2),
            CutRect(anchor='br', offset_x_cm=3, offset_y_cm=2, w_cm=2, h_cm=2),
        ])
        d.l_corner = 'br'
        W, H = d.canvas_w_px, d.canvas_h_px
        mask = _build_design_lshape_mask(d, use_outer=True)
        assert mask.shape == (H, W)
        assert mask.any()

    def test_staircase_inner(self):
        d = self._make_design(cut_rects=[
            CutRect(anchor='br', offset_x_cm=0, offset_y_cm=0, w_cm=3, h_cm=2),
            CutRect(anchor='br', offset_x_cm=3, offset_y_cm=2, w_cm=2, h_cm=2),
        ])
        d.l_corner = 'br'
        W, H = d.canvas_w_px, d.canvas_h_px
        mask = _build_design_lshape_mask(d, use_outer=False)
        assert mask.shape == (H, W)
        assert mask.any()

    def test_shrink_parameter(self):
        d = self._make_design()
        d.l_corner = 'br'
        d.l_cut_w_cm = 3.0
        d.l_cut_h_cm = 2.0
        mask_no_shrink = _build_design_lshape_mask(d, use_outer=True, shrink_px=0)
        mask_shrunk = _build_design_lshape_mask(d, use_outer=True, shrink_px=10)
        assert mask_shrunk.sum() < mask_no_shrink.sum()

    def test_staircase_shrink(self):
        d = self._make_design(cut_rects=[
            CutRect(anchor='br', offset_x_cm=0, offset_y_cm=0, w_cm=3, h_cm=2),
            CutRect(anchor='br', offset_x_cm=3, offset_y_cm=2, w_cm=2, h_cm=2),
        ])
        d.l_corner = 'br'
        W, H = d.canvas_w_px, d.canvas_h_px
        mask = _build_design_lshape_mask(d, use_outer=True, shrink_px=5)
        assert mask.shape == (H, W)

    def test_backward_compat_no_cut_rects(self):
        """无 cut_rects 时等价于旧 cut_specs 路径"""
        d = self._make_design()
        d.l_corner = 'br'
        d.l_cut_w_cm = 3.0
        d.l_cut_h_cm = 2.0
        W, H = d.canvas_w_px, d.canvas_h_px
        mask_helper = _build_design_lshape_mask(d, use_outer=True)
        outer = d.outer_rect_px()
        lshape = d.l_shapes_px()
        mask_manual = np.array(build_lshape_mask(
            (W, H), outer, lshape.corner,
            lshape.cut_w, lshape.cut_h,
            d.corners_px, fill_value=255,
            cuts=lshape.cut_specs()), dtype=bool)
        np.testing.assert_array_equal(mask_helper, mask_manual)


# ── compute_lshape_border_bands 阶梯支持 ──────────────────────────

class TestComputeLshapeBorderBandsStaircase:
    """边框带计算在阶梯 CutRect 下的正确性"""

    def _make_design_with_borders(self, cut_rects=None):
        from core.geometry import BorderLayer
        d = CropDesign(
            canvas_w_cm=10.0, canvas_h_cm=8.0,
            inner_margin_top_cm=0.5,
            inner_margin_bottom_cm=0.5,
            inner_margin_left_cm=0.5,
            inner_margin_right_cm=0.5,
        )
        d.mode = 'rect_lshape'
        d.l_corner = 'br'
        d.l_cut_w_cm = 3.0
        d.l_cut_h_cm = 2.0
        d.l_cut_rects = cut_rects or []
        d.borders = [
            BorderLayer(fill_type='solid', color=(0, 0, 0), offset_cm=0.0),
            BorderLayer(fill_type='solid', color=(128, 128, 128), offset_cm=0.5),
        ]
        return d

    def test_simple_lshape_bands(self):
        d = self._make_design_with_borders()
        W, H = d.canvas_w_px, d.canvas_h_px
        bands = compute_lshape_border_bands(d)
        assert len(bands) >= 2
        for mask, layer in bands:
            assert mask.shape == (H, W)
            assert mask.dtype == bool

    def test_staircase_bands_no_crash(self):
        d = self._make_design_with_borders(cut_rects=[
            CutRect(anchor='br', offset_x_cm=0, offset_y_cm=0, w_cm=3, h_cm=2),
            CutRect(anchor='br', offset_x_cm=3, offset_y_cm=2, w_cm=2, h_cm=2),
        ])
        bands = compute_lshape_border_bands(d)
        assert len(bands) >= 2

    def test_staircase_bands_no_overlap(self):
        """各层 band 之间不应有重叠像素"""
        d = self._make_design_with_borders(cut_rects=[
            CutRect(anchor='br', offset_x_cm=0, offset_y_cm=0, w_cm=3, h_cm=2),
            CutRect(anchor='br', offset_x_cm=3, offset_y_cm=2, w_cm=2, h_cm=2),
        ])
        W, H = d.canvas_w_px, d.canvas_h_px
        bands = compute_lshape_border_bands(d)
        total = np.zeros((H, W), dtype=int)
        for mask, _ in bands:
            total += mask.astype(int)
        assert (total <= 1).all(), "band 间存在重叠像素"

    def test_staircase_bands_cover_frame(self):
        """所有 band 的并集应覆盖 frame 区域（outer L - inner L）"""
        d = self._make_design_with_borders(cut_rects=[
            CutRect(anchor='br', offset_x_cm=0, offset_y_cm=0, w_cm=3, h_cm=2),
            CutRect(anchor='br', offset_x_cm=3, offset_y_cm=2, w_cm=2, h_cm=2),
        ])
        W, H = d.canvas_w_px, d.canvas_h_px
        bands = compute_lshape_border_bands(d)
        lshape = d.l_shapes_px()
        outer_mask = np.array(build_lshape_mask(
            (W, H), d.outer_rect_px(), 'br',
            lshape.cut_w, lshape.cut_h,
            d.corners_px, fill_value=255,
            cuts=lshape.cut_rect_specs()), dtype=bool)
        inner = d.inner_rect_px()
        inner_corners = compute_inner_corner_radii(d.outer_rect_px(), inner, d.corners_px)
        inner_mask = np.array(build_lshape_mask(
            (W, H), inner, 'br',
            lshape.cut_w, lshape.cut_h,
            inner_corners, fill_value=255,
            cuts=lshape.cut_rect_specs()), dtype=bool)
        frame = outer_mask & ~inner_mask
        band_union = np.zeros((H, W), dtype=bool)
        for mask, _ in bands:
            band_union |= mask
        coverage = (band_union & frame).sum() / max(frame.sum(), 1)
        assert coverage > 0.95, f"band 覆盖率 {coverage:.2%} 不足"
