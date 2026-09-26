"""综合形状面板：在 LShapePanel 基础上增加单中心洞参数。"""
from __future__ import annotations

import logging
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QDoubleSpinBox, QFormLayout, QGroupBox, QPushButton, QCheckBox

from workers.property_panel_workers import _CompositeParseWorker
from .lshape_panel import LShapePanel

logger = logging.getLogger(__name__)


class CompositePanel(LShapePanel):
    """独立综合形状面板，复用 L 形面板的目标文件、草图和挖角控件。"""

    composite_params_changed = pyqtSignal(dict)
    composite_recognize_finished = pyqtSignal(object)

    def __init__(self, parent=None):
        self._composite_parse_result = None
        self._composite_parse_worker = None
        super().__init__(parent)
        self._build_composite_controls()

    def _build_composite_controls(self):
        self._gb_composite = QGroupBox("中心矩形洞（综合形状）", self)
        form = QFormLayout(self._gb_composite)
        self._hole_w = self._make_spin(80.0)
        self._hole_h = self._make_spin(60.0)
        self._hole_mt = self._make_spin(10.0)
        self._hole_mb = self._make_spin(18.0)
        self._hole_ml = self._make_spin(45.0)
        self._hole_mr = self._make_spin(60.0)
        self._hole_material = QCheckBox("中心洞填充素材")
        for label, widget in (
            ("洞宽(cm)", self._hole_w), ("洞高(cm)", self._hole_h),
            ("上边距(cm)", self._hole_mt), ("下边距(cm)", self._hole_mb),
            ("左边距(cm)", self._hole_ml), ("右边距(cm)", self._hole_mr),
        ):
            form.addRow(label, widget)
        form.addRow(self._hole_material)
        self._btn_composite_recognize = QPushButton("识别综合形状草图")
        self._btn_composite_recognize.clicked.connect(self._recognize_composite_sketch)
        form.addRow(self._btn_composite_recognize)
        self._inner_layout.addWidget(self._gb_composite)

    @staticmethod
    def _make_spin(value):
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 10000.0)
        spin.setDecimals(2)
        spin.setValue(value)
        return spin

    def get_composite_params(self) -> dict:
        """返回可直接映射到 CropDesign 的复合参数。"""
        params = super().get_lshape_params()
        params.update({
            'mode': 'rect_lshape_hole',
            'hole_w_cm': self._hole_w.value(),
            'hole_h_cm': self._hole_h.value(),
            'hole_margin_top_cm': self._hole_mt.value(),
            'hole_margin_bottom_cm': self._hole_mb.value(),
            'hole_margin_left_cm': self._hole_ml.value(),
            'hole_margin_right_cm': self._hole_mr.value(),
            'hole_fill_mode': 'image' if self._hole_material.isChecked() else 'blank',
        })
        return params

    def _recognize_composite_sketch(self):
        """调用统一解析入口并回填控件；失败时保留用户当前值。"""
        sketch_path = self._sk_preview.property('sketch_path')
        if not sketch_path:
            return
        try:
            if self._composite_parse_worker is not None and self._composite_parse_worker.isRunning():
                self._composite_parse_worker.requestInterruption()
                self._composite_parse_worker.wait(2000)
            worker = _CompositeParseWorker(
                sketch_path, self.get_outer_w_cm(), self.get_outer_h_cm(), self)
            worker.finished_ok.connect(self._on_composite_parsed)
            worker.finished_err.connect(self._on_composite_parse_error)
            worker.finished.connect(lambda: self._btn_composite_recognize.setEnabled(True))
            self._composite_parse_worker = worker
            self._btn_composite_recognize.setEnabled(False)
            worker.start()
        except Exception:
            logger.exception("[CompositePanel] 综合形状草图识别失败")

    def _on_composite_parsed(self, result):
        self._composite_parse_result = result
        if result.success:
            for widget, value in ((self._hole_w, result.hole_w_cm),
                                  (self._hole_h, result.hole_h_cm),
                                  (self._hole_mt, result.margin_top_cm),
                                  (self._hole_mb, result.margin_bottom_cm),
                                  (self._hole_ml, result.margin_left_cm),
                                  (self._hole_mr, result.margin_right_cm)):
                widget.setValue(value)
            self.composite_params_changed.emit(self.get_composite_params())
        self.composite_recognize_finished.emit(result)

    def _on_composite_parse_error(self, message):
        logger.warning('[CompositePanel] 识别异常：%s', message)

    def shutdown(self):
        worker = self._composite_parse_worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            worker.wait(2000)
        super().shutdown()
