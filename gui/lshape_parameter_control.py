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
