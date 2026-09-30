from gui.lshape_panel import LShapePanel
from core.config import CUT_LOSS_CM


def test_standard_snapshot_converts_canvas_once_and_is_detached(qapp):
    panel = LShapePanel()
    panel.set_outer_dims(80, 60)
    panel.set_lshape_params('bl', 12, 9)
    control = panel._parameter_control
    snapshot = control.build_parameter_snapshot()
    assert snapshot == {
        'corner': 'bl', 'cut_w_cm': 12, 'cut_h_cm': 9,
        'cuts_cm': [{'corner': 'bl', 'cut_w_cm': 12, 'cut_h_cm': 9}],
        'outer_w_cm': 80, 'outer_h_cm': 60,
    }
    snapshot['cuts_cm'][0]['cut_w_cm'] = 99
    assert panel.get_cut_w_cm() == 12
    assert panel.get_lshape_params()['cuts_cm'][0]['cut_w_cm'] == 12
    # Old CornerCutControl APIs must delegate without recursive calls.
    assert panel._corner_control.build_manual_params(80, 60)['cut_w_cm'] == 12
    panel.close()


def test_staircase_snapshot_and_outer_edit_preserve_strips(qapp):
    panel = LShapePanel()
    panel.set_outer_dims(80, 60)
    rects = [
        {'anchor': 'tl', 'offset_x_cm': 0, 'offset_y_cm': 0, 'w_cm': 30, 'h_cm': 5},
        {'anchor': 'tl', 'offset_x_cm': 0, 'offset_y_cm': 5, 'w_cm': 20, 'h_cm': 7},
    ]
    panel.set_cut_rects(rects)
    snapshot = panel._parameter_control.build_parameter_snapshot()
    assert snapshot == {
        'corner': 'tl', 'cut_w_cm': 30, 'cut_h_cm': 12,
        'cuts_cm': [], 'cut_rects': rects,
        'outer_w_cm': 80, 'outer_h_cm': 60,
    }
    panel._sp_outer_w.setValue(90 + CUT_LOSS_CM)
    assert panel.get_lshape_params()['outer_w_cm'] == 90
    assert panel.get_lshape_params()['cut_w_cm'] == 30
    assert panel.get_lshape_params()['cut_rects'] == rects
    panel._corner_control.on_staircase_changed()
    assert panel.get_lshape_params()['cut_h_cm'] == 12
    panel.close()


def test_parameter_notifications_follow_model_updates(qapp):
    panel = LShapePanel()
    events = []
    panel._parameter_control.parametersEdited.connect(
        lambda mode: events.append((mode, dict(panel.get_lshape_params()))))
    renders = []
    panel.lshape_params_changed.connect(lambda: renders.append(True))
    panel._sp_outer_w.setValue(81)
    assert events[-1][0] == 'standard'
    assert events[-1][1]['outer_w_cm'] == 80
    panel.set_cut_rects([
        {'anchor': 'tr', 'offset_x_cm': 0, 'offset_y_cm': 0, 'w_cm': 20, 'h_cm': 5},
    ])
    panel._stair_rows[0][0].setValue(25)
    assert events[-1][0] == 'staircase'
    assert events[-1][1]['cut_w_cm'] == 25
    assert renders == []
    panel.close()


def test_parameter_control_keeps_legacy_mode_contract(qapp):
    panel = LShapePanel()
    control = panel._parameter_control
    assert control._panel is panel
    assert control._corner_control is panel._corner_control
    assert control.mode_combo is panel._mode_combo
    assert control._gb_outer is panel._gb_outer
    assert control._sp_outer_w is panel._sp_outer_w
    assert control._sp_outer_h is panel._sp_outer_h
    assert control._gb_l is panel._gb_l
    assert control._sp_lw is panel._sp_lw
    assert control._sp_lh is panel._sp_lh
    assert control.get_outer_size() == (panel._sp_outer_w.value(), panel._sp_outer_h.value())
    assert control.get_cuts_cm() == panel.get_cuts_cm()
    snapshot_cuts, primary = control.manual_cut_snapshot()
    assert snapshot_cuts == panel.get_cuts_cm()
    assert primary['corner'] == panel.get_corner()
    assert control.build_manual_params(80, 60)['outer_w_cm'] == 80
    control.set_outer_size(42, 24)
    assert control.get_outer_size() == (42.0, 24.0)
    panel.set_lshape_params('tr', 10, 8)
    assert panel.get_corner() == 'tr'
    assert panel.get_cut_w_cm() == 10
    assert panel.get_cut_h_cm() == 8
    assert panel.get_lshape_params()['cut_w_cm'] == 10
    panel.set_lshape_cuts([{'corner': 'bl', 'cut_w_cm': 7, 'cut_h_cm': 6}])
    assert panel.get_lshape_params()['cuts_cm'] == panel.get_cuts_cm()
    assert control.build_staircase_ui is not None
    panel.close()


def test_staircase_refill_resizes_rows_and_updates_model(qapp):
    panel = LShapePanel()
    panel.set_outer_dims(80, 60)
    rects = [
        {'anchor': 'tr', 'offset_x_cm': 0, 'offset_y_cm': 0, 'w_cm': 30, 'h_cm': 5},
        {'anchor': 'tr', 'offset_x_cm': 0, 'offset_y_cm': 5, 'w_cm': 20, 'h_cm': 7},
        {'anchor': 'tr', 'offset_x_cm': 0, 'offset_y_cm': 12, 'w_cm': 8, 'h_cm': 4},
    ]
    panel.set_cut_rects(rects)
    assert len(panel._stair_rows) == 3
    assert panel.get_cut_rects_cm() == rects
    assert panel.get_lshape_params()['outer_w_cm'] == 80
    panel.set_cut_rects(rects[:1])
    assert len(panel._stair_rows) == 1
    assert panel.get_cut_rects_cm() == rects[:1]
    assert panel._stair_rows is panel._parameter_control._stair_rows
    panel.close()
