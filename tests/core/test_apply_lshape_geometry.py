"""Independent unit tests for apply_lshape_geometry function.

Tests the L-shape geometry application logic in isolation:
- Mode and margin settings
- cut_rects branch (new multi-rect format)
- cuts_cm branch (legacy single-cut format)
- Material image assignment
- Cut filtering and validation
"""
import pytest
from core.geometry import CropDesign
from workers.design_builders import apply_lshape_geometry, LShapeBuildParams


class TestLShapeModeAndMargins:
    """Test L-shape mode and margin configuration."""

    def test_mode_set_to_rect_lshape(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert design.mode == 'rect_lshape'

    def test_outer_margin_set_to_zero(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        design.outer_margin_cm = 5.0
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert design.outer_margin_cm == 0.0

    def test_all_inner_margins_set_to_zero(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        design.inner_margin_top_cm = 5.0
        design.inner_margin_bottom_cm = 3.0
        design.inner_margin_left_cm = 2.0
        design.inner_margin_right_cm = 4.0
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert design.inner_margin_top_cm == 0.0
        assert design.inner_margin_bottom_cm == 0.0
        assert design.inner_margin_left_cm == 0.0
        assert design.inner_margin_right_cm == 0.0

    def test_pool_hole_transparent_set_true(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert design.pool_hole_transparent is True


class TestLegacyCutsBranch:
    """Test the legacy cuts_cm branch (single-cut format)."""

    def test_single_cut_from_cuts_cm(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "br",
            "cut_w_cm": 15.0,
            "cut_h_cm": 18.0,
            "cuts_cm": [
                {"corner": "br", "cut_w_cm": 15.0, "cut_h_cm": 18.0}
            ],
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert design.l_corner == "br"
        assert design.l_cut_w_cm == 15.0
        assert design.l_cut_h_cm == 18.0
        assert len(design.l_cuts_cm) == 1
        assert design.l_cuts_cm[0]['corner'] == "br"
        assert design.l_cuts_cm[0]['cut_w_cm'] == 15.0
        assert design.l_cuts_cm[0]['cut_h_cm'] == 18.0

    def test_multiple_cuts_from_cuts_cm(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
            "cuts_cm": [
                {"corner": "tl", "cut_w_cm": 5.0, "cut_h_cm": 6.0},
                {"corner": "tr", "cut_w_cm": 7.0, "cut_h_cm": 8.0},
                {"corner": "bl", "cut_w_cm": 9.0, "cut_h_cm": 10.0},
                {"corner": "br", "cut_w_cm": 11.0, "cut_h_cm": 12.0},
            ],
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert len(design.l_cuts_cm) == 4
        assert design.l_cuts_cm[0]['corner'] == "tl"
        assert design.l_cuts_cm[3]['corner'] == "br"

    def test_cuts_cm_limited_to_four(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
            "cuts_cm": [
                {"corner": "tl", "cut_w_cm": 5.0, "cut_h_cm": 6.0},
                {"corner": "tr", "cut_w_cm": 7.0, "cut_h_cm": 8.0},
                {"corner": "bl", "cut_w_cm": 9.0, "cut_h_cm": 10.0},
                {"corner": "br", "cut_w_cm": 11.0, "cut_h_cm": 12.0},
                {"corner": "tl", "cut_w_cm": 13.0, "cut_h_cm": 14.0},
            ],
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert len(design.l_cuts_cm) == 4

    def test_invalid_corner_filtered_out(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
            "cuts_cm": [
                {"corner": "tr", "cut_w_cm": 10.0, "cut_h_cm": 12.0},
                {"corner": "invalid", "cut_w_cm": 5.0, "cut_h_cm": 6.0},
            ],
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert len(design.l_cuts_cm) == 1
        assert design.l_cuts_cm[0]['corner'] == "tr"

    def test_empty_cuts_cm_results_in_empty_list(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
            "cuts_cm": [],
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert len(design.l_cuts_cm) == 0


class TestCutRectsBranch:
    """Test the new cut_rects branch (multi-rect format)."""

    def test_single_cut_rect(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
            "cut_rects": [
                {
                    "anchor": "tr",
                    "offset_x_cm": 5.0,
                    "offset_y_cm": 3.0,
                    "w_cm": 10.0,
                    "h_cm": 12.0,
                }
            ],
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert len(design.l_cut_rects) == 1
        assert design.l_cut_rects[0].anchor == "tr"
        assert design.l_cut_rects[0].offset_x_cm == 5.0
        assert design.l_cut_rects[0].offset_y_cm == 3.0
        assert design.l_cut_rects[0].w_cm == 10.0
        assert design.l_cut_rects[0].h_cm == 12.0

    def test_multiple_cut_rects(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
            "cut_rects": [
                {"anchor": "tl", "offset_x_cm": 0.0, "offset_y_cm": 0.0, "w_cm": 5.0, "h_cm": 6.0},
                {"anchor": "br", "offset_x_cm": 2.0, "offset_y_cm": 2.0, "w_cm": 7.0, "h_cm": 8.0},
            ],
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert len(design.l_cut_rects) == 2
        assert design.l_cut_rects[0].anchor == "tl"
        assert design.l_cut_rects[1].anchor == "br"

    def test_invalid_anchor_filtered_out(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
            "cut_rects": [
                {"anchor": "tr", "offset_x_cm": 0.0, "offset_y_cm": 0.0, "w_cm": 10.0, "h_cm": 12.0},
                {"anchor": "invalid", "offset_x_cm": 0.0, "offset_y_cm": 0.0, "w_cm": 5.0, "h_cm": 6.0},
            ],
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert len(design.l_cut_rects) == 1
        assert design.l_cut_rects[0].anchor == "tr"

    def test_zero_width_rect_filtered_out(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
            "cut_rects": [
                {"anchor": "tr", "offset_x_cm": 0.0, "offset_y_cm": 0.0, "w_cm": 0.0, "h_cm": 12.0},
            ],
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert len(design.l_cut_rects) == 0

    def test_zero_height_rect_filtered_out(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
            "cut_rects": [
                {"anchor": "tr", "offset_x_cm": 0.0, "offset_y_cm": 0.0, "w_cm": 10.0, "h_cm": 0.0},
            ],
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert len(design.l_cut_rects) == 0

    def test_missing_anchor_filtered_out(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
            "cut_rects": [
                {"offset_x_cm": 0.0, "offset_y_cm": 0.0, "w_cm": 10.0, "h_cm": 12.0},
            ],
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert len(design.l_cut_rects) == 0

    def test_non_dict_rect_filtered_out(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
            "cut_rects": [
                {"anchor": "tr", "offset_x_cm": 0.0, "offset_y_cm": 0.0, "w_cm": 10.0, "h_cm": 12.0},
                "not a dict",
                123,
                None,
            ],
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert len(design.l_cut_rects) == 1


class TestMaterialImageAssignment:
    """Test material image path assignment."""

    def test_pool_outer_material_image_set(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
        })
        apply_lshape_geometry(design, params, "/path/to/outer.jpg")
        assert design.pool_outer_material_image == "/path/to/outer.jpg"

    def test_outer_bg_image_set(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
        })
        apply_lshape_geometry(design, params, "/path/to/bg.jpg")
        assert design.outer_bg_image == "/path/to/bg.jpg"

    def test_pool_inner_material_image_set(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
        })
        apply_lshape_geometry(design, params, "/path/to/inner.jpg")
        assert design.pool_inner_material_image == "/path/to/inner.jpg"


class TestLShapeGeometryEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_dict_params_accepted(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = {
            "corner": "bl",
            "cut_w_cm": 8.0,
            "cut_h_cm": 10.0,
        }
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert design.l_corner == "bl"
        assert design.l_cut_w_cm == 8.0
        assert design.l_cut_h_cm == 10.0

    def test_missing_cut_dimensions_default_to_zero(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "corner": "tr",
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert design.l_cut_w_cm == 0.0
        assert design.l_cut_h_cm == 0.0

    def test_missing_corner_defaults_to_tr(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        params = LShapeBuildParams({
            "cut_w_cm": 10.0,
            "cut_h_cm": 12.0,
        })
        apply_lshape_geometry(design, params, "/path/to/material.jpg")
        assert design.l_corner == "tr"

    def test_all_four_corners_valid(self):
        for corner in ["tl", "tr", "bl", "br"]:
            design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
            params = LShapeBuildParams({
                "corner": corner,
                "cut_w_cm": 10.0,
                "cut_h_cm": 12.0,
            })
            apply_lshape_geometry(design, params, "/path/to/material.jpg")
            assert design.l_corner == corner
