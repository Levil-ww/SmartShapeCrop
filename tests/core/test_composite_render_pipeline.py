import numpy as np

from core.geometry import COMPOSITE_MODE, CropDesign
from core.image_ops import _get_inner_pixel_mask


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
