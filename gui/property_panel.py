"""
gui/property_panel.py
右侧属性面板：修改 CropDesign 参数后，通知主窗口重新渲染。
"""
from __future__ import annotations
import logging
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel, QDoubleSpinBox,
    QSpinBox, QComboBox, QPushButton, QCheckBox, QFileDialog, QLineEdit,
    QColorDialog, QFrame, QScrollArea, QMessageBox,
)
from PyQt5.QtGui import QColor

from core.geometry import CropDesign, BorderLayer, BorderText

logger = logging.getLogger(__name__)


def _color_to_tuple(qc: QColor) -> tuple[int, int, int]:
    return (qc.red(), qc.green(), qc.blue())


def _tuple_to_color(t: tuple[int, int, int]) -> QColor:
    return QColor(*t)


class ColorButton(QPushButton):
    """点击选择颜色的小按钮，显示当前颜色色块"""

    changed = pyqtSignal(tuple)

    def __init__(self, init_color: tuple[int, int, int] = (0, 0, 0)):
        super().__init__()
        self.setFixedSize(40, 24)
        self._c = init_color
        self._update_style()
        self.clicked.connect(self._pick)

    def _update_style(self):
        r, g, b = self._c
        self.setStyleSheet(
            f"QPushButton {{ background-color: rgb({r},{g},{b});"
            f" border: 1px solid #888; border-radius: 3px; }}")

    def _pick(self):
        c = QColorDialog.getColor(_tuple_to_color(self._c), self, "选择颜色")
        if c.isValid():
            self._c = _color_to_tuple(c)
            self._update_style()
            self.changed.emit(self._c)

    def color(self) -> tuple[int, int, int]:
        return self._c

    def set_color(self, c: tuple[int, int, int]) -> None:
        self._c = c
        self._update_style()


class PropertyPanel(QWidget):
    """右侧属性面板"""

    design_changed = pyqtSignal(object)   # 发送更新后的 CropDesign
    save_requested = pyqtSignal()
    export_psd_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.design = CropDesign()
        self._build_ui()
        self._load_from_design()

    # ---- UI ----
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self._inner_layout = QVBoxLayout(inner)
        self._inner_layout.setSpacing(10)
        scroll.setWidget(inner)
        root.addWidget(scroll)

        # 1) 画布尺寸与 DPI
        gb1 = QGroupBox("画布尺寸 (厘米)")
        f = QVBoxLayout(gb1)
        self._sp_w = self._dspin(5, 200, self.design.canvas_w_cm, decimals=1)
        self._sp_h = self._dspin(5, 200, self.design.canvas_h_cm, decimals=1)
        self._sp_dpi = QSpinBox(); self._sp_dpi.setRange(72, 600); self._sp_dpi.setValue(self.design.dpi)
        f.addLayout(self._row("宽(cm)", self._sp_w))
        f.addLayout(self._row("高(cm)", self._sp_h))
        f.addLayout(self._row("DPI", self._sp_dpi))
        self._inner_layout.addWidget(gb1)

        # 2) 裁剪模式
        gb_mode = QGroupBox("裁剪模式")
        fm = QVBoxLayout(gb_mode)
        self._cb_mode = QComboBox()
        self._cb_mode.addItem("矩形嵌套挖洞 (图1/5)", "rect_hole")
        self._cb_mode.addItem("L形挖角 (图2/4)", "rect_lshape")
        self._cb_mode.addItem("椭圆挖洞 (图3)", "ellipse_hole")
        fm.addWidget(self._cb_mode)

        self._sp_outer_margin = self._dspin(0, 20, self.design.outer_margin_cm)
        fm.addLayout(self._row("外框留白(cm)", self._sp_outer_margin))
        self._inner_layout.addWidget(gb_mode)

        # 3) 内挖参数（矩形/L形共用）
        gb_inner = QGroupBox("内挖边距 (厘米)")
        fi = QVBoxLayout(gb_inner)
        self._sp_mt = self._dspin(0, 50, self.design.inner_margin_top_cm)
        self._sp_mb = self._dspin(0, 50, self.design.inner_margin_bottom_cm)
        self._sp_ml = self._dspin(0, 50, self.design.inner_margin_left_cm)
        self._sp_mr = self._dspin(0, 50, self.design.inner_margin_right_cm)
        fi.addLayout(self._row("上", self._sp_mt))
        fi.addLayout(self._row("下", self._sp_mb))
        fi.addLayout(self._row("左", self._sp_ml))
        fi.addLayout(self._row("右", self._sp_mr))
        self._inner_layout.addWidget(gb_inner)

        # 4) L 形参数
        self._gb_l = QGroupBox("L形挖角参数")
        fl = QVBoxLayout(self._gb_l)
        self._cb_lcorner = QComboBox()
        self._cb_lcorner.addItem("左上角", "tl"); self._cb_lcorner.addItem("右上角", "tr")
        self._cb_lcorner.addItem("左下角", "bl"); self._cb_lcorner.addItem("右下角", "br")
        self._sp_lw = self._dspin(0, 50, self.design.l_cut_w_cm)
        self._sp_lh = self._dspin(0, 50, self.design.l_cut_h_cm)
        fl.addLayout(self._row("挖角位置", self._cb_lcorner))
        fl.addLayout(self._row("挖角宽度(cm)", self._sp_lw))
        fl.addLayout(self._row("挖角高度(cm)", self._sp_lh))
        self._inner_layout.addWidget(self._gb_l)

        # 4.5) 圆角设置
        self._gb_corner = QGroupBox("圆角设置（厘米）")
        fc = QVBoxLayout(self._gb_corner)
        grid_corner = QGridLayout()
        self._sp_design_corners = {}
        corner_labels = [('tl', '左上角'), ('tr', '右上角'), ('bl', '左下角'), ('br', '右下角')]
        for i, (key, name) in enumerate(corner_labels):
            grid_corner.addWidget(QLabel(name), i // 2, (i % 2) * 3)
            sp = QDoubleSpinBox(); sp.setRange(0, 50); sp.setValue(getattr(self.design, f'corner_{key}_cm', 0)); sp.setDecimals(1); sp.setSuffix(" cm")
            grid_corner.addWidget(sp, i // 2, (i % 2) * 3 + 1)
            self._sp_design_corners[key] = sp
        fc.addLayout(grid_corner)
        self._inner_layout.addWidget(self._gb_corner)

        # 5) 椭圆参数
        self._gb_e = QGroupBox("椭圆参数")
        fe = QVBoxLayout(self._gb_e)
        self._sp_erx = self._dspin(0.05, 0.49, self.design.ellipse_rx_ratio, decimals=2)
        self._sp_ery = self._dspin(0.05, 0.49, self.design.ellipse_ry_ratio, decimals=2)
        fe.addLayout(self._row("X半径/画布宽", self._sp_erx))
        fe.addLayout(self._row("Y半径/画布高", self._sp_ery))
        self._inner_layout.addWidget(self._gb_e)

        # 6) 边框层
        gb_b = QGroupBox("多层边框")
        fb = QVBoxLayout(gb_b)
        self._layers_label = QLabel()
        fb.addWidget(self._layers_label)
        row_l = QHBoxLayout()
        btn_add_layer = QPushButton("+ 加一层")
        btn_del_layer = QPushButton("- 删一层")
        self._btn_edit_layers = QPushButton("编辑层…")
        row_l.addWidget(btn_add_layer); row_l.addWidget(btn_del_layer); row_l.addWidget(self._btn_edit_layers)
        fb.addLayout(row_l)
        self._inner_layout.addWidget(gb_b)

        # 7) 背景色 / 素材
        gb_bg = QGroupBox("背景设置")
        fbg = QVBoxLayout(gb_bg)
        row_ob = QHBoxLayout(); self._btn_outer_color = ColorButton(self.design.outer_bg_color)
        self._ed_outer_img = QLineEdit(); self._ed_outer_img.setPlaceholderText("外框素材JPG（可选）")
        btn_op1 = QPushButton("…"); btn_op1.setFixedWidth(30)
        row_ob.addWidget(QLabel("外")); row_ob.addWidget(self._btn_outer_color)
        row_ob.addWidget(self._ed_outer_img, 1); row_ob.addWidget(btn_op1)
        fbg.addLayout(row_ob)

        row_hb = QHBoxLayout(); self._btn_hole_color = ColorButton(self.design.hole_bg_color)
        self._ed_hole_img = QLineEdit(); self._ed_hole_img.setPlaceholderText("内部填充素材JPG（可选）")
        btn_op2 = QPushButton("…"); btn_op2.setFixedWidth(30)
        row_hb.addWidget(QLabel("内")); row_hb.addWidget(self._btn_hole_color)
        row_hb.addWidget(self._ed_hole_img, 1); row_hb.addWidget(btn_op2)
        fbg.addLayout(row_hb)
        self._inner_layout.addWidget(gb_bg)

        # 8) 边框文字
        self._gb_txt = QGroupBox("边框环绕文字")
        self._gb_txt.setCheckable(True); self._gb_txt.setChecked(False)
        ft = QVBoxLayout(self._gb_txt)
        self._ed_txt = QLineEdit("Cross the stars over the moon to meet your better self.")
        self._sp_fs = QSpinBox(); self._sp_fs.setRange(8, 200); self._sp_fs.setValue(30)
        self._btn_txt_color = ColorButton((0, 0, 0))
        self._ck_mirror = QCheckBox("底部文字镜像翻转（图1样式）"); self._ck_mirror.setChecked(True)
        ft.addLayout(self._row("文字", self._ed_txt))
        ft.addLayout(self._row("字号(px)", self._sp_fs))
        ft.addLayout(self._row("颜色", self._btn_txt_color))
        ft.addWidget(self._ck_mirror)
        self._inner_layout.addWidget(self._gb_txt)

        # 9) PSD 批量导入
        gb_psd = QGroupBox("PSD素材导入")
        fp = QVBoxLayout(gb_psd)
        self._ed_psd = QLineEdit(); self._ed_psd.setPlaceholderText("PSD文件…")
        row_psd = QHBoxLayout()
        btn_psd = QPushButton("选择PSD"); btn_psd2 = QPushButton("导出为JPG素材")
        row_psd.addWidget(self._ed_psd, 1); row_psd.addWidget(btn_psd); row_psd.addWidget(btn_psd2)
        fp.addLayout(row_psd)
        self._psd_info = QLabel("（将PSD中每个可见图层导出为独立JPG，自动裁掉透明边）")
        self._psd_info.setWordWrap(True); self._psd_info.setStyleSheet("color:#666;")
        fp.addWidget(self._psd_info)
        self._inner_layout.addWidget(gb_psd)

        # 10) 底部按钮
        row_btns = QHBoxLayout()
        self._btn_apply = QPushButton("生成预览")
        self._btn_apply.setStyleSheet("padding:8px 12px; font-weight:bold;")
        self._btn_save = QPushButton("导出 JPG")
        self._btn_save.setStyleSheet("padding:8px 12px;")
        row_btns.addWidget(self._btn_apply, 1); row_btns.addWidget(self._btn_save, 1)
        root.addLayout(row_btns)

        # 连接信号
        self._cb_mode.currentIndexChanged.connect(self._on_mode_change)
        btn_add_layer.clicked.connect(self._add_layer)
        btn_del_layer.clicked.connect(self._del_layer)
        self._btn_edit_layers.clicked.connect(self._edit_layers)
        self._btn_outer_color.changed.connect(lambda c: self._apply_quiet())
        self._btn_hole_color.changed.connect(lambda c: self._apply_quiet())
        btn_op1.clicked.connect(lambda: self._pick_file(self._ed_outer_img, "JPG/PSD 素材 (*.jpg *.jpeg *.psd)"))
        btn_op2.clicked.connect(lambda: self._pick_file(self._ed_hole_img, "JPG/PSD 素材 (*.jpg *.jpeg *.psd)"))
        btn_psd.clicked.connect(lambda: self._pick_file(self._ed_psd, "PSD 文件 (*.psd *.psb)"))
        btn_psd2.clicked.connect(self._export_psd_layers)
        self._btn_apply.clicked.connect(self.apply)
        self._btn_save.clicked.connect(self.save_requested.emit)

        self._on_mode_change()

    def _dspin(self, mn, mx, val, decimals=2):
        s = QDoubleSpinBox(); s.setRange(mn, mx); s.setValue(val); s.setDecimals(decimals); s.setSingleStep(0.5)
        return s

    def _row(self, label: str, widget: QWidget) -> QHBoxLayout:
        lay = QHBoxLayout(); lay.addWidget(QLabel(label), 0); lay.addWidget(widget, 1)
        return lay

    def _pick_file(self, target_edit: QLineEdit, filt: str):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", filt)
        if path:
            target_edit.setText(path)

    # ---- 模式切换显示/隐藏 L 形 / 椭圆 ----
    def _on_mode_change(self):
        mode = self._cb_mode.currentData()
        self._gb_l.setVisible(mode == 'rect_lshape')
        self._gb_e.setVisible(mode == 'ellipse_hole')

    # ---- 边框层增删改 ----
    def _update_layers_label(self):
        names = []
        for i, l in enumerate(self.design.borders):
            if l.fill_type == 'solid':
                names.append(f"#{i+1} {l.offset_cm:.1f}cm 纯色")
            elif l.fill_type == 'image' and l.image_path:
                names.append(f"#{i+1} {l.offset_cm:.1f}cm 图")
            else:
                names.append(f"#{i+1} {l.offset_cm:.1f}cm")
        self._layers_label.setText(" / ".join(names) if names else "（空）")

    def _add_layer(self):
        self.design.borders.append(BorderLayer(offset_cm=0.2, fill_type='solid', color=(255, 255, 255)))
        self._update_layers_label()
        self._apply_quiet()

    def _del_layer(self):
        if len(self.design.borders) > 1:
            self.design.borders.pop()
            self._update_layers_label()
            self._apply_quiet()

    def _edit_layers(self):
        dlg = _LayersDialog(self.design.borders, self)
        if dlg.exec_():
            self.design.borders = dlg.result_layers
            self._update_layers_label()
            self._apply_quiet()

    # ---- 把面板值同步到 self.design（不触发预览） ----
    def _collect(self):
        d = self.design
        d.canvas_w_cm = self._sp_w.value()
        d.canvas_h_cm = self._sp_h.value()
        d.dpi = self._sp_dpi.value()
        d.mode = self._cb_mode.currentData()
        d.outer_margin_cm = self._sp_outer_margin.value()
        d.inner_margin_top_cm = self._sp_mt.value()
        d.inner_margin_bottom_cm = self._sp_mb.value()
        d.inner_margin_left_cm = self._sp_ml.value()
        d.inner_margin_right_cm = self._sp_mr.value()
        d.l_corner = self._cb_lcorner.currentData()
        d.l_cut_w_cm = self._sp_lw.value()
        d.l_cut_h_cm = self._sp_lh.value()
        # 圆角设置
        d.corner_tl_cm = self._sp_design_corners['tl'].value()
        d.corner_tr_cm = self._sp_design_corners['tr'].value()
        d.corner_bl_cm = self._sp_design_corners['bl'].value()
        d.corner_br_cm = self._sp_design_corners['br'].value()
        d.ellipse_rx_ratio = self._sp_erx.value()
        d.ellipse_ry_ratio = self._sp_ery.value()
        d.outer_bg_color = self._btn_outer_color.color()
        d.hole_bg_color = self._btn_hole_color.color()
        d.outer_bg_image = self._ed_outer_img.text().strip() or None
        d.hole_bg_image = self._ed_hole_img.text().strip() or None
        # 文字
        if self._gb_txt.isChecked():
            bt = d.border_text or BorderText()
            bt.text = self._ed_txt.text()
            bt.font_size_px = self._sp_fs.value()
            bt.color = self._btn_txt_color.color()
            bt.mirror_bottom = self._ck_mirror.isChecked()
            d.border_text = bt
        else:
            d.border_text = None

    def _apply_quiet(self):
        """属性变动时：静默触发预览，按钮统一 apply 也会调用"""
        self._collect()
        self._update_layers_label()
        self.design_changed.emit(self.design)

    def apply(self):
        self._apply_quiet()

    def _load_from_design(self):
        self._update_layers_label()

    # ---- PSD 导出 ----
    def _export_psd_layers(self):
        psd = self._ed_psd.text().strip()
        if not psd:
            QMessageBox.information(self, "提示", "请先选择 PSD 文件")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not out_dir:
            return
        try:
            from core.psd_loader import export_psd_layers_as_jpgs
            paths = export_psd_layers_as_jpgs(psd, out_dir, auto_crop=True)
            QMessageBox.information(self, "导出完成",
                                    f"成功导出 {len(paths)} 个图层到:\n{out_dir}")
            self.export_psd_requested.emit(out_dir)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))


# ---- 边框层编辑对话框 ----
from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView


class _LayersDialog(QDialog):
    def __init__(self, layers: list[BorderLayer], parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑边框层")
        self.resize(560, 360)
        self.result_layers = [BorderLayer(l.offset_cm, 0, l.fill_type, l.color, l.image_path, l.tile_mode) for l in layers]

        v = QVBoxLayout(self)
        self.tbl = QTableWidget(len(self.result_layers), 5)
        self.tbl.setHorizontalHeaderLabels(["厚度(cm)", "填充", "颜色", "素材图", "平铺"])
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        for i, l in enumerate(self.result_layers):
            self._set_row(i, l)
        v.addWidget(self.tbl)

        row_btn = QHBoxLayout()
        b_add = QPushButton("+ 插入"); b_del = QPushButton("- 删除"); b_up = QPushButton("上移"); b_dn = QPushButton("下移")
        row_btn.addWidget(b_add); row_btn.addWidget(b_del); row_btn.addWidget(b_up); row_btn.addWidget(b_dn); row_btn.addStretch(1)
        v.addLayout(row_btn)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self.tbl.cellDoubleClicked.connect(self._on_cell)
        b_add.clicked.connect(self._add); b_del.clicked.connect(self._del)
        b_up.clicked.connect(lambda: self._move(-1)); b_dn.clicked.connect(lambda: self._move(1))

    def _set_row(self, i, l: BorderLayer):
        self.tbl.setItem(i, 0, QTableWidgetItem(f"{l.offset_cm:.2f}"))
        self.tbl.setItem(i, 1, QTableWidgetItem("纯色" if l.fill_type == 'solid' else "图片"))
        r, g, b = l.color
        self.tbl.setItem(i, 2, QTableWidgetItem(f"#{r:02X}{g:02X}{b:02X}"))
        self.tbl.setItem(i, 3, QTableWidgetItem(l.image_path or ""))
        self.tbl.setItem(i, 4, QTableWidgetItem("是" if l.tile_mode else "否"))

    def _read_row(self, i):
        try:
            self.result_layers[i].offset_cm = float(self.tbl.item(i, 0).text())
            self.result_layers[i].fill_type = 'solid' if self.tbl.item(i, 1).text() == '纯色' else 'image'
            self.result_layers[i].image_path = self.tbl.item(i, 3).text().strip() or None
            self.result_layers[i].tile_mode = self.tbl.item(i, 4).text() == '是'
        except Exception as e:
            logger.warning(f"边框层表格第 {i} 行读取失败: {e}")

    def _on_cell(self, row, col):
        l = self.result_layers[row]
        if col == 0:
            # 厚度
            from PyQt5.QtWidgets import QInputDialog
            v, ok = QInputDialog.getDouble(self, "厚度(cm)", "cm", l.offset_cm, 0.01, 20, 2)
            if ok:
                l.offset_cm = v; self._set_row(row, l)
        elif col == 1:
            l.fill_type = 'image' if l.fill_type == 'solid' else 'solid'
            self._set_row(row, l)
        elif col == 2:
            c = QColorDialog.getColor(_tuple_to_color(l.color), self, "颜色")
            if c.isValid():
                l.color = _color_to_tuple(c); self._set_row(row, l)
        elif col == 3:
            p, _ = QFileDialog.getOpenFileName(self, "素材图", "", "JPG/PSD (*.jpg *.jpeg *.psd)")
            if p:
                l.image_path = p; self._set_row(row, l)
        elif col == 4:
            l.tile_mode = not l.tile_mode; self._set_row(row, l)

    def _add(self):
        self.result_layers.append(BorderLayer(offset_cm=0.2))
        self.tbl.insertRow(self.tbl.rowCount())
        self._set_row(self.tbl.rowCount() - 1, self.result_layers[-1])

    def _del(self):
        r = self.tbl.currentRow()
        if 0 <= r < len(self.result_layers) and len(self.result_layers) > 1:
            self.result_layers.pop(r); self.tbl.removeRow(r)

    def _move(self, d):
        r = self.tbl.currentRow()
        t = r + d
        if 0 <= r < len(self.result_layers) and 0 <= t < len(self.result_layers):
            self.result_layers[r], self.result_layers[t] = self.result_layers[t], self.result_layers[r]
            self._set_row(r, self.result_layers[r]); self._set_row(t, self.result_layers[t])
            self.tbl.selectRow(t)

    def accept(self):
        # 提交所有行当前的文本输入
        for i in range(len(self.result_layers)):
            self._read_row(i)
        super().accept()
