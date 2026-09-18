"""
tests/gui/test_lshape_panel_staircase.py
Phase 4 GUI tests: staircase L-shape corner cut UI.

Covers:
  - set_cut_rects() backfill into staircase sub-row SpinBoxes
  - get_cut_rects_cm() extraction from staircase sub-rows
  - _set_staircase_mode() GroupBox visibility toggle
  - get_corner()/get_cut_w_cm()/get_cut_h_cm() staircase mode delegation
  - _collect_ui_snapshot() cut_rects extraction (via property_panel_layers)
  - DesignModel.apply_ui_snapshot() l_cut_rects writing
  - clear_lshape_params() staircase mode reset
  - add/remove level row button behavior
"""
import os
import sys

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
        rects = [
            {'anchor': 'tr', 'offset_x_cm': 1.5, 'offset_y_cm': 2.5,
             'w_cm': 10.0, 'h_cm': 15.0},
        ]
        lshape_panel.set_cut_rects(rects)
        qapp.processEvents()
        ox, oy, w, h, _ = lshape_panel._stair_rows[0]
        assert abs(ox.value() - 1.5) < 0.01
        assert abs(oy.value() - 2.5) < 0.01
        assert abs(w.value() - 10.0) < 0.01
        assert abs(h.value() - 15.0) < 0.01

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
        lshape_panel.set_cut_rects([
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 0.0,
             'w_cm': 10.0, 'h_cm': 15.0},
            {'anchor': 'tr', 'offset_x_cm': 10.0, 'offset_y_cm': 15.0,
             'w_cm': 8.0, 'h_cm': 12.0},
        ])
        qapp.processEvents()
        result = lshape_panel.get_cut_rects_cm()
        assert len(result) == 2
        assert result[0]['anchor'] == 'tr'
        assert abs(result[0]['w_cm'] - 10.0) < 0.01
        assert abs(result[1]['offset_x_cm'] - 10.0) < 0.01

    def test_skips_zero_size_rows(self, qapp, lshape_panel):
        lshape_panel.set_cut_rects([
            {'anchor': 'tr', 'offset_x_cm': 0.0, 'offset_y_cm': 0.0,
             'w_cm': 10.0, 'h_cm': 15.0},
        ])
        qapp.processEvents()
        lshape_panel._stair_rows[0][2].setValue(0.0)
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
        lshape_panel.set_cut_rects([
            {'anchor': 'tr', 'offset_x_cm': 0, 'offset_y_cm': 0,
             'w_cm': 10.0, 'h_cm': 15.0},
            {'anchor': 'tr', 'offset_x_cm': 10, 'offset_y_cm': 15,
             'w_cm': 8.0, 'h_cm': 12.0},
        ])
        qapp.processEvents()
        cuts = lshape_panel.get_cuts_cm()
        assert len(cuts) == 2
        assert cuts[0]['corner'] == 'tr'
        assert abs(cuts[0]['cut_w_cm'] - 10.0) < 0.01


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
