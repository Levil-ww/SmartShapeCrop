"""组合式 L 形挖角控件适配层。

第二阶段重构的第一步：集中挖角相关的公共读写契约。
当前 UI 仍由 ``LShapePanel`` 创建，以保证本步不改变布局和交互；后续
可以把控件创建代码迁移到这里，而调用方无需再改 API。
"""
from __future__ import annotations

from PyQt5.QtWidgets import QWidget

class CornerCutControl(QWidget):
    """为标准多角和单边阶梯模式提供统一的挖角契约。

    ``panel`` 是暂时的 UI 宿主。所有方法都调用宿主的 legacy 实现，避免
    在迁移期间复制几何规则或改变现有的信号、数值换算和模式切换行为。
    """

    def __init__(self, panel):
        # QWidget 化是本阶段的迁移边界。现有控件仍由 LShapePanel 创建并
        # 保持原布局，避免 Qt layout reparent 导致视觉和信号行为变化。
        super().__init__(panel)
        self.setObjectName("cornerCutControl")
        self._panel = panel

    def get_mode(self) -> str:
        return "staircase" if self._panel._staircase_mode else "standard"

    def set_mode(self, mode: str) -> None:
        self._panel._set_mode_legacy(mode == "staircase")

    def get_corner(self) -> str:
        return self._panel._get_corner_legacy()

    def get_cut_w_cm(self) -> float:
        return self._panel._get_cut_w_cm_legacy()

    def get_cut_h_cm(self) -> float:
        return self._panel._get_cut_h_cm_legacy()

    def get_cuts_cm(self) -> list[dict]:
        return self._panel._get_cuts_cm_legacy()

    def get_cut_rects_cm(self) -> list[dict]:
        return self._panel._get_cut_rects_cm_legacy()

    def get_lshape_params(self):
        return self._panel._get_lshape_params_legacy()

    def set_lshape_params(self, corner: str, cut_w_cm: float, cut_h_cm: float):
        return self._panel._set_lshape_params_legacy(corner, cut_w_cm, cut_h_cm)

    def set_lshape_cuts(self, cuts: list[dict] | None):
        return self._panel._set_lshape_cuts_legacy(cuts)

