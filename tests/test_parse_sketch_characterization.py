"""
特征化测试：parse_sketch 7步法主路径（characterization / golden test）。

历史背景：本文件早期版本锁定的是已删除的 `_geometry_driven_parse` 主路径与
“经典回退”路径；解析架构已重构为单一 `_7step_parse`（7step_v7），旧断言
（method == "geometry_driven" / monkeypatch _geometry_driven_parse）全部失效。

现行设计：
  - 用 PIL 合成“双嵌套黑色矩形”草图（无文字）——cv2 矩形检测（Step 1/2）
    在合成干净图上是确定性的，已实测可用。
  - 7步法 Step 3 起依赖 OCR 数值。为保证测试确定性、不依赖本机 Tesseract
    二进制，将两个 OCR 入口 monkeypatch 为“罐头”识别结果：
      * sp._multi_scale_ocr_scan        → [(value, conf, bbox), ...]
      * sp._extract_direction_label_numbers → {field: (value, conf, bbox), ...}
    bbox 的中心点被精确放置在 `_divide_8_zones` 划分的对应语义区域内，
    从而端到端锁定 Step 3~7（空间映射 / 赋值 / 校验 / 自洽评分）的行为。

覆盖：宽版 target 模式、竖版 target 模式（防横竖颠倒）、无 target 自动识别
（Step 5.5 外框候选枚举）、方向标签锁定优先级（Step 4）。
"""
import os

import pytest
from PIL import Image, ImageDraw

from core.pool_designer import parse_sketch
import core.pool_designer.sketch_parser as sp


# ---------------------------------------------------------------------------
# 夹具：合成草图 + 清空模块缓存（避免跨用例污染）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_sketch_caches():
    """每个用例前清空 parse_sketch 的两份模块级缓存。"""
    with sp._SKETCH_CACHE_LOCK:
        sp._SKETCH_CACHE.clear()
    with sp._SKETCH_CONSISTENT_CACHE_LOCK:
        sp._SKETCH_CONSISTENT_CACHE.clear()
    yield


def _draw_nested(path: str, canvas, outer, inner, lw=3):
    img = Image.new("RGB", canvas, "white")
    d = ImageDraw.Draw(img)
    d.rectangle(outer, outline="black", width=lw)
    d.rectangle(inner, outline="black", width=lw)
    img.save(path)


def _box(cx, cy, w=20, h=16):
    """以 (cx, cy) 为中心构造 OCR bbox (x, y, w, h)。"""
    return (int(cx - w / 2), int(cy - h / 2), w, h)


def _fake_ocr_scan_factory(ocr_items):
    """返回可替换 sp._multi_scale_ocr_scan 的伪函数。

    ocr_items: [(value, (cx, cy)), ...] —— 每个数值及其 bbox 中心。
    """
    def _fake(cv2, tesseract, region_img, fast_mode=False, enhanced_gray=None, **kwargs):
        return [(v, 95.0, _box(cx, cy)) for v, (cx, cy) in ocr_items]
    return _fake


def _no_direction_labels(cv2, tesseract, gray_img, enhanced_gray=None, **kwargs):
    """默认无方向标签锁定。"""
    return {}


# ---------------------------------------------------------------------------
# 宽版草图（target 模式）
# ---------------------------------------------------------------------------

class TestWideSketch:
    """画布 1000×500；外框 (50,50)-(950,450)；内框 (150,100)-(850,400)。

    target 90×40cm。罐头 OCR 值及语义区域放置（依 _divide_8_zones）：
      outer_w=90（外框正下方）  outer_h=40（外框左侧）
      inner_w=70（内框下部）    inner_h=30（内框左部）
      上=5 下=5（内框上/下方）  左=10 右=10（内框左/右侧）
    完全自洽：90=70+10+10，40=30+5+5。
    """

    OCR_ITEMS = [
        (90.0, (500, 480)),   # outer_w zone：cy>450, 50<=cx<=950
        (40.0, (20, 250)),    # outer_h zone：cx<50, 50<=cy<=450
        (70.0, (400, 320)),   # inner_w zone：内框内 cy>250
        (30.0, (300, 200)),   # inner_h zone：内框内 cy<250 且 cx<500
        (5.0, (500, 75)),     # margin_top zone：cy<100, 150<=cx<=850
        (5.0, (500, 425)),    # margin_bottom zone：cy>400, 150<=cx<=850
        (10.0, (100, 250)),   # margin_left zone：50<=cx<150, 100<=cy<=400
        (10.0, (900, 250)),   # margin_right zone：850<cx<=950, 100<=cy<=400
    ]

    def test_7step_target_mode(self, tmp_path, monkeypatch):
        p = str(tmp_path / "wide.png")
        _draw_nested(p, (1000, 500), (50, 50, 950, 450), (150, 100, 850, 400))

        monkeypatch.setattr(sp, "_multi_scale_ocr_scan", _fake_ocr_scan_factory(self.OCR_ITEMS))
        monkeypatch.setattr(sp, "_extract_direction_label_numbers", _no_direction_labels)

        r = parse_sketch(p, target_outer_w_cm=90.0, target_outer_h_cm=40.0)

        # 主路径成功，且走的是 7步法
        assert r.success is True, f"message={r.message!r}"
        assert r.method.startswith("7step_v7"), f"method={r.method!r}"

        # 外框 = target（主源）
        assert r.outer_w_cm == pytest.approx(90.0, abs=0.5)
        assert r.outer_h_cm == pytest.approx(40.0, abs=0.5)

        # 内框 = OCR 空间映射值
        assert r.inner_w_cm == pytest.approx(70.0, abs=1.0)
        assert r.inner_h_cm == pytest.approx(30.0, abs=1.0)

        # 边距（宽版：上下5 / 左右10，不可与竖版混淆）
        assert r.margin_top_cm == pytest.approx(5.0, abs=1.0)
        assert r.margin_bottom_cm == pytest.approx(5.0, abs=1.0)
        assert r.margin_left_cm == pytest.approx(10.0, abs=1.0)
        assert r.margin_right_cm == pytest.approx(10.0, abs=1.0)

        # 结构不变量：外框 = 内框 + 边距和（几何自洽）
        assert abs(r.outer_w_cm - r.inner_w_cm - r.margin_left_cm - r.margin_right_cm) < 1.5
        assert abs(r.outer_h_cm - r.inner_h_cm - r.margin_top_cm - r.margin_bottom_cm) < 1.5

        # 完全自洽赋值 → 高自洽评分
        assert r.debug.get("self_consistency", 0) >= 0.95


# ---------------------------------------------------------------------------
# 竖版草图（方向处理保护）
# ---------------------------------------------------------------------------

class TestTallSketch:
    """画布 500×1000；外框 (50,50)-(450,950)；内框 (100,150)-(400,850)。

    target 40×90cm。竖版用以防止宽高/方向被错误交换（历史横竖颠倒 bug）。
    罐头值：outer 40×90，inner 30×70，边距上下=10、左右=5。
    """

    OCR_ITEMS = [
        (40.0, (250, 980)),   # outer_w zone：cy>950, 50<=cx<=450
        (90.0, (20, 500)),    # outer_h zone：cx<50, 50<=cy<=950
        (30.0, (250, 600)),   # inner_w zone：内框内 cy>500
        (70.0, (200, 350)),   # inner_h zone：内框内 cy<500 且 cx<250
        (10.0, (250, 125)),   # margin_top zone：cy<150, 100<=cx<=400
        (10.0, (250, 900)),   # margin_bottom zone：cy>850, 100<=cx<=400
        (5.0, (75, 500)),     # margin_left zone：50<=cx<100, 150<=cy<=850
        (5.0, (425, 500)),    # margin_right zone：400<cx<=450, 150<=cy<=850
    ]

    def test_7step_target_mode_tall(self, tmp_path, monkeypatch):
        p = str(tmp_path / "tall.png")
        _draw_nested(p, (500, 1000), (50, 50, 450, 950), (100, 150, 400, 850))

        monkeypatch.setattr(sp, "_multi_scale_ocr_scan", _fake_ocr_scan_factory(self.OCR_ITEMS))
        monkeypatch.setattr(sp, "_extract_direction_label_numbers", _no_direction_labels)

        r = parse_sketch(p, target_outer_w_cm=40.0, target_outer_h_cm=90.0)

        assert r.success is True, f"message={r.message!r}"
        assert r.method.startswith("7step_v7"), f"method={r.method!r}"

        # 外框方向不能被颠倒：宽=40 高=90
        assert r.outer_w_cm == pytest.approx(40.0, abs=0.5)
        assert r.outer_h_cm == pytest.approx(90.0, abs=0.5)

        # 内框 30×70（宽<高）
        assert r.inner_w_cm == pytest.approx(30.0, abs=1.0)
        assert r.inner_h_cm == pytest.approx(70.0, abs=1.0)

        # 边距：上下=10，左右=5（不可与宽版混淆）
        assert r.margin_top_cm == pytest.approx(10.0, abs=1.0)
        assert r.margin_bottom_cm == pytest.approx(10.0, abs=1.0)
        assert r.margin_left_cm == pytest.approx(5.0, abs=1.0)
        assert r.margin_right_cm == pytest.approx(5.0, abs=1.0)

        # 结构不变量
        assert abs(r.outer_w_cm - r.inner_w_cm - r.margin_left_cm - r.margin_right_cm) < 1.5
        assert abs(r.outer_h_cm - r.inner_h_cm - r.margin_top_cm - r.margin_bottom_cm) < 1.5


# ---------------------------------------------------------------------------
# 无 target 自动识别（Step 5.5 外框候选枚举选优）
# ---------------------------------------------------------------------------

class TestAutoDetectNoTarget:
    """不传 target：外框尺寸必须从 OCR 候选池枚举选出。

    候选池含 90/70/40/30（20~600 范围）。Step 5.5 枚举两两组合，正确的
    90×40 组合在自洽分、像素/厘米比例匹配、圆整偏好、桶匹配四个维度上
    均最优，应被稳定选中；Step 6.5 比例校验不应误触发宽高交换。
    """

    def test_auto_detect_picks_correct_outer(self, tmp_path, monkeypatch):
        p = str(tmp_path / "wide.png")
        _draw_nested(p, (1000, 500), (50, 50, 950, 450), (150, 100, 850, 400))

        monkeypatch.setattr(sp, "_multi_scale_ocr_scan",
                            _fake_ocr_scan_factory(TestWideSketch.OCR_ITEMS))
        monkeypatch.setattr(sp, "_extract_direction_label_numbers", _no_direction_labels)

        r = parse_sketch(p)  # 无 target

        assert r.success is True, f"message={r.message!r}"
        assert r.method.startswith("7step_v7"), f"method={r.method!r}"

        # 枚举必须选出 90×40（而不是 70×40 / 90×30 等错误组合）
        assert r.outer_w_cm == pytest.approx(90.0, abs=1.5)
        assert r.outer_h_cm == pytest.approx(40.0, abs=1.5)

        # 方向未被错误交换：宽>高
        assert r.outer_w_cm > r.outer_h_cm

        assert r.inner_w_cm == pytest.approx(70.0, abs=1.0)
        assert r.inner_h_cm == pytest.approx(30.0, abs=1.0)
        assert r.margin_top_cm == pytest.approx(5.0, abs=1.0)
        assert r.margin_bottom_cm == pytest.approx(5.0, abs=1.0)
        assert r.margin_left_cm == pytest.approx(10.0, abs=1.0)
        assert r.margin_right_cm == pytest.approx(10.0, abs=1.0)

        # 结构不变量
        assert abs(r.outer_w_cm - r.inner_w_cm - r.margin_left_cm - r.margin_right_cm) < 1.5
        assert abs(r.outer_h_cm - r.inner_h_cm - r.margin_top_cm - r.margin_bottom_cm) < 1.5


# ---------------------------------------------------------------------------
# 方向标签锁定优先级（Step 4）
# ---------------------------------------------------------------------------

class TestDirectionLabelLock:
    """方向标签锁定的边距必须压过全局 OCR 空间映射的同位值。

    全局 OCR 在四个边距区放“错误”值（8/8/12/12），方向标签给出正确值
    （5/5/10/10）。Step 4 锁定后，错误值被排除出候选，最终边距必须取
    标签值——这是“方向标签优先锁定”路径的行为锁定。
    """

    def test_direction_labels_override_spatial_candidates(self, tmp_path, monkeypatch):
        p = str(tmp_path / "wide.png")
        _draw_nested(p, (1000, 500), (50, 50, 950, 450), (150, 100, 850, 400))

        # 全局 OCR：边距区故意放错误值
        ocr_items = [
            (90.0, (500, 480)),   # outer_w
            (40.0, (20, 250)),    # outer_h
            (70.0, (400, 320)),   # inner_w
            (30.0, (300, 200)),   # inner_h
            (8.0, (500, 75)),     # margin_top（错误，应被标签覆盖）
            (8.0, (500, 425)),    # margin_bottom（错误）
            (12.0, (100, 250)),   # margin_left（错误）
            (12.0, (900, 250)),   # margin_right（错误）
        ]
        monkeypatch.setattr(sp, "_multi_scale_ocr_scan", _fake_ocr_scan_factory(ocr_items))

        # 方向标签锁定正确边距：{field: (value, conf, bbox)}
        locked = {
            "margin_top": (5.0, 96.0, _box(500, 75)),
            "margin_bottom": (5.0, 96.0, _box(500, 425)),
            "margin_left": (10.0, 96.0, _box(100, 250)),
            "margin_right": (10.0, 96.0, _box(900, 250)),
        }
        monkeypatch.setattr(
            sp, "_extract_direction_label_numbers",
            lambda cv2, tesseract, gray_img, enhanced_gray=None, **kwargs: locked)

        r = parse_sketch(p, target_outer_w_cm=90.0, target_outer_h_cm=40.0)

        assert r.success is True, f"message={r.message!r}"

        # 边距必须来自方向标签（正确值），而非被排除的 OCR 候选（错误值）
        assert r.margin_top_cm == pytest.approx(5.0, abs=0.5)
        assert r.margin_bottom_cm == pytest.approx(5.0, abs=0.5)
        assert r.margin_left_cm == pytest.approx(10.0, abs=0.5)
        assert r.margin_right_cm == pytest.approx(10.0, abs=0.5)

        # 内框不受影响
        assert r.inner_w_cm == pytest.approx(70.0, abs=1.0)
        assert r.inner_h_cm == pytest.approx(30.0, abs=1.0)

        # debug 信息应暴露方向标签结果
        dm = r.debug.get("direction_margins", {})
        assert set(dm.keys()) == {"margin_top", "margin_bottom", "margin_left", "margin_right"}
