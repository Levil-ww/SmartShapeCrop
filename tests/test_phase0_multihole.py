"""Phase 0 regression checks for multi-hole visual-edit prerequisites."""

import pytest

from workers.property_panel_workers import _inherit_multihole_material


def _canvas_to_screen(origin, display_size, canvas_size, rect):
    """Mirror the overlay scale rule used by PreviewCanvas."""
    x, y = origin
    width, height = display_size
    canvas_w, canvas_h = canvas_size
    rx, ry, rw, rh = rect
    return (
        x + round(rx * width / canvas_w),
        y + round(ry * height / canvas_h),
        round(rw * width / canvas_w),
        round(rh * height / canvas_h),
    )


@pytest.mark.parametrize("display_size", [(1000, 500), (400, 200)])
def test_overlay_coordinate_scale_is_stable_for_lod_and_window_resize(display_size):
    # Same normalized rectangle must map to the same relative screen position
    # regardless of LOD or widget size.
    result = _canvas_to_screen((10, 20), display_size, (2000, 1000),
                               (500, 250, 1000, 400))
    assert result == (
        10 + round(display_size[0] * 0.25),
        20 + round(display_size[1] * 0.25),
        round(display_size[0] * 0.5),
        round(display_size[1] * 0.4),
    )


def test_ui_override_material_fields_are_inherited_per_hole():
    old = [
        {"inner_material_path": "hole-1.png", "_src_design_w_cm": 40.0},
        {"inner_material_path": "hole-2.png", "_cached_inner_image": object()},
    ]
    assert _inherit_multihole_material(old, 0) == {
        "inner_material_path": "hole-1.png", "_src_design_w_cm": 40.0,
    }
    inherited = _inherit_multihole_material(old, 1)
    assert inherited["inner_material_path"] == "hole-2.png"
    assert "_cached_inner_image" in inherited
    assert _inherit_multihole_material(old, 3) == {}
