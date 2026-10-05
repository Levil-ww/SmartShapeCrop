"""单洞水池素材填充与空白洞共用圆角几何，边距不随填充方式变化。"""
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from core.geometry import CropDesign
from core.image_ops import (
    _compute_border_mask, _get_inner_pixel_mask, _make_lod_design, render_design,
)


@pytest.fixture
def pool_design(tmp_path):
    outer = tmp_path / "outer.png"
    inner = tmp_path / "inner.png"
    Image.new("RGB", (240, 180), (121, 78, 46)).save(outer)
    Image.new("RGB", (160, 100), (245, 230, 200)).save(inner)
    return CropDesign(
        canvas_w_cm=24, canvas_h_cm=18, dpi=50,
        inner_margin_top_cm=4, inner_margin_bottom_cm=3,
        inner_margin_left_cm=5, inner_margin_right_cm=2,
        pool_outer_material_image=str(outer),
        pool_inner_material_image=str(inner),
    )


@pytest.mark.parametrize("radii", [
    (0, 0, 0, 0), (2, 0, 0, 0), (0, 2, 0, 0),
    (0, 0, 2, 0), (0, 0, 0, 2), (1, 2, 3, 4), (8, 8, 8, 8),
])
@pytest.mark.parametrize("quality", ["preview", "export"])
def test_material_and_blank_share_shape_and_border(pool_design, radii, quality):
    d = pool_design
    for key, radius in zip(("tl", "tr", "bl", "br"), radii):
        setattr(d, f"corner_{key}_cm", radius)
    blank = replace(d, pool_hole_transparent=True, pool_inner_material_image=None)
    expected = _get_inner_pixel_mask(blank)
    actual = _get_inner_pixel_mask(d)
    assert np.array_equal(actual, expected)
    assert d.inner_rect_px() == blank.inner_rect_px()
    border = _compute_border_mask(d, d.canvas_w_px, d.canvas_h_px, actual, 10)
    expected_border = _compute_border_mask(
        blank, blank.canvas_w_px, blank.canvas_h_px, expected, 10,
    )
    assert np.array_equal(border, expected_border)
    filled_image = np.array(render_design(d, quality=quality))
    blank_image = np.array(render_design(blank, quality=quality))
    assert np.all(filled_image[border] == 0)
    assert np.all(filled_image[actual & ~border] == (245, 230, 200))
    assert np.array_equal(filled_image[~actual], blank_image[~actual])


@pytest.mark.parametrize("mode", ["rect_hole", "ellipse_hole", "rect_lshape", "rect_lshape_hole"])
def test_non_single_pool_material_keeps_existing_geometry(pool_design, mode):
    d = replace(pool_design, mode=mode, corner_tl_cm=2, corner_tr_cm=3,
                corner_bl_cm=1, corner_br_cm=4)
    without_material = replace(d, pool_inner_material_image=None)
    if mode == "rect_hole":
        # 多洞的形状仍由各洞矩形决定，不启用这次单洞圆角规则。
        d.pool_is_multi_hole = without_material.pool_is_multi_hole = True
        holes = [dict(x_cm=5, y_cm=4, w_cm=5, h_cm=6),
                 dict(x_cm=12, y_cm=4, w_cm=5, h_cm=6)]
        d.pool_holes_cm = without_material.pool_holes_cm = holes
    mask = _get_inner_pixel_mask(d)
    old_mask = _get_inner_pixel_mask(without_material)
    assert np.array_equal(mask, old_mask)
    assert np.array_equal(
        _compute_border_mask(d, d.canvas_w_px, d.canvas_h_px, mask, 10),
        _compute_border_mask(without_material, d.canvas_w_px, d.canvas_h_px, old_mask, 10),
    )


def test_missing_material_keeps_existing_geometry(pool_design, tmp_path):
    d = replace(pool_design, corner_tl_cm=2,
                pool_inner_material_image=str(tmp_path / "missing.png"))
    assert np.array_equal(_get_inner_pixel_mask(d),
                          _get_inner_pixel_mask(replace(d, pool_inner_material_image=None)))


@pytest.mark.parametrize("scale", [0.25, 0.5])
def test_lod_material_matches_blank_geometry(pool_design, scale):
    d = replace(pool_design, corner_tl_cm=1, corner_tr_cm=2,
                corner_bl_cm=3, corner_br_cm=4)
    lod = _make_lod_design(d, round(d.canvas_w_px * scale), round(d.canvas_h_px * scale))
    blank = replace(lod, pool_hole_transparent=True, pool_inner_material_image=None)
    mask = _get_inner_pixel_mask(lod)
    assert np.array_equal(mask, _get_inner_pixel_mask(blank))
    image = np.array(render_design(lod, quality="preview", pixel_scale=scale, skip_validate=True))
    blank_image = np.array(render_design(blank, quality="preview", pixel_scale=scale,
                                        skip_validate=True))
    assert np.array_equal(image[~mask], blank_image[~mask])
    border = _compute_border_mask(lod, lod.canvas_w_px, lod.canvas_h_px, mask,
                                  max(2, int(np.ceil(10 * scale))))
    assert np.all(image[border] == 0)
    assert np.all(image[mask & ~border] == (245, 230, 200))
