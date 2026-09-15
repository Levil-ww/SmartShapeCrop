"""多角 OCR 逐角归属验证测试（三期）。

测试 _attribute_cut_ocr_per_corner 函数能否将草图标注的 OCR 数值
正确归属到每个角的挖角尺寸 (E/D)，而非依赖像素比例反推。
"""

import os
import sys
import tempfile

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.sketch_parser.lshape_sketch_parser import (
    _attribute_cut_ocr_per_corner,
    _resolve_cut_pair_per_corner,
    parse_lshape_sketch,
)


def _load_font(size):
    candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for c in candidates:
        if os.path.isfile(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                pass
    return ImageFont.load_default()


class TestAttributeCutOcrPerCorner:
    """直接测试 _attribute_cut_ocr_per_corner 的逐角归属逻辑。"""

    def test_dual_corner_ocr_attribution(self):
        """双角 (tr+bl) 草图的 OCR 数值应各自归到正确的角。

        草图：板材 143×62.8cm，s=4，margin=20
          tr: cut 28×8cm，concave=(480, 52)
          bl: cut 30×12.8cm，concave=(140, 220)
        """
        geo = {
            'bbox': (20, 20, 592, 271),
            'outer_w_px': 572.0,
            'outer_h_px': 251.0,
            'all_corners': [
                {
                    'corner': 'tr',
                    'cut_w_px': 112.0,
                    'cut_h_px': 32.0,
                    'concave': (480, 52),
                    'score': 100.0,
                },
                {
                    'corner': 'bl',
                    'cut_w_px': 120.0,
                    'cut_h_px': 51.0,
                    'concave': (140, 220),
                    'score': 90.0,
                },
            ],
        }
        outer_w_cm = 143.0
        outer_h_cm = 62.8

        ocr_numbers = [
            # tr: cut_w=28 on top edge, right of concave (x>=480)
            (28.0, 0.9, (520, 15, 30, 12)),
            # tr: remaining=115 on top edge, left of concave (should NOT match tr)
            (115.0, 0.85, (250, 15, 30, 12)),
            # tr: cut_h=8 on right edge, above concave (y<=52)
            (8.0, 0.9, (588, 25, 12, 20)),
            # tr: remaining=54.8 on right edge, below concave
            (54.8, 0.85, (588, 160, 12, 30)),
            # bl: cut_w=30 on bottom edge, left of concave (x<=140)
            (30.0, 0.9, (60, 268, 30, 12)),
            # bl: remaining=113 on bottom edge, right of concave
            (113.0, 0.85, (360, 268, 30, 12)),
            # bl: cut_h=12.8 on left edge, below concave (y>=220)
            (12.8, 0.9, (12, 245, 12, 25)),
            # bl: remaining=50 on left edge, above concave
            (50.0, 0.85, (12, 120, 12, 25)),
            # Outer dimensions
            (143.0, 0.9, (300, 268, 35, 12)),
            (62.8, 0.9, (8, 140, 12, 30)),
        ]

        results = _attribute_cut_ocr_per_corner(
            geo['all_corners'], ocr_numbers, geo, outer_w_cm, outer_h_cm)

        assert len(results) == 2

        tr = next(r for r in results if r['corner'] == 'tr')
        bl = next(r for r in results if r['corner'] == 'bl')

        assert tr['source'] == 'ocr', f"tr 应为 ocr 来源，实际 {tr['source']}"
        assert abs(tr['cut_w_cm'] - 28.0) < 2.0, f"tr cut_w 应≈28，实际 {tr['cut_w_cm']}"
        assert abs(tr['cut_h_cm'] - 8.0) < 2.0, f"tr cut_h 应≈8，实际 {tr['cut_h_cm']}"

        assert bl['source'] == 'ocr', f"bl 应为 ocr 来源，实际 {bl['source']}"
        assert abs(bl['cut_w_cm'] - 30.0) < 2.0, f"bl cut_w 应≈30，实际 {bl['cut_w_cm']}"
        assert abs(bl['cut_h_cm'] - 12.8) < 2.0, f"bl cut_h 应≈12.8，实际 {bl['cut_h_cm']}"

    def test_adjacent_corner_shared_edge(self):
        """相邻角 (tl+tr) 共享顶边时，OCR 数值按凹角点分段归属。

        草图：板材 100×60cm，s=4，margin=20
          tl: cut 20×5cm，concave=(100, 40)
          tr: cut 25×10cm，concave=(300, 40)
        顶边被两个凹角分成三段：[20,100]=tl切段，[100,300]=中间段，[300,400]=tr切段
        """
        geo = {
            'bbox': (20, 20, 400, 240),
            'outer_w_px': 380.0,
            'outer_h_px': 220.0,
            'all_corners': [
                {
                    'corner': 'tl',
                    'cut_w_px': 80.0,   # 20cm * 4
                    'cut_h_px': 20.0,   # 5cm * 4
                    'concave': (100, 40),
                    'score': 100.0,
                },
                {
                    'corner': 'tr',
                    'cut_w_px': 100.0,  # 25cm * 4
                    'cut_h_px': 40.0,   # 10cm * 4
                    'concave': (300, 40),
                    'score': 90.0,
                },
            ],
        }
        outer_w_cm = 100.0
        outer_h_cm = 60.0

        ocr_numbers = [
            # tl: cut_w=20 on top edge, left of concave (x<=100)
            (20.0, 0.9, (50, 15, 25, 12)),
            # tr: cut_w=25 on top edge, right of concave (x>=300)
            (25.0, 0.9, (340, 15, 25, 12)),
            # Middle segment value (should be ignored by both tl and tr)
            (60.0, 0.8, (200, 15, 30, 12)),
            # tl: cut_h=5 on left edge, above concave (y<=40)
            (5.0, 0.9, (12, 25, 12, 15)),
            # tr: cut_h=10 on right edge, above concave (y<=40)
            (10.0, 0.9, (392, 25, 12, 15)),
        ]

        results = _attribute_cut_ocr_per_corner(
            geo['all_corners'], ocr_numbers, geo, outer_w_cm, outer_h_cm)

        assert len(results) == 2

        tl = next(r for r in results if r['corner'] == 'tl')
        tr = next(r for r in results if r['corner'] == 'tr')

        assert abs(tl['cut_w_cm'] - 20.0) < 2.0, f"tl cut_w 应≈20，实际 {tl['cut_w_cm']}"
        assert abs(tl['cut_h_cm'] - 5.0) < 1.0, f"tl cut_h 应≈5，实际 {tl['cut_h_cm']}"

        assert abs(tr['cut_w_cm'] - 25.0) < 2.0, f"tr cut_w 应≈25，实际 {tr['cut_w_cm']}"
        assert abs(tr['cut_h_cm'] - 10.0) < 2.0, f"tr cut_h 应≈10，实际 {tr['cut_h_cm']}"

    def test_fallback_to_pixel_ratio_when_no_ocr(self):
        """OCR 数据为空时应回退到像素比例反推。"""
        geo = {
            'bbox': (20, 20, 592, 271),
            'outer_w_px': 572.0,
            'outer_h_px': 251.0,
            'all_corners': [
                {
                    'corner': 'tr',
                    'cut_w_px': 112.0,
                    'cut_h_px': 32.0,
                    'concave': (480, 52),
                    'score': 100.0,
                },
            ],
        }
        outer_w_cm = 143.0
        outer_h_cm = 62.8

        results = _attribute_cut_ocr_per_corner(
            geo['all_corners'], [], geo, outer_w_cm, outer_h_cm)

        assert len(results) == 1
        r = results[0]
        assert r['source'] == 'pixel_ratio', f"无 OCR 时应为 pixel_ratio，实际 {r['source']}"
        expected_w = round(143.0 * 112.0 / 572.0, 2)
        expected_h = round(62.8 * 32.0 / 251.0, 2)
        assert abs(r['cut_w_cm'] - expected_w) < 0.1
        assert abs(r['cut_h_cm'] - expected_h) < 0.1

    def test_ocr_rejected_when_far_from_geo_ratio(self):
        """OCR 值与像素比例偏差>40% 时应拒绝 OCR，回退像素比例。"""
        geo = {
            'bbox': (20, 20, 592, 271),
            'outer_w_px': 572.0,
            'outer_h_px': 251.0,
            'all_corners': [
                {
                    'corner': 'tr',
                    'cut_w_px': 112.0,  # ratio ≈ 0.196, expected ≈ 28cm
                    'cut_h_px': 32.0,
                    'concave': (480, 52),
                    'score': 100.0,
                },
            ],
        }
        outer_w_cm = 143.0
        outer_h_cm = 62.8

        # 两个都偏离的值：_resolve_cut_pair 会选 100（更接近 28），
        # 但 100/143=0.699 vs geo_ratio=0.196 → 偏差 0.503 > 0.40 → 拒绝
        # 右边的数值放在 y=42+，远离顶边 edge_tol=20.08 的重叠区
        ocr_numbers = [
            (100.0, 0.9, (520, 15, 30, 12)),   # on tr's top edge
            (120.0, 0.85, (540, 15, 30, 12)),  # on tr's top edge
            (40.0, 0.9, (588, 42, 12, 8)),     # on tr's right edge, ny=46
            (50.0, 0.85, (588, 44, 12, 8)),    # on tr's right edge, ny=48
        ]

        results = _attribute_cut_ocr_per_corner(
            geo['all_corners'], ocr_numbers, geo, outer_w_cm, outer_h_cm)

        r = results[0]
        assert r['source'] == 'pixel_ratio', (
            f"OCR 偏差过大应回退 pixel_ratio，实际 {r['source']}"
        )

    def test_single_corner_backward_compat(self):
        """单角场景：OCR 归属结果应与像素比例接近。"""
        geo = {
            'bbox': (60, 60, 510, 192),
            'outer_w_px': 450.0,
            'outer_h_px': 132.0,
            'all_corners': [
                {
                    'corner': 'tr',
                    'cut_w_px': 100.0,
                    'cut_h_px': 8.0,
                    'concave': (410, 68),
                    'score': 100.0,
                },
            ],
        }
        outer_w_cm = 450.0
        outer_h_cm = 33.0

        # OCR: E=100 on top edge, D=2 on right edge
        ocr_numbers = [
            (100.0, 0.9, (440, 55, 30, 12)),  # tr cut_w on top, right of concave
            (2.0, 0.9, (500, 62, 12, 15)),    # tr cut_h on right, above concave
            (350.0, 0.85, (200, 55, 30, 12)),  # F=350 on top, left of concave
            (31.0, 0.85, (500, 130, 12, 15)),  # C=31 on right, below concave
            (450.0, 0.9, (250, 180, 35, 12)),  # B=450 on bottom
            (33.0, 0.9, (50, 120, 12, 25)),    # A=33 on left
        ]

        results = _attribute_cut_ocr_per_corner(
            geo['all_corners'], ocr_numbers, geo, outer_w_cm, outer_h_cm)

        r = results[0]
        assert r['source'] == 'ocr', f"单角有 OCR 时应为 ocr，实际 {r['source']}"
        assert abs(r['cut_w_cm'] - 100.0) < 5.0, f"cut_w 应≈100，实际 {r['cut_w_cm']}"
        assert abs(r['cut_h_cm'] - 2.0) < 1.0, f"cut_h 应≈2，实际 {r['cut_h_cm']}"


class TestResolveCutPairPerCorner:
    """测试 _resolve_cut_pair_per_corner 的比例匹配逻辑。"""

    def test_single_value_as_cut(self):
        """单值匹配 geo_ratio 时应识别为挖角尺寸。"""
        items = [(28.0, 540, 20, 0.9)]
        cut, rem = _resolve_cut_pair_per_corner(items, 143.0, 28.0 / 143.0)
        assert abs(cut - 28.0) < 0.1
        assert abs(rem - 115.0) < 0.1

    def test_single_value_as_remaining(self):
        """单值匹配 remaining 时应识别为剩余段，挖角=outer-val。"""
        items = [(115.0, 250, 20, 0.9)]
        geo_ratio = 28.0 / 143.0  # ≈ 0.196
        cut, rem = _resolve_cut_pair_per_corner(items, 143.0, geo_ratio)
        # 115 as remaining → cut = 143 - 115 = 28
        assert abs(cut - 28.0) < 0.1
        assert abs(rem - 115.0) < 0.1

    def test_multi_value_picks_closest_to_geo_ratio(self):
        """多值时选最接近 geo_ratio 的作为挖角尺寸。"""
        items = [
            (28.0, 540, 20, 0.9),
            (115.0, 250, 20, 0.85),
        ]
        geo_ratio = 28.0 / 143.0
        cut, rem = _resolve_cut_pair_per_corner(items, 143.0, geo_ratio)
        assert abs(cut - 28.0) < 0.1

    def test_empty_items_returns_none(self):
        """空输入返回 None。"""
        cut, rem = _resolve_cut_pair_per_corner([], 143.0, 0.2)
        assert cut is None
        assert rem is None


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
