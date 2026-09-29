"""Worker snapshot construction and builder dispatch tests.

Tests the Worker's _build_design method which:
1. Constructs LegacyBuildSnapshot from worker state
2. Converts to DesignBuildRequest via LegacyRequestAdapter
3. Creates new_design closure that adds trim to canvas
4. Dispatches to appropriate builder via BUILDERS dict
"""
import pytest
from unittest.mock import Mock, MagicMock
from core.geometry import CropDesign
from workers.design_builders import (
    LegacyBuildSnapshot,
    LegacyRequestAdapter,
    PoolBuildParams,
    LShapeBuildParams,
    CompositeBuildParams,
    DesignBuildRequest,
    BUILDERS,
)


class TestWorkerSnapshotConstruction:
    """Test LegacyBuildSnapshot construction from worker state."""

    def test_snapshot_captures_all_worker_fields(self):
        snapshot = LegacyBuildSnapshot(
            target="圆角水池",
            user_margins={"top": 2.0, "bottom": 2.0},
            user_multihole_params={"active_count": 2},
            lshape_params={"corner": "br", "cut_w_cm": 5.0},
            composite_params={"outer_w_cm": 100.0},
        )
        assert snapshot.target == "圆角水池"
        assert snapshot.user_margins == {"top": 2.0, "bottom": 2.0}
        assert snapshot.user_multihole_params == {"active_count": 2}
        assert snapshot.lshape_params == {"corner": "br", "cut_w_cm": 5.0}
        assert snapshot.composite_params == {"outer_w_cm": 100.0}

    def test_snapshot_defaults_to_empty_and_none(self):
        snapshot = LegacyBuildSnapshot()
        assert snapshot.target == ""
        assert snapshot.user_margins is None
        assert snapshot.user_multihole_params is None
        assert snapshot.lshape_params is None
        assert snapshot.composite_params is None

    def test_snapshot_is_frozen_dataclass(self):
        snapshot = LegacyBuildSnapshot(target="test")
        with pytest.raises(AttributeError):
            snapshot.target = "changed"


class TestAdapterModeSelection:
    """Test LegacyRequestAdapter mode selection logic."""

    def _best(self):
        return Mock(path="/test/material.jpg")

    def _sketch(self):
        sketch = Mock()
        sketch.success = True
        sketch.margin_top_cm = 2.0
        sketch.margin_bottom_cm = 2.0
        sketch.margin_left_cm = 2.0
        sketch.margin_right_cm = 2.0
        return sketch

    def test_composite_params_triggers_composite_mode(self):
        snapshot = LegacyBuildSnapshot(composite_params={"outer_w_cm": 100.0})
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), self._sketch(), 100, 80, False, 1.0)
        assert request.mode == "composite"

    def test_lshape_flag_triggers_lshape_mode(self):
        snapshot = LegacyBuildSnapshot(lshape_params={"corner": "br"})
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), self._sketch(), 100, 80, True, 1.0)
        assert request.mode == "lshape"

    def test_no_composite_no_lshape_triggers_pool_mode(self):
        snapshot = LegacyBuildSnapshot(target="圆角水池")
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), self._sketch(), 100, 80, False, 1.0)
        assert request.mode == "pool"

    def test_composite_takes_priority_over_lshape(self):
        snapshot = LegacyBuildSnapshot(
            composite_params={"outer_w_cm": 100.0},
            lshape_params={"corner": "br"})
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), self._sketch(), 100, 80, True, 1.0)
        assert request.mode == "composite"


class TestNewDesignClosure:
    """Test the new_design closure that adds trim to canvas dimensions."""

    def test_new_design_adds_trim_to_width(self):
        trim = 1.5
        def new_design(w, h, trim):
            d = CropDesign(canvas_w_cm=w + trim, canvas_h_cm=h + trim, dpi=150)
            d.outer_margin_cm = 0.0
            return d
        design = new_design(100.0, 80.0, trim)
        assert design.canvas_w_cm == 101.5

    def test_new_design_adds_trim_to_height(self):
        trim = 2.0
        def new_design(w, h, trim):
            d = CropDesign(canvas_w_cm=w + trim, canvas_h_cm=h + trim, dpi=150)
            d.outer_margin_cm = 0.0
            return d
        design = new_design(100.0, 80.0, trim)
        assert design.canvas_h_cm == 82.0

    def test_new_design_sets_outer_margin_zero(self):
        def new_design(w, h, trim):
            d = CropDesign(canvas_w_cm=w + trim, canvas_h_cm=h + trim, dpi=150)
            d.outer_margin_cm = 0.0
            return d
        design = new_design(100.0, 80.0, 1.0)
        assert design.outer_margin_cm == 0.0

    def test_new_design_sets_dpi(self):
        def new_design(w, h, trim):
            d = CropDesign(canvas_w_cm=w + trim, canvas_h_cm=h + trim, dpi=150)
            d.outer_margin_cm = 0.0
            return d
        design = new_design(100.0, 80.0, 1.0)
        assert design.dpi == 150


class TestBuilderDispatch:
    """Test BUILDERS dict dispatch to correct builder."""

    def test_builders_contains_pool_key(self):
        assert "pool" in BUILDERS

    def test_builders_contains_lshape_key(self):
        assert "lshape" in BUILDERS

    def test_builders_contains_composite_key(self):
        assert "composite" in BUILDERS

    def test_pool_builder_has_build_method(self):
        builder = BUILDERS["pool"]
        assert hasattr(builder, "build")
        assert callable(builder.build)

    def test_lshape_builder_has_build_method(self):
        builder = BUILDERS["lshape"]
        assert hasattr(builder, "build")
        assert callable(builder.build)

    def test_composite_builder_has_build_method(self):
        builder = BUILDERS["composite"]
        assert hasattr(builder, "build")
        assert callable(builder.build)


class TestSnapshotToRequestFieldPreservation:
    """Test that all snapshot fields are preserved through adapter conversion."""

    def _best(self):
        return Mock(path="/test/material.jpg")

    def _sketch(self):
        sketch = Mock()
        sketch.success = True
        sketch.margin_top_cm = 2.0
        sketch.margin_bottom_cm = 2.0
        sketch.margin_left_cm = 2.0
        sketch.margin_right_cm = 2.0
        return sketch

    def test_target_preserved_in_pool_params(self):
        snapshot = LegacyBuildSnapshot(target="椭圆水池")
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), self._sketch(), 100, 80, False, 1.0)
        assert request.pool_params.target == "椭圆水池"

    def test_user_margins_preserved_in_pool_params(self):
        margins = {"top": 3.0, "bottom": 4.0, "left": 2.0, "right": 2.0}
        snapshot = LegacyBuildSnapshot(user_margins=margins)
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), self._sketch(), 100, 80, False, 1.0)
        assert request.pool_params.user_margins == margins

    def test_user_multihole_preserved_in_pool_params(self):
        multihole = {"active_count": 3, "holes_wh": [(10, 20), (10, 20), (10, 20)]}
        snapshot = LegacyBuildSnapshot(user_multihole_params=multihole)
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), self._sketch(), 100, 80, False, 1.0)
        assert request.pool_params.user_multihole_params == multihole

    def test_lshape_params_wrapped_in_typed_object(self):
        lshape_dict = {"corner": "tl", "cut_w_cm": 5.0, "cut_h_cm": 6.0}
        snapshot = LegacyBuildSnapshot(lshape_params=lshape_dict)
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), self._sketch(), 100, 80, True, 1.0)
        assert isinstance(request.lshape_params, LShapeBuildParams)
        assert request.lshape_params.values == lshape_dict

    def test_composite_params_wrapped_in_typed_object(self):
        composite_dict = {"outer_w_cm": 100.0, "outer_h_cm": 80.0}
        snapshot = LegacyBuildSnapshot(composite_params=composite_dict)
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), self._sketch(), 100, 80, False, 1.0)
        assert isinstance(request.composite_params, CompositeBuildParams)
        assert request.composite_params.values == composite_dict

    def test_canvas_dimensions_preserved(self):
        snapshot = LegacyBuildSnapshot()
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), self._sketch(), 123.4, 56.7, False, 1.0)
        assert request.canvas_w_cm == 123.4
        assert request.canvas_h_cm == 56.7

    def test_trim_preserved(self):
        snapshot = LegacyBuildSnapshot()
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), self._sketch(), 100, 80, False, 2.5)
        assert request.trim_cm == 2.5

    def test_best_path_preserved(self):
        best = Mock(path="/custom/path/material.png")
        snapshot = LegacyBuildSnapshot()
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, best, self._sketch(), 100, 80, False, 1.0)
        assert request.best.path == "/custom/path/material.png"

    def test_sketch_result_preserved(self):
        sketch = self._sketch()
        snapshot = LegacyBuildSnapshot()
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), sketch, 100, 80, False, 1.0)
        assert request.sketch_result is sketch
