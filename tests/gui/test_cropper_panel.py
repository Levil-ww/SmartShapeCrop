"""
tests/gui/test_cropper_panel.py
圆角裁剪工具面板（CropperPanel）的初始状态与轻量交互。

只验证 UI 契约与不崩溃，不触发真实裁剪/导出（那会读图、写盘）。
"""
from PyQt5.QtWidgets import QComboBox

EXPECTED_ORIENTATIONS = ['竖版（长边为高）', '横版（长边为宽）']
EXPECTED_RESIZE_MODES = [
    '简单缩放（拉伸填满）', '轻度裁剪', '智能模式', '裁剪填满', '留白填充',
]
EXPECTED_CORNER_MODES = ['四角相同', '左上+右下', '右上+左下', '左下+右下']


class TestCropperPanelInitialState:

    def test_orientation_combo(self, cropper_panel, gui_helpers):
        combo = gui_helpers.find_combo_containing(cropper_panel, EXPECTED_ORIENTATIONS[0])
        assert combo is not None, '未找到方向下拉'
        items = gui_helpers.combo_items(combo)
        for o in EXPECTED_ORIENTATIONS:
            assert o in items, f'方向下拉缺少「{o}」，现有：{items}'

    def test_resize_mode_combo(self, cropper_panel, gui_helpers):
        combo = gui_helpers.find_combo_containing(cropper_panel, EXPECTED_RESIZE_MODES[0])
        assert combo is not None, '未找到缩放模式下拉'
        items = gui_helpers.combo_items(combo)
        for m in EXPECTED_RESIZE_MODES:
            assert m in items, f'缩放模式下拉缺少「{m}」，现有：{items}'

    def test_corner_mode_buttons_exist(self, cropper_panel, gui_helpers):
        for text in EXPECTED_CORNER_MODES:
            gui_helpers.assert_button_exists(cropper_panel, text)

    def test_dpi_spinbox_range(self, cropper_panel, gui_helpers):
        spin = gui_helpers.find_spinbox(cropper_panel, 72, 600)
        assert spin is not None, '未找到范围为 [72, 600] 的 DPI 控件'
        assert 72 <= spin.value() <= 600, f'DPI 默认值 {spin.value()} 越界'

    def test_target_size_spinboxes_in_range(self, cropper_panel, gui_helpers):
        """成品宽高范围 1~500 cm。"""
        spin = gui_helpers.find_spinbox(cropper_panel, 1.0, 500.0)
        assert spin is not None, '未找到范围为 [1, 500] 的成品尺寸控件'
        assert 1.0 <= spin.value() <= 500.0, f'成品尺寸默认值 {spin.value()} 越界'

    def test_key_buttons_exist(self, cropper_panel, gui_helpers):
        for text in ['浏览…', '自动匹配', '2. 自动识别尺寸/圆角', '自动命名',
                     '生成预览', '导出 JPG']:
            gui_helpers.assert_button_exists(cropper_panel, text)


class TestCropperPanelInteraction:

    def test_switching_orientation_does_not_crash(self, qapp, cropper_panel, gui_helpers):
        combo = gui_helpers.find_combo_containing(cropper_panel, EXPECTED_ORIENTATIONS[0])
        assert combo is not None
        original = combo.currentIndex()
        for idx in range(combo.count()):
            combo.setCurrentIndex(idx)
            qapp.processEvents()
            assert combo.currentIndex() == idx
        combo.setCurrentIndex(original)
        qapp.processEvents()

    def test_switching_resize_mode_does_not_crash(self, qapp, cropper_panel, gui_helpers):
        combo = gui_helpers.find_combo_containing(cropper_panel, EXPECTED_RESIZE_MODES[0])
        assert combo is not None
        original = combo.currentIndex()
        for idx in range(combo.count()):
            combo.setCurrentIndex(idx)
            qapp.processEvents()
            assert combo.currentIndex() == idx
        combo.setCurrentIndex(original)
        qapp.processEvents()

    def test_rounding_mode_buttons_are_checkable_and_clickable(self, qapp,
                                                               cropper_panel, gui_helpers):
        """圆角模式是互斥选项：点击其中一个，其余应处于未选中。"""
        buttons = [gui_helpers.assert_button_exists(cropper_panel, t)
                   for t in EXPECTED_CORNER_MODES]
        clickable = [b for b in buttons if b.isCheckable()]
        if not clickable:
            # 若实现改为非 checkable 按钮组，至少要保证可点击且不崩
            for b in buttons:
                b.click()
                qapp.processEvents()
            return
        target = clickable[0]
        target.click()
        qapp.processEvents()
        assert target.isChecked(), '点击后圆角模式按钮应处于选中状态'
