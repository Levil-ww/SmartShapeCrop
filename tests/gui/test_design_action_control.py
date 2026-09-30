"""Compatibility and signal contracts for the extracted action sections."""
from gui.design_action_control import DesignActionControl
from gui.lshape_panel import LShapePanel


def test_buttons_emit_once_without_checked_argument(qapp):
    control = DesignActionControl()
    received = []
    for button, signal, name in (
        (control.recognize_button, control.recognizeRequested, 'recognize'),
        (control.generate_button, control.generateRequested, 'generate'),
        (control.save_button, control.saveRequested, 'save'),
    ):
        signal.connect(lambda name=name: received.append(name))
        button.click()
    assert received == ['recognize', 'generate', 'save']
    control.close()


def test_generate_busy_state_and_status(qapp):
    control = DesignActionControl()
    control.set_generate_enabled(False, '生成中…')
    assert not control.generate_button.isEnabled()
    assert control.generate_button.text() == '生成中…'
    control.set_generate_enabled(True)
    assert control.generate_button.isEnabled()
    assert control.generate_button.text() == '生成中…'
    control.set_status('失败', True)
    assert control.status_label.text() == '失败'
    assert '#FFEBEE' in control.status_label.styleSheet()
    control.set_status('完成')
    assert control.status_label.text() == '完成'
    assert '#E8F5E9' in control.status_label.styleSheet()
    control.close()


def test_panel_legacy_fields_share_control_state(qapp):
    panel = LShapePanel()
    control = panel._action_control
    assert panel._btn_generate is control.generate_button
    assert panel._btn_save is control.save_button
    assert panel._btn_lshape is control.recognize_button
    assert panel._lshape_status is control.status_label
    assert panel._gb_lshape_recog is control.group
    assert panel._action_layout is control.action_layout
    panel.set_generate_enabled(False, '等待')
    assert not control.generate_button.isEnabled()
    panel._set_status('错误', True)
    assert control.status_label.text() == '错误'
    panel.close()
