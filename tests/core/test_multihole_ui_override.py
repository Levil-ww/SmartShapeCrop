"""build_multihole_geometry UI override logic tests.

Tests the UI override section of build_multihole_geometry which:
1. Detects user_multihole dict with active_count >= 2
2. Overrides hole sizes (w/h) from UI values
3. Overrides gaps from UI values
4. Recalculates x/y positions based on new sizes
5. Preserves per-hole margins (mt/mb/ml/mr) from sketch
6. Handles horizontal and vertical layouts
"""
import pytest
from unittest.mock import Mock
from core.geometry import CropDesign
from workers.design_builders import build_multihole_geometry


class TestUIOverrideEntryConditions:
    """Test conditions for UI override to trigger."""

    def _make_sketch(self, n_holes=2):
        sketch = Mock()
        sketch.success = True
        sketch.is_multi_hole = True
        sketch.holes = []
        for i in range(n_holes):
            hole = Mock()
            hole.w_cm = 20.0
            hole.h_cm = 30.0
            hole.margin_top_cm = 2.0
            hole.margin_bottom_cm = 2.0
            hole.margin_left_cm = 2.0 if i == 0 else 0.0
            hole.margin_right_cm = 2.0 if i == n_holes - 1 else 0.0
            sketch.holes.append(hole)
        sketch.hole_gaps_cm = [5.0] * (n_holes - 1)
        sketch.layout_type = "horizontal"
        sketch.margin_top_cm = 2.0
        sketch.margin_bottom_cm = 2.0
        sketch.margin_left_cm = 2.0
        sketch.margin_right_cm = 2.0
        return sketch

    def test_ui_override_triggers_with_active_count_2(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2)
        user_multihole = {
            "active_count": 2,
            "holes_wh": [(25.0, 35.0), (25.0, 35.0)],
            "gaps_cm": [6.0]
        }
        build_multihole_geometry(design, sketch, 1.0, user_multihole)
        assert len(design.pool_holes_cm) == 2
        assert design.pool_holes_cm[0]['w_cm'] == 25.0
        assert design.pool_holes_cm[0]['h_cm'] == 35.0

    def test_ui_override_skipped_with_active_count_1(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2)
        user_multihole = {
            "active_count": 1,
            "holes_wh": [(25.0, 35.0)],
            "gaps_cm": []
        }
        build_multihole_geometry(design, sketch, 1.0, user_multihole)
        assert len(design.pool_holes_cm) == 2
        assert design.pool_holes_cm[0]['w_cm'] == 21.0

    def test_ui_override_skipped_with_none_user_multihole(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2)
        build_multihole_geometry(design, sketch, 1.0, None)
        assert len(design.pool_holes_cm) == 2
        assert design.pool_holes_cm[0]['w_cm'] == 21.0

    def test_ui_override_skipped_with_insufficient_holes_wh(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(3)
        user_multihole = {
            "active_count": 3,
            "holes_wh": [(25.0, 35.0)],
            "gaps_cm": [6.0, 6.0]
        }
        build_multihole_geometry(design, sketch, 1.0, user_multihole)
        assert design.pool_holes_cm[0]['w_cm'] == 21.0

    def test_ui_override_skipped_with_insufficient_gaps(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(3)
        user_multihole = {
            "active_count": 3,
            "holes_wh": [(25.0, 35.0), (25.0, 35.0), (25.0, 35.0)],
            "gaps_cm": [6.0]
        }
        build_multihole_geometry(design, sketch, 1.0, user_multihole)
        assert design.pool_holes_cm[0]['w_cm'] == 21.0


class TestUIOverrideHorizontalLayout:
    """Test UI override with horizontal layout."""

    def _make_sketch(self, n_holes=2):
        sketch = Mock()
        sketch.success = True
        sketch.is_multi_hole = True
        sketch.holes = []
        for i in range(n_holes):
            hole = Mock()
            hole.w_cm = 20.0
            hole.h_cm = 30.0
            hole.margin_top_cm = 2.0
            hole.margin_bottom_cm = 2.0
            hole.margin_left_cm = 2.0 if i == 0 else 0.0
            hole.margin_right_cm = 2.0 if i == n_holes - 1 else 0.0
            sketch.holes.append(hole)
        sketch.hole_gaps_cm = [5.0] * (n_holes - 1)
        sketch.layout_type = "horizontal"
        sketch.margin_top_cm = 2.0
        sketch.margin_bottom_cm = 2.0
        sketch.margin_left_cm = 2.0
        sketch.margin_right_cm = 2.0
        return sketch

    def test_ui_override_replaces_sizes_horizontal(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2)
        user_multihole = {
            "active_count": 2,
            "holes_wh": [(30.0, 40.0), (30.0, 40.0)],
            "gaps_cm": [6.0],
            "layout_type": "horizontal"
        }
        build_multihole_geometry(design, sketch, 1.0, user_multihole)
        assert design.pool_holes_cm[0]['w_cm'] == 30.0
        assert design.pool_holes_cm[0]['h_cm'] == 40.0
        assert design.pool_holes_cm[1]['w_cm'] == 30.0
        assert design.pool_holes_cm[1]['h_cm'] == 40.0

    def test_ui_override_recalculates_x_positions(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2)
        user_multihole = {
            "active_count": 2,
            "holes_wh": [(20.0, 30.0), (20.0, 30.0)],
            "gaps_cm": [10.0],
            "layout_type": "horizontal"
        }
        build_multihole_geometry(design, sketch, 1.0, user_multihole)
        x0 = design.pool_holes_cm[0]['x_cm']
        x1 = design.pool_holes_cm[1]['x_cm']
        assert x1 == x0 + 20.0 + 10.0

    def test_ui_override_replaces_gaps(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2)
        user_multihole = {
            "active_count": 2,
            "holes_wh": [(20.0, 30.0), (20.0, 30.0)],
            "gaps_cm": [15.0],
            "layout_type": "horizontal"
        }
        build_multihole_geometry(design, sketch, 1.0, user_multihole)
        assert design.pool_holes_gaps_cm == [15.0]


class TestUIOverrideVerticalLayout:
    """Test UI override with vertical layout."""

    def _make_sketch(self, n_holes=2):
        sketch = Mock()
        sketch.success = True
        sketch.is_multi_hole = True
        sketch.holes = []
        for i in range(n_holes):
            hole = Mock()
            hole.w_cm = 20.0
            hole.h_cm = 30.0
            hole.margin_top_cm = 2.0 if i == 0 else 0.0
            hole.margin_bottom_cm = 2.0 if i == n_holes - 1 else 0.0
            hole.margin_left_cm = 2.0
            hole.margin_right_cm = 2.0
            sketch.holes.append(hole)
        sketch.hole_gaps_cm = [5.0] * (n_holes - 1)
        sketch.layout_type = "vertical"
        sketch.margin_top_cm = 2.0
        sketch.margin_bottom_cm = 2.0
        sketch.margin_left_cm = 2.0
        sketch.margin_right_cm = 2.0
        return sketch

    def test_ui_override_recalculates_y_positions_vertical(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2)
        user_multihole = {
            "active_count": 2,
            "holes_wh": [(20.0, 30.0), (20.0, 30.0)],
            "gaps_cm": [10.0],
            "layout_type": "vertical"
        }
        build_multihole_geometry(design, sketch, 1.0, user_multihole)
        y0 = design.pool_holes_cm[0]['y_cm']
        y1 = design.pool_holes_cm[1]['y_cm']
        assert y1 == y0 + 30.0 + 10.0

    def test_ui_override_sets_layout_type(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2)
        user_multihole = {
            "active_count": 2,
            "holes_wh": [(20.0, 30.0), (20.0, 30.0)],
            "gaps_cm": [10.0],
            "layout_type": "vertical"
        }
        build_multihole_geometry(design, sketch, 1.0, user_multihole)
        assert design.pool_layout_type == "vertical"


class TestUIOverridePerHoleMargins:
    """Test that UI override preserves per-hole margins."""

    def _make_sketch(self, n_holes=2):
        sketch = Mock()
        sketch.success = True
        sketch.is_multi_hole = True
        sketch.holes = []
        for i in range(n_holes):
            hole = Mock()
            hole.w_cm = 20.0
            hole.h_cm = 30.0
            hole.margin_top_cm = 2.0 + i * 0.5
            hole.margin_bottom_cm = 2.0
            hole.margin_left_cm = 2.0 if i == 0 else 0.0
            hole.margin_right_cm = 2.0 if i == n_holes - 1 else 0.0
            sketch.holes.append(hole)
        sketch.hole_gaps_cm = [5.0] * (n_holes - 1)
        sketch.layout_type = "horizontal"
        sketch.margin_top_cm = 2.0
        sketch.margin_bottom_cm = 2.0
        sketch.margin_left_cm = 2.0
        sketch.margin_right_cm = 2.0
        return sketch

    def test_ui_override_preserves_per_hole_mt(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2)
        user_multihole = {
            "active_count": 2,
            "holes_wh": [(20.0, 30.0), (20.0, 30.0)],
            "gaps_cm": [6.0],
            "layout_type": "horizontal"
        }
        build_multihole_geometry(design, sketch, 1.0, user_multihole)
        assert design.pool_holes_cm[0]['mt_cm'] == 2.0
        assert design.pool_holes_cm[1]['mt_cm'] == 2.5

    def test_ui_override_preserves_per_hole_ml(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2)
        user_multihole = {
            "active_count": 2,
            "holes_wh": [(20.0, 30.0), (20.0, 30.0)],
            "gaps_cm": [6.0],
            "layout_type": "horizontal"
        }
        build_multihole_geometry(design, sketch, 1.0, user_multihole)
        assert design.pool_holes_cm[0]['ml_cm'] == 2.0
        assert design.pool_holes_cm[1]['ml_cm'] == 2.0


class TestUIOverrideEdgeCases:
    """Test UI override edge cases."""

    def _make_sketch(self, n_holes=2):
        sketch = Mock()
        sketch.success = True
        sketch.is_multi_hole = True
        sketch.holes = []
        for i in range(n_holes):
            hole = Mock()
            hole.w_cm = 20.0
            hole.h_cm = 30.0
            hole.margin_top_cm = 2.0
            hole.margin_bottom_cm = 2.0
            hole.margin_left_cm = 2.0 if i == 0 else 0.0
            hole.margin_right_cm = 2.0 if i == n_holes - 1 else 0.0
            sketch.holes.append(hole)
        sketch.hole_gaps_cm = [5.0] * (n_holes - 1)
        sketch.layout_type = "horizontal"
        sketch.margin_top_cm = 2.0
        sketch.margin_bottom_cm = 2.0
        sketch.margin_left_cm = 2.0
        sketch.margin_right_cm = 2.0
        return sketch

    def test_ui_override_with_zero_size(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2)
        user_multihole = {
            "active_count": 2,
            "holes_wh": [(0.0, 0.0), (20.0, 30.0)],
            "gaps_cm": [6.0],
            "layout_type": "horizontal"
        }
        build_multihole_geometry(design, sketch, 1.0, user_multihole)
        assert design.pool_holes_cm[0]['w_cm'] == 0.0
        assert design.pool_holes_cm[0]['h_cm'] == 0.0

    def test_ui_override_with_negative_size_clamped_to_zero(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2)
        user_multihole = {
            "active_count": 2,
            "holes_wh": [(-5.0, -10.0), (20.0, 30.0)],
            "gaps_cm": [6.0],
            "layout_type": "horizontal"
        }
        build_multihole_geometry(design, sketch, 1.0, user_multihole)
        assert design.pool_holes_cm[0]['w_cm'] == 0.0
        assert design.pool_holes_cm[0]['h_cm'] == 0.0

    def test_ui_override_with_negative_gap_clamped_to_zero(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2)
        user_multihole = {
            "active_count": 2,
            "holes_wh": [(20.0, 30.0), (20.0, 30.0)],
            "gaps_cm": [-5.0],
            "layout_type": "horizontal"
        }
        build_multihole_geometry(design, sketch, 1.0, user_multihole)
        assert design.pool_holes_gaps_cm == [0.0]

    def test_ui_override_truncates_extra_holes_wh(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2)
        user_multihole = {
            "active_count": 2,
            "holes_wh": [(20.0, 30.0), (20.0, 30.0), (20.0, 30.0)],
            "gaps_cm": [6.0, 6.0],
            "layout_type": "horizontal"
        }
        build_multihole_geometry(design, sketch, 1.0, user_multihole)
        assert len(design.pool_holes_cm) == 2

    def test_ui_override_truncates_extra_gaps(self):
        design = CropDesign(canvas_w_cm=100, canvas_h_cm=80, dpi=150)
        sketch = self._make_sketch(2)
        user_multihole = {
            "active_count": 2,
            "holes_wh": [(20.0, 30.0), (20.0, 30.0)],
            "gaps_cm": [6.0, 6.0, 6.0],
            "layout_type": "horizontal"
        }
        build_multihole_geometry(design, sketch, 1.0, user_multihole)
        assert len(design.pool_holes_gaps_cm) == 1
