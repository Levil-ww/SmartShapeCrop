"""
tests/gui/test_signals_contract.py
信号契约：各面板对外承诺的 pyqtSignal 必须存在且可连接。

为什么单独测：信号是面板与外界的唯一契约。main.py、property_panel.py 都在
__init__ 阶段 connect 这些信号；一旦某个信号被改名或删除，程序会在构造主窗口
时抛 AttributeError 直接崩溃。这里把契约固化下来，同时给出可读的失败提示。

采用"必须包含"而非"必须等于"：日后新增信号无需改测试，删除/改名则会立刻红。
"""


class TestPreviewCanvasSignals:

    def test_required_signals_exist(self, preview_canvas, gui_helpers):
        have = gui_helpers.custom_signal_names(preview_canvas)
        assert 'rendered' in have, (
            f'PreviewCanvas 缺少 rendered 信号（main.py 依赖它刷新画布）。现有：{sorted(have)}'
        )

    def test_signals_are_connectable(self, preview_canvas):
        """信号必须能真实连接。用"精确断开"验证：若未建立连接，disconnect(槽) 会抛 TypeError。"""
        def _slot(*_args):
            pass
        preview_canvas.rendered.connect(_slot)
        preview_canvas.rendered.disconnect(_slot)


class TestPropertyPanelSignals:

    REQUIRED = {
        'design_changed',      # -> MainWindow._on_design_changed
        'save_requested',      # -> MainWindow._on_save
        'sketch_loaded',       # -> MainWindow._on_sketch_loaded
        'export_psd_requested',
        'pool_generate_succeeded',
    }

    def test_required_signals_exist(self, property_panel, gui_helpers):
        have = gui_helpers.custom_signal_names(property_panel)
        missing = self.REQUIRED - have
        assert not missing, (
            f'PropertyPanel 缺少信号 {sorted(missing)}。现有：{sorted(have)}'
        )


class TestCropperPanelSignals:

    def test_required_signals_exist(self, cropper_panel, gui_helpers):
        have = gui_helpers.custom_signal_names(cropper_panel)
        assert 'image_cropped' in have, (
            f'CropperPanel 缺少 image_cropped 信号（main.py 依赖它接收裁剪结果）。'
            f'现有：{sorted(have)}'
        )


class TestLShapePanelSignals:

    # 2026-09-11 实测：LShapePanel 共 13 个自定义信号，
    # 其中 10 个被 property_panel.set_lshape_panel 连接。
    REQUIRED = {
        'generate_requested',
        'lshape_applied',
        'lshape_params_changed',
        'lshape_recognize_finished',
        'lshape_recognize_started',
        'save_requested',
        'sketch_clear_requested',
        'sketch_load_requested',
        'sketch_pick_requested',
        'sketch_view_requested',
        'target_changed',
        'target_clear_requested',
        'target_pick_requested',
    }

    def test_required_signals_exist(self, lshape_panel, gui_helpers):
        have = gui_helpers.custom_signal_names(lshape_panel)
        missing = self.REQUIRED - have
        assert not missing, (
            f'LShapePanel 缺少信号 {sorted(missing)}。现有：{sorted(have)}'
        )

    def test_all_required_signals_connectable(self, lshape_panel):
        """逐个信号连接后再精确断开，确保没有签名损坏的信号。"""
        for name in sorted(self.REQUIRED):
            sig = getattr(lshape_panel, name)

            def _slot(*_args, _n=name):
                pass

            sig.connect(_slot)
            sig.disconnect(_slot)


class TestCrossPanelInjection:
    """set_lshape_panel 的注入契约：注入前无连接，注入后建立跨面板接线。"""

    def test_panel_has_no_lshape_ref_before_injection(self, property_panel):
        assert property_panel._lshape_panel is None, (
            '独立构造的 PropertyPanel 不应持有 LShapePanel 引用'
        )

    def test_injection_establishes_wiring(self, property_panel, lshape_panel, gui_helpers):
        property_panel.set_lshape_panel(lshape_panel)
        assert property_panel._lshape_panel is lshape_panel, '注入后应持有引用'
        gui_helpers.assert_signal_connected(
            lshape_panel.lshape_params_changed,
            property_panel._on_lshape_params_changed,
            'LShapePanel.lshape_params_changed -> PropertyPanel._on_lshape_params_changed',
        )
        gui_helpers.assert_signal_connected(
            lshape_panel.lshape_applied,
            property_panel._on_lshape_applied,
            'LShapePanel.lshape_applied -> PropertyPanel._on_lshape_applied',
        )
