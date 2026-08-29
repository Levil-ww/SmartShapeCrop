"""多洞草图解析器验证测试（2026-08-29 新增）。

验证目标：
  1. 单洞路径零回归：原有 sketch_parser 单洞逻辑不被多洞扩展破坏
  2. 多洞布局检测：_classify_hole_layout 能正确分出 1 外框 + N 内框
  3. 多洞区域划分：_divide_multi_hole_zones 的 zone_of 正确分配
  4. 箭头方向绑定：_parse_arrow_or_dir_token 识别 ←→↑↓
  5. 多洞空间归属：_multi_hole_spatial_bind 桶分配正确
  6. 几何自洽评分：_score_multi_hole_consistency 打分合理
"""

from __future__ import annotations

import logging
import sys
import os
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# ---- 让 pytest 能直接 import 项目模块 ----
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# 0. 语法 & 导入 健康检查（零依赖，必通过）
# ---------------------------------------------------------------------------


def test_01_multihole_module_imports_cleanly():
    """新模块 import 时无异常（零文件/环境依赖）。"""
    from core.pool_designer import sketch_parser_multihole as mh
    assert hasattr(mh, '_classify_hole_layout')
    assert hasattr(mh, '_divide_multi_hole_zones')
    assert hasattr(mh, '_parse_arrow_or_dir_token')
    assert hasattr(mh, '_multi_hole_spatial_bind')
    assert hasattr(mh, '_score_multi_hole_consistency')
    assert hasattr(mh, '_validate_multi_hole_geometry')
    assert hasattr(mh, 'try_parse_multi_hole')
    assert hasattr(mh, 'HoleInfo')
    assert hasattr(mh, 'MultiHoleParseResult')
    logger.info("[T01] 多洞模块导入 OK")


def test_02_sketch_parser_new_fields_have_defaults():
    """SketchParseResult 新增字段都有默认值 → 旧代码实例化不出错。"""
    from core.pool_designer import SketchParseResult
    r = SketchParseResult()
    # 原有字段
    assert r.success is False
    assert r.outer_w_cm == 0.0
    assert r.inner_w_cm == 0.0
    assert r.debug == {}
    # 新增字段（默认值必须与单洞兼容）
    assert r.layout_type == "single"
    assert r.is_multi_hole is False
    assert r.holes == []
    assert r.hole_gaps_cm == []
    assert r.inner_rects_px == []
    logger.info("[T02] SketchParseResult 向后兼容 OK")


def test_03_package_init_exports():
    """__init__.py 新增导出符号可访问。"""
    from core.pool_designer import (
        HoleInfo,
        MultiHoleParseResult,
        try_parse_multi_hole,
    )
    # HoleInfo 实例化默认值
    h = HoleInfo()
    assert h.index == 0
    assert h.w_cm == 0.0
    assert h.rect_px == (0, 0, 0, 0)
    logger.info("[T03] 包导出 OK")


# ---------------------------------------------------------------------------
# 1. 多洞布局分类（纯逻辑）
# ---------------------------------------------------------------------------


def test_10_classify_two_horizontal_holes():
    """典型横排双洞：外框(0,0,500,200)，内洞A(50,40,150,120) 内洞B(300,40,150,120)。"""
    from core.pool_designer.sketch_parser_multihole import _classify_hole_layout

    # (x,y,w,h,score,area)，按面积降序
    all_rects = [
        (0,   0, 500, 200, 1.0, 500*200),   # 外框
        (300, 40, 150, 120, 0.9, 150*120),   # 洞B（右）
        (50,  40, 150, 120, 0.9, 150*120),   # 洞A（左）
        (210, 10, 10, 10, 0.1, 100),         # 噪点
    ]
    outer, inners, layout = _classify_hole_layout(all_rects)
    assert outer == (0, 0, 500, 200)
    assert len(inners) == 2
    assert layout == 'horizontal'
    # 排序后 inners[0] 应为左洞 (cx 较小)
    assert inners[0] == (50, 40, 150, 120)
    assert inners[1] == (300, 40, 150, 120)
    logger.info(f"[T10] classify OK: layout={layout} inners={inners}")


def test_11_classify_insufficient_innners_returns_empty():
    """只有 1 个内框 → 返回空（上层回退单洞）。"""
    from core.pool_designer.sketch_parser_multihole import _classify_hole_layout
    all_rects = [
        (0, 0, 500, 200, 1.0, 500*200),
        (100, 40, 300, 120, 0.9, 300*120),
    ]
    outer, inners, layout = _classify_hole_layout(all_rects)
    assert outer is None
    assert inners == []
    assert layout == ''
    logger.info("[T11] 内框不足时回退单洞 OK")


def test_12_classify_vertical_holes():
    """竖排双洞。"""
    from core.pool_designer.sketch_parser_multihole import _classify_hole_layout
    all_rects = [
        (0,   0, 200, 500, 1.0, 200*500),
        (40,  50, 120, 150, 0.9, 120*150),   # 上洞
        (40, 300, 120, 150, 0.9, 120*150),   # 下洞
    ]
    outer, inners, layout = _classify_hole_layout(all_rects)
    assert outer is not None
    assert len(inners) == 2
    assert layout == 'vertical'
    # 竖排按 cy 升序
    assert inners[0][1] < inners[1][1]
    logger.info(f"[T12] vertical layout OK: {inners}")


def test_13_classify_filters_combined_hull():
    """**复现用户真实场景**：6 矩形候选中混入 1 个「双洞联合包围盒 hull」。

    模拟结构（近似比例，实际以用户 PNG 为基准 350x59 外框 + 双 45x35.5 洞）：
      - 外框  (29,  56, 992, 387) → 面积 383k
      - hull  (92, 170, 747, 160) → 面积 119k (≈31%) — 包含两个真实洞
      - 右洞  (~500, 170, ~300, 160) → 面积 ~48k (真实 hole 1)
      - 左洞  (92,  170, ~280, 160) → 面积 ~45k (真实 hole 0)
      - 噪点 1/2
    预期：hull 被 Phase C 正确剔除，2 个真实洞被识别。
    """
    from core.pool_designer.sketch_parser_multihole import _classify_hole_layout

    def _area(x, y, w, h):
        return w * h

    OX, OY, OW, OH = 29, 56, 992, 387
    # 左洞
    LX, LY, LW, LH = 92, 170, 280, 160
    # 右洞（中间 gap 所以起点不是 LX+LW；给一个小间隙）
    GAP_PX = 50
    RX, RY, RW, RH = LX + LW + GAP_PX, 170, 300, 160
    # Hull 包围两者（注意 LX=92 是起点，RX+RW=722+300=1022 但是 OX+OW=1021 需
    #   要严格嵌套 → 让 hull 的 x+w <= OX+OW-1，即 1020）
    HX = LX
    HY = LY  # same y
    HW = (RX + RW) - LX - 2   # 缩 2px，避免碰到外框边界
    HH = LH

    # 按面积降序排列：外框 > hull > 右洞 > 左洞 > 噪点1 > 噪点2
    all_rects = sorted([
        (OX, OY, OW, OH, 1.0, _area(OX, OY, OW, OH)),
        (HX, HY, HW, HH, 0.9, _area(HX, HY, HW, HH)),
        (RX, RY, RW, RH, 0.9, _area(RX, RY, RW, RH)),
        (LX, LY, LW, LH, 0.9, _area(LX, LY, LW, LH)),
        (120, 80, 15, 15, 0.1, _area(120, 80, 15, 15)),     # 噪点1
        (900, 250, 12, 12, 0.1, _area(900, 250, 12, 12)),   # 噪点2
    ], key=lambda r: r[5], reverse=True)

    outer, inners, layout = _classify_hole_layout(all_rects)

    # 1) 必须识别出 2 洞（hull 被剔除）
    assert outer == (OX, OY, OW, OH), f"外框错误: {outer}"
    assert len(inners) == 2, f"洞数应为 2，实际 {len(inners)} (inners={inners})"
    assert layout == 'horizontal'

    # 2) inners 中不能含有 hull（hull x=92 w=HW 太大，真正左洞 w=LW=280）
    for (ix, iy, iw, ih) in inners:
        assert iw != HW, f"hull 未被剔除！仍有候选 xywh={(ix,iy,iw,ih)}"

    # 3) 按 cx 升序：左洞第一、右洞第二
    inners_by_x = sorted(inners, key=lambda r: r[0] + r[2] / 2)
    assert inners_by_x[0][0] == LX, f"左洞 x 应为 {LX}，实际 {inners_by_x[0]}"
    assert inners_by_x[1][0] == RX, f"右洞 x 应为 {RX}，实际 {inners_by_x[1]}"

    logger.info(f"[T13] hull 过滤+真实洞识别 OK: layout={layout} inners={inners}")


def test_14_classify_no_hull_scenarios_still_work():
    """Phase C 去 hull 后不足 2 项时的回退路径：只有 2 洞(无hull)+1噪点时，仍识别成功。

    验证：不因为 hull 检测逻辑而误伤「无 hull 的纯两洞场景」。
    """
    from core.pool_designer.sketch_parser_multihole import _classify_hole_layout

    # 与 T10 同构但面积比例稍大（两洞面积接近，hull 不存在）
    all_rects = [
        (0,  0,  1000, 400, 1.0, 400_000),
        (560, 80, 380, 240, 0.9, 380*240),   # 约 91k
        (60,  80, 380, 240, 0.9, 380*240),   # 约 91k
        (30, 370, 8, 8, 0.1, 64),            # 噪点
    ]
    outer, inners, layout = _classify_hole_layout(all_rects)
    assert outer is not None
    assert len(inners) == 2
    assert layout == 'horizontal'
    # 两洞均未被误删
    xs = {r[0] for r in inners}
    assert xs == {60, 560}, xs
    logger.info(f"[T14] 无hull回退路径 OK: inners={inners}")


# ---------------------------------------------------------------------------
# 2. 多洞区域划分 zone_of（纯逻辑）
# ---------------------------------------------------------------------------


def _make_horizontal_2_holes():
    outer = (0, 0, 500, 200)
    inners = [(50, 40, 150, 120), (300, 40, 150, 120)]
    layout = 'horizontal'
    return outer, inners, layout


def test_20_zone_shared_margins():
    """共享的 top/bottom 区域点。"""
    from core.pool_designer.sketch_parser_multihole import _divide_multi_hole_zones
    outer, inners, layout = _make_horizontal_2_holes()
    zone_of = _divide_multi_hole_zones(outer, inners, layout, 500, 200)
    # 共享 top: (cx=洞之间的 x, cy=10 在共享上距区)
    assert zone_of(250, 10) == 'margin_top'
    # 共享 bottom: (cx=洞之间的 x, cy=180 在共享下距区)
    assert zone_of(250, 180) == 'margin_bottom'
    logger.info("[T20] 共享区域判定 OK")


def test_21_zone_hole_0_margin_left_and_inside():
    """最左洞的左边距区 + 洞 0 内部。

    洞0矩形: (50, 40, 150, 120) → 中心 icx=125, icy=100
    横排规则:
      - cy > icy (下半部分)     → inner_w
      - cx < icx 且 cy ≤ icy   → inner_h
      - cx >= icx 且 cy ≤ icy  → inner_w
    """
    from core.pool_designer.sketch_parser_multihole import _divide_multi_hole_zones
    outer, inners, layout = _make_horizontal_2_holes()
    zone_of = _divide_multi_hole_zones(outer, inners, layout, 500, 200)
    # 洞 0 左侧外边距区 (cx 在外框到洞0左之间，y 在洞 0 范围内)
    assert zone_of(25, 100) == 'margin_left_0'
    # 洞 0 内部:
    #   (x=80, y=70): 左上方 (cx<icx=True, cy<icy) → inner_h_0
    #   (x=150, y=140): 右下方 (cy>icy=100) → inner_w_0
    assert zone_of(80, 70) == 'inner_h_0'
    assert zone_of(150, 140) == 'inner_w_0'
    logger.info("[T21] 洞0专属区+内部判定 OK")


def test_22_zone_hole_1_margin_right():
    """最右洞的右边距区。"""
    from core.pool_designer.sketch_parser_multihole import _divide_multi_hole_zones
    outer, inners, layout = _make_horizontal_2_holes()
    zone_of = _divide_multi_hole_zones(outer, inners, layout, 500, 200)
    # 洞 1 右侧 (cx=460 在外框到洞1右之间，y 在洞 1 范围内)
    assert zone_of(470, 100) == 'margin_right_1'
    logger.info("[T22] 洞1右外边距判定 OK")


def test_23_zone_gap_between_holes():
    """洞与洞之间的 gap 区。"""
    from core.pool_designer.sketch_parser_multihole import _divide_multi_hole_zones
    outer, inners, layout = _make_horizontal_2_holes()
    zone_of = _divide_multi_hole_zones(outer, inners, layout, 500, 200)
    # gap_0_1 的 x 范围: 洞0右=200 ~ 洞1左=300，y 在洞高交集内 (40~160)
    assert zone_of(250, 100) == 'gap_0_1'
    logger.info("[T23] gap 判定 OK")


def test_24_zone_outer_w_and_outer_h():
    """外框外侧标注区。"""
    from core.pool_designer.sketch_parser_multihole import _divide_multi_hole_zones
    outer, inners, layout = _make_horizontal_2_holes()
    zone_of = _divide_multi_hole_zones(outer, inners, layout, 500, 200)
    # outer_w: 外框底部正下方
    assert zone_of(250, 210) == 'outer_w'
    # outer_h: 外框左侧外部
    assert zone_of(-5, 100) == 'outer_h'
    logger.info("[T24] 外框外侧标注区 OK")


# ---------------------------------------------------------------------------
# 3. 箭头+方向字 token 解析（纯逻辑）
# ---------------------------------------------------------------------------


def test_30_arrow_chars_map_to_correct_fields():
    """各种箭头符号 → 边距字段。"""
    from core.pool_designer.sketch_parser_multihole import _parse_arrow_or_dir_token
    cases = [
        ('←21.5', 'margin_left', 21.5),
        ('→46',   'margin_right', 46.0),
        ('↑11.5', 'margin_top', 11.5),
        ('↓12',   'margin_bottom', 12.0),
        # 方向字 (原有能力)
        ('上11.5', 'margin_top', 11.5),
        ('下12',   'margin_bottom', 12.0),
        ('左21.5', 'margin_left', 21.5),
        ('右59',   'margin_right', 59.0),
        # 反向数值在前
        ('192.5→', 'margin_right', 192.5),
        ('35.5↓',  'margin_bottom', 35.5),
        ('11.5↑',  'margin_top', 11.5),
        ('21.5←',  'margin_left', 21.5),
    ]
    for text, exp_field, exp_val in cases:
        field, val = _parse_arrow_or_dir_token(text)
        assert field == exp_field, f"'{text}' → field={field}, 期望 {exp_field}"
        assert abs(val - exp_val) < 0.001, f"'{text}' → val={val}, 期望 {exp_val}"
    logger.info(f"[T30] 箭头/方向字 双向匹配 {len(cases)} 案例全部 OK")


def test_31_arrow_only_char_returns_none_val():
    """仅单独的箭头字符 → 返回 (field, None)，用于双 token 关联。"""
    from core.pool_designer.sketch_parser_multihole import _parse_arrow_or_dir_token
    assert _parse_arrow_or_dir_token('←') == ('margin_left', None)
    assert _parse_arrow_or_dir_token('→') == ('margin_right', None)
    assert _parse_arrow_or_dir_token('↑') == ('margin_top', None)
    assert _parse_arrow_or_dir_token('↓') == ('margin_bottom', None)
    logger.info("[T31] 纯箭头字符识别 OK")


def test_32_invalid_tokens_return_none():
    """无效文本返回 (None, None)。"""
    from core.pool_designer.sketch_parser_multihole import _parse_arrow_or_dir_token
    bad_cases = ['abc', '', '  ', 'hello world', 'XX123']
    for t in bad_cases:
        field, val = _parse_arrow_or_dir_token(t)
        assert field is None and val is None, f"'{t}' 应返回 None 对"
    logger.info("[T32] 无效文本过滤 OK")


# ---------------------------------------------------------------------------
# 4. 多洞几何自洽评分（纯逻辑）
# ---------------------------------------------------------------------------


def test_40_perfect_horizontal_consistency_scores_high():
    """完全自洽的横排双洞 → 高分。"""
    from core.pool_designer.sketch_parser_multihole import _score_multi_hole_consistency
    # outer 350 × 59（模拟用户图）
    # 洞 0: 77.5×45 (左距 21.5)，洞 1: 77.5×45 (右距 59)，gap=46
    # mt=11.5, mb=12
    # 横向守恒: 21.5 + 77.5 + 46 + 77.5 + 59 = 282? 让我们配成自洽 350
    tw, th = 350.0, 75.0
    holes = [{'w': 140.0, 'h': 45.0}, {'w': 140.0, 'h': 45.0}]
    gaps = [25.0]
    layout = 'horizontal'
    mt, mb = 10.0, 20.0   # 纵向: 10 + 45 + 20 = 75 ✓
    ml, mr = 10.0, 35.0   # 横向: 10 + 140 + 25 + 140 + 35 = 350 ✓
    sc = _score_multi_hole_consistency(tw, th, holes, gaps, layout, mt, mb, ml, mr)
    assert sc >= 0.85, f"完全自洽得分应≥0.85，实际 {sc:.3f}"
    logger.info(f"[T40] 完全自洽横排双洞 sc={sc:.3f}")


def test_41_missing_fields_score_lower():
    """缺失部分字段 → 分数低于完全自洽。"""
    from core.pool_designer.sketch_parser_multihole import _score_multi_hole_consistency
    # 同上但缺失 gap 值
    tw, th = 350.0, 75.0
    holes = [{'w': 140.0, 'h': 45.0}, {'w': 140.0, 'h': 0.0}]
    gaps = [0.0]
    layout = 'horizontal'
    mt, mb = 10.0, 20.0
    ml, mr = 10.0, 35.0
    sc = _score_multi_hole_consistency(tw, th, holes, gaps, layout, mt, mb, ml, mr)
    # 至少不会崩溃，且分数 >0
    assert 0.0 < sc < 1.0
    logger.info(f"[T41] 缺失字段 sc={sc:.3f}（非崩溃）")


def test_42_zero_outer_scores_zero():
    """外框无效 → 0 分。"""
    from core.pool_designer.sketch_parser_multihole import _score_multi_hole_consistency
    sc = _score_multi_hole_consistency(0, 0, [], [], 'horizontal', 0, 0, 0, 0)
    assert sc == 0.0
    logger.info("[T42] 零外框正确判 0 分")


# ---------------------------------------------------------------------------
# 5. 空间归属桶分配（纯逻辑）
# ---------------------------------------------------------------------------


def test_50_spatial_bind_populates_buckets_correctly():
    """根据坐标把 OCR 候选分到正确的桶。"""
    from core.pool_designer.sketch_parser_multihole import (
        _divide_multi_hole_zones,
        _multi_hole_spatial_bind,
    )
    outer, inners, layout = _make_horizontal_2_holes()
    zone_of = _divide_multi_hole_zones(outer, inners, layout, 500, 200)
    # 构造 OCR 候选：(val, conf, (x,y,w,h))
    ocr = [
        (10.0, 95, (240, 5, 20, 10)),   # 共享 top
        (20.0, 95, (240, 185, 20, 10)), # 共享 bottom
        (10.0, 90, (5, 90, 20, 20)),    # margin_left_0
        (35.0, 90, (460, 90, 20, 20)),  # margin_right_1
        (140.0, 92, (100, 130, 30, 15)),  # hole0 w (下半部)
        (45.0, 92, (100, 70, 20, 15)),    # hole0 h (上半部)
        (25.0, 88, (240, 95, 20, 10)),    # gap_0_1
    ]
    buckets = _multi_hole_spatial_bind(
        ocr, zone_of, excluded_fields=set(), excluded_values=[],
        n_holes=2, layout=layout)
    # 关键桶应命中
    assert 'margin_top' in buckets
    assert 'margin_bottom' in buckets
    assert 'margin_left_0' in buckets
    assert 'margin_right_1' in buckets
    assert 'inner_w_0' in buckets
    assert 'inner_h_0' in buckets
    assert 'gap_0_1' in buckets
    logger.info(f"[T50] 桶分配 OK: 字段={list(buckets.keys())}")


# ---------------------------------------------------------------------------
# 6. 多洞几何约束修正（纯逻辑）
# ---------------------------------------------------------------------------


def test_60_derive_missing_horizontal():
    """横排双洞：缺失一个横向值 → 可正确反推。"""
    from core.pool_designer.sketch_parser_multihole import _validate_multi_hole_geometry
    assignment = {
        'total_w': (350.0, 0.7),
        'total_h': (75.0, 0.7),
        'margin_top': (10.0, 0.7),
        'margin_bottom': (20.0, 0.7),
        'margin_left': (10.0, 0.7),
        'margin_right': (0.0, 0.3),       # 缺失，将反推
        'inner_w_0': (140.0, 0.7),
        'inner_h_0': (45.0, 0.7),
        'inner_w_1': (140.0, 0.7),
        'inner_h_1': (45.0, 0.7),
        'gap_0_1': (25.0, 0.7),
    }
    fixed = _validate_multi_hole_geometry(assignment, n_holes=2, layout='horizontal',
                                           target_outer_w=350.0, target_outer_h=75.0)
    # mr 应该被反推出: 350 - 10 - 140 - 25 - 140 = 35
    mr_val = fixed['margin_right'][0]
    assert abs(mr_val - 35.0) < 0.1, f"反推 mr={mr_val:.1f} 期望 35.0"
    # 纵向：洞高均值填充 + 反推（这里 mt+ih+mb 已自洽）
    mt_val = fixed['margin_top'][0]
    mb_val = fixed['margin_bottom'][0]
    ih_val = fixed['inner_h_0'][0]
    assert abs(mt_val + ih_val + mb_val - 75.0) < 0.5, f"纵向自洽偏差: {mt_val}+{ih_val}+{mb_val} vs 75"
    logger.info(f"[T60] 几何约束修正 OK: mr={mr_val:.1f} 纵向={mt_val}+{ih_val}+{mb_val}")


# ---------------------------------------------------------------------------
# 7. try_parse_multi_hole 对单洞草图正确 fallback（关键回归测试）
# ---------------------------------------------------------------------------


def test_70_multihole_entry_graceful_fallback_on_bad_path():
    """不存在的文件 → 失败（不抛异常）。"""
    from core.pool_designer.sketch_parser_multihole import try_parse_multi_hole
    result = try_parse_multi_hole("/this/path/does/not/exist/sketch.png")
    assert result.get('success') is False
    assert '不存在' in result.get('message', '') or result.get('_fallback_to_single_hole') is True
    logger.info(f"[T70] 坏路径优雅处理: msg={result.get('message','')[:50]}")


def test_71_parse_sketch_backward_compatible_syntax():
    """主入口 parse_sketch 新老字段默认值完整（不启动 cv2/tesseract）。

    单洞路径的原有代码行为必须完全保持：
      - 当 validate_sketch_file 失败时返回的 SketchParseResult 与修改前一致
      - 多洞分流的 try/except 包住的代码不会影响早期 return
    """
    from core.pool_designer import parse_sketch
    r = parse_sketch("definitely_no_such_file.png")
    assert r.success is False
    # 新字段的默认值也必须正确（单洞场景为默认）
    assert r.is_multi_hole is False
    assert r.layout_type == "single"
    assert r.holes == []
    assert r.hole_gaps_cm == []
    assert r.inner_rects_px == []
    logger.info(f"[T71] parse_sketch 坏文件返回结果完全兼容: is_multi={r.is_multi_hole}")


# ---------------------------------------------------------------------------
# 8. 桶选策略：几何包含去重 + 加权众数（真实场景 11.5 vs 5.0 拆读回归）
# ---------------------------------------------------------------------------


def test_80_geometric_containment_drops_split_read_5():
    """T15 复现用户 mt=5.0 的根因：同 field 中 11.5@(708,114,29,13) bbox 完全
    包含 5.0@(730,114,7,13)。Post A 应剔除 5.0。"""
    from core.pool_designer.sketch_parser_multihole import _multi_hole_spatial_bind

    # 两个候选：11.5 (大bbox) + 5.0 (被完全包含的小bbox)
    ocr_results = [
        # val, conf, bbox=(x, y, w, h)
        (5.0, 96, (730, 114, 7, 13)),
        (11.5, 81, (708, 114, 29, 13)),
        (11.5, 86, (228, 127, 29, 13)),   # 另一个洞上方的 11.5（不包含）
    ]

    # 让所有候选落入 margin_top 字段的 zone_func
    def zone_func(cx, cy):
        return 'margin_top'

    buckets = _multi_hole_spatial_bind(
        ocr_results, zone_func, set(), [], 2, 'horizontal')
    assert 'margin_top' in buckets
    vals = [round(v, 1) for v, c, bb in buckets['margin_top']]
    # 5.0 必须被剔除
    assert 5.0 not in vals, f"拆读 5.0 未被剔除！vals={vals}"
    # 11.5 保留 2 个
    assert vals.count(11.5) == 2, f"应保留两个 11.5，实际 {vals}"
    # Top 必须是 11.5（Post B 按众数加权排第一）
    assert round(buckets['margin_top'][0][0], 1) == 11.5
    logger.info(f"[T80] 几何包含去重+众数投票 OK: top={vals[0]} full={vals}")


def test_81_weighted_majority_beats_singleton_high_conf():
    """T15 补充：不满足几何包含时，仅众数投票也能让双 11.5(sum conf=167) 胜过 单 5.0(96)。"""
    from core.pool_designer.sketch_parser_multihole import _multi_hole_spatial_bind

    ocr_results = [
        # 位置都在不同处（没有包含关系），但 11.5 有 2 条总 conf=167 高于 5.0 单条 conf=96
        (5.0, 96, (730, 114, 7, 13)),
        (11.5, 81, (708, 100, 29, 13)),
        (11.5, 86, (228, 100, 29, 13)),
    ]

    def zone_func(cx, cy):
        return 'margin_top'

    buckets = _multi_hole_spatial_bind(
        ocr_results, zone_func, set(), [], 2, 'horizontal')
    mt_list = buckets.get('margin_top', [])
    assert len(mt_list) == 3, f"应保留3项(无人被Post-A几何剔除)：实际 {len(mt_list)}"
    # Top 必须是 11.5（sum 167 赢 5.0 的 96）
    assert round(mt_list[0][0], 1) == 11.5, \
        f"众数投票失败！Top={mt_list[0][0]}, list={[(round(v,1),c) for v,c,_ in mt_list]}"
    logger.info(f"[T81] 加权众数 OK: top={round(mt_list[0][0],1)}")


# ---------------------------------------------------------------------------
# 9. 多洞 mask UNION 渲染（image_ops Add-On 验证）
# ---------------------------------------------------------------------------


def test_90_multi_hole_mask_union_basic():
    """T16: CropDesign pool_holes_cm>=2 时，_get_inner_pixel_mask 返回 UNION mask。

    场景：画布 351×60cm @ DPI=72；2 横排洞各 45×35.5，共享 mt=11.5 mb=12 ml=21.5
         gap=46 outer=350。验证 mask 上两个区域分别为 True，gap 中心为 False
         （未合并成单洞大区域）。
    """
    try:
        from core.geometry import CropDesign
        from core.image_ops import _get_inner_pixel_mask
    except Exception:
        pytest.skip("本地 image_ops/geometry 不可用")

    d = CropDesign()
    d.canvas_w_cm = 351.0   # outer 350 + TRIM_CM 1
    d.canvas_h_cm = 60.0    # outer 59 + TRIM_CM 1
    d.dpi = 72              # 低 DPI 加速测试
    d.mode = 'rect_hole'
    d.outer_margin_cm = 0.0
    # 兼容边距（单洞字段）
    d.inner_margin_top_cm = 11.5
    d.inner_margin_bottom_cm = 12.0
    d.inner_margin_left_cm = 21.5
    d.inner_margin_right_cm = 192.5
    # ===== 多洞 Add-On 字段（纯加） =====
    d.pool_is_multi_hole = True
    mt_abs = 0.0 + 11.5
    h_h = 35.5
    x0 = 0.0 + 21.5
    d.pool_holes_cm = [
        {'x_cm': x0,            'y_cm': mt_abs, 'w_cm': 45.0, 'h_cm': h_h},
        {'x_cm': x0 + 45 + 46,  'y_cm': mt_abs, 'w_cm': 45.0, 'h_cm': h_h},
    ]
    d.pool_holes_gaps_cm = [46.0]

    mask = _get_inner_pixel_mask(d)

    import numpy as np
    assert mask.dtype == bool
    n_true = int(np.count_nonzero(mask))
    W, H = d.canvas_w_px, d.canvas_h_px
    assert n_true > 0, "mask 中没有 True 区域！"
    assert n_true < W * H, "mask 全 True（整张布）！"

    # gap 正中心应为 False（两洞之间不挖空）
    gap_cm_center_x = x0 + 45.0 + 23.0  # 21.5+45+23=89.5
    y_center_cm = mt_abs + h_h / 2
    gx = int(round(d.cm2px(gap_cm_center_x)))
    gy = int(round(d.cm2px(y_center_cm)))
    if 0 <= gx < W and 0 <= gy < H:
        assert bool(mask[gy, gx]) is False, \
            f"gap 中心 ({gx},{gy}) 应为 False（不挖），实际为 True！两洞合并成单洞大区域了！"

    # 双洞中心应为 True
    for hc in d.pool_holes_cm:
        cx_px = int(round(d.cm2px(hc['x_cm'] + hc['w_cm'] / 2)))
        cy_px = int(round(d.cm2px(hc['y_cm'] + hc['h_cm'] / 2)))
        if 0 <= cx_px < W and 0 <= cy_px < H:
            assert bool(mask[cy_px, cx_px]) is True, \
                f"洞中心 ({cx_px},{cy_px}) 应为 True（挖洞），实际 False！"

    logger.info(f"[T90] 多洞 UNION mask OK: WxH={W}x{H} true_pixels={n_true}")


def test_91_multi_hole_empty_defaults_still_single_hole_code_path():
    """T16 补充：默认值（pool_holes_cm=[] 或 pool_is_multi_hole=False）
    时仍走单洞分支（旧代码路径），Add-On 不触发。

    验证：不抛异常，且行为等价于旧版单洞（inner_rect 区域为 True）。
    """
    try:
        from core.geometry import CropDesign
        from core.image_ops import _get_inner_pixel_mask
    except Exception:
        pytest.skip("本地 image_ops/geometry 不可用")

    d = CropDesign()
    d.canvas_w_cm = 351.0
    d.canvas_h_cm = 60.0
    d.dpi = 72
    d.mode = 'rect_hole'
    d.outer_margin_cm = 0.0
    d.inner_margin_top_cm = 11.5
    d.inner_margin_bottom_cm = 12.0
    d.inner_margin_left_cm = 21.5
    d.inner_margin_right_cm = 192.5
    # pool_is_multi_hole = False（默认）且 pool_holes_cm = []（默认）
    mask = _get_inner_pixel_mask(d)
    import numpy as np
    assert mask.dtype == bool
    # 单洞路径的洞中心应为 True
    ir = d.inner_rect_px()
    cx_px = int(round(ir.x + ir.w / 2))
    cy_px = int(round(ir.y + ir.h / 2))
    W, H = d.canvas_w_px, d.canvas_h_px
    if 0 <= cx_px < W and 0 <= cy_px < H:
        assert bool(mask[cy_px, cx_px]) is True, "单洞路径应保留 inner_rect 区域为 True"
    logger.info(f"[T91] 单洞默认路径零回归: true_pixels={int(np.count_nonzero(mask))}")


def test_92_multi_hole_border_each_hole_four_sides_10px():
    """T92: 多洞模式下，render_layout_pillow 输出必须为每洞绘制完整 4 边 10px 黑框。

    重现用户真实场景：outer=350×59 cm；2 横排洞 45×35.5 cm；mt=11.5 mb=12 ml=21.5 gap=46 mr=192.5。
    DPI 取 72（10px border 在 72dpi 时仍然约 10 像素，验证足够）。

    断言 (每洞独立)：
    (A) 洞 top 边沿水平线上、left 边沿竖直线、right 边沿竖直线、bottom 边沿水平线
        —— 这些位置的像素应全为 BLACK (0,0,0)。
    (B) 洞内部向内 2 个 border_width 像素的中心像素 必须 不是 BLACK（内部白）。
    """
    try:
        import numpy as np
        from core.geometry import CropDesign
        from core.image_ops import render_design
    except Exception as e:  # pragma: no cover
        pytest.skip(f"本地模块导入失败: {e}")

    d = CropDesign()
    d.canvas_w_cm = 351.0
    d.canvas_h_cm = 60.0
    d.dpi = 72
    d.mode = 'rect_hole'
    d.outer_margin_cm = 0.0
    d.inner_margin_top_cm = 11.5
    d.inner_margin_bottom_cm = 12.0
    d.inner_margin_left_cm = 21.5
    d.inner_margin_right_cm = 192.5
    d.pool_is_multi_hole = True
    x0 = 21.5
    d.pool_holes_cm = [
        {'x_cm': x0,             'y_cm': 11.5, 'w_cm': 45.0, 'h_cm': 35.5},
        {'x_cm': x0 + 45 + 46,   'y_cm': 11.5, 'w_cm': 45.0, 'h_cm': 35.5},
    ]
    d.pool_holes_gaps_cm = [46.0]

    img = render_design(d, quality='preview', pixel_scale=1.0)
    # --- PURE PIL SAMPLING (bypass PIL+numpy interface env quirks) ---
    rgb_img = img.convert('RGB')
    W_arr, H_arr = rgb_img.size   # PIL size = (W, H)

    # pixel_scale=1.0 → bw = max(2, ceil(10*1)) = 10
    bw = 10

    def px(cm_):
        return int(round(d.cm2px(cm_)))

    for i, hc in enumerate(d.pool_holes_cm):
        x1 = px(hc['x_cm']); y1 = px(hc['y_cm'])
        x2 = px(hc['x_cm'] + hc['w_cm']); y2 = px(hc['y_cm'] + hc['h_cm'])

        # 采样 5 点；纯 PIL getpixel，避免 numpy striding 造成的环境误判
        n = 5
        xs = np.linspace(x1, x2 - 1, n).astype(int)
        xs = np.clip(xs, 0, W_arr - 1)
        ys_top    = np.full(n, y1,       dtype=int)
        ys_bottom = np.full(n, y2 - 1,   dtype=int)
        ys_vert   = np.linspace(y1, y2 - 1, n).astype(int)
        ys_vert   = np.clip(ys_vert, 0, H_arr - 1)
        xs_left   = np.full(n, x1,       dtype=int)
        xs_right  = np.full(n, x2 - 1,   dtype=int)

        def _rgbs(xs_, ys_):
            return [rgb_img.getpixel((int(x), int(y))) for x, y in zip(xs_, ys_)]

        BLACK = (0, 0, 0)
        top_rgbs   = _rgbs(xs, ys_top)
        bot_rgbs   = _rgbs(xs, ys_bottom)
        left_rgbs  = _rgbs(xs_left, ys_vert)
        right_rgbs = _rgbs(xs_right, ys_vert)

        top_ok   = all(p == BLACK for p in top_rgbs)
        bot_ok   = all(p == BLACK for p in bot_rgbs)
        left_ok  = all(p == BLACK for p in left_rgbs)
        right_ok = all(p == BLACK for p in right_rgbs)

        assert top_ok,   (f"洞{i+1} 上边沿采样未全黑 (x={x1}..{x2}, y={y1}); "
                          f"Image W={W_arr} H={H_arr}; actual rgbs={top_rgbs}")
        assert bot_ok,   f"洞{i+1} 下边沿采样未全黑 (x={x1}..{x2}, y={y2-1}); actual rgbs={bot_rgbs}"
        assert left_ok,  f"洞{i+1} 左边沿采样未全黑 (x={x1}, y={y1}..{y2}); actual rgbs={left_rgbs}"
        assert right_ok, f"洞{i+1} 右边沿采样未全黑 (x={x2-1}, y={y1}..{y2}); actual rgbs={right_rgbs}"

        # (B) 中心：向内 2*bw 像素以上，绝对不能 BLACK
        cx = px(hc['x_cm'] + hc['w_cm'] / 2)
        cy = px(hc['y_cm'] + hc['h_cm'] / 2)
        cx = int(np.clip(cx, 0, W_arr - 1))
        cy = int(np.clip(cy, 0, H_arr - 1))
        center_rgb = rgb_img.getpixel((cx, cy))
        assert center_rgb != BLACK, \
            f"洞{i+1} 中心像素 ({cx},{cy})={center_rgb} 为 BLACK，疑似边框溢出或 hole 整体未画白"

    logger.info(f"[T92] 多洞每洞 4 边 10px 黑框 OK ({bw}px)，每洞中心均非 BLACK ✓")


def test_93_user_multihole_params_override_sketch_in_worker():
    """T93: 多洞 UI 用户修改的洞宽/洞高/间距，经 PoolRenderWorker 正确覆盖 sketch 值并重算坐标。

    模拟流程（纯后台，不启动 GUI）：
    1) 构造假的 TemplateMatcher（外框素材匹配）—— 用 tempfile + 1x1 占位图；
    2) 构造假的 MultiHoleParseResult sketch_result（通过 pre_parsed_result 注入 Worker 避免真实 parse_sketch）；
    3) Worker 启用 user_multihole_params：洞1 w/h、洞2 w/h、间距1↔2 都人为改到不同值；
    4) Worker 输出 design.pool_holes_cm 长度==2，每个洞的 w/h/间距 与 UI 值严格一致，
       且 x_cm 的连续性成立（ml + w1 + gap12 + w2 + mr == canvas_w）。

    这覆盖的契约是：多洞参数面板值必须"像单洞边距一样"经过 Worker 进入 design 数据。
    """
    import os, tempfile
    try:
        from PyQt5.QtWidgets import QApplication  # noqa: F401
    except Exception as e:  # pragma: no cover
        pytest.skip(f"PyQt5 不可用: {e}")
    try:
        from core.geometry import CropDesign
        from core.parser.template_matcher import TemplateMatcher
        from core.pool_designer import HoleInfo, MultiHoleParseResult
        from gui.property_panel_workers import PoolRenderWorker
    except Exception as e:  # pragma: no cover
        pytest.skip(f"模块导入失败: {e}")

    # —— 构造假模板库目录（采用 test_template_matcher.py 验证通过的 ;竖版WxHcm 形态）——
    with tempfile.TemporaryDirectory() as tpl_dir:
        from PIL import Image as _PILImage
        # 与核心测试用例相同的"产品-...-花型;竖版WxHcm.jpg"解析格式，保证 TemplateMatcher 100% 命中。
        outer_p = os.path.join(tpl_dir, "双面草-定制-定制尺寸-安妮森林;竖版236x484cm.jpg")
        _PILImage.new('RGB', (100, 50), (128, 128, 128)).save(outer_p, 'JPEG', quality=90)

        matcher = TemplateMatcher()
        matcher.set_template_dir(tpl_dir)
        matcher.scan_library(force=True)

        # —— 构造假 sketch_result：2 横排洞，outer=247x50cm（与图4示例接近）——
        outer_w = 247.0
        outer_h = 50.0
        h1 = HoleInfo(index=0, w_cm=50.0, h_cm=17.0,
                      margin_top_cm=10.0, margin_bottom_cm=23.0,
                      margin_left_cm=36.0, margin_right_cm=0.0)
        h2 = HoleInfo(index=1, w_cm=58.0, h_cm=26.0,
                      margin_top_cm=10.0, margin_bottom_cm=15.0,
                      margin_left_cm=0.0,  margin_right_cm=78.0)
        sr = MultiHoleParseResult()
        sr.success = True
        sr.message = "fake multihole"
        sr.outer_w_cm = outer_w
        sr.outer_h_cm = outer_h
        sr.is_multi_hole = True
        sr.layout_type = 'horizontal'
        sr.holes = [h1, h2]
        sr.hole_gaps_cm = [20.0]
        # 补齐 SketchParseResult 兼容字段（PoolWorker 全局边距优先使用这些）
        sr.margin_top_cm = 10.0
        sr.margin_bottom_cm = 15.0
        sr.margin_left_cm = 36.0
        sr.margin_right_cm = 78.0

        # UI 修改：洞1→54x18，洞2→61x26，间距1↔2→19
        # （正好对齐用户图4上：洞1=54×18，洞2=61×26，间距=19 cm）
        ump = {
            'active_count': 2,
            'holes_wh': [(54.0, 18.0), (61.0, 26.0)],
            'gaps_cm': [19.0],
            'layout_type': 'horizontal',
        }

        # 构造一个真实存在的临时草图文件（哪怕空文件也行），让 Worker L150 条件 True，
        # 从而进入「使用界面已解析的草图结果」分支，把 pre_parsed_result 赋给 sketch_result。
        # 这与真实 GUI 行为一致：用户点击按钮时 sketch_path 几乎总是存在的。
        import tempfile as _tf
        _sketch_fh, _sketch_path = _tf.mkstemp(suffix='.jpg', prefix='t93_fake_sketch_')
        os.close(_sketch_fh)
        with open(_sketch_path, 'wb') as _f:
            # 写一个最小有效 JPEG（1x1 白图），避免 parse_sketch 在极端 fallback 场景下直接 IOError
            from PIL import Image as _PI2
            _PI2.new('RGB', (10, 10), (255, 255, 255)).save(_f, 'JPEG', quality=90)
        try:
            # target 文件名：采用同"花型;横版WxHcm后缀"解析格式，与 outer=247×50（宽>高 横版）一致。
            target = "双面草-定制-定制尺寸-安妮森林;横版248x51cm"
            worker = PoolRenderWorker(
                matcher=matcher,
                template_dir=tpl_dir,
                target_filename=target,
                sketch_path=_sketch_path,   # 真实文件，触发 pre_parsed_result 赋值
                pre_parsed_result=sr,
                user_margins=None,
                user_multihole_params=ump,
            )
            # 直接同步 run()，不走 QThread.start() 事件循环
            ok_results: list[tuple] = []
            err_results: list[str] = []
            worker.finished_ok.connect(lambda d, s, t: ok_results.append((d, s, t)))
            worker.finished_err.connect(lambda m: err_results.append(m))
            worker.run()

            assert not err_results, f"PoolRenderWorker 意外失败: {err_results}"
            assert ok_results, "Worker 未发射 finished_ok"
            design, sketch_result, _log_txt = ok_results[0]
            d = design  # type: CropDesign

            # —— 核心断言：UI 修改已写入 design.pool_holes_cm ——
            assert getattr(d, 'pool_is_multi_hole', False) is True
            assert len(d.pool_holes_cm) == 2, f"洞数应为2，实际={len(d.pool_holes_cm)}"
            assert d.pool_holes_gaps_cm == [19.0], f"间距覆盖失败: {d.pool_holes_gaps_cm}"
            h1c, h2c = d.pool_holes_cm
            # w/h 必须是 UI 值（Worker 不应该再偷偷 +1 TRIM_CM ——
            #   UI 已经直接填入与 sketch 扩展后一致的量级，因此相等即可）
            assert h1c['w_cm'] == pytest.approx(54.0, abs=0.01), f"洞1宽未覆盖: {h1c['w_cm']}"
            assert h1c['h_cm'] == pytest.approx(18.0, abs=0.01), f"洞1高未覆盖: {h1c['h_cm']}"
            assert h2c['w_cm'] == pytest.approx(61.0, abs=0.01), f"洞2宽未覆盖: {h2c['w_cm']}"
            assert h2c['h_cm'] == pytest.approx(26.0, abs=0.01), f"洞2高未覆盖: {h2c['h_cm']}"

            # 几何自洽：横排的 x 必须连续 ——
            #   x1 = ml ; x2 = x1 + w1 + gap ; 最后 x2 + w2 + mr == canvas_w
            TRIM_CM = 1.0  # 与 Worker 一致：canvas = outer + 1cm
            canvas_w_expected = outer_w + TRIM_CM
            assert d.canvas_w_cm == pytest.approx(canvas_w_expected, abs=0.01)
            x1_expected = d.inner_margin_left_cm        # 36.0
            x2_expected = x1_expected + 54.0 + 19.0      # 109.0
            assert h1c['x_cm'] == pytest.approx(x1_expected, abs=0.01), (
                f"洞1 x 错误: {h1c['x_cm']}, 期望 {x1_expected}")
            assert h2c['x_cm'] == pytest.approx(x2_expected, abs=0.01), (
                f"洞2 x 错误: {h2c['x_cm']}, 期望 {x2_expected}")
            total = h2c['x_cm'] + h2c['w_cm'] + d.inner_margin_right_cm
            assert total == pytest.approx(d.canvas_w_cm, abs=0.01), (
                f"横排不自洽: x2+w2+mr={total} != canvas_w={d.canvas_w_cm}")

            # per-hole y：mt=10 时 y1=y2=10.0（因为两洞 mt=10）
            assert h1c['y_cm'] == pytest.approx(10.0, abs=0.01), f"洞1 y 错误: {h1c['y_cm']}"
            # y2 独立边距： sketch h2.margin_top=10（与全局一致）
            assert h2c['y_cm'] == pytest.approx(10.0, abs=0.01), f"洞2 y 错误: {h2c['y_cm']}"

            # 每洞素材匹配查询输入：与 pool_holes_cm 解耦（在 _on_pool_finished_ok 内部读取）
            # 这里至少保证洞素材字段是可写、且 w/h 可用作查询尺寸。
            d.pool_holes_cm[0]['inner_material_path'] = '洞1-54x18-fake.jpg'
            d.pool_holes_cm[1]['inner_material_path'] = '洞2-61x26-fake.jpg'
            assert '54x18' in d.pool_holes_cm[0]['inner_material_path']
            assert '61x26' in d.pool_holes_cm[1]['inner_material_path']
            logger.info("[T93] 多洞UI→Worker 覆盖 + 几何自洽 OK ✓")
        finally:
            try:
                os.unlink(_sketch_path)
            except Exception:
                pass



def test_94_multihole_none_single_hole_no_impact():
    """T94: user_multihole_params=None（等价 GroupBox 隐藏的单洞场景）→ 零影响。

    与 T93 相同骨架，但不传入 user_multihole_params；且 sr.is_multi_hole=False
    模拟纯单洞草图，验证：
      - pool_is_multi_hole == False
      - pool_holes_cm == []（默认空）
      - Worker 正常走旧单洞分支：inner=canvas-margins 成立。
    """
    import os, tempfile
    try:
        from PyQt5.QtWidgets import QApplication  # noqa: F401
    except Exception as e:  # pragma: no cover
        pytest.skip(f"PyQt5 不可用: {e}")
    try:
        from core.geometry import CropDesign
        from core.parser.template_matcher import TemplateMatcher
        from core.pool_designer import SketchParseResult
        from gui.property_panel_workers import PoolRenderWorker
    except Exception as e:  # pragma: no cover
        pytest.skip(f"模块导入失败: {e}")

    with tempfile.TemporaryDirectory() as tpl_dir:
        from PIL import Image as _PILImage
        # outer=133×60.5 是宽>高的横版图；文件名用 ";横版133x60.5cm" 匹配方向，
        # 避免 parse_filename 二次 swap 导致 canvas 尺寸反了。
        outer_p = os.path.join(tpl_dir, "吸水皮革-定制-定制尺寸-庄园秘境;横版133x60.5cm.jpg")
        _PILImage.new('RGB', (100, 50), (100, 100, 100)).save(outer_p, 'JPEG', quality=90)

        matcher = TemplateMatcher()
        matcher.set_template_dir(tpl_dir)
        matcher.scan_library(force=True)

        outer_w, outer_h = 133.0, 60.5
        sr = SketchParseResult()
        sr.success = True
        sr.message = "fake single"
        sr.outer_w_cm = outer_w
        sr.outer_h_cm = outer_h
        sr.inner_w_cm = 60.5 - 10.0 - 10.0  # placeholder
        sr.inner_h_cm = 133.0 - 10.0 - 10.0
        sr.margin_top_cm = 9.5
        sr.margin_bottom_cm = 9.5
        sr.margin_left_cm = 6.5
        sr.margin_right_cm = 6.5
        sr.is_multi_hole = False
        sr.layout_type = 'single'
        sr.holes = []
        sr.hole_gaps_cm = []

        # 与 T93 同样修法：给一个真实临时草图文件路径，确保 pre_parsed_result 被使用。
        import tempfile as _tf2
        _sk2_fh, _sk2_path = _tf2.mkstemp(suffix='.jpg', prefix='t94_fake_sketch_')
        os.close(_sk2_fh)
        with open(_sk2_path, 'wb') as _f2:
            from PIL import Image as _PI3
            _PI3.new('RGB', (10, 10), (255, 255, 255)).save(_f2, 'JPEG', quality=90)
        try:
            # 不传 user_multihole_params（=None），且草图本身是单洞
            worker = PoolRenderWorker(
                matcher=matcher,
                template_dir=tpl_dir,
                target_filename="吸水皮革-定制-定制尺寸-庄园秘境;横版134x61.5cm",
                sketch_path=_sk2_path,
                pre_parsed_result=sr,
                user_margins=None,
                user_multihole_params=None,
            )
            ok_results: list[tuple] = []
            err_results: list[str] = []
            worker.finished_ok.connect(lambda d, s, t: ok_results.append((d, s, t)))
            worker.finished_err.connect(lambda m: err_results.append(m))
            worker.run()

            assert not err_results, f"PoolRenderWorker 意外失败: {err_results}"
            design = ok_results[0][0]  # type: CropDesign
            # —— 零侵入契约断言 ——
            assert getattr(design, 'pool_is_multi_hole', False) is False
            assert getattr(design, 'pool_holes_cm', []) == []
            # 单洞内挖不变式：inner = canvas - margins（canvas = outer + TRIM）
            TRIM = 1.0
            exp_w = outer_w + TRIM - design.inner_margin_left_cm - design.inner_margin_right_cm
            exp_h = outer_h + TRIM - design.inner_margin_top_cm - design.inner_margin_bottom_cm
            inner_w = design.canvas_w_cm - design.inner_margin_left_cm - design.inner_margin_right_cm
            inner_h = design.canvas_h_cm - design.inner_margin_top_cm - design.inner_margin_bottom_cm
            assert inner_w == pytest.approx(exp_w, abs=0.01), (
                f"inner_w={inner_w} != exp_w={exp_w} | "
                f"canvas={design.canvas_w_cm}x{design.canvas_h_cm} | "
                f"ml={design.inner_margin_left_cm} mr={design.inner_margin_right_cm}")
            assert inner_h == pytest.approx(exp_h, abs=0.01), (
                f"inner_h={inner_h} != exp_h={exp_h} | "
                f"mt={design.inner_margin_top_cm} mb={design.inner_margin_bottom_cm}")
            logger.info("[T94] 单洞/多洞UI隐藏时，Worker 行为零变更 ✓")
        finally:
            try:
                os.unlink(_sk2_path)
            except Exception:
                pass


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s [%(name)s] %(levelname)s %(message)s')
    pytest.main([__file__, '-v', '--tb=short'])
