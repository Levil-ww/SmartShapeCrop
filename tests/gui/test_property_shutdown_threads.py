"""Closing a panel must not destroy a running prewarm QThread."""
import threading
import time

import pytest
from PyQt5.QtCore import QThread
from gui.property_panel import PropertyPanel


class SlowScan(QThread):
    def __init__(self, parent):
        super().__init__(parent)
        self.entered = threading.Event()

    def run(self):
        self.entered.set()
        while not self.isInterruptionRequested():
            time.sleep(0.005)
        # Cancellation need not complete at the instant it is requested.
        time.sleep(0.05)


@pytest.mark.parametrize('retired', [False, True])
def test_shutdown_waits_for_scan_before_parent_destruction(qapp, retired):
    panel = PropertyPanel()
    worker = SlowScan(panel)
    panel._warmup_worker = None if retired else worker
    worker.start()
    try:
        assert worker.entered.wait(1), 'Test worker did not start'
        panel.shutdown()
        assert not worker.isRunning(), 'Panel would destroy a running QThread on close'
        panel.shutdown()  # aboutToQuit also invokes shutdown after closeEvent
    finally:
        worker.requestInterruption()
        worker.wait(2000)
        panel.deleteLater()
