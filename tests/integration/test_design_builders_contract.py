"""Contract tests for DesignBuildContext / DesignBuilder / BUILDERS / LegacyRequestAdapter.

Unlike test_design_builders_equivalence.py (which checks geometry outcomes),
these tests pin the structural contracts the Worker relies on:
context shape, protocol conformance, dispatch table contents,
request field order, and adapter field mapping.
"""
import dataclasses
from types import SimpleNamespace

import pytest

from core.geometry import CropDesign
from workers.design_builders import (
    BUILDERS,
    CompositeBuildParams,
    CompositeDesignBuilder,
    DesignBuildError,
    DesignBuildContext,
    DesignBuildRequest,
    DesignBuildValidationError,
    DesignBuilder,
    LegacyBuildSnapshot,
    LShapeBuildParams,
    LShapeDesignBuilder,
    LegacyRequestAdapter,
    PoolBuildParams,
    PoolDesignBuilder,
)


class _Best:
    path = "material.jpg"


def _sketch(**kwargs):
    values = dict(success=True, margin_top_cm=10.0, margin_bottom_cm=11.0,
                  margin_left_cm=12.0, margin_right_cm=13.0,
                  is_multi_hole=False, holes=[])
    values.update(kwargs)
    return SimpleNamespace(**values)


def _worker(**overrides):
    values = dict(_composite_params=None, _target="花型-101x81CM",
                  _user_margins=None, _user_multihole=None,
                  _lshape_params=None)
    values.update(overrides)
    return SimpleNamespace(**values)


def _snapshot(worker):
    return LegacyBuildSnapshot(
        target=worker._target,
        user_margins=worker._user_margins,
        user_multihole_params=worker._user_multihole,
        lshape_params=worker._lshape_params,
        composite_params=worker._composite_params,
    )


def _spy_context():
    calls, logs = [], []
    context = DesignBuildContext(
        new_design=lambda w, h, trim: (calls.append((w, h, trim)) or _new(w, h, trim)),
        log=logs.append,
    )
    return context, calls, logs


def _new(w, h, trim):
    d = CropDesign(canvas_w_cm=w + trim, canvas_h_cm=h + trim, dpi=150)
    d.outer_margin_cm = 0.0
    return d


def test_context_is_frozen_dataclass_with_callable_fields():
    context = DesignBuildContext(new_design=lambda w, h, t: (w, h, t),
                                 log=lambda msg: None)
    assert dataclasses.is_dataclass(context)
    assert [f.name for f in dataclasses.fields(context)] == ["new_design", "log"]
    assert context.new_design(1.0, 2.0, 3.0) == (1.0, 2.0, 3.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.log = print


def test_builders_satisfy_design_builder_protocol():
    for builder in (PoolDesignBuilder(), LShapeDesignBuilder(),
                    CompositeDesignBuilder()):
        assert isinstance(builder, DesignBuilder)
    for builder in BUILDERS.values():
        assert isinstance(builder, DesignBuilder)


def test_dispatch_table_covers_exactly_three_modes():
    assert set(BUILDERS) == {"pool", "lshape", "composite"}
    assert isinstance(BUILDERS["pool"], PoolDesignBuilder)
    assert isinstance(BUILDERS["lshape"], LShapeDesignBuilder)
    assert isinstance(BUILDERS["composite"], CompositeDesignBuilder)


def test_request_is_frozen_and_positional_order_is_stable():
    request = DesignBuildRequest(
        mode="pool", best=_Best(), sketch_result=_sketch(),
        canvas_w_cm=100.0, canvas_h_cm=80.0, trim_cm=1.0,
        pool_params=PoolBuildParams("花型-101x81CM", {"left": 1.0}, {"a": 1}),
        lshape_params=LShapeBuildParams({"b": 2}),
        composite_params=CompositeBuildParams({"c": 3}))
    assert request.mode == "pool"
    assert request.best is not None
    assert request.canvas_w_cm == 100.0
    assert request.canvas_h_cm == 80.0
    assert request.trim_cm == 1.0
    assert request.pool_params.target == "花型-101x81CM"
    assert request.pool_params.user_margins == {"left": 1.0}
    assert request.pool_params.user_multihole_params == {"a": 1}
    assert request.lshape_params.values == {"b": 2}
    assert request.composite_params.values == {"c": 3}
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.mode = "lshape"


def test_adapter_mode_priority_composite_beats_lshape_beats_pool():
    pool_worker = _worker()
    pool = LegacyRequestAdapter.from_snapshot(
        _snapshot(pool_worker), _Best(), _sketch(), 100, 80, False, 1)
    assert pool.mode == "pool"
    lshape_worker = _worker(_lshape_params={"corner": "tr"})
    lshape = LegacyRequestAdapter.from_snapshot(
        _snapshot(lshape_worker), _Best(), _sketch(),
        100, 80, True, 1)
    assert lshape.mode == "lshape"
    composite_worker = _worker(_composite_params={"cuts_cm": [{"corner": "br"}]},
                               _lshape_params={"corner": "tr"})
    composite = LegacyRequestAdapter.from_snapshot(
        _snapshot(composite_worker),
        _Best(), _sketch(), 100, 80, True, 1)
    assert composite.mode == "composite"


def test_adapter_maps_fields_with_type_coercion():
    worker = _worker(_target=None, _user_margins={"left": 14.0},
                     _user_multihole={"active_count": 2},
                     _lshape_params={"corner": "tr"},
                     _composite_params=None)
    best = _Best()
    request = LegacyRequestAdapter.from_snapshot(_snapshot(worker), best, _sketch(),
                                               100, 80, 1, 1)
    assert request.canvas_w_cm == 100.0 and isinstance(request.canvas_w_cm, float)
    assert request.canvas_h_cm == 80.0 and isinstance(request.canvas_h_cm, float)
    assert request.trim_cm == 1.0 and isinstance(request.trim_cm, float)
    assert request.pool_params.target == ""
    assert request.pool_params.user_margins == {"left": 14.0}
    assert request.pool_params.user_multihole_params == {"active_count": 2}
    assert request.lshape_params.values == {"corner": "tr"}
    assert request.composite_params is None
    assert request.best is best


def test_pool_builder_builds_design_through_context_factory():
    context, calls, logs = _spy_context()
    request = DesignBuildRequest(mode="pool", best=_Best(), sketch_result=_sketch(),
                                 canvas_w_cm=100.0, canvas_h_cm=80.0, trim_cm=1.0,
                                 pool_params=PoolBuildParams(target="花型-101x81CM"))
    design = PoolDesignBuilder().build(request, context)
    assert isinstance(design, CropDesign)
    assert calls == [(100.0, 80.0, 1.0)]
    assert design.mode == "rect_hole"
    assert design.pool_outer_material_image == _Best().path


def test_lshape_builder_builds_design_through_context_factory():
    context, calls, logs = _spy_context()
    request = DesignBuildRequest(mode="lshape", best=_Best(), sketch_result=None,
                                 canvas_w_cm=80.0, canvas_h_cm=100.0, trim_cm=1.0,
                                 lshape_params=LShapeBuildParams(
                                     {"corner": "tr", "cut_w_cm": 20.0,
                                      "cut_h_cm": 15.0}))
    design = LShapeDesignBuilder().build(request, context)
    assert calls == [(80.0, 100.0, 1.0)]
    assert design.mode == "rect_lshape"
    assert design.l_corner == "tr"


def test_composite_builder_returns_validated_design_with_material():
    context, calls, logs = _spy_context()
    request = DesignBuildRequest(
        mode="composite", best=_Best(), sketch_result=None,
        canvas_w_cm=101.0, canvas_h_cm=81.0, trim_cm=1.0,
        composite_params=CompositeBuildParams({
            "outer_w_cm": 100.0, "outer_h_cm": 80.0,
            "corner": "br", "cut_w_cm": 20.0, "cut_h_cm": 15.0,
            "cuts_cm": [{"corner": "br", "cut_w_cm": 20.0, "cut_h_cm": 15.0}],
            "hole_margin_top_cm": 10.0, "hole_margin_bottom_cm": 11.0,
            "hole_margin_left_cm": 12.0, "hole_margin_right_cm": 13.0,
            "hole_fill_mode": "blank",
        }))
    design = CompositeDesignBuilder().build(request, context)
    assert design.mode == "rect_lshape_hole"
    assert design.canvas_w_cm == 101.0 and design.canvas_h_cm == 81.0
    assert design.pool_outer_material_image == _Best().path
    assert design.outer_bg_image == _Best().path


def test_composite_builder_rejects_params_without_cuts():
    request = DesignBuildRequest(
        mode="composite", best=_Best(), sketch_result=None,
        canvas_w_cm=101.0, canvas_h_cm=81.0, trim_cm=1.0,
        composite_params=CompositeBuildParams(
            {"outer_w_cm": 100.0, "outer_h_cm": 80.0}))
    with pytest.raises(ValueError):
        CompositeDesignBuilder().build(request, _spy_context()[0])


# ── Priority 1: Builder boundary tests ──────────────────────────────────


class TestBuilderValidationBoundaries:
    """Builder.validate() rejects invalid requests before any geometry work."""

    def test_pool_builder_rejects_negative_trim(self):
        request = DesignBuildRequest(
            mode="pool", best=_Best(), sketch_result=_sketch(),
            canvas_w_cm=100.0, canvas_h_cm=80.0, trim_cm=-1.0,
            pool_params=PoolBuildParams(target="花型-101x81CM"))
        with pytest.raises(DesignBuildValidationError, match="trim_cm"):
            PoolDesignBuilder().build(request, _spy_context()[0])

    def test_pool_builder_rejects_unknown_mode(self):
        request = DesignBuildRequest(
            mode="hexagon", best=_Best(), sketch_result=_sketch(),
            canvas_w_cm=100.0, canvas_h_cm=80.0, trim_cm=1.0)
        with pytest.raises(DesignBuildValidationError, match="未知构建模式"):
            PoolDesignBuilder().build(request, _spy_context()[0])

    def test_lshape_builder_rejects_missing_lshape_params(self):
        request = DesignBuildRequest(
            mode="lshape", best=_Best(), sketch_result=None,
            canvas_w_cm=80.0, canvas_h_cm=100.0, trim_cm=1.0,
            lshape_params=None)
        with pytest.raises(DesignBuildValidationError, match="lshape_params"):
            LShapeDesignBuilder().build(request, _spy_context()[0])

    def test_composite_builder_rejects_missing_composite_params(self):
        request = DesignBuildRequest(
            mode="composite", best=_Best(), sketch_result=None,
            canvas_w_cm=101.0, canvas_h_cm=81.0, trim_cm=1.0,
            composite_params=None)
        with pytest.raises(DesignBuildValidationError, match="composite_params"):
            CompositeDesignBuilder().build(request, _spy_context()[0])

    def test_pool_builder_succeeds_with_empty_target(self):
        context, calls, _ = _spy_context()
        request = DesignBuildRequest(
            mode="pool", best=_Best(), sketch_result=_sketch(),
            canvas_w_cm=100.0, canvas_h_cm=80.0, trim_cm=1.0,
            pool_params=PoolBuildParams(target=""))
        design = PoolDesignBuilder().build(request, context)
        assert isinstance(design, CropDesign)
        assert design.mode == "rect_hole"

    def test_pool_builder_succeeds_with_none_sketch(self):
        context, calls, _ = _spy_context()
        request = DesignBuildRequest(
            mode="pool", best=_Best(), sketch_result=None,
            canvas_w_cm=100.0, canvas_h_cm=80.0, trim_cm=1.0,
            pool_params=PoolBuildParams(target="花型-101x81CM"))
        design = PoolDesignBuilder().build(request, context)
        assert isinstance(design, CropDesign)
        assert design.inner_margin_top_cm == 8.0

    def test_lshape_builder_succeeds_with_minimal_params(self):
        context, calls, _ = _spy_context()
        request = DesignBuildRequest(
            mode="lshape", best=_Best(), sketch_result=None,
            canvas_w_cm=80.0, canvas_h_cm=100.0, trim_cm=1.0,
            lshape_params=LShapeBuildParams({"corner": "bl"}))
        design = LShapeDesignBuilder().build(request, context)
        assert design.mode == "rect_lshape"
        assert design.l_corner == "bl"
        assert design.l_cut_w_cm == 0.0
        assert design.l_cut_h_cm == 0.0


# ── Priority 3: Adapter field mapping direct assertions ─────────────────


class TestAdapterFieldMapping:
    """Direct assertions on LegacyRequestAdapter output structure."""

    def test_adapter_pool_mode_preserves_all_snapshot_fields(self):
        snapshot = LegacyBuildSnapshot(
            target="花型-101x81CM",
            user_margins={"top": 5.0, "bottom": 6.0},
            user_multihole_params={"active_count": 3},
            lshape_params=None,
            composite_params=None,
        )
        best = _Best()
        sketch = _sketch()
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, best, sketch, 120.5, 90.5, False, 1.5)
        assert request.mode == "pool"
        assert request.pool_params.target == "花型-101x81CM"
        assert request.pool_params.user_margins == {"top": 5.0, "bottom": 6.0}
        assert request.pool_params.user_multihole_params == {"active_count": 3}
        assert request.lshape_params is None
        assert request.composite_params is None
        assert request.best is best
        assert request.sketch_result is sketch

    def test_adapter_wraps_lshape_params_in_typed_wrapper(self):
        snapshot = LegacyBuildSnapshot(
            target="", lshape_params={"corner": "tr", "cut_w_cm": 20.0})
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, _Best(), _sketch(), 100, 80, True, 1)
        assert request.mode == "lshape"
        assert isinstance(request.lshape_params, LShapeBuildParams)
        assert request.lshape_params.values == {"corner": "tr", "cut_w_cm": 20.0}

    def test_adapter_wraps_composite_params_in_typed_wrapper(self):
        composite = {"outer_w_cm": 100.0, "cuts_cm": [{"corner": "br"}]}
        snapshot = LegacyBuildSnapshot(target="", composite_params=composite)
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, _Best(), _sketch(), 100, 80, False, 1)
        assert request.mode == "composite"
        assert isinstance(request.composite_params, CompositeBuildParams)
        assert request.composite_params.values == composite

    def test_adapter_coerces_numeric_types_to_float(self):
        snapshot = LegacyBuildSnapshot(target="test")
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, _Best(), _sketch(), 100, 80, False, 1)
        assert isinstance(request.canvas_w_cm, float)
        assert isinstance(request.canvas_h_cm, float)
        assert isinstance(request.trim_cm, float)
        assert request.canvas_w_cm == 100.0
        assert request.trim_cm == 1.0

    def test_adapter_none_target_becomes_empty_string(self):
        snapshot = LegacyBuildSnapshot(target=None)
        request = LegacyRequestAdapter.from_snapshot(
            snapshot, _Best(), _sketch(), 100, 80, False, 1)
        assert request.pool_params.target == ""
        assert isinstance(request.pool_params.target, str)


# ── Priority 1: Params class validation tests ───────────────────────────


class TestPoolBuildParamsValidation:
    """PoolBuildParams.validate() rejects invalid field types."""

    def test_valid_params_passes(self):
        params = PoolBuildParams(target="test", user_margins={"top": 1.0})
        params.validate()

    def test_non_string_target_rejected(self):
        params = PoolBuildParams(target=123)
        with pytest.raises(DesignBuildValidationError, match="target"):
            params.validate()

    def test_non_dict_user_margins_rejected(self):
        params = PoolBuildParams(user_margins="invalid")
        with pytest.raises(DesignBuildValidationError, match="user_margins"):
            params.validate()

    def test_non_dict_user_multihole_rejected(self):
        params = PoolBuildParams(user_multihole_params=[1, 2, 3])
        with pytest.raises(DesignBuildValidationError, match="user_multihole_params"):
            params.validate()


class TestLShapeBuildParamsValidation:
    """LShapeBuildParams.validate() rejects invalid corner and negative cuts."""

    def test_valid_params_passes(self):
        params = LShapeBuildParams({"corner": "tr", "cut_w_cm": 20.0, "cut_h_cm": 15.0})
        params.validate()

    def test_non_dict_values_rejected(self):
        params = LShapeBuildParams("invalid")
        with pytest.raises(DesignBuildValidationError, match="values"):
            params.validate()

    def test_invalid_corner_rejected(self):
        params = LShapeBuildParams({"corner": "middle"})
        with pytest.raises(DesignBuildValidationError, match="corner"):
            params.validate()

    def test_negative_cut_w_rejected(self):
        params = LShapeBuildParams({"cut_w_cm": -5.0})
        with pytest.raises(DesignBuildValidationError, match="cut_w_cm"):
            params.validate()

    def test_negative_cut_h_rejected(self):
        params = LShapeBuildParams({"cut_h_cm": -10.0})
        with pytest.raises(DesignBuildValidationError, match="cut_h_cm"):
            params.validate()

    def test_zero_cut_is_allowed(self):
        params = LShapeBuildParams({"cut_w_cm": 0.0, "cut_h_cm": 0.0})
        params.validate()


class TestCompositeBuildParamsValidation:
    """CompositeBuildParams.validate() rejects invalid dimensions and cuts."""

    def test_valid_params_passes(self):
        params = CompositeBuildParams({
            "outer_w_cm": 100.0, "outer_h_cm": 80.0,
            "cuts_cm": [{"corner": "br", "cut_w_cm": 20.0}]
        })
        params.validate()

    def test_non_dict_values_rejected(self):
        params = CompositeBuildParams([1, 2, 3])
        with pytest.raises(DesignBuildValidationError, match="values"):
            params.validate()

    def test_zero_outer_w_rejected(self):
        params = CompositeBuildParams({"outer_w_cm": 0.0})
        with pytest.raises(DesignBuildValidationError, match="outer_w_cm"):
            params.validate()

    def test_negative_outer_h_rejected(self):
        params = CompositeBuildParams({"outer_h_cm": -50.0})
        with pytest.raises(DesignBuildValidationError, match="outer_h_cm"):
            params.validate()

    def test_non_list_cuts_rejected(self):
        params = CompositeBuildParams({"cuts_cm": "invalid"})
        with pytest.raises(DesignBuildValidationError, match="cuts_cm"):
            params.validate()

    def test_non_dict_cut_item_rejected(self):
        params = CompositeBuildParams({"cuts_cm": ["invalid"]})
        with pytest.raises(DesignBuildValidationError, match="cuts_cm\\[0\\]"):
            params.validate()

    def test_invalid_cut_corner_rejected(self):
        params = CompositeBuildParams({"cuts_cm": [{"corner": "center"}]})
        with pytest.raises(DesignBuildValidationError, match="corner"):
            params.validate()

    def test_empty_cuts_list_passes(self):
        params = CompositeBuildParams({"cuts_cm": []})
        params.validate()


# ── Priority 3: Composite geometry error path tests ─────────────────────


class TestCompositeGeometryErrorPaths:
    """apply_composite_geometry raises on invalid parameter combinations."""

    def test_missing_outer_w_cm_raises(self):
        request = DesignBuildRequest(
            mode="composite", best=_Best(), sketch_result=None,
            canvas_w_cm=101.0, canvas_h_cm=81.0, trim_cm=1.0,
            composite_params=CompositeBuildParams({
                "outer_h_cm": 80.0,
                "cuts_cm": [{"corner": "br", "cut_w_cm": 20.0, "cut_h_cm": 15.0}]
            }))
        with pytest.raises((KeyError, DesignBuildValidationError)):
            CompositeDesignBuilder().build(request, _spy_context()[0])

    def test_missing_outer_h_cm_raises(self):
        request = DesignBuildRequest(
            mode="composite", best=_Best(), sketch_result=None,
            canvas_w_cm=101.0, canvas_h_cm=81.0, trim_cm=1.0,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 100.0,
                "cuts_cm": [{"corner": "br", "cut_w_cm": 20.0, "cut_h_cm": 15.0}]
            }))
        with pytest.raises((KeyError, DesignBuildValidationError)):
            CompositeDesignBuilder().build(request, _spy_context()[0])

    def test_cut_exceeding_outer_width_raises(self):
        request = DesignBuildRequest(
            mode="composite", best=_Best(), sketch_result=None,
            canvas_w_cm=101.0, canvas_h_cm=81.0, trim_cm=1.0,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 100.0, "outer_h_cm": 80.0,
                "cuts_cm": [{"corner": "br", "cut_w_cm": 150.0, "cut_h_cm": 15.0}]
            }))
        with pytest.raises((ValueError, DesignBuildValidationError)):
            CompositeDesignBuilder().build(request, _spy_context()[0])

    def test_cut_exceeding_outer_height_raises(self):
        request = DesignBuildRequest(
            mode="composite", best=_Best(), sketch_result=None,
            canvas_w_cm=101.0, canvas_h_cm=81.0, trim_cm=1.0,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 100.0, "outer_h_cm": 80.0,
                "cuts_cm": [{"corner": "br", "cut_w_cm": 20.0, "cut_h_cm": 150.0}]
            }))
        with pytest.raises((ValueError, DesignBuildValidationError)):
            CompositeDesignBuilder().build(request, _spy_context()[0])

    def test_builder_validates_params_before_geometry(self):
        """Invalid params should be caught by validate(), not by geometry code."""
        request = DesignBuildRequest(
            mode="composite", best=_Best(), sketch_result=None,
            canvas_w_cm=101.0, canvas_h_cm=81.0, trim_cm=1.0,
            composite_params=CompositeBuildParams({
                "outer_w_cm": -100.0, "outer_h_cm": 80.0,
                "cuts_cm": [{"corner": "br"}]
            }))
        with pytest.raises(DesignBuildValidationError, match="outer_w_cm"):
            CompositeDesignBuilder().build(request, _spy_context()[0])
