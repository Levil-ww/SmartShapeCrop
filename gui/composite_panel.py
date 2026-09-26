"""综合形状面板：在 LShapePanel 基础上增加单中心洞参数。"""
from __future__ import annotations

import logging
import os
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QDoubleSpinBox, QFormLayout, QGroupBox, QLabel, QPushButton, QCheckBox, QFileDialog

from workers.property_panel_workers import _CompositeParseWorker
from core.config import CUT_LOSS_CM
from core.geometry import CropDesign
from .lshape_panel import LShapePanel

logger = logging.getLogger(__name__)

# 综合形状识别的外框基准下限：L 形面板外框 SpinBox 最小画布值 5.0 → 设计真值 4.0，
# 等于该值说明用户从未提供过尺寸基准（默认态），此时识别结果全是按 4cm 等比缩放的噪声。
_COMPOSITE_BASIS_MIN_CM = 4.0


class CompositePanel(LShapePanel):
    """独立综合形状面板，复用 L 形面板的目标文件、草图和挖角控件。"""

    composite_params_changed = pyqtSignal(dict)
    composite_recognize_finished = pyqtSignal(object)

    def __init__(self, parent=None):
        self._composite_parse_result = None
        self._composite_parse_worker = None
        # D2：识别基准来源标记（'none' = 用户未提供；'target' = 目标文件名解析；'recognize' = 上次识别回填）
        self._composite_basis_source = 'none'
        self._composite_basis_w = 0.0
        self._composite_basis_h = 0.0
        super().__init__(parent)
        # 综合面板不展示 L 形专用识别区，避免误导用户；使用下方综合识别按钮。
        self._gb_lshape_recog.setVisible(False)
        self.sketch_pick_requested.connect(self._pick_composite_sketch)
        self.sketch_load_requested.connect(self._load_composite_sketch)
        self._build_composite_controls()

    def _pick_composite_sketch(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择综合形状草图", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;所有文件 (*)")
        if path:
            # 走信号而非直接调用：本地缩略图（自连）与主画布显示（PropertyPanel）同时生效
            self.sketch_load_requested.emit(path)

    def _load_composite_sketch(self, path: str):
        """加载草图 → 本地缩略图 + 主画布显示，并尝试自动识别（D4）。"""
        if not path:
            return
        self.set_sketch_path_for_view(path)
        self.sync_sketch_preview(path)
        if os.path.isfile(path):
            self._auto_recognize_composite_sketch()

    def _auto_recognize_composite_sketch(self):
        """上传后自动识别；缺少尺寸基准时不发起识别，只给可见提示（不弹模态框）。"""
        if not self._has_composite_basis():
            self.set_composite_status(
                "草图已加载。请先填写含尺寸的目标文件名（如 花型_185x88cm.jpg），"
                "或在【外框尺寸】中手动填写外框大小，然后点「识别综合形状草图」。")
            return
        self._recognize_composite_sketch()

    def _has_composite_basis(self) -> bool:
        """识别基准是否可用：外框设计真值必须高于默认下限（D2 守卫）。"""
        return (self.get_outer_w_cm() > _COMPOSITE_BASIS_MIN_CM
                and self.get_outer_h_cm() > _COMPOSITE_BASIS_MIN_CM)

    def mark_composite_basis_from_target(self, w_cm: float, h_cm: float) -> None:
        """记录「尺寸基准来自目标文件名」，供识别与状态提示使用。"""
        try:
            w_cm = float(w_cm)
            h_cm = float(h_cm)
        except (TypeError, ValueError):
            return
        if w_cm <= 0 or h_cm <= 0:
            return
        self._composite_basis_source = 'target'
        self._composite_basis_w = w_cm
        self._composite_basis_h = h_cm

    def set_composite_status(self, msg: str, is_error: bool = False) -> None:
        """综合面板自己的状态行（L 形状态行在隐藏的识别区里，不可见）。"""
        color = "#B00020" if is_error else "#388E3C"
        bg = '#FFEBEE' if is_error else '#E8F5E9'
        self._composite_status.setText(msg)
        self._composite_status.setStyleSheet(
            f"color:{color}; padding:4px 6px; background: {bg}; border-radius: 4px;")

    def clear_composite_sketch(self) -> None:
        """清除草图（含本地缩略图 / 查看路径 / 解析结果），保留用户已填参数。"""
        self.cancel_composite_parse()
        self._composite_parse_result = None
        self.set_sketch_path_for_view("")
        self.sync_sketch_preview("")
        self.set_composite_status("草图已清除（综合形状参数保持不变）")

    def view_composite_sketch(self) -> None:
        """点击缩略图：打开大图查看对话框。"""
        path = self._sk_preview.property('sketch_path')
        if not path or not os.path.isfile(path):
            self.set_composite_status("当前没有可查看的草图", is_error=True)
            return
        try:
            from .property_panel_dialogs import _SketchViewerDialog
            _SketchViewerDialog(path, self).exec_()
        except Exception as e:
            logger.warning(f"[CompositePanel] 打开草图大图失败: {e}")
            self.set_composite_status(f"无法打开草图：{e}", is_error=True)

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
        self._btn_composite_recognize.setToolTip(
            "按当前「外框尺寸」（来自目标文件名或手动填写）为基准，识别草图里的\n"
            "中心矩形洞与 L 形挖角，识别结果直接回填到下方参数与挖角控件。")
        self._btn_composite_recognize.clicked.connect(self._recognize_composite_sketch)
        form.addRow(self._btn_composite_recognize)
        # 状态行：L 形面板的状态行在隐藏的识别区里，这里必须有独立可见的反馈区
        self._composite_status = QLabel("（填写目标文件名并上传草图后，点上方按钮识别综合形状）")
        self._composite_status.setWordWrap(True)
        self._composite_status.setStyleSheet("color:#555; padding: 4px 6px;")
        form.addRow(self._composite_status)
        self._inner_layout.addWidget(self._gb_composite)
        # 生成预览与导出按钮固定放在综合参数之后，作为面板底部主操作。
        if hasattr(self, '_action_layout'):
            self._inner_layout.removeItem(self._action_layout)
            self._inner_layout.addLayout(self._action_layout)

    @staticmethod
    def _make_spin(value):
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 10000.0)
        spin.setDecimals(2)
        spin.setValue(value)
        return spin

    def get_composite_params(self) -> dict:
        """返回可直接映射到 CropDesign 的复合参数。"""
        params = super().get_lshape_params() or {}
        params.update({
            'mode': 'rect_lshape_hole',
            'hole_w_cm': self._hole_w.value(),
            'hole_h_cm': self._hole_h.value(),
            'hole_margin_top_cm': self._hole_mt.value(),
            'hole_margin_bottom_cm': self._hole_mb.value(),
            'hole_margin_left_cm': self._hole_ml.value(),
            'hole_margin_right_cm': self._hole_mr.value(),
            'hole_fill_mode': 'image' if self._hole_material.isChecked() else 'blank',
            # 外框设计真值（与 L 形面板同语义：SpinBox 画布值 − 1cm 损耗）
            'outer_w_cm': self.get_outer_w_cm(),
            'outer_h_cm': self.get_outer_h_cm(),
            'cuts_cm': self.get_cuts_cm(),
        })
        return params

    def to_crop_design(self, *, dpi: int = 150) -> CropDesign:
        """将面板参数转换为独立的复合 CropDesign 快照。

        [D5] canvas_*_cm 必须是画布值（外框设计真值 + CUT_LOSS_CM），
        与水池/L 形面板口径一致；l_cut_* 取真实挖角值而非历史占位默认值，
        否则 validate() 会直接拒绝该 mode 的设计。
        """
        cuts = self.get_cuts_cm()
        if not cuts:
            raise ValueError("综合形状至少需要 1 处挖角（当前挖角列表为空）")
        params = self.get_composite_params()
        primary = cuts[0]
        return CropDesign(
            mode='rect_lshape_hole', dpi=dpi,
            canvas_w_cm=self.get_outer_w_cm() + CUT_LOSS_CM,
            canvas_h_cm=self.get_outer_h_cm() + CUT_LOSS_CM,
            inner_margin_top_cm=params['hole_margin_top_cm'],
            inner_margin_bottom_cm=params['hole_margin_bottom_cm'],
            inner_margin_left_cm=params['hole_margin_left_cm'],
            inner_margin_right_cm=params['hole_margin_right_cm'],
            l_corner=primary['corner'],
            l_cut_w_cm=primary['cut_w_cm'],
            l_cut_h_cm=primary['cut_h_cm'],
            l_cuts_cm=cuts,
        )

    def cancel_composite_parse(self):
        """取消正在运行的综合解析线程（不等待调用方，避免堵塞 UI）。"""
        worker = self._composite_parse_worker
        self._composite_parse_worker = None
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            worker.wait(2000)

    def _recognize_composite_sketch(self):
        """调用统一解析入口并回填控件；失败时保留用户当前值。

        [D2] 识别基准 = 外框设计真值（来自目标文件名解析或用户在【外框尺寸】的输入），
        不再隐式使用面板默认的 4cm —— 那样会把整张草图按 4cm 等比缩放，
        产出"看似成功"的噪声结果。
        """
        sketch_path = self._sk_preview.property('sketch_path')
        if not sketch_path:
            self.set_composite_status("请先上传综合形状草图", is_error=True)
            return
        if not self._has_composite_basis():
            self.set_composite_status(
                "缺少尺寸基准：请填写含尺寸的目标文件名（如 花型_185x88cm.jpg），"
                "或在【外框尺寸】中手动填写外框大小后再识别。", is_error=True)
            return
        basis_w = self.get_outer_w_cm()
        basis_h = self.get_outer_h_cm()
        try:
            self.cancel_composite_parse()
            worker = _CompositeParseWorker(sketch_path, basis_w, basis_h, self)
            worker.finished_ok.connect(self._on_composite_parsed)
            worker.finished_err.connect(self._on_composite_parse_error)
            worker.finished.connect(lambda: self._btn_composite_recognize.setEnabled(True))
            self._composite_parse_worker = worker
            self._btn_composite_recognize.setEnabled(False)
            self.set_composite_status(
                f"正在识别…（基准外框 {basis_w:.1f} × {basis_h:.1f} cm）")
            worker.start()
        except Exception as e:
            logger.exception("[CompositePanel] 综合形状草图识别失败")
            self._btn_composite_recognize.setEnabled(True)
            self.set_composite_status(f"识别启动失败：{e}", is_error=True)

    def _echo_composite_outer(self, outer_w_cm: float, outer_h_cm: float):
        """回填外框：解析结果给的是画布值（真值 + 1cm 损耗），直接写 SpinBox。"""
        try:
            outer_w_cm = float(outer_w_cm or 0.0)
            outer_h_cm = float(outer_h_cm or 0.0)
        except (TypeError, ValueError):
            return
        if outer_w_cm <= 0 or outer_h_cm <= 0:
            return
        self._sp_outer_w.blockSignals(True)
        self._sp_outer_h.blockSignals(True)
        try:
            self._sp_outer_w.setValue(max(self._sp_outer_w.minimum(), outer_w_cm))
            self._sp_outer_h.setValue(max(self._sp_outer_h.minimum(), outer_h_cm))
        finally:
            self._sp_outer_w.blockSignals(False)
            self._sp_outer_h.blockSignals(False)
        self._composite_basis_w = max(0.0, outer_w_cm - CUT_LOSS_CM)
        self._composite_basis_h = max(0.0, outer_h_cm - CUT_LOSS_CM)

    def _echo_composite_cuts(self, cuts: list[dict] | None):
        """回填挖角：走 L 形面板的 set_lshape_cuts()，同时保证 _lshape_params 已建好。"""
        valid = [c for c in (cuts or [])
                 if float(c.get('cut_w_cm', 0) or 0) > 0 and float(c.get('cut_h_cm', 0) or 0) > 0][:4]
        if self._lshape_params is None:
            # set_lshape_cuts() 会写入 self._lshape_params['cuts_cm']，必须先建 dict
            self._lshape_params = {
                'corner': self._cb_lcorner.currentData(),
                'cut_w_cm': 0.0,
                'cut_h_cm': 0.0,
                'cuts_cm': [],
                'outer_w_cm': self.get_outer_w_cm(),
                'outer_h_cm': self.get_outer_h_cm(),
            }
        if valid:
            # 综合识别的挖角是"标准多角"语义：先退出阶梯模式，避免 get_cuts_cm() 返回空
            if getattr(self, '_staircase_mode', False):
                self._set_staircase_mode(False)
            self.set_lshape_cuts(valid)
            primary = valid[0]
        else:
            self._lshape_params['cuts_cm'] = []
            primary = {
                'corner': self._cb_lcorner.currentData(),
                'cut_w_cm': max(0.0, self._sp_lw.value()),
                'cut_h_cm': max(0.0, self._sp_lh.value()),
            }
        self._lshape_params['corner'] = primary['corner']
        self._lshape_params['cut_w_cm'] = primary['cut_w_cm']
        self._lshape_params['cut_h_cm'] = primary['cut_h_cm']
        self._params_source = 'recognize'

    def _on_composite_parsed(self, result):
        self._composite_parse_result = result
        if not result.success:
            self.set_composite_status(
                f"识别未通过：{result.message or '未检测到可用的综合形状几何'}",
                is_error=True)
            self.composite_recognize_finished.emit(result)
            return
        # 1) 中心洞 + 四边内边距（6 项）
        for widget, value in ((self._hole_w, result.hole_w_cm),
                              (self._hole_h, result.hole_h_cm),
                              (self._hole_mt, result.margin_top_cm),
                              (self._hole_mb, result.margin_bottom_cm),
                              (self._hole_ml, result.margin_left_cm),
                              (self._hole_mr, result.margin_right_cm)):
            widget.setValue(float(value or 0.0))
        # 2) 外框（2 项）：结果给的是画布值
        self._echo_composite_outer(result.outer_w_cm, result.outer_h_cm)
        # 3) 挖角（N ≤ 4 项）
        self._echo_composite_cuts(list(result.cuts_cm or []))
        self._composite_basis_source = 'recognize'
        cuts_n = len(self.get_cuts_cm())
        self.set_composite_status(
            f"✅ 识别完成：外框 {self.get_outer_w_cm():.1f} × {self.get_outer_h_cm():.1f} cm"
            f"（画布 {self.get_outer_w_cm() + CUT_LOSS_CM:.1f} × {self.get_outer_h_cm() + CUT_LOSS_CM:.1f} cm），"
            f"中心洞 {result.hole_w_cm:.2f} × {result.hole_h_cm:.2f} cm，"
            f"{cuts_n} 处挖角，边距 上{result.margin_top_cm:.2f}/下{result.margin_bottom_cm:.2f}"
            f"/左{result.margin_left_cm:.2f}/右{result.margin_right_cm:.2f} cm")
        self.composite_params_changed.emit(self.get_composite_params())
        self.composite_recognize_finished.emit(result)

    def _on_composite_parse_error(self, message):
        logger.warning('[CompositePanel] 识别异常：%s', message)
        self.set_composite_status(f"识别异常：{message}", is_error=True)

    def shutdown(self):
        self.cancel_composite_parse()
        super().shutdown()
