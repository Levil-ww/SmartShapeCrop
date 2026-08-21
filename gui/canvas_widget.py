"""
gui/canvas_widget.py
预览画布组件：
- 实际渲染：始终使用 design.canvas_w_px × canvas_h_px 的全分辨率（用于保存）
- 界面显示：按 QWidget 窗口大小等比缩放居中（仅预览，不影响保存）
- 避免把“预览缩放后的 QPixmap”作为保存源（经验：799713）
"""
from __future__ import annotations
import io
from PIL import Image
from PyQt5.QtCore import Qt, QSize, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage, QPainter, QColor, QPalette
from PyQt5.QtWidgets import QWidget, QSizePolicy


class PreviewCanvas(QWidget):
    """显示当前 CropDesign 的预览画布（只读，显示用）"""

    rendered = pyqtSignal(object)   # 每次重绘全尺寸图像后发射，方便外部保存

    def __init__(self, parent=None):
        super().__init__(parent)
        self._design = None
        self._full_image: Image.Image | None = None  # 预览渲染图（BILINEAR；导出由 main._on_save 重渲 LANCZOS）
        self._preview_pixmap: QPixmap | None = None  # 缩放后的预览图
        self.setMinimumSize(QSize(400, 500))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setBackgroundRole(QPalette.Dark)
        self.setAutoFillBackground(True)

    # ---- 外部接口 ----
    def set_design(self, design) -> None:
        """设置设计并触发重渲染"""
        self._design = design
        self._render()
        self.update()

    def full_image(self) -> Image.Image | None:
        """返回全尺寸渲染图（保存时使用这个，不要用预览图）"""
        return self._full_image

    # ---- 内部渲染 ----
    def _render(self) -> None:
        if self._design is None:
            self._full_image = None
            self._preview_pixmap = None
            return
        from core.image_ops import render_design
        # 预览阶段用 BILINEAR 重采样，比 LANCZOS 快 3-5×；
        # 最终保存会在 main._on_save 用 quality='export' 重渲一次 LANCZOS
        self._full_image = render_design(self._design, quality='preview')
        self._update_preview_pixmap()
        self.rendered.emit(self._full_image)

    def _update_preview_pixmap(self) -> None:
        if self._full_image is None:
            self._preview_pixmap = None
            return
        # PIL -> QImage -> QPixmap
        pil = self._full_image.convert('RGB')
        data = pil.tobytes('raw', 'RGB')
        qimg = QImage(data, pil.width, pil.height, pil.width * 3, QImage.Format_RGB888).copy()
        self._source_pixmap = QPixmap.fromImage(qimg)
        self._rescale_preview()

    def _rescale_preview(self):
        if not hasattr(self, '_source_pixmap') or self._source_pixmap is None:
            self._preview_pixmap = None
            return
        # 预览按 widget 尺寸缩放，保持比例（经验：799713）
        w, h = max(1, self.width()), max(1, self.height())
        self._preview_pixmap = self._source_pixmap.scaled(
            w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    # ---- Qt 事件 ----
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale_preview()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 40, 40))  # 画布周围深灰
        if self._preview_pixmap is None or self._preview_pixmap.isNull():
            p.setPen(QColor(180, 180, 180))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "请在右侧设置参数后生成预览")
            return
        # 居中绘制
        pm_w, pm_h = self._preview_pixmap.width(), self._preview_pixmap.height()
        x = (self.width() - pm_w) // 2
        y = (self.height() - pm_h) // 2
        p.drawPixmap(x, y, self._preview_pixmap)
        # 画一个浅色边框
        p.setPen(QColor(120, 120, 120))
        p.drawRect(x, y, pm_w - 1, pm_h - 1)
