"""中心水池复用单洞素材圆角，同时与综合形状外轮廓隔离。"""
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from core.geometry import COMPOSITE_MODE, CropDesign, _build_design_lshape_mask
from core.image_ops import (
    _compute_border_mask, _get_inner_pixel_mask, _make_lod_design, render_design,
)


@pytest.fixture
def design(tmp_path):
    inner = tmp_path / 'inner.png'
    Image.new('RGB', (160, 100), (245, 230, 200)).save(inner)
    return CropDesign(
        mode=COMPOSITE_MODE, canvas_w_cm=24, canvas_h_cm=18, dpi=50,
        inner_margin_top_cm=4, inner_margin_bottom_cm=3,
        inner_margin_left_cm=5, inner_margin_right_cm=2,
        l_corner='tr', l_cut_w_cm=1, l_cut_h_cm=2,
        pool_inner_material_image=str(inner),
    )


@pytest.mark.parametrize('radii', [
    (0, 0, 0, 0), (2, 0, 0, 0), (0, 2, 0, 0),
    (0, 0, 2, 0), (0, 0, 0, 2), (1, 2, 3, 4), (8, 8, 8, 8),
])
@pytest.mark.parametrize('quality', ['preview', 'export'])
def test_material_corners_match_single_pool(design, radii, quality):
    for key, radius in zip(('tl', 'tr', 'bl', 'br'), radii):
        setattr(design, f'hole_corner_{key}_cm', radius)
    pool = replace(design, mode='rect_hole')
    for key, radius in zip(('tl', 'tr', 'bl', 'br'), radii):
        setattr(pool, f'corner_{key}_cm', radius)
    blank = replace(design, pool_hole_transparent=True, pool_inner_material_image=None)
    mask = _get_inner_pixel_mask(design)
    assert np.array_equal(mask, _get_inner_pixel_mask(pool))
    assert np.array_equal(mask, _get_inner_pixel_mask(blank))
    border = _compute_border_mask(design, design.canvas_w_px, design.canvas_h_px, mask, 10)
    pool_border = _compute_border_mask(pool, pool.canvas_w_px, pool.canvas_h_px, mask, 10)
    assert np.array_equal(border & mask, pool_border)
    rendered = np.asarray(render_design(design, quality=quality))
    assert np.all(rendered[mask & ~border] == (245, 230, 200))
    assert np.all(rendered[mask & border] == 0)
    assert np.array_equal(
        _build_design_lshape_mask(design, use_outer=True),
        _build_design_lshape_mask(replace(design, hole_corner_tl_cm=0,
                                        hole_corner_tr_cm=0, hole_corner_bl_cm=0,
                                        hole_corner_br_cm=0), use_outer=True),
    )


@pytest.mark.parametrize('scale', [0.25, 0.5])
def test_lod_scales_independent_hole_corners(design, scale):
    design.hole_corner_tl_cm = 2
    design.hole_corner_br_cm = 4
    lod = _make_lod_design(design, round(design.canvas_w_px * scale),
                           round(design.canvas_h_px * scale))
    actual_scale = min(lod.canvas_w_cm / design.canvas_w_cm,
                       lod.canvas_h_cm / design.canvas_h_cm)
    assert lod.hole_corner_tl_cm == pytest.approx(2 * actual_scale)
    assert lod.hole_corner_br_cm == pytest.approx(4 * actual_scale)
    assert design.hole_corner_tl_cm == 2
    pool = replace(lod, mode='rect_hole', corner_tl_cm=lod.hole_corner_tl_cm,
                   corner_br_cm=lod.hole_corner_br_cm)
    assert np.array_equal(_get_inner_pixel_mask(lod), _get_inner_pixel_mask(pool))


@pytest.mark.parametrize('radius', [-1, float('nan'), float('inf'), 10])
def test_invalid_hole_radius_rejected(design, radius):
    design.hole_corner_tl_cm = radius
    with pytest.raises(ValueError, match='hole_corner_tl_cm'):
        design.validate()
