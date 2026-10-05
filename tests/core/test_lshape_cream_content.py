"""厚黑框后的米白内容底不得作为额外纯色条补画。"""
import pytest
from PIL import Image, ImageDraw

from core.geometry import CropDesign
from core.image_ops import render_design
from core.lshape_border_route import _Seg, _classify_profile


@pytest.mark.parametrize('band,field,line,expected_layers', [
    ((254, 248, 234), (254, 248, 234), False, 1),
    ((229, 214, 195), (222, 207, 186), False, 1),
    ((229, 214, 195), (170, 130, 90), False, 2),
    ((158, 115, 81), (235, 226, 209), False, 2),
    ((254, 248, 234), (180, 160, 130), False, 2),
    ((254, 248, 234), (254, 248, 234), True, 3),
])
def test_cream_content_filter_keeps_independent_bands_and_inner_lines(
        band, field, line, expected_layers):
    edge = 8 if line else 120
    segments = [_Seg(0, edge-1, (0, 0, 0), 0), _Seg(edge, edge+69, band, 0)]
    if line:
        segments.append(_Seg(edge+70, edge+73, (70, 60, 50), 0))
    layers = _classify_profile(segments, field, 400, 300, 60)
    assert len(layers) == expected_layers
    if expected_layers > 1:
        assert layers[1] == (band, 70)
    if line:
        assert layers[2] == ((70, 60, 50), 4)


@pytest.mark.parametrize('mode', ['rect_lshape', 'rect_lshape_hole'])
@pytest.mark.parametrize('dpi', [12.7, 25.4])
def test_thick_black_border_does_not_flatten_cream_content(tmp_path, mode, dpi):
    """庄园秘境式切边的黑框应直接接原有花纹，而不是抹掉花纹。"""
    src = Image.new('RGB', (1600, 1200), (254, 248, 234))
    draw = ImageDraw.Draw(src)
    draw.rectangle((0, 0, 1599, 1199), outline=(0, 0, 0), width=140)
    draw.rectangle((230, 230, 1369, 969), outline=(0, 0, 0), width=6)
    # 检测扫描线避开这块花纹；米白底的中位色仍与内容中心一致。
    draw.rectangle((1340, 530, 1390, 550), fill=(90, 60, 40))
    path = tmp_path / 'cream_content.png'
    src.save(path)
    design = CropDesign(
        mode=mode, canvas_w_cm=160, canvas_h_cm=120, dpi=dpi,
        pool_outer_material_image=str(path), pool_hole_transparent=True,
        l_cuts_cm=[{'corner': 'tr', 'cut_w_cm': 40, 'cut_h_cm': 35}],
        inner_margin_top_cm=70 if mode == 'rect_lshape_hole' else 0,
        inner_margin_bottom_cm=10 if mode == 'rect_lshape_hole' else 0,
        inner_margin_left_cm=60 if mode == 'rect_lshape_hole' else 0,
        inner_margin_right_cm=60 if mode == 'rect_lshape_hole' else 0,
    )
    result = render_design(design)
    scale = dpi / 25.4
    assert result.getpixel((round(1360*scale), round(540*scale))) == (90, 60, 40)
    assert result.getpixel((round(1360*scale), round(400*scale))) == (0, 0, 0)
    assert result.getpixel((round(1360*scale), round(100*scale))) == (255, 255, 255)


@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
def test_maria_muted_cream_content_is_preserved_in_both_panels(tmp_path, corner):
    src = Image.new('RGB', (3200, 1200), (222, 207, 186))
    draw = ImageDraw.Draw(src)
    draw.rectangle((0, 0, 3199, 1199), outline=(0, 0, 0), width=154)
    draw.rectangle((154, 154, 3045, 1045), outline=(229, 214, 195), width=102)
    px = 3000 if corner in ('tr', 'br') else 200
    py = 535 if corner in ('tl', 'tr') else 664
    draw.rectangle((px-8, py-8, px+8, py+8), fill=(93, 70, 50))
    path = tmp_path / 'muted_cream_content.png'
    src.save(path)
    outputs = []
    for mode in ('rect_lshape', 'rect_lshape_hole'):
        result = render_design(CropDesign(
            mode=mode, canvas_w_cm=320, canvas_h_cm=120, dpi=25.4,
            pool_outer_material_image=str(path), pool_hole_transparent=True,
            l_cuts_cm=[dict(corner=corner, cut_w_cm=40, cut_h_cm=35)],
            inner_margin_left_cm=60 if mode == 'rect_lshape_hole' else 0,
            inner_margin_right_cm=60 if mode == 'rect_lshape_hole' else 0,
            inner_margin_top_cm=20 if mode == 'rect_lshape_hole' else 0,
            inner_margin_bottom_cm=20 if mode == 'rect_lshape_hole' else 0,
        ))
        assert result.getpixel((px, py)) == (93, 70, 50)
        box = (0, 0, 550, 1200) if corner in ('tl', 'bl') else (2650, 0, 3200, 1200)
        outputs.append(result.crop(box).tobytes())
    assert outputs[0] == outputs[1]
