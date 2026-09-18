"""第二期识别层单元测试 — 阶梯 L 形挖角识别。"""
import numpy as np
import pytest
from services.sketch_parser.lshape_sketch_parser import (
    _detect_concave_sliding_window,
    _classify_pattern,
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

    def test_multi_edge_different_corners(self):
        """不同角位 → multi_edge。"""
        all_corners = [
            {'corner': 'tr', 'concave': (180, 20)},
            {'corner': 'bl', 'concave': (30, 180)},
        ]
        pattern = _classify_pattern(all_corners)
        assert pattern == 'multi_edge'

    def test_multi_edge_same_corner_different_edges(self):
        """同角位但不在同边 → multi_edge。"""
        all_corners = [
            {'corner': 'tr', 'concave': (180, 20)},
            {'corner': 'tr', 'concave': (150, 50)},
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
