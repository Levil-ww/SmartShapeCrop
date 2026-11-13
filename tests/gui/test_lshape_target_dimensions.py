"""L 形目标文件名应同步外框尺寸。"""


def test_lshape_target_updates_outer_size_with_cut_loss(property_panel, lshape_panel):
    property_panel.set_lshape_panel(lshape_panel)
    lshape_panel.set_outer_dims(175, 56)
    lshape_panel.set_lshape_params('tl', 7, 33)
    lshape_panel._target_edit.setText('皮革-定制-裁剪有图-蝴蝶契约;59x107.5CM裁剪有图盖火裁剪余料一起发')

    assert lshape_panel._sp_outer_w.value() == 108.5
    assert lshape_panel._sp_outer_h.value() == 60.0
    assert lshape_panel.get_lshape_params()['outer_w_cm'] == 107.5
    assert lshape_panel.get_lshape_params()['outer_h_cm'] == 59.0
    assert lshape_panel.get_cut_w_cm() == 7
    assert lshape_panel.get_cut_h_cm() == 33

    # 不含尺寸的名称不得重置用户已有的外框。
    lshape_panel._target_edit.setText('蝴蝶契约')
    assert lshape_panel._sp_outer_w.value() == 108.5
    assert lshape_panel._sp_outer_h.value() == 60.0
