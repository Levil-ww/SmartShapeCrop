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
)


# ---------------------------------------------------------------------------
# 合成素材夹具
# ---------------------------------------------------------------------------

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
        """内凹角 (200,100)：d=max(dx,dy) 分层，边界深度归属外层（与条带一致）。"""
        canvas = self._canvas()
        out = patch_lshape_cut_layers(canvas, 'tr', 200, 0, 200, 100, self.LAYERS)
        np.testing.assert_array_equal(out[105, 195], [0, 0, 0])      # d=5 → 黑
        np.testing.assert_array_equal(out[115, 190], [120, 70, 40])  # d=15 → 棕
        np.testing.assert_array_equal(out[130, 190], [120, 70, 40])  # d=30 → 棕(边界)
        np.testing.assert_array_equal(out[132, 188], [70, 60, 50])   # d=32 → 深灰
        np.testing.assert_array_equal(out[140, 170], [255, 255, 255])  # d>35 → 不动

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

def _color_close(c1, c2, tol=20.0) -> bool:
    return float(np.linalg.norm(
        np.array(c1, dtype=np.float64) - np.array(c2, dtype=np.float64))) < tol


def _is_brownish(c) -> bool:
    r, g, b = int(c[0]), int(c[1]), int(c[2])
    return 90 <= r <= 160 and 45 <= g <= 100 and 15 <= b <= 70


def _make_bordered(border: int, size=(400, 300)) -> Image.Image:
    img = Image.new('RGB', size, (255, 255, 255))
    ImageDraw.Draw(img).rectangle(
        [0, 0, size[0] - 1, size[1] - 1], outline=(10, 10, 10), width=border)
    return img
