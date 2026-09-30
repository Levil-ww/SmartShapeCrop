from core.multihole_layout import layout_holes


def test_layout_holes_horizontal_and_vertical_share_geometry_contract():
    sizes = [(10, 4), (8, 6)]
    gaps = [2]
    margins = [lambda _i: 1.0, lambda _i: 2.0, lambda i: 3.0 + i, lambda i: 4.0 + i]
    horizontal = layout_holes('horizontal', 0, 0, sizes, gaps, *margins)
    vertical = layout_holes('vertical', 0, 0, sizes, gaps, *margins)
    assert [(h['x_cm'], h['y_cm']) for h in horizontal] == [(3.0, 1.0), (15.0, 1.0)]
    assert [(h['x_cm'], h['y_cm']) for h in vertical] == [(3.0, 1.0), (4.0, 7.0)]
    assert horizontal[0]['w_cm'] == 10
    assert vertical[1]['h_cm'] == 6
