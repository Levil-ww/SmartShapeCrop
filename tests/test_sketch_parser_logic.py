"""
测试：core.pool_designer.sketch_parser 纯逻辑函数

无需真实草图图片 / Tesseract / OpenCV 图像，仅验证可独立调用的几何与赋值逻辑：
  - _compute_gaps                  外框-内框 4 间隙区域计算 + 无效间隙过滤
  - _score_assignment_consistency  OCR 赋值方案的几何自洽性评分 (sc, 0~1)
  - _validate_and_fix_margins      边距几何自洽修正（缺失推导 / 比例缩放 / 异常裁剪）
  - _validate_geometric_constraints 几何约束校验（OCR 与几何值差异大时覆盖）

这些函数历史上是 8.x 系列反复回归的重灾区（横竖颠倒、边距错乱、自洽保护伪命中），
补纯逻辑单测可在无图片环境下快速锁定回归。
"""
import pytest

from core.pool_designer.sketch_parser import (
    _compute_gaps,
    _score_assignment_consistency,
    _validate_and_fix_margins,
    _validate_geometric_constraints,
)


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------

def _asg(tw=0.0, th=0.0, iw=0.0, ih=0.0, mt=0.0, mb=0.0, ml=0.0, mr=0.0, conf=0.9):
    """构造 _score_assignment_consistency / _validate_and_fix_margin 用的赋值 dict。

    每个字段值为 (数值, 置信度) 元组，与解析器内部约定一致。
    """
    return {
        'total_w': (tw, conf), 'total_h': (th, conf),
        'inner_w': (iw, conf), 'inner_h': (ih, conf),
        'margin_top': (mt, conf), 'margin_bottom': (mb, conf),
        'margin_left': (ml, conf), 'margin_right': (mr, conf),
    }


# ===========================================================================
# 1. _compute_gaps
# ===========================================================================

class TestComputeGaps:
    """外框-内框 4 个间隙区域计算。"""

    def test_all_four_gaps_valid(self):
        """居中内框：4 个间隙均足够大，全部返回。"""
        gaps = _compute_gaps(0, 0, 100, 50, 20, 5, 60, 40)
        assert set(gaps.keys()) == {'top', 'bottom', 'left', 'right'}
        # 上边隙：外框上边 → 内框上边
        assert gaps['top'] == (0, 0, 100, 5)
        # 下边隙：内框下边 → 外框下边
        assert gaps['bottom'] == (0, 45, 100, 50)
        # 左边隙：外框左边 → 内框左边
        assert gaps['left'] == (0, 0, 20, 50)
        # 右边隙：内框右边 → 外框右边
        assert gaps['right'] == (80, 0, 100, 50)

    def test_inner_equals_outer_filters_all(self):
        """内框=外框：所有间隙宽/高为 0，全部被过滤。"""
        gaps = _compute_gaps(0, 0, 100, 50, 0, 0, 100, 50)
        assert gaps == {}

    def test_thin_horizontal_gap_filtered(self):
        """上下间隙过小（<3px）被过滤，仅保留左右间隙。"""
        # iy=2 → 上边隙高 2px；内框下边=2+46=48 → 下边隙高 50-48=2px
        gaps = _compute_gaps(0, 0, 100, 50, 20, 2, 60, 46)
        assert set(gaps.keys()) == {'left', 'right'}


# ===========================================================================
# 2. _score_assignment_consistency
# ===========================================================================

class TestScoreAssignmentConsistency:
    """几何自洽性评分（sc）。历史 bug：伪自洽保护导致错值被保留。"""

    def test_fully_self_consistent_is_one(self):
        """完全自洽（外框=内框+边距和）应得满分 1.0。

        tw=100=80+10+10，th=60=40+10+10。
        """
        a = _asg(tw=100, th=60, iw=80, ih=40, mt=10, mb=10, ml=10, mr=10)
        assert _score_assignment_consistency(a) == pytest.approx(1.0)

    def test_zero_outer_is_zero(self):
        """外框为 0 直接返回 0。"""
        a = _asg(tw=0, th=60, iw=80, ih=40)
        assert _score_assignment_consistency(a) == 0.0

    def test_inner_exceeds_outer_is_clamped_zero(self):
        """内框 > 外框（不可能）受重罚，得分被夹到 0。"""
        a = _asg(tw=100, th=60, iw=120, ih=40)  # iw=120 > tw=100
        assert _score_assignment_consistency(a) == 0.0

    def test_negative_margin_penalized(self):
        """负边距触发 -0.3 惩罚，但水平自洽仍得分。"""
        a = _asg(tw=100, th=60, iw=80, ih=40, mt=-5, mb=10, ml=10, mr=10)
        # 0.15*7/8 + 0.1 + 0.1(3边距) - 0.3(负) + 0.4(水平自洽) = 0.43125
        assert _score_assignment_consistency(a) == pytest.approx(0.43125)

    def test_partial_outer_only_low_score(self):
        """仅有外框两值时得分低，体现完整度奖励。"""
        a = _asg(tw=100, th=60)
        assert _score_assignment_consistency(a) == pytest.approx(0.0375)

    def test_consistent_beats_inconsistent(self):
        """自洽解得分严格高于同等字段数但不自洽的解。"""
        consistent = _asg(tw=100, th=60, iw=80, ih=40, mt=10, mb=10, ml=10, mr=10)
        # 同字段数但边距不满足 outer=inner+margins
        inconsistent = _asg(tw=100, th=60, iw=80, ih=40, mt=3, mb=3, ml=3, mr=3)
        assert _score_assignment_consistency(consistent) > _score_assignment_consistency(inconsistent)


# ===========================================================================
# 3. _validate_and_fix_margins
# ===========================================================================

class TestValidateAndFixMargins:
    """边距几何自洽修正：缺失推导 / 比例缩放 / 异常裁剪。"""

    def test_derive_missing_margin_from_known(self):
        """已知右边距，由外框-内框反推左边距。"""
        r = _asg(tw=100, iw=80, ml=0, mr=15, th=60, ih=40, mt=0, mb=0)
        out = _validate_and_fix_margins(r)
        # 期望水平间隙 = 100-80 = 20，已知 mr=15 → ml = 20-15 = 5
        assert out['margin_left'][0] == pytest.approx(5.0)
        assert out['margin_right'][0] == pytest.approx(15.0)
        # 垂直方向两边距均未知，不推导
        assert out['margin_top'][0] == 0.0
        assert out['margin_bottom'][0] == 0.0

    def test_rescale_when_ratio_far_off(self):
        """边距和与外框-内框差异 >2x 时按比例缩放两侧。"""
        r = _asg(tw=100, iw=80, ml=2, mr=2)  # 期望 20，实际 4，比例 5x
        out = _validate_and_fix_margins(r)
        assert out['margin_left'][0] == pytest.approx(10.0)
        assert out['margin_right'][0] == pytest.approx(10.0)

    def test_clip_oversized_margin(self):
        """单侧边距超过 (外框-内框)*90% 上限时裁剪到上限。"""
        # 期望水平间隙 20，ml=30+mr=5 → 实际 35，比例 20/35≈0.57（在 [0.5,2] 内不缩放）
        # 上限 = min(100*0.6=60, (100-80)*0.9=18) = 18 → ml 30 被裁到 18
        r = _asg(tw=100, iw=80, ml=30, mr=5)
        out = _validate_and_fix_margins(r)
        assert out['margin_left'][0] == pytest.approx(18.0)
        assert out['margin_right'][0] == pytest.approx(5.0)

    def test_negative_margin_zeroed(self):
        """无外框尺寸参与推导时，负边距直接清零。"""
        r = _asg(mt=-5)  # tw/th/iw/ih 均为 0，不触发推导与裁剪
        out = _validate_and_fix_margins(r)
        assert out['margin_top'][0] == 0.0

    def test_does_not_overwrite_correct_margins(self):
        """已自洽的边距不应被修改。"""
        r = _asg(tw=100, th=60, iw=80, ih=40, mt=10, mb=10, ml=10, mr=10)
        out = _validate_and_fix_margins(r)
        assert out['margin_top'][0] == pytest.approx(10.0)
        assert out['margin_left'][0] == pytest.approx(10.0)

    def test_ocr_noise_margin_does_not_override_target_outer(self):
        """[2026-08 回归] OCR 把装饰文字识别为超大边距(如74cm)时，不应覆盖 target 外框尺寸。

        场景：文件名外框 target_outer_w=51cm，OCR 把花纹装饰"Cross"识别为74，
        方向标签绑定为 margin_left=74, margin_right=74。旧逻辑会反推
        total_w=73.6+74+74=221.6（严重错误）。修复后保留 target。
        """
        r = _asg(tw=51.0, th=89.5, iw=73.6, ih=34.0, mt=8.8, mb=8.2, ml=74.0, mr=74.0)
        # 传入 target_outer_w=51，target_outer_h=89.5 模拟真实场景
        # dir_locked 包含 4 个方向标签，其中 ml/mr 是 OCR 噪声
        out = _validate_and_fix_margins(
            r, target_outer_w=51.0, target_outer_h=89.5,
            dir_locked_fields={'margin_top', 'margin_bottom', 'margin_left', 'margin_right'}
        )
        # 关键断言：外框宽不应被噪声边距放大，应保留 target
        assert out['total_w'][0] == pytest.approx(51.0, abs=0.01), (
            f"total_w 应保留 target=51，实际={out['total_w'][0]}"
        )
        assert out['total_h'][0] == pytest.approx(89.5, abs=0.01)

    def test_sanitize_oversized_dir_margin(self):
        """方向标签边距过大(>30% target 短边)应被清洗掉。"""
        # target_outer_w=89.5 → sanity_cap = min(89.5*0.30, 30) = 26.85
        # margin_left=74 远超 cap，应被清洗为 0，不再参与外框反推
        r = _asg(tw=89.5, th=51.0, iw=73.6, ih=34.0, mt=8.8, mb=8.2, ml=74.0, mr=74.0)
        out = _validate_and_fix_margins(
            r, target_outer_w=89.5, target_outer_h=51.0,
            dir_locked_fields={'margin_left', 'margin_right'}
        )
        # 外框宽保留 target
        assert out['total_w'][0] == pytest.approx(89.5, abs=0.01)

    def test_normal_margin_still_derives_total(self):
        """正常大小的方向标签边距(如10cm)仍可参与外框反推。"""
        # 无 target 时，用方向标签边距 + inner 反推 total
        r = _asg(iw=80.0, ih=40.0, mt=10.0, mb=10.0, ml=10.0, mr=10.0)
        out = _validate_and_fix_margins(
            r, target_outer_w=0.0, target_outer_h=0.0,
            dir_locked_fields={'margin_top', 'margin_bottom', 'margin_left', 'margin_right'}
        )
        # total_w = inner_w + ml + mr = 80 + 10 + 10 = 100
        assert out['total_w'][0] == pytest.approx(100.0, abs=0.01)
        assert out['total_h'][0] == pytest.approx(60.0, abs=0.01)


# ===========================================================================
# 4. _validate_geometric_constraints
# ===========================================================================

class TestValidateGeometricConstraints:
    """几何约束校验：OCR 与几何计算值差异过大时用几何值覆盖。"""

    # 外框 200×100px，内框 160×80px（偏移 20,10）
    # target 100×50cm → cm/px = 0.5（双轴一致）
    # 几何边距：上=10px*0.5=5cm，下=5cm，左=20px*0.5=10cm，右=10cm
    OUTER = (0, 0, 200, 100)
    INNER = (20, 10, 160, 80)

    def test_ocr_close_to_geometric_is_kept(self):
        """OCR 值与几何值接近时保留 OCR 值。"""
        margins = {
            'margin_top': (5, 0.9), 'margin_bottom': (5, 0.9),
            'margin_left': (10, 0.9), 'margin_right': (10, 0.9),
        }
        out = _validate_geometric_constraints(
            margins, {}, self.OUTER, self.INNER,
            cm_per_px_x=0.5, cm_per_px_y=0.5,
            target_outer_w_cm=100, target_outer_h_cm=50,
        )
        assert out['margin_top'] == pytest.approx(5.0)
        assert out['margin_bottom'] == pytest.approx(5.0)
        assert out['margin_left'] == pytest.approx(10.0)
        assert out['margin_right'] == pytest.approx(10.0)

    def test_ocr_far_from_geometric_is_overridden(self):
        """OCR 值偏离几何值超过容差时，用几何值覆盖。"""
        # mt=20，几何值=5，diff=15 > max(3, 50*0.15=7.5) → 覆盖为 5
        margins = {
            'margin_top': (20, 0.9), 'margin_bottom': (5, 0.9),
            'margin_left': (10, 0.9), 'margin_right': (10, 0.9),
        }
        out = _validate_geometric_constraints(
            margins, {}, self.OUTER, self.INNER,
            cm_per_px_x=0.5, cm_per_px_y=0.5,
            target_outer_w_cm=100, target_outer_h_cm=50,
        )
        assert out['margin_top'] == pytest.approx(5.0)   # 被覆盖
        assert out['margin_bottom'] == pytest.approx(5.0)  # 保留

    def test_missing_margin_filled_from_geometric(self):
        """缺失的边距由几何像素值填充。"""
        out = _validate_geometric_constraints(
            {}, {}, self.OUTER, self.INNER,
            cm_per_px_x=0.5, cm_per_px_y=0.5,
            target_outer_w_cm=100, target_outer_h_cm=50,
        )
        assert out['margin_top'] == pytest.approx(5.0)
        assert out['margin_bottom'] == pytest.approx(5.0)
        assert out['margin_left'] == pytest.approx(10.0)
        assert out['margin_right'] == pytest.approx(10.0)
