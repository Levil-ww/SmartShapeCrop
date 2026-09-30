"""Recognition status and preview/export controls owned by a reusable widget."""
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QWidget, QGroupBox, QVBoxLayout, QHBoxLayout, QPushButton, QLabel


class DesignActionControl(QWidget):
    """Creates separate sections for the panel's existing layout positions."""

    recognizeRequested = pyqtSignal()
    generateRequested = pyqtSignal()
    saveRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.group = QGroupBox("L 形挖角识别", self)
        self.group.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 2px solid #E6A23C; "
            "border-radius: 6px; margin-top: 14px; padding-top: 12px; "
            "background: #FFFFFF; }"
            "QGroupBox[lowConfidence=\"true\"] { border-color: #D97706; "
            "background: #FFFBEB; }"
            "QGroupBox::title { subcontrol-origin: border; subcontrol-position: top left; "
            "left: 12px; top: -2px; padding: 0 6px; color: #B26A00; }")
        fr = QVBoxLayout(self.group)
        fr.setSpacing(6)

        self.recognize_button = QPushButton("✂️ 识别L形挖角")
        self.recognize_button.setToolTip(
            "把当前草图按 L 形挖角识别（A/B/C/D/E/F 六处尺寸标注）。\n"
            "识别成功会弹出确认框，可修改挖角位置/宽/高后一键生成。\n"
            "上传草图后也会自动尝试 L 形识别；此按钮用于手动重新识别。")
        self.recognize_button.setStyleSheet(
            "QPushButton { background:#FFF3E0; color:#B26A00; border:1px solid #E6A23C;"
            " border-radius:4px; padding:6px 10px; font-weight:bold; }"
            "QPushButton:hover { background:#FFE8C2; }"
            "QPushButton:disabled { color:#ccc; background:#f5f5f5; border-color:#ddd; }")
        self.recognize_button.clicked.connect(self.recognizeRequested)
        fr.addWidget(self.recognize_button)

        self.status_label = QLabel(
            "（填写目标文件名并上传草图后，点上方按钮识别 L 形挖角）")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color:#555; padding: 4px 6px;")
        fr.addWidget(self.status_label)
        row_action = QHBoxLayout()
        row_action.setSpacing(8)
        self.generate_button = QPushButton("🔍 生成预览", self)
        self.generate_button.setToolTip(
            "匹配模板 → 解析草图 → 生成预览（在水池设计器画布上实时渲染）")
        self.generate_button.setStyleSheet(
            "QPushButton { padding: 10px 12px; font-weight: bold; font-size: 14px;"
            " background: #4A90E2; color: white; border: none; border-radius: 5px; }"
            "QPushButton:hover { background: #357ABD; }"
            "QPushButton:disabled { background: #A0BFE0; color: #eee; }")
        self.generate_button.clicked.connect(self.generateRequested)
        self.save_button = QPushButton("💾 导出 JPG", self)
        self.save_button.setToolTip(
            "把当前画布设计渲染为全分辨率 JPG 并保存到本地文件。\n"
            "导出文件名优先取上方“目标文件名”，未填写则按画布尺寸自动生成。")
        self.save_button.setStyleSheet(
            "QPushButton { padding: 10px 12px; font-weight: bold; font-size: 14px;"
            " background: #27AE60; color: white; border: none; border-radius: 5px; }"
            "QPushButton:hover { background: #1F8B4C; }"
            "QPushButton:disabled { background: #A8D8B9; color: #eee; }")
        self.save_button.clicked.connect(self.saveRequested)
        row_action.addWidget(self.generate_button, 1)
        row_action.addWidget(self.save_button, 1)
        self.action_layout = row_action

    def set_generate_enabled(self, enabled: bool, text: str | None = None):
        self.generate_button.setEnabled(enabled)
        if text is not None:
            self.generate_button.setText(text)

    def set_status(self, msg: str, is_error: bool = False):
        color = "#B00020" if is_error else "#388E3C"
        self.status_label.setText(msg)
        self.status_label.setStyleSheet(
            f"color:{color}; padding:4px 6px; background: {'#FFEBEE' if is_error else '#E8F5E9'};"
            " border-radius: 4px;")
