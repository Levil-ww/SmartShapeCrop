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


class TestCropDesignValidateEllipse:
    """ellipse_hole 模式 ratio 校验"""

    def test_zero_rx_raises(self):
        with pytest.raises(ValueError, match='ellipse_rx_ratio'):
            CropDesign(mode='ellipse_hole', ellipse_rx_ratio=0).validate()

    def test_negative_rx_raises(self):
        with pytest.raises(ValueError, match='ellipse_rx_ratio'):
            CropDesign(mode='ellipse_hole', ellipse_rx_ratio=-0.1).validate()

    def test_rx_over_1_raises(self):
        with pytest.raises(ValueError, match='ellipse_rx_ratio'):
            CropDesign(mode='ellipse_hole', ellipse_rx_ratio=1.5).validate()

    def test_ry_boundary_1_passes(self):
        CropDesign(mode='ellipse_hole', ellipse_ry_ratio=1.0).validate()

    def test_ry_zero_raises(self):
        with pytest.raises(ValueError, match='ellipse_ry_ratio'):
            CropDesign(mode='ellipse_hole', ellipse_ry_ratio=0).validate()


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
