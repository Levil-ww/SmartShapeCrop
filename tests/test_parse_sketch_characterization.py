"""
特征化测试：parse_sketch 几何驱动主路径（characterization / golden test）。

目的：在 P2 拆分 parse_sketch 经典回退算法前，先锁定【主路径】（几何驱动成功
即早返回）的端到端行为，使后续重构有可观测的回归网。

设计：用 PIL 合成"双嵌套黑色矩形"草图（无文字标注，避免 Tesseract 抖动），
触发 _geometry_driven_parse 成功路径。结果依赖 cv2 矩形检测 + 几何间隙计算，
在合成干净图上是确定性的。

宽版 + 竖版两例，覆盖方向处理，防止横竖颠倒类回归。
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


# ---------------------------------------------------------------------------
# 宽版草图
# ---------------------------------------------------------------------------

class TestWideSketch:
    """画布 1000×500；外框 (50,50)-(950,450)=900×400px；内框 (150,100)-(850,400)=700×300px。

    target 90×40cm → cm/px = 0.1。
    期望几何边距：上=5 下=5 左=10 右=10；内框 70×30cm。
    （实测基线：inner 70.12×30.12，margins 4.89/4.99/9.89/9.99 —— 0.12 偏差来自
    3px 边线宽度被检测在边线内侧，确定性。）
    """

    def test_geometry_driven_path(self, tmp_path):
        p = str(tmp_path / "wide.png")
        _draw_nested(p, (1000, 500), (50, 50, 950, 450), (150, 100, 850, 400))
        r = parse_sketch(p, target_outer_w_cm=90.0, target_outer_h_cm=40.0)

        # 主路径成功
        assert r.success is True
        assert r.method == "geometry_driven"

        # 外框 = target（主源）
        assert r.outer_w_cm == pytest.approx(90.0, abs=0.5)
        assert r.outer_h_cm == pytest.approx(40.0, abs=0.5)

        # 内框 ≈ 70×30
        assert r.inner_w_cm == pytest.approx(70.0, abs=1.0)
        assert r.inner_h_cm == pytest.approx(30.0, abs=1.0)

        # 边距 ≈ 上5 下5 左10 右10
        assert r.margin_top_cm == pytest.approx(5.0, abs=1.0)
        assert r.margin_bottom_cm == pytest.approx(5.0, abs=1.0)
        assert r.margin_left_cm == pytest.approx(10.0, abs=1.0)
        assert r.margin_right_cm == pytest.approx(10.0, abs=1.0)

        # 结构不变量：外框 = 内框 + 边距和（几何自洽）
        assert abs(r.outer_w_cm - r.inner_w_cm - r.margin_left_cm - r.margin_right_cm) < 1.5
        assert abs(r.outer_h_cm - r.inner_h_cm - r.margin_top_cm - r.margin_bottom_cm) < 1.5


# ---------------------------------------------------------------------------
# 竖版草图（方向处理保护）
# ---------------------------------------------------------------------------

class TestTallSketch:
    """画布 500×1000；外框 (50,50)-(450,950)=400×900px；内框 (100,150)-(400,850)=300×700px。

    target 40×90cm → cm/px = 0.1。
    期望几何边距：上=10 下=10 左=5 右=5；内框 30×70cm。
    竖版用以防止宽高/方向被错误交换（历史横竖颠倒 bug）。
    """

    def test_geometry_driven_path_tall(self, tmp_path):
        p = str(tmp_path / "tall.png")
        _draw_nested(p, (500, 1000), (50, 50, 450, 950), (100, 150, 400, 850))
        r = parse_sketch(p, target_outer_w_cm=40.0, target_outer_h_cm=90.0)

        assert r.success is True
        assert r.method == "geometry_driven"

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
# 经典回退路径（强制几何驱动失败以触发）
# ---------------------------------------------------------------------------

class TestClassicFallback:
    """monkeypatch _geometry_driven_parse 返回失败 → parse_sketch 走经典回退算法。

    覆盖 647 行经典回退上帝块的端到端行为，为 P2 拆分该块提供回归网。
    无文字合成图上 OCR 添益为零，结果由几何检测决定，确定性。
    缩短 _PARSE_TIMEOUT_SEC 至 2s 以加速（不影响几何结果）。
    """

    def test_classic_fallback_geometry(self, tmp_path, monkeypatch):
        p = str(tmp_path / "wide.png")
        _draw_nested(p, (1000, 500), (50, 50, 950, 450), (150, 100, 850, 400))

        # 强制几何驱动失败 + 缩短超时
        monkeypatch.setattr(sp, "_geometry_driven_parse", lambda *a, **k: {"success": False})
        monkeypatch.setattr(sp, "_PARSE_TIMEOUT_SEC", 2)

        r = parse_sketch(p, target_outer_w_cm=90.0, target_outer_h_cm=40.0)

        assert r.success is True
        assert r.method == "geometry"

        assert r.outer_w_cm == pytest.approx(90.0, abs=0.5)
        assert r.outer_h_cm == pytest.approx(40.0, abs=0.5)
        assert r.inner_w_cm == pytest.approx(70.0, abs=1.0)
        assert r.inner_h_cm == pytest.approx(30.0, abs=1.0)
        assert r.margin_top_cm == pytest.approx(5.0, abs=1.0)
        assert r.margin_bottom_cm == pytest.approx(5.0, abs=1.0)
        assert r.margin_left_cm == pytest.approx(10.0, abs=1.0)
        assert r.margin_right_cm == pytest.approx(10.0, abs=1.0)

        # 结构不变量
        assert abs(r.outer_w_cm - r.inner_w_cm - r.margin_left_cm - r.margin_right_cm) < 1.5
        assert abs(r.outer_h_cm - r.inner_h_cm - r.margin_top_cm - r.margin_bottom_cm) < 1.5
