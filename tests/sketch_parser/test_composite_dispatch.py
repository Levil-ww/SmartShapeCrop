from services import sketch_parser


def test_shape_dispatch_selects_composite_parser(monkeypatch):
    called = {}

    def fake_parser(path, **kwargs):
        called.update(path=path, kwargs=kwargs)
        return 'composite'

    monkeypatch.setattr(sketch_parser, 'parse_composite_sketch', fake_parser)
    result = sketch_parser.parse_shape_sketch(
        'drawing.png', mode='rect_lshape_hole',
        target_outer_w_cm=185, target_outer_h_cm=88,
    )

    assert result == 'composite'
    assert called['path'] == 'drawing.png'
    assert called['kwargs']['target_outer_w_cm'] == 185
    assert called['kwargs']['target_outer_h_cm'] == 88


def test_shape_dispatch_keeps_lshape_parser_separate(monkeypatch):
    monkeypatch.setattr(sketch_parser, 'parse_lshape_sketch', lambda *a, **k: 'lshape')
    assert sketch_parser.parse_shape_sketch('drawing.png', mode='rect_lshape') == 'lshape'
