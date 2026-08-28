"""gui/property_panel 子模块 —— 对话框层（草图查看器 / 图层编辑）（由 property_panel.py 拆分而来，facade 模式）。

原文件 gui/property_panel.py 保留为 facade（PropertyPanel 主类 + 编排），
本模块只包含 对话框层（草图查看器 / 图层编辑） 相关的实现，逻辑与原文件完全一致。
"""
from __future__ import annotations
import logging
import os
from datetime import date, datetime, timedelta
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QSize, QTimer
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


class _SketchViewerDialog(QDialog):
    """查看上传草图的大图对话框：支持适应窗口、原尺寸、放大缩小、滚动查看。"""

    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self._image_path = image_path
        self._scale_factor = 1.0
        self._pm_original = QPixmap(image_path)

        # —— 窗口基础设置 ——
        self.setWindowTitle(f"查看草图 - {os.path.basename(image_path)}")
        self.resize(900, 700)
        self.setMinimumSize(400, 300)

        # —— 顶部工具栏 ——
        toolbar = QHBoxLayout()
        self._btn_fit = QPushButton("🔍 适应窗口")
        self._btn_actual = QPushButton("📐 原尺寸 (100%)")
        self._btn_zoom_in = QPushButton("➕ 放大")
        self._btn_zoom_out = QPushButton("➖ 缩小")
        self._btn_close = QPushButton("✕ 关闭")
        self._btn_close.setStyleSheet("background:#f44336;color:white;padding:6px 12px;border-radius:4px;")
        toolbar.addWidget(self._btn_fit)
        toolbar.addWidget(self._btn_actual)
        toolbar.addWidget(self._btn_zoom_in)
        toolbar.addWidget(self._btn_zoom_out)
        toolbar.addStretch(1)
        toolbar.addWidget(self._btn_close)

        # —— 图片信息栏 ——
        self._info_label = QLabel()
        self._info_label.setStyleSheet("color:#555;padding:4px 8px;background:#f5f5f5;border-radius:4px;")

        # —— 滚动区 + 图片显示 ——
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)  # 图片自己控制缩放
        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignCenter)
        self._img_label.setStyleSheet("background:#2b2b2b;")
        self._scroll.setWidget(self._img_label)

        # —— 布局 ——
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)
        root.addLayout(toolbar)
        root.addWidget(self._info_label)
        root.addWidget(self._scroll, 1)

        # —— 连接按钮 ——
        self._btn_fit.clicked.connect(self._zoom_fit)
        self._btn_actual.clicked.connect(self._zoom_actual)
        self._btn_zoom_in.clicked.connect(lambda: self._zoom_step(1.25))
        self._btn_zoom_out.clicked.connect(lambda: self._zoom_step(1 / 1.25))
        self._btn_close.clicked.connect(self.accept)

        # —— 初始化显示 ——
        if self._pm_original.isNull():
            self._info_label.setText(f"❌ 无法加载图片：{image_path}")
            self._img_label.setText("图片加载失败")
        else:
            self._update_info()
            # 首次显示：延迟到事件循环后执行适应窗口，因为布局尺寸还没确定
            QTimer(self).singleShot(0, self._zoom_fit)

    # ---- 辅助：更新图片信息 ----
    def _update_info(self):
        if self._pm_original.isNull():
            return
        w, h = self._pm_original.width(), self._pm_original.height()
        try:
            fsize = os.path.getsize(self._image_path)
            if fsize >= 1024 * 1024:
                fsize_str = f"{fsize / 1024 / 1024:.2f} MB"
            else:
                fsize_str = f"{fsize / 1024:.2f} KB"
        except Exception:
            fsize_str = "未知大小"
        self._info_label.setText(
            f"📄 {os.path.basename(self._image_path)}　|　"
            f"📏 原始尺寸：{w} × {h} px　|　"
            f"🗂 文件大小：{fsize_str}　|　"
            f"🔎 当前缩放：{self._scale_factor * 100:.0f}%"
        )

    # ---- 辅助：应用当前缩放系数到图片显示 ----
    def _apply_scale(self):
        if self._pm_original.isNull():
            return
        new_w = int(self._pm_original.width() * self._scale_factor)
        new_h = int(self._pm_original.height() * self._scale_factor)
        if new_w <= 0 or new_h <= 0:
            return
        scaled = self._pm_original.scaled(
            new_w, new_h,
            Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self._img_label.setPixmap(scaled)
        self._img_label.resize(scaled.size())
        self._update_info()

    # ---- 缩放：适应窗口 ----
    def _zoom_fit(self):
        if self._pm_original.isNull():
            return
        # 用滚动区 viewport 的可用尺寸计算
        vw = self._scroll.viewport().width() - 4
        vh = self._scroll.viewport().height() - 4
        if vw <= 0 or vh <= 0:
            return
        sw = vw / self._pm_original.width()
        sh = vh / self._pm_original.height()
        self._scale_factor = min(sw, sh, 3.0)  # 适应窗口，但不超过 300% 避免过模糊
        self._apply_scale()

    # ---- 缩放：原尺寸 100% ----
    def _zoom_actual(self):
        self._scale_factor = 1.0
        self._apply_scale()

    # ---- 缩放：按倍率步进 ----
    def _zoom_step(self, factor: float):
        if self._pm_original.isNull():
            return
        new_scale = self._scale_factor * factor
        # 限制在 10% ~ 800% 范围内
        new_scale = max(0.1, min(8.0, new_scale))
        # 没有实质变化就不重绘
        if abs(new_scale - self._scale_factor) < 0.001:
            return
        self._scale_factor = new_scale
        self._apply_scale()

    # ---- 滚轮缩放（辅助体验）----
    def wheelEvent(self, event):
        # 只有在光标位于图片区域附近时才触发；这里简化为整窗口支持 Ctrl+滚轮
        if event.modifiers() & Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self._zoom_step(1.15)
            else:
                self._zoom_step(1 / 1.15)
            event.accept()
        else:
            super().wheelEvent(event)

    # ---- 窗口大小变化时，如果之前是"适应窗口"模式，重新适应 ----
    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 如果当前缩放比例非常接近当前视口下的适应值，就重新适应
        if not self._pm_original.isNull():
            vw = max(1, self._scroll.viewport().width() - 4)
            vh = max(1, self._scroll.viewport().height() - 4)
            sw = vw / self._pm_original.width()
            sh = vh / self._pm_original.height()
            fit_factor = min(sw, sh, 3.0)
            # 若当前系数与 fit_factor 偏差在 1% 内，认为处于"适应窗口"状态，随窗口大小重新适应
            if abs(self._scale_factor - fit_factor) / max(0.0001, fit_factor) < 0.01:
                self._scale_factor = fit_factor
                self._apply_scale()



class _LayersDialog(QDialog):
    def __init__(self, layers: list[BorderLayer], parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑边框层")
        self.resize(560, 360)
        self.result_layers = [BorderLayer(l.offset_cm, 0, l.fill_type, l.color, l.image_path, l.tile_mode) for l in layers]

        v = QVBoxLayout(self)
        self.tbl = QTableWidget(len(self.result_layers), 5)
        self.tbl.setHorizontalHeaderLabels(["厚度(cm)", "填充", "颜色", "素材图", "平铺"])
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        for i, l in enumerate(self.result_layers):
            self._set_row(i, l)
        v.addWidget(self.tbl)

        row_btn = QHBoxLayout()
        b_add = QPushButton("+ 插入"); b_del = QPushButton("- 删除"); b_up = QPushButton("上移"); b_dn = QPushButton("下移")
        row_btn.addWidget(b_add); row_btn.addWidget(b_del); row_btn.addWidget(b_up); row_btn.addWidget(b_dn); row_btn.addStretch(1)
        v.addLayout(row_btn)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self.tbl.cellDoubleClicked.connect(self._on_cell)
        b_add.clicked.connect(self._add); b_del.clicked.connect(self._del)
        b_up.clicked.connect(lambda: self._move(-1)); b_dn.clicked.connect(lambda: self._move(1))

    def _set_row(self, i, l: BorderLayer):
        self.tbl.setItem(i, 0, QTableWidgetItem(f"{l.offset_cm:.2f}"))
        self.tbl.setItem(i, 1, QTableWidgetItem("纯色" if l.fill_type == 'solid' else "图片"))
        r, g, b = l.color
        self.tbl.setItem(i, 2, QTableWidgetItem(f"#{r:02X}{g:02X}{b:02X}"))
        self.tbl.setItem(i, 3, QTableWidgetItem(l.image_path or ""))
        self.tbl.setItem(i, 4, QTableWidgetItem("是" if l.tile_mode else "否"))

    def _read_row(self, i):
        try:
            self.result_layers[i].offset_cm = float(self.tbl.item(i, 0).text())
            self.result_layers[i].fill_type = 'solid' if self.tbl.item(i, 1).text() == '纯色' else 'image'
            self.result_layers[i].image_path = self.tbl.item(i, 3).text().strip() or None
            self.result_layers[i].tile_mode = self.tbl.item(i, 4).text() == '是'
        except Exception as e:
            logger.warning(f"边框层表格第 {i} 行读取失败: {e}")

    def _on_cell(self, row, col):
        l = self.result_layers[row]
        if col == 0:
            # 厚度
            from PyQt5.QtWidgets import QInputDialog
            v, ok = QInputDialog.getDouble(self, "厚度(cm)", "cm", l.offset_cm, 0.01, 20, 2)
            if ok:
                l.offset_cm = v; self._set_row(row, l)
        elif col == 1:
            l.fill_type = 'image' if l.fill_type == 'solid' else 'solid'
            self._set_row(row, l)
        elif col == 2:
            c = QColorDialog.getColor(_tuple_to_color(l.color), self, "颜色")
            if c.isValid():
                l.color = _color_to_tuple(c); self._set_row(row, l)
        elif col == 3:
            p, _ = QFileDialog.getOpenFileName(self, "素材图", "", "JPG/PSD (*.jpg *.jpeg *.psd)")
            if p:
                l.image_path = p; self._set_row(row, l)
        elif col == 4:
            l.tile_mode = not l.tile_mode; self._set_row(row, l)

    def _add(self):
        self.result_layers.append(BorderLayer(offset_cm=0.2))
        self.tbl.insertRow(self.tbl.rowCount())
        self._set_row(self.tbl.rowCount() - 1, self.result_layers[-1])

    def _del(self):
        r = self.tbl.currentRow()
        if 0 <= r < len(self.result_layers):
            self.result_layers.pop(r); self.tbl.removeRow(r)

    def _move(self, d):
        r = self.tbl.currentRow()
        t = r + d
        if 0 <= r < len(self.result_layers) and 0 <= t < len(self.result_layers):
            self.result_layers[r], self.result_layers[t] = self.result_layers[t], self.result_layers[r]
            self._set_row(r, self.result_layers[r]); self._set_row(t, self.result_layers[t])
            self.tbl.selectRow(t)

    def accept(self):
        # 提交所有行当前的文本输入
        for i in range(len(self.result_layers)):
            self._read_row(i)
        super().accept()

