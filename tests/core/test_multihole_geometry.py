"""
tests/core/test_multihole_geometry.py
build_multihole_geometry() 独立单元测试。

覆盖该函数 200+ 行代码的核心分支：
- 入口条件：仅 is_multi_hole=True 且 holes>=2 时生效
- 全局边距优先级：sketch_result 全局值 > per-hole min
- 三种 layout 分支：horizontal / vertical / mixed
- 尺寸扩展 + 间距补偿不变量
- UI override：用户修改多洞参数后覆盖每洞 w/h/gaps
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.geometry import CropDesign
from workers.design_builders import build_multihole_geometry


def _make_design():
    """创建最小可用的 CropDesign 用于测试。"""
    d = CropDesign(canvas_w_cm=200.0, canvas_h_cm=100.0, dpi=150)
    d.outer_margin_cm = 0.0
    d.inner_margin_top_cm = 10.0
    d.inner_margin_bottom_cm = 10.0
    d.inner_margin_left_cm = 10.0
    d.inner_margin_right_cm = 10.0
    return d


def _hole(w=30.0, h=40.0, mt=0.0, mb=0.0, ml=0.0, mr=0.0):
    """构造一个 HoleInfo 风格的 SimpleNamespace。"""
    return SimpleNamespace(
        w_cm=w, h_cm=h,
        margin_top_cm=mt, margin_bottom_cm=mb,
        margin_left_cm=ml, margin_right_cm=mr,
    )


def _sketch_multi(holes, gaps=None, layout='horizontal',
                  mt=10.0, mb=10.0, ml=20.0, mr=20.0):
    """构造多洞 sketch_result。"""
    return SimpleNamespace(
        success=True,
        is_multi_hole=True,
        holes=holes,
        hole_gaps_cm=gaps or [],
        layout_type=layout,
        margin_top_cm=mt, margin_bottom_cm=mb,
        margin_left_cm=ml, margin_right_cm=mr,
    )


class TestEntryConditions:
    """函数仅在特定条件下生效。"""

    def test_single_hole_does_not_trigger_multihole(self):
        design = _make_design()
        sketch = SimpleNamespace(
            success=True, is_multi_hole=True,
            holes=[_hole()], hole_gaps_cm=[], layout_type='horizontal',
            margin_top_cm=10, margin_bottom_cm=10,
            margin_left_cm=20, margin_right_cm=20,
        )
        build_multihole_geometry(design, sketch, trim_cm=1.0)
        assert design.pool_is_multi_hole is not True
        assert design.pool_holes_cm == []

    def test_non_multi_hole_sketch_does_not_trigger(self):
        design = _make_design()
        sketch = SimpleNamespace(
            success=True, is_multi_hole=False,
            holes=[_hole(), _hole()], hole_gaps_cm=[],
            margin_top_cm=10, margin_bottom_cm=10,
            margin_left_cm=20, margin_right_cm=20,
        )
        build_multihole_geometry(design, sketch, trim_cm=1.0)
        assert design.pool_is_multi_hole is not True

    def test_none_sketch_does_not_crash(self):
        design = _make_design()
        build_multihole_geometry(design, None, trim_cm=1.0)
        assert design.pool_is_multi_hole is not True


class TestHorizontalLayout:
    """横排布局：x 轴连续，y 轴每洞独立 mt。"""

    def test_two_holes_horizontal_positions(self):
        design = _make_design()
        holes = [_hole(w=30, h=40, ml=20, mt=15),
                 _hole(w=25, h=35, mt=12)]
        sketch = _sketch_multi(holes, gaps=[10.0], layout='horizontal',
                               mt=10, mb=10, ml=20, mr=20)
        build_multihole_geometry(design, sketch, trim_cm=1.0)

        assert design.pool_is_multi_hole is True
        assert len(design.pool_holes_cm) == 2
        # 第一洞：x = ml = 20, y = mt = 15, w = 30+1 = 31, h = 40+1 = 41
        h0 = design.pool_holes_cm[0]
        assert h0['x_cm'] == 20.0
        assert h0['y_cm'] == 15.0
        assert h0['w_cm'] == 31.0
        assert h0['h_cm'] == 41.0
        # 间距补偿：gap = 10 - 1 = 9
        assert design.pool_holes_gaps_cm == [9.0]
        # 第二洞：x = 20 + 31 + 9 = 60
        h1 = design.pool_holes_cm[1]
        assert h1['x_cm'] == 60.0
        assert h1['w_cm'] == 26.0

    def test_hole_size_expansion_invariant(self):
        """每洞尺寸 +trim_cm，间距 -trim_cm，保证总长不变。"""
        design = _make_design()
        holes = [_hole(w=30, h=40, ml=10), _hole(w=30, h=40)]
        sketch = _sketch_multi(holes, gaps=[20.0], layout='horizontal',
                               mt=10, mb=10, ml=10, mr=10)
        build_multihole_geometry(design, sketch, trim_cm=1.0)
        # ml + (w0+1) + (gap-1) + (w1+1) + mr = 10 + 31 + 19 + 31 + 10 = 101
        # canvas_w = outer + 1 = 200 + 1 = 201... 实际 canvas_w_cm=200
        # 验证间距确实被补偿
        assert design.pool_holes_gaps_cm == [19.0]


class TestVerticalLayout:
    """竖排布局：y 轴连续，x 轴每洞独立 ml。"""

    def test_two_holes_vertical_positions(self):
        design = _make_design()
        holes = [_hole(w=30, h=40, ml=15, mt=10),
                 _hole(w=25, h=35, ml=12, mt=10)]
        sketch = _sketch_multi(holes, gaps=[8.0], layout='vertical',
                               mt=10, mb=10, ml=10, mr=10)
        build_multihole_geometry(design, sketch, trim_cm=1.0)

        assert len(design.pool_holes_cm) == 2
        # 第一洞：y = mt = 10, x = ml = 15
        h0 = design.pool_holes_cm[0]
        assert h0['y_cm'] == 10.0
        assert h0['x_cm'] == 15.0
        assert h0['h_cm'] == 41.0  # 40 + 1
        # 间距补偿
        assert design.pool_holes_gaps_cm == [7.0]
        # 第二洞：y = 10 + 41 + 7 = 58
        h1 = design.pool_holes_cm[1]
        assert h1['y_cm'] == 58.0
        assert h1['x_cm'] == 12.0  # 独立 ml


class TestMixedLayout:
    """混合布局退化为横排。"""

    def test_mixed_falls_back_to_horizontal(self):
        design = _make_design()
        holes = [_hole(w=30, h=40, ml=20, mt=15),
                 _hole(w=25, h=35, mt=12)]
        sketch = _sketch_multi(holes, gaps=[10.0], layout='mixed',
                               mt=10, mb=10, ml=20, mr=20)
        build_multihole_geometry(design, sketch, trim_cm=1.0)

        assert len(design.pool_holes_cm) == 2
        # mixed 按横排处理，x 轴连续
        h0 = design.pool_holes_cm[0]
        h1 = design.pool_holes_cm[1]
        assert h1['x_cm'] > h0['x_cm']


class TestGlobalMarginsPriority:
    """全局边距优先级：sketch_result 全局值 > per-hole min。"""

    def test_global_margins_take_priority(self):
        design = _make_design()
        holes = [_hole(ml=5, mr=5, mt=8, mb=8),
                 _hole(ml=3, mr=3, mt=6, mb=6)]
        sketch = _sketch_multi(holes, gaps=[10.0],
                               mt=15, mb=15, ml=25, mr=25)
        build_multihole_geometry(design, sketch, trim_cm=1.0)

        # 全局值优先
        assert design.inner_margin_top_cm == 15.0
        assert design.inner_margin_bottom_cm == 15.0
        assert design.inner_margin_left_cm == 25.0
        assert design.inner_margin_right_cm == 25.0

    def test_per_hole_fallback_when_global_zero(self):
        design = _make_design()
        holes = [_hole(ml=5, mr=5, mt=8, mb=8),
                 _hole(ml=3, mr=3, mt=6, mb=6)]
        sketch = _sketch_multi(holes, gaps=[10.0],
                               mt=0, mb=0, ml=0, mr=0)
        build_multihole_geometry(design, sketch, trim_cm=1.0)

        # 全局为 0 时 fallback 到 per-hole min
        assert design.inner_margin_top_cm == 6.0  # min(8, 6)
        assert design.inner_margin_left_cm == 3.0  # min(5, 3)


class TestUIOverride:
    """用户通过面板修改多洞参数后覆盖每洞 w/h/gaps。"""

    def test_ui_override_replaces_hole_sizes(self):
        design = _make_design()
        holes = [_hole(w=30, h=40, ml=20, mt=15),
                 _hole(w=25, h=35, mt=12)]
        sketch = _sketch_multi(holes, gaps=[10.0], layout='horizontal',
                               mt=10, mb=10, ml=20, mr=20)
        user_multihole = {
            'active_count': 2,
            'holes_wh': [(35.0, 45.0), (28.0, 38.0)],
            'gaps_cm': [12.0],
            'layout_type': 'horizontal',
        }
        build_multihole_geometry(design, sketch, trim_cm=1.0,
                                 user_multihole=user_multihole)

        assert len(design.pool_holes_cm) == 2
        # UI 覆盖后尺寸直接使用用户值（不再 +trim）
        assert design.pool_holes_cm[0]['w_cm'] == 35.0
        assert design.pool_holes_cm[0]['h_cm'] == 45.0
        assert design.pool_holes_cm[1]['w_cm'] == 28.0
        assert design.pool_holes_gaps_cm == [12.0]

    def test_ui_override_vertical_layout(self):
        design = _make_design()
        holes = [_hole(w=30, h=40, ml=15, mt=10),
                 _hole(w=25, h=35, ml=12, mt=10)]
        sketch = _sketch_multi(holes, gaps=[8.0], layout='vertical',
                               mt=10, mb=10, ml=10, mr=10)
        user_multihole = {
            'active_count': 2,
            'holes_wh': [(32.0, 42.0), (27.0, 37.0)],
            'gaps_cm': [9.0],
            'layout_type': 'vertical',
        }
        build_multihole_geometry(design, sketch, trim_cm=1.0,
                                 user_multihole=user_multihole)

        assert design.pool_layout_type == 'vertical'
        # 竖排：y 轴连续
        h0 = design.pool_holes_cm[0]
        h1 = design.pool_holes_cm[1]
        assert h1['y_cm'] > h0['y_cm']
        assert h1['y_cm'] == h0['y_cm'] + h0['h_cm'] + 9.0

    def test_ui_override_not_triggered_for_single_hole(self):
        """active_count < 2 时 UI override 不生效。"""
        design = _make_design()
        holes = [_hole(w=30, h=40, ml=20, mt=15),
                 _hole(w=25, h=35, mt=12)]
        sketch = _sketch_multi(holes, gaps=[10.0], layout='horizontal',
                               mt=10, mb=10, ml=20, mr=20)
        user_multihole = {
            'active_count': 1,  # 单洞不触发
            'holes_wh': [(35.0, 45.0)],
            'gaps_cm': [],
        }
        build_multihole_geometry(design, sketch, trim_cm=1.0,
                                 user_multihole=user_multihole)

        # 未触发 UI override，保持 sketch 解析值（+trim）
        assert design.pool_holes_cm[0]['w_cm'] == 31.0  # 30 + 1


class TestPerHoleMargins:
    """per-hole 边距在 UI override 后保留。"""

    def test_per_hole_mt_preserved_after_ui_override(self):
        design = _make_design()
        holes = [_hole(w=30, h=40, ml=20, mt=18),
                 _hole(w=25, h=35, mt=22)]
        sketch = _sketch_multi(holes, gaps=[10.0], layout='horizontal',
                               mt=10, mb=10, ml=20, mr=20)
        user_multihole = {
            'active_count': 2,
            'holes_wh': [(35.0, 45.0), (28.0, 38.0)],
            'gaps_cm': [12.0],
            'layout_type': 'horizontal',
        }
        build_multihole_geometry(design, sketch, trim_cm=1.0,
                                 user_multihole=user_multihole)

        # per-hole mt 保留（来自 sketch 解析）
        assert design.pool_holes_cm[0]['mt_cm'] == 18.0
        assert design.pool_holes_cm[1]['mt_cm'] == 22.0
