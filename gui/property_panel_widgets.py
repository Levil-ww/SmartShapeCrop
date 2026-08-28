"""gui/property_panel 子模块 —— 小控件层（颜色按钮 / 草图拖放标签 / 颜色转换函数）（由 property_panel.py 拆分而来，facade 模式）。

原文件 gui/property_panel.py 保留为 facade（PropertyPanel 主类 + 编排），
本模块只包含 小控件层（颜色按钮 / 草图拖放标签 / 颜色转换函数） 相关的实现，逻辑与原文件完全一致。
"""
from __future__ import annotations
import logging
import os
from datetime import date, datetime, timedelta
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QSize
from PyQt5.QtGui import QColor, QPixmap
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel, QDoubleSpinBox,
    QSpinBox, QComboBox, QPushButton, QCheckBox, QFileDialog, QLineEdit,
    QColorDialog, QFrame, QScrollArea, QMessageBox, QProgressDialog,
    QToolButton, QMenu, QAction, QDialog, QApplication,
)
from PyQt5.QtCore import QMimeData  # noqa: E402  (拖拽支持)
from PIL import Image

from core.geometry import CropDesign, BorderLayer, BorderText
from core.parser.name_parser import parse_filename
from core.parser.template_matcher import TemplateMatcher
from core.app_settings import get_app_settings
from core.pool_designer import validate_sketch_file
from core.pool_designer.sketch_parser import _SKETCH_ACCEPT_EXT, get_tesseract_status

logger = logging.getLogger(__name__)


def _color_to_tuple(qc: QColor) -> tuple[int, int, int]:
    return (qc.red(), qc.green(), qc.blue())



def _tuple_to_color(t: tuple[int, int, int]) -> QColor:
    return QColor(*t)



class ColorButton(QPushButton):
    """点击选择颜色的小按钮，显示当前颜色色块"""

    changed = pyqtSignal(tuple)

    def __init__(self, init_color: tuple[int, int, int] = (0, 0, 0)):
        super().__init__()
        self.setFixedSize(40, 24)
        self._c = init_color
        self._update_style()
        self.clicked.connect(self._pick)

    def _update_style(self):
        r, g, b = self._c
        self.setStyleSheet(
            f"QPushButton {{ background-color: rgb({r},{g},{b});"
            f" border: 1px solid #888; border-radius: 3px; }}")

    def _pick(self):
        c = QColorDialog.getColor(_tuple_to_color(self._c), self, "选择颜色")
        if c.isValid():
            self._c = _color_to_tuple(c)
            self._update_style()
            self.changed.emit(self._c)

    def color(self) -> tuple[int, int, int]:
        return self._c

    def set_color(self, c: tuple[int, int, int]) -> None:
        self._c = c
        self._update_style()



class _SketchDropLabel(QLabel):
    """带拖拽支持的草图预览标签；拖入图片文件或点击按钮均可上传；有图时点击可查看大图。"""

    fileDropped = pyqtSignal(str)   # 拖入文件成功时发出路径
    clicked = pyqtSignal()          # 点击时发出（用于打开大图预览）

    _ACCEPT_EXT = _SKETCH_ACCEPT_EXT  # 复用 sketch_parser 的统一白名单，避免两处副本漂移

    def __init__(self, text="（未上传）\n或拖入图片", parent=None):
        super().__init__(text, parent)
        self.setAcceptDrops(True)
        self.setFixedSize(96, 96)  # 略微加高，便于拖入
        self.setStyleSheet(
            "QLabel { border: 2px dashed #4A90E2; color: #4A90E2; background:#EFF6FF;"
            " qproperty-alignment: AlignCenter; border-radius: 6px; font-size: 11px; }")
        self.setScaledContents(True)
        self._has_image = False

    # ---- 公开：设置/清除图片状态 ----
    def set_has_image(self, has: bool):
        """设置是否已有图片，影响点击光标、悬浮提示与样式。"""
        self._has_image = has
        if has:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("点击查看草图大图")
        else:
            self.setCursor(Qt.ArrowCursor)
            self.setToolTip("")

    # ---- 点击事件：有图时触发 clicked 信号 ----
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._has_image:
            self.clicked.emit()
        else:
            super().mousePressEvent(event)

    # ---- 拖拽事件 ----
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile():
                ext = os.path.splitext(urls[0].toLocalFile())[1].lower()
                if ext in self._ACCEPT_EXT:
                    event.acceptProposedAction()
                    # 拖入时高亮边框提示
                    self.setStyleSheet(
                        "QLabel { border: 2px dashed #2E7DD1; color: #2E7DD1; background:#DBEAFE;"
                        " qproperty-alignment: AlignCenter; border-radius: 6px; font-size: 11px; }")
                    return
        event.ignore()

    def dragLeaveEvent(self, event):
        # 恢复默认样式（根据是否有图片）
        if self._has_image:
            self.setStyleSheet(
                "QLabel { border: 1px solid #888; background:#fff; border-radius: 6px; }")
        else:
            self.setStyleSheet(
                "QLabel { border: 2px dashed #4A90E2; color: #4A90E2; background:#EFF6FF;"
                " qproperty-alignment: AlignCenter; border-radius: 6px; font-size: 11px; }")

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not urls or not urls[0].isLocalFile():
            event.ignore()
            return
        path = urls[0].toLocalFile()
        ext = os.path.splitext(path)[1].lower()
        if ext not in self._ACCEPT_EXT:
            event.ignore()
            return
        event.acceptProposedAction()
        # 恢复样式（无图状态）并通知；具体样式/has_image 由主逻辑 setPixmap 后设置
        self.setStyleSheet(
            "QLabel { border: 2px dashed #4A90E2; color: #4A90E2; background:#EFF6FF;"
            " qproperty-alignment: AlignCenter; border-radius: 6px; font-size: 11px; }")
        self.fileDropped.emit(path)

