"""阶段3识别精度单元测试 —— 综合形状 OCR 十类角色映射 + RETR_CCOMP 内轮廓策略。

OCR 隔离约定（报告 §OCR 测试隔离）：只 patch 模块级 `_safe_import_tesseract` /
`_multi_scale_ocr_scan`，不碰真实 Tesseract；所有用例在无 OCR 环境下确定性运行。
合成草图用 PIL 绘制（白底黑笔迹），几何真值按 5 px/cm 精确布局。
"""
from __future__ import annotations

import pytest

from services.sketch_parser import composite_sketch_parser as composite
from services.sketch_parser import lshape_sketch_parser as lshape_mod

# ── 合成草图布局（5 px/cm，外框 185×88 cm，画布 1325×730）────────────
# 外框 bbox (200,150,925,440)；中心洞 (425,200,400,300) → 80×60 cm；
# tr 挖角 35×10 cm（实心黑角块 175×50，与外框笔迹求并集形成凹口）；
# 干扰矩形 (900,520,100,60)：严格在外框内、面积占比 ~1.5%，洞提取应取其大者。
OUTER = (200, 150, 925, 440)
HOLE = (425, 200, 400, 300)
DECOY = (900, 520, 100, 60)
TARGET_W_CM, TARGET_H_CM = 185.0, 88.0
# 裁剪区 band = int(min(max(30, 0.12*925), 150)) = 111 → 区域左上角 (89,39)；
# fake OCR 的 bbox 是相对裁剪区的，需把全图探针坐标减去该偏移。
CROP_X0, CROP_Y0 = 89, 39

# OCR 角色探针（全图坐标，bbox 30×20，中心点落在目标角色区）
PROBE_BBOX = {
    "margin_left": (285, 340, 30, 20),
    "hole_w": (585, 390, 30, 20),
    "margin_right": (985, 340, 30, 20),
    "margin_top": (585, 160, 30, 20),
    "hole_h": (485, 290, 30, 20),
    "margin_bottom": (585, 540, 30, 20),
}
# 通过两等式闸口的"标注值"（宽轴 Σ=185.0，高轴 Σ=88.0）
OCR_GOOD = {
    "margin_left": 44.6, "hole_w": 81.0, "margin_right": 59.4,
    "margin_top": 10.2, "hole_h": 60.5, "margin_bottom": 17.3,
}


def _draw_sketch(path):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1325, 730), "white")
    d = ImageDraw.Draw(img)
    ox, oy, ow, oh = OUTER
    d.rectangle([ox, oy, ox + ow - 1, oy + oh - 1], outline="black", width=3)
    hx, hy, hw, hh = HOLE
    d.rectangle([hx, hy, hx + hw - 1, hy + hh - 1], outline="black", width=3)
    # tr 挖角：擦掉外框右上角（35cm×10cm = 175×50 px），
    # 并补画凹口两条内边，使外轮廓成为闭合的 L 形环带
    # （只擦不补会让笔迹带断开，最大轮廓退化为细条带，挖角识别失效）
    d.rectangle([ox + ow - 175, oy, ox + ow - 1, oy + 49], fill="white")
    d.line([(ox + ow - 175, oy), (ox + ow - 175, oy + 50)], fill="black", width=3)
    d.line([(ox + ow - 175, oy + 50), (ox + ow - 1, oy + 50)], fill="black", width=3)
    dx, dy, dw, dh = DECOY
    d.rectangle([dx, dy, dx + dw - 1, dy + dh - 1], outline="black", width=3)
    img.save(path)
    return str(path)


@pytest.fixture
def sketch_path(tmp_path):
    return _draw_sketch(tmp_path / "composite.png")


@pytest.fixture(autouse=True)
def _isolate_lshape_ocr(monkeypatch):
    """L 形挖角委派也走 OCR：统一打空，避免真实 Tesseract 拖慢/干扰测试。"""
    monkeypatch.setattr(lshape_mod, "_multi_scale_ocr_scan", lambda *a, **k: [])


def _patch_tesseract_available(monkeypatch):
    monkeypatch.setattr(composite, "_safe_import_tesseract", lambda: object())


def _fake_scan(monkeypatch, field_values: dict):
    """按角色名给定标注值，生成裁剪区坐标系的 fake OCR 结果。"""
    hits = []
    for name, val in field_values.items():
        bx, by, bw, bh = PROBE_BBOX[name]
        hits.append((val, 95, (bx - CROP_X0, by - CROP_Y0, bw, bh)))

    def fake(cv2_mod, tess, region, fast_mode=False, enhanced_gray=None, check_cancel=None, **kw):
        return list(hits)

    monkeypatch.setattr(composite, "_multi_scale_ocr_scan", fake)


class TestHierarchyHoleExtraction:
    """RETR_CCOMP 层级策略：洞=严格在外框内的最大 0 级轮廓，干扰矩形不夺主。"""

    def test_hierarchy_wins_over_decoy(self, sketch_path, monkeypatch):
        monkeypatch.setattr(composite, "_safe_import_tesseract", lambda: None)
        r = composite.parse_composite_sketch(
            sketch_path, target_outer_w_cm=TARGET_W_CM, target_outer_h_cm=TARGET_H_CM)
        assert r.success
        assert r.debug["method"] == "hierarchy"
        assert r.hole_detected and r.hole_consumed
        # 洞取 400×300 而非干扰矩形 100×60 → 80×60 cm（输出 +1 损耗）
        assert r.hole_w_cm == pytest.approx(81.0, abs=0.51)
        assert r.hole_h_cm == pytest.approx(61.0, abs=0.51)
        assert r.margin_left_cm == pytest.approx(45.0, abs=0.2)
        assert r.margin_top_cm == pytest.approx(10.0, abs=0.2)
        assert r.margin_right_cm == pytest.approx(60.0, abs=0.2)
        assert r.margin_bottom_cm == pytest.approx(18.0, abs=0.2)

    def test_cuts_delegated_to_lshape(self, sketch_path, monkeypatch):
        monkeypatch.setattr(composite, "_safe_import_tesseract", lambda: None)
        r = composite.parse_composite_sketch(
            sketch_path, target_outer_w_cm=TARGET_W_CM, target_outer_h_cm=TARGET_H_CM)
        assert len(r.cuts_cm) == 1
        cut = r.cuts_cm[0]
        assert cut["corner"] == "tr"
        # 笔画草图凹口有 ±2px（~0.4cm）固有收缩，挖角精度属 L 形解析器自身口径
        assert cut["cut_w_cm"] == pytest.approx(35.0, abs=1.0)
        assert cut["cut_h_cm"] == pytest.approx(10.0, abs=1.0)

    def test_retr_list_fallback_when_hierarchy_misses_hole(self, sketch_path, monkeypatch):
        monkeypatch.setattr(composite, "_safe_import_tesseract", lambda: None)
        monkeypatch.setattr(
            composite, "_extract_outer_and_hole_hierarchy",
            lambda cv2, gray: (OUTER, None, "outer_only"))
        r = composite.parse_composite_sketch(
            sketch_path, target_outer_w_cm=TARGET_W_CM, target_outer_h_cm=TARGET_H_CM)
        assert r.success
        assert r.debug["method"] == "retr_list_fallback"
        assert r.hole_w_cm == pytest.approx(81.0, abs=0.51)


class TestCompositeZoneFunc:
    """区域判定函数：十类角色桶的空间归属（8 探针 + 界外 None）。"""

    def test_ten_role_probes(self):
        zone_of = composite._composite_zone_func(OUTER, HOLE)
        ox, oy, ow, oh = OUTER
        hx, hy, hw, hh = HOLE
        cases = [
            (hx + hw // 2, hy + hh - 10, "hole_w"),        # 洞内部下半
            (hx + 10, hy + hh // 2, "hole_h"),             # 洞内部左半
            (ox + 50, hy + hh // 2, "margin_left"),        # 左边距带
            (ox + ow - 50, hy + hh // 2, "margin_right"),  # 右边距带
            (hx + hw // 2, oy + 20, "margin_top"),         # 上边距带
            (hx + hw // 2, oy + oh - 20, "margin_bottom"),  # 下边距带
            (hx + hw // 2, oy + oh + 40, "outer_w"),       # 外框正下方
            (ox - 40, hy + hh // 2, "outer_h"),            # 外框正左侧
            (10, 10, None),                                # 画布角落 → 无角色
        ]
        for cx, cy, expected in cases:
            assert zone_of(cx, cy) == expected, f"({cx},{cy}) 应归 {expected}"


class TestOcrAxisGate:
    """整轴两等式闸口：OCR 全在且成立 → ocr；误归属 → 整轴 pixel_ratio。"""

    def test_ocr_wins_when_axis_equation_holds(self, sketch_path, monkeypatch):
        _patch_tesseract_available(monkeypatch)
        _fake_scan(monkeypatch, dict(OCR_GOOD))
        r = composite.parse_composite_sketch(
            sketch_path, target_outer_w_cm=TARGET_W_CM, target_outer_h_cm=TARGET_H_CM)
        assert r.success
        assert r.sources["width_axis"] == "ocr"
        assert r.sources["height_axis"] == "ocr"
        # 非等比草图：OCR 标注值覆盖像素比例值（输出洞尺寸 +1 损耗）
        assert r.hole_w_cm == pytest.approx(82.0, abs=1e-6)
        assert r.hole_h_cm == pytest.approx(61.5, abs=1e-6)
        assert r.margin_left_cm == pytest.approx(44.6, abs=1e-6)
        assert r.margin_right_cm == pytest.approx(59.4, abs=1e-6)
        assert r.margin_top_cm == pytest.approx(10.2, abs=1e-6)
        assert r.margin_bottom_cm == pytest.approx(17.3, abs=1e-6)
        assert r.self_consistency == 1.0

    def test_gate_rejects_misattributed_width_axis(self, sketch_path, monkeypatch):
        _patch_tesseract_available(monkeypatch)
        bad = dict(OCR_GOOD)
        bad["hole_w"] = 75.0  # 宽轴 Σ=179.0 ≠ 185，闸口拦截
        _fake_scan(monkeypatch, bad)
        r = composite.parse_composite_sketch(
            sketch_path, target_outer_w_cm=TARGET_W_CM, target_outer_h_cm=TARGET_H_CM)
        assert r.success
        assert r.sources["width_axis"] == "pixel_ratio"
        assert r.sources["height_axis"] == "ocr"
        assert r.hole_w_cm == pytest.approx(81.0, abs=0.51)   # 回退像素 80+1
        assert r.margin_left_cm == pytest.approx(45.0, abs=0.2)

    def test_ocr_fill_single_missing_role(self, sketch_path, monkeypatch):
        _patch_tesseract_available(monkeypatch)
        partial = {k: v for k, v in OCR_GOOD.items() if k != "hole_w"}
        _fake_scan(monkeypatch, partial)
        r = composite.parse_composite_sketch(
            sketch_path, target_outer_w_cm=TARGET_W_CM, target_outer_h_cm=TARGET_H_CM)
        assert r.success
        # 缺 hole_w：fill = 185 − 44.6 − 59.4 = 81.0，与像素 hint 80.0 偏差 1.25% ≤ 30%
        assert r.sources["width_axis"] == "ocr_fill"
        assert r.hole_w_cm == pytest.approx(82.0, abs=1e-6)

    def test_empty_scan_falls_back_to_pixel_ratio(self, sketch_path, monkeypatch):
        _patch_tesseract_available(monkeypatch)
        monkeypatch.setattr(composite, "_multi_scale_ocr_scan", lambda *a, **k: [])
        r = composite.parse_composite_sketch(
            sketch_path, target_outer_w_cm=TARGET_W_CM, target_outer_h_cm=TARGET_H_CM)
        assert r.success
        assert r.sources["width_axis"] == "pixel_ratio"
        assert r.sources["height_axis"] == "pixel_ratio"
        assert r.hole_w_cm == pytest.approx(81.0, abs=0.51)


class TestOcrUnavailableDegradation:
    """OCR 引擎不可用时几何降级：不崩溃、结果可用、来源可溯源。"""

    def test_tesseract_unavailable_degrades_cleanly(self, sketch_path, monkeypatch):
        monkeypatch.setattr(composite, "_safe_import_tesseract", lambda: None)

        def _forbidden_scan(*a, **k):  # OCR 分支不应被触达
            raise AssertionError("OCR 不可用时不应调用 _multi_scale_ocr_scan")

        monkeypatch.setattr(composite, "_multi_scale_ocr_scan", _forbidden_scan)
        r = composite.parse_composite_sketch(
            sketch_path, target_outer_w_cm=TARGET_W_CM, target_outer_h_cm=TARGET_H_CM)
        assert r.success
        assert r.sources["ocr"] == "unavailable"
        assert r.sources["width_axis"] == "pixel_ratio"
        assert r.hole_w_cm == pytest.approx(81.0, abs=0.51)
        assert len(r.cuts_cm) == 1
