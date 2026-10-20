"""边余量在识别回填、手动编辑和外框同步后的回归测试。"""
from types import SimpleNamespace

import pytest

from gui.composite_panel import CompositePanel
from services.sketch_parser.composite_sketch_parser import CompositeSketchParseResult


def assert_margins(panel, top, bottom, left, right):
    text = panel._margin_hint.text()
    for label, value in zip(('上', '下', '左', '右'), (top, bottom, left, right)):
        assert f'{label}{value:.1f} cm' in text


def test_lshape_recognized_two_right_corners(lshape_panel):
    panel = lshape_panel
    panel.generate_requested.connect(lambda: None)
    result = SimpleNamespace(
        outer_w_cm=262.4, outer_h_cm=60.0,
        debug={'cuts_cm': [
            {'corner': 'tr', 'cut_w_cm': 14.3, 'cut_h_cm': 12.3},
            {'corner': 'br', 'cut_w_cm': 10.6, 'cut_h_cm': 4.4},
        ]})
    panel._apply_lshape_params('tr', 14.3, 12.3, result)
    assert_margins(panel, 248.1, 251.8, 60.0, 43.3)
    assert panel._params_source == 'recognize'


def test_manual_edit_then_outer_sync_keeps_both_corners(lshape_panel):
    panel = lshape_panel
    panel.set_outer_dims(262.4, 60.0)
    panel.set_lshape_cuts([
        {'corner': 'tr', 'cut_w_cm': 14.3, 'cut_h_cm': 12.3},
        {'corner': 'br', 'cut_w_cm': 10.6, 'cut_h_cm': 4.4},
    ])
    panel._corner_rows[1][2].setValue(11.6)
    panel._corner_rows[1][3].setValue(5.4)
    assert_margins(panel, 248.1, 250.8, 60.0, 42.3)
    panel.set_outer_dims(272.4, 70.0)
    assert_margins(panel, 258.1, 260.8, 70.0, 52.3)


def test_composite_recognition_refreshes_margin_hint(qapp):
    panel = CompositePanel()
    try:
        result = CompositeSketchParseResult(
            success=True, outer_w_cm=186.0, outer_h_cm=89.0,
            hole_w_cm=81.0, hole_h_cm=61.0,
            margin_top_cm=10.0, margin_bottom_cm=18.0,
            margin_left_cm=45.0, margin_right_cm=60.0,
            cuts_cm=[{'corner': 'tr', 'cut_w_cm': 35.0, 'cut_h_cm': 10.0}])
        panel._on_composite_parsed(result)
        assert_margins(panel, 150.0, 185.0, 88.0, 78.0)
        assert panel._params_source == 'recognize'
    finally:
        panel.close()


@pytest.mark.parametrize('composite', [False, True])
def test_second_corner_edits_refresh_without_preview(qapp, lshape_panel, composite):
    panel = CompositePanel() if composite else lshape_panel
    previews = []
    panel.generate_requested.connect(lambda: previews.append(True))
    try:
        panel.set_outer_dims(262.4, 60.0)
        panel.set_lshape_cuts([
            {'corner': 'tr', 'cut_w_cm': 14.3, 'cut_h_cm': 12.3},
            {'corner': 'br', 'cut_w_cm': 10.6, 'cut_h_cm': 4.4},
        ])
        enabled, combo, width, height = panel._corner_rows[1]
        assert_margins(panel, 248.1, 251.8, 60.0, 43.3)
        width.setValue(11.6)
        assert_margins(panel, 248.1, 250.8, 60.0, 43.3)
        height.setValue(5.4)
        assert_margins(panel, 248.1, 250.8, 60.0, 42.3)
        enabled.setChecked(False)
        assert_margins(panel, 248.1, 262.4, 60.0, 47.7)
        enabled.setChecked(True)
        combo.setCurrentIndex(combo.findData('bl'))
        assert_margins(panel, 248.1, 250.8, 54.6, 47.7)
        assert not previews
    finally:
        if composite:
            panel.close()


def test_margin_overflow_warning_after_blocked_fill(lshape_panel):
    panel = lshape_panel
    panel.set_outer_dims(100.0, 20.0)
    panel.set_lshape_cuts([
        {'corner': 'tr', 'cut_w_cm': 10.0, 'cut_h_cm': 12.0},
        {'corner': 'br', 'cut_w_cm': 10.0, 'cut_h_cm': 10.0},
    ])
    assert_margins(panel, 90.0, 90.0, 20.0, -2.0)
    assert '输入超出外框' in panel._margin_hint.text()
    panel.set_outer_dims(100.0, 30.0)
    assert_margins(panel, 90.0, 90.0, 30.0, 8.0)
    assert '输入超出外框' not in panel._margin_hint.text()


@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
def test_single_corner_fill_and_clear_refreshes(lshape_panel, corner):
    panel = lshape_panel
    panel.set_outer_dims(100.0, 80.0)
    panel.set_lshape_params(corner, 20.0, 10.0)
    assert_margins(panel,
                   80.0 if corner in ('tl', 'tr') else 100.0,
                   80.0 if corner in ('bl', 'br') else 100.0,
                   70.0 if corner in ('tl', 'bl') else 80.0,
                   70.0 if corner in ('tr', 'br') else 80.0)
    panel.set_lshape_cuts([])
    assert_margins(panel, 100.0, 100.0, 80.0, 80.0)


def staircase_strips(anchor):
    return [
        {'anchor': anchor, 'offset_x_cm': 0, 'offset_y_cm': 0,
         'w_cm': 30, 'h_cm': 8},
        {'anchor': anchor, 'offset_x_cm': 0, 'offset_y_cm': 8,
         'w_cm': 10, 'h_cm': 12},
    ]


@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
def test_staircase_margin_fill_edit_and_outer_sync(lshape_panel, corner):
    panel = lshape_panel
    previews = []
    panel.generate_requested.connect(lambda: previews.append(True))
    panel.set_outer_dims(100, 80)
    panel.set_cut_rects(staircase_strips(corner))
    hint = panel._corner_control._stair_margin_hint
    assert hint.parentWidget() is panel._gb_staircase
    assert not hint.isHidden()
    assert not panel._gb_staircase.isHidden()
    assert_margins(panel,
                   70 if corner in ('tl', 'tr') else 100,
                   70 if corner in ('bl', 'br') else 100,
                   60 if corner in ('tl', 'bl') else 80,
                   60 if corner in ('tr', 'br') else 80)
    assert hint.text() == panel._margin_hint.text()
    panel._stair_corner.setCurrentIndex(panel._stair_corner.findData('tr'))
    panel._stair_rows[1][0].setValue(15)
    panel._stair_rows[1][1].setValue(14)
    assert_margins(panel, 65, 100, 80, 58)
    panel.set_outer_dims(110, 90)
    assert_margins(panel, 75, 110, 90, 68)
    panel._sp_outer_h.setValue(101)
    assert_margins(panel, 75, 110, 100, 78)
    panel._on_stair_remove_level()
    assert_margins(panel, 90, 110, 100, 92)
    assert hint.text() == panel._margin_hint.text()
    assert not previews


def test_multicorner_staircase_margin_toggle_overflow_and_switch(lshape_panel):
    panel = lshape_panel
    panel.set_outer_dims(100, 35)
    panel.set_cut_rects(staircase_strips('tr') + staircase_strips('br'))
    control = panel._corner_control
    hint = control._multi_stair_margin_hint
    assert hint.parentWidget() is control._gb_multi_staircase
    assert not hint.isHidden()
    assert not control._gb_multi_staircase.isHidden()
    assert_margins(panel, 70, 70, 35, -5)
    assert '输入超出外框' in hint.text()
    assert '#C0392B' in hint.styleSheet()
    control._multi_stair_groups['br']['box'].setChecked(False)
    assert_margins(panel, 70, 100, 35, 15)
    assert '输入超出外框' not in hint.text()
    group = control._multi_stair_groups['tr']
    control.add_level(group)
    group['rows'][-1][0].setValue(5)
    group['rows'][-1][1].setValue(4)
    assert_margins(panel, 65, 100, 35, 11)
    assert hint.text() == panel._margin_hint.text()
    control.remove_level(group)
    assert_margins(panel, 70, 100, 35, 15)
    panel._mode_combo.setCurrentIndex(panel._mode_combo.findData('staircase'))
    assert control._stair_margin_hint.text() == hint.text()
    assert not panel._gb_staircase.isHidden()
    panel.clear_lshape_params()
    assert_margins(panel, 4, 4, 4, 4)
    panel.set_outer_dims(100, 35)
    assert_margins(panel, 100, 100, 35, 35)
    for mode, active_hint in (
            ('staircase', control._stair_margin_hint),
            ('multicorner_staircase', hint)):
        panel._mode_combo.setCurrentIndex(panel._mode_combo.findData(mode))
        assert_margins(panel, 100, 100, 35, 35)
        assert active_hint.text() == panel._margin_hint.text()
