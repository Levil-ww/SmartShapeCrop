"""Reusable target-file input control.

The widget owns the target text and its controls.  Panels may keep compatibility
aliases to the child widgets while migrating their orchestration code.
"""
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolButton,
    QMenu,
)


class TargetFileWidget(QWidget):
    """Target filename editor with pick, clear and history affordances."""

    textChanged = pyqtSignal(str)
    pickRequested = pyqtSignal()
    clearRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("目标文件:"), 0)
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText(
            "例：吸水皮革-定制-裁剪有图-克罗印花;60.5x133CM  （花型名+尺寸必须写）")
        self.edit.textChanged.connect(self.textChanged)
        row.addWidget(self.edit, 1)
        self.pick_button = QPushButton("选文件", self)
        self.pick_button.setFixedWidth(64)
        self.pick_button.clicked.connect(self.pickRequested)
        row.addWidget(self.pick_button, 0)
        self.clear_button = QPushButton("清空", self)
        self.clear_button.setFixedWidth(48)
        self.clear_button.clicked.connect(self.clearRequested)
        row.addWidget(self.clear_button, 0)
        self.history_button = QToolButton(self)
        self.history_button.setText("▾")
        self.history_button.setPopupMode(QToolButton.InstantPopup)
        self.history_button.setToolTip("目标文件名历史记录（保留3天）")
        self.history_menu = QMenu(self.history_button)
        self.history_button.setMenu(self.history_menu)
        row.addWidget(self.history_button, 0)

    def text(self) -> str:
        return self.edit.text()

    def setText(self, value: str):
        self.edit.setText(value)

    def clear(self):
        self.edit.clear()
