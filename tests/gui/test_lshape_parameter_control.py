from gui.lshape_panel import LShapePanel


def test_parameter_control_keeps_legacy_mode_contract(qapp):
    panel = LShapePanel()
    control = panel._parameter_control
    assert control._panel is panel
    assert control._corner_control is panel._corner_control
    assert control.mode_combo is panel._mode_combo
    assert control.build_staircase_ui is not None
    panel.close()
