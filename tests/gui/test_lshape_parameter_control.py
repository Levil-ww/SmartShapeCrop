from gui.lshape_panel import LShapePanel


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
    control.set_outer_size(42, 24)
    assert control.get_outer_size() == (42.0, 24.0)
    panel.set_lshape_params('tr', 10, 8)
    assert panel.get_corner() == 'tr'
    assert panel.get_cut_w_cm() == 10
    assert panel.get_cut_h_cm() == 8
    assert control.build_staircase_ui is not None
    panel.close()
