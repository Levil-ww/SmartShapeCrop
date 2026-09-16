"""Test signal delivery across worker thread boundary."""
import sys
import os
import time
import threading
sys.path.insert(0, os.path.dirname(__file__))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QThread, pyqtSignal, QObject

app = QApplication(sys.argv)


class Worker(QThread):
    finished_ok = pyqtSignal(object, object, str)

    def run(self):
        print(f"[Worker.run] thread={threading.current_thread().name}")
        time.sleep(0.5)
        self.finished_ok.emit("design", "sketch", "log")


class Panel(QObject):
    design_changed = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.design = "initial"

    def on_finished_ok(self, design, sketch, log):
        print(f"[Panel.on_finished_ok] thread={threading.current_thread().name}, design={design}")
        self.design = design
        n = self.receivers(self.design_changed)
        print(f"[Panel.on_finished_ok] receivers(design_changed)={n}")
        self.design_changed.emit(self.design)
        print(f"[Panel.on_finished_ok] after emit, thread={threading.current_thread().name}")


class Window(QObject):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.panel.design_changed.connect(self.on_design_changed)

    def on_design_changed(self, design):
        print(f"[Window.on_design_changed] CALLED! thread={threading.current_thread().name}, design={design}")


panel = Panel()
window = Window(panel)

worker = Worker()
worker.finished_ok.connect(panel.on_finished_ok)

print(f"[Main] thread={threading.current_thread().name}")
print("[Main] starting worker...")
worker.start()

# Process events for a bit
for _ in range(20):
    app.processEvents()
    time.sleep(0.1)

worker.wait()
print("[Main] done")
