"""
gui/property_panel.py
右侧属性面板：修改 CropDesign 参数后，通知主窗口重新渲染。
"""
from __future__ import annotations
import logging
import os
from datetime import date, datetime, timedelta
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QSize
from PyQt5.QtGui import QColor, QPixmap
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel, QDoubleSpinBox,
    QSpinBox, QComboBox, QPushButton, QCheckBox, QFileDialog, QLineEdit,
    QColorDialog, QFrame, QScrollArea, QMessageBox, QProgressDialog,
    QToolButton, QMenu, QAction, QDialog, QApplication,
)
from PyQt5.QtGui import QColor
from PyQt5.QtCore import QMimeData  # noqa: E402  (拖拽支持)
from PIL import Image

from core.geometry import CropDesign, BorderLayer, BorderText
from core.parser.name_parser import parse_filename
from core.parser.template_matcher import TemplateMatcher
from core.app_settings import get_app_settings
from core.pool_designer import validate_sketch_file
from core.pool_designer.sketch_parser import _SKETCH_ACCEPT_EXT, get_tesseract_status

logger = logging.getLogger(__name__)

from .property_panel_widgets import (ColorButton, _SketchDropLabel, _color_to_tuple, _tuple_to_color)
from .property_panel_workers import PoolRenderWorker, _SketchParseWorker
from .property_panel_dialogs import _LayersDialog, _SketchViewerDialog
from .property_panel_poolbox import _PoolBoxMixin
from .property_panel_generate import _GenerateMixin
from .property_panel_layers import _LayersMixin

class PropertyPanel(_LayersMixin, _GenerateMixin, _PoolBoxMixin, QWidget):
    """右侧属性面板"""

    design_changed = pyqtSignal(object)   # 发送更新后的 CropDesign
    save_requested = pyqtSignal()
    export_psd_requested = pyqtSignal(str)
    sketch_loaded = pyqtSignal(object)    # 草图上传后发出 PIL Image（None 表示清除），主窗口用于在主画布显示

    def get_output_filename(self) -> str:
        """返回用于导出 JPG 的建议文件名（不含扩展名），水池模式优先用输出文件名框"""
        # 水池模式输出文件名
        pool_name = getattr(self, '_pool_output_name', None)
        if pool_name is not None:
            s = pool_name.text().strip()
            if s:
                # 去掉扩展名（用户可能手动填了 .jpg）
                base, ext = os.path.splitext(s)
                if ext.lower() in {'.jpg', '.jpeg', '.png'}:
                    return base
                return s
        # 回退：尺寸命名
        return f"SmartShapeCrop_{int(self.design.canvas_w_cm)}x{int(self.design.canvas_h_cm)}cm"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.design = CropDesign()
        # —— 智能水池：共享的 TemplateMatcher（独立缓存，不影响圆角裁剪工具）——
        self._matcher = TemplateMatcher()
        self._matcher.set_log_callback(lambda m: logger.info(f"[PoolMatcher] {m}"))
        self._pool_worker = None  # type: PoolRenderWorker | None
        self._sketch_parse_worker = None  # type: _SketchParseWorker | None
        self._lshape_parse_worker = None  # type: _LShapeParseWorker | None
        self._sketch_path = ""
        self._sketch_parse_result = None  # 草图解析缓存，供实时回填 UI
        self._lshape_params = None  # L 形挖角参数（确认后写入：corner/cut_w_cm/cut_h_cm/outer_w_cm/outer_h_cm）
        # —— 持久化设置（与圆角裁剪工具共用同一份 QSettings）——
        self._app_settings = get_app_settings()
        self._build_ui()
        self._load_from_design()
        self._pool_restore_last_template_dir()
        self._refresh_target_history_ui()

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

        # 0) 智能水池模式（一键：匹配模板 + 解析草图 + 生成预览）
        self._build_pool_box()

        # 1) + 2) 画布尺寸 与 裁剪模式 同行排列
        row_size_mode = QHBoxLayout()
        gb1 = QGroupBox("画布尺寸 (厘米)")
        f = QVBoxLayout(gb1)
        self._sp_w = self._dspin(5, 500, self.design.canvas_w_cm, decimals=1)
        self._sp_h = self._dspin(5, 500, self.design.canvas_h_cm, decimals=1)
        self._sp_dpi = QSpinBox(); self._sp_dpi.setRange(72, 600); self._sp_dpi.setValue(self.design.dpi)
        f.addLayout(self._row("宽(cm)", self._sp_w))
        f.addLayout(self._row("高(cm)", self._sp_h))
        f.addLayout(self._row("DPI", self._sp_dpi))

        gb_mode = QGroupBox("裁剪模式")
        fm = QVBoxLayout(gb_mode)
        self._cb_mode = QComboBox()
        self._cb_mode.addItem("矩形嵌套挖洞", "rect_hole")
        self._cb_mode.addItem("L形挖角", "rect_lshape")
        self._cb_mode.addItem("椭圆挖洞", "ellipse_hole")
        fm.addWidget(self._cb_mode)

        self._sp_outer_margin = self._dspin(0, 20, self.design.outer_margin_cm)
        fm.addLayout(self._row("外框留白(cm)", self._sp_outer_margin))

        row_size_mode.addWidget(gb1)
        row_size_mode.addWidget(gb_mode)
        self._inner_layout.addLayout(row_size_mode)

        # 3) 内挖边距 与 圆角设置 同行排列
        row_inner_corner = QHBoxLayout()

        gb_inner = QGroupBox("内挖边距 (厘米)")
        fi = QVBoxLayout(gb_inner)
        self._sp_mt = self._dspin(0, 450, self.design.inner_margin_top_cm)
        self._sp_mb = self._dspin(0, 450, self.design.inner_margin_bottom_cm)
        self._sp_ml = self._dspin(0, 450, self.design.inner_margin_left_cm)
        self._sp_mr = self._dspin(0, 450, self.design.inner_margin_right_cm)
        fi.addLayout(self._row("上", self._sp_mt))
        fi.addLayout(self._row("下", self._sp_mb))
        fi.addLayout(self._row("左", self._sp_ml))
        fi.addLayout(self._row("右", self._sp_mr))

        self._gb_corner = QGroupBox("圆角设置（厘米）")
        fc = QVBoxLayout(self._gb_corner)
        grid_corner = QGridLayout()
        self._sp_design_corners = {}
        corner_labels = [('tl', '左上角'), ('tr', '右上角'), ('bl', '左下角'), ('br', '右下角')]
        for i, (key, name) in enumerate(corner_labels):
            grid_corner.addWidget(QLabel(name), i // 2, (i % 2) * 2)
            sp = QDoubleSpinBox(); sp.setRange(0, 50); sp.setValue(getattr(self.design, f'corner_{key}_cm', 0)); sp.setDecimals(1); sp.setSuffix(" cm")
            sp.setFixedWidth(100)
            grid_corner.addWidget(sp, i // 2, (i % 2) * 2 + 1)
            self._sp_design_corners[key] = sp
        fc.addLayout(grid_corner)

        row_inner_corner.addWidget(gb_inner)
        row_inner_corner.addWidget(self._gb_corner)
        self._inner_layout.addLayout(row_inner_corner)

        # 4) L 形参数
        self._gb_l = QGroupBox("L形挖角参数")
        fl = QVBoxLayout(self._gb_l)
        self._cb_lcorner = QComboBox()
        self._cb_lcorner.addItem("左上角", "tl"); self._cb_lcorner.addItem("右上角", "tr")
        self._cb_lcorner.addItem("左下角", "bl"); self._cb_lcorner.addItem("右下角", "br")
        # [Fix 2026-08-28] 挖角尺寸范围放宽到 0-450cm（大尺寸 L 形挖角，如 33x450cm 图挖 100cm 角）
        self._sp_lw = self._dspin(0, 450, self.design.l_cut_w_cm)
        self._sp_lh = self._dspin(0, 450, self.design.l_cut_h_cm)
        fl.addLayout(self._row("挖角位置", self._cb_lcorner))
        fl.addLayout(self._row("挖角宽度(cm)", self._sp_lw))
        fl.addLayout(self._row("挖角高度(cm)", self._sp_lh))
        self._inner_layout.addWidget(self._gb_l)

        # 5) 椭圆参数
        self._gb_e = QGroupBox("椭圆参数")
        fe = QVBoxLayout(self._gb_e)
        self._sp_erx = self._dspin(0.05, 0.49, self.design.ellipse_rx_ratio, decimals=2)
        self._sp_ery = self._dspin(0.05, 0.49, self.design.ellipse_ry_ratio, decimals=2)
        fe.addLayout(self._row("X半径/画布宽", self._sp_erx))
        fe.addLayout(self._row("Y半径/画布高", self._sp_ery))
        self._inner_layout.addWidget(self._gb_e)

        # 6) 边框层
        gb_b = QGroupBox("多层边框", self)  # 设parent防止GC删除子控件
        fb = QVBoxLayout(gb_b)
        self._layers_label = QLabel()
        fb.addWidget(self._layers_label)
        row_l = QHBoxLayout()
        btn_add_layer = QPushButton("+ 加一层")
        btn_del_layer = QPushButton("- 删一层")
        self._btn_edit_layers = QPushButton("编辑层…")
        row_l.addWidget(btn_add_layer); row_l.addWidget(btn_del_layer); row_l.addWidget(self._btn_edit_layers)
        fb.addLayout(row_l)
        self._gb_hidden_layers = gb_b  # 保持引用，防止被GC
        gb_b.hide()  # 显式隐藏，避免父widget样式导致边框/文字残留
        # self._inner_layout.addWidget(gb_b)  # 多层边框 - 智能匹配后不再需要

        # 7) 背景色 / 素材
        gb_bg = QGroupBox("背景设置", self)  # 设parent防止GC删除子控件
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
        self._gb_hidden_bg = gb_bg  # 保持引用，防止被GC
        gb_bg.hide()  # 显式隐藏，避免父widget样式导致边框/文字残留
        # self._inner_layout.addWidget(gb_bg)  # 背景设置 - 智能匹配后不再需要

        # 8) 边框文字
        self._gb_txt = QGroupBox("边框环绕文字", self)  # 补parent
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
        self._gb_txt.hide()  # 显式隐藏，避免父widget样式导致边框/文字残留
        # self._inner_layout.addWidget(self._gb_txt)  # 边框环绕文字 - 智能匹配后不再需要

        # 9) PSD 批量导入
        gb_psd = QGroupBox("PSD素材导入", self)  # 设parent防止GC删除子控件
        fp = QVBoxLayout(gb_psd)
        self._ed_psd = QLineEdit(); self._ed_psd.setPlaceholderText("PSD文件…")
        row_psd = QHBoxLayout()
        btn_psd = QPushButton("选择PSD"); btn_psd2 = QPushButton("导出为JPG素材")
        row_psd.addWidget(self._ed_psd, 1); row_psd.addWidget(btn_psd); row_psd.addWidget(btn_psd2)
        fp.addLayout(row_psd)
        self._psd_info = QLabel("（将PSD中每个可见图层导出为独立JPG，自动裁掉透明边）")
        self._psd_info.setWordWrap(True); self._psd_info.setStyleSheet("color:#666;")
        fp.addWidget(self._psd_info)
        self._gb_hidden_psd = gb_psd  # 保持引用，防止被GC
        gb_psd.hide()  # 显式隐藏，避免父widget样式导致边框/文字残留
        # self._inner_layout.addWidget(gb_psd)  # PSD素材导入 - 智能匹配后不再需要

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
        # 以下信号对应的UI区块已隐藏（智能匹配后自动处理）
        # btn_add_layer.clicked.connect(self._add_layer)
        # btn_del_layer.clicked.connect(self._del_layer)
        # self._btn_edit_layers.clicked.connect(self._edit_layers)
        # self._btn_outer_color.changed.connect(lambda c: self._apply_quiet())
        # self._btn_hole_color.changed.connect(lambda c: self._apply_quiet())
        # btn_op1.clicked.connect(lambda: self._pick_file(self._ed_outer_img, "JPG/PSD 素材 (*.jpg *.jpeg *.psd)"))
        # btn_op2.clicked.connect(lambda: self._pick_file(self._ed_hole_img, "JPG/PSD 素材 (*.jpg *.jpeg *.psd)"))
        # btn_psd.clicked.connect(lambda: self._pick_file(self._ed_psd, "PSD 文件 (*.psd *.psb)"))
        # btn_psd2.clicked.connect(self._export_psd_layers)
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

    # ---- 智能水池模式 UI ----

    # -------------- 智能水池：事件处理 --------------





    # ================================================================
    # 模板库目录：持久化 / 历史记录（与圆角裁剪工具共用 AppSettings）
    # ================================================================






    # ================================================================
    # 目标文件名历史记录（按日分组，保留 3 天）
    # ================================================================
















    @staticmethod
    def _safe_dir_val2(raw):
        """安全解析方向标注值，支持多种数据格式。"""
        try:
            if raw is None:
                return 0.0
            if isinstance(raw, (int, float)):
                return float(raw)
            if isinstance(raw, str):
                return float(raw.strip())
            if isinstance(raw, (tuple, list)) and len(raw) > 0:
                v = raw[0]
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, str):
                    return float(v.strip())
        except (ValueError, TypeError, IndexError):
            pass
        return 0.0

    def _set_pool_status(self, msg: str, is_error: bool = False):
        color = "#B00020" if is_error else "#388E3C"
        self._pool_status.setText(msg)
        self._pool_status.setStyleSheet(
            f"color:{color}; padding:4px 6px; background: {'#FFEBEE' if is_error else '#E8F5E9'};"
            " border-radius: 4px;")





    # ---- 模式切换显示/隐藏 L 形 / 椭圆 ----
    def _on_mode_change(self):
        mode = self._cb_mode.currentData()
        self._gb_l.setVisible(mode == 'rect_lshape')
        self._gb_e.setVisible(mode == 'ellipse_hole')

    # ---- 边框层增删改 ----




    # ---- 把面板值同步到 self.design（不触发预览） ----




    # ---- PSD 导出 ----
