"""
F1 回归测试：修复 inner/outer 矩形退化导致的 ValueError 崩溃

复现路径（来自 logs/crash.log）：
    main -> canvas.set_design -> _render_lod
         -> render_design_lod -> render_design
         -> geometry.fill_rect_mask(inner_rect, 255)
    ValueError: x1 must be greater than or equal to x0

根因：当内/外边距之和超过可用画布空间（或 LOD 下采样取整反转）时，
inner_rect_px()/outer_rect_px() 计算出负宽/负高，to_int_tuple() 取整后
x1 < x0，PIL ImageDraw 直接抛异常，预览静默崩溃。

修复（core/geometry.py）：
  - RectShape.to_int_tuple 归一 x0<=x1, y0<=y1
  - inner_rect_px/outer_rect_px 将 w/h clamp 到非负
  - fill_rect_mask/apply_rounded_corners_to_mask/fill_ellipse_mask 退化守卫

本测试断言：所有退化场景都不再抛异常，且返回结果合法。
"""
import sys
import os
sys.path.insert(0, '.')

import numpy as np
from PIL import Image, ImageDraw
from core.geometry import (
    CropDesign, RectShape,
    fill_rect_mask, fill_ellipse_mask, apply_rounded_corners_to_mask,
)


def _make_degenerate_design():
    """构造一个内边距之和 > 外框可用空间的退化设计（复现 F1）。"""
    d = CropDesign(
        canvas_w_cm=50.0, canvas_h_cm=70.0, dpi=150,
        mode='rect_hole',
        outer_margin_cm=1.0,
        # 上下/左右边距各 30cm，远超 50cm 画布 → 内矩形负宽高
        inner_margin_top_cm=30.0,
        inner_margin_bottom_cm=30.0,
        inner_margin_left_cm=30.0,
        inner_margin_right_cm=30.0,
    )
    return d


# ---------- 1. 整数元组归一（底层防护） ----------

def test_to_int_tuple_normalizes_degenerate():
    r = RectShape(x=100, y=100, w=-5, h=-5)
    assert r.to_int_tuple() == (100, 100, 100, 100)


def test_to_int_tuple_normalizes_positive():
    r = RectShape(x=10.4, y=20.6, w=50.2, h=40.8)
    x0, y0, x1, y1 = r.to_int_tuple()
    assert x0 <= x1 and y0 <= y1


# ---------- 2. px 计算方法 clamp 非负 ----------

def test_inner_rect_px_clamped_non_negative():
    d = _make_degenerate_design()
    ir = d.inner_rect_px()
    assert ir.w >= 0 and ir.h >= 0, f"inner_rect 应为非负宽高, got ({ir.w},{ir.h})"


def test_outer_rect_px_clamped_non_negative():
    d = _make_degenerate_design()
    d.outer_margin_cm = 999.0  # 外边距爆炸
    orr = d.outer_rect_px()
    assert orr.w >= 0 and orr.h >= 0, f"outer_rect 应为非负宽高, got ({orr.w},{orr.h})"


# ---------- 3. 绘制函数退化守卫 ----------

def test_fill_rect_mask_skips_degenerate_no_crash():
    mask = Image.new('L', (100, 100), 0)
    # 负宽高矩形不应抛异常
    fill_rect_mask(mask, RectShape(x=50, y=50, w=-20, h=-20), 255)
    # 同时确认未越界绘制（整图应保持原样，退化矩形绘制为空操作）
    arr = np.array(mask)
    assert arr.min() == 0 and arr.max() == 0


def test_fill_rect_mask_negative_w_positive_h_no_crash():
    mask = Image.new('L', (100, 100), 0)
    fill_rect_mask(mask, RectShape(x=60, y=10, w=-30, h=40), 255)
    arr = np.array(mask)
    assert arr.max() == 0  # x1<x0 归一后宽=0，无绘制


def test_apply_rounded_corners_skips_degenerate_no_crash():
    mask = Image.new('L', (200, 200), 0)
    degenerate = RectShape(x=80, y=80, w=-40, h=-40)
    # 不应抛异常
    apply_rounded_corners_to_mask(
        mask, degenerate,
        {'tl': 10, 'tr': 10, 'bl': 10, 'br': 10},
        fill_value=255)


def test_fill_ellipse_mask_negative_radius_no_crash():
    mask = Image.new('L', (100, 100), 0)
    from core.geometry import EllipseShape
    fill_ellipse_mask(mask, EllipseShape(cx=50, cy=50, rx=-30, ry=20), 255)
    from core.geometry import EllipseShape as E2
    fill_ellipse_mask(mask, E2(cx=50, cy=50, rx=20, ry=-30), 255)
    arr = np.array(mask)
    assert arr.max() == 0


# ---------- 4. 端到端：复现 GUI 崩溃路径 ----------

def test_render_design_lod_degenerate_no_crash():
    """直接复现 F1 崩溃路径：LOD 预览渲染退化设计。"""
    from core.image_ops import render_design_lod
    d = _make_degenerate_design()
    img = render_design_lod(d, scale=0.25)
    assert isinstance(img, Image.Image)
    assert img.size == (d.canvas_w_px, d.canvas_h_px)


def test_render_design_degenerate_no_crash():
    from core.image_ops import render_design
    d = _make_degenerate_design()
    img = render_design(d, quality='preview')
    assert isinstance(img, Image.Image)
    assert img.size == (d.canvas_w_px, d.canvas_h_px)


def test_render_design_lshape_degenerate_no_crash():
    from core.image_ops import render_design, render_design_lod
    d = CropDesign(
        canvas_w_cm=40.0, canvas_h_cm=30.0, dpi=150,
        mode='rect_lshape',
        outer_margin_cm=0.5,
        inner_margin_top_cm=25.0, inner_margin_bottom_cm=25.0,
        inner_margin_left_cm=25.0, inner_margin_right_cm=25.0,
        l_corner='br', l_cut_w_cm=8.0, l_cut_h_cm=6.0,
    )
    img1 = render_design(d, quality='preview')
    img2 = render_design_lod(d, scale=0.25)
    assert img1.size == (d.canvas_w_px, d.canvas_h_px)
    assert img2.size == (d.canvas_w_px, d.canvas_h_px)


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-v']))
