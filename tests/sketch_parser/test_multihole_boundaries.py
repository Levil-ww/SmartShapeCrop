"""
sketch_parser_multihole.py 边界条件测试 [T-06]

覆盖多洞解析器的关键纯函数边界：
  - _classify_hole_layout: 候选不足 / pool <2 / hull 剔除 / 间距否决 / D.6 否决
  - _parse_arrow_or_dir_token: 空值 / 方向字符 / 数值组合 / 非方向文本
  - _divide_multi_hole_zones: 洞内 / gap / per-hole margin / 外框外
  - _score_multi_hole_consistency: 非法外框 / 完美一致 / 空洞
  - _validate_multi_hole_geometry: 负值裁剪 / 边距上限 / 缺失反推
  - _round_pref_bonus_multi: 整数倍数加分
  - _multi_hole_spatial_bind: 空输入 / 排除字段 / bbox 去重 / 加权众数
  - _build_multi_hole_assignment: 空桶 / per-hole fallback / 方向锁定优先
  - _mh_check_deadline: None / 超时 / 未超时

无需 Tesseract / cv2 / 真实草图：所有输入均为合成数据。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from services.sketch_parser.sketch_parser_multihole import (
    _classify_hole_layout,
    _parse_arrow_or_dir_token,
    _divide_multi_hole_zones,
    _score_multi_hole_consistency,
    _validate_multi_hole_geometry,
    _round_pref_bonus_multi,
    _multi_hole_spatial_bind,
    _build_multi_hole_assignment,
    _mh_check_deadline,
)


# ---------------------------------------------------------------------------
# 合成数据构造器
# ---------------------------------------------------------------------------

def _rect(x, y, w, h, score=90):
    """构造 all_rects 元素 (x,y,w,h,score,area)。"""
    return (x, y, w, h, score, w * h)


# ===========================================================================
# _classify_hole_layout 边界
# ===========================================================================

def test_classify_empty_input():
    outer, inners, layout = _classify_hole_layout([])
    assert outer is None and inners == [] and layout == ''


def test_classify_too_few_rects():
    rects = [_rect(0, 0, 100, 100), _rect(10, 10, 30, 30)]
    outer, inners, layout = _classify_hole_layout(rects)
    assert outer is None and inners == [] and layout == ''


def test_classify_pool_less_than_two():
    # 1 外框 + 1 内框 → pool 仅 1 项 → 回退单洞
    rects = [_rect(0, 0, 100, 100), _rect(10, 10, 30, 30)]
    outer, inners, layout = _classify_hole_layout(rects)
    assert outer is None


def test_classify_valid_two_holes_horizontal():
    # 外框 1000x500, 两洞 300x400 横排
    rects = [
        _rect(0, 0, 1000, 500),         # outer
        _rect(100, 50, 300, 400),        # hole0
        _rect(600, 50, 300, 400),        # hole1
    ]
    outer, inners, layout = _classify_hole_layout(rects)
    assert outer is not None
    assert len(inners) == 2
    assert layout == 'horizontal'


def test_classify_hull_filtered():
    # 外框 + 联合 hull(含两洞) + 两洞 → hull 应被剔除
    rects = [
        _rect(0, 0, 1000, 500),         # outer (idx 0)
        _rect(50, 25, 900, 450),         # hull 含两洞 (idx 1)
        _rect(100, 50, 300, 400),        # hole0 (idx 2)
        _rect(600, 50, 300, 400),        # hole1 (idx 3)
    ]
    outer, inners, layout = _classify_hole_layout(rects)
    assert outer is not None
    # 应识别出两洞而非 hull
    assert len(inners) >= 2


def test_classify_gap_veto_same_hole_split():
    # 两"洞"几乎贴在一起（gap 极小）→ 否决为同洞分割
    outer_w, outer_h = 1000, 500
    rects = [
        _rect(0, 0, outer_w, outer_h),
        _rect(100, 50, 300, 400),        # hole0
        _rect(401, 50, 300, 400),        # hole1: gap=1px (远小于 veto 阈值)
    ]
    outer, inners, layout = _classify_hole_layout(
        rects, target_outer_w_cm=100.0, target_outer_h_cm=50.0)
    # gap=1px < hole_dim*0.05=15 AND < outer*0.02=20 → veto
    assert outer is None


# ===========================================================================
# _parse_arrow_or_dir_token 边界
# ===========================================================================

def test_arrow_token_empty():
    assert _parse_arrow_or_dir_token(None) == (None, None)
    assert _parse_arrow_or_dir_token('') == (None, None)
    assert _parse_arrow_or_dir_token('   ') == (None, None)


def test_arrow_token_direction_only():
    fld, val = _parse_arrow_or_dir_token('←')
    assert fld == 'margin_left' and val is None


def test_arrow_token_direction_plus_value():
    fld, val = _parse_arrow_or_dir_token('→46')
    assert fld == 'margin_right' and val == 46.0


def test_arrow_token_value_plus_direction():
    fld, val = _parse_arrow_or_dir_token('21.5↓')
    assert fld == 'margin_bottom' and val == 21.5


def test_arrow_token_chinese_direction():
    fld, val = _parse_arrow_or_dir_token('上6')
    assert fld == 'margin_top' and val == 6.0


def test_arrow_token_non_direction():
    fld, val = _parse_arrow_or_dir_token('hello')
    assert fld is None and val is None


def test_arrow_token_numeric_only():
    fld, val = _parse_arrow_or_dir_token('123.4')
    assert fld is None and val is None


# ===========================================================================
# _divide_multi_hole_zones 边界
# ===========================================================================

def _make_horizontal_zones():
    outer = (0, 0, 1000, 500)
    inners = [(100, 50, 300, 400), (600, 50, 300, 400)]
    return _divide_multi_hole_zones(outer, inners, 'horizontal', 1200, 600)


def test_zone_outside_outer_returns_none():
    zone_of = _make_horizontal_zones()
    assert zone_of(1100, 250) is None


def test_zone_inside_hole_returns_inner_dim():
    zone_of = _make_horizontal_zones()
    # hole0 中心 (250, 250)
    z = zone_of(250, 250)
    assert z in ('inner_w_0', 'inner_h_0')


def test_zone_in_gap():
    zone_of = _make_horizontal_zones()
    # gap 区域: x in [400, 600], y 在两洞 y 交集 [50, 450]
    z = zone_of(500, 250)
    assert z == 'gap_0_1'


def test_zone_outer_w_below_outer():
    zone_of = _make_horizontal_zones()
    # 外框正下方: cy > 500, cx in [0, 1000]
    z = zone_of(500, 550)
    assert z == 'outer_w'


def test_zone_outer_h_left_of_outer():
    zone_of = _make_horizontal_zones()
    # 外框正左方: cx < 0, cy in [0, 500]
    z = zone_of(-50, 250)
    assert z == 'outer_h'


def test_zone_per_hole_margin_top():
    zone_of = _make_horizontal_zones()
    # 洞0 正上方: x in [100, 400], y in [0, 50)
    z = zone_of(250, 25)
    assert z == 'margin_top_0'


def test_zone_shared_margin_left_0():
    zone_of = _make_horizontal_zones()
    # 最左洞左侧: x in [0, 100], y in [50, 450]
    z = zone_of(50, 250)
    assert z == 'margin_left_0'


# ===========================================================================
# _score_multi_hole_consistency 边界
# ===========================================================================

def test_score_invalid_outer_zero():
    sc = _score_multi_hole_consistency(0, 0, [], [], 'horizontal', 0, 0, 0, 0)
    assert sc == 0.0


def test_score_perfect_consistency():
    # 完美守恒: outer = ml + w0 + gap + w1 + mr = 2+10+2.5+12+2 = 28.5
    # 但 tw=28.5 → ratio=1.0; 等高; 全字段非零
    holes = [{'w': 10.0, 'h': 20.0}, {'w': 12.0, 'h': 20.0}]
    gaps = [2.5]
    sc = _score_multi_hole_consistency(
        28.5, 20.0, holes, gaps, 'horizontal',
        0.0, 0.0, 2.0, 2.0)
    assert sc > 0.8


def test_score_empty_holes():
    sc = _score_multi_hole_consistency(
        100.0, 100.0, [], [], 'horizontal', 0, 0, 0, 0)
    assert 0.0 <= sc <= 1.0


# ===========================================================================
# _validate_multi_hole_geometry 边界
# ===========================================================================

def test_validate_negative_values_clamped():
    asg = {
        'total_w': (100.0, 0.9), 'total_h': (50.0, 0.9),
        'margin_top': (-5.0, 0.5), 'margin_bottom': (-3.0, 0.5),
        'margin_left': (5.0, 0.5), 'margin_right': (5.0, 0.5),
        'inner_w_0': (40.0, 0.5), 'inner_h_0': (20.0, 0.5),
        'inner_w_1': (40.0, 0.5), 'inner_h_1': (20.0, 0.5),
        'gap_0_1': (5.0, 0.5),
    }
    result = _validate_multi_hole_geometry(asg, 2, 'horizontal')
    # 两个纵向边距均为负 → 裁剪到 0；missing=2 不触发反推 → 保持 0
    assert result['margin_top'][0] == 0.0
    assert result['margin_bottom'][0] == 0.0


def test_validate_margin_cap_at_90_percent():
    asg = {
        'total_w': (100.0, 0.9), 'total_h': (100.0, 0.9),
        'margin_top': (95.0, 0.5),   # > 90% of 100 → 裁剪到 90
        'margin_bottom': (5.0, 0.5),
        'margin_left': (5.0, 0.5),
        'margin_right': (5.0, 0.5),
        'inner_w_0': (40.0, 0.5), 'inner_h_0': (20.0, 0.5),
    }
    result = _validate_multi_hole_geometry(asg, 1, 'horizontal')
    assert result['margin_top'][0] <= 90.0


def test_validate_missing_single_value_reverse_derived():
    # 恰好缺失 margin_left → 反推
    asg = {
        'total_w': (100.0, 0.9), 'total_h': (50.0, 0.9),
        'margin_top': (5.0, 0.5), 'margin_bottom': (5.0, 0.5),
        'margin_left': (0.0, 0.3),   # 缺失
        'margin_right': (10.0, 0.5),
        'inner_w_0': (30.0, 0.5), 'inner_h_0': (20.0, 0.5),
        'inner_w_1': (40.0, 0.5), 'inner_h_1': (20.0, 0.5),
        'gap_0_1': (5.0, 0.5),
    }
    result = _validate_multi_hole_geometry(asg, 2, 'horizontal')
    # ml = 100 - (10 + 30 + 40 + 5) = 15
    assert result['margin_left'][0] == 15.0


# ===========================================================================
# _round_pref_bonus_multi 边界
# ===========================================================================

def test_round_bonus_non_integer():
    assert _round_pref_bonus_multi(10.5) == 0.0


def test_round_bonus_hundreds():
    assert _round_pref_bonus_multi(100.0) == 0.05


def test_round_bonus_fifties():
    assert _round_pref_bonus_multi(150.0) == 0.04


def test_round_bonus_tens():
    assert _round_pref_bonus_multi(30.0) == 0.03


def test_round_bonus_fives():
    assert _round_pref_bonus_multi(25.0) == 0.02


def test_round_bonus_other():
    assert _round_pref_bonus_multi(13.0) == 0.0


# ===========================================================================
# _multi_hole_spatial_bind 边界
# ===========================================================================

def test_spatial_bind_empty_ocr():
    def zone_of(cx, cy):
        return 'margin_top'
    buckets = _multi_hole_spatial_bind([], zone_of, set(), [], 2, 'horizontal')
    assert buckets == {}


def test_spatial_bind_excluded_fields_filtered():
    def zone_of(cx, cy):
        return 'margin_top'
    ocr = [(5.0, 90, (10, 10, 20, 20))]
    buckets = _multi_hole_spatial_bind(
        ocr, zone_of, {'margin_top'}, [], 2, 'horizontal')
    assert 'margin_top' not in buckets


def test_spatial_bind_excluded_values_filtered():
    def zone_of(cx, cy):
        return 'margin_top'
    ocr = [(5.0, 90, (10, 10, 20, 20))]
    buckets = _multi_hole_spatial_bind(
        ocr, zone_of, set(), [5.0], 2, 'horizontal')
    assert buckets.get('margin_top', []) == []


def test_spatial_bind_bbox_containment_dedup():
    # A 完全包含 B 且值不同 → B 被剔除
    def zone_of(cx, cy):
        return 'margin_top'
    ocr = [
        (11.5, 80, (10, 10, 100, 30)),   # A: 大 bbox
        (5.0, 90, (90, 20, 20, 10)),     # B: 小 bbox 完全在 A 内 → 拆读残片
    ]
    buckets = _multi_hole_spatial_bind(
        ocr, zone_of, set(), [], 2, 'horizontal')
    mt = buckets.get('margin_top', [])
    values = [round(v, 1) for v, _, _ in mt]
    assert 5.0 not in values
    assert 11.5 in values


def test_spatial_bind_weighted_mode_sorting():
    # 11.5 出现两次 (conf 81+86=167) > 5.0 单次 (conf 96) → 11.5 排首位
    def zone_of(cx, cy):
        return 'margin_top'
    ocr = [
        (5.0, 96, (10, 10, 20, 20)),
        (11.5, 81, (50, 10, 40, 20)),
        (11.5, 86, (50, 10, 40, 20)),
    ]
    buckets = _multi_hole_spatial_bind(
        ocr, zone_of, set(), [], 2, 'horizontal')
    mt = buckets['margin_top']
    assert round(mt[0][0], 1) == 11.5


# ===========================================================================
# _build_multi_hole_assignment 边界
# ===========================================================================

def test_assignment_empty_buckets():
    asg = _build_multi_hole_assignment(
        {}, {}, 100.0, 50.0, 2, 'horizontal')
    assert asg['total_w'] == (100.0, 0.7)
    assert asg['total_h'] == (50.0, 0.7)
    assert asg['margin_top'][0] == 0.0


def test_assignment_dir_locked_priority():
    dir_locked = {'margin_top': (15.0, 0.95, (10, 10, 20, 20))}
    buckets = {'margin_top': [(8.0, 80, (5, 5, 10, 10))]}
    asg = _build_multi_hole_assignment(
        dir_locked, buckets, 100.0, 50.0, 2, 'horizontal')
    # 方向锁定优先
    assert asg['margin_top'][0] == 15.0


def test_assignment_per_hole_fallback_to_global():
    # per-hole 桶空 → fallback 到全局桶
    buckets = {'margin_top': [(8.0, 80, (5, 5, 10, 10))]}
    asg = _build_multi_hole_assignment(
        {}, buckets, 100.0, 50.0, 2, 'horizontal')
    # margin_top_0 fallback 到 margin_top
    assert asg['margin_top_0'][0] == 8.0


# ===========================================================================
# _mh_check_deadline 边界
# ===========================================================================

def test_deadline_none_returns_none():
    assert _mh_check_deadline(None, 'test') is None


def test_deadline_not_passed_returns_none():
    future = time.monotonic() + 100.0
    assert _mh_check_deadline(future, 'test') is None


def test_deadline_passed_returns_fail():
    past = time.monotonic() - 1.0
    result = _mh_check_deadline(past, 'OCR')
    assert result is not None
    assert result['success'] is False
    assert '超时' in result['message']
