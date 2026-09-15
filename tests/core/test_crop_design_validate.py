"""
测试：core.geometry.CropDesign.validate() 参数校验

覆盖：
  - 正常默认值通过校验
  - 画布尺寸非法（零/负数）
  - DPI 非法（零/负数）
  - mode 非法
  - margin 为负数
  - 圆角半径为负数 / 超过画布一半
  - ellipse_hole 模式 ratio 越界
  - rect_lshape 模式参数非法
"""
import pytest
from core.geometry import CropDesign


class TestCropDesignValidateDefaults:
    """默认值应全部通过校验"""

    def test_default_passes(self):
        CropDesign().validate()

    def test_rect_hole_mode_passes(self):
        CropDesign(mode='rect_hole').validate()

    def test_rect_lshape_mode_passes(self):
        CropDesign(mode='rect_lshape').validate()

    def test_ellipse_hole_mode_passes(self):
        CropDesign(mode='ellipse_hole').validate()


class TestCropDesignValidateCanvas:
    """画布尺寸校验"""

    def test_zero_w_raises(self):
        with pytest.raises(ValueError, match='canvas_w_cm'):
            CropDesign(canvas_w_cm=0).validate()

    def test_negative_w_raises(self):
        with pytest.raises(ValueError, match='canvas_w_cm'):
            CropDesign(canvas_w_cm=-10).validate()

    def test_zero_h_raises(self):
        with pytest.raises(ValueError, match='canvas_h_cm'):
            CropDesign(canvas_h_cm=0).validate()

    def test_negative_h_raises(self):
        with pytest.raises(ValueError, match='canvas_h_cm'):
            CropDesign(canvas_h_cm=-5).validate()


class TestCropDesignValidateDpi:
    """DPI 校验"""

    def test_zero_dpi_raises(self):
        with pytest.raises(ValueError, match='dpi'):
            CropDesign(dpi=0).validate()

    def test_negative_dpi_raises(self):
        with pytest.raises(ValueError, match='dpi'):
            CropDesign(dpi=-150).validate()


class TestCropDesignValidateMode:
    """mode 校验"""

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match='mode'):
            CropDesign(mode='bogus').validate()


class TestCropDesignValidateMargins:
    """margin 不能为负"""

    def test_negative_outer_margin_raises(self):
        with pytest.raises(ValueError, match='outer_margin_cm'):
            CropDesign(outer_margin_cm=-1.0).validate()

    def test_negative_inner_top_raises(self):
        with pytest.raises(ValueError, match='inner_margin_top_cm'):
            CropDesign(inner_margin_top_cm=-0.5).validate()

    def test_negative_inner_bottom_raises(self):
        with pytest.raises(ValueError, match='inner_margin_bottom_cm'):
            CropDesign(inner_margin_bottom_cm=-0.5).validate()

    def test_negative_inner_left_raises(self):
        with pytest.raises(ValueError, match='inner_margin_left_cm'):
            CropDesign(inner_margin_left_cm=-0.5).validate()

    def test_negative_inner_right_raises(self):
        with pytest.raises(ValueError, match='inner_margin_right_cm'):
            CropDesign(inner_margin_right_cm=-0.5).validate()

    def test_zero_margins_pass(self):
        CropDesign(outer_margin_cm=0, inner_margin_top_cm=0,
                   inner_margin_bottom_cm=0, inner_margin_left_cm=0,
                   inner_margin_right_cm=0).validate()


class TestCropDesignValidateCorners:
    """圆角半径校验"""

    def test_negative_corner_raises(self):
        with pytest.raises(ValueError, match='corner_tl_cm'):
            CropDesign(corner_tl_cm=-1.0).validate()

    def test_corner_exceeds_half_raises(self):
        # min(50, 70)/2 = 25; 30 > 25
        with pytest.raises(ValueError, match='超过画布尺寸的一半'):
            CropDesign(corner_br_cm=30.0).validate()

    def test_corner_at_half_passes(self):
        CropDesign(corner_br_cm=25.0).validate()

    def test_zero_corner_passes(self):
        CropDesign(corner_tl_cm=0, corner_tr_cm=0,
                   corner_bl_cm=0, corner_br_cm=0).validate()


class TestCropDesignEllipseGeometry:
    """椭圆直径由四边距计算，并补偿 1cm 画布损耗。"""

    def test_ellipse_uses_inner_rect_diameter_plus_one_cm(self):
        design = CropDesign(
            mode='ellipse_hole', canvas_w_cm=78.0, canvas_h_cm=59.0, dpi=150,
            inner_margin_top_cm=16.0, inner_margin_bottom_cm=12.0,
            inner_margin_left_cm=18.0, inner_margin_right_cm=12.0,
        )

        ellipse = design.ellipse_px()
        px_per_cm = design.dpi / 2.54

        assert ellipse.rx * 2 == pytest.approx(48.0 * px_per_cm)
        assert ellipse.ry * 2 == pytest.approx(31.0 * px_per_cm)
        assert ellipse.cx == pytest.approx((18.0 + 48.0 / 2) * px_per_cm)
        assert ellipse.cy == pytest.approx((16.0 + 31.0 / 2) * px_per_cm)
        assert ellipse.cx - ellipse.rx == pytest.approx(18.0 * px_per_cm)
        assert ellipse.cx + ellipse.rx == pytest.approx((78.0 - 12.0) * px_per_cm)
        assert ellipse.cy - ellipse.ry == pytest.approx(16.0 * px_per_cm)
        assert ellipse.cy + ellipse.ry == pytest.approx((59.0 - 12.0) * px_per_cm)

    def test_manual_equal_diameters_make_circle(self):
        design = CropDesign(
            mode='ellipse_hole', canvas_w_cm=78.0, canvas_h_cm=59.0, dpi=150,
            inner_margin_top_cm=16.0, inner_margin_bottom_cm=12.0,
            inner_margin_left_cm=18.0, inner_margin_right_cm=12.0,
            ellipse_diameter_w_cm=40.0,
            ellipse_diameter_h_cm=40.0,
        )

        ellipse = design.ellipse_px()
        assert ellipse.rx == pytest.approx(ellipse.ry)
        assert ellipse.rx * 2 == pytest.approx(40.0 * design.dpi / 2.54)


class TestCropDesignValidateLShape:
    """rect_lshape 模式参数校验"""

    def test_invalid_l_corner_raises(self):
        with pytest.raises(ValueError, match='l_corner'):
            CropDesign(mode='rect_lshape', l_corner='xx').validate()

    def test_all_valid_corners_pass(self):
        for c in ('tl', 'tr', 'bl', 'br'):
            CropDesign(mode='rect_lshape', l_corner=c).validate()

    def test_zero_l_cut_w_raises(self):
        with pytest.raises(ValueError, match='l_cut_w_cm'):
            CropDesign(mode='rect_lshape', l_cut_w_cm=0).validate()

    def test_negative_l_cut_w_raises(self):
        with pytest.raises(ValueError, match='l_cut_w_cm'):
            CropDesign(mode='rect_lshape', l_cut_w_cm=-5.0).validate()

    def test_zero_l_cut_h_raises(self):
        with pytest.raises(ValueError, match='l_cut_h_cm'):
            CropDesign(mode='rect_lshape', l_cut_h_cm=0).validate()

    def test_negative_l_cut_h_raises(self):
        with pytest.raises(ValueError, match='l_cut_h_cm'):
            CropDesign(mode='rect_lshape', l_cut_h_cm=-3.0).validate()

    def test_diagonal_multi_cuts_pass_edge_constraints(self):
        CropDesign(
            mode='rect_lshape',
            canvas_w_cm=143.0,
            canvas_h_cm=62.8,
            inner_margin_top_cm=0.0,
            inner_margin_bottom_cm=0.0,
            inner_margin_left_cm=0.0,
            inner_margin_right_cm=0.0,
            l_cuts_cm=[
                {'corner': 'tr', 'cut_w_cm': 28.0, 'cut_h_cm': 8.0},
                {'corner': 'bl', 'cut_w_cm': 30.0, 'cut_h_cm': 12.8},
            ],
        ).validate()

    def test_adjacent_cuts_must_leave_edge_clearance(self):
        with pytest.raises(ValueError, match='上边'):
            CropDesign(
                mode='rect_lshape',
                l_cuts_cm=[
                    {'corner': 'tl', 'cut_w_cm': 20.0, 'cut_h_cm': 10.0},
                    {'corner': 'tr', 'cut_w_cm': 20.0, 'cut_h_cm': 10.0},
                ],
            ).validate()

    def test_vertical_adjacent_cuts_must_leave_edge_clearance(self):
        with pytest.raises(ValueError, match='左边'):
            CropDesign(
                mode='rect_lshape',
                l_cuts_cm=[
                    {'corner': 'tl', 'cut_w_cm': 10.0, 'cut_h_cm': 30.0},
                    {'corner': 'bl', 'cut_w_cm': 10.0, 'cut_h_cm': 30.0},
                ],
            ).validate()

    @pytest.mark.parametrize(
        ('edge_name', 'cuts'),
        [
            ('上边', [
                {'corner': 'tl', 'cut_w_cm': 24.0, 'cut_h_cm': 5.0},
                {'corner': 'tr', 'cut_w_cm': 24.0, 'cut_h_cm': 5.0},
            ]),
            ('下边', [
                {'corner': 'bl', 'cut_w_cm': 24.0, 'cut_h_cm': 5.0},
                {'corner': 'br', 'cut_w_cm': 24.0, 'cut_h_cm': 5.0},
            ]),
            ('左边', [
                {'corner': 'tl', 'cut_w_cm': 5.0, 'cut_h_cm': 27.0},
                {'corner': 'bl', 'cut_w_cm': 5.0, 'cut_h_cm': 27.0},
            ]),
            ('右边', [
                {'corner': 'tr', 'cut_w_cm': 5.0, 'cut_h_cm': 27.0},
                {'corner': 'br', 'cut_w_cm': 5.0, 'cut_h_cm': 27.0},
            ]),
        ],
    )
    def test_each_edge_rejects_boundary_sum(self, edge_name, cuts):
        with pytest.raises(ValueError, match=edge_name):
            CropDesign(mode='rect_lshape', l_cuts_cm=cuts).validate()

    @pytest.mark.parametrize(
        'cuts',
        [
            [{'corner': 'tl', 'cut_w_cm': 31.9, 'cut_h_cm': 10.0}],
            [{'corner': 'tr', 'cut_w_cm': 31.9, 'cut_h_cm': 10.0}],
            [{'corner': 'bl', 'cut_w_cm': 10.0, 'cut_h_cm': 51.9}],
            [{'corner': 'br', 'cut_w_cm': 10.0, 'cut_h_cm': 51.9}],
        ],
    )
    def test_single_cut_below_edge_limit_passes(self, cuts):
        CropDesign(mode='rect_lshape', l_cuts_cm=cuts).validate()

    def test_exact_clearance_boundary_is_rejected(self):
        with pytest.raises(ValueError, match='上边'):
            CropDesign(
                mode='rect_lshape',
                l_cuts_cm=[
                    {'corner': 'tl', 'cut_w_cm': 23.0, 'cut_h_cm': 8.0},
                    {'corner': 'tr', 'cut_w_cm': 25.0, 'cut_h_cm': 8.0},
                ],
            ).validate()

    def test_four_corner_combination_passes_all_edges(self):
        CropDesign(
            mode='rect_lshape',
            l_cuts_cm=[
                {'corner': 'tl', 'cut_w_cm': 12.0, 'cut_h_cm': 12.0},
                {'corner': 'tr', 'cut_w_cm': 12.0, 'cut_h_cm': 12.0},
                {'corner': 'bl', 'cut_w_cm': 12.0, 'cut_h_cm': 12.0},
                {'corner': 'br', 'cut_w_cm': 12.0, 'cut_h_cm': 12.0},
            ],
        ).validate()
