"""JPEG 色带过渡不能参与内框细线的四边取色。"""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from core.geometry import CropDesign
from core.image_ops import render_design
from core.lshape_border_route import detect_border_profile


def _material():
    image = Image.new('RGB', (1000, 800), (234, 217, 187))
    draw = ImageDraw.Draw(image)
    for inset, color, width in [
        (0, (6, 0, 2), 8),
        (8, (247, 233, 206), 90),
        (98, (168, 148, 123), 25),
    ]:
        draw.rectangle((inset, inset, 999-inset, 799-inset),
                       outline=color, width=width)
    # 原素材左边的 JPEG 过渡：色带两端略有偏色，中段仍是原色。
    for x, color in [
        (8, (51, 48, 39)), (9, (211, 208, 191)),
        (10, (253, 249, 224)), (11, (229, 224, 195)),
        (12, (245, 235, 208)),
        (96, (255, 241, 215)), (97, (240, 226, 200)),
    ]:
        draw.line((x, 150, x, 650), fill=color)
    return image


def test_inner_line_detection_preserves_material_color():
    layers = detect_border_profile(_material())
    assert layers is not None and len(layers) == 3
    assert layers[2][0] == (168, 148, 123)


@pytest.mark.parametrize('mode', ['rect_lshape', 'rect_lshape_hole'])
@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
def test_rendered_cut_inner_line_matches_original(tmp_path, mode, corner):
    path = tmp_path / 'material.png'
    _material().save(path)
    design = CropDesign(
        mode=mode, canvas_w_cm=100, canvas_h_cm=80, dpi=25.4,
        pool_outer_material_image=str(path), pool_hole_transparent=True,
        l_cuts_cm=[dict(corner=corner, cut_w_cm=30, cut_h_cm=25)],
        inner_margin_left_cm=40 if mode == 'rect_lshape_hole' else 0,
        inner_margin_right_cm=40 if mode == 'rect_lshape_hole' else 0,
        inner_margin_top_cm=35 if mode == 'rect_lshape_hole' else 0,
        inner_margin_bottom_cm=15 if mode == 'rect_lshape_hole' else 0,
    )
    result = np.asarray(render_design(design, quality='export'))
    if corner in ('tr', 'br'):
        result = np.fliplr(result)
    if corner in ('bl', 'br'):
        result = np.flipud(result)
    # 切口下方新补的横线，和远处未裁切的原素材横线同色。
    assert tuple(result[355, 180]) == (168, 148, 123)
    assert tuple(result[105, 700]) == (168, 148, 123)
