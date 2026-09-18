"""
tests/gui/test_lshape_panel_staircase.py
Phase 4 GUI tests: staircase L-shape corner cut UI.

Covers:
  - set_cut_rects() CutRect 条带 → 步进值逆换算回填（r_i = w_i − w_{i+1}）
  - get_cut_rects_cm() 步进值 → 条带换算（offset_x=0, offset_y=Σ落差, w=Σ步进宽）
  - _set_staircase_mode() GroupBox visibility toggle + 模式选择器同步
  - _on_mode_combo_changed() 手动切换模式（清参 / 保留第 1 级）
  - get_corner()/get_cut_w_cm()/get_cut_h_cm() staircase mode delegation
  - get_cuts_cm() 阶梯模式恒返 []（同角位重复由旧格式校验拒绝）
  - DesignModel.apply_ui_snapshot() l_cut_rects 写入 + l_cuts_cm 互斥守卫
  - PoolRenderWorker._apply_lshape_params() cut_rects 路径 + 旧 cuts_cm 路径
  - clear_lshape_params() staircase mode reset
  - add/remove level row button behavior
"""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.geometry import CropDesign, CutRect
from models.design_model import DesignModel


def _base_snap(**over):
    snap = {
        'canvas_w_cm': 80.0, 'canvas_h_cm': 130.0, 'dpi': 150, 'mode': 'rect_lshape',
        'outer_margin_cm': 0.0,
        'inner': {'top': 0.0, 'bottom': 0.0, 'left': 0.0, 'right': 0.0},
        'lshape': None,
        'corners': {'tl': 0.0, 'tr': 0.0, 'bl': 0.0, 'br': 0.0},
        'ellipse': {'diameter_w_cm': 0.0, 'diameter_h_cm': 0.0},
        'colors': {'outer': (255, 255, 255), 'hole': (255, 255, 255)},
        'images': {'outer': None, 'hole': None},
        'text': {'enabled': False, 'text': '', 'font_size_px': 12,
                 'color': (0, 0, 0), 'mirror_bottom': False},
        'hole_mode': None,
        'multihole': None,
    }
    snap.update(over)
    return snap


class TestStaircaseModeToggle:
    def test_initial_mode_is_standard(self, lshape_panel):
        assert not lshape_panel._staircase_mode
        assert not lshape_panel._gb_l.isHidden()
        assert lshape_panel._gb_staircase.isHidden()

    def test_set_staircase_mode_shows_staircase_groupbox(self, qapp, lshape_panel):
        lshape_panel._set_staircase_mode(True)
        qapp.processEvents()
        assert lshape_panel._staircase_mode
        assert lshape_panel._gb_l.isHidden()
        assert not lshape_panel._gb_staircase.isHidden()

    def test_set_staircase_mode_false_restores_standard(self, qapp, lshape_panel):
        lshape_panel._set_staircase_mode(True)
        qapp.processEvents()
        lshape_panel._set_staircase_mode(False)
        qapp.processEvents()
        assert not lshape_panel._staircase_mode
        assert not lshape_panel._gb_l.isHidden()
        assert lshape_panel._gb_staircase.isHidden()


class TestSetCutRects:
    def test_set_cut_rects_enters_staircase_mode(self, qapp, lshape_panel):
        rects = [
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 0.0,
             'w_cm': 10.0, 'h_cm': 15.0},
            {'anchor': 'tr', 'offset_x_cm': 10.0, 'offset_y_cm': 15.0,
             'w_cm': 8.0, 'h_cm': 12.0},
        ]
        lshape_panel.set_cut_rects(rects)
        qapp.processEvents()
        assert lshape_panel._staircase_mode
        assert len(lshape_panel._stair_rows) == 2

    def test_set_cut_rects_fills_spinboxes(self, qapp, lshape_panel):
        # 条带 (w=20, oy=0) + (w=15, oy=7) → 逆换算步进 r1=20−15=5, d1=7; r2=15, d2=7
        rects = [
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 0.0,
             'w_cm': 20.0, 'h_cm': 7.0},
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 7.0,
             'w_cm': 15.0, 'h_cm': 7.0},
        ]
        lshape_panel.set_cut_rects(rects)
        qapp.processEvents()
        r1, d1, _ = lshape_panel._stair_rows[0]
        r2, d2, _ = lshape_panel._stair_rows[1]
        assert abs(r1.value() - 5.0) < 0.01
        assert abs(d1.value() - 7.0) < 0.01
        assert abs(r2.value() - 15.0) < 0.01
        assert abs(d2.value() - 7.0) < 0.01

    def test_set_cut_rects_sets_corner(self, qapp, lshape_panel):
        rects = [
            {'anchor': 'bl', 'offset_x_cm': 0.0, 'offset_y_cm': 0.0,
             'w_cm': 5.0, 'h_cm': 5.0},
        ]
        lshape_panel.set_cut_rects(rects)
        qapp.processEvents()
        assert lshape_panel._stair_corner.currentData() == 'bl'

    def test_set_cut_rects_adjusts_row_count(self, qapp, lshape_panel):
        lshape_panel.set_cut_rects([
            {'anchor': 'tr', 'offset_x_cm': 0, 'offset_y_cm': 0, 'w_cm': 5, 'h_cm': 5},
            {'anchor': 'tr', 'offset_x_cm': 5, 'offset_y_cm': 5, 'w_cm': 3, 'h_cm': 3},
            {'anchor': 'tr', 'offset_x_cm': 8, 'offset_y_cm': 8, 'w_cm': 2, 'h_cm': 2},
        ])
        qapp.processEvents()
        assert len(lshape_panel._stair_rows) == 3
        lshape_panel.set_cut_rects([
            {'anchor': 'tr', 'offset_x_cm': 0, 'offset_y_cm': 0, 'w_cm': 5, 'h_cm': 5},
        ])
        qapp.processEvents()
        assert len(lshape_panel._stair_rows) == 1

    def test_set_cut_rects_empty_is_noop(self, lshape_panel):
        lshape_panel.set_cut_rects([])
        assert not lshape_panel._staircase_mode

    def test_set_cut_rects_caps_at_max_levels(self, qapp, lshape_panel):
        rects = [
            {'anchor': 'tr', 'offset_x_cm': i * 2, 'offset_y_cm': i * 2,
             'w_cm': 5, 'h_cm': 5}
            for i in range(5)
        ]
        lshape_panel.set_cut_rects(rects)
        qapp.processEvents()
        assert len(lshape_panel._stair_rows) <= lshape_panel._stair_max_levels


class TestGetCutRectsCm:
    def test_returns_empty_in_standard_mode(self, lshape_panel):
        assert lshape_panel.get_cut_rects_cm() == []

    def test_returns_cut_rects_in_staircase_mode(self, qapp, lshape_panel):
        # 往返一致：条带 → 步进 → 条带（报告 V2.4 正逆换算）
        lshape_panel.set_cut_rects([
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 0.0,
             'w_cm': 20.0, 'h_cm': 7.0},
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 7.0,
             'w_cm': 15.0, 'h_cm': 7.0},
        ])
        qapp.processEvents()
        result = lshape_panel.get_cut_rects_cm()
        assert len(result) == 2
        assert result[0]['anchor'] == 'tr'
        assert abs(result[0]['offset_x_cm'] - 0.0) < 0.01
        assert abs(result[0]['offset_y_cm'] - 0.0) < 0.01
        assert abs(result[0]['w_cm'] - 20.0) < 0.01
        assert abs(result[0]['h_cm'] - 7.0) < 0.01
        assert abs(result[1]['offset_y_cm'] - 7.0) < 0.01
        assert abs(result[1]['w_cm'] - 15.0) < 0.01
        assert abs(result[1]['h_cm'] - 7.0) < 0.01

    def test_skips_zero_size_rows(self, qapp, lshape_panel):
        lshape_panel.set_cut_rects([
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 0.0,
             'w_cm': 10.0, 'h_cm': 15.0},
        ])
        qapp.processEvents()
        r_sp = lshape_panel._stair_rows[0][0]
        r_sp.setValue(0.0)
        qapp.processEvents()
        result = lshape_panel.get_cut_rects_cm()
        assert len(result) == 0


class TestStaircaseGetters:
    def test_get_corner_in_staircase_mode(self, qapp, lshape_panel):
        lshape_panel.set_cut_rects([
            {'anchor': 'bl', 'offset_x_cm': 0, 'offset_y_cm': 0,
             'w_cm': 5, 'h_cm': 5},
        ])
        qapp.processEvents()
        assert lshape_panel.get_corner() == 'bl'

    def test_get_cut_w_cm_in_staircase_mode(self, qapp, lshape_panel):
        lshape_panel.set_cut_rects([
            {'anchor': 'tr', 'offset_x_cm': 0, 'offset_y_cm': 0,
             'w_cm': 12.5, 'h_cm': 8.0},
        ])
        qapp.processEvents()
        assert abs(lshape_panel.get_cut_w_cm() - 12.5) < 0.01

    def test_get_cut_h_cm_in_staircase_mode(self, qapp, lshape_panel):
        lshape_panel.set_cut_rects([
            {'anchor': 'tr', 'offset_x_cm': 0, 'offset_y_cm': 0,
             'w_cm': 12.5, 'h_cm': 8.0},
        ])
        qapp.processEvents()
        assert abs(lshape_panel.get_cut_h_cm() - 8.0) < 0.01

    def test_get_cuts_cm_in_staircase_mode(self, qapp, lshape_panel):
        # 阶梯模式恒返 []：旧格式 {corner, cut_w, cut_h} 不允许同角位重复，
        # 阶梯几何真值只由 get_cut_rects_cm() 承载（core validate 会拒绝重复角位）
        lshape_panel.set_cut_rects([
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 0.0,
             'w_cm': 20.0, 'h_cm': 7.0},
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 7.0,
             'w_cm': 15.0, 'h_cm': 7.0},
        ])
        qapp.processEvents()
        assert lshape_panel.get_cuts_cm() == []


class TestStepsStripsConversion:
    """报告 V2.4 步进↔条带换算：手动改值、往返保持、角位无关性。"""

    CANONICAL = [
        {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 0.0,
         'w_cm': 20.0, 'h_cm': 7.0},
        {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 7.0,
         'w_cm': 15.0, 'h_cm': 7.0},
    ]

    def test_manual_edit_recomputes_strips(self, qapp, lshape_panel):
        # 手动改第 1 级步进宽 5 → 6：总宽 21，第 2 级条带不变
        lshape_panel.set_cut_rects(self.CANONICAL)
        qapp.processEvents()
        r_sp, _d_sp, _ = lshape_panel._stair_rows[0]
        r_sp.setValue(6.0)
        qapp.processEvents()
        result = lshape_panel.get_cut_rects_cm()
        assert abs(result[0]['w_cm'] - 21.0) < 0.01
        assert abs(result[1]['w_cm'] - 15.0) < 0.01

    def test_round_trip_preserves_bl_anchor(self, qapp, lshape_panel):
        bl_strips = [
            dict(r, anchor='bl') for r in self.CANONICAL
        ]
        lshape_panel.set_cut_rects(bl_strips)
        qapp.processEvents()
        result = lshape_panel.get_cut_rects_cm()
        assert all(r['anchor'] == 'bl' for r in result)
        assert abs(result[0]['offset_y_cm'] - 0.0) < 0.01
        assert abs(result[0]['w_cm'] - 20.0) < 0.01
        assert abs(result[1]['offset_y_cm'] - 7.0) < 0.01
        assert abs(result[1]['w_cm'] - 15.0) < 0.01

    def test_anchor_independence(self, qapp, lshape_panel):
        # 同一组步进值在四个角位产出相同的 (offset_y, w, h) 几何
        # （offset 从 anchor 自身边测量，镜像不变）
        per_anchor = []
        for anchor in ('tl', 'tr', 'bl', 'br'):
            lshape_panel.set_cut_rects(
                [dict(r, anchor=anchor) for r in self.CANONICAL])
            qapp.processEvents()
            per_anchor.append([
                (round(r['offset_y_cm'], 2), round(r['w_cm'], 2),
                 round(r['h_cm'], 2))
                for r in lshape_panel.get_cut_rects_cm()
            ])
        assert per_anchor[0] == [(0.0, 20.0, 7.0), (7.0, 15.0, 7.0)]
        assert all(a == per_anchor[0] for a in per_anchor[1:])


class TestModeSelector:
    """顶部「挖角模式」选择器：手动切换阶梯 ↔ 标准（报告 V2.3 模式切换规则）。"""

    def test_initial_combo_is_standard(self, lshape_panel):
        assert lshape_panel._mode_combo.currentData() == 'standard'

    def test_switch_to_staircase_clears_params(self, qapp, lshape_panel):
        lshape_panel._mode_combo.setCurrentIndex(1)
        qapp.processEvents()
        assert lshape_panel._staircase_mode
        assert lshape_panel._mode_combo.currentData() == 'staircase'
        assert len(lshape_panel._stair_rows) == 2
        assert lshape_panel._lshape_params['cut_rects'] == []
        assert lshape_panel._params_source == 'manual'
        # _gb_l（4 角行）显式隐藏，_gb_staircase 显示
        assert lshape_panel._gb_l.isHidden()
        assert not lshape_panel._gb_staircase.isHidden()

    def test_switch_back_to_standard_keeps_first_level(self, qapp, lshape_panel):
        lshape_panel.set_cut_rects([
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 0.0,
             'w_cm': 20.0, 'h_cm': 7.0},
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 7.0,
             'w_cm': 15.0, 'h_cm': 7.0},
        ])
        qapp.processEvents()
        assert lshape_panel._staircase_mode
        lshape_panel._mode_combo.setCurrentIndex(0)
        qapp.processEvents()
        assert not lshape_panel._staircase_mode
        assert lshape_panel._mode_combo.currentData() == 'standard'
        # 保留第 1 级（第一根条带 w=20, h=7）作为单角挖角
        assert lshape_panel.get_corner() == 'tr'
        assert abs(lshape_panel.get_cut_w_cm() - 20.0) < 0.01
        assert abs(lshape_panel.get_cut_h_cm() - 7.0) < 0.01
        # 其余角行全部停用
        assert not lshape_panel._corner_rows[1][0].isChecked()

    def test_set_staircase_mode_syncs_combo(self, qapp, lshape_panel):
        lshape_panel._set_staircase_mode(True)
        qapp.processEvents()
        assert lshape_panel._mode_combo.currentData() == 'staircase'
        lshape_panel._set_staircase_mode(False)
        qapp.processEvents()
        assert lshape_panel._mode_combo.currentData() == 'standard'


class TestStaircaseAddRemove:
    def test_add_level_row(self, qapp, lshape_panel):
        lshape_panel._set_staircase_mode(True)
        initial = len(lshape_panel._stair_rows)
        lshape_panel._on_stair_add_level()
        qapp.processEvents()
        assert len(lshape_panel._stair_rows) == initial + 1

    def test_remove_level_row(self, qapp, lshape_panel):
        lshape_panel._set_staircase_mode(True)
        initial = len(lshape_panel._stair_rows)
        lshape_panel._on_stair_remove_level()
        qapp.processEvents()
        assert len(lshape_panel._stair_rows) == initial - 1

    def test_cannot_remove_below_one(self, qapp, lshape_panel):
        lshape_panel._set_staircase_mode(True)
        while len(lshape_panel._stair_rows) > 1:
            lshape_panel._on_stair_remove_level()
        lshape_panel._on_stair_remove_level()
        qapp.processEvents()
        assert len(lshape_panel._stair_rows) == 1

    def test_cannot_add_above_max(self, qapp, lshape_panel):
        lshape_panel._set_staircase_mode(True)
        while len(lshape_panel._stair_rows) < lshape_panel._stair_max_levels:
            lshape_panel._on_stair_add_level()
        lshape_panel._on_stair_add_level()
        qapp.processEvents()
        assert len(lshape_panel._stair_rows) == lshape_panel._stair_max_levels


class TestClearLshapeParamsResetsStaircase:
    def test_clear_resets_staircase_mode(self, qapp, lshape_panel):
        lshape_panel.set_cut_rects([
            {'anchor': 'tr', 'offset_x_cm': 0, 'offset_y_cm': 0,
             'w_cm': 5, 'h_cm': 5},
        ])
        qapp.processEvents()
        assert lshape_panel._staircase_mode
        lshape_panel.clear_lshape_params()
        qapp.processEvents()
        assert not lshape_panel._staircase_mode


class TestDesignModelCutRects:
    def test_apply_ui_snapshot_writes_l_cut_rects(self):
        m = DesignModel()
        lshape = {
            'corner': 'tr',
            'cut_w_cm': 10.0,
            'cut_h_cm': 15.0,
            'cuts_cm': [
                {'corner': 'tr', 'cut_w_cm': 10.0, 'cut_h_cm': 15.0},
                {'corner': 'tr', 'cut_w_cm': 8.0, 'cut_h_cm': 12.0},
            ],
            'cut_rects': [
                {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 0.0,
                 'w_cm': 10.0, 'h_cm': 15.0},
                {'anchor': 'tr', 'offset_x_cm': 10.0, 'offset_y_cm': 15.0,
                 'w_cm': 8.0, 'h_cm': 12.0},
            ],
        }
        m.apply_ui_snapshot(_base_snap(lshape=lshape))
        assert len(m.design.l_cut_rects) == 2
        assert isinstance(m.design.l_cut_rects[0], CutRect)
        assert m.design.l_cut_rects[0].anchor == 'tr'
        assert abs(m.design.l_cut_rects[0].w_cm - 10.0) < 0.01
        assert abs(m.design.l_cut_rects[1].offset_x_cm - 10.0) < 0.01
        # cut_rects 非空时旧格式 l_cuts_cm 必须保持为空
        # （l_cuts_cm 校验拒绝同角位重复，写入会破坏 validate）
        assert m.design.l_cuts_cm == []

    def test_apply_ui_snapshot_legacy_cuts_cm_without_cut_rects(self):
        # 旧格式（多角位、无重复）：cut_rects 为空时 l_cuts_cm 正常写入，
        # primary = cuts_cm[0] 覆盖 corner/cut_w/cut_h
        m = DesignModel()
        lshape = {
            'corner': 'tr',
            'cut_w_cm': 10.0,
            'cut_h_cm': 15.0,
            'cuts_cm': [
                {'corner': 'tr', 'cut_w_cm': 10.0, 'cut_h_cm': 15.0},
                {'corner': 'bl', 'cut_w_cm': 8.0, 'cut_h_cm': 12.0},
            ],
            'cut_rects': [],
        }
        m.apply_ui_snapshot(_base_snap(lshape=lshape))
        assert m.design.l_cut_rects == []
        assert len(m.design.l_cuts_cm) == 2
        assert m.design.l_cuts_cm[0]['corner'] == 'tr'
        assert m.design.l_cuts_cm[1]['corner'] == 'bl'
        assert m.design.l_corner == 'tr'
        assert abs(m.design.l_cut_w_cm - 10.0) < 0.01
        assert abs(m.design.l_cut_h_cm - 15.0) < 0.01

    def test_apply_ui_snapshot_empty_cut_rects_clears(self):
        m = DesignModel()
        m.design.l_cut_rects = [CutRect(anchor='tr', w_cm=5, h_cm=5)]
        lshape = {
            'corner': 'tr', 'cut_w_cm': 5.0, 'cut_h_cm': 5.0,
            'cuts_cm': [], 'cut_rects': [],
        }
        m.apply_ui_snapshot(_base_snap(lshape=lshape))
        assert m.design.l_cut_rects == []

    def test_apply_ui_snapshot_no_lshape_clears_cut_rects(self):
        m = DesignModel()
        m.design.l_cut_rects = [CutRect(anchor='tr', w_cm=5, h_cm=5)]
        m.apply_ui_snapshot(_base_snap(lshape=None))
        assert m.design.l_cut_rects == []

    def test_apply_ui_snapshot_cut_rects_caps_at_3(self):
        m = DesignModel()
        rects = [
            {'anchor': 'tr', 'offset_x_cm': i * 2, 'offset_y_cm': i * 2,
             'w_cm': 5, 'h_cm': 5}
            for i in range(5)
        ]
        lshape = {
            'corner': 'tr', 'cut_w_cm': 5.0, 'cut_h_cm': 5.0,
            'cuts_cm': [], 'cut_rects': rects,
        }
        m.apply_ui_snapshot(_base_snap(lshape=lshape))
        assert len(m.design.l_cut_rects) == 3


class TestWorkerCutRectsPath:
    """PoolRenderWorker._apply_lshape_params 的 cut_rects 路径（用户报错链路根因）：

    修复前 worker 只读 lp['cuts_cm']，阶梯场景 cut_rects 被丢弃 →
    l_cuts_cm 同角位重复 → validate 抛
    "ValueError: l_cuts_cm 不允许重复角位: 'tr'"。
    修复后 cut_rects 非空 → design.l_cut_rects（唯一几何来源），l_cuts_cm 保持为空。
    """

    STRIPS = [
        {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 0.0,
         'w_cm': 20.0, 'h_cm': 7.0},
        {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 7.0,
         'w_cm': 15.0, 'h_cm': 7.0},
    ]

    def _make_worker(self, lshape_params):
        from workers.property_panel_workers import PoolRenderWorker
        return PoolRenderWorker(
            matcher=None, template_dir='', target_filename='x',
            lshape_params=lshape_params)

    def _run(self, worker, canvas_w=188.5, canvas_h=74.0):
        design = CropDesign()
        worker._apply_lshape_params(
            design, SimpleNamespace(path='x'), canvas_w, canvas_h, 1.0)
        return design

    def test_cut_rects_path_writes_l_cut_rects(self):
        worker = self._make_worker({
            'corner': 'tr', 'cut_w_cm': 20.0, 'cut_h_cm': 14.0,
            'cuts_cm': [], 'cut_rects': self.STRIPS,
            'outer_w_cm': 188.5, 'outer_h_cm': 74.0,
        })
        design = self._run(worker)
        assert design.mode == 'rect_lshape'
        assert len(design.l_cut_rects) == 2
        assert all(isinstance(cr, CutRect) for cr in design.l_cut_rects)
        assert design.l_cut_rects[0].anchor == 'tr'
        assert abs(design.l_cut_rects[0].offset_y_cm - 0.0) < 0.01
        assert abs(design.l_cut_rects[0].w_cm - 20.0) < 0.01
        assert abs(design.l_cut_rects[1].offset_y_cm - 7.0) < 0.01
        assert abs(design.l_cut_rects[1].w_cm - 15.0) < 0.01
        assert design.l_cuts_cm == []

    def test_cut_rects_path_filters_invalid_entries(self):
        # 非法条目（坏 anchor / 非正宽高）被过滤，不产生半成品几何
        worker = self._make_worker({
            'corner': 'tr', 'cut_w_cm': 20.0, 'cut_h_cm': 7.0,
            'cuts_cm': [],
            'cut_rects': self.STRIPS + [
                {'anchor': 'xx', 'offset_x_cm': 0, 'offset_y_cm': 14,
                 'w_cm': 5.0, 'h_cm': 7.0},
                {'anchor': 'tr', 'offset_x_cm': 0, 'offset_y_cm': 14,
                 'w_cm': 0.0, 'h_cm': 7.0},
            ],
            'outer_w_cm': 188.5, 'outer_h_cm': 74.0,
        })
        design = self._run(worker)
        assert len(design.l_cut_rects) == 2

    def test_legacy_cuts_cm_path_untouched(self):
        worker = self._make_worker({
            'corner': 'tr', 'cut_w_cm': 10.0, 'cut_h_cm': 15.0,
            'cuts_cm': [
                {'corner': 'tr', 'cut_w_cm': 10.0, 'cut_h_cm': 15.0},
                {'corner': 'bl', 'cut_w_cm': 8.0, 'cut_h_cm': 12.0},
            ],
            'cut_rects': [],
            'outer_w_cm': 100.0, 'outer_h_cm': 60.0,
        })
        design = self._run(worker, canvas_w=100.0, canvas_h=60.0)
        assert design.mode == 'rect_lshape'
        assert len(design.l_cuts_cm) == 2
        assert design.l_cuts_cm[0]['corner'] == 'tr'
        assert design.l_cuts_cm[1]['corner'] == 'bl'
        assert design.l_cut_rects == []
