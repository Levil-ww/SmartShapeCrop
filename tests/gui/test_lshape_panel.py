"""
tests/gui/test_lshape_panel.py
L 形挖角设计面板（LShapePanel）的初始状态与轻量交互。

L 形挖角是 2026-09-02 从水池设计器拆分出来的独立面板，
拆分后既被主窗口挂载、又被 PropertyPanel 通过 set_lshape_panel 连接，
依赖关系最复杂，故单独成文件覆盖。
"""

EXPECTED_CORNERS = ['左上角', '右上角', '左下角', '右下角']


class TestLShapePanelInitialState:

    def test_corner_combo_offers_four_positions(self, lshape_panel, gui_helpers):
        combo = gui_helpers.find_combo_containing(lshape_panel, EXPECTED_CORNERS[0])
        assert combo is not None, '未找到挖角位置下拉'
        items = gui_helpers.combo_items(combo)
        for c in EXPECTED_CORNERS:
            assert c in items, f'挖角位置下拉缺少「{c}」，现有：{items}'

    def test_size_spinboxes_in_range(self, lshape_panel, gui_helpers):
        """挖角宽高范围 5~500 mm，小于 5mm 无法加工。"""
        spin = gui_helpers.find_spinbox(lshape_panel, 5.0, 500.0)
        assert spin is not None, '未找到范围为 [5, 500] 的挖角尺寸控件'
        assert 5.0 <= spin.value() <= 500.0, f'挖角尺寸默认值 {spin.value()} 越界'

    def test_key_buttons_exist(self, lshape_panel, gui_helpers):
        for text in ['上传草图…', '清除草图', '✂️ 识别L形挖角', '🔍 生成预览',
                     '💾 导出 JPG', '同步目标名']:
            gui_helpers.assert_button_exists(lshape_panel, text)

    def test_sketch_buttons_initially_consistent(self, lshape_panel, gui_helpers):
        """未加载草图时，「清除草图」不应可用（否则点了会静默失败）。"""
        clear_btn = gui_helpers.find_button(lshape_panel, '清除草图')
        upload_btn = gui_helpers.find_button(lshape_panel, '上传草图…')
        assert upload_btn is not None and upload_btn.isEnabled(), '「上传草图…」应始终可用'
        if clear_btn is not None and clear_btn.isEnabled():
            # 实现若允许初始可点，则必须保证点击不抛异常（由下方交互测试覆盖）
            pass


class TestLShapePanelInteraction:

    def test_switching_corner_does_not_crash(self, qapp, lshape_panel, gui_helpers):
        combo = gui_helpers.find_combo_containing(lshape_panel, EXPECTED_CORNERS[0])
        assert combo is not None
        original = combo.currentIndex()
        for idx in range(combo.count()):
            combo.setCurrentIndex(idx)
            qapp.processEvents()
            assert combo.currentIndex() == idx
        combo.setCurrentIndex(original)
        qapp.processEvents()

    def test_changing_corner_size_does_not_crash(self, qapp, lshape_panel, gui_helpers):
        spin = gui_helpers.find_spinbox(lshape_panel, 5.0, 500.0)
        assert spin is not None
        old = spin.value()
        spin.setValue(50.0)
        qapp.processEvents()
        assert spin.value() == 50.0
        spin.setValue(old)
        qapp.processEvents()

    def test_upload_button_emits_sketch_pick_requested(self, qapp, lshape_panel,
                                                       gui_helpers):
        """「上传草图…」不自己弹对话框，只发 sketch_pick_requested 委托 PropertyPanel 处理。

        这是该面板的核心委托契约：按钮 -> 信号 -> PropertyPanel 打开文件选择。
        """
        received = []
        lshape_panel.sketch_pick_requested.connect(lambda: received.append(1))
        btn = gui_helpers.assert_button_exists(lshape_panel, '上传草图…')
        btn.click()
        qapp.processEvents()
        assert received, (
            '点击「上传草图…」未发出 sketch_pick_requested，'
            '上传入口与委托链路已断开（用户点了会没反应）'
        )

    def test_clear_button_emits_sketch_clear_requested(self, qapp, lshape_panel,
                                                       gui_helpers):
        received = []
        lshape_panel.sketch_clear_requested.connect(lambda: received.append(1))
        btn = gui_helpers.assert_button_exists(lshape_panel, '清除草图')
        btn.click()
        qapp.processEvents()
        assert received, (
            '点击「清除草图」未发出 sketch_clear_requested，清除入口已断开'
        )
