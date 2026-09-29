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
    DesignBuildContext,
    DesignBuildRequest,
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
