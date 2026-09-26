import numpy as np
import pytest

from core.geometry import (
    COMPOSITE_MODE,
    CropDesign,
    RectShape,
    build_composite_mask,
    build_lshape_mask,
    is_lshape_layout,
)


def _case():
    size = (185, 88)
    outer = RectShape(0, 0, 185, 88)
    cuts = [('tr', 35, 10)]
    hole = RectShape(45, 10, 80, 60)
    return size, outer, cuts, hole


def test_composite_mask_preserves_set_identities():
    size, outer, cuts, hole = _case()
    lmask = np.asarray(build_lshape_mask(size, outer, 'tr', 35, 10, {}), dtype=bool)
    composite = build_composite_mask(size, outer, cuts, hole)
    hole_mask = np.zeros((size[1], size[0]), dtype=bool)
    hole_mask[10:71, 45:126] = True

    assert np.array_equal(composite | hole_mask, lmask)
    assert not np.any(composite & hole_mask)
    assert np.array_equal(composite, lmask & ~hole_mask)


def test_composite_mode_validation_uses_outer_frame_for_cuts():
    design = CropDesign(
        mode=COMPOSITE_MODE,
        canvas_w_cm=185,
        canvas_h_cm=88,
        inner_margin_left_cm=45,
        inner_margin_right_cm=60,
        inner_margin_top_cm=10,
        inner_margin_bottom_cm=18,
        l_corner='tr',
        l_cut_w_cm=35,
        l_cut_h_cm=10,
    )
    design.validate()


def test_composite_mode_rejects_cut_that_exceeds_outer_frame():
    design = CropDesign(
        mode=COMPOSITE_MODE,
        canvas_w_cm=100,
        canvas_h_cm=80,
        l_corner='tr',
        l_cut_w_cm=100,
        l_cut_h_cm=10,
    )
    with pytest.raises(ValueError):
        design.validate()


def test_composite_mode_is_lshape_layout():
    assert is_lshape_layout('rect_lshape')
    assert is_lshape_layout(COMPOSITE_MODE)
    assert not is_lshape_layout('rect_hole')
