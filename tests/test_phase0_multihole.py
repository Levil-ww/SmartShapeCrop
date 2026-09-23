"""Phase 0 regression checks for multi-hole visual-edit prerequisites."""

import pytest
from types import SimpleNamespace

from workers.property_panel_workers import _inherit_multihole_material
from workers.property_panel_workers import _InnerMatchWorker


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


def test_multihole_matching_keeps_each_hole_size_and_material_separate(tmp_path):
    class Matcher:
        def __init__(self):
            self.queries = []

        def get_template_dir(self):
            return str(tmp_path)

        def set_template_dir(self, value):
            pass

        def scan_library(self, **kwargs):
            pass

        def find_best_match(self, query):
            self.queries.append(query)
            path = "hole1.png" if "46.0x38.5" in query else "hole2.png"
            return SimpleNamespace(path=path, score=10.0), []

    matcher = Matcher()
    worker = _InnerMatchWorker(
        matcher, str(tmp_path), "花型-98x43CM", True,
        [(46.0, 38.5), (51.0, 42.3)], None,
    )
    result = []
    worker.finished_ok.connect(result.append)
    worker.run()  # synchronous unit invocation; no GUI event loop required

    assert len(matcher.queries) == 2
    assert matcher.queries[0] != matcher.queries[1]
    assert [item['path'] for item in result[0]['holes']] == ["hole1.png", "hole2.png"]
