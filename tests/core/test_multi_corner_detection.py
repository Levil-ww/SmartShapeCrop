"""多角 L 形挖角识别验证测试。

合成一张双角挖角草图（tr 28×8cm + bl 30×12.8cm，板材 143×62.8cm），
验证解除三个收敛点后，识别层能同时返回两个凹角。
"""

import os
import sys
import tempfile

import numpy as np
import pytest
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.sketch_parser.lshape_sketch_parser import (
    _detect_lshape_geometry,
    parse_lshape_sketch,
)
from services.sketch_parser.sketch_parser_vision import _load_image, _to_gray


def _make_dual_corner_sketch(out_path, *, s=4.0, stroke=6, margin=20):
    """合成双角挖角草图。

    轮廓顶点（cm，y 向下）:
      (0,0) → (115,0) → (115,8) → (143,8) → (143,62.8) → (30,62.8) → (30,50) → (0,50)

    挖角①: tr = 28×8 cm（右上角）
    挖角②: bl = 30×12.8 cm（左下角）
    """
    W_cm, H_cm = 143.0, 62.8
    verts_cm = [
        (0, 0), (115, 0), (115, 8), (143, 8),
        (143, 62.8), (30, 62.8), (30, 50), (0, 50),
    ]
    wpx = int(W_cm * s)
    hpx = int(H_cm * s)
    img_w = wpx + 2 * margin
    img_h = hpx + 2 * margin
    img = Image.new('RGB', (img_w, img_h), (255, 255, 255))
    d = ImageDraw.Draw(img)

    verts_px = [(margin + x * s, margin + y * s) for x, y in verts_cm]
    d.line(verts_px + [verts_px[0]], fill=(0, 0, 0), width=stroke, joint='curve')
    img.save(out_path, 'PNG')
    return out_path


class TestDualCornerDetection:
    """验证解除收敛后双角识别。"""

    def test_geometry_detects_two_corners(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'dual_corner.png')
            _make_dual_corner_sketch(p)
            import cv2
            img, _ = _load_image(p)
            gray = _to_gray(img)
            geo = _detect_lshape_geometry(cv2, gray)
            assert geo is not None, "应检测到 L 形几何"
            n = geo.get('n_detected', 1)
            assert n == 2, f"应检测到 2 个凹角，实际 {n}"
            corners = {c['corner'] for c in geo.get('all_corners', [])}
            assert 'tr' in corners, f"应包含 tr 角，实际 {corners}"
            assert 'bl' in corners, f"应包含 bl 角，实际 {corners}"

    def test_hull_detects_two_gaps(self):
        """凸包差异法应返回 2 个合格连通域。"""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'dual_corner.png')
            _make_dual_corner_sketch(p)
            import cv2
            img, _ = _load_image(p)
            gray = _to_gray(img)
            geo = _detect_lshape_geometry(cv2, gray)
            assert geo is not None
            n_hull = geo.get('n_detected_hull', 0)
            assert n_hull == 2, f"凸包差异法应检测到 2 个 gap，实际 {n_hull}"

    def test_corner_dimensions_approximate(self):
        """验证双角尺寸近似正确。"""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'dual_corner.png')
            _make_dual_corner_sketch(p)
            import cv2
            img, _ = _load_image(p)
            gray = _to_gray(img)
            geo = _detect_lshape_geometry(cv2, gray)
            assert geo is not None
            all_corners = geo.get('all_corners', [])
            assert len(all_corners) == 2

            px_outer_w = geo['outer_w_px']
            px_outer_h = geo['outer_h_px']

            for c in all_corners:
                if c['corner'] == 'tr':
                    r_w = c['cut_w_px'] / px_outer_w
                    r_h = c['cut_h_px'] / px_outer_h
                    assert abs(r_w - 28.0 / 143.0) < 0.05, f"tr cut_w 比例偏差: {r_w}"
                    assert abs(r_h - 8.0 / 62.8) < 0.05, f"tr cut_h 比例偏差: {r_h}"
                elif c['corner'] == 'bl':
                    r_w = c['cut_w_px'] / px_outer_w
                    r_h = c['cut_h_px'] / px_outer_h
                    assert abs(r_w - 30.0 / 143.0) < 0.05, f"bl cut_w 比例偏差: {r_w}"
                    assert abs(r_h - 12.8 / 62.8) < 0.05, f"bl cut_h 比例偏差: {r_h}"

    def test_g1_gate_fires_on_multi_corner(self):
        """G1 闸口在多角场景应报告 n_detected > n_consumed。"""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'dual_corner.png')
            _make_dual_corner_sketch(p)
            res = parse_lshape_sketch(
                p, target_outer_w_cm=143.0, target_outer_h_cm=62.8
            )
            assert res.success, f"应成功（含 G1 告警）: {res.message}"
            assert res.notches_detected >= 2, (
                f"G1 应检测到 ≥2 个角，实际 {res.notches_detected}"
            )
            assert res.notches_consumed == 1, (
                f"第一期应只消费 1 个角，实际 {res.notches_consumed}"
            )
            assert res.notches_detected > res.notches_consumed, "G1 闸口应触发"
            assert '⚠️' in res.message, f"消息应含告警，实际: {res.message}"


class TestSingleCornerBackwardCompat:
    """验证单角场景等价性（单元素列表 ≡ 原单角行为）。"""

    def test_single_corner_still_works(self):
        """单角草图应返回 n_detected=1，无 G1 告警。"""
        from tests.core.test_lshape_sketch_parser import _make_lshape_sketch
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'single_tr.png')
            _make_lshape_sketch(p, corner='tr')
            import cv2
            from services.sketch_parser.sketch_parser_vision import _load_image, _to_gray
            img, _ = _load_image(p)
            gray = _to_gray(img)
            geo = _detect_lshape_geometry(cv2, gray)
            assert geo is not None, "单角应检测到 L 形"
            assert geo.get('n_detected', 1) == 1, (
                f"单角应 n_detected=1，实际 {geo.get('n_detected')}"
            )
            assert geo['corner'] == 'tr', f"corner 应为 tr，实际 {geo['corner']}"

    def test_single_corner_parse_no_g1_warning(self):
        """单角 parse 应 success=True，无 G1 告警。"""
        from tests.core.test_lshape_sketch_parser import _make_lshape_sketch
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'single_tr.png')
            _make_lshape_sketch(p, corner='tr')
            res = parse_lshape_sketch(p)
            assert res.success, f"单角应 success=True: {res.message}"
            assert res.notches_detected <= 1, (
                f"单角 notches_detected 应 ≤1，实际 {res.notches_detected}"
            )
            assert '⚠️' not in res.message, f"单角不应有 G1 告警: {res.message}"


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
