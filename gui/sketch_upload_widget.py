"""Reusable sketch upload and thumbnail preview control."""
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel

from .property_panel_widgets import _SketchDropLabel


class SketchUploadWidget(QWidget):
    """Owns the sketch drop preview and its upload/clear actions."""

    uploadRequested = pyqtSignal()
    clearRequested = pyqtSignal()
    viewRequested = pyqtSignal()
    sketchDropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.preview = _SketchDropLabel("（未上传）\n或拖入图片", self)
        self.preview.fileDropped.connect(self.sketchDropped)
        self.preview.clicked.connect(self.viewRequested)
        row.addWidget(self.preview, 0)
        buttons = QVBoxLayout()
        upload = QPushButton("上传草图…", self)
        upload.clicked.connect(self.uploadRequested)
        clear = QPushButton("清除草图", self)
        clear.clicked.connect(self.clearRequested)
        buttons.addWidget(upload)
        buttons.addWidget(clear)
        row.addLayout(buttons, 0)
        desc = QLabel(
            "💡 草图格式示例（红色线标注上下左右边距即可）\n"
            "自动识别失败时可在【水池设计器】下方【内挖边距】手动调整", self)
        desc.setStyleSheet("color:#666;")
        desc.setWordWrap(True)
        row.addWidget(desc, 1)

    def set_path(self, path: str):
        self.preview.setProperty("sketch_path", path or "")

    def path(self) -> str:
        return self.preview.property("sketch_path") or ""

