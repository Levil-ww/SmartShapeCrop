"""Phase 1 read-only multi-hole overlay tests."""

from core.geometry import CropDesign
from gui.canvas_widget import PreviewCanvas


def test_multihole_overlay_uses_effective_size_and_list_order(qapp):
    canvas = PreviewCanvas()
    design = CropDesign(canvas_w_cm=100.0, canvas_h_cm=80.0, dpi=30,
                         mode='rect_hole')
    design.pool_is_multi_hole = True
    design.pool_holes_cm = [
        {'x_cm': 10.0, 'y_cm': 8.0, 'w_cm': 31.0, 'h_cm': 21.0},
        {'x_cm': 55.0, 'y_cm': 8.0, 'w_cm': 26.0, 'h_cm': 16.0},
    ]
    design.pool_holes_gaps_cm = [4.0]
    canvas._update_pool_holes_overlay(design)

    assert [item['index'] for item in canvas._pool_holes_overlay] == [0, 1]
    assert canvas._pool_holes_overlay[0]['size_cm'] == (31.0, 21.0)
    assert canvas._pool_holes_overlay[1]['size_cm'] == (26.0, 16.0)
    canvas.deleteLater()


def test_multihole_overlay_is_disabled_for_other_modes(qapp):
    canvas = PreviewCanvas()
    design = CropDesign(canvas_w_cm=100.0, canvas_h_cm=80.0, dpi=30,
                         mode='rect_lshape')
    design.pool_is_multi_hole = True
    design.pool_holes_cm = [
        {'x_cm': 1.0, 'y_cm': 1.0, 'w_cm': 10.0, 'h_cm': 10.0},
        {'x_cm': 20.0, 'y_cm': 1.0, 'w_cm': 10.0, 'h_cm': 10.0},
    ]
    canvas._update_pool_holes_overlay(design)
    assert canvas._pool_holes_overlay == []
    canvas.deleteLater()
