"""Compatibility boundary for the L-shape parameter section."""
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QWidget, QGroupBox, QVBoxLayout
from core.config import CUT_LOSS_CM


class LShapeParameterControl(QWidget):
    """Adapter for mode selection and L-shape parameter construction."""

    parametersEdited = pyqtSignal(str)

    def __init__(self, panel, corner_control):
        super().__init__(panel)
        self._panel = panel
        self._corner_control = corner_control
        self.parametersEdited.connect(self._apply_parameter_edit)

    def _apply_parameter_edit(self, mode: str):
        if mode == 'staircase':
            self._panel._sync_staircase_snapshot()
        else:
            self._panel._on_param_changed_legacy()

    def on_standard_changed(self, *_):
        self.parametersEdited.emit('standard')

    def on_staircase_changed(self, *_):
        self.parametersEdited.emit('staircase')

    @property
    def mode_combo(self):
        return self._corner_control._mode_combo

    def build_mode_selector(self):
        return self._corner_control.build_mode_selector()

    def build_outer_ui(self):
        """Create the outer-size controls and return their legacy handles."""
        group = QGroupBox("外框尺寸（cm）", self)
        group.setStyleSheet(self._panel._param_group_style("#5B6CFF"))
        form = QVBoxLayout(group)
        form.setSpacing(6)
        width = self._panel._dspin(5, 500, 5.0)
        height = self._panel._dspin(5, 500, 5.0)
        form.addLayout(self._panel._row("宽(cm)", width))
        form.addLayout(self._panel._row("高(cm)", height))
        self._gb_outer, self._sp_outer_w, self._sp_outer_h = group, width, height
        return group, width, height

    def build_standard_ui(self):
        """Create the standard L-shape group through the established contract."""
        group = self._corner_control.build_standard_ui()
        self._gb_l = group
        self._corner_rows = self._corner_control._corner_rows
        self._cb_lcorner = self._corner_control._cb_lcorner
        self._sp_lw = self._corner_control._sp_lw
        self._sp_lh = self._corner_control._sp_lh
        self._margin_hint = self._corner_control._margin_hint
        return group

    def build_staircase_ui(self):
        group = self._corner_control.build_staircase_ui()
        self._gb_staircase = group
        self._stair_corner = self._corner_control._stair_corner
        self._stair_rows_container = self._corner_control._stair_rows_container
        self._stair_rows = self._corner_control._stair_rows
        self._stair_add_btns = self._corner_control._stair_add_btns
        return group

    def set_staircase_mode(self, enabled: bool):
        return self._corner_control.set_staircase_mode(enabled)

    def get_outer_size(self) -> tuple[float, float]:
        return self._sp_outer_w.value(), self._sp_outer_h.value()

    def set_outer_size(self, width: float, height: float, *, block_signals: bool = False):
        widgets = (self._sp_outer_w, self._sp_outer_h)
        if block_signals:
            for widget in widgets:
                widget.blockSignals(True)
        try:
            self._sp_outer_w.setValue(max(0.0, float(width)))
            self._sp_outer_h.setValue(max(0.0, float(height)))
        finally:
            if block_signals:
                for widget in widgets:
                    widget.blockSignals(False)

    def set_standard_params(self, corner: str, cut_w_cm: float, cut_h_cm: float):
        self._cb_lcorner.blockSignals(True)
        self._sp_lw.blockSignals(True)
        self._sp_lh.blockSignals(True)
        try:
            index = self._cb_lcorner.findData(corner)
            if index >= 0:
                self._cb_lcorner.setCurrentIndex(index)
            self._sp_lw.setValue(max(0.0, float(cut_w_cm)))
            self._sp_lh.setValue(max(0.0, float(cut_h_cm)))
            self._corner_rows[0][0].setChecked(True)
            for row in self._corner_rows[1:]:
                row[0].setChecked(False)
        finally:
            self._cb_lcorner.blockSignals(False)
            self._sp_lw.blockSignals(False)
            self._sp_lh.blockSignals(False)
        return self.get_cuts_cm()

    def set_cuts(self, cuts: list[dict] | None):
        cuts = list(cuts or [])[:4]
        for index, (enabled, combo, width, height) in enumerate(self._corner_rows):
            enabled.blockSignals(True); combo.blockSignals(True)
            width.blockSignals(True); height.blockSignals(True)
            try:
                if index < len(cuts):
                    cut = cuts[index]
                    enabled.setChecked(True)
                    combo.setCurrentIndex(max(0, combo.findData(cut.get('corner', 'br'))))
                    width.setValue(max(0.0, float(cut.get('cut_w_cm', 0))))
                    height.setValue(max(0.0, float(cut.get('cut_h_cm', 0))))
                else:
                    enabled.setChecked(False)
            finally:
                enabled.blockSignals(False); combo.blockSignals(False)
                width.blockSignals(False); height.blockSignals(False)
        return self.get_cuts_cm()

    def get_corner(self) -> str:
        return self._corner_control.get_corner()

    def get_lshape_params(self):
        return self._corner_control.get_lshape_params()

    def get_cut_w_cm(self) -> float:
        return self._corner_control.get_cut_w_cm()

    def get_cut_h_cm(self) -> float:
        return self._corner_control.get_cut_h_cm()

    def get_cuts_cm(self) -> list[dict]:
        return self._corner_control.get_cuts_cm()

    def manual_cut_snapshot(self) -> tuple[list[dict], dict]:
        cuts = self.get_cuts_cm()
        if cuts:
            return cuts, dict(cuts[0])
        return [], {
            'corner': self._cb_lcorner.currentData(),
            'cut_w_cm': max(0.0, self._sp_lw.value()),
            'cut_h_cm': max(0.0, self._sp_lh.value()),
        }

    def build_manual_params(self, outer_w_cm: float, outer_h_cm: float) -> dict:
        """Compatibility API accepting outer dimensions already in design cm."""
        cuts, primary = self.manual_cut_snapshot()
        return {
            **primary, 'cuts_cm': cuts,
            'outer_w_cm': max(0.0, float(outer_w_cm)),
            'outer_h_cm': max(0.0, float(outer_h_cm)),
        }

    def build_parameter_snapshot(self) -> dict:
        """Read either mode without mutating the model; convert canvas cm once."""
        canvas_w, canvas_h = self.get_outer_size()
        outer_w = max(0.0, canvas_w - CUT_LOSS_CM)
        outer_h = max(0.0, canvas_h - CUT_LOSS_CM)
        if not self._corner_control.staircase_mode:
            return self.build_manual_params(outer_w, outer_h)
        rects = self.get_cut_rects_cm()
        return {
            'corner': self._stair_corner.currentData() or 'tr',
            'cut_w_cm': rects[0]['w_cm'] if rects else 0.0,
            'cut_h_cm': sum(rect['h_cm'] for rect in rects),
            'cuts_cm': [], 'cut_rects': rects,
            'outer_w_cm': outer_w, 'outer_h_cm': outer_h,
        }

    def set_cut_rects(self, cut_rects: list[dict]):
        """识别结果回填：把 CutRect 条带列表逆换算为步进值写入阶梯子行 SpinBox。

        cut_rects: [{'anchor': str, 'offset_x_cm': float, 'offset_y_cm': float,
                      'w_cm': float, 'h_cm': float}, ...]
        逆换算：r_i = w_i − w_{i+1}（末级 r_N = w_N）；d_i = h_i；按 offset_y 升序。
        自动切换到阶梯模式（_gb_staircase 可见，_gb_l 隐藏）。
        子行数按输入长度调整（1~3），多余行删除，不足行追加。
        """
        cut_rects = list(cut_rects or [])[:self._panel._stair_max_levels]
        if not cut_rects:
            return
        self.set_staircase_mode(True)
        anchor = cut_rects[0].get('anchor', 'tr')
        idx = self._stair_corner.findData(anchor)
        self._stair_corner.blockSignals(True)
        if idx >= 0:
            self._stair_corner.setCurrentIndex(idx)
        self._stair_corner.blockSignals(False)
        rects = sorted(
            cut_rects,
            key=lambda c: (float(c.get('offset_y_cm', 0)), float(c.get('offset_x_cm', 0))))
        steps = []
        for i, cr in enumerate(rects):
            w_i = max(0.0, float(cr.get('w_cm', 0)))
            d_i = max(0.0, float(cr.get('h_cm', 0)))
            if i < len(rects) - 1:
                w_next = max(0.0, float(rects[i + 1].get('w_cm', 0)))
                r_i = w_i - w_next if w_i > w_next else 0.0
            else:
                r_i = w_i
            steps.append((r_i, d_i))
        while len(self._stair_rows) > len(steps):
            self._corner_control.remove_level()
        for i, (r_val, d_val) in enumerate(steps):
            if i >= len(self._stair_rows):
                self._corner_control.add_level_row()
            r_sp, d_sp, _ = self._stair_rows[i]
            for sp in (r_sp, d_sp):
                sp.blockSignals(True)
            try:
                r_sp.setValue(max(0.0, r_val))
                d_sp.setValue(max(0.0, d_val))
            finally:
                for sp in (r_sp, d_sp):
                    sp.blockSignals(False)
        self._corner_control.update_buttons()
        self.on_staircase_changed()

    def get_cut_rects_cm(self) -> list[dict]:
        return self._corner_control.get_cut_rects_cm()

    def add_level_row(self, *args):
        return self._corner_control.add_level_row(*args)

    def add_level(self):
        return self._corner_control.add_level()

    def remove_level(self):
        return self._corner_control.remove_level()

    def update_buttons(self):
        return self._corner_control.update_buttons()

    def on_mode_combo_changed(self, *args):
        return self._corner_control.on_mode_combo_changed(*args)
