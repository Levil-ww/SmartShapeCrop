"""apply_composite_geometry independent unit tests.

Tests the apply_composite_geometry function which:
1. Creates CropDesign with canvas dimensions (outer + trim)
2. Expands composite params with canvas dimensions
3. Validates at least 1 cut is present
4. Applies params through DesignModel
5. Validates the resulting design
6. Sets material images
"""
import pytest
from unittest.mock import Mock
from core.geometry import CropDesign
from workers.design_builders import (
    apply_composite_geometry,
    DesignBuildRequest,
    CompositeBuildParams,
    DesignBuildValidationError,
)


class TestCanvasDimensionCalculation:
    """Test canvas dimension calculation from outer dimensions + trim."""

    def _make_request(self, outer_w=100.0, outer_h=80.0, trim=1.0, cuts=None):
        if cuts is None:
            cuts = [{"corner": "br", "cut_w_cm": 5.0, "cut_h_cm": 6.0}]
        return DesignBuildRequest(
            mode="composite",
            best=Mock(path="/test/material.jpg"),
            sketch_result=None,
            canvas_w_cm=outer_w,
            canvas_h_cm=outer_h,
            trim_cm=trim,
            composite_params=CompositeBuildParams({
                "outer_w_cm": outer_w,
                "outer_h_cm": outer_h,
                "cuts_cm": cuts
            })
        )

    def test_canvas_width_includes_trim(self):
        request = self._make_request(outer_w=100.0, trim=1.5)
        design = apply_composite_geometry(request)
        assert design.canvas_w_cm == 101.5

    def test_canvas_height_includes_trim(self):
        request = self._make_request(outer_h=80.0, trim=2.0)
        design = apply_composite_geometry(request)
        assert design.canvas_h_cm == 82.0

    def test_canvas_dimensions_with_zero_trim(self):
        request = self._make_request(outer_w=100.0, outer_h=80.0, trim=0.0)
        design = apply_composite_geometry(request)
        assert design.canvas_w_cm == 100.0
        assert design.canvas_h_cm == 80.0


class TestCutValidation:
    """Test cut validation requirements."""

    def _make_request(self, cuts):
        return DesignBuildRequest(
            mode="composite",
            best=Mock(path="/test/material.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 100.0,
                "outer_h_cm": 80.0,
                "cuts_cm": cuts
            })
        )

    def test_empty_cuts_raises(self):
        request = self._make_request([])
        with pytest.raises(DesignBuildValidationError, match="至少需要 1 处挖角"):
            apply_composite_geometry(request)

    def test_none_cuts_raises(self):
        request = self._make_request(None)
        with pytest.raises(DesignBuildValidationError, match="至少需要 1 处挖角"):
            apply_composite_geometry(request)

    def test_single_cut_passes(self):
        request = self._make_request([{"corner": "br", "cut_w_cm": 5.0, "cut_h_cm": 6.0}])
        design = apply_composite_geometry(request)
        assert design.mode == "rect_lshape_hole"

    def test_multiple_cuts_pass(self):
        cuts = [
            {"corner": "br", "cut_w_cm": 5.0, "cut_h_cm": 6.0},
            {"corner": "tl", "cut_w_cm": 7.0, "cut_h_cm": 8.0}
        ]
        request = self._make_request(cuts)
        design = apply_composite_geometry(request)
        assert len(design.l_cuts_cm) == 2


class TestDesignModeAndMargins:
    """Test design mode and margin settings."""

    def _make_request(self, cuts=None):
        if cuts is None:
            cuts = [{"corner": "br", "cut_w_cm": 5.0, "cut_h_cm": 6.0}]
        return DesignBuildRequest(
            mode="composite",
            best=Mock(path="/test/material.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 100.0,
                "outer_h_cm": 80.0,
                "cuts_cm": cuts
            })
        )

    def test_mode_set_to_rect_lshape_hole(self):
        request = self._make_request()
        design = apply_composite_geometry(request)
        assert design.mode == "rect_lshape_hole"

    def test_outer_margin_set_to_zero(self):
        request = self._make_request()
        design = apply_composite_geometry(request)
        assert design.outer_margin_cm == 0.0

    def test_hole_margins_from_params(self):
        request = DesignBuildRequest(
            mode="composite",
            best=Mock(path="/test/material.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 100.0,
                "outer_h_cm": 80.0,
                "cuts_cm": [{"corner": "br", "cut_w_cm": 5.0, "cut_h_cm": 6.0}],
                "hole_margin_top_cm": 3.0,
                "hole_margin_bottom_cm": 4.0,
                "hole_margin_left_cm": 5.0,
                "hole_margin_right_cm": 6.0
            })
        )
        design = apply_composite_geometry(request)
        assert design.inner_margin_top_cm == 3.0
        assert design.inner_margin_bottom_cm == 4.0
        assert design.inner_margin_left_cm == 5.0
        assert design.inner_margin_right_cm == 6.0


class TestMaterialImageAssignment:
    """Test material image path assignment."""

    def _make_request(self, path="/test/material.jpg"):
        return DesignBuildRequest(
            mode="composite",
            best=Mock(path=path),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 100.0,
                "outer_h_cm": 80.0,
                "cuts_cm": [{"corner": "br", "cut_w_cm": 5.0, "cut_h_cm": 6.0}]
            })
        )

    def test_pool_outer_material_image_set(self):
        request = self._make_request("/custom/path.png")
        design = apply_composite_geometry(request)
        assert design.pool_outer_material_image == "/custom/path.png"

    def test_outer_bg_image_set(self):
        request = self._make_request("/custom/path.png")
        design = apply_composite_geometry(request)
        assert design.outer_bg_image == "/custom/path.png"


class TestDesignValidation:
    """Test that resulting design passes validation."""

    def _make_request(self, cuts=None):
        if cuts is None:
            cuts = [{"corner": "br", "cut_w_cm": 5.0, "cut_h_cm": 6.0}]
        return DesignBuildRequest(
            mode="composite",
            best=Mock(path="/test/material.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 100.0,
                "outer_h_cm": 80.0,
                "cuts_cm": cuts
            })
        )

    def test_valid_design_passes_validate(self):
        request = self._make_request()
        design = apply_composite_geometry(request)
        design.validate()

    def test_multiple_cuts_design_passes_validate(self):
        cuts = [
            {"corner": "br", "cut_w_cm": 5.0, "cut_h_cm": 6.0},
            {"corner": "tl", "cut_w_cm": 7.0, "cut_h_cm": 8.0}
        ]
        request = self._make_request(cuts)
        design = apply_composite_geometry(request)
        design.validate()


class TestCutGeometryApplication:
    """Test that cut geometry is correctly applied."""

    def _make_request(self, cuts):
        return DesignBuildRequest(
            mode="composite",
            best=Mock(path="/test/material.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 100.0,
                "outer_h_cm": 80.0,
                "cuts_cm": cuts
            })
        )

    def test_first_cut_synced_to_legacy_fields(self):
        cuts = [
            {"corner": "br", "cut_w_cm": 5.0, "cut_h_cm": 6.0},
            {"corner": "tl", "cut_w_cm": 7.0, "cut_h_cm": 8.0}
        ]
        request = self._make_request(cuts)
        design = apply_composite_geometry(request)
        assert design.l_corner == "br"
        assert design.l_cut_w_cm == 5.0
        assert design.l_cut_h_cm == 6.0

    def test_all_cuts_preserved_in_l_cuts_cm(self):
        cuts = [
            {"corner": "br", "cut_w_cm": 5.0, "cut_h_cm": 6.0},
            {"corner": "tl", "cut_w_cm": 7.0, "cut_h_cm": 8.0},
            {"corner": "tr", "cut_w_cm": 9.0, "cut_h_cm": 10.0}
        ]
        request = self._make_request(cuts)
        design = apply_composite_geometry(request)
        assert len(design.l_cuts_cm) == 3
        assert design.l_cuts_cm[0]['corner'] == "br"
        assert design.l_cuts_cm[1]['corner'] == "tl"
        assert design.l_cuts_cm[2]['corner'] == "tr"


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_large_outer_dimensions(self):
        request = DesignBuildRequest(
            mode="composite",
            best=Mock(path="/test/material.jpg"),
            sketch_result=None,
            canvas_w_cm=500.0,
            canvas_h_cm=400.0,
            trim_cm=2.0,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 500.0,
                "outer_h_cm": 400.0,
                "cuts_cm": [{"corner": "br", "cut_w_cm": 50.0, "cut_h_cm": 60.0}]
            })
        )
        design = apply_composite_geometry(request)
        assert design.canvas_w_cm == 502.0
        assert design.canvas_h_cm == 402.0

    def test_small_cut_dimensions(self):
        request = DesignBuildRequest(
            mode="composite",
            best=Mock(path="/test/material.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 100.0,
                "outer_h_cm": 80.0,
                "cuts_cm": [{"corner": "br", "cut_w_cm": 0.5, "cut_h_cm": 0.6}]
            })
        )
        design = apply_composite_geometry(request)
        assert design.l_cut_w_cm == 0.5
        assert design.l_cut_h_cm == 0.6

    def test_four_cuts_maximum(self):
        cuts = [
            {"corner": "br", "cut_w_cm": 5.0, "cut_h_cm": 6.0},
            {"corner": "bl", "cut_w_cm": 5.0, "cut_h_cm": 6.0},
            {"corner": "tr", "cut_w_cm": 5.0, "cut_h_cm": 6.0},
            {"corner": "tl", "cut_w_cm": 5.0, "cut_h_cm": 6.0}
        ]
        request = DesignBuildRequest(
            mode="composite",
            best=Mock(path="/test/material.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 100.0,
                "outer_h_cm": 80.0,
                "cuts_cm": cuts
            })
        )
        design = apply_composite_geometry(request)
        assert len(design.l_cuts_cm) == 4
