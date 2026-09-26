import numpy as np

from core.geometry import COMPOSITE_MODE, CropDesign
from core.image_ops import _get_inner_pixel_mask, _compute_border_mask


def test_composite_render_mask_targets_center_hole_only():
    design = CropDesign(
        mode=COMPOSITE_MODE,
        canvas_w_cm=185,
        canvas_h_cm=88,
        dpi=2.54,
        inner_margin_left_cm=45,
        inner_margin_right_cm=60,
        inner_margin_top_cm=10,
        inner_margin_bottom_cm=18,
    )

    mask = _get_inner_pixel_mask(design)

    assert mask.shape == (88, 185)
    assert mask[20, 60]
    assert not mask[5, 5]
    assert not mask[20, 20]


def test_composite_mode_does_not_change_rect_hole_mask_dispatch():
    design = CropDesign(mode='rect_hole', canvas_w_cm=20, canvas_h_cm=20, dpi=1,
                        inner_margin_left_cm=2, inner_margin_right_cm=2,
                        inner_margin_top_cm=2, inner_margin_bottom_cm=2)
    mask = _get_inner_pixel_mask(design)
    assert isinstance(mask, np.ndarray)
    assert mask[5, 5]


def test_composite_border_contains_hole_and_cut_edges():
    design = CropDesign(
        mode=COMPOSITE_MODE, canvas_w_cm=100, canvas_h_cm=80, dpi=2.54,
        inner_margin_left_cm=20, inner_margin_right_cm=20,
        inner_margin_top_cm=10, inner_margin_bottom_cm=10,
        l_corner='tr', l_cut_w_cm=15, l_cut_h_cm=10,
    )
    inner = _get_inner_pixel_mask(design)
    border = _compute_border_mask(design, design.canvas_w_px, design.canvas_h_px,
                                  inner, 2)
    assert border[10, 20]
    assert border[10, 84]
