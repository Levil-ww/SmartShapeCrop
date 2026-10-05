"""两个面板在不同素材/画布尺寸、DPI、角位下共用自动补边规则。"""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from core.geometry import CropDesign
from core.image_ops import render_design
from core.geometry import RectShape
from core.lshape_border import _try_v13_with_params


@pytest.fixture(scope='module', params=[
    (style, size) for style in ('cream_content', 'brown_band', 'three_layers')
    for size in ((1600, 1200), (2400, 900))
])
def border_material(request, tmp_path_factory):
    style, size = request.param
    w, h = size
    field = (222, 207, 186) if style == 'cream_content' else (218, 205, 185)
    im = Image.new('RGB', size, field)
    d = ImageDraw.Draw(im)
    edge = 120 if style == 'cream_content' else 8
    band = (229, 214, 195) if style == 'cream_content' else (
        (108, 70, 55) if style == 'brown_band' else (243, 236, 220))
    d.rectangle((0, 0, w-1, h-1), outline=(0, 0, 0), width=edge)
    d.rectangle((edge, edge, w-edge-1, h-edge-1), outline=band, width=60)
    if style == 'three_layers':
        d.rectangle((edge+60, edge+60, w-edge-61, h-edge-61),
                    outline=(70, 60, 50), width=3)
    path = tmp_path_factory.mktemp('border_matrix') / 'material.png'
    im.save(path)
    return str(path)


@pytest.mark.parametrize('canvas_cm', [(80, 40), (132, 83), (191, 52), (83, 132)])
@pytest.mark.parametrize('dpi', [12.7, 25.4])
@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
def test_same_cut_matches_between_panels_across_sizes(border_material, canvas_cm, dpi, corner):
    cw, ch = canvas_cm
    results = []
    for mode in ('rect_lshape', 'rect_lshape_hole'):
        composite = mode == 'rect_lshape_hole'
        image = render_design(CropDesign(
            mode=mode, canvas_w_cm=cw, canvas_h_cm=ch, dpi=dpi,
            pool_outer_material_image=border_material, pool_hole_transparent=True,
            l_cuts_cm=[dict(corner=corner, cut_w_cm=cw*.18, cut_h_cm=ch*.20)],
            inner_margin_left_cm=cw*.40 if composite else 0,
            inner_margin_right_cm=cw*.40 if composite else 0,
            inner_margin_top_cm=ch*.30 if composite else 0,
            inner_margin_bottom_cm=ch*.30 if composite else 0,
        ))
        w, h = image.size
        box = (0, 0, round(w*.35), h) if corner in ('tl', 'bl') else (round(w*.65), 0, w, h)
        results.append(np.array(image.crop(box)))
    # 允许边缘渲染的近黑抗锯齿差值，色带/细框的位置不能偏移。
    delta = np.abs(results[0].astype(int)-results[1].astype(int))
    assert delta.max() <= 3


@pytest.mark.parametrize('dpi', [75, 150])
def test_preview_and_export_resolution_match_between_panels(border_material, dpi):
    test_same_cut_matches_between_panels_across_sizes(border_material, (80, 40), dpi, 'tr')


@pytest.mark.parametrize('mode', ['rect_lshape', 'rect_lshape_hole'])
@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
def test_portrait_source_rotation_preserves_border(border_material, tmp_path, mode, corner):
    rotated = tmp_path / 'material_rotated.png'
    with Image.open(border_material) as source:
        source.transpose(Image.Transpose.ROTATE_90).save(rotated)
    results = []
    for path in (border_material, str(rotated)):
        design = CropDesign(
            mode=mode, canvas_w_cm=132, canvas_h_cm=83, dpi=25.4,
            pool_outer_material_image=path, pool_hole_transparent=True,
            l_cuts_cm=[dict(corner=corner, cut_w_cm=24, cut_h_cm=16)],
            inner_margin_left_cm=53, inner_margin_right_cm=53,
            inner_margin_top_cm=25, inner_margin_bottom_cm=25,
        )
        # L 面板没有中心洞，外框仍铺满画布。
        if mode == 'rect_lshape':
            design.inner_margin_left_cm = design.inner_margin_right_cm = 0
            design.inner_margin_top_cm = design.inner_margin_bottom_cm = 0
        results.append(np.asarray(render_design(design)))
    assert np.array_equal(*results)


@pytest.mark.parametrize('route', ['v13', 'legacy'])
def test_band_follows_actual_horizontal_and_vertical_scale(route, monkeypatch):
    source = Image.new('RGB', (400, 200), (220, 210, 190))
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 399, 199), outline=(0, 0, 0), width=4)
    draw.rectangle((4, 4, 395, 195), outline=(100, 60, 40), width=20)
    material = source.resize((200, 200), Image.Resampling.NEAREST)
    canvas = np.array(material)
    if route == 'v13':
        assert _try_v13_with_params(
            canvas, material, RectShape(0, 0, 200, 200), 'tl', 60, 40,
            source, .5, 1, (4, 20, (0, 0, 0), (100, 60, 40)), None, None)
    else:
        from core import lshape_border
        monkeypatch.setattr(lshape_border, 'detect_pool_material_borders',
                            lambda *args: [((0, 0, 0), 4), ((100, 60, 40), 20)])
        assert lshape_border._apply_legacy_border_path(
            canvas, RectShape(0, 0, 200, 200), 'tl', 60, 40, source,
            (220, 210, 190), .5, 1, None, None)
    assert np.all(canvas[30, 62:72] == (100, 60, 40))
    assert tuple(canvas[30, 72]) == (220, 210, 190)
    assert np.all(canvas[44:64, 30] == (100, 60, 40))
    assert tuple(canvas[64, 30]) == (220, 210, 190)
