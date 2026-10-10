"""阶梯切边应保留素材的三层结构和各方向线宽。"""
import numpy as np
import pytest
from itertools import combinations
from PIL import Image, ImageDraw

from core.geometry import RectShape
from core.lshape_border import apply_lshape_border_completion


def material(style, size=(1000, 800)):
    im = Image.new('RGB', size, (215, 201, 174))
    d = ImageDraw.Draw(im)
    w, h = size
    band = (247, 233, 206)
    d.rectangle((8, 8, w-9, h-9), fill=band)
    d.rectangle((115, 115, w-116, h-116), fill=(215, 201, 174))
    # 点带位于最外内框线之后，不能跟着切口重画。
    d.rectangle((0, 0, w-1, h-1), outline=(0, 0, 0), width=8)
    d.rectangle((8, 8, w-9, h-9), outline=band, width=60)
    if style == 'wide_colored':
        d.rectangle((68, 68, w-69, h-69), outline=(168, 148, 123), width=33)
    else:
        # 平滑灰过渡被旧分段器识别为2px细线，真正的黑线在其后。
        for offset, color, width in [(68, 223, 2), (70, 189, 1),
                                     (71, 158, 1), (72, 126, 1),
                                     (73, 92, 1), (74, 61, 1), (75, 0, 10)]:
            d.rectangle((offset, offset, w-offset-1, h-offset-1),
                        outline=(color, color, color), width=width)
    return im


@pytest.mark.parametrize('style', ['wide_colored', 'soft_black'])
@pytest.mark.parametrize('sx,sy', [(1, 1), (1.4, .8), (.7, 1.2)])
@pytest.mark.parametrize('corner', ['tr', 'tl', 'br', 'bl'])
@pytest.mark.parametrize('native_scale', [.5, 1, 2])
def test_staircase_matches_original_inner_frame(style, sx, sy, corner, native_scale):
    src = material(style)
    src = src.resize((round(src.width*native_scale), round(src.height*native_scale)),
                     Image.Resampling.NEAREST)
    size = (round(src.width*sx), round(src.height*sy))
    oriented = src
    if (src.width > src.height) != (size[0] > size[1]):
        oriented = src.transpose(Image.Transpose.ROTATE_270)
    im = oriented.resize(size, Image.Resampling.BILINEAR)
    original = np.array(im)
    a = original.copy()
    h, w = a.shape[:2]
    x0, x1 = round(w*.6), round(w*.8)
    y0, y1 = round(h*.3), round(h*.6)
    mask = np.zeros((h, w), bool)
    mask[:y0, x0:] = True
    mask[y0:y1, x1:] = True
    rects = [(x0, 0, w, y0), (x1, y0, w, y1)]
    if corner in ('tl', 'bl'):
        a, mask = np.fliplr(a).copy(), np.fliplr(mask).copy()
        rects = [(w-r, t, w-l, b) for l, t, r, b in rects]
    if corner in ('br', 'bl'):
        a, mask = np.flipud(a).copy(), np.flipud(mask).copy()
        rects = [(l, h-b, r, h-t) for l, t, r, b in rects]
    a[mask] = 255
    assert apply_lshape_border_completion(
        a, im, RectShape(0, 0, w, h), corner, 0, 0,
        src_material_img=src, scale_x=sx, scale_y=sy,
        staircase_cut_rects=rects, cut_area_mask=mask)
    assert np.all(a[mask] == 255)
    if corner in ('br', 'bl'):
        a = np.flipud(a)
    if corner in ('tl', 'bl'):
        a = np.fliplr(a)
    color = np.array((168, 148, 123) if style == 'wide_colored' else (0, 0, 0))
    sx, sy = w/oriented.width, h/oriented.height
    def line_width(profile):
        matches = np.linalg.norm(profile.astype(float)-color, axis=1) < 50
        # 排除最外描边，只比较内框线。
        matches[:round(40*min(sx, sy))] = False
        return np.count_nonzero(matches)
    tx, ty = round(115*native_scale*sx), round(115*native_scale*sy)
    assert abs(line_width(a[round(h*.15), x0-tx:x0][::-1])-line_width(original[h//2, :tx])) <= 1
    assert abs(line_width(a[y1:y1+ty, round(w*.85)])-line_width(original[:ty, w//2])) <= 1
    # 台阶中段仍然挖空，不能被外包矩形/补边填回。
    assert np.all(a[:y0, x0:] == 255)
    assert np.all(a[y0:y1, x1:] == 255)


def test_horizontal_and_vertical_antialias_colors_stay_on_their_axis():
    source = np.array(material('soft_black'))
    source[0] = (10, 10, 10)
    source[:, -1] = (30, 30, 30)
    src = Image.fromarray(source)
    canvas = source.copy()
    mask = np.zeros(source.shape[:2], bool)
    mask[:240, 600:] = True
    mask[240:480, 800:] = True
    canvas[mask] = 255
    assert apply_lshape_border_completion(
        canvas, src, RectShape(0, 0, 1000, 800), 'tr', 0, 0,
        src_material_img=src, cut_area_mask=mask,
        staircase_cut_rects=[(600, 0, 1000, 240), (800, 240, 1000, 480)])
    np.testing.assert_array_equal(canvas[480, 850], source[0, 500])
    np.testing.assert_array_equal(canvas[440, 799], source[400, -1])


def test_three_steps_without_mask_preserve_all_cut_rectangles():
    src = material('soft_black', size=(1200, 900))
    source = np.array(src)
    canvas = source.copy()
    rects = [(650, 0, 1200, 200), (850, 200, 1200, 450), (1000, 450, 1200, 700)]
    mask = np.zeros(source.shape[:2], bool)
    for x0, y0, x1, y1 in rects:
        mask[y0:y1, x0:x1] = True
    canvas[mask] = 255
    assert apply_lshape_border_completion(
        canvas, src, RectShape(0, 0, 1200, 900), 'tr', 0, 0,
        src_material_img=src, staircase_cut_rects=rects)
    np.testing.assert_array_equal(canvas[mask], 255)
    # 每一级真正暴露的横边都包含完整黑色内框线。
    for y, x in ((200, 750), (450, 900), (700, 1040)):
        np.testing.assert_array_equal(canvas[y:y+85, x], source[:85, 500])
    np.testing.assert_array_equal(canvas[500, 400], source[500, 400])


def test_plain_staircase_material_falls_back_without_repainting_content():
    src = Image.new('RGB', (800, 600), (247, 233, 206))
    canvas = np.array(src)
    before = canvas.copy()
    assert not apply_lshape_border_completion(
        canvas, src, RectShape(0, 0, 800, 600), 'tr', 0, 0,
        src_material_img=src, staircase_cut_rects=[(600, 0, 800, 200)])
    np.testing.assert_array_equal(canvas, before)


@pytest.mark.parametrize('corners', [pair for n in (2, 3, 4)
                                    for pair in combinations(('tl', 'tr', 'bl', 'br'), n)])
@pytest.mark.parametrize('style', ['wide_colored', 'soft_black'])
def test_multicorner_staircase_uses_each_corners_material_edges(corners, style):
    # 四边最外描边的像素不同，多角不能全部使用主角位的 top/right 剖面。
    source = np.array(material(style))
    source[0] = (10, 10, 10)
    source[-1] = (20, 20, 20)
    source[:, 0] = (30, 30, 30)
    source[:, -1] = (40, 40, 40)
    src = Image.fromarray(source)
    canvas = source.copy()
    mask = np.zeros(source.shape[:2], bool)
    rects = []
    h, w = mask.shape
    per_corner = {}
    for corner in corners:
        # 角内两级：200×80 + 120×80，四角相互分离。
        cuts = [(0, 0, 200, 80), (0, 80, 120, 160)]
        if corner in ('tr', 'br'):
            cuts = [(w-r, t, w-l, b) for l, t, r, b in cuts]
        if corner in ('bl', 'br'):
            cuts = [(l, h-b, r, h-t) for l, t, r, b in cuts]
        per_corner[corner] = cuts
        rects.extend(cuts)
        for l, t, r, b in cuts:
            mask[t:b, l:r] = True
    canvas[mask] = 255
    assert apply_lshape_border_completion(
        canvas, src, RectShape(0, 0, w, h), corners[0], 0, 0,
        src_material_img=src, cut_area_mask=mask, staircase_cut_rects=rects)
    np.testing.assert_array_equal(canvas[mask], 255)
    for corner, cuts in per_corner.items():
        isolated = source.copy()
        single_mask = np.zeros(mask.shape, bool)
        for l, t, r, b in cuts:
            single_mask[t:b, l:r] = True
        isolated[single_mask] = 255
        assert apply_lshape_border_completion(
            isolated, src, RectShape(0, 0, w, h), corner, 0, 0,
            src_material_img=src, cut_area_mask=single_mask, staircase_cut_rects=cuts)
        x = 200 if corner in ('tl', 'bl') else w-201
        y = 40 if corner in ('tl', 'tr') else h-41
        np.testing.assert_array_equal(canvas[y, x],
                                      (30, 30, 30) if corner in ('tl', 'bl') else (40, 40, 40))
        # 整个角位区域的三层结构、抗锯齿应与单边处理一致。
        ys = slice(0, 280) if corner in ('tl', 'tr') else slice(h-280, h)
        xs = slice(0, 320) if corner in ('tl', 'bl') else slice(w-320, w)
        np.testing.assert_array_equal(canvas[ys, xs], isolated[ys, xs])
    np.testing.assert_array_equal(canvas[h//2, w//2], source[h//2, w//2])


def test_deep_single_corner_strips_keep_the_original_material_profile():
    source = np.array(material('soft_black', (1200, 900)))
    source[0] = (10, 10, 10)
    source[-1] = (20, 20, 20)
    src = Image.fromarray(source)
    rects = [(650, 0, 1200, 200), (850, 200, 1200, 450), (1000, 450, 1200, 700)]
    mask = np.zeros(source.shape[:2], bool)
    for l, t, r, b in rects:
        mask[t:b, l:r] = True
    with_rects = source.copy()
    with_mask = source.copy()
    with_rects[mask] = 255
    with_mask[mask] = 255
    assert apply_lshape_border_completion(
        with_rects, src, RectShape(0, 0, 1200, 900), 'tr', 0, 0,
        src_material_img=src, cut_area_mask=mask, staircase_cut_rects=rects)
    # 直接使用单角剖面，与提供同角条带时的结果一致。
    # 直接调用单角阶梯专用路径构造参照。
    from core.lshape_staircase_border import apply_staircase_material_profile
    assert apply_staircase_material_profile(with_mask, src, 'tr', 1, 1, None, mask)
    np.testing.assert_array_equal(with_rects, with_mask)
