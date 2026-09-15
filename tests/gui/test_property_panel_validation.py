from gui.property_panel_generate import format_design_validation_error


def test_edge_validation_error_is_explicit_for_gui():
    message = format_design_validation_error(
        ValueError('L 形挖角在右边上的尺寸和 48cm 必须小于外边长度 50cm 减 2cm 余量')
    )

    assert '参数校验失败' in message
    assert '右边' in message
    assert '减小该边相邻挖角尺寸' in message


def test_general_validation_error_keeps_original_detail():
    message = format_design_validation_error(ValueError('canvas_w_cm 必须为正数'))

    assert message == '参数校验失败：canvas_w_cm 必须为正数'