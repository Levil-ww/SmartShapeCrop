"""
测试：core/lshape_border_route.py —— 「描边+色带+细边框」结构重走线

覆盖 2026-09-08 新增的 Profile Route 路径（蔓生花 / 中古雨林类素材的
L 形挖角边框补全）：

  - detect_border_profile        四边剖面投票检测（合成素材逐类验证）
  - patch_lshape_cut_layers      N 层切边补画（含内凹角几何分层）
  - apply_lshape_border_completion 自动路由集成（Profile 优先，回退兼容）

设计原则与 test_lshape_border.py 一致：
  - 素材图全部 PIL 现场合成，不依赖外部图片
  - 断言聚焦契约（层数/厚度/颜色/位置），容忍 ±1~2px 分割误差
"""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from core.geometry import RectShape
from core.lshape_border import apply_lshape_border_completion
from core.lshape_border_route import (
    detect_border_profile,
    patch_lshape_cut_layers,
    profile_yields_to_v13,
    _Seg,
    _is_anchor_seg,
)


@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
@pytest.mark.parametrize('cut_cm', [(0.5, 16.9), (2.0, 16.9), (2.49, 16.9),
                                   (16.9, 0.5), (16.9, 2.49)])
@pytest.mark.parametrize('dpi', [25.4, 50.8])
def test_small_cut_does_not_reroute_inner_line(corner, cut_cm, dpi):
    """小切口只补外描边/色带，不在原内框线之外生成新细线。"""
    src = _make_manshenghua_material()
    canvas = np.asarray(src).copy()
    cw, ch = round(cut_cm[0] * dpi / 2.54), round(cut_cm[1] * dpi / 2.54)
    x0 = 0 if corner in ('tl', 'bl') else 800 - cw
    y0 = 0 if corner in ('tl', 'tr') else 600 - ch
    canvas[y0:y0+ch, x0:x0+cw] = 255
    before = canvas.copy()
    assert apply_lshape_border_completion(
        canvas, src, RectShape(0, 0, 800, 600), corner,
        cut_cm[0] * dpi / 2.54, cut_cm[1] * dpi / 2.54,
        dpi=dpi, src_material_img=src,
        preserve_inner_line_corners=frozenset([corner]))
    line = np.all(canvas == (70, 60, 50), axis=2)
    original_line = np.all(before == (70, 60, 50), axis=2)
    assert not np.any(line & ~original_line), '小切口生成了向外溢出的新细线'
    np.testing.assert_array_equal(
        canvas[original_line], before[original_line],
        err_msg='停止重绘之后，原素材内框线仍被色带覆盖')
    np.testing.assert_array_equal(canvas[y0:y0+ch, x0:x0+cw], 255)


@pytest.mark.parametrize('mode', ['rect_lshape', 'rect_lshape_hole'])
@pytest.mark.parametrize('dpi', [25, 75])
@pytest.mark.parametrize('small_cm', [0.5, 2.0, 2.49, 2.5, 2.51])
@pytest.mark.parametrize('short_axis', ['width', 'height'])
@pytest.mark.parametrize('quality', ['export', 'lod'])
def test_render_small_cut_threshold_and_normal_cut_are_independent(
        tmp_path, monkeypatch, mode, dpi, small_cm, short_axis, quality):
    """真实渲染入口：两面板、两种 DPI、损耗边距和 2.5cm 边界。"""
    from core import lshape_border
    from core.geometry import CropDesign
    from core.image_ops import render_design, render_design_lod

    path = tmp_path / 'border.png'
    _make_manshenghua_material().save(path)
    design = CropDesign(
        canvas_w_cm=80, canvas_h_cm=60, dpi=dpi, mode=mode,
        outer_margin_cm=0.5,
        pool_outer_material_image=str(path),
        l_cuts_cm=[
            {'corner': 'br',
             'cut_w_cm': small_cm if short_axis == 'width' else 16.9,
             'cut_h_cm': small_cm if short_axis == 'height' else 16.9},
            {'corner': 'tl', 'cut_w_cm': 19.8, 'cut_h_cm': 20.0},
        ])
    calls = []
    original = lshape_border._patch_lshape_auto_layers

    def capture(canvas, rect, corner, cw, ch, layers_x, layers_y, **kwargs):
        calls.append((corner, kwargs.get('preserve_inner_line', False)))
        before = canvas.copy()
        ok = original(canvas, rect, corner, cw, ch, layers_x, layers_y, **kwargs)
        if kwargs.get('preserve_inner_line'):
            px = sum(t for _, t in layers_x[:2])
            py = sum(t for _, t in layers_y[:2])
            np.testing.assert_array_equal(canvas[py:-py, px:-px], before[py:-py, px:-px])
        return ok

    monkeypatch.setattr(lshape_border, '_patch_lshape_auto_layers', capture)
    result = (render_design_lod(design, scale=0.25) if quality == 'lod'
              else render_design(design, quality='export'))
    assert result.size == (design.canvas_w_px, design.canvas_h_px)
    assert calls == [('br', small_cm < 2.5), ('tl', False)]


# ---------------------------------------------------------------------------
# 合成素材夹具
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
@pytest.mark.parametrize('cut', [(1000, 35), (1130, 150)])
@pytest.mark.parametrize('transpose', [False, True])
@pytest.mark.parametrize('sx,sy', [(1, 1), (1.25, 0.75)])
def test_inner_line_cannot_extend_into_original_outer_band(corner, cut, transpose, sx, sy):
    """100×3.5 / 113×15cm 及其横纵对称：接头不能溢入原外侧色带。"""
    src = _make_manshenghua_material(size=(1210, 620))
    cw, ch = cut
    if transpose:
        src = src.transpose(Image.Transpose.TRANSPOSE)
        cw, ch = ch, cw
    w, h = round(src.width*sx), round(src.height*sy)
    canvas = np.array(src.resize((w, h), Image.Resampling.NEAREST))
    cw, ch = round(cw*sx), round(ch*sy)
    x0 = 0 if corner in ('tl', 'bl') else w-cw
    y0 = 0 if corner in ('tl', 'tr') else h-ch
    canvas[y0:y0+ch, x0:x0+cw] = 255
    before = canvas.copy()
    assert apply_lshape_border_completion(
        canvas, src, RectShape(0, 0, w, h), corner, cw, ch,
        src_material_img=src, scale_x=sx, scale_y=sy)
    xs, ys = np.arange(w), np.arange(h)
    outer_band = ((np.minimum(xs, w-1-xs)[None, :] < round(68*sx)-2)
                  | (np.minimum(ys, h-1-ys)[:, None] < round(68*sy)-2))
    new_line = (np.all(canvas == (70, 60, 50), axis=2)
                & ~np.all(before == (70, 60, 50), axis=2))
    assert not np.any(new_line & outer_band), '内框接头在原外侧色带生成了竖/横线'
    assert np.any(new_line & ~outer_band), '正常切边的内框线应继续补全'
    np.testing.assert_array_equal(canvas[y0:y0+ch, x0:x0+cw], 255)

def _make_keluo_material(size=(600, 400)) -> Image.Image:
    """克罗印花风格：黑描边 6px + 棕色带 40px + 米色底（两层结构）。"""
    w, h = size
    img = Image.new('RGB', size, (245, 235, 215))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, h - 1], outline=(25, 20, 18), width=6)
    d.rectangle([6, 6, w - 7, h - 7], outline=(120, 70, 40), width=40)
    return img


def _make_manshenghua_material(size=(800, 600)) -> Image.Image:
    """蔓生花风格：黑描边8 + 米色边距60 + 细线3 + 点状色带30 + 细线3 + 花田。

    与真实素材 PSD 对齐：黑描边（10 源 px → 合成 8），米色边距、点状色带、
    内细线全部按真实结构排布。Profile 路径应停在「最外内框线」(3 层)。
    点状色带 = 米色底 + 稀疏深色小点（约 1/9 密度，仅限带区），
    验证"点带按均值色处理、不特殊处理点点"。
    """
    w, h = size
    img = Image.new('RGB', size, (205, 185, 155))          # 花田底色
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, h - 1], outline=(20, 18, 16), width=8)       # 黑描边
    d.rectangle([8, 8, w - 9, h - 9], outline=(243, 236, 220), width=60)   # 米色边距
    d.rectangle([68, 68, w - 69, h - 69], outline=(70, 60, 50), width=3)   # 外细线
    # 点状色带：底色米色 + 稀疏深点（整圈带区：边缘距离 ∈ [71, 101)）
    d.rectangle([71, 71, w - 72, h - 72], fill=(243, 236, 220))
    for y in range(71, h - 72):
        for x in range(71, w - 72):
            edge_dist = min(x, y, w - 1 - x, h - 1 - y)
            if 71 <= edge_dist < 101 and (x + y) % 9 == 0:
                d.point((x, y), fill=(90, 70, 50))
    # 花田（band 之外的内区）：回涂花田底色
    d.rectangle([104, 104, w - 105, h - 105], fill=(205, 185, 155))
    d.rectangle([101, 101, w - 102, h - 102], outline=(70, 60, 50), width=3)  # 内细线
    return img


def _make_zhongguyulin_material(size=(600, 400)) -> Image.Image:
    """中古雨林风格：最外黑细描边 4px + 白色边距（文字不模拟）+ 白底花纹。"""
    w, h = size
    img = Image.new('RGB', size, (250, 250, 250))
    ImageDraw.Draw(img).rectangle(
        [0, 0, w - 1, h - 1], outline=(30, 30, 30), width=4)
    return img


def _make_zhongguyulin_full_material(size=(800, 610)) -> Image.Image:
    """中古雨林完整边框：黑描边4 + 白边距40 + 黑框线4 + 文字带30 + 黑框线4。

    文字带 = 白底 + 斜向条纹（周期 12px、密度 1/4，模拟文字行纹理 ——
    周期与密度须足够大，否则被「3 扫描线均值 + 平滑」抹平）。
    高度取 610 使三条扫描行（183/305/427）与条纹相位错开，对应真实素材
    「四边文字排布方向不同、相位天然不同」的行为。
    边框总厚 82px ≈ 短边(610) 的 13%，可通过 15% 结构窗口。
    """
    w, h = size
    img = Image.new('RGB', size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, h - 1], outline=(0, 0, 0), width=4)       # 黑描边
    # 文字带：edge_dist ∈ [48, 78)，白底 + 斜向条纹（周期 12px、密度 1/4，
    # 模拟文字行 —— 周期与密度须足够大，否则被「3 扫描线均值 + 平滑」抹平）
    d.rectangle([48, 48, w - 49, h - 49], fill=(255, 255, 255))
    for y in range(48, h - 48):
        for x in range(48, w - 48):
            edge_dist = min(x, y, w - 1 - x, h - 1 - y)
            if 48 <= edge_dist < 78 and (x + y) % 12 < 3:
                d.point((x, y), fill=(10, 10, 10))
    d.rectangle([44, 44, w - 45, h - 45], outline=(0, 0, 0), width=4)   # 外框线 44-47
    d.rectangle([78, 78, w - 79, h - 79], outline=(0, 0, 0), width=4)   # 内框线 78-81
    return img


def _make_zhuangyuanmiji_material(size=(800, 610)) -> Image.Image:
    """庄园秘境风格：最外 3px 出血白边 + 粗黑带 60px + 米底 + 黑细线 8px。

    关键属性：
      - 出血白边与 V13 检测冲突（V13 要求最外即黑），验证锚点跳过 + 路由让位
      - 粗黑带厚度 ≥50（_THICK_BLACK_MIN），验证厚黑让位规则
      - 粗黑带后无第二条细线在窗口内，验证 2 层 [黑带, 米底] 截断
    """
    w, h = size
    img = Image.new('RGB', size, (254, 248, 234))          # 米色底
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, h - 1], outline=(252, 246, 244), width=3)  # 出血白边
    d.rectangle([3, 3, w - 4, h - 4], outline=(0, 0, 0), width=60)        # 粗黑带 [3,63)
    d.rectangle([100, 100, w - 101, h - 101], outline=(0, 0, 0), width=8)  # 内框线（窗口外）
    return img


# ---------------------------------------------------------------------------
# 1. detect_border_profile —— 结构检测
# ---------------------------------------------------------------------------

class TestDetectBorderProfile:

    def test_common_thick_black_frame_survives_content_layer_vote_split(self):
        """花满金陵式四边黑框一致、浅色内容层不一致时不能丢掉黑框。"""
        img = Image.new('RGB', (1000, 800), (255, 243, 221))
        draw = ImageDraw.Draw(img)
        draw.rectangle((0, 0, 999, 799), outline=(0, 0, 0), width=100)
        draw.rectangle((100, 100, 899, 699), outline=(250, 234, 210), width=40)
        draw.rectangle((140, 140, 859, 659), outline=(0, 0, 0), width=4)
        draw.rectangle((100, 100, 899, 119), fill=(255, 255, 255))
        draw.rectangle((100, 680, 899, 699), fill=(255, 255, 255))
        layers = detect_border_profile(img)
        assert layers == [((0, 0, 0), 100)]

    def test_gray_compression_prefix_does_not_end_thick_frame_before_band(self):
        """繁花说原图上下边的3px深灰过渡不能充当第一条独立细线。"""
        img = Image.new('RGB', (800, 600), (139, 99, 75))
        draw = ImageDraw.Draw(img)
        draw.rectangle((0, 0, 799, 599), outline=(0, 0, 0), width=100)
        draw.rectangle((100, 100, 699, 499), outline=(251, 235, 219), width=40)
        draw.rectangle((0, 0, 799, 2), fill=(22, 22, 22))
        draw.rectangle((0, 597, 799, 599), fill=(22, 22, 22))
        layers = detect_border_profile(img)
        assert layers is not None
        assert len(layers) == 2
        assert layers[0] == ((0, 0, 0), 100)
        assert layers[1] == ((251, 235, 219), 40)

    @pytest.mark.parametrize('case', ['recover', 'blocked', 'different_band'])
    @pytest.mark.parametrize('directional_scale', [False, True])
    def test_patterned_band_rescans_before_rejecting_two_vs_two_vote(self, case, directional_scale):
        """繁花说式纹饰命中水平扫描线时，仍应识别独立的浅色色带。"""
        img = Image.new('RGB', (800, 600), (139, 99, 75))
        draw = ImageDraw.Draw(img)
        draw.rectangle((0, 0, 799, 599), outline=(0, 0, 0), width=100)
        draw.rectangle((100, 100, 699, 499), outline=(251, 235, 219), width=40)
        for x in (240, 400):
            draw.rectangle((x-3, 100, x+3, 129), fill=(0, 0, 0))
            draw.rectangle((x-3, 470, x+3, 499), fill=(0, 0, 0))
        if case == 'blocked':
            for x in (160, 320, 480):
                draw.rectangle((x-3, 100, x+3, 129), fill=(0, 0, 0))
                draw.rectangle((x-3, 470, x+3, 499), fill=(0, 0, 0))
        elif case == 'different_band':
            draw.rectangle((660, 140, 699, 459), fill=(240, 200, 160))
        layers = detect_border_profile(img)
        if case != 'recover':
            assert layers is None  # 复扫没有足够一致证据时仍拒绝，不强行升为两层。
            return
        assert layers is not None
        assert len(layers) == 2
        assert layers[0][0] == (0, 0, 0)
        assert layers[1][0] == (251, 235, 219)
        assert abs(layers[1][1] - 40) <= 2
        canvas = np.asarray(img).copy()
        canvas[:160, :200] = 255
        canvas[:160, 600:] = 255
        assert apply_lshape_border_completion(
            canvas, img, RectShape(0, 0, 800, 600), 'tr', 200, 160,
            src_material_img=img, cuts=[('tl', 200, 160), ('tr', 200, 160)],
            directional_scale=directional_scale)
        # 检测保留浅色结构信息，但繁花说式厚黑框只补黑色，不铺纯米条。
        np.testing.assert_array_equal(canvas[270, 650], (139, 99, 75))
        np.testing.assert_array_equal(canvas[140, 480], (139, 99, 75))
        np.testing.assert_array_equal(canvas[140, 550], (0, 0, 0))
        np.testing.assert_array_equal(canvas[100, 700], (255, 255, 255))

    def test_jpeg_gray_outer_frame_is_anchor(self):
        """压缩后约 110 灰的连续外框仍应作为锚点，避免 Profile 整体放弃。"""
        assert _is_anchor_seg(_Seg(0, 5, (108, 101, 100), 2.0))

    def test_keluo_two_layers(self):
        """克罗印花风格 → [黑细描边, 深色带] 2 层，路由层让位 V13（保护已认可效果）。"""
        layers = detect_border_profile(_make_keluo_material())
        assert layers is not None, '克罗印花应检出 2 层结构'
        assert len(layers) == 2, f'期望 2 层，实际: {layers}'
        (c1, t1), (c2, t2) = layers
        assert max(c1) < 90 and 4 <= t1 <= 9         # 黑描边 ~6
        assert _is_brownish(c2) and 34 <= t2 <= 46  # 棕带 ~40
        assert profile_yields_to_v13(layers) is True

    def test_manshenghua_three_layers(self):
        """蔓生花风格 → [黑描边, 米色边距, 细线] 3 层（点带/内细线不处理）。"""
        layers = detect_border_profile(_make_manshenghua_material())
        assert layers is not None, '蔓生花风格应命中 Profile 路径'
        assert len(layers) == 3, f'期望 3 层（止于最外内框线），实际: {layers}'
        (c_stroke, t_stroke), (c_band, t_band), (c_line, t_line) = layers
        # 黑描边 ~8
        assert max(c_stroke) < 90 and 5 <= t_stroke <= 11
        # 米色边距 ~60
        assert _color_close(c_band, (243, 236, 220)) and 54 <= t_band <= 66
        # 内细线（点带之前的"最外内框线"）~3
        assert max(c_line) < 120 and 1 <= t_line <= 6

    def test_zhongguyulin_stroke_only(self):
        """中古雨林风格 → [黑细描边] 1 层（白色边距 ≈ field 色，文字不处理）。"""
        layers = detect_border_profile(_make_zhongguyulin_material())
        assert layers is not None
        assert len(layers) == 1, f'期望 1 层黑描边，实际: {layers}'
        (c, t), = layers
        assert max(c) < 90 and 2 <= t <= 7

    def test_zhongguyulin_full_three_layers(self):
        """中古雨林完整边框 → 3 层（描边 / 白边距 / 框线；文字带不处理）。

        锚点对齐 + 截断到第二条细线 → [黑4, 白40, 线4]。
        """
        layers = detect_border_profile(_make_zhongguyulin_full_material())
        assert layers is not None, '完整雨林边框应命中 3 层结构'
        assert len(layers) == 3, f'期望 3 层（止于最外内框线），实际: {layers}'
        (c1, t1), (c2, t2), (c3, t3) = layers
        assert max(c1) < 90 and 2 <= t1 <= 7           # 黑描边 ~4
        assert _color_close(c2, (255, 255, 255)) and 34 <= t2 <= 46     # 白边距 ~40
        assert max(c3) < 90 and 2 <= t3 <= 7           # 内框线 ~4

    def test_zhuangyuanmiji_thick_band(self):
        """庄园秘境风格 → [粗黑带, 米底] 2 层；锚点跳过出血白边，厚黑让位 V13。"""
        layers = detect_border_profile(_make_zhuangyuanmiji_material())
        assert layers is not None, '庄园秘境粗黑带应命中 Profile'
        assert len(layers) == 2, f'期望 2 层（窗口内无第二条线），实际: {layers}'
        (c1, t1), (c2, t2) = layers
        # 锚点跳过 3px 出血白边，落在粗黑带
        assert max(c1) < 90 and 55 <= t1 <= 65          # 粗黑带 ~60
        assert _color_close(c2, (254, 248, 234)) and 30 <= t2 <= 40  # 米底 ~37
        # 厚黑带应让位 V13
        assert profile_yields_to_v13(layers) is True

    def test_left_bleed_edge_does_not_break_anchor(self):
        """单侧出血白边（真实素材 left 边偶发 2~6px 白边）→ 锚点跳过 → 层序仍对齐。

        修复 2026-09-08：未加锚点对齐前，left 2px 白边会让四边投票把颜色
        平均成灰糊，导致 L 形挖角补画视觉上"无效果"。
        """
        img = _make_zhongguyulin_full_material()
        d = ImageDraw.Draw(img)
        w, h = img.size
        d.rectangle([0, 0, 1, h - 1], fill=(255, 255, 255))   # left 边 2px 白
        layers = detect_border_profile(img)
        assert layers is not None
        assert len(layers) == 3
        # 黑描边应该仍是黑色（不被白边平均成灰）
        c1, _ = layers[0]
        assert max(c1) < 90, f'锚点未被正确对齐: 首层 {c1}'
        # 白边距仍为白
        c2, t2 = layers[1]
        assert _color_close(c2, (255, 255, 255)) and 34 <= t2 <= 46

    def test_yield_rules(self):
        """profile_yields_to_v13 让位判定各场景。"""
        assert profile_yields_to_v13([]) is False
        # 厚黑描边（庄园秘境粗黑带）
        assert profile_yields_to_v13([((10, 10, 10), 60)]) is True
        # 克罗印花（黑细描边+深色带）
        assert profile_yields_to_v13([((25, 20, 18), 6),
                                     ((120, 70, 40), 40)]) is True
        # 中古雨林（黑细描边+白边距 ≠ 深色带）
        assert profile_yields_to_v13([((10, 10, 10), 10),
                                     ((255, 255, 255), 40)]) is False
        # 蔓生花（黑细描边+米色带 ≈ field 色）
        assert profile_yields_to_v13([((20, 18, 16), 8),
                                     ((243, 236, 220), 60)]) is False
        # 首层非黑
        assert profile_yields_to_v13([((243, 236, 220), 60)]) is False
        # 主色带后误分出的近白内容底应回到 V13 的两层路径，避免重复补画色带。
        assert profile_yields_to_v13([((8, 8, 8), 10),
                                     ((209, 196, 173), 240),
                                     ((234, 223, 208), 86)]) is True

    def test_thin_black_with_neutral_light_content_keeps_only_black_layer(self):
        """素华牡丹类黑边+近白内容底不应生成额外浅色色带。"""
        img = Image.new('RGB', (800, 600), (180, 160, 130))
        ImageDraw.Draw(img).rectangle(
            [0, 0, 799, 599], outline=(8, 8, 8), width=10)
        ImageDraw.Draw(img).rectangle(
            [10, 10, 789, 589], fill=(255, 255, 236))
        layers = detect_border_profile(img)
        assert layers == [((8, 8, 8), 10)]

    def test_plain_material_returns_none(self):
        """纯色素材（无边框）→ None（回退旧路径，画布不被修改）。"""
        assert detect_border_profile(Image.new('RGB', (400, 300), (255, 255, 255))) is None
        assert detect_border_profile(Image.new('RGB', (400, 300), (128, 128, 128))) is None

    def test_thick_black_border_returns_layers_via_yield(self):
        """首层厚黑描边 → Profile 仍返回 layers（让位判定外移到路由层）。"""
        layers = detect_border_profile(_make_bordered(60))
        assert layers is not None
        assert len(layers) == 2, f'期望 [黑厚带, 白边距] 2 层，实际: {layers}'
        (c1, t1), (c2, t2) = layers
        assert max(c1) < 90 and 50 <= t1 <= 70          # 粗黑带 ~60
        assert _color_close(c2, (255, 255, 255)) and 35 <= t2 <= 55  # 白边距 ~45
        assert profile_yields_to_v13(layers) is True
        # _make_bordered(150) 仍是 None（巨段黑不在窗口内）
        assert detect_border_profile(_make_bordered(150)) is None

    def test_small_material_returns_none(self):
        """素材过小（短边 < 40px）→ None。"""
        assert detect_border_profile(Image.new('RGB', (30, 20), (200, 0, 0))) is None

    def test_none_returns_none(self):
        assert detect_border_profile(None) is None


# ---------------------------------------------------------------------------
# 2. patch_lshape_cut_layers —— N 层补画
# ---------------------------------------------------------------------------

class TestPatchLshapeCutLayers:

    LAYERS = [((0, 0, 0), 10), ((120, 70, 40), 20), ((70, 60, 50), 5)]  # T=35

    def _canvas(self, w=400, h=300):
        return np.full((h, w, 3), 255, dtype=np.uint8)

    def test_tr_corner_layer_order(self):
        """tr 挖角：保留区在切边左侧/下侧，层序从切边向内：黑→棕→深灰。"""
        canvas = self._canvas()
        out = patch_lshape_cut_layers(canvas, 'tr', 200, 0, 200, 100, self.LAYERS)
        # 垂直切边 x=200（保留区在左）：黑 [190,200)，棕 [170,190)，深灰 [165,170)
        np.testing.assert_array_equal(out[50, 195], [0, 0, 0])
        np.testing.assert_array_equal(out[50, 180], [120, 70, 40])
        np.testing.assert_array_equal(out[50, 167], [70, 60, 50])
        np.testing.assert_array_equal(out[50, 160], [255, 255, 255])  # 层外不动
        # 水平切边 y=100（保留区在下）：黑 [100,110)，棕 [110,130)，深灰 [130,135)
        np.testing.assert_array_equal(out[105, 300], [0, 0, 0])
        np.testing.assert_array_equal(out[120, 300], [120, 70, 40])
        np.testing.assert_array_equal(out[132, 300], [70, 60, 50])
        np.testing.assert_array_equal(out[140, 300], [255, 255, 255])
        # 缺口区（x≥200 且 y<100）保持原样
        np.testing.assert_array_equal(out[50, 300], [255, 255, 255])

    def test_reflex_corner_geometric_layering(self):
        """内凹角 (200,100)：dx<=edge 用 dy 分层；dx>edge 用 max(dx, dy) L 形分层。

        [2026-09-16 修复]
        原始代码对所有 dx 统一用 max(dx, dy) 分层，导致 dx<=edge 范围内**同一行内**
        颜色跳变（dx 小 → 黑描边，dx 大 → band），翻回后每行从黑渐变到 band，
        就是用户看到的"多出一截线段"。

        修复后：
        - dx<=edge: 用 dy 决定层（searchsorted(offs, dy)），和水平切边层结构一致，
          忽略 dx。保证 dx<=edge 范围内每行颜色一致。
        - dx>edge: max(dx, dy) L 形分层填充。
        """
        canvas = self._canvas()
        out = patch_lshape_cut_layers(canvas, 'tr', 200, 0, 200, 100, self.LAYERS)
        # dx=5<=edge(10), dy=5<edge → 用 dy=5 分层 → 层0 黑
        np.testing.assert_array_equal(out[105, 195], [0, 0, 0])
        # dx=10<=edge(10), dy=15>=edge → 用 dy=15 分层 → 层1 棕
        np.testing.assert_array_equal(out[115, 190], [120, 70, 40])
        # dx=10<=edge(10), dy=30>=edge+band → 用 dy=30 分层 → 层2 深灰
        np.testing.assert_array_equal(out[130, 190], [70, 60, 50])
        # dx=12>edge → max 分层；d=max(12,32)=32 ∈ [30,35) → 层2 深灰
        np.testing.assert_array_equal(out[132, 188], [70, 60, 50])
        # d>35 → 超出层范围不动
        np.testing.assert_array_equal(out[140, 170], [255, 255, 255])

    def test_bl_corner_flips(self):
        """bl 挖角经双向翻转后同样正确（缺口贴左下角，切边补在缺口右上两侧）。"""
        canvas = self._canvas()
        out = patch_lshape_cut_layers(canvas, 'bl', 0, 200, 200, 100, self.LAYERS)
        # 垂直切边 x=200（缺口右边界，保留区在右）：黑 [200,210)
        np.testing.assert_array_equal(out[250, 205], [0, 0, 0])
        # 水平切边 y=200（缺口上边界，保留区在上）：黑 [190,200)
        np.testing.assert_array_equal(out[195, 100], [0, 0, 0])
        # 缺口区（x<200 且 y>200）保持原样
        np.testing.assert_array_equal(out[250, 100], [255, 255, 255])

    def test_input_not_mutated(self):
        canvas = self._canvas()
        before = canvas.copy()
        patch_lshape_cut_layers(canvas, 'tr', 200, 0, 200, 100, self.LAYERS)
        np.testing.assert_array_equal(canvas, before)

    def test_bad_geometry_raises(self):
        canvas = self._canvas()
        with pytest.raises(ValueError):
            patch_lshape_cut_layers(canvas, 'tr', 50, 50, 200, 100, self.LAYERS)
        with pytest.raises(ValueError):
            patch_lshape_cut_layers(canvas, 'tr', 200, 0, 200, 100, [])

    def test_inner_layers_fill_full_height(self):
        """垂直边递减：外层延伸到画布顶端，内层从 offs[k] 起不到顶端。

        与水平方向对称：
          水平: 外层延伸到画布右端 W，内层 W-offs[k] 不到右端
          垂直: 外层延伸到画布顶端 0，内层 offs[k] 不到顶端
        特殊情况：offs[k] >= yc 时 y_lo 回退到 0，保证挖角很小时内层不消失。
        """
        canvas = self._canvas()
        out = patch_lshape_cut_layers(canvas, 'tr', 200, 0, 200, 100, self.LAYERS)
        # LAYERS = [黑10, 棕20, 灰5], offs=[0,10,30,35]
        # flip 后 yc=100 > offs 全部 → 递减生效
        # 棕层 k=1 offs=10: y_lo=10, y_hi=100 → y=5 处无棕，y=15 处有棕
        np.testing.assert_array_equal(out[5, 180], [255, 255, 255])
        np.testing.assert_array_equal(out[15, 180], [120, 70, 40])


# ---------------------------------------------------------------------------
# 3. apply_lshape_border_completion 自动路由集成
# ---------------------------------------------------------------------------

class TestCompletionRouting:

    @pytest.mark.parametrize('directional_scale', [False, True])
    @pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
    def test_luyi_cream_strip_is_not_repainted(self, directional_scale, corner):
        """路易花坊日志中的152px黑带+18px米色条，只沿切边补黑带。"""
        src = Image.new('RGB', (1600, 1200), (149, 132, 112))
        draw = ImageDraw.Draw(src)
        draw.rectangle((0, 0, 1599, 1199), outline=(0, 0, 0), width=152)
        draw.rectangle((152, 152, 1447, 1047), outline=(238, 226, 210), width=18)
        canvas = np.asarray(src).copy()
        assert apply_lshape_border_completion(
            canvas, src, RectShape(0, 0, 1600, 1200), corner, 400, 300,
            src_material_img=src, directional_scale=directional_scale)
        # 以右上角为基准采样，其他角位映射到对应位置。
        x = 1399 if corner in ('tr', 'br') else 200
        black_y = 400 if corner in ('tr', 'tl') else 799
        content_y = 461 if corner in ('tr', 'tl') else 738
        np.testing.assert_array_equal(canvas[black_y, x], (0, 0, 0))
        np.testing.assert_array_equal(canvas[content_y, x], (149, 132, 112))

    @pytest.mark.parametrize('directional_scale', [False, True])
    @pytest.mark.parametrize('bg_color', [(235, 226, 209), (158, 115, 81)])
    @pytest.mark.parametrize('edge,size', [(60, (1000, 800)), (220, (2400, 2000))])
    def test_two_layer_band_keeps_material_color_across_panel_backgrounds(
            self, directional_scale, bg_color, edge, size):
        """两面板采样底色不同，也不能把黑边后的棕带重绘成花纹米色。"""
        w, h = size
        src = Image.new('RGB', size, (235, 226, 209))
        draw = ImageDraw.Draw(src)
        draw.rectangle((0, 0, w-1, h-1), outline=(0, 0, 0), width=edge)
        draw.rectangle((edge, edge, w-edge-1, h-edge-1),
                       outline=(158, 115, 81), width=40)
        layers = detect_border_profile(src)
        assert len(layers) == 2
        canvas = np.asarray(src).copy()
        assert apply_lshape_border_completion(
            canvas, src, RectShape(0, 0, w, h), 'tr', 350, 300,
            src_material_img=src, bg_color=bg_color,
            cuts=[('tr', 350, 300), ('tl', 350, 300)],
            directional_scale=directional_scale,
        )
        for x in (300, w-300):
            np.testing.assert_array_equal(canvas[320, x], (0, 0, 0))
            np.testing.assert_array_equal(canvas[300+edge+20, x], (158, 115, 81))

    def _canvas(self, w=800, h=600):
        return np.full((h, w, 3), 255, dtype=np.uint8)

    def test_manshenghua_completion_draws_three_layers(self):
        """蔓生花素材 → Profile 路径命中，切边出现 [描边, 边距, 细线] 3 层（点带不补）。"""
        canvas = self._canvas()
        src = _make_manshenghua_material(size=(800, 600))
        ok = apply_lshape_border_completion(
            canvas, src, RectShape(x=0, y=0, w=800, h=600),
            'tr', 200.0, 100.0,
            src_material_img=src, scale_x=1.0, scale_y=1.0,
        )
        assert ok is True
        # 层序（外→内，画布像素）：黑描边8 [592,600)，米边距60 [532,592)，
        # 细线3 [529,532)。总厚 71。
        # 垂直边内层 y 自自身深度起（offs=[0,8,68,71]），取 y=70 探全部层
        np.testing.assert_array_equal(canvas[70, 596][:], [20, 18, 16])     # 黑描边
        np.testing.assert_array_equal(canvas[70, 560][:], [243, 236, 220])  # 米边距
        np.testing.assert_array_equal(canvas[70, 530][:], [70, 60, 50])     # 细线
        # 点带（canvas[70, 520]）/ 内细线（canvas[70, 500]）不补画 → 白色
        np.testing.assert_array_equal(canvas[70, 520], [255, 255, 255])
        np.testing.assert_array_equal(canvas[70, 500], [255, 255, 255])
        # 水平切边 y=100：黑 [100,108)，米 [108,168)，细线 [168,171)
        np.testing.assert_array_equal(canvas[105, 700][:], [20, 18, 16])
        np.testing.assert_array_equal(canvas[130, 700][:], [243, 236, 220])
        np.testing.assert_array_equal(canvas[169, 700][:], [70, 60, 50])
        np.testing.assert_array_equal(canvas[175, 700], [255, 255, 255])
        # 缺口区保持白色
        np.testing.assert_array_equal(canvas[50, 700], [255, 255, 255])

    def test_zhuangyuanmiji_thick_band_completion(self):
        """庄园秘境素材 → 厚黑带让位 V13 失败 → Profile 接管；带厚与素材一致。"""
        canvas = self._canvas(800, 610)
        src = _make_zhuangyuanmiji_material()
        ok = apply_lshape_border_completion(
            canvas, src, RectShape(x=0, y=0, w=800, h=610),
            'tr', 200.0, 150.0,
            src_material_img=src, scale_x=1.0, scale_y=1.0,
            bg_color=(254, 248, 234),
        )
        assert ok is True
        # 探测：cut xc=600，yc=150；垂直边层厚按几何均值（=1.0，scale_x=scale_y=1.0）
        # 粗黑带 ~60 + 米底 ~37 = 总厚 97。垂直边补画 x[600-97, 600) = [503,600)。
        # 黑带 [540,600)、米底 [503,540)。
        # 取 y=100（yc 之内）观察各层
        assert canvas[100, 595].max() <= 30                                 # 黑带
        assert _color_close(tuple(int(v) for v in canvas[100, 545]),
                            (0, 0, 0))                                     # 黑带内缘
        assert _color_close(tuple(int(v) for v in canvas[100, 520]),
                            (254, 248, 234))                               # 米底
        np.testing.assert_array_equal(canvas[100, 500], [255, 255, 255])   # 不超出总厚
        # 水平切边 y=150：黑 [150, 210)，米 [210, 247)
        assert canvas[180, 700].max() <= 30
        assert _color_close(tuple(int(v) for v in canvas[230, 700]),
                            (254, 248, 234))

    def test_keluo_still_completed(self):
        """克罗印花素材 → 新路由下仍正常补边（不回归）。"""
        canvas = self._canvas()
        src = _make_keluo_material()
        ok = apply_lshape_border_completion(
            canvas, src.resize((800, 600)),
            RectShape(x=0, y=0, w=800, h=600),
            'tr', 200.0, 100.0,
            src_material_img=src, scale_x=2.0, scale_y=2.0,
        )
        assert ok is True
        # 黑描边 6px×2=12：垂直边 [588,600)；棕色带 40px×2=80：[508,588)
        assert canvas[50, 595].max() <= 60
        assert _is_brownish(tuple(int(v) for v in canvas[50, 550]))
        # 水平切边：黑 [100,112)
        assert canvas[105, 700].max() <= 60

    def test_plain_material_returns_false_untouched(self):
        """纯色素材 → 全路径未命中 → False 且画布零修改（向后兼容契约）。"""
        canvas = self._canvas()
        before = canvas.copy()
        ok = apply_lshape_border_completion(
            canvas, Image.new('RGB', (800, 600), (255, 255, 255)),
            RectShape(x=0, y=0, w=800, h=600),
            'tr', 200.0, 100.0,
        )
        assert ok is False
        np.testing.assert_array_equal(canvas, before)


# ---------------------------------------------------------------------------
# 工具断言
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('directional', [False, True])
def test_paisi_split_jpeg_stroke_keeps_beige_cut_border(directional):
    """压缩过渡不能把佩斯的细描边拆成独立框线，导致补边全失败。"""
    a = np.full((800, 1000, 3), (240, 230, 210), dtype=np.uint8)
    a[:153] = a[-153:] = (228, 214, 187)
    a[:, :157] = a[:, -157:] = (228, 214, 187)
    a[:13] = a[-13:] = 0
    a[:, :17] = a[:, -17:] = 0
    a[:2] = a[-2:] = (44, 41, 38)
    a[:, :2] = a[:, -2:] = (46, 46, 46)
    a[:, 2:4] = a[:, -4:-2] = (16, 16, 16)
    # 顶边黑段自身也被 JPEG 拆成两个近黑段。
    a[3:8, 17:-17] = (1, 0, 14)
    a[-8:-3, 17:-17] = (1, 0, 14)
    src = Image.fromarray(a)
    canvas = np.full_like(a, 255)
    assert apply_lshape_border_completion(
        canvas, src, RectShape(x=0, y=0, w=1000, h=800),
        'tr', 250, 200, src_material_img=src,
        scale_x=1, scale_y=1, directional_scale=directional,
    )
    np.testing.assert_array_equal(canvas[240, 900], (228, 214, 187))
    np.testing.assert_array_equal(canvas[100, 900], (255, 255, 255))


@pytest.mark.parametrize('corner', ['tr', 'tl', 'bl', 'br'])
@pytest.mark.parametrize('sx,sy', [(1, 1), (1.5, 0.75)])
@pytest.mark.parametrize('soft_edge', [False, True])
@pytest.mark.parametrize('directional', [False, True])
def test_composite_v13_stroke_matches_each_material_axis(corner, sx, sy, soft_edge, directional):
    src = Image.new('RGB', (1000, 800), (240, 230, 210))
    a = np.array(src)
    a[:153] = a[-153:] = (228, 214, 187)
    a[:, :157] = a[:, -157:] = (228, 214, 187)
    a[:13] = a[-13:] = 0
    a[:, :17] = a[:, -17:] = 0
    src = Image.fromarray(a)
    if soft_edge:
        # 模拟缩放后保留下来的灰色内缘；补画不能把它扩成纯黑。
        a[11:13] = a[-13:-11] = (60, 60, 60)
        a[:, 15:17] = a[:, -17:-15] = (70, 70, 70)
        canvas_source = Image.fromarray(a)
    else:
        canvas_source = src
    a = np.array(canvas_source.resize((round(1000*sx), round(800*sy)), Image.Resampling.NEAREST))
    canvas = a.copy()
    xc, yc = round(750*sx), round(200*sy)
    canvas[:yc, xc:] = 255
    if corner in ('tl', 'bl'):
        canvas = np.fliplr(canvas).copy()
    if corner in ('bl', 'br'):
        canvas = np.flipud(canvas).copy()
    assert apply_lshape_border_completion(
        canvas, src, RectShape(0, 0, a.shape[1], a.shape[0]), corner,
        round(250*sx), yc, src_material_img=src,
        scale_x=sx, scale_y=sy, directional_scale=directional)
    if corner in ('bl', 'br'):
        canvas = np.flipud(canvas)
    if corner in ('tl', 'bl'):
        canvas = np.fliplr(canvas)
    # 水平补画应与原顶边同为13px，竖向补画应与原右边同为17px。
    ex = np.count_nonzero(a[round(400*sy), -round(30*sx):].max(axis=1) < 90)
    ey = np.count_nonzero(a[:round(30*sy), round(500*sx)].max(axis=1) < 90)
    assert np.count_nonzero(canvas[yc:yc+round(30*sy), round(900*sx)].max(axis=1) < 90) == ey
    assert np.count_nonzero(canvas[round(100*sy), xc-round(30*sx):xc].max(axis=1) < 90) == ex
    np.testing.assert_array_equal(canvas[yc:yc+ey, round(900*sx)],
                                  a[:ey, round(500*sx)])
    np.testing.assert_array_equal(canvas[round(100*sy), xc-ex:xc],
                                  a[round(400*sy), -ex:])
    # 内凹接头应接续整条水平描边的过渡，不能留下实色黑块。
    np.testing.assert_array_equal(canvas[yc:yc+ey, xc-1],
                                  canvas[yc:yc+ey, round(900*sx)])
    assert np.all(canvas[:yc, xc:] == 255)
    # 色带不能压窄接缝处保留下来的原描边。
    np.testing.assert_array_equal(canvas[:ey, round(720*sx)], a[:ey, round(720*sx)])
    np.testing.assert_array_equal(canvas[round(240*sy), -ex:], a[round(240*sy), -ex:])

def _color_close(c1, c2, tol=20.0) -> bool:
    return float(np.linalg.norm(
        np.array(c1, dtype=np.float64) - np.array(c2, dtype=np.float64))) < tol


@pytest.mark.parametrize('corner', ['tr', 'tl', 'bl', 'br'])
@pytest.mark.parametrize('colors', [
    [(0, 0, 0)],
    [(80, 30, 20), (170, 120, 70)],
    [(110, 70, 35), (200, 150, 90)],
    [(0, 0, 0), (243, 236, 220), (70, 60, 50)],
])
@pytest.mark.parametrize('route', ['profile', 'legacy'])
def test_all_profile_border_styles_align_stroke_and_corners(corner, colors, route):
    from core.lshape_border_route import _apply_profile_path
    a = np.full((600, 800, 3), (220, 200, 170), dtype=np.uint8)
    a[:10] = a[-10:] = colors[0]
    a[:, :14] = a[:, -14:] = colors[0]
    a[:100, 600:] = 255
    if corner in ('tl', 'bl'):
        a = np.fliplr(a).copy()
    if corner in ('bl', 'br'):
        a = np.flipud(a).copy()
    before = a.copy()
    layers = [(c, t) for c, t in zip(colors, [12, 40, 3])]
    if route == 'profile':
        assert _apply_profile_path(
            canvas_arr=a, outer_rect=RectShape(0, 0, 800, 600),
            cut_corner=corner, cut_w_px=200, cut_h_px=100,
            layers_src=layers, scale_x=1, scale_y=1)
    else:
        from core.lshape_border import _draw_lshape_layers_on_retained_side
        assert _draw_lshape_layers_on_retained_side(
            a, RectShape(0, 0, 800, 600), corner, 200, 100, layers)
    if corner in ('bl', 'br'):
        a = np.flipud(a)
        before = np.flipud(before)
    if corner in ('tl', 'bl'):
        a = np.fliplr(a)
        before = np.fliplr(before)
    np.testing.assert_array_equal(a[100:110, 700], before[:10, 400])
    assert tuple(a[110, 700]) != colors[0]
    np.testing.assert_array_equal(a[50, 586:600], before[400, -14:])
    assert tuple(a[50, 585]) != colors[0]
    assert np.all(a[:100, 600:] == 255)


@pytest.mark.parametrize('corners', [('tl', 'tr'), ('bl', 'br'), ('tl', 'tr', 'bl', 'br')])
@pytest.mark.parametrize('layers', [
    [((0, 0, 0), 12)],
    [((110, 70, 35), 12), ((200, 150, 90), 40)],
    [((0, 0, 0), 12), ((243, 236, 220), 40), ((70, 60, 50), 3)],
])
def test_shared_alignment_multicut_preserves_every_corner(monkeypatch, corners, layers):
    import core.lshape_border as border
    monkeypatch.setattr(border, '_detect_lshape_border_auto',
                        lambda image: (layers, None, False, False))
    a = np.full((600, 800, 3), (220, 200, 170), dtype=np.uint8)
    a[:10] = a[-10:] = layers[0][0]
    a[:, :14] = a[:, -14:] = layers[0][0]
    src = Image.fromarray(a)
    for c in corners:
        xs = slice(0, 200) if c in ('tl', 'bl') else slice(600, 800)
        ys = slice(0, 100) if c in ('tl', 'tr') else slice(500, 600)
        a[ys, xs] = 255
    assert border.apply_lshape_border_completion(
        a, src, RectShape(0, 0, 800, 600), corners[0], 200, 100,
        src_material_img=src, cuts=[(c, 200, 100) for c in corners])
    for c in corners:
        normalized = np.flipud(a) if c in ('bl', 'br') else a
        if c in ('tl', 'bl'):
            normalized = np.fliplr(normalized)
        assert np.all(normalized[:100, 600:] == 255)
        assert np.all(normalized[100:110, 700] == layers[0][0])
        assert tuple(normalized[110, 700]) != layers[0][0]
        assert np.all(normalized[50, 586:600] == layers[0][0])
        assert tuple(normalized[50, 585]) != layers[0][0]


@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
@pytest.mark.parametrize('directional', [False, True])
@pytest.mark.parametrize('case', ['consistent', 'missing_boundary', 'unequal_width'])
def test_dotted_inner_decoration_keeps_plain_outer_band(corner, directional, case):
    src = Image.new('RGB', (800, 600), (242, 234, 211))
    d = ImageDraw.Draw(src)
    d.rectangle((0, 0, 799, 599), outline=(0, 0, 0), width=8)
    for x in (240, 400, 560):
        d.rectangle((x-4, 68, x+4, 79), fill=(0, 0, 0))
        d.rectangle((x-4, 520, x+4, 531), fill=(0, 0, 0))
    for y in (180, 300):
        d.rectangle((68, y-4, 79, y+4), fill=(0, 0, 0))
    for y in (300, 420):
        d.rectangle((720, y-4, 731, y+4), fill=(0, 0, 0))
    if case == 'missing_boundary':
        d.rectangle((68, 8, 79, 591), fill=(242, 234, 211))
        d.rectangle((720, 8, 731, 591), fill=(242, 234, 211))
    elif case == 'unequal_width':
        d.rectangle((720, 8, 731, 591), fill=(242, 234, 211))
        for y in (300, 420):
            d.rectangle((700, y-4, 711, y+4), fill=(0, 0, 0))
    layers = detect_border_profile(src)
    if case != 'consistent':
        assert layers is None
        return
    assert layers is not None and len(layers) == 2
    assert layers[1][0] == (242, 234, 211)
    assert abs(layers[1][1]-60) <= 2
    a = np.full((600, 800, 3), 255, dtype=np.uint8)
    assert apply_lshape_border_completion(
        a, src, RectShape(0, 0, 800, 600), corner, 200, 120,
        src_material_img=src, directional_scale=directional)
    if corner in ('bl', 'br'):
        a = np.flipud(a)
    if corner in ('tl', 'bl'):
        a = np.fliplr(a)
    assert np.all(a[50, 598] == 0)
    np.testing.assert_array_equal(a[50, 570], (242, 234, 211))
    np.testing.assert_array_equal(a[50, 520], (255, 255, 255))


def _is_brownish(c) -> bool:
    r, g, b = int(c[0]), int(c[1]), int(c[2])
    return 90 <= r <= 160 and 45 <= g <= 100 and 15 <= b <= 70


def _make_bordered(border: int, size=(400, 300)) -> Image.Image:
    img = Image.new('RGB', size, (255, 255, 255))
    ImageDraw.Draw(img).rectangle(
        [0, 0, size[0] - 1, size[1] - 1], outline=(10, 10, 10), width=border)
    return img
