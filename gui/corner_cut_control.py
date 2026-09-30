"""组合式 L 形挖角控件适配层。

第二阶段重构的第一步：集中挖角相关的公共读写契约。
当前 UI 仍由 ``LShapePanel`` 创建，以保证本步不改变布局和交互；后续
可以把控件创建代码迁移到这里，而调用方无需再改 API。
"""
from __future__ import annotations

from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QComboBox, QGroupBox,
    QCheckBox,
)

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
        self._stair_rows = []
        self._stair_add_btns = []
        self._stair_corner = None
        self._stair_rows_container = None
        self._corner_rows = []
        self._gb_l = None
        self._cb_lcorner = None
        self._sp_lw = None
        self._sp_lh = None

    @property
    def staircase_mode(self) -> bool:
        return self._panel._staircase_mode

    @property
    def stair_rows(self):
        return self._stair_rows

    @property
    def corner_rows(self):
        return self._panel._corner_rows

    def set_staircase_mode(self, enabled: bool) -> None:
        self._panel._set_staircase_mode_legacy(enabled)

    def on_mode_combo_changed(self, *args) -> None:
        self._panel._on_mode_combo_changed_legacy(*args)

    def build_mode_selector(self):
        """创建模式选择行；返回布局供宿主插入原位置。"""
        row = QHBoxLayout()
        row.addWidget(QLabel("挖角模式"), 0)
        self._mode_combo = QComboBox(self)
        self._mode_combo.addItem("标准 L 形（多角位）", "standard")
        self._mode_combo.addItem("单边阶梯 L 形（同角位多级）", "staircase")
        self._mode_combo.currentIndexChanged.connect(self.on_mode_combo_changed)
        row.addWidget(self._mode_combo, 1)
        return row

    def build_staircase_ui(self):
        """构建阶梯 UI 的迁移入口。

        本步骤先迁移构建责任入口，内部仍调用宿主的兼容实现；下一步再将
        rows、角位选择器和容器的真实状态所有权移入本控件。
        """
        self._panel._build_staircase_ui_legacy()
        self._stair_rows = self._panel._stair_rows
        self._stair_add_btns = self._panel._stair_add_btns
        self._stair_corner = self._panel._stair_corner
        self._stair_rows_container = self._panel._stair_rows_container

    def adopt_standard_ui(self):
        """接管标准多角 UI 的 canonical 引用，保持宿主兼容字段不变。"""
        panel = self._panel
        self._corner_rows = panel._corner_rows
        self._gb_l = panel._gb_l
        self._cb_lcorner = panel._cb_lcorner
        self._sp_lw = panel._sp_lw
        self._sp_lh = panel._sp_lh

    def build_standard_ui(self):
        """创建标准多角参数组，并返回可插入宿主布局的 GroupBox。"""
        panel = self._panel
        group = QGroupBox("L 形挖角参数", self)
        group.setStyleSheet(panel._param_group_style("#5B6CFF"))
        form = QVBoxLayout(group)
        form.setSpacing(6)
        rows = []
        for row_index in range(4):
            enabled = QCheckBox(f"挖角 {row_index + 1}", group)
            enabled.setChecked(row_index == 0)
            combo = QComboBox(group)
            combo.addItem("左上角", "tl")
            combo.addItem("右上角", "tr")
            combo.addItem("左下角", "bl")
            combo.addItem("右下角", "br")
            combo.setCurrentIndex(3 if row_index == 0 else row_index)
            width = panel._dspin(0, 450, 0.0)
            height = panel._dspin(0, 450, 0.0)
            enabled.toggled.connect(panel._on_param_changed)
            combo.currentIndexChanged.connect(panel._on_param_changed)
            width.valueChanged.connect(panel._on_param_changed)
            height.valueChanged.connect(panel._on_param_changed)
            row = QHBoxLayout()
            row.addWidget(enabled, 0)
            row.addWidget(combo, 1)
            row.addWidget(QLabel("宽"), 0)
            row.addWidget(width, 1)
            row.addWidget(QLabel("高"), 0)
            row.addWidget(height, 1)
            form.addLayout(row)
            rows.append((enabled, combo, width, height))
        margin_hint = QLabel("边余量：上— · 下— · 左— · 右—", group)
        margin_hint.setObjectName("margin_hint")
        margin_hint.setWordWrap(True)
        margin_hint.setStyleSheet("color:#667085; padding: 2px 4px;")
        form.addWidget(margin_hint)
        self._corner_rows = rows
        self._gb_l = group
        self._cb_lcorner = rows[0][1]
        self._sp_lw = rows[0][2]
        self._sp_lh = rows[0][3]
        self._margin_hint = margin_hint
        return group

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
        panel = self._panel
        if panel._staircase_mode:
            # 旧格式不允许同角位重复；阶梯真值由 cut rects 承载。
            return []
        cuts = []
        for enabled, combo, width, height in panel._corner_rows:
            if not enabled.isChecked() or width.value() <= 0 or height.value() <= 0:
                continue
            cuts.append({
                'corner': combo.currentData(),
                'cut_w_cm': width.value(),
                'cut_h_cm': height.value(),
            })
        return cuts[:4]

    def get_cut_rects_cm(self) -> list[dict]:
        panel = self._panel
        if not panel._staircase_mode:
            return []
        anchor = panel._stair_corner.currentData() or 'tr'
        rows = [(r_sp.value(), d_sp.value())
                for r_sp, d_sp, _ in panel._stair_rows]
        rows = [(r, d) for r, d in rows if r > 0 and d > 0]
        if not rows:
            return []
        result = []
        prev_y = 0.0
        total_w = sum(r for r, _ in rows)
        for r, d in rows:
            result.append({
                'anchor': anchor,
                'offset_x_cm': 0.0,
                'offset_y_cm': round(prev_y, 2),
                'w_cm': round(total_w, 2),
                'h_cm': round(d, 2),
            })
            prev_y += d
            total_w -= r
        return result

    def get_lshape_params(self):
        return self._panel._get_lshape_params_legacy()

    def set_lshape_params(self, corner: str, cut_w_cm: float, cut_h_cm: float):
        return self._panel._set_lshape_params_legacy(corner, cut_w_cm, cut_h_cm)

    def set_lshape_cuts(self, cuts: list[dict] | None):
        return self._panel._set_lshape_cuts_legacy(cuts)

