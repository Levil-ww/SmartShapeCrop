"""apply_pool_geometry independent unit tests.

Tests the apply_pool_geometry function which:
1. Sets design.mode based on target (ellipse_hole vs rect_hole)
2. Extracts margins from sketch_result or applies user_margins override
3. Falls back to 10% of min(canvas_w, canvas_h) when no sketch
4. Calls build_multihole_geometry for hole positioning
5. Sets pool_hole_transparent and material images
"""
import pytest
from unittest.mock import Mock, patch
from core.geometry import CropDesign
from workers.design_builders import apply_pool_geometry


class TestModeSelection:
    """Test design.mode selection based on target string."""

    def test_chinese_ellipse_target_sets_ellipse_mode(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        apply_pool_geometry(
            design, "椭圆水池", None, 100, 80, None, 1.0, "/test.jpg")
        assert design.mode == "ellipse_hole"

    def test_english_ellipse_target_sets_ellipse_mode(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        apply_pool_geometry(
            design, "ellipse pool", None, 100, 80, None, 1.0, "/test.jpg")
        assert design.mode == "ellipse_hole"

    def test_mixed_case_ellipse_target_sets_ellipse_mode(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        apply_pool_geometry(
            design, "Ellipse Pool", None, 100, 80, None, 1.0, "/test.jpg")
        assert design.mode == "ellipse_hole"

    def test_rect_target_sets_rect_mode(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        apply_pool_geometry(
            design, "圆角水池", None, 100, 80, None, 1.0, "/test.jpg")
        assert design.mode == "rect_hole"

    def test_empty_target_sets_rect_mode(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        apply_pool_geometry(
            design, "", None, 100, 80, None, 1.0, "/test.jpg")
        assert design.mode == "rect_hole"

    def test_none_target_sets_rect_mode(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        apply_pool_geometry(
            design, None, None, 100, 80, None, 1.0, "/test.jpg")
        assert design.mode == "rect_hole"


class TestSketchMarginExtraction:
    """Test margin extraction from sketch_result."""

    def _make_sketch(self, top=2.0, bottom=3.0, left=4.0, right=5.0):
        sketch = Mock()
        sketch.success = True
        sketch.margin_top_cm = top
        sketch.margin_bottom_cm = bottom
        sketch.margin_left_cm = left
        sketch.margin_right_cm = right
        return sketch

    def test_sketch_margins_applied_when_success(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2.5, 3.5, 4.5, 5.5)
        apply_pool_geometry(
            design, "圆角水池", sketch, 100, 80, None, 1.0, "/test.jpg")
        assert design.inner_margin_top_cm == 2.5
        assert design.inner_margin_bottom_cm == 3.5
        assert design.inner_margin_left_cm == 4.5
        assert design.inner_margin_right_cm == 5.5

    def test_sketch_failure_triggers_fallback(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = Mock()
        sketch.success = False
        apply_pool_geometry(
            design, "圆角水池", sketch, 100, 80, None, 1.0, "/test.jpg")
        expected = min(100, 80) * 0.10
        assert design.inner_margin_top_cm == expected
        assert design.inner_margin_bottom_cm == expected
        assert design.inner_margin_left_cm == expected
        assert design.inner_margin_right_cm == expected

    def test_none_sketch_triggers_fallback(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        apply_pool_geometry(
            design, "圆角水池", None, 100, 80, None, 1.0, "/test.jpg")
        expected = min(100, 80) * 0.10
        assert design.inner_margin_top_cm == expected


class TestUserMarginOverride:
    """Test user_margins override of sketch margins."""

    def _make_sketch(self, top=2.0, bottom=3.0, left=4.0, right=5.0):
        sketch = Mock()
        sketch.success = True
        sketch.margin_top_cm = top
        sketch.margin_bottom_cm = bottom
        sketch.margin_left_cm = left
        sketch.margin_right_cm = right
        return sketch

    def test_user_margins_override_sketch_when_present(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2.0, 3.0, 4.0, 5.0)
        user_margins = {"top": 10.0, "bottom": 11.0, "left": 12.0, "right": 13.0}
        apply_pool_geometry(
            design, "圆角水池", sketch, 100, 80, user_margins, 1.0, "/test.jpg")
        assert design.inner_margin_top_cm == 10.0
        assert design.inner_margin_bottom_cm == 11.0
        assert design.inner_margin_left_cm == 12.0
        assert design.inner_margin_right_cm == 13.0

    def test_partial_user_margins_merge_with_sketch(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2.0, 3.0, 4.0, 5.0)
        user_margins = {"top": 10.0, "bottom": None}
        apply_pool_geometry(
            design, "圆角水池", sketch, 100, 80, user_margins, 1.0, "/test.jpg")
        assert design.inner_margin_top_cm == 10.0
        assert design.inner_margin_bottom_cm == 3.0
        assert design.inner_margin_left_cm == 4.0
        assert design.inner_margin_right_cm == 5.0

    def test_empty_user_margins_uses_sketch(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2.0, 3.0, 4.0, 5.0)
        user_margins = {}
        apply_pool_geometry(
            design, "圆角水池", sketch, 100, 80, user_margins, 1.0, "/test.jpg")
        assert design.inner_margin_top_cm == 2.0
        assert design.inner_margin_bottom_cm == 3.0

    def test_lshape_mode_skips_user_margin_override(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2.0, 3.0, 4.0, 5.0)
        user_margins = {"top": 10.0, "bottom": 11.0, "left": 12.0, "right": 13.0}
        apply_pool_geometry(
            design, "圆角水池", sketch, 100, 80, user_margins, 1.0, "/test.jpg",
            is_lshape=True)
        assert design.inner_margin_top_cm == 2.0
        assert design.inner_margin_bottom_cm == 3.0
        assert design.inner_margin_left_cm == 4.0
        assert design.inner_margin_right_cm == 5.0


class TestFallbackMarginCalculation:
    """Test fallback margin calculation when no sketch."""

    def test_fallback_uses_10_percent_of_min_dimension(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        apply_pool_geometry(
            design, "圆角水池", None, 100, 80, None, 1.0, "/test.jpg")
        expected = min(100, 80) * 0.10
        assert design.inner_margin_top_cm == expected

    def test_fallback_with_square_canvas(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=100, dpi=150)
        apply_pool_geometry(
            design, "圆角水池", None, 100, 100, None, 1.0, "/test.jpg")
        expected = 100 * 0.10
        assert design.inner_margin_top_cm == expected

    def test_fallback_with_small_canvas(self):
        design = CropDesign(canvas_w_cm=50, canvas_h_cm=30, dpi=150)
        apply_pool_geometry(
            design, "圆角水池", None, 50, 30, None, 1.0, "/test.jpg")
        expected = min(50, 30) * 0.10
        assert design.inner_margin_top_cm == expected

    def test_fallback_with_large_canvas(self):
        design = CropDesign(canvas_w_cm=200, canvas_h_cm=150, dpi=150)
        apply_pool_geometry(
            design, "圆角水池", None, 200, 150, None, 1.0, "/test.jpg")
        expected = min(200, 150) * 0.10
        assert design.inner_margin_top_cm == expected


class TestMaterialImageAssignment:
    """Test material image path assignment."""

    def test_pool_outer_material_image_set(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        apply_pool_geometry(
            design, "圆角水池", None, 100, 80, None, 1.0, "/test/material.jpg")
        assert design.pool_outer_material_image == "/test/material.jpg"

    def test_outer_bg_image_set(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        apply_pool_geometry(
            design, "圆角水池", None, 100, 80, None, 1.0, "/test/material.jpg")
        assert design.outer_bg_image == "/test/material.jpg"

    def test_pool_hole_transparent_set(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        apply_pool_geometry(
            design, "圆角水池", None, 100, 80, None, 1.0, "/test.jpg")
        assert design.pool_hole_transparent is True


class TestMultiHoleIntegration:
    """Test integration with build_multihole_geometry."""

    def _make_sketch(self):
        sketch = Mock()
        sketch.success = True
        sketch.margin_top_cm = 2.0
        sketch.margin_bottom_cm = 2.0
        sketch.margin_left_cm = 2.0
        sketch.margin_right_cm = 2.0
        sketch.layout_type = "horizontal"
        sketch.pool_holes_cm = [
            {"x_cm": 5, "y_cm": 5, "w_cm": 20, "h_cm": 30,
             "mt_cm": 2, "mb_cm": 2, "ml_cm": 2, "mr_cm": 2}
        ]
        sketch.pool_holes_gaps_cm = []
        sketch.pool_is_multi_hole = False
        return sketch

    @patch("workers.design_builders.build_multihole_geometry")
    def test_build_multihole_called(self, mock_build):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch()
        apply_pool_geometry(
            design, "圆角水池", sketch, 100, 80, None, 1.0, "/test.jpg")
        mock_build.assert_called_once()

    @patch("workers.design_builders.build_multihole_geometry")
    def test_build_multihole_receives_correct_args(self, mock_build):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch()
        user_multihole = {"active_count": 2}
        log_fn = Mock()
        apply_pool_geometry(
            design, "圆角水池", sketch, 100, 80, None, 1.5, "/test.jpg",
            user_multihole=user_multihole, log=log_fn)
        call_args = mock_build.call_args
        assert call_args[0][0] is design
        assert call_args[0][1] is sketch
        assert call_args[0][2] == 1.5
        assert call_args[0][3] == user_multihole
        assert call_args[0][4] is log_fn


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_trim_does_not_crash(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        apply_pool_geometry(
            design, "圆角水池", None, 100, 80, None, 0.0, "/test.jpg")
        assert design.mode == "rect_hole"

    def test_negative_trim_does_not_crash(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        apply_pool_geometry(
            design, "圆角水池", None, 100, 80, None, -1.0, "/test.jpg")
        assert design.mode == "rect_hole"

    def test_very_large_canvas(self):
        design = CropDesign(canvas_w_cm=1000, canvas_h_cm=800, dpi=150)
        apply_pool_geometry(
            design, "圆角水池", None, 1000, 800, None, 1.0, "/test.jpg")
        expected = min(1000, 800) * 0.10
        assert design.inner_margin_top_cm == expected

    def test_very_small_canvas(self):
        design = CropDesign(canvas_w_cm=10, canvas_h_cm=8, dpi=150)
        apply_pool_geometry(
            design, "圆角水池", None, 10, 8, None, 1.0, "/test.jpg")
        expected = min(10, 8) * 0.10
        assert design.inner_margin_top_cm == expected
