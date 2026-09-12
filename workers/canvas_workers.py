"""
workers/canvas_workers.py
画布后台渲染 Worker：PreviewRenderWorker + ExportSaveWorker。

从 gui/canvas_widget.py 提取，使 Worker 层与 GUI 层分离。
功能逻辑零修改。
"""
from __future__ import annotations
import logging
import time
from PyQt5.QtCore import pyqtSignal, QThread

logger = logging.getLogger(__name__)


class PreviewRenderWorker(QThread):
    """
    后台渲染 Worker：在独立线程中执行全分辨率 render_design。
    避免 UI 主线程阻塞，渲染完成后通过 finished_ok 信号返回结果。
    """
    finished_ok = pyqtSignal(object, float)   # (PIL.Image, elapsed_seconds)
    finished_err = pyqtSignal(str)

    def __init__(self, design, parent=None):
        super().__init__(parent)
        self._design = design

    def run(self):
        try:
            from core.image_ops import render_design
            t0 = time.perf_counter()
            img = render_design(self._design, quality='preview')
            elapsed = time.perf_counter() - t0
            if self.isInterruptionRequested():
                return
            self.finished_ok.emit(img, elapsed)
        except Exception as e:
            logger.exception("[PreviewRenderWorker] 后台渲染异常")
            if self.isInterruptionRequested():
                return
            self.finished_err.emit(str(e))


class ExportSaveWorker(QThread):
    """
    [Fix 2026-09-02 B] 导出 JPG 专用后台 Worker：
      1) 在独立线程中全分辨率 LANCZOS 渲染 render_design(quality='export')
      2) 同线程内写入 JPG 文件（IO 操作与 GUI 解耦）
      3) 通过信号返回结果，UI 主线程全程不阻塞、无"卡住"。
    原实现 main.py _on_save 在主线程同步执行 render_design + save_jpg：
      29.3 MP 画布 + 300DPI 素材 → 5-15 秒 UI 完全冻结（Windows标为"未响应"）。
    本 Worker 照搬 PreviewRenderWorker 的已验证模式（clone 快照防 F4 竞争）。
    """
    save_ok = pyqtSignal(str, float, int, int)   # (path, elapsed_sec, img_w, img_h)
    save_err = pyqtSignal(str)
    save_cancelled = pyqtSignal()

    def __init__(self, design_snapshot, out_path: str, dpi: int,
                 jpeg_quality: int = 95, parent=None):
        super().__init__(parent)
        self._design = design_snapshot
        self._out_path = out_path
        self._dpi = dpi
        self._jpeg_q = jpeg_quality

    def run(self):
        try:
            from core.image_ops import render_design, save_jpg
            t0 = time.perf_counter()
            # 步骤1：全分辨率 LANCZOS 渲染
            if self.isInterruptionRequested():
                self.save_cancelled.emit()
                return
            img = render_design(self._design, quality='export')
            if self.isInterruptionRequested():
                self.save_cancelled.emit()
                return
            # 步骤2：写入 JPG（同线程内 IO，避免跨线程搬运 29MP 图像）
            save_jpg(img, self._out_path, quality=self._jpeg_q, dpi=self._dpi)
            elapsed = time.perf_counter() - t0
            if self.isInterruptionRequested():
                # 极端情况：文件已写入但用户在 save 途中取消 → 保留文件（不删除避免误操作）
                self.save_cancelled.emit()
                return
            self.save_ok.emit(self._out_path, elapsed, img.width, img.height)
        except Exception as e:
            logger.exception("[ExportSaveWorker] 导出渲染或保存失败")
            if self.isInterruptionRequested():
                self.save_cancelled.emit()
                return
            self.save_err.emit(str(e))
