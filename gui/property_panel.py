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
    QSpinBox, QComboBox, QPushButton, QCheckBox, QFileDialog, QLineEdit, QFormLayout,
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
        self._sketch_path = ""
        self._sketch_parse_result = None  # 草图解析缓存，供实时回填 UI
        # ===== [L-Shape Panel Refactor 2026-09-02] L 形挖角逻辑已迁移到 LShapePanel =====
        # _lshape_parse_worker 与 _lshape_params 现由 LShapePanel 持有；PropertyPanel 通过
        # self._lshape_panel 间接访问。保留 _lshape_panel 引用以便 _collect / _detect_* 等链路读取。
        self._lshape_panel = None  # type: 'LShapePanel | None' —— 由 main.py 通过 set_lshape_panel 注入
        # ===== [MULTI-HOLE Add-On 2026-08-29] 激活洞数（用于 _collect 限定写回范围） =====
        # 默认 0：单洞模式，_collect 多洞分支不会写回任何数据；
        # 多洞模式下由 _fill_multi_hole_ui/_hide_multi_hole_ui 更新为 2..MAX_MH_UI_HOLES。
        # 预分配 8 个 SpinBox 只是 UI 占位，绝不可以全部写回 design！
        self._mh_active_count = 0
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
        # [Fix 2026-09-02] 边距 SpinBox 改值即时预览（纯 Canvas 重渲染，不跑 PoolWorker）
        # 之前用户改边距后必须手动点大按钮或底部小按钮，体验不佳且容易漏触发生效。
        # valueChanged → _apply_quiet → _collect + design_changed → Canvas 重渲染。
        self._sp_mt.valueChanged.connect(self._apply_quiet)
        self._sp_mb.valueChanged.connect(self._apply_quiet)
        self._sp_ml.valueChanged.connect(self._apply_quiet)
        self._sp_mr.valueChanged.connect(self._apply_quiet)
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

        # 3b) ===== [MULTI-HOLE Add-On 2026-08-29] 多洞参数区（默认隐藏）=====
        # 单洞模式：永远隐藏 → 视觉和行为零影响；
        # 多洞模式：Worker 完成后按 design.pool_is_multi_hole 显示。
        # 洞 1~N 尺寸 + N-1 洞间距的 SpinBox。纯加 GroupBox，不改动 gb_inner 任何行。
        self._gb_multihole = QGroupBox("多洞参数（厘米）")
        self._gb_multihole.setObjectName("gb_multihole")
        fm = QFormLayout(self._gb_multihole)
        fm.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        # 标题标签（初始占位，回填 sketch_result 时再写入具体洞数）
        self._mh_title_label = QLabel("洞数量：0（仅多洞模式生效）")
        self._mh_title_label.setStyleSheet("font-weight:bold; color:#2a6;")
        fm.addRow(self._mh_title_label)

        # 初始化最大洞数=8（常规产品足够；支持更多洞时可 expand 动态增行）
        self._MAX_MH_UI_HOLES = 8
        self._mh_sp_hole_w = []   # list[QDoubleSpinBox]
        self._mh_sp_hole_h = []   # list[QDoubleSpinBox]
        self._mh_sp_gaps = []     # list[QDoubleSpinBox] len = N-1
        self._mh_rows_widgets = []  # list[(QLabel, QWidget)] 用于显隐控制

        for idx in range(self._MAX_MH_UI_HOLES):
            i = idx + 1
            spw = self._dspin(0, 1000, 0.0); spw.setSuffix(" cm")
            sph = self._dspin(0, 1000, 0.0); sph.setSuffix(" cm")
            row_w = QHBoxLayout(); row_w.addWidget(QLabel("宽"), 0); row_w.addWidget(spw, 1)
            row_h = QHBoxLayout(); row_h.addWidget(QLabel("高"), 0); row_h.addWidget(sph, 1)
            cols = QVBoxLayout(); cols.addLayout(row_w); cols.addLayout(row_h)
            wrap = QWidget(); wrap.setLayout(cols)
            lab = QLabel(f"洞{i}")
            fm.addRow(lab, wrap)
            self._mh_sp_hole_w.append(spw)
            self._mh_sp_hole_h.append(sph)
            self._mh_rows_widgets.append((lab, wrap))

            # 间距：N 个洞 → N-1 个间距。最后一个洞之后不加
            if idx < self._MAX_MH_UI_HOLES - 1:
                spg = self._dspin(0, 1000, 0.0); spg.setSuffix(" cm")
                lab_gap = QLabel(f"间距{i}↔{i+1}")
                fm.addRow(lab_gap, spg)
                self._mh_sp_gaps.append(spg)
                self._mh_rows_widgets.append((lab_gap, spg))

        # 默认所有洞/间距都隐藏；回填时根据真实洞数 show 对应行
        self._set_multi_hole_row_visibility(0)
        self._gb_multihole.hide()   # 启动时默认隐藏（单洞模式）
        self._inner_layout.addWidget(self._gb_multihole)

        # 4) L 形参数 —— 已迁移到独立的 LShapePanel（gui/lshape_panel.py）
        # 原 _gb_l / _cb_lcorner / _sp_lw / _sp_lh 由 LShapePanel 承载；
        # PropertyPanel 通过 self._lshape_panel 间接访问（_collect / _sync_panel_from_design）。
        # _on_mode_change 不再需要切换 _gb_l 可见性（LShapePanel 作为独立 tab 始终可见）。

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

    # ==================== LShapePanel 桥接（迁移自原 L 形挖角逻辑） ====================
    # L 形挖角的 UI 与识别逻辑已迁移到独立的 LShapePanel（gui/lshape_panel.py）。
    # PropertyPanel 通过本节方法与 LShapePanel 双向通信，保持原功能逻辑不变：
    #   - set_lshape_panel: main.py 注入 LShapePanel 引用；
    #   - _on_lshape_params_changed: 用户改挖角参数 → 触发即时预览（替代原 _apply_quiet）；
    #   - _on_lshape_applied: 用户确认 L 形挖角 → 切换模式 + 更新画布 + 触发预览
    #     （迁移自原 _apply_lshape_params 中"切换模式/更新画布"部分，参数回填由 LShapePanel 自行完成）；
    #   - _on_lshape_recognize_started: 用户点"识别 L 形挖角" → 把草图路径/目标尺寸注入 LShapePanel；
    #   - _get_lshape_params: PoolRenderWorker / _detect_user_margin_edits 读取 L 形参数（替代原 self._lshape_params）。
    def set_lshape_panel(self, panel) -> None:
        """main.py 在创建两个面板后调用，注入 LShapePanel 引用并连接全部双向同步信号。"""
        self._lshape_panel = panel

        # —— L 形挖角原有信号（LShapePanel → PropertyPanel）——
        panel.lshape_params_changed.connect(self._on_lshape_params_changed)
        panel.lshape_applied.connect(self._on_lshape_applied)
        panel.lshape_recognize_started.connect(self._on_lshape_recognize_started)

        # —— 新增：草图上传/清除/查看/拖入（LShapePanel → PropertyPanel，委托同一套方法）——
        panel.sketch_pick_requested.connect(self._pool_pick_sketch)
        panel.sketch_clear_requested.connect(self._pool_clear_sketch)
        panel.sketch_view_requested.connect(self._pool_view_sketch)
        panel.sketch_load_requested.connect(self._pool_load_sketch_from_path)

        # —— 新增：目标文件名变更/选文件/清空/历史选择（LShapePanel → PropertyPanel）——
        panel.target_changed.connect(self._on_lshape_target_changed)
        panel.target_pick_requested.connect(self._pool_pick_target_file)
        panel.target_clear_requested.connect(lambda: self._pool_target.clear())
        panel.target_history_pick.connect(self._on_lshape_target_history_pick)

        # —— 新增：一键生成（LShapePanel → PropertyPanel，委托同一套方法）——
        panel.generate_requested.connect(self._pool_run_generate)

        # —— 反向同步：把当前 state 回填到 LShapePanel（草图 + 目标文件名 + 历史菜单）——
        panel.sync_sketch_preview(getattr(self, '_sketch_path', ''))
        panel.set_sketch_path_for_view(getattr(self, '_sketch_path', ''))
        if hasattr(self, '_pool_target'):
            panel.sync_target_from_panel(self._pool_target.text())
        if hasattr(self, '_pool_target_history_menu') and self._pool_target_history_menu:
            panel.set_history_menu(self._pool_target_history_menu)

    def _on_lshape_params_changed(self, *_):
        """用户改动挖角参数（位置/宽/高）→ 触发即时预览。

        与原 _apply_quiet 链路一致：_collect 读取 LShapePanel 控件值 → design_changed。
        """
        self._apply_quiet()

    def _on_lshape_applied(self, params: dict):
        """用户在确认框中确认 L 形挖角 → 切换模式 + 更新画布 + 触发预览。

        迁移自原 _apply_lshape_params 中"切换模式 + 更新画布尺寸"部分；
        参数回填（_cb_lcorner/_sp_lw/_sp_lh）已由 LShapePanel._apply_lshape_params 自行完成。
        """
        try:
            # 1) 裁剪模式 → L 形挖角
            idx = self._cb_mode.findData('rect_lshape')
            if idx >= 0:
                self._cb_mode.setCurrentIndex(idx)
            self._on_mode_change()
            # 2) 画布尺寸 = 外框 + 1cm 损耗（保存 raw 外框供解析换算）
            outer_w = float(params.get('outer_w_cm', 0) or 0)
            outer_h = float(params.get('outer_h_cm', 0) or 0)
            if outer_w > 0 and outer_h > 0:
                self._pool_raw_outer_w = outer_w
                self._pool_raw_outer_h = outer_h
                self._sp_w.setValue(outer_w + 1.0)
                self._sp_h.setValue(outer_h + 1.0)
            # 3) 触发预览渲染
            self._set_pool_status(
                f"✅ L 形挖角已确认：corner={params.get('corner', '?')}，"
                f"挖角 {float(params.get('cut_w_cm', 0)):.1f} × "
                f"{float(params.get('cut_h_cm', 0)):.1f} cm，"
                f"外框 {outer_w:.1f} × {outer_h:.1f} cm。\n"
                f"点击下方「匹配模板 → 解析草图 → 生成预览」完成素材匹配与渲染。")
            self._apply_quiet()
        except Exception as e:
            logger.exception(f"[PropertyPanel] _on_lshape_applied 异常: {e}")
            self._set_pool_status(f"L 形参数回填异常：{e}", is_error=True)

    def _on_lshape_recognize_started(self):
        """用户点 LShapePanel 的"识别 L 形挖角"按钮 → 注入草图路径/目标尺寸并启动解析。

        迁移自原 _pool_try_lshape_parse 中"准备参数"部分；
        实际的 Worker 启动/确认框/参数回填由 LShapePanel.try_lshape_parse 承载。
        """
        if self._lshape_panel is None:
            return
        if not self._sketch_path or not os.path.isfile(self._sketch_path):
            self._lshape_panel._set_status(
                "请先在水池设计器上传尺寸草图，再进行 L 形挖角识别", is_error=True)
            return
        # 目标尺寸：优先文件名解析的原始外框（无 1cm 损耗）
        raw_w = getattr(self, '_pool_raw_outer_w', 0.0)
        raw_h = getattr(self, '_pool_raw_outer_h', 0.0)
        if raw_w <= 0 or raw_h <= 0:
            raw_w = self._sp_w.value()
            raw_h = self._sp_h.value()
        if raw_w <= 0 or raw_h <= 0:
            self._lshape_panel._set_status(
                "请先在水池设计器填写目标尺寸，再进行 L 形挖角识别", is_error=True)
            return
        self._lshape_panel.try_lshape_parse(self._sketch_path, raw_w, raw_h)

    def _get_lshape_params(self):
        """读取 LShapePanel._lshape_params（替代原 self._lshape_params）。

        供 PoolRenderWorker / _detect_user_margin_edits 等链路使用。
        """
        if self._lshape_panel is None:
            return None
        return self._lshape_panel.get_lshape_params()

    def _on_lshape_target_changed(self, text: str):
        """LShapePanel 目标文件名变更 → 同步到水池设计器 + 触发统一处理。

        与水池设计器中 _pool_target.textChanged → _on_pool_target_changed 走同一套逻辑，
        但先写入 _pool_target 再触发，保证 _on_pool_target_changed 内部状态一致。
        """
        if self._lshape_panel is None:
            return
        # blockSignals 避免 _pool_target.setText 反过来触发 LShapePanel → 递归
        self._pool_target.blockSignals(True)
        try:
            if self._pool_target.text() != text:
                self._pool_target.setText(text)
        finally:
            self._pool_target.blockSignals(False)
        # 触发统一处理（解析尺寸 + 回填画布 + 自动识别草图边距）
        self._on_pool_target_changed(text)

    def _on_lshape_target_history_pick(self, name: str):
        """LShapePanel 历史菜单选中 → 写入水池设计器目标文件名 + 触发统一处理。"""
        if self._lshape_panel is None or not name:
            return
        self._pool_target.setText(name)
        self._pool_target.setFocus()
        self._pool_target.setCursorPosition(len(name))


    # ===== [MULTI-HOLE Add-On 2026-08-29] 多洞 UI 辅助函数 =====
    # 单洞模式下这些函数不会被调用（所有调用都被 if is_multi_hole guard 包住）。
    def _set_multi_hole_row_visibility(self, n_holes: int):
        """显示前 N 个洞 + 前 N-1 个间距；其余隐藏。n_holes=0 全部隐藏。"""
        if not hasattr(self, '_mh_rows_widgets') or not self._mh_rows_widgets:
            return
        n_show = max(0, int(n_holes))
        n_gaps_show = max(0, n_show - 1)
        # _mh_rows_widgets 构造顺序：[hole0, gap0, hole1, gap1, ..., hole(N-2), gap(N-2), hole(N-1)]
        # 判断行类型：gap 的 widget 是 QDoubleSpinBox（单一控件）；
        # hole 的 widget 是外层 QWidget（含宽高两行的包装）。
        h_idx = 0
        g_idx = 0
        for (lab, wgt) in self._mh_rows_widgets:
            if isinstance(wgt, QDoubleSpinBox):
                show = (g_idx < n_gaps_show)
                g_idx += 1
            else:
                show = (h_idx < n_show)
                h_idx += 1
            lab.setVisible(show)
            wgt.setVisible(show)

    def _fill_multi_hole_ui(self, holes_cm: list, gaps_cm: list, layout: str = 'horizontal'):
        """PoolWorker 结束后：把 design.pool_holes_cm/gaps 填到多洞 SpinBox。

        同时：(1) 显示 GroupBox + 对应行；(2) 写 layout 标记到 design 供 _collect 读取。
        """
        try:
            n = len(holes_cm) if holes_cm else 0
            # ===== [MULTI-HOLE Add-On 2026-08-29] 同步激活洞数 → _collect 写回严格限定 N =====
            # 单洞(0/1) 或非池模式 → 必须写 0，避免残留激活洞数导致 _collect 污染 design
            self._mh_active_count = max(0, int(n)) if n >= 2 else 0
            # 先显隐行（保证 setVisible 生效）
            self._set_multi_hole_row_visibility(n if n >= 2 else 0)
            # 显示 / 隐藏 GroupBox
            want_visible = (n >= 2)
            if hasattr(self, '_gb_multihole'):
                # 显式 show：避免 Qt 在 QFormLayout setVisible(row widget) 后 GroupBox
                # 自身仍然 hide 导致无法出现在 UI。
                if want_visible:
                    self._gb_multihole.show()
                else:
                    self._gb_multihole.hide()
            title = ""
            if n >= 2:
                layout_zh = {"horizontal": "横排", "vertical": "竖排"}.get((layout or 'horizontal'), "排列")
                title = f"共 {n} 洞（{layout_zh}）"
            else:
                title = "洞数量：0（仅多洞模式生效）"
            if hasattr(self, '_mh_title_label'):
                self._mh_title_label.setText(title)
            if n < 2:
                return
            # 把 layout 存到 design（_collect 里读取使用）；如果字段不存在则忽略
            try:
                self.design.pool_layout_type = (layout or 'horizontal')
            except Exception:
                pass
            # 批量填值：blockSignals 避免无谓的预览重算
            signals_blocked = []
            def _blk(sp):
                sp.blockSignals(True)
                signals_blocked.append(sp)
            for i in range(min(n, len(self._mh_sp_hole_w))):
                hc = holes_cm[i]
                _blk(self._mh_sp_hole_w[i]); self._mh_sp_hole_w[i].setValue(max(0.0, float(hc.get('w_cm', 0))))
                _blk(self._mh_sp_hole_h[i]); self._mh_sp_hole_h[i].setValue(max(0.0, float(hc.get('h_cm', 0))))
            for i in range(min(len(gaps_cm or []), len(self._mh_sp_gaps))):
                _blk(self._mh_sp_gaps[i]); self._mh_sp_gaps[i].setValue(max(0.0, float(gaps_cm[i])))
            for sp in signals_blocked:
                sp.blockSignals(False)
        except Exception as e:
            import logging as _logging
            _logging.getLogger(__name__).warning(f"[Multi-hole UI] 回填多洞参数失败: {e}")

    def _hide_multi_hole_ui(self):
        """单洞/非池模式：隐藏多洞 GroupBox（零视觉影响）。"""
        try:
            # ===== [MULTI-HOLE Add-On 2026-08-29] 清除激活洞数 =====
            # 保证：隐藏后再点"应用"时 _collect 多洞分支完全跳过，不再把 8 个 SpinBox
            # 写回 design，避免状态栏出现 洞3..洞8 (0x0) 等冗余。
            self._mh_active_count = 0
            if hasattr(self, '_gb_multihole'):
                self._gb_multihole.hide()
            self._set_multi_hole_row_visibility(0)
        except Exception:
            pass

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
        # L 形参数已迁移到独立 LShapePanel（始终作为 tab 可见，无需此处切换）
        self._gb_e.setVisible(mode == 'ellipse_hole')

    # ---- 边框层增删改 ----




    # ---- 把面板值同步到 self.design（不触发预览） ----




    # ---- PSD 导出 ----
