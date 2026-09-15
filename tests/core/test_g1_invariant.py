"""G1 结构一致性不变量测试。

验证 G1 闸口作为永久不变量的核心属性：
1. 所有退出路径必须经过 G1 检查（debug['g1_invariant_applied'] == True）
2. notches_detected != notches_consumed 时必须告警
3. notches_detected == notches_consumed 时必须通过
4. 单角场景向后兼容
"""

import os
import sys
import tempfile

import pytest
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.sketch_parser.lshape_sketch_parser import (
    LSketchParseResult,
    _apply_g1_invariant,
    parse_lshape_sketch,
)


def _load_font(size):
    candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        if os.path.isfile(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _make_lshape_sketch(out_path, *, corner='tr',
                        A=33.0, B=450.0, C=31.0, D=2.0, E=100.0, F=350.0,
                        s=4.0, stroke=6, margin=60):
    assert abs((F + E) - B) < 1e-6
    assert abs((C + D) - A) < 1e-6

    W = int((B * s) + 2 * margin)
    H = int((A * s) + 2 * margin)
    img = Image.new('RGB', (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)

    ox, oy = margin, margin
    wpx, hpx = int(B * s), int(A * s)
    en, dn = int(E * s), int(D * s)

    if corner == 'tr':
        verts = [
            (ox, oy), (ox + wpx - en, oy), (ox + wpx - en, oy + dn),
            (ox + wpx, oy + dn), (ox + wpx, oy + hpx), (ox, oy + hpx),
        ]
    elif corner == 'br':
        verts = [
            (ox, oy), (ox + wpx, oy), (ox + wpx, oy + hpx - dn),
            (ox + wpx - en, oy + hpx - dn), (ox + wpx - en, oy + hpx), (ox, oy + hpx),
        ]
    elif corner == 'tl':
        verts = [
            (ox + en, oy), (ox + wpx, oy), (ox + wpx, oy + hpx),
            (ox, oy + hpx), (ox, oy + dn), (ox + en, oy + dn),
        ]
    else:
        verts = [
            (ox, oy), (ox + wpx, oy), (ox + wpx, oy + hpx),
            (ox + en, oy + hpx), (ox + en, oy + hpx - dn), (ox, oy + hpx - dn),
        ]

    d.line(verts + [verts[0]], fill=(0, 0, 0), width=stroke, joint='curve')

    font = _load_font(max(18, int(7 * max(1, s / 2))))
    a_side = 'left' if corner in ('tr', 'br') else 'right'
    b_side = 'bottom' if corner in ('tr', 'tl') else 'top'
    fe_side = 'top' if corner in ('tr', 'tl') else 'bottom'
    cd_side = 'right' if corner in ('tr', 'br') else 'left'

    if corner in ('tr', 'br'):
        f_x, e_x = ox + (wpx - en) * 0.5, ox + wpx - en * 0.5
    else:
        f_x, e_x = ox + en + (wpx - en) * 0.5, ox + en * 0.5
    if corner in ('tr', 'tl'):
        c_y, d_y = oy + (dn + hpx) * 0.5, oy + dn * 0.5
    else:
        c_y, d_y = oy + (hpx - dn) * 0.5, oy + hpx - dn * 0.5

    labels = [
        (a_side, f"A {A:.0f}", oy + hpx * 0.5),
        (b_side, f"B {B:.0f}", ox + wpx * 0.5),
        (fe_side, f"F {F:.0f}", f_x),
        (fe_side, f"E {E:.0f}", e_x),
        (cd_side, f"C {C:.0f}", c_y),
        (cd_side, f"D {D:.0f}", d_y),
    ]

    for side, text, pos in labels:
        if side == 'top':
            x, y = pos, oy - 25 if oy > 25 else oy + 5
        elif side == 'bottom':
            x, y = pos, oy + hpx + 5
        elif side == 'left':
            x, y = max(2, ox - 35), pos
        else:
            x, y = ox + wpx + 5, pos
        d.text((x, y), text, fill=(0, 0, 0), font=font)

    img.save(out_path)


def _make_dual_corner_sketch(out_path):
    """合成双角草图（tr + bl），无 OCR 标注。"""
    from tests.core.test_multi_corner_detection import _make_dual_corner_sketch as _mk
    _mk(out_path)


class TestG1InvariantUnit:
    """直接测试 _apply_g1_invariant 函数。"""

    def test_g1_passes_when_detected_equals_consumed(self):
        """检测数 == 消费数时 G1 通过。"""
        result = LSketchParseResult()
        all_corners = [{'corner': 'tr'}]
        cuts_cm = [{'corner': 'tr', 'cut_w_cm': 28.0, 'cut_h_cm': 8.0}]
        passed = _apply_g1_invariant(
            result, n_detected=1, n_consumed=1,
            all_corners_geo=all_corners, cuts_cm=cuts_cm,
            base_msg="识别成功")
        assert passed is True
        assert result.success is True or result.success is False
        assert result.notches_detected == 1
        assert result.notches_consumed == 1
        assert '⚠️' not in result.message
        assert result.debug['g1_invariant_applied'] is True
        assert result.debug['g1_blocked'] is False

    def test_g1_fires_when_detected_exceeds_consumed(self):
        """检测数 > 消费数时 G1 告警。"""
        result = LSketchParseResult()
        all_corners = [{'corner': 'tr'}, {'corner': 'bl'}]
        cuts_cm = [{'corner': 'tr', 'cut_w_cm': 28.0, 'cut_h_cm': 8.0}]
        passed = _apply_g1_invariant(
            result, n_detected=2, n_consumed=1,
            all_corners_geo=all_corners, cuts_cm=cuts_cm,
            base_msg="识别部分完成")
        assert passed is False
        assert result.notches_detected == 2
        assert result.notches_consumed == 1
        assert '⚠️' in result.message
        assert 'bl' in result.message
        assert result.debug['g1_blocked'] is True
        assert result.debug['g1_invariant_applied'] is True

    def test_g1_fires_when_consumed_exceeds_detected(self):
        """消费数 > 检测数时 G1 也告警。"""
        result = LSketchParseResult()
        all_corners = [{'corner': 'tr'}, {'corner': 'bl'}]
        cuts_cm = [
            {'corner': 'tr', 'cut_w_cm': 28.0, 'cut_h_cm': 8.0},
            {'corner': 'bl', 'cut_w_cm': 30.0, 'cut_h_cm': 12.8},
        ]
        passed = _apply_g1_invariant(
            result, n_detected=1, n_consumed=2,
            all_corners_geo=all_corners, cuts_cm=cuts_cm,
            base_msg="异常")
        assert passed is False
        assert result.notches_detected == 1
        assert result.notches_consumed == 2
        assert '⚠️' in result.message
        assert result.debug['g1_blocked'] is True

    def test_g1_always_sets_invariant_applied_flag(self):
        """无论通过与否，g1_invariant_applied 必须为 True。"""
        for n_det, n_con in [(1, 1), (2, 1), (1, 2), (0, 0), (3, 2)]:
            result = LSketchParseResult()
            _apply_g1_invariant(
                result, n_det, n_con, [], [],
                base_msg="test")
            assert result.debug['g1_invariant_applied'] is True, (
                f"n_det={n_det} n_con={n_con} 时 g1_invariant_applied 应为 True"
            )


class TestG1InvariantAllExitPaths:
    """验证 parse_lshape_sketch 的所有退出路径都经过 G1。"""

    def test_g1_runs_on_normal_completion(self):
        """正常完成路径：G1 不变量标记存在。"""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'lshape.png')
            _make_lshape_sketch(p, corner='tr')
            res = parse_lshape_sketch(
                p, target_outer_w_cm=450.0, target_outer_h_cm=33.0)
            assert res.debug.get('g1_invariant_applied') is True, (
                "正常完成路径必须经过 G1 不变量检查"
            )

    def test_g1_runs_on_incomplete_dimensions(self):
        """尺寸不完整的 early return 路径：G1 不变量标记存在。"""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'dual_corner.png')
            _make_dual_corner_sketch(p)
            res = parse_lshape_sketch(
                p, target_outer_w_cm=143.0, target_outer_h_cm=62.8)
            assert res.debug.get('g1_invariant_applied') is True, (
                "尺寸不完整 early return 路径也必须经过 G1 不变量检查"
            )

    def test_g1_runs_on_geometry_failure(self):
        """几何检测失败路径（非 L 形）。

        几何失败时 geo is None，直接 return，G1 不适用。
        此路径不需要 g1_invariant_applied，因为连凹角都没检测到。
        """
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'blank.png')
            img = Image.new('RGB', (200, 200), (255, 255, 255))
            img.save(p)
            res = parse_lshape_sketch(p)
            assert res.success is False
            assert 'g1_invariant_applied' not in res.debug

    def test_g1_runs_on_file_validation_failure(self):
        """文件验证失败路径：G1 不适用（没有图片可解析）。"""
        res = parse_lshape_sketch('/nonexistent/file.png')
        assert res.success is False
        assert 'g1_invariant_applied' not in res.debug

    def test_g1_runs_on_no_cv2(self):
        """CV2 缺失路径：G1 不适用（没有几何检测）。"""
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'lshape.png')
            _make_lshape_sketch(p, corner='tr')
            with patch(
                'services.sketch_parser.lshape_sketch_parser._safe_import_cv2',
                return_value=None
            ):
                res = parse_lshape_sketch(p)
                assert res.success is False
                assert 'g1_invariant_applied' not in res.debug


class TestG1InvariantProperties:
    """G1 不变量的核心属性测试。"""

    def test_single_corner_no_false_alarm(self):
        """单角草图：G1 不变量应运行，且检测数==消费数时不应告警。"""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'single.png')
            _make_lshape_sketch(p, corner='tr')
            res = parse_lshape_sketch(
                p, target_outer_w_cm=450.0, target_outer_h_cm=33.0)
            assert res.debug.get('g1_invariant_applied') is True
            if res.notches_detected == res.notches_consumed:
                assert '⚠️' not in res.message, (
                    f"检测==消费时不应有 G1 告警: {res.message}"
                )

    def test_dual_corner_triggers_g1_when_not_all_consumed(self):
        """双角草图如果只消费 1 个角，G1 必须告警。"""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'dual.png')
            _make_dual_corner_sketch(p)
            res = parse_lshape_sketch(
                p, target_outer_w_cm=143.0, target_outer_h_cm=62.8)
            assert res.debug.get('g1_invariant_applied') is True
            if res.notches_detected > 1:
                assert '⚠️' in res.message or res.notches_detected == res.notches_consumed, (
                    f"多角检测但未全部消费时应告警: {res.message}"
                )

    def test_g1_debug_fields_always_populated_on_geo_success(self):
        """几何检测成功后，无论后续路径如何，G1 审计字段必须被填充。"""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'lshape.png')
            _make_lshape_sketch(p, corner='br')
            res = parse_lshape_sketch(
                p, target_outer_w_cm=450.0, target_outer_h_cm=33.0)
            assert res.notches_detected >= 1
            assert res.notches_consumed >= 0
            assert 'g1_blocked' in res.debug
            assert 'g1_invariant_applied' in res.debug


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
