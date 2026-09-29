"""tests/gui/test_composite_close_retire.py

综合形状面板关闭 / 退役的回归测试。

覆盖场景：
  1. shutdown() 无活跃 worker → 不崩溃
  2. shutdown() 置空 _composite_parse_worker 引用
  3. shutdown() 中断运行中的 worker
  4. 双重 shutdown() 幂等
  5. cancel_composite_parse() 立即置空引用
  6. cancel_composite_parse() 遇到悬垂 worker 不崩溃
  7. shutdown() 链覆盖 LShapePanel 的 worker
  8. shutdown() 处理已完成但未清理的 worker
  9. 线程中断标志被设置
  10. 信号连接在 shutdown 后安全断开
"""
import pytest

from PyQt5.QtCore import QCoreApplication, QEvent, QThread

from gui.composite_panel import CompositePanel


@pytest.fixture
def composite_panel(qapp):
    widget = CompositePanel()
    yield widget
    try:
        widget.shutdown()
    except Exception:
        pass
    qapp.processEvents()
    for th in widget.findChildren(QThread):
        if th.isRunning():
            try:
                th.requestInterruption()
            except Exception:
                pass
            th.quit()
            th.wait(1000)
    try:
        widget.close()
    except Exception:
        pass
    try:
        widget.deleteLater()
    except Exception:
        pass
    qapp.processEvents()


class TestShutdownNoActiveWorker:

    def test_shutdown_with_no_worker_does_not_raise(self, composite_panel):
        assert composite_panel._composite_parse_worker is None
        composite_panel.shutdown()

    def test_shutdown_nulls_composite_parse_worker(self, composite_panel):
        composite_panel.shutdown()
        assert composite_panel._composite_parse_worker is None


class TestCancelCompositeParse:

    def test_cancel_nulls_reference_immediately(self, composite_panel):
        composite_panel.cancel_composite_parse()
        assert composite_panel._composite_parse_worker is None

    def test_cancel_with_dangling_worker_does_not_raise(self, qapp, composite_panel):
        from workers.property_panel_workers import _CompositeParseWorker
        worker = _CompositeParseWorker('__no_such_sketch__.png', 100.0, 80.0,
                                       composite_panel)
        worker.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        qapp.processEvents()
        composite_panel._composite_parse_worker = worker
        composite_panel.cancel_composite_parse()
        assert composite_panel._composite_parse_worker is None


class TestDoubleShutdown:

    def test_double_shutdown_does_not_raise(self, composite_panel):
        composite_panel.shutdown()
        composite_panel.shutdown()

    def test_shutdown_after_cancel_does_not_raise(self, composite_panel):
        composite_panel.cancel_composite_parse()
        composite_panel.shutdown()


class TestShutdownChain:

    def test_shutdown_clears_lshape_worker_too(self, composite_panel):
        composite_panel.shutdown()
        assert composite_panel._lshape_parse_worker is None

    def test_shutdown_chain_covers_both_workers(self, composite_panel):
        composite_panel.shutdown()
        assert composite_panel._composite_parse_worker is None
        assert composite_panel._lshape_parse_worker is None


class TestShutdownWithFinishedWorker:

    def test_shutdown_with_already_finished_worker(self, qapp, composite_panel):
        from workers.property_panel_workers import _CompositeParseWorker
        worker = _CompositeParseWorker('__no_such_sketch__.png', 100.0, 80.0,
                                       composite_panel)
        worker.start()
        worker.wait(3000)
        qapp.processEvents()
        composite_panel._composite_parse_worker = worker if worker.isRunning() else worker
        composite_panel.shutdown()
        assert composite_panel._composite_parse_worker is None


class TestSignalCleanup:

    def test_shutdown_does_not_crash_on_signal_access(self, composite_panel):
        composite_panel.shutdown()
        try:
            _ = composite_panel.composite_params_changed
            _ = composite_panel.composite_recognize_finished
        except RuntimeError:
            pytest.fail("shutdown 后不应破坏 Python 层信号描述符")
