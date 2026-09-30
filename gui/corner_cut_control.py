"""组合式 L 形挖角控件适配层。

第二阶段重构的第一步：集中挖角相关的公共读写契约。
当前 UI 仍由 ``LShapePanel`` 创建，以保证本步不改变布局和交互；后续
可以把控件创建代码迁移到这里，而调用方无需再改 API。
"""
from __future__ import annotations

from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QComboBox, QGroupBox,
    QCheckBox, QPushButton,
)
from core.config import CUT_LOSS_CM

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
        panel = self._panel
        panel._staircase_mode = enabled
        self._gb_l.setVisible(not enabled)
        self._gb_staircase.setVisible(enabled)
        self._mode_combo.blockSignals(True)
        try:
            self._mode_combo.setCurrentIndex(1 if enabled else 0)
        finally:
            self._mode_combo.blockSignals(False)

    def on_mode_combo_changed(self, *args) -> None:
        panel = self._panel
        want = self._mode_combo.currentData() == 'staircase'
        if want == panel._staircase_mode:
            return
        if want:
            panel._lshape_params = None
            panel._params_source = None
            self.set_staircase_mode(True)
            while len(self._stair_rows) < 2:
                self.add_level_row()
            while len(self._stair_rows) > 2:
                self.remove_level()
            for r_sp, d_sp, _ in self._stair_rows:
                r_sp.blockSignals(True); d_sp.blockSignals(True)
                r_sp.setValue(0.0); d_sp.setValue(0.0)
                r_sp.blockSignals(False); d_sp.blockSignals(False)
            self.update_buttons()
            panel._on_staircase_changed()
            panel._set_status("已切换到「单边阶梯 L 形」：共用一个角位，逐级填「步进宽 × 落差」（第 1 级 = 远端第一步，依次向角位）")
        else:
            rects = self.get_cut_rects_cm()
            first = rects[0] if rects else None
            self.set_staircase_mode(False)
            panel._lshape_params = None
            panel._params_source = None
            if first is not None:
                ci = self._cb_lcorner.findData(first['anchor'])
                if ci >= 0:
                    self._cb_lcorner.setCurrentIndex(ci)
                self._sp_lw.setValue(first['w_cm'])
                self._sp_lh.setValue(first['h_cm'])
            for enabled, _combo, width, height in self._corner_rows[1:]:
                enabled.setChecked(False); width.setValue(0.0); height.setValue(0.0)
            panel._on_param_changed()
            panel._set_status("已切换到「标准 L 形」模式（保留第 1 级参数）")

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
        """创建阶梯参数组；行内容仍通过兼容入口填充。"""
        panel = self._panel
        self._gb_staircase = QGroupBox("单边阶梯挖角参数", self)
        self._gb_staircase.setStyleSheet(panel._param_group_style("#E67E22"))
        form = QVBoxLayout(self._gb_staircase)
        form.setSpacing(6)
        row_corner = QHBoxLayout()
        row_corner.addWidget(QLabel("角位"), 0)
        self._stair_corner = QComboBox(self)
        for text, data in (("左上角", "tl"), ("右上角", "tr"),
                           ("左下角", "bl"), ("右下角", "br")):
            self._stair_corner.addItem(text, data)
        self._stair_corner.setCurrentIndex(1)
        self._stair_corner.currentIndexChanged.connect(panel._on_staircase_changed)
        row_corner.addWidget(self._stair_corner, 1)
        form.addLayout(row_corner)
        self._stair_rows_container = QVBoxLayout()
        self._stair_rows_container.setSpacing(4)
        form.addLayout(self._stair_rows_container)
        self._stair_rows = []
        self._stair_add_btns = []
        panel._stair_corner = self._stair_corner
        panel._stair_rows_container = self._stair_rows_container
        panel._stair_rows = self._stair_rows
        panel._stair_add_btns = self._stair_add_btns
        self.add_level_row()
        self.add_level_row()
        self.update_buttons()
        return self._gb_staircase

    def add_level_row(self, r: float = 0.0, d: float = 0.0):
        panel = self._panel
        if len(self._stair_rows) >= panel._stair_max_levels:
            return
        level_idx = len(self._stair_rows)
        sp_r = panel._dspin(0, 450, r)
        sp_d = panel._dspin(0, 450, d)
        for sp in (sp_r, sp_d):
            sp.valueChanged.connect(panel._on_staircase_changed)
        row = QHBoxLayout()
        row.setSpacing(4)
        if level_idx > 0:
            arrow = QLabel("↳")
            arrow.setStyleSheet("color:#E67E22; font-size:12px;")
            row.addWidget(arrow, 0)
        label = QLabel(f"第{level_idx + 1}级")
        label.setStyleSheet("color:#E67E22; font-weight:bold; font-size:12px;")
        row.addWidget(label, 0)
        row.addWidget(QLabel("宽"), 0)
        row.addWidget(sp_r, 1)
        row.addWidget(QLabel("cm"), 0)
        row.addWidget(QLabel("高"), 0)
        row.addWidget(sp_d, 1)
        add_btn = QPushButton("+ 追加一级")
        add_btn.setFixedWidth(80)
        add_btn.clicked.connect(self.add_level)
        row.addWidget(add_btn, 0)
        self._stair_add_btns.append(add_btn)
        container = QWidget()
        container.setLayout(row)
        if level_idx > 0:
            container.setStyleSheet(f"margin-left: {16 * level_idx}px;")
        self._stair_rows_container.addWidget(container)
        self._stair_rows.append((sp_r, sp_d, container))
        self.update_buttons()

    def add_level(self):
        if len(self._stair_rows) < self._panel._stair_max_levels:
            self.add_level_row()
            self._panel._on_staircase_changed()

    def remove_level(self):
        if len(self._stair_rows) <= 1:
            return
        _r, _d, widget = self._stair_rows.pop()
        self._stair_rows_container.removeWidget(widget)
        widget.setParent(None)
        widget.deleteLater()
        button = self._stair_add_btns.pop()
        button.setParent(None)
        button.deleteLater()
        self.update_buttons()
        self._panel._on_staircase_changed()

    def update_buttons(self):
        n = len(self._stair_rows)
        for i, button in enumerate(self._stair_add_btns):
            button.setVisible(i == n - 1 and n < self._panel._stair_max_levels)

    def on_staircase_changed(self):
        panel = self._panel
        if not panel._staircase_mode:
            return
        rects = self.get_cut_rects_cm()
        outer_w = max(0.0, panel._sp_outer_w.value() - CUT_LOSS_CM)
        outer_h = max(0.0, panel._sp_outer_h.value() - CUT_LOSS_CM)
        anchor = self._stair_corner.currentData() or 'tr'
        primary_w = rects[0]['w_cm'] if rects else 0.0
        primary_h = sum(item['h_cm'] for item in rects)
        if panel._lshape_params is None:
            panel._lshape_params = {}
        panel._lshape_params.update({
            'corner': anchor, 'cut_w_cm': primary_w, 'cut_h_cm': primary_h,
            'cuts_cm': [], 'cut_rects': rects,
            'outer_w_cm': outer_w, 'outer_h_cm': outer_h,
        })
        panel._params_source = 'manual'
        if not rects:
            panel._set_status(
                "请填写至少一级「步进宽 × 落差」（均需 > 0）后再生成预览",
                is_error=True)

    def on_param_changed(self, *args):
        """参数变化入口；面板保留外框换算和状态提示实现。"""
        return self._panel._on_param_changed_legacy(*args)

    def manual_cut_snapshot(self) -> tuple[list[dict], dict]:
        cuts = self.get_cuts_cm()
        if cuts:
            return cuts, dict(cuts[0])
        return [], {
            'corner': self._cb_lcorner.currentData(),
            'cut_w_cm': max(0.0, self._sp_lw.value()),
            'cut_h_cm': max(0.0, self._sp_lh.value()),
        }

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

