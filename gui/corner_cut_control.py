"""组合式 L 形挖角控件适配层。

第二阶段重构的第一步：集中挖角相关的公共读写契约。
当前 UI 仍由 ``LShapePanel`` 创建，以保证本步不改变布局和交互；后续
可以把控件创建代码迁移到这里，而调用方无需再改 API。
"""
from __future__ import annotations

from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QLabel, QComboBox, QGroupBox,
    QCheckBox, QPushButton,
)
from core.config import CUT_LOSS_CM

class CornerCutControl(QWidget):
    """为标准多角和单边阶梯模式提供统一的挖角契约。

    ``panel`` 是暂时的 UI 宿主。所有方法都调用宿主的 legacy 实现，避免
    在迁移期间复制几何规则或改变现有的信号、数值换算和模式切换行为。
    """

    def __init__(self, panel, *, allow_multicorner_staircase: bool = False):
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
        self._allow_multicorner_staircase = allow_multicorner_staircase
        self._multicorner_staircase_mode = False
        self._multi_stair_groups = {}
        self._gb_multi_staircase = None

    @property
    def staircase_mode(self) -> bool:
        return self._panel._staircase_mode

    @property
    def stair_rows(self):
        return self._stair_rows

    @property
    def corner_rows(self):
        return self._panel._corner_rows

    def set_staircase_mode(self, enabled: bool, *, multicorner: bool = False) -> None:
        panel = self._panel
        panel._staircase_mode = enabled
        self._multicorner_staircase_mode = (
            enabled and multicorner and self._allow_multicorner_staircase)
        self._gb_l.setVisible(not enabled)
        self._gb_staircase.setVisible(enabled and not self._multicorner_staircase_mode)
        if self._gb_multi_staircase is not None:
            self._gb_multi_staircase.setVisible(self._multicorner_staircase_mode)
        self._mode_combo.blockSignals(True)
        try:
            mode = ('multicorner_staircase' if self._multicorner_staircase_mode
                    else 'staircase' if enabled else 'standard')
            self._mode_combo.setCurrentIndex(self._mode_combo.findData(mode))
        finally:
            self._mode_combo.blockSignals(False)

    def on_mode_combo_changed(self, *args) -> None:
        panel = self._panel
        mode = self._mode_combo.currentData()
        if mode == self.get_mode():
            return
        if mode in ('staircase', 'multicorner_staircase') and panel._staircase_mode:
            rects = self.get_cut_rects_cm()
            if mode == 'staircase' and rects:
                anchor = rects[0]['anchor']
                rects = [rect for rect in rects if rect['anchor'] == anchor]
            self.set_staircase_mode(True, multicorner=mode == 'multicorner_staircase')
            if rects:
                panel._parameter_control.set_cut_rects(
                    rects, multicorner=mode == 'multicorner_staircase')
            elif mode == 'multicorner_staircase':
                self.reset_multi_staircase()
            else:
                panel._parameter_control._fill_stair_rows([])
            panel._parameter_control.on_staircase_changed()
            panel._set_status("已切换阶梯模式：保留当前角位的步进宽与落差" if mode == 'staircase'
                              else "已切换到「多角位阶梯 L 形」：每角独立填写步进宽与落差，最多 3 级")
            return
        if mode == 'multicorner_staircase':
            panel._lshape_params = None
            panel._params_source = None
            self.reset_multi_staircase()
            self.set_staircase_mode(True, multicorner=True)
            panel._parameter_control.on_staircase_changed()
            panel._set_status("已切换到「多角位阶梯 L 形」：勾选角位，每角独立填写步进宽与落差，最多 3 级")
        elif mode == 'staircase':
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
            panel._parameter_control.on_staircase_changed()
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
            panel._parameter_control.on_standard_changed()
            panel._set_status("已切换到「标准 L 形」模式（保留第 1 级参数）")

    def build_mode_selector(self):
        """创建模式选择行；返回布局供宿主插入原位置。"""
        row = QHBoxLayout()
        row.addWidget(QLabel("挖角模式"), 0)
        self._mode_combo = QComboBox(self)
        self._mode_combo.addItem("标准 L 形（多角位）", "standard")
        self._mode_combo.addItem("单边阶梯 L 形（同角位多级）", "staircase")
        if self._allow_multicorner_staircase:
            self._mode_combo.addItem("多角位阶梯 L 形（每角独立多级）", "multicorner_staircase")
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
        self._stair_corner.currentIndexChanged.connect(panel._parameter_control.on_staircase_changed)
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

    def build_multi_staircase_ui(self):
        """固定四角，复用单边的级数控件和步进语义。"""
        if not self._allow_multicorner_staircase:
            return None
        self._gb_multi_staircase = QGroupBox("多角位阶梯挖角参数", self)
        self._gb_multi_staircase.setStyleSheet(self._panel._param_group_style("#E67E22"))
        form = QVBoxLayout(self._gb_multi_staircase)
        form.setContentsMargins(10, 8, 10, 8)
        form.setSpacing(6)
        hint = QLabel("步进宽 × 落差（cm）｜每角最多 3 级；第 1 级从远端开始，依次向角位。")
        hint.setWordWrap(True)
        form.addWidget(hint)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        form.addLayout(grid)
        for index, (label, anchor) in enumerate((("左上角", "tl"), ("右上角", "tr"),
                                               ("左下角", "bl"), ("右下角", "br"))):
            box = QGroupBox(label, self._gb_multi_staircase)
            box.setCheckable(True)
            box.setChecked(anchor == 'tr')
            layout = QVBoxLayout(box)
            layout.setContentsMargins(8, 6, 8, 6)
            layout.setSpacing(4)
            rows_layout = QVBoxLayout()
            rows_layout.setSpacing(4)
            layout.addLayout(rows_layout)
            actions = QHBoxLayout()
            actions.setSpacing(4)
            actions.addStretch(1)
            layout.addLayout(actions)
            group = {'box': box, 'layout': rows_layout, 'rows': [], 'buttons': [],
                     'actions': actions}
            self._multi_stair_groups[anchor] = group
            self.add_level_row(group=group)
            self.add_level_row(group=group)
            remove = QPushButton("− 末级", box)
            remove.setToolTip("移除最后一级，至少保留一级")
            remove.setFixedWidth(72)
            remove.clicked.connect(lambda _=False, g=group: self.remove_level(g))
            group['remove'] = remove
            actions.addWidget(remove)
            self.update_buttons(group)
            box.toggled.connect(self._panel._parameter_control.on_staircase_changed)
            grid.addWidget(box, index // 2, index % 2)
        self._gb_multi_staircase.setVisible(False)
        return self._gb_multi_staircase

    def reset_multi_staircase(self):
        for anchor, group in self._multi_stair_groups.items():
            group['box'].blockSignals(True)
            group['box'].setChecked(anchor == 'tr')
            group['box'].blockSignals(False)
            while len(group['rows']) > 2:
                self.remove_level(group, notify=False)
            while len(group['rows']) < 2:
                self.add_level_row(group=group)
            for r_sp, d_sp, _ in group['rows']:
                for sp in (r_sp, d_sp):
                    sp.blockSignals(True)
                    sp.setValue(0)
                    sp.blockSignals(False)
            self.update_buttons(group)

    def add_level_row(self, r: float = 0.0, d: float = 0.0, *, group=None):
        panel = self._panel
        rows = self._stair_rows if group is None else group['rows']
        buttons = self._stair_add_btns if group is None else group['buttons']
        layout = self._stair_rows_container if group is None else group['layout']
        if len(rows) >= panel._stair_max_levels:
            return
        level_idx = len(rows)
        sp_r = panel._dspin(0, 450, r)
        sp_d = panel._dspin(0, 450, d)
        for sp in (sp_r, sp_d):
            sp.valueChanged.connect(panel._parameter_control.on_staircase_changed)
        row = QHBoxLayout()
        row.setSpacing(4)
        if group is not None:
            row.setContentsMargins(0, 0, 0, 0)
        if level_idx > 0 and group is None:
            arrow = QLabel("↳")
            arrow.setStyleSheet("color:#E67E22; font-size:12px;")
            row.addWidget(arrow, 0)
        label = QLabel(f"第{level_idx + 1}级")
        label.setStyleSheet("color:#E67E22; font-weight:bold; font-size:12px;")
        row.addWidget(label, 0)
        row.addWidget(QLabel("宽"), 0)
        row.addWidget(sp_r, 1)
        if group is None:
            row.addWidget(QLabel("cm"), 0)
        row.addWidget(QLabel("高"), 0)
        row.addWidget(sp_d, 1)
        add_btn = QPushButton("+ 一级" if group is not None else "+ 追加一级")
        add_btn.setFixedWidth(72 if group is not None else 80)
        add_btn.setToolTip("追加一级阶梯，最多 3 级")
        add_btn.clicked.connect(lambda _=False, g=group: self.add_level(g))
        if group is None:
            row.addWidget(add_btn, 0)
        else:
            actions = group['actions']
            actions.insertWidget(actions.count() - (1 if 'remove' in group else 0), add_btn)
        buttons.append(add_btn)
        container = QWidget()
        container.setLayout(row)
        if level_idx > 0 and group is None:
            container.setStyleSheet(f"margin-left: {16 * level_idx}px;")
        layout.addWidget(container)
        rows.append((sp_r, sp_d, container))
        self.update_buttons(group)

    def add_level(self, group=None):
        rows = self._stair_rows if group is None else group['rows']
        if len(rows) < self._panel._stair_max_levels:
            self.add_level_row(group=group)
            self._panel._parameter_control.on_staircase_changed()

    def remove_level(self, group=None, *, notify=True):
        rows = self._stair_rows if group is None else group['rows']
        buttons = self._stair_add_btns if group is None else group['buttons']
        layout = self._stair_rows_container if group is None else group['layout']
        if len(rows) <= 1:
            return
        _r, _d, widget = rows.pop()
        layout.removeWidget(widget)
        widget.setParent(None)
        widget.deleteLater()
        button = buttons.pop()
        button.setParent(None)
        button.deleteLater()
        self.update_buttons(group)
        if notify:
            self._panel._parameter_control.on_staircase_changed()

    def update_buttons(self, group=None):
        rows = self._stair_rows if group is None else group['rows']
        buttons = self._stair_add_btns if group is None else group['buttons']
        n = len(rows)
        for i, button in enumerate(buttons):
            button.setVisible(i == n - 1 and n < self._panel._stair_max_levels)
        if group is not None and 'remove' in group:
            group['remove'].setEnabled(n > 1)

    def on_staircase_changed(self):
        """Compatibility entry; snapshot generation belongs to parameter control."""
        return self._panel._sync_staircase_snapshot()

    def on_param_changed(self, *args):
        return self._panel._on_param_changed_legacy(*args)

    def manual_cut_snapshot(self) -> tuple[list[dict], dict]:
        return self._panel._parameter_control.manual_cut_snapshot()

    def build_manual_params(self, outer_w_cm: float, outer_h_cm: float) -> dict:
        return self._panel._parameter_control.build_manual_params(outer_w_cm, outer_h_cm)

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
            enabled.toggled.connect(panel._parameter_control.on_standard_changed)
            combo.currentIndexChanged.connect(panel._parameter_control.on_standard_changed)
            width.valueChanged.connect(panel._parameter_control.on_standard_changed)
            height.valueChanged.connect(panel._parameter_control.on_standard_changed)
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
        if self._multicorner_staircase_mode:
            return 'multicorner_staircase'
        return "staircase" if self._panel._staircase_mode else "standard"

    def set_mode(self, mode: str) -> None:
        self.set_staircase_mode(mode in ('staircase', 'multicorner_staircase'),
                                multicorner=mode == 'multicorner_staircase')

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
        if self._multicorner_staircase_mode:
            return [rect for anchor, group in self._multi_stair_groups.items()
                    if group['box'].isChecked()
                    for rect in self._steps_to_rects(anchor, group['rows'])]
        anchor = panel._stair_corner.currentData() or 'tr'
        return self._steps_to_rects(anchor, panel._stair_rows)

    @staticmethod
    def _steps_to_rects(anchor, stair_rows):
        """单角与多角共用条带换算：宽为剩余步进之和，偏移为已走落差。"""
        rows = [(r_sp.value(), d_sp.value())
                for r_sp, d_sp, _ in stair_rows]
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

