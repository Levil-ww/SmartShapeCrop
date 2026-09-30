"""Compatibility boundary for the L-shape parameter section."""
from PyQt5.QtWidgets import QWidget


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

    def build_staircase_ui(self):
        return self._corner_control.build_staircase_ui()

    def set_staircase_mode(self, enabled: bool):
        return self._corner_control.set_staircase_mode(enabled)
