"""第二期识别层单元测试 — 阶梯 L 形挖角识别。"""
import numpy as np
import pytest
from services.sketch_parser.lshape_sketch_parser import (
    _detect_concave_sliding_window,
    _classify_pattern,
    _convert_stepped_to_cut_rects,
)


class TestB1SlidingWindowBucketMerge:
    """B1 解除滑动窗口桶合并：每桶保留全部候选 + 去重 + cap 4。"""

    def test_returns_list_format(self):
        """返回值应为列表格式（即使为空）。"""
        # 简单矩形轮廓（无凹角）
        pts = np.array([
            [0, 0], [200, 0], [200, 200], [0, 200],
        ], dtype=float)
        corners = [(0, 0), (200, 0), (0, 200), (200, 200)]
        results = _detect_concave_sliding_window(
            pts, 200, 200, 282.8, corners, 0, 0, 200, 200)
        # 无凹角时应返回空列表或 None
        assert results is None or results == []


class TestB3ClassifyPattern:
    """B3 同边分类器：检测单边阶梯 vs 多边 L 形。"""

    def test_single_edge_stepped_same_vertical_edge(self):
        """同角位多个凹点在同竖边（x 相同）→ single_edge_stepped。"""
        all_corners = [
            {'corner': 'tr', 'concave': (180, 20)},
            {'corner': 'tr', 'concave': (180, 50)},  # x 相同，y 不同
        ]
        pattern = _classify_pattern(all_corners)
        assert pattern == 'single_edge_stepped'

    def test_single_edge_stepped_same_horizontal_edge(self):
        """同角位多个凹点在同横边（y 相同）→ single_edge_stepped。"""
        all_corners = [
            {'corner': 'tr', 'concave': (180, 20)},
            {'corner': 'tr', 'concave': (150, 20)},  # y 相同，x 不同
        ]
        pattern = _classify_pattern(all_corners)
        assert pattern == 'single_edge_stepped'

    def test_single_edge_stepped_right_edge_negative_slope(self):
        """同角位凹点对角错位（右边缘阶梯，x 增 y 减）→ single_edge_stepped。

        [V2.4] 阶梯相邻凹点沿边方向对角错位、不共线，单调性即可判同边。
        """
        all_corners = [
            {'corner': 'tr', 'concave': (180, 20)},
            {'corner': 'tr', 'concave': (150, 50)},  # x 减 y 增，单调
        ]
        pattern = _classify_pattern(all_corners)
        assert pattern == 'single_edge_stepped'

    def test_single_edge_stepped_diagonal_real_sketch(self):
        """真实草图回归（2026-09-18）：上边缘阶梯凹点对角错位 → stepped。

        草图实测凹点 (168.5, 7) 与 (173.5, 14)（px×10 缩放后），
        x 增 y 增（正相关），旧共线判定永不命中导致退化为单角 bbox 合并。
        """
        all_corners = [
            {'corner': 'tr', 'concave': (1685, 70)},
            {'corner': 'tr', 'concave': (1735, 140)},
        ]
        pattern = _classify_pattern(all_corners)
        assert pattern == 'single_edge_stepped'

    def test_multi_edge_different_corners(self):
        """不同角位 → multi_edge。"""
        all_corners = [
            {'corner': 'tr', 'concave': (180, 20)},
            {'corner': 'bl', 'concave': (30, 180)},
        ]
        pattern = _classify_pattern(all_corners)
        assert pattern == 'multi_edge'

    def test_multi_edge_same_corner_non_monotone(self):
        """同角位但 y 非单调（3 点来回折返）→ multi_edge。"""
        all_corners = [
            {'corner': 'tr', 'concave': (150, 20)},
            {'corner': 'tr', 'concave': (160, 60)},
            {'corner': 'tr', 'concave': (180, 40)},  # y: 20→60→40 非单调
        ]
        pattern = _classify_pattern(all_corners)
        assert pattern == 'multi_edge'

    def test_single_corner_defaults_to_multi_edge(self):
        """单角位 → multi_edge（非阶梯）。"""
        all_corners = [
            {'corner': 'tr', 'concave': (180, 20)},
        ]
        pattern = _classify_pattern(all_corners)
        assert pattern == 'multi_edge'


class TestB2SteppedConversion:
    """B2 条带分解转换：阶梯凹点坐标 → CutRect 列表（报告 V2.4 基准式）。

    tr 系公式 S_i = (ox=0, oy=y_{i-1}, w=W−x_i, h=y_i−y_{i-1})，
    其余角位经镜像化为 tr 系（offset 以角位自身两边为基准，数值不变）。
    """

    GEO_REAL = {'outer_w_px': 1885, 'outer_h_px': 740}
    DIMS_REAL = {'outer_w_cm': 188.5, 'outer_h_cm': 74.0}

    def test_tr_real_sketch_two_strips(self):
        """真实草图回归（2026-09-18）：tr 凹点 (1685,70)/(1735,140) → 两级条带。"""
        all_corners = [
            {'corner': 'tr', 'concave': (1685, 70)},
            {'corner': 'tr', 'concave': (1735, 140)},
        ]
        cut_rects, iou = _convert_stepped_to_cut_rects(
            all_corners, 'tr', dict(self.GEO_REAL), dict(self.DIMS_REAL))
        assert cut_rects == [
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 0.0,
             'w_cm': 20.0, 'h_cm': 7.0},
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 7.0,
             'w_cm': 15.0, 'h_cm': 7.0},
        ]
        assert iou is None  # 无轮廓时不做 IoU 闸口判定

    def test_tl_mirror_invariance(self):
        """tl 角 x 镜像化 tr 系：offset 基准随角位，公式产出正确条带。"""
        all_corners = [
            {'corner': 'tl', 'concave': (300, 400)},
            {'corner': 'tl', 'concave': (500, 700)},
        ]
        geo = {'outer_w_px': 2000, 'outer_h_px': 1000}
        dims = {'outer_w_cm': 200.0, 'outer_h_cm': 100.0}
        cut_rects, _ = _convert_stepped_to_cut_rects(all_corners, 'tl', geo, dims)
        assert cut_rects == [
            {'anchor': 'tl', 'offset_x_cm': 0.0, 'offset_y_cm': 0.0,
             'w_cm': 30.0, 'h_cm': 40.0},
            {'anchor': 'tl', 'offset_x_cm': 0.0, 'offset_y_cm': 40.0,
             'w_cm': 50.0, 'h_cm': 30.0},
        ]

    def test_expanding_profile(self):
        """外扩形态（凹点 x 随 y 减小远离角位）同样产出递增条带。"""
        all_corners = [
            {'corner': 'tr', 'concave': (1800, 200)},
            {'corner': 'tr', 'concave': (1700, 500)},
        ]
        geo = {'outer_w_px': 2000, 'outer_h_px': 1000}
        dims = {'outer_w_cm': 200.0, 'outer_h_cm': 100.0}
        cut_rects, _ = _convert_stepped_to_cut_rects(all_corners, 'tr', geo, dims)
        assert cut_rects == [
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 0.0,
             'w_cm': 20.0, 'h_cm': 20.0},
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 20.0,
             'w_cm': 30.0, 'h_cm': 30.0},
        ]

    def test_same_y_degenerate_single_merged_strip(self):
        """同 y 双凹点：零高度条带被过滤，输出单条合并凹槽（G1 报部分完成）。"""
        all_corners = [
            {'corner': 'tr', 'concave': (1500, 200)},
            {'corner': 'tr', 'concave': (1800, 200)},
        ]
        geo = {'outer_w_px': 2000, 'outer_h_px': 1000}
        dims = {'outer_w_cm': 200.0, 'outer_h_cm': 100.0}
        cut_rects, _ = _convert_stepped_to_cut_rects(all_corners, 'tr', geo, dims)
        assert cut_rects == [
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 0.0,
             'w_cm': 50.0, 'h_cm': 20.0},
        ]

    def test_fewer_than_two_points_returns_none(self):
        """桶内不足 2 点 → 无法构成条带，返回 (None, None)。"""
        all_corners = [{'corner': 'tr', 'concave': (1685, 70)}]
        cut_rects, iou = _convert_stepped_to_cut_rects(
            all_corners, 'tr', dict(self.GEO_REAL), dict(self.DIMS_REAL))
        assert cut_rects is None
        assert iou is None

    def test_iou_against_exact_contour(self):
        """条带 CutRect 反拼轮廓与精确识别轮廓 IoU=1（B4 闸口上限验证）。"""
        all_corners = [
            {'corner': 'tr', 'concave': (1685, 70)},
            {'corner': 'tr', 'concave': (1735, 140)},
        ]
        verts = [
            (0, 0), (1685, 0), (1685, 70), (1735, 70), (1735, 140),
            (1885, 140), (1885, 740), (0, 740),
        ]
        geo = dict(self.GEO_REAL, verts=verts)
        cut_rects, iou = _convert_stepped_to_cut_rects(
            all_corners, 'tr', geo, dict(self.DIMS_REAL))
        assert cut_rects is not None
        assert iou is not None
        assert iou >= 0.99
