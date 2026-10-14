"""长条素材的横纵内框线宽不同时，L 形切边仍应补完整三层。"""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from core.geometry import RectShape
from core.lshape_border import apply_lshape_border_completion
from core.lshape_border_route import detect_border_profile


def long_material(line_color=(70, 60, 50)):
    """模拟 59×299cm 素材：纵框线 40px、横框线 8px，色带相同。"""
    image = Image.new('RGB', (3000, 600), (205, 185, 155))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 2999, 599), outline=(20, 18, 16), width=8)
    draw.rectangle((8, 8, 2991, 591), outline=(243, 236, 220), width=60)
    for box in ((68, 68, 107, 531), (2892, 68, 2931, 531),
                (68, 68, 2931, 75), (68, 524, 2931, 531)):
        draw.rectangle(box, fill=line_color)
    return image


@pytest.mark.parametrize('line_color', [(70, 60, 50), (168, 148, 123)])
@pytest.mark.parametrize('transpose', [False, True])
def test_long_material_keeps_three_layer_border(line_color, transpose):
    src = long_material(line_color)
    if transpose:
        src = src.transpose(Image.Transpose.TRANSPOSE)
    layers = detect_border_profile(src)
    assert layers is not None, '横纵框线宽度不同不应导致层数投票失败'
    assert [color for color, _ in layers] == [
        (20, 18, 16), (243, 236, 220), line_color]


@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
@pytest.mark.parametrize('line_color', [(70, 60, 50), (168, 148, 123)])
def test_long_material_standard_cut_repaints_band_and_line(corner, line_color):
    src = long_material(line_color)
    canvas = np.array(src)
    # 截图中的 32×27cm 切口，采用 10px/cm。
    x0 = 0 if corner in ('tl', 'bl') else 2680
    y0 = 0 if corner in ('tl', 'tr') else 330
    canvas[y0:y0+270, x0:x0+320] = 255
    assert apply_lshape_border_completion(
        canvas, src, RectShape(0, 0, 3000, 600), corner, 320, 270,
        src_material_img=src)
    view = canvas
    if corner in ('tr', 'br'):
        view = np.fliplr(view)
    if corner in ('bl', 'br'):
        view = np.flipud(view)
    assert tuple(view[180, 350]) == (243, 236, 220)
    assert tuple(view[180, 390]) == line_color
    assert tuple(view[300, 180]) == (243, 236, 220)
    assert tuple(view[340, 180]) == line_color
    np.testing.assert_array_equal(view[:270, :320], 255)


def test_long_material_missing_two_inner_lines_is_not_forced_to_three_layers():
    src = long_material()
    draw = ImageDraw.Draw(src)
    for box in ((68, 76, 107, 523), (2892, 76, 2931, 523)):
        draw.rectangle(box, fill=(205, 185, 155))
    assert detect_border_profile(src) is None


def directional_material():
    """长条拉伸后的色带：横向深度120px，纵向深度60px。"""
    image = Image.new('RGB', (3000, 600), (205, 185, 155))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 2999, 599), outline=(20, 18, 16), width=8)
    for box in ((8, 8, 127, 591), (2872, 8, 2991, 591),
                (8, 8, 2991, 67), (8, 532, 2991, 591)):
        draw.rectangle(box, fill=(243, 236, 220))
    for box in ((128, 68, 167, 531), (2832, 68, 2871, 531),
                (128, 68, 2871, 75), (128, 524, 2871, 531)):
        draw.rectangle(box, fill=(70, 60, 50))
    return image


@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
def test_directional_border_joins_without_gap_or_overhang(corner):
    src = directional_material()
    canvas = np.array(src)
    x0 = 0 if corner in ('tl', 'bl') else 2680
    y0 = 0 if corner in ('tl', 'tr') else 330
    canvas[y0:y0+270, x0:x0+320] = 255
    assert apply_lshape_border_completion(
        canvas, src, RectShape(0, 0, 3000, 600), corner, 320, 270,
        src_material_img=src)
    view = canvas
    if corner in ('tr', 'br'):
        view = np.fliplr(view)
    if corner in ('bl', 'br'):
        view = np.flipud(view)
    # 竖向新框线必须直接接上原横框线；横向新框线不能越过原竖框线。
    np.testing.assert_array_equal(view[68:100, 450], [(70, 60, 50)] * 32)
    assert tuple(view[340, 120]) == (243, 236, 220)
    assert tuple(view[340, 130]) == (70, 60, 50)
    np.testing.assert_array_equal(view[:270, :320], 255)
