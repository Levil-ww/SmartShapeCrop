"""Masked export fills must retain the legacy pixels, including feather edges."""
import numpy as np
import pytest
from PIL import Image

from core.geometry import CropDesign, BorderLayer, compute_border_bands, _erode_mask
from core.image_ops import _render_band_layers, _seam_feather_paste, load_and_fit


@pytest.mark.parametrize('kind', ['empty', 'full', 'rect', 'scattered'])
@pytest.mark.parametrize('delta', [0, 4, 80])
def test_feather_pixels_equal_legacy(kind, delta):
    rng = np.random.default_rng(17)
    outer = rng.integers(0, 150, (37, 53, 3), dtype=np.uint8)
    inner = outer + delta
    mask = np.zeros(outer.shape[:2], dtype=bool)
    if kind == 'full':
        mask[:] = True
    elif kind == 'rect':
        mask[7:29, 11:41] = True
    elif kind == 'scattered':
        mask[:] = rng.random(mask.shape) > 0.4
    expected = outer.copy()
    edge1 = _erode_mask(mask, 1)
    s0 = mask & ~edge1
    expected[mask] = inner[mask]
    if s0.any() and float(np.abs(outer[s0].astype(np.int16) - inner[s0].astype(np.int16)).mean()) >= 5:
        s1 = edge1 & ~_erode_mask(mask, 2)
        expected[s0] = (0.70 * outer[s0].astype(np.float32)
                        + 0.30 * inner[s0].astype(np.float32)).astype(np.uint8)
        expected[s1] = (0.30 * outer[s1].astype(np.float32)
                        + 0.70 * inner[s1].astype(np.float32)).astype(np.uint8)
    actual = outer.copy()
    inner.setflags(write=False)
    _seam_feather_paste(actual, mask, inner)
    assert np.array_equal(actual, expected)


@pytest.mark.parametrize('mode', ['rect_hole', 'ellipse_hole', 'rect_lshape'])
@pytest.mark.parametrize('fill', ['solid', 'image', 'tile', 'missing'])
def test_border_pixels_equal_legacy(mode, fill, tmp_path):
    path = tmp_path / 'texture.png'
    rng = np.random.default_rng(18)
    Image.fromarray(rng.integers(0, 256, (13, 19, 3), dtype=np.uint8)).save(path)
    d = CropDesign(mode=mode, canvas_w_cm=12, canvas_h_cm=15, dpi=30,
                   inner_margin_top_cm=3, inner_margin_bottom_cm=3,
                   inner_margin_left_cm=3, inner_margin_right_cm=3,
                   borders=[BorderLayer(offset_cm=0.2, color=(13, 47, 91),
                        fill_type='solid' if fill == 'solid' else 'image',
                        image_path=str(path) if fill != 'missing' else '',
                        tile_mode=fill == 'tile')])
    w, h = d.canvas_w_px, d.canvas_h_px
    actual = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    expected = actual.copy()
    for mask, layer in compute_border_bands(d):
        if layer.fill_type == 'image' and layer.image_path:
            img = load_and_fit(layer.image_path, w, h,
                               mode='tile' if layer.tile_mode else 'cover', quality='export')
            color = np.array(img, dtype=np.uint8)
        else:
            color = np.full((h, w, 3), layer.color, dtype=np.uint8)
        expected[mask] = color[mask]
    _render_band_layers(actual, d, w, h, 'export', False, False)
    assert np.array_equal(actual, expected)
