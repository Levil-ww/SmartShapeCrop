"""Compatibility boundary for the L-shape parameter section."""
from PyQt5.QtWidgets import QWidget, QGroupBox, QVBoxLayout


class LShapeParameterControl(QWidget):
    """Adapter for mode selection and L-shape parameter construction."""

    def __init__(self, panel, corner_control):
        super().__init__(panel)
        self._panel = panel
        self._corner_control = corner_control

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

    def get_cut_w_cm(self) -> float:
        return self._corner_control.get_cut_w_cm()

    def get_cut_h_cm(self) -> float:
        return self._corner_control.get_cut_h_cm()

    def get_cuts_cm(self) -> list[dict]:
        return self._corner_control.get_cuts_cm()
