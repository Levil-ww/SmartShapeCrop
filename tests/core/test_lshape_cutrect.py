# -*- coding: utf-8 -*-
"""单边阶梯 L 形 CutRect 数据模型（一期）测试。

一期范围：CutRect 数据模型 + CropDesign.l_cut_rects + validate() 阶梯分支
+ build_lshape_mask 偏移感知 cut 规格 + _rect_from_anchor_offset。
旧 (corner, w, h) 3 元组路径必须逐像素不变（回归守卫）。
"""
import numpy as np
import pytest

from core.geometry import (
    CropDesign,
    CutRect,
    LShape,
    RectShape,
    _get_lshape_cut_rect_at_offset,
    _rect_from_anchor_offset,
    build_lshape_mask,
)

ZERO_RADII = {'tl': 0.0, 'tr': 0.0, 'bl': 0.0, 'br': 0.0}

# 真实草图（安妮森林）内缩阶梯：一级 18.5x6.5 贴 tr 角，二级 8.5x3.5 紧贴其下
INSET_STAIR = [CutRect('tr', 0, 0, 18.5, 6.5), CutRect('tr', 0, 6.5, 8.5, 3.5)]


def _design(cuts, canvas_w=93.5, canvas_h=43.8):
    return CropDesign(
        mode='rect_lshape',
        canvas_w_cm=canvas_w,
        canvas_h_cm=canvas_h,
        outer_margin_cm=0.0,
        inner_margin_top_cm=0.0,
        inner_margin_bottom_cm=0.0,
        inner_margin_left_cm=0.0,
        inner_margin_right_cm=0.0,
        l_cut_rects=cuts,
    )


# ---------- CutRect 数据模型 ----------

def test_cutrect_equality_and_fields():
    a = CutRect('tr', 0, 0, 18.5, 6.5)
    b = CutRect('tr', 0.0, 0.0, 18.5, 6.5)
    assert a == b
    assert a.anchor == 'tr'
    assert (a.offset_x_cm, a.offset_y_cm, a.w_cm, a.h_cm) == (0, 0, 18.5, 6.5)


def test_crop_design_l_cut_rects_default_empty():
    assert CropDesign(mode='rect_lshape').l_cut_rects == []


# ---------- _rect_from_anchor_offset ----------

def test_rect_from_anchor_offset_zero_matches_legacy_all_corners():
    outer = RectShape(0, 0, 1000, 500)
    for ck in ('tl', 'tr', 'bl', 'br'):
        r_new = _rect_from_anchor_offset(outer, ck, 0, 0, 300, 200)
        r_old = _get_lshape_cut_rect_at_offset(outer, ck, 300, 200, 0)
        assert (r_new.x, r_new.y, r_new.w, r_new.h) == (r_old.x, r_old.y, r_old.w, r_old.h)


def test_rect_from_anchor_offset_tr_positions():
    outer = RectShape(0, 0, 1000, 500)
    r = _rect_from_anchor_offset(outer, 'tr', 100, 50, 300, 200)
    assert (r.x, r.y, r.w, r.h) == (1000 - 100 - 300, 50, 300, 200)


def test_rect_from_anchor_offset_tl_positions():
    outer = RectShape(0, 0, 1000, 500)
    r = _rect_from_anchor_offset(outer, 'tl', 40, 30, 300, 200)
    assert (r.x, r.y, r.w, r.h) == (40, 30, 300, 200)


def test_rect_from_anchor_offset_bl_positions():
    outer = RectShape(0, 0, 1000, 500)
    r = _rect_from_anchor_offset(outer, 'bl', 40, 30, 300, 200)
    assert (r.x, r.y, r.w, r.h) == (40, 500 - 30 - 200, 300, 200)


def test_rect_from_anchor_offset_br_positions():
    outer = RectShape(0, 0, 1000, 500)
    r = _rect_from_anchor_offset(outer, 'br', 40, 30, 300, 200)
    assert (r.x, r.y, r.w, r.h) == (1000 - 40 - 300, 500 - 30 - 200, 300, 200)


def test_rect_from_anchor_offset_clamps_to_available():
    outer = RectShape(0, 0, 1000, 500)
    r = _rect_from_anchor_offset(outer, 'tr', 0, 0, 5000, 200)
    assert r.w == 1000
    assert r.h == 200
    assert r.x == 0


# ---------- validate() 阶梯分支 ----------

def test_validate_accepts_inset_staircase():
    _design(INSET_STAIR).validate()


def test_validate_accepts_outset_staircase():
    _design([CutRect('br', 0, 0, 5, 7), CutRect('br', 0, 7, 20, 7)]).validate()


def test_validate_accepts_diagonal_staircase():
    """报告附录的对角点接触阶梯：退化（角点相连）但合法。"""
    _design([CutRect('tr', 0, 0, 10, 6.5), CutRect('tr', 10, 6.5, 8.5, 3.5)]).validate()


def test_validate_rejects_four_levels_same_anchor():
    cuts = [CutRect('tr', 0, i * 3, 10, 3) for i in range(4)]
    with pytest.raises(ValueError, match='最多支持 3 级'):
        _design(cuts).validate()


def test_validate_rejects_nonpositive_size():
    with pytest.raises(ValueError, match='宽高必须为正数'):
        _design([CutRect('tr', 0, 0, 0, 5)]).validate()
    with pytest.raises(ValueError, match='宽高必须为正数'):
        _design([CutRect('tr', 0, 0, 10, -1)]).validate()


def test_validate_rejects_negative_offset():
    with pytest.raises(ValueError, match='不能为负'):
        _design([CutRect('tr', -1, 0, 10, 5)]).validate()
    with pytest.raises(ValueError, match='不能为负'):
        _design([CutRect('tr', 0, -0.5, 10, 5)]).validate()


def test_validate_rejects_out_of_bounds():
    with pytest.raises(ValueError, match='超出外框'):
        _design([CutRect('tr', 0, 0, 93.5, 5)]).validate()


def test_validate_boundary_width_passes():
    # 93.5cm 画布 − 0.5cm 余量 = 93.0cm 恰好可容纳
    _design([CutRect('tr', 0, 0, 93.0, 5)]).validate()


def test_validate_rejects_overlap_allows_shared_edge():
    with pytest.raises(ValueError, match='重叠'):
        _design([CutRect('tr', 0, 0, 10, 5), CutRect('tr', 2, 2, 10, 5)]).validate()
    # 共享边（相邻堆叠）合法
    _design([CutRect('tr', 0, 0, 10, 5), CutRect('tr', 0, 5, 10, 5)]).validate()


def test_validate_rejects_bad_anchor():
    with pytest.raises(ValueError, match='anchor'):
        _design([CutRect('xx', 0, 0, 10, 5)]).validate()


def test_validate_skips_legacy_edge_sum_when_cut_rects_present():
    """l_cut_rects 非空时是唯一几何来源：旧 l_cuts_cm 回退值 (br 15x10)
    在小画布上触发旧边约束（下边 15 > 12−0.5）属误报，必须跳过。"""
    _design([CutRect('tr', 0, 0, 10, 2), CutRect('tr', 0, 2, 6, 2)],
            canvas_w=12, canvas_h=12).validate()


# ---------- 旧路径零改动（回归守卫） ----------

def test_validate_old_duplicate_corner_still_rejected():
    d = CropDesign(
        mode='rect_lshape',
        l_cuts_cm=[
            {'corner': 'tr', 'cut_w_cm': 10, 'cut_h_cm': 5},
            {'corner': 'tr', 'cut_w_cm': 8, 'cut_h_cm': 3},
        ],
    )
    with pytest.raises(ValueError, match='重复角位'):
        d.validate()


def test_cut_specs_still_returns_tuples():
    d = CropDesign(mode='rect_lshape', l_corner='tr', l_cut_w_cm=10, l_cut_h_cm=5, dpi=100)
    shape = d.l_shapes_px()
    assert shape.cut_specs() == [('tr', d.cm2px(10.0), d.cm2px(5.0))]


def test_l_shapes_px_populates_cut_rects_and_keeps_tuple_specs():
    d = CropDesign(
        mode='rect_lshape',
        canvas_w_cm=93.5, canvas_h_cm=43.8, dpi=100,
        outer_margin_cm=0.0, inner_margin_top_cm=0.0, inner_margin_bottom_cm=0.0,
        inner_margin_left_cm=0.0, inner_margin_right_cm=0.0,
        l_cut_rects=INSET_STAIR,
    )
    shape = d.l_shapes_px()
    assert len(shape.cut_rects) == 2
    assert shape.cut_rects[0] == {
        'corner': 'tr', 'cut_w': d.cm2px(18.5), 'cut_h': d.cm2px(6.5),
        'offset_x': 0.0, 'offset_y': 0.0,
    }
    assert shape.cut_rects[1]['offset_y'] == pytest.approx(d.cm2px(6.5))
    assert shape.cut_rects[1]['cut_w'] == pytest.approx(d.cm2px(8.5))
    # 下游 3 元组解包必须仍然可用（边界近似：忽略 offset）
    specs = shape.cut_specs()
    assert all(len(s) == 3 for s in specs)
    assert specs[0][0] == 'tr'


def test_cut_rect_specs_wraps_legacy_single_corner():
    d = CropDesign(mode='rect_lshape', l_corner='tr', l_cut_w_cm=10, l_cut_h_cm=5, dpi=100)
    specs = d.l_shapes_px().cut_rect_specs()
    assert specs == [{'corner': 'tr', 'cut_w': d.cm2px(10.0), 'cut_h': d.cm2px(5.0),
                      'offset_x': 0.0, 'offset_y': 0.0}]


# ---------- build_lshape_mask 偏移感知规格 ----------

@pytest.mark.parametrize('ck', ['tl', 'tr', 'bl', 'br'])
def test_mask_dict_offset0_equals_legacy_tuple(ck):
    W, H = 331, 217
    m_old = build_lshape_mask((W, H), RectShape(0, 0, W, H), ck, 120, 90, ZERO_RADII, 255,
                              cuts=[(ck, 120.0, 90.0)])
    m_new = build_lshape_mask((W, H), RectShape(0, 0, W, H), ck, 120, 90, ZERO_RADII, 255,
                              cuts=[{'corner': ck, 'cut_w': 120.0, 'cut_h': 90.0}])
    assert np.array_equal(np.array(m_old), np.array(m_new))


@pytest.mark.parametrize('ck', ['tl', 'tr', 'bl', 'br'])
def test_mask_dict_offset0_equals_legacy_tuple_with_radii(ck):
    radii = {'tl': 12.0, 'tr': 15.0, 'bl': 0.0, 'br': 18.0}
    W, H = 400, 300
    m_old = build_lshape_mask((W, H), RectShape(0, 0, W, H), ck, 150, 110, radii, 255,
                              cuts=[(ck, 150.0, 110.0)])
    m_new = build_lshape_mask((W, H), RectShape(0, 0, W, H), ck, 150, 110, radii, 255,
                              cuts=[{'corner': ck, 'cut_w': 150.0, 'cut_h': 110.0,
                                     'offset_x': 0.0, 'offset_y': 0.0}])
    assert np.array_equal(np.array(m_old), np.array(m_new))


def test_mask_renders_two_level_inset_staircase():
    """一期验收：内缩两级台阶，第二级位置/尺寸必须真实出现在 mask 中。"""
    W = H = 200
    m = build_lshape_mask(
        (W, H), RectShape(0, 0, W, H), 'tr', 180, 65, ZERO_RADII, 255,
        cuts=[('tr', 180.0, 65.0),
              {'corner': 'tr', 'cut_w': 85.0, 'cut_h': 35.0,
               'offset_x': 0.0, 'offset_y': 65.0}])
    a = np.array(m, dtype=bool)
    # 顶部区域（一级内）：挖掉右侧 180px → 保留 0..19
    assert int(a[10].sum()) == 20
    assert int(a[64].sum()) == 20
    # 一级/二级交界行仍属一级宽度
    assert int(a[65].sum()) == 20
    # 二级区域：挖掉右侧 85px → 保留 0..114
    assert int(a[66].sum()) == 115
    assert int(a[100].sum()) == 115
    # 二级以下：完整保留
    assert int(a[101].sum()) == 200
    assert int(a[150].sum()) == 200
    # 面积 = 40000 − (180x65 + 85x35)，允许 PIL 含端点取整的 ±200px 偏差
    assert abs(int(a.sum()) - (40000 - 180 * 65 - 85 * 35)) <= 200


def test_mask_renders_outset_staircase_br():
    """外扩阶梯：br 角第一级 50x70，第二级 200x70 向上紧贴（offset_y=70）。"""
    W = H = 300
    m = build_lshape_mask(
        (W, H), RectShape(0, 0, W, H), 'br', 50, 70, ZERO_RADII, 255,
        cuts=[('br', 50.0, 70.0),
              {'corner': 'br', 'cut_w': 200.0, 'cut_h': 70.0,
               'offset_x': 0.0, 'offset_y': 70.0}])
    a = np.array(m, dtype=bool)
    assert int(a[10].sum()) == 300   # 未被挖
    assert int(a[160].sum()) == 100  # 二级：右侧挖 200
    assert int(a[229].sum()) == 100
    assert int(a[230].sum()) == 100  # 交界行（两级并集覆盖 100..299）
    assert int(a[231].sum()) == 250  # 一级：右侧挖 50
    assert int(a[299].sum()) == 250
