"""浅色素材外描边：抗锯齿和分辨率不得改变圆角线宽。"""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from core.image_cropper import apply_border_only_corners


@pytest.mark.parametrize('width', [1, 9, 16, 28])
@pytest.mark.parametrize('aspect', [1.4, 1.8])
def test_light_material_arc_matches_straight_outline(width, aspect):
    h, radius = 360, 110
    w = int(h * aspect)
    img = Image.new('RGB', (w, h), (247, 234, 205))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, w - 1, h - 1), outline=(0, 0, 0), width=width)
    if width > 1:
        # JPEG/缩放在最外一行留下过渡色，真实线宽仍为 width。
        draw.rectangle((0, 0, w - 1, h - 1), outline=(180, 180, 180), width=1)
    original = np.asarray(img).copy()
    result = np.asarray(apply_border_only_corners(
        img, dict.fromkeys(('tl', 'tr', 'bl', 'br'), radius * 2.54 / 150), dpi=150,
    ))
    for cx, cy, sx, sy in [(radius, radius, -1, -1),
                            (w-radius, radius, 1, -1),
                            (radius, h-radius, -1, 1),
                            (w-radius, h-radius, 1, 1)]:
        # 沿 45° 法线检查连续黑线及内侧素材，不用检测器结果作预期。
        for depth in range(1, width):
            d = (radius - depth) / np.sqrt(2)
            assert np.mean(result[round(cy+sy*d), round(cx+sx*d)]) < 80
        d = (radius - max(0.5, width / 2)) / np.sqrt(2)
        assert np.mean(result[round(cy+sy*d), round(cx+sx*d)]) < 80
        d = (radius - width - 3) / np.sqrt(2)
        assert tuple(result[round(cy+sy*d), round(cx+sx*d)]) == (247, 234, 205)
    assert np.array_equal(result[h//2-20:h//2+20], original[h//2-20:h//2+20])


@pytest.mark.parametrize('detected', [None, [((0, 0, 0), 28)]])
def test_confirmed_wide_outline_is_redrawn_even_when_detection_is_accurate(detected):
    img = Image.new('RGB', (500, 360), (247, 234, 205))
    ImageDraw.Draw(img).rectangle((0, 0, 499, 359), outline=(0, 0, 0), width=28)
    result = np.asarray(apply_border_only_corners(
        img, {'tl': 110 * 2.54 / 150}, pre_detected_layers=detected,
    ))
    for depth in range(1, 28):
        p = round(110 - (110 - depth) / np.sqrt(2))
        assert np.mean(result[p, p]) < 80


def test_light_edge_transition_does_not_suppress_outline():
    """外沿浅色过渡带不能让真实黑线被当作浅色背景而漏补。"""
    img = Image.new('RGB', (500, 360), (247, 234, 205))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 499, 359), outline=(0, 0, 0), width=16)
    draw.rectangle((0, 0, 499, 359), outline=(245, 245, 245), width=3)
    result = np.asarray(apply_border_only_corners(img, {'tl': 110 * 2.54 / 150}))
    for depth in range(4, 15):
        p = round(110 - (110 - depth) / np.sqrt(2))
        assert np.mean(result[p, p]) < 80
