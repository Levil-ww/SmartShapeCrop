"""
tests/gui/test_main_window.py
主窗口装配：三个标签页的挂载、核心子组件的存在、以及信号-槽接线是否完好。

为什么值得测：main.py 的信号接线一旦断开（信号改名、槽改名、漏接），
界面不会报错、也不会崩溃，只会"点了没反应"——这类静默失效最难靠手点发现。
本文件用 disconnect 探测法逐条验证接线，任何一条断了都会立刻红。
"""
import pytest

from PyQt5.QtWidgets import (
    QTabWidget, QMainWindow, QMenuBar, QStatusBar,
)

from gui.canvas_widget import PreviewCanvas
from gui.property_panel import PropertyPanel
from gui.cropper_panel import CropperPanel
from gui.lshape_panel import LShapePanel

EXPECTED_TABS = ['圆角裁剪工具', '水池设计器', 'L形挖角设计']


class TestMainWindowAssembly:
    """主窗口的部件装配。"""

    def test_three_tabs_present_in_order(self, main_window):
        tabs = main_window._tabs
        assert isinstance(tabs, QTabWidget), '主窗口缺少 QTabWidget'
        actual = [tabs.tabText(i) for i in range(tabs.count())]
        assert actual == EXPECTED_TABS, (
            f'标签页不符。期望 {EXPECTED_TABS}，实际 {actual}'
        )

    def test_core_subwidgets_are_mounted(self, main_window):
        """canvas / panel / cropper / lshape_panel 必须存在且类型正确。"""
        assert isinstance(main_window.canvas, PreviewCanvas)
        assert isinstance(main_window.panel, PropertyPanel)
        assert isinstance(main_window.cropper, CropperPanel)
        assert isinstance(main_window.lshape_panel, LShapePanel)

    def test_tabs_hold_the_expected_widgets(self, main_window):
        """标签页里的部件应当就是 MainWindow 上挂的那几个（防止挂错/重复构造）。"""
        tabs = main_window._tabs
        widgets = [tabs.widget(i) for i in range(tabs.count())]
        assert widgets[0] is main_window.cropper, '第一个标签页应为圆角裁剪工具'
        assert widgets[1] is main_window.panel, '第二个标签页应为水池设计器'
        assert widgets[2] is main_window.lshape_panel, '第三个标签页应为 L形挖角设计'

    def test_lshape_panel_is_injected_into_property_panel(self, main_window):
        """main.py 调用 set_lshape_panel 注入引用，两个面板靠它协同。"""
        assert getattr(main_window.panel, '_lshape_panel', None) is main_window.lshape_panel, (
            'PropertyPanel 未持有 LShapePanel 引用，两面板协同会失效'
        )

    def test_menu_and_status_bar_built(self, main_window):
        assert isinstance(main_window.menuBar(), QMenuBar)
        assert isinstance(main_window.statusBar(), QStatusBar)
        # 文件菜单与帮助菜单是主窗口承诺的功能入口
        menu_titles = [a.text() for a in main_window.menuBar().actions() if a.text()]
        assert any('文件' in t for t in menu_titles), (
            f'未找到「文件」菜单，现有菜单：{menu_titles}'
        )

    def test_save_guard_initialized(self, main_window):
        """[Fix 2026-09-02 B] 导出防重复点击依赖 _is_saving 初值。"""
        assert main_window._is_saving is False, '_is_saving 初值应为 False'
        assert main_window._save_worker is None, '_save_worker 初值应为 None'


class TestMainWindowSignalWiring:
    """main.py __init__ 中的信号-槽接线（共 5 条）。"""

    def test_panel_design_changed_wired(self, main_window, gui_helpers):
        gui_helpers.assert_signal_connected(
            main_window.panel.design_changed,
            main_window._on_design_changed,
            'PropertyPanel.design_changed -> MainWindow._on_design_changed',
        )

    def test_panel_save_requested_wired(self, main_window, gui_helpers):
        gui_helpers.assert_signal_connected(
            main_window.panel.save_requested,
            main_window._on_save,
            'PropertyPanel.save_requested -> MainWindow._on_save',
        )

    def test_panel_sketch_loaded_wired(self, main_window, gui_helpers):
        gui_helpers.assert_signal_connected(
            main_window.panel.sketch_loaded,
            main_window._on_sketch_loaded,
            'PropertyPanel.sketch_loaded -> MainWindow._on_sketch_loaded',
        )

    def test_canvas_rendered_wired(self, main_window, gui_helpers):
        gui_helpers.assert_signal_connected(
            main_window.canvas.rendered,
            main_window._on_rendered,
            'PreviewCanvas.rendered -> MainWindow._on_rendered',
        )

    def test_cropper_image_cropped_wired(self, main_window, gui_helpers):
        gui_helpers.assert_signal_connected(
            main_window.cropper.image_cropped,
            main_window._on_cropped_image,
            'CropperPanel.image_cropped -> MainWindow._on_cropped_image',
        )

    def test_lshape_signals_wired_into_property_panel(self, main_window, gui_helpers):
        """set_lshape_panel 内部建立的关键接线（取 3 条代表性链路）。"""
        panel = main_window.panel
        lp = main_window.lshape_panel
        gui_helpers.assert_signal_connected(
            lp.lshape_params_changed, panel._on_lshape_params_changed,
            'LShapePanel.lshape_params_changed -> PropertyPanel._on_lshape_params_changed',
        )
        gui_helpers.assert_signal_connected(
            lp.lshape_applied, panel._on_lshape_applied,
            'LShapePanel.lshape_applied -> PropertyPanel._on_lshape_applied',
        )
        gui_helpers.assert_signal_connected(
            lp.lshape_recognize_started, panel._on_lshape_recognize_started,
            'LShapePanel.lshape_recognize_started -> PropertyPanel._on_lshape_recognize_started',
        )


class TestMainWindowShutdownContract:
    """退出清理契约：main() 中 app.aboutToQuit 依赖 cropper/canvas 的 shutdown。"""

    def test_cropper_shutdown_callable(self, main_window):
        assert callable(getattr(main_window.cropper, 'shutdown', None)), (
            'CropperPanel.shutdown 缺失，app.aboutToQuit 连接会在启动时失败'
        )

    def test_canvas_shutdown_callable(self, main_window):
        assert callable(getattr(main_window.canvas, 'shutdown', None)), (
            'PreviewCanvas.shutdown 缺失，app.aboutToQuit 连接会在启动时失败'
        )
