"""浅色边框中的虚线装饰和 JPEG 过渡不应改变 L 形补边结构。"""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from core.geometry import RectShape
from core.lshape_border import apply_lshape_border_completion, detect_border_v13
from core.lshape_border_route import detect_border_profile


def _dashed_material():
    img = Image.new('RGB', (800, 800), (248, 244, 233))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 799, 799), outline=(4, 0, 0), width=8)
    for pos in range(20, 780, 20):
        d.rectangle((pos-3, 65, pos+3, 94), fill=(197, 185, 171))
        d.rectangle((pos-3, 705, pos+3, 734), fill=(197, 185, 171))
        d.rectangle((65, pos-3, 94, pos+3), fill=(197, 185, 171))
        d.rectangle((705, pos-3, 734, pos+3), fill=(197, 185, 171))
    return img


def test_dashed_decoration_is_not_a_solid_v13_band():
    detected = detect_border_v13(_dashed_material())
    assert detected is not None
    assert detected[1] == 0, '虚线被识别成了整条深色色带'


@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
@pytest.mark.parametrize('staircase', [False, True])
def test_dashed_decoration_does_not_paint_a_solid_cut_band(corner, staircase):
    src = _dashed_material()
    canvas = np.asarray(src).copy()
    x = 0 if corner in ('tl', 'bl') else 600
    y = 0 if corner in ('tl', 'tr') else 680
    canvas[y:y+120, x:x+200] = 255
    rects = None
    if staircase:
        rects = [(600, 0, 800, 120), (760, 120, 800, 200)]
        if corner in ('tl', 'bl'):
            rects = [(800-r, t, 800-l, b) for l, t, r, b in rects]
        if corner in ('bl', 'br'):
            rects = [(l, 800-b, r, 800-t) for l, t, r, b in rects]
        for l, t, r, b in rects:
            canvas[t:b, l:r] = 255
    assert apply_lshape_border_completion(
        canvas, src, RectShape(0, 0, 800, 800), corner, 200, 120,
        src_material_img=src, staircase_cut_rects=rects)
    if rects:
        for l, t, r, b in rects:
            assert np.all(canvas[t:b, l:r] == 255)
    if corner in ('bl', 'br'):
        canvas = np.flipud(canvas)
    if corner in ('tl', 'bl'):
        canvas = np.fliplr(canvas)
    np.testing.assert_array_equal(canvas[140, 680], (248, 244, 233))


def _transition_material():
    img = Image.new('RGB', (800, 600), (210, 190, 160))
    d = ImageDraw.Draw(img)
    # 独立的一像素过渡模拟真实 JPEG 框线两侧的中间色。
    for inset, color in ((0, (0, 0, 0)), (8, (120, 110, 90)),
                         (9, (247, 233, 206)), (89, (207, 190, 164)),
                         (90, (168, 148, 123)), (96, (215, 198, 172)),
                         (97, (247, 233, 206)), (125, (210, 190, 160))):
        d.rectangle((inset, inset, 799-inset, 599-inset), fill=color)
    return img


def test_profile_keeps_transition_depth_before_inner_line():
    layers = detect_border_profile(_transition_material())
    assert layers is not None and len(layers) == 3
    assert sum(t for _, t in layers[:2]) == 90, '过滤过渡色后内框线位置发生偏移'


@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
def test_inner_line_join_does_not_overrun_original_line(corner):
    src = _transition_material()
    canvas = np.asarray(src).copy()
    x = 0 if corner in ('tl', 'bl') else 600
    y = 0 if corner in ('tl', 'tr') else 480
    canvas[y:y+120, x:x+200] = 255
    assert apply_lshape_border_completion(
        canvas, src, RectShape(0, 0, 800, 600), corner, 200, 120,
        src_material_img=src)
    if corner in ('bl', 'br'):
        canvas = np.flipud(canvas)
    if corner in ('tl', 'bl'):
        canvas = np.fliplr(canvas)
    # 水平新细线在右侧接原竖框：原框外侧的米色带必须保持米色。
    np.testing.assert_array_equal(canvas[212, 710], np.asarray(src)[212, 710])


@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
def test_slightly_asymmetric_source_uses_each_edge_depth(corner):
    src = _transition_material().resize((1728, 1200), Image.Resampling.NEAREST)
    canvas = np.asarray(src).copy()
    x = 0 if corner in ('tl', 'bl') else 1328
    y = 0 if corner in ('tl', 'tr') else 960
    canvas[y:y+240, x:x+400] = 255
    before = canvas.copy()
    assert apply_lshape_border_completion(
        canvas, src, RectShape(0, 0, 1728, 1200), corner, 400, 240,
        src_material_img=src)
    new_line = (np.all(canvas == (168, 148, 123), axis=2)
                & ~np.all(before == (168, 148, 123), axis=2))
    original = np.asarray(src)
    xs = np.flatnonzero(np.all(original[600] == (168, 148, 123), axis=1))
    ys = np.flatnonzero(np.all(original[:, 864] == (168, 148, 123), axis=1))
    outer_band = np.ones((1200, 1728), dtype=bool)
    outer_band[ys[0]:ys[-1]+1, xs[0]:xs[-1]+1] = False
    assert not np.any(new_line & outer_band), '轻微边宽差被平均后接头溢出'
