"""Equivalence checks for the extracted design builders.

These tests exercise the same pure construction boundaries used by
PoolRenderWorker, so regressions are caught without starting a Qt worker.
"""
from types import SimpleNamespace

from core.geometry import CropDesign
from workers.design_builders import (
    CompositeBuildParams,
    DesignBuildRequest,
    apply_composite_geometry,
    apply_lshape_geometry,
    apply_pool_geometry,
)


class _Best:
    path = "material.jpg"


def _sketch(**kwargs):
    values = dict(success=True, margin_top_cm=10.0, margin_bottom_cm=11.0,
                  margin_left_cm=12.0, margin_right_cm=13.0,
                  is_multi_hole=False, holes=[])
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_pool_builder_preserves_rect_and_margin_contract():
    d = CropDesign(canvas_w_cm=101.0, canvas_h_cm=81.0, dpi=150)
    apply_pool_geometry(d, "花型-101x81CM", _sketch(), 100.0, 80.0,
                        {"left": 14.0}, 1.0, _Best().path)
    assert d.mode == "rect_hole"
    assert (d.inner_margin_top_cm, d.inner_margin_bottom_cm,
            d.inner_margin_left_cm, d.inner_margin_right_cm) == (10.0, 11.0, 14.0, 13.0)
    assert d.pool_outer_material_image == _Best().path


def test_multihole_builder_keeps_positions_and_compensation():
    holes = [SimpleNamespace(w_cm=20.0, h_cm=30.0, margin_top_cm=8.0,
                             margin_bottom_cm=9.0, margin_left_cm=7.0,
                             margin_right_cm=6.0),
             SimpleNamespace(w_cm=25.0, h_cm=30.0, margin_top_cm=8.0,
                             margin_bottom_cm=9.0, margin_left_cm=7.0,
                             margin_right_cm=6.0)]
    sketch = _sketch(is_multi_hole=True, holes=holes,
                     hole_gaps_cm=[5.0], layout_type="horizontal")
    d = CropDesign(canvas_w_cm=101.0, canvas_h_cm=81.0, dpi=150)
    apply_pool_geometry(d, "花型-101x81CM", sketch, 100.0, 80.0,
                        None, 1.0, _Best().path)
    assert d.pool_is_multi_hole is True
    assert len(d.pool_holes_cm) == 2
    assert d.pool_holes_cm[0]["w_cm"] == 21.0
    assert d.pool_holes_gaps_cm == [4.0]


def test_lshape_builder_maps_corner_and_cut_rects():
    d = CropDesign(canvas_w_cm=81.0, canvas_h_cm=101.0, dpi=150)
    apply_lshape_geometry(d, {
        "corner": "tr", "cut_w_cm": 20.0, "cut_h_cm": 15.0,
        "cut_rects": [{"anchor": "tr", "offset_x_cm": 0,
                       "offset_y_cm": 0, "w_cm": 20, "h_cm": 15}],
    }, _Best().path)
    assert d.mode == "rect_lshape"
    assert d.l_corner == "tr"
    assert len(d.l_cut_rects) == 1
    assert d.pool_outer_material_image == _Best().path


def test_composite_builder_preserves_hole_and_cut_parameters():
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
    d = apply_composite_geometry(request)
    assert d.mode == "rect_lshape_hole"
    assert d.l_corner == "br"
    assert d.inner_margin_left_cm == 12.0
    assert d.pool_hole_transparent is True
