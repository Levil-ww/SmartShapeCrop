"""
tests/gui/conftest.py
GUI 层测试的公共夹具（离屏运行，不弹真实窗口）。

背景（2026-09-11）：
- tests/gui/ 自建目录以来长期为空，GUI 层 6,577 行代码零测试覆盖，
  被《V2.2 全面检测报告》列为主要短板。本文件为该层建立可运行的基础设施。

三个必须处理的环境问题（都是实测踩出来的，改动前请先读完）：

1) 必须离屏。测试环境无显示设备，若不设 QT_QPA_PLATFORM=offscreen，
   QApplication 会直接以 "could not connect to display" 失败。
   该变量必须在 QApplication 创建**之前**写入 os.environ，故置于本模块顶层。

2) 必须停止后台线程。PropertyPanel 构造时会读取"上次模板库目录"，
   若目录存在则启动 _WarmupScanWorker 后台扫描；解释器退出时该线程仍在运行
   会导致进程以退出码 127 崩溃（实测：构造 PropertyPanel 后退出码 127，
   停掉线程后恢复 0）。teardown 中用 findChildren(QThread) 统一收尾。

3) 必须隔离持久化设置。AppSettings 走 QSettings（Windows 注册表），
   且默认模板库目录在本机是网络路径（\\\\192.168.1.199\\...，21 万张图）。
   直接构造面板会：污染用户真实配置 + 触发网络盘扫描 + 测试结果依赖外部环境。
   故用独立 organization/app_name 的测试实例替换模块级单例，
   并把默认模板目录清空（同时让第 2 点的预热线程不再启动）。

以上处理全部在测试侧完成，不修改 gui/ core/ 任何一行源码。
"""
import os
import sys

# 必须在 QApplication 创建前设置，且不能覆盖外部显式指定的值
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path

import pytest

# PyQt5 缺失时整目录跳过，而不是让 56 个用例全部报 ImportError。
# （GUI 测试依赖真实的 Qt 运行时，无法用 stub 替代。）
pytest.importorskip('PyQt5.QtWidgets', reason='GUI 测试需要 PyQt5')

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtCore import QThread
from PyQt5.QtWidgets import QApplication, QAbstractButton, QWidget


# ---------------------------------------------------------------------------
# QApplication（会话级单例）
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session')
def qapp():
    """整个测试会话共用一个 QApplication。QApplication 全局唯一，不可重复创建。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    assert app is not None, 'QApplication 创建失败'
    yield app
    app.processEvents()


# ---------------------------------------------------------------------------
# 设置隔离（autouse，对 tests/gui/ 下所有用例生效）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_app_settings(monkeypatch):
    """把 AppSettings 单例替换为测试专用实例，避免读写用户真实配置与网络目录。"""
    import core.app_settings as app_settings_mod

    test_settings = app_settings_mod.AppSettings(
        organization='SmartShapeCropTest',
        app_name='SmartShapeCropTest',
    )
    # 清空默认模板库目录：既避免网络盘扫描，也避免预热线程启动
    try:
        test_settings.set_default_template_dir('')
    except Exception:
        pass

    monkeypatch.setattr(app_settings_mod, '_app_settings_singleton', test_settings, raising=True)
    yield test_settings
    # 清理测试写入的注册表键，不留下痕迹
    try:
        test_settings._qs.clear()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 线程清理
# ---------------------------------------------------------------------------

def stop_threads(widget, timeout_ms: int = 3000) -> int:
    """停止 widget 名下所有仍在运行的 QThread，返回停止的线程数。

    用于 teardown：避免解释器退出时因工作线程仍在运行而崩溃（退出码 127）。
    """
    stopped = 0
    for th in widget.findChildren(QThread):
        if th.isRunning():
            try:
                th.requestInterruption()
            except Exception:
                pass
            th.quit()
            if not th.wait(timeout_ms):
                th.terminate()
                th.wait(1000)
            stopped += 1
    return stopped


def _dispose(qapp, widget):
    """统一的销毁流程：停线程 -> close -> deleteLater -> 处理残留事件。"""
    qapp.processEvents()
    stop_threads(widget)
    try:
        widget.close()
    except Exception:
        pass
    try:
        widget.deleteLater()
    except Exception:
        pass
    qapp.processEvents()


# ---------------------------------------------------------------------------
# 组件夹具
# ---------------------------------------------------------------------------

def _make(qapp, factory):
    widget = factory()
    yield widget
    _dispose(qapp, widget)


@pytest.fixture
def preview_canvas(qapp):
    from gui.canvas_widget import PreviewCanvas
    widget = PreviewCanvas()
    yield widget
    _dispose(qapp, widget)


@pytest.fixture
def property_panel(qapp):
    from gui.property_panel import PropertyPanel
    widget = PropertyPanel()
    yield widget
    _dispose(qapp, widget)


@pytest.fixture
def cropper_panel(qapp):
    from gui.cropper_panel import CropperPanel
    widget = CropperPanel()
    yield widget
    _dispose(qapp, widget)


@pytest.fixture
def lshape_panel(qapp):
    from gui.lshape_panel import LShapePanel
    widget = LShapePanel()
    yield widget
    _dispose(qapp, widget)


@pytest.fixture
def main_window(qapp):
    import main
    widget = main.MainWindow()
    yield widget
    _dispose(qapp, widget)


# ---------------------------------------------------------------------------
# 断言辅助
# ---------------------------------------------------------------------------

def button_texts(widget):
    """收集 widget 下所有按钮的可见文本（去重前的原始顺序）。"""
    return [b.text().strip() for b in widget.findChildren(QAbstractButton) if b.text().strip()]


def find_button(widget, text):
    """按精确文本查找按钮，找不到返回 None。"""
    for b in widget.findChildren(QAbstractButton):
        if b.text().strip() == text:
            return b
    return None


def find_combo_containing(widget, text):
    """找到下拉框中**包含**指定选项文本的第一个 QComboBox。

    本项目的 GUI 控件大多未设置 objectName，无法按名字定位，
    只能按内容识别——这也是这里必须用"包含某选项"而非名字查找的原因。
    """
    from PyQt5.QtWidgets import QComboBox
    for c in widget.findChildren(QComboBox):
        for i in range(c.count()):
            if c.itemText(i).strip() == text:
                return c
    return None


def combo_items(combo):
    return [combo.itemText(i).strip() for i in range(combo.count())]


def find_spinbox(widget, vmin, vmax):
    """按取值范围精确匹配 QSpinBox / QDoubleSpinBox。"""
    from PyQt5.QtWidgets import QSpinBox, QDoubleSpinBox
    for s in list(widget.findChildren(QSpinBox)) + list(widget.findChildren(QDoubleSpinBox)):
        if s.minimum() == vmin and s.maximum() == vmax:
            return s
    return None


def assert_button_exists(widget, text):
    btn = find_button(widget, text)
    assert btn is not None, (
        f'{type(widget).__name__} 下未找到按钮「{text}」。'
        f'现有按钮：{button_texts(widget)}'
    )
    return btn


# ---------------------------------------------------------------------------
# 辅助函数出口
# ---------------------------------------------------------------------------
# 说明：tests/ 的子目录不是 Python 包（无 __init__.py），测试文件无法用
# `from .conftest import ...` 相对导入。因此统一通过 gui_helpers 夹具暴露，
# 与既有 tests/ 目录结构保持一致，不为 GUI 测试单独引入包结构。

@pytest.fixture
def gui_helpers():
    from types import SimpleNamespace
    return SimpleNamespace(
        stop_threads=stop_threads,
        button_texts=button_texts,
        find_button=find_button,
        assert_button_exists=assert_button_exists,
        assert_signal_connected=assert_signal_connected,
        custom_signal_names=custom_signal_names,
        find_combo_containing=find_combo_containing,
        combo_items=combo_items,
        find_spinbox=find_spinbox,
    )


def custom_signal_names(obj, base_cls=None):
    """列出 obj 所属类上"自定义"的 pyqtSignal（排除 Qt 基类内置信号）集合。

    base_cls 默认为 QWidget；测 QMainWindow 子类时传入 QMainWindow，
    否则 iconSizeChanged 等基类信号会被误判为自定义信号。
    """
    if base_cls is None:
        base_cls = QWidget
    base = {n for n in dir(base_cls)
            if type(getattr(base_cls, n, None)).__name__ == 'pyqtSignal'}
    cls = type(obj)
    return {n for n in dir(cls)
            if type(getattr(cls, n, None)).__name__ == 'pyqtSignal'} - base


def assert_signal_connected(signal, slot, label):
    """断言 signal 已连接到 slot，验证后原样复原连接。

    原理：PyQt5 对未建立的连接调用 disconnect(slot) 会抛 TypeError。
    先断开再接回，不改变被测对象的最终状态。
    """
    try:
        signal.disconnect(slot)
    except (TypeError, RuntimeError) as exc:
        raise AssertionError(
            f'{label} 未连接（{type(exc).__name__}: {exc}）。'
            '若这是有意的重构，请同步更新本断言与 main.py 的接线。'
        ) from exc
    signal.connect(slot)
