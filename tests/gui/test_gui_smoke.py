"""
tests/gui/test_gui_smoke.py
GUI 层构造冒烟：四大面板 + 主窗口能否在离屏环境下无异常地构建。

这一层的价值：GUI 代码 6,577 行，此前零覆盖。任何一次改动只要让面板在
构造阶段抛异常（导入错误、属性名写错、控件未初始化、布局参数非法），
程序启动时就会白屏或崩溃——而没有测试时这类问题只能靠手工点开界面发现。

本文件只做"能不能建起来"的验证，不涉及业务逻辑。
"""
import pytest

from PyQt5.QtWidgets import QWidget, QMainWindow

# 各组件构造后应当具备的最小子控件数量。
# 数值取自 2026-09-11 实测（离屏构造），仅作"UI 确实建起来了"的下界，
# 日后新增控件不必改这里；若重构导致控件批量消失，这里会红。
MIN_CHILDREN = {
    'PropertyPanel': 300,
    'CropperPanel': 80,
    'LShapePanel': 60,
    'MainWindow': 500,
}


class TestGuiConstruction:
    """四大 GUI 组件 + 主窗口的构造冒烟。"""

    def test_preview_canvas_constructs(self, preview_canvas):
        canvas = preview_canvas
        assert isinstance(canvas, QWidget), 'PreviewCanvas 应当是 QWidget 子类'
        # PreviewCanvas 是自绘控件，本身没有子控件，故只校验其可见性与有效性
        assert canvas.width() >= 0 and canvas.height() >= 0
        assert canvas.isEnabled(), '画布默认应可用'

    def test_property_panel_constructs(self, property_panel):
        panel = property_panel
        assert isinstance(panel, QWidget)
        assert len(panel.findChildren(object)) >= MIN_CHILDREN['PropertyPanel'], (
            f'PropertyPanel 子控件数 {len(panel.findChildren(object))} '
            f'低于下界 {MIN_CHILDREN["PropertyPanel"]}，UI 可能未完整构建'
        )

    def test_cropper_panel_constructs(self, cropper_panel):
        panel = cropper_panel
        assert isinstance(panel, QWidget)
        assert len(panel.findChildren(object)) >= MIN_CHILDREN['CropperPanel'], (
            f'CropperPanel 子控件数 {len(panel.findChildren(object))} '
            f'低于下界 {MIN_CHILDREN["CropperPanel"]}'
        )

    def test_lshape_panel_constructs(self, lshape_panel):
        panel = lshape_panel
        assert isinstance(panel, QWidget)
        assert len(panel.findChildren(object)) >= MIN_CHILDREN['LShapePanel'], (
            f'LShapePanel 子控件数 {len(panel.findChildren(object))} '
            f'低于下界 {MIN_CHILDREN["LShapePanel"]}'
        )

    def test_main_window_constructs(self, main_window):
        win = main_window
        assert isinstance(win, QMainWindow)
        assert 'SmartShapeCrop' in win.windowTitle(), (
            f'主窗口标题异常：{win.windowTitle()!r}'
        )
        assert win.centralWidget() is not None, '主窗口必须有中央部件'
        assert win.statusBar() is not None, '主窗口必须有状态栏'
        assert len(win.findChildren(object)) >= MIN_CHILDREN['MainWindow'], (
            f'MainWindow 子控件数 {len(win.findChildren(object))} '
            f'低于下界 {MIN_CHILDREN["MainWindow"]}'
        )

    def test_public_api_exports_are_importable(self):
        """gui/__init__.py 声明的四个公共 API 必须可导入且是类。"""
        import gui
        for name in ('PreviewCanvas', 'PropertyPanel', 'CropperPanel', 'LShapePanel'):
            assert hasattr(gui, name), f'gui 包未导出 {name}'
            assert isinstance(getattr(gui, name), type), f'gui.{name} 不是类'
        assert set(gui.__all__) == {
            'PreviewCanvas', 'PropertyPanel', 'CropperPanel', 'LShapePanel'
        }, f'gui.__all__ 与实际导出不一致：{gui.__all__}'


class TestGuiThreadHygiene:
    """后台线程卫生：构造产生的线程必须可停止，否则进程退出会崩溃。

    背景：PropertyPanel 构造时若"上次模板库目录"存在，会启动 _WarmupScanWorker；
    实测解释器退出时该线程仍在运行会让进程以退出码 127 崩溃。
    测试夹具已通过清空默认目录规避，这里验证该规避确实生效、且线程可收尾。
    """

    def test_property_panel_starts_no_thread_when_template_dir_empty(self, property_panel):
        """默认模板目录为空时，构造 PropertyPanel 不应启动任何后台线程。"""
        from PyQt5.QtCore import QThread
        running = [t for t in property_panel.findChildren(QThread) if t.isRunning()]
        assert running == [], (
            f'默认模板目录为空时仍启动了 {len(running)} 个后台线程，'
            '测试将依赖外部环境（网络盘）并可能拖慢套件'
        )

    def test_threads_can_all_be_stopped(self, qapp, main_window, gui_helpers):
        """主窗口名下所有后台线程都必须能被停止（teardown 依赖这一点）。"""
        from PyQt5.QtCore import QThread
        qapp.processEvents()
        gui_helpers.stop_threads(main_window)
        qapp.processEvents()
        still_running = [t for t in main_window.findChildren(QThread) if t.isRunning()]
        assert still_running == [], (
            f'{len(still_running)} 个线程无法停止，'
            'pytest 进程退出时可能以非 0 码崩溃'
        )
