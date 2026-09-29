"""LegacyRequestAdapter.from_snapshot boundary tests.

Tests edge cases and error handling in the adapter layer:
1. None/empty value handling
2. Type coercion boundaries
3. Missing required fields
4. Invalid input types
"""
import pytest
from unittest.mock import Mock
from core.geometry import CropDesign
from workers.design_builders import (
    LegacyBuildSnapshot,
    LegacyRequestAdapter,
    PoolBuildParams,
    LShapeBuildParams,
    CompositeBuildParams,
    DesignBuildValidationError,
)


class TestNoneValueHandling:
    """Test adapter handling of None values."""

    def _best(self):
        return Mock(path="/test/material.jpg")

    def test_none_target_becomes_empty_string(self):
        snapshot = LegacyBuildSnapshot(target=None)
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1.0)
        assert request.pool_params.target == ""
        assert isinstance(request.pool_params.target, str)

    def test_none_user_margins_preserved(self):
        snapshot = LegacyBuildSnapshot(user_margins=None)
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1.0)
        assert request.pool_params.user_margins is None

    def test_none_user_multihole_preserved(self):
        snapshot = LegacyBuildSnapshot(user_multihole_params=None)
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1.0)
        assert request.pool_params.user_multihole_params is None

    def test_none_lshape_params_becomes_none(self):
        snapshot = LegacyBuildSnapshot(lshape_params=None)
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, True, 1.0)
        assert request.lshape_params is None

    def test_none_composite_params_becomes_none(self):
        snapshot = LegacyBuildSnapshot(composite_params=None)
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1.0)
        assert request.composite_params is None

    def test_none_sketch_result_preserved(self):
        snapshot = LegacyBuildSnapshot()
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1.0)
        assert request.sketch_result is None


class TestEmptyValueHandling:
    """Test adapter handling of empty values."""

    def _best(self):
        return Mock(path="/test/material.jpg")

    def test_empty_string_target_preserved(self):
        snapshot = LegacyBuildSnapshot(target="")
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1.0)
        assert request.pool_params.target == ""

    def test_empty_dict_user_margins_preserved(self):
        snapshot = LegacyBuildSnapshot(user_margins={})
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1.0)
        assert request.pool_params.user_margins == {}

    def test_empty_dict_lshape_params_wrapped(self):
        snapshot = LegacyBuildSnapshot(lshape_params={})
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, True, 1.0)
        assert isinstance(request.lshape_params, LShapeBuildParams)
        assert request.lshape_params.values == {}


class TestTypeCoercionBoundaries:
    """Test type coercion at boundaries."""

    def _best(self):
        return Mock(path="/test/material.jpg")

    def test_integer_canvas_width_coerced_to_float(self):
        snapshot = LegacyBuildSnapshot()
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1.0)
        assert isinstance(request.canvas_w_cm, float)
        assert request.canvas_w_cm == 100.0

    def test_integer_canvas_height_coerced_to_float(self):
        snapshot = LegacyBuildSnapshot()
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1.0)
        assert isinstance(request.canvas_h_cm, float)
        assert request.canvas_h_cm == 80.0

    def test_integer_trim_coerced_to_float(self):
        snapshot = LegacyBuildSnapshot()
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1)
        assert isinstance(request.trim_cm, float)
        assert request.trim_cm == 1.0

    def test_string_numeric_coerced_to_float(self):
        snapshot = LegacyBuildSnapshot()
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, "100.5", "80.5", False, "1.5")
        assert request.canvas_w_cm == 100.5
        assert request.canvas_h_cm == 80.5
        assert request.trim_cm == 1.5

    def test_zero_canvas_dimensions_preserved(self):
        snapshot = LegacyBuildSnapshot()
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 0, 0, False, 1.0)
        assert request.canvas_w_cm == 0.0
        assert request.canvas_h_cm == 0.0

    def test_negative_trim_preserved(self):
        snapshot = LegacyBuildSnapshot()
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, -1.0)
        assert request.trim_cm == -1.0


class TestModeSelectionEdgeCases:
    """Test mode selection in edge cases."""

    def _best(self):
        return Mock(path="/test/material.jpg")

    def test_both_lshape_and_composite_selects_composite(self):
        snapshot = LegacyBuildSnapshot(
            lshape_params={"corner": "br"},
            composite_params={"outer_w_cm": 100.0})
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, True, 1.0)
        assert request.mode == "composite"

    def test_empty_lshape_dict_still_selects_lshape_mode(self):
        snapshot = LegacyBuildSnapshot(lshape_params={})
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, True, 1.0)
        assert request.mode == "lshape"

    def test_empty_composite_dict_still_selects_composite_mode(self):
        snapshot = LegacyBuildSnapshot(composite_params={})
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1.0)
        assert request.mode == "composite"

    def test_is_lshape_false_with_lshape_params_selects_pool(self):
        snapshot = LegacyBuildSnapshot(lshape_params={"corner": "br"})
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1.0)
        assert request.mode == "pool"


class TestFieldPreservationIntegrity:
    """Test that all fields are preserved without mutation."""

    def _best(self):
        return Mock(path="/test/material.jpg")

    def _sketch(self):
        sketch = Mock()
        sketch.success = True
        sketch.margin_top_cm = 2.0
        return sketch

    def test_complex_user_margins_dict_preserved(self):
        margins = {
            "top": 2.5,
            "bottom": 3.5,
            "left": 4.5,
            "right": 5.5,
            "extra_key": "value"
        }
        snapshot = LegacyBuildSnapshot(user_margins=margins)
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1.0)
        assert request.pool_params.user_margins == margins

    def test_complex_multihole_params_preserved(self):
        multihole = {
            "active_count": 3,
            "holes_wh": [(10, 20), (15, 25), (20, 30)],
            "gaps_cm": [5.0, 6.0],
            "layout_type": "vertical"
        }
        snapshot = LegacyBuildSnapshot(user_multihole_params=multihole)
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1.0)
        assert request.pool_params.user_multihole_params == multihole

    def test_sketch_result_object_identity_preserved(self):
        sketch = self._sketch()
        snapshot = LegacyBuildSnapshot()
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), sketch, 100, 80, False, 1.0)
        assert request.sketch_result is sketch

    def test_best_object_identity_preserved(self):
        best = self._best()
        snapshot = LegacyBuildSnapshot()
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, best, None, 100, 80, False, 1.0)
        assert request.best is best


class TestRequestImmutability:
    """Test that returned request is immutable."""

    def _best(self):
        return Mock(path="/test/material.jpg")

    def test_request_is_frozen_dataclass(self):
        snapshot = LegacyBuildSnapshot()
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1.0)
        with pytest.raises(AttributeError):
            request.mode = "changed"

    def test_pool_params_is_frozen_dataclass(self):
        snapshot = LegacyBuildSnapshot()
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1.0)
        with pytest.raises(AttributeError):
            request.pool_params.target = "changed"

    def test_lshape_params_is_frozen_dataclass(self):
        snapshot = LegacyBuildSnapshot(lshape_params={"corner": "br"})
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, True, 1.0)
        with pytest.raises(AttributeError):
            request.lshape_params.values = {}

    def test_composite_params_is_frozen_dataclass(self):
        snapshot = LegacyBuildSnapshot(composite_params={"outer_w_cm": 100.0})
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, self._best(), None, 100, 80, False, 1.0)
        with pytest.raises(AttributeError):
            request.composite_params.values = {}
