"""
workers/cropper_workers.py
裁剪后台 Worker：CropWorker + AutoMatchWorker。

从 gui/cropper_panel.py 提取，使 Worker 层与 GUI 层分离。
功能逻辑零修改。
"""
from __future__ import annotations
import time
import traceback
from PyQt5.QtCore import QThread, pyqtSignal

from core.image_cropper import crop_image, CropConfig
from services.parser.template_matcher import TemplateMatcher


class CropWorker(QThread):
    """
    [PERF] 异步裁剪 Worker — 将大图裁剪操作移至后台线程。

    对于印刷级大图（1-2亿像素），裁剪操作（边框检测、圆角重绘）
    在 UI 主线程执行会导致界面卡死。通过 QThread 异步执行，
    同时发送进度信号更新 UI。
    """

    finished_ok = pyqtSignal(object)
    finished_err = pyqtSignal(str)
    progress = pyqtSignal(int, str)

    def __init__(self, config: CropConfig, parent=None):
        super().__init__(parent)
        self._config = config

    def run(self):
        try:
            result = crop_image(self._config)
            self.finished_ok.emit(result)
        except Exception as e:
            self.finished_err.emit(str(e))


class AutoMatchWorker(QThread):
    """
    异步模板匹配 Worker — 将 scan_library + find_best_match 移至后台线程。

    模板库扫描（特别是首次 20万+ 文件全量扫描）和匹配评分会阻塞 UI 主线程。
    通过 QThread 异步执行，配合 QProgressDialog 进度反馈。
    """

    finished_ok = pyqtSignal(object, list, float, dict)  # (best_entry, candidates, elapsed, stats)
    finished_err = pyqtSignal(str)
    progress = pyqtSignal(int, str)
    log_msg = pyqtSignal(str)

    def __init__(self, template_dir: str, target_name: str, matcher: TemplateMatcher, parent=None):
        super().__init__(parent)
        self._template_dir = template_dir
        self._target_name = target_name
        self._matcher = matcher

    def run(self):
        try:
            # 1) 确保 matcher 目录正确（仅当不同时设置，避免清空缓存）
            if self._matcher.get_template_dir() != self._template_dir:
                self._matcher.set_template_dir(self._template_dir)

            self.progress.emit(10, "正在扫描模板库...")
            self.log_msg.emit("正在扫描模板库...")

            t0 = time.time()
            self._matcher.scan_library(force=False, check_cancel=self.isInterruptionRequested)

            if self.isInterruptionRequested():
                self.log_msg.emit("模板扫描已取消")
                return

            self.progress.emit(60, "正在匹配源图...")
            self.log_msg.emit("正在匹配源图...")

            best, candidates = self._matcher.find_best_match(self._target_name)
            match_dt = time.time() - t0

            self.progress.emit(100, "匹配完成")
            self.log_msg.emit(f"匹配完成，耗时 {match_dt:.2f}s")

            stats = {}
            try:
                stats = self._matcher.get_library_stats()
            except Exception:
                pass

            self.finished_ok.emit(best, candidates, match_dt, stats)
        except Exception as e:
            traceback.print_exc()
            self.finished_err.emit(str(e))
