"""gui/lshape_panel.py
L 形挖角设计面板：把 L 形挖角相关的所有 UI 与识别逻辑单开一个面板。

【2026-09-02 扩展】新增草图上传 + 目标文件名输入 + 一键生成控件，
所有操作委托给 PropertyPanel 的同一套实现（共享状态、共享逻辑），
两个面板都保留各自的 UI（水池设计器 + L形挖角设计），用户可在任意一侧操作。

设计目标（不改功能逻辑）：
  1) 把原 property_panel.py 中的 `_gb_l`（L 形参数 GroupBox）整体搬来；
  2) 把原 property_panel_poolbox.py 中的 `_pool_btn_lshape`（识别按钮）
     与 L 形解析 Worker 调度整体搬来；
  3) 新增草图上传 + 目标文件名 + 一键生成控件（镜像水池设计器），
     通过信号委托给 PropertyPanel 的同名方法，保证调用一致；
  4) PropertyPanel 通过 set_lshape_params() / sync_sketch_preview() /
     sync_target_from_panel() 回填本面板 UI，实现双向同步；
  5) PropertyPanel 通过 get_corner()/get_cut_w_cm()/get_cut_h_cm()
     在 _collect() 中读取本面板挖角参数，与原直读控件语义完全一致。
"""
from __future__ import annotations
import logging
import os
from datetime import date, datetime, timedelta
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QDoubleSpinBox, QComboBox, QPushButton,
    QCheckBox,
    QScrollArea, QMessageBox,
    QLineEdit, QToolButton, QMenu, QAction, QFileDialog,
)

from core.app_settings import get_app_settings
from core.config import CUT_LOSS_CM
from .property_panel_widgets import _SketchDropLabel
from workers.property_panel_workers import _LShapeParseWorker
logger = logging.getLogger(__name__)


class LShapePanel(QWidget):
    """L 形挖角设计面板：独立承载 L 形挖角的参数设置、草图上传与生成。

    信号（→ PropertyPanel 委托）：
        sketch_pick_requested()     —— 用户点"上传草图"
        sketch_clear_requested()    —— 用户点"清除草图"
        sketch_view_requested()     —— 用户点"查看草图大图"
        sketch_load_requested(str)  —— 用户拖拽草图到本面板 → 带路径委托加载
        target_changed(str)         —— 用户修改目标文件名（textChanged 去抖后发出）
        target_pick_requested()    —— 用户点"选文件"按钮
        target_clear_requested()   —— 用户点"清空"按钮
        target_history_pick(str)    —— 用户从历史菜单选中一条记录
        generate_requested()        —— 用户点"匹配模板 → 解析草图 → 生成预览"
        save_requested()             —— 用户点"导出 JPG"（委托 PropertyPanel.save_requested → main._on_save）
        lshape_params_changed()     —— 用户改动挖角参数（信号定义保留，emit 已注释为测试契约保留，见 _on_param_changed）
        lshape_applied(dict)        —— 用户确认 L 形挖角 → 切换模式 + 更新画布 + 预览
        lshape_recognize_started()  —— 用户点"识别 L 形挖角" → 启动后台解析
    """

    # —— 委托给 PropertyPanel 的信号 ——
    sketch_pick_requested = pyqtSignal()
    sketch_clear_requested = pyqtSignal()
    sketch_view_requested = pyqtSignal()
    sketch_load_requested = pyqtSignal(str)
    target_changed = pyqtSignal(str)
    target_pick_requested = pyqtSignal()
    target_clear_requested = pyqtSignal()
    generate_requested = pyqtSignal()
    save_requested = pyqtSignal()          # 用户点"导出 JPG" → 委托 PropertyPanel → main._on_save
    # —— L 形挖角原有信号 ——
    lshape_params_changed = pyqtSignal()
    lshape_applied = pyqtSignal(dict)
    lshape_recognize_started = pyqtSignal()
    # [2026-09-02 自动 L 形检测] L 形识别结束（成功/失败/取消）：bool=True 成功，False 失败
    # PropertyPanel 用来清除 _lshape_auto_pending 标记，失败时回退到矩形结果
    lshape_recognize_finished = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        # —— L 形挖角参数（确认后写入：corner/cut_w_cm/cut_h_cm/outer_w_cm/outer_h_cm）——
        self._lshape_params = None
        self._lshape_parse_worker = None  # type: _LShapeParseWorker | None
        # —— 参数来源标记：None=未识别 / 'recognize'=识别值 / 'manual'=用户手动修改 ——
        self._params_source = None
        # —— 阶梯模式：True=单边阶梯挖角（_gb_staircase 可见，_gb_l 隐藏）——
        self._staircase_mode = False
        # —— 阶梯子行控件列表：[(offset_x, offset_y, width, height), ...] ——
        self._stair_rows = []
        self._stair_max_levels = 3
        # —— 防止 target_changed 信号在 PropertyPanel 回填时触发递归 ——
        self._block_target_signal = False
        # —— 持久化设置（与水池设计器/圆角裁剪工具共用同一份 QSettings，但 source 隔离）——
        self._app_settings = get_app_settings()
        self._build_ui()
        # —— 初始化本面板独立的目标文件名历史菜单 ——
        self._refresh_target_history_ui()

    # ====================================================================
    # UI 构建
    # ====================================================================
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

        # ===== 1) 目标文件名 =====
        self._gb_target = QGroupBox("📋 目标文件名")
        row_fn = QHBoxLayout(self._gb_target)
        row_fn.addWidget(QLabel("目标文件:"), 0)
        self._target_edit = QLineEdit()
        self._target_edit.setPlaceholderText(
            "例：吸水皮革-定制-裁剪有图-克罗印花;60.5x133CM  （花型名+尺寸必须写）")
        self._target_edit.textChanged.connect(self._on_target_text_changed)
        row_fn.addWidget(self._target_edit, 1)
        btn_pick = QPushButton("选文件")
        btn_pick.setFixedWidth(64)
        btn_pick.clicked.connect(self.target_pick_requested.emit)
        row_fn.addWidget(btn_pick, 0)
        btn_clr = QPushButton("清空")
        btn_clr.setFixedWidth(48)
        btn_clr.clicked.connect(self.target_clear_requested.emit)
        row_fn.addWidget(btn_clr, 0)
        # 历史记录按钮（PropertyPanel 侧注入菜单项）
        self._btn_target_history = QToolButton()
        self._btn_target_history.setText("▾")
        self._btn_target_history.setPopupMode(QToolButton.InstantPopup)
        self._btn_target_history.setToolTip("目标文件名历史记录（保留3天）")
        self._target_history_menu = QMenu(self._btn_target_history)
        self._btn_target_history.setMenu(self._target_history_menu)
        row_fn.addWidget(self._btn_target_history, 0)
        self._inner_layout.addWidget(self._gb_target)

        # ===== 1.5) 输出文件名（默认跟随目标文件名，用于导出 JPG）=====
        row_out = QHBoxLayout()
        row_out.addWidget(QLabel("输出文件名:"), 0)
        self._output_name = QLineEdit()
        self._output_name.setPlaceholderText(
            "导出 JPG 时使用的文件名（不含扩展名），默认跟随上方【目标文件】")
        row_out.addWidget(self._output_name, 1)
        btn_sync = QPushButton("同步目标名")
        btn_sync.setFixedWidth(80)
        btn_sync.clicked.connect(self._btn_sync_output_clicked)
        row_out.addWidget(btn_sync, 0)
        self._inner_layout.addLayout(row_out)

        # ===== 2) 尺寸草图上传 + 缩略预览 =====
        self._gb_sketch = QGroupBox("🖼 尺寸草图")
        row_sk = QHBoxLayout(self._gb_sketch)
        self._sk_preview = _SketchDropLabel("（未上传）\n或拖入图片")
        self._sk_preview.fileDropped.connect(self._on_sketch_dropped)   # 拖拽 → 委托 PropertyPanel
        self._sk_preview.clicked.connect(self.sketch_view_requested.emit)  # 点击 → 委托查看大图
        sk_btns = QVBoxLayout()
        btn_sk1 = QPushButton("上传草图…")
        btn_sk1.clicked.connect(self.sketch_pick_requested.emit)
        btn_sk2 = QPushButton("清除草图")
        btn_sk2.clicked.connect(self.sketch_clear_requested.emit)
        sk_btns.addWidget(btn_sk1)
        sk_btns.addWidget(btn_sk2)
        row_sk.addWidget(self._sk_preview, 0)
        row_sk.addLayout(sk_btns, 0)
        sk_desc = QLabel(
            "💡 草图格式示例（红色线标注上下左右边距即可）\n"
            "自动识别失败时可在【水池设计器】下方【内挖边距】手动调整")
        sk_desc.setStyleSheet("color:#666;")
        sk_desc.setWordWrap(True)
        row_sk.addWidget(sk_desc, 1)
        self._inner_layout.addWidget(self._gb_sketch)

        # ===== 3) L 形挖角识别区 =====
        # 标题只保留文字，去掉底部白色背景渲染，避免背景块遮挡内容。
        # 用 subcontrol-origin: border + 给足 margin-top，让含 emoji 图标的
        # 标题完整浮在边框线上方（与「尺寸草图」观感一致，不被裁切）。
        self._gb_lshape_recog = QGroupBox("L 形挖角识别")
        self._gb_lshape_recog.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 2px solid #E6A23C; "
            "border-radius: 6px; margin-top: 14px; padding-top: 12px; "
            "background: #FFFFFF; }"
            "QGroupBox[lowConfidence=\"true\"] { border-color: #D97706; "
            "background: #FFFBEB; }"
            "QGroupBox::title { subcontrol-origin: border; subcontrol-position: top left; "
            "left: 12px; top: -2px; padding: 0 6px; color: #B26A00; }")
        fr = QVBoxLayout(self._gb_lshape_recog)
        fr.setSpacing(6)

        self._btn_lshape = QPushButton("✂️ 识别L形挖角")
        self._btn_lshape.setToolTip(
            "把当前草图按 L 形挖角识别（A/B/C/D/E/F 六处尺寸标注）。\n"
            "识别成功会弹出确认框，可修改挖角位置/宽/高后一键生成。\n"
            "上传草图后也会自动尝试 L 形识别；此按钮用于手动重新识别。")
        self._btn_lshape.setStyleSheet(
            "QPushButton { background:#FFF3E0; color:#B26A00; border:1px solid #E6A23C;"
            " border-radius:4px; padding:6px 10px; font-weight:bold; }"
            "QPushButton:hover { background:#FFE8C2; }"
            "QPushButton:disabled { color:#ccc; background:#f5f5f5; border-color:#ddd; }")
        self._btn_lshape.clicked.connect(self._on_recognize_clicked)
        fr.addWidget(self._btn_lshape)

        self._lshape_status = QLabel(
            "（填写目标文件名并上传草图后，点上方按钮识别 L 形挖角）")
        self._lshape_status.setWordWrap(True)
        self._lshape_status.setStyleSheet("color:#555; padding: 4px 6px;")
        fr.addWidget(self._lshape_status)
        self._inner_layout.addWidget(self._gb_lshape_recog)

        # ===== 4+5) 参数组同行并排：外框尺寸(左) + L 形挖角参数(右) =====
        # v2.1 布局调整：把原本上下堆叠的两个 GroupBox 改为同一行并排，
        # 减少纵向滚动；外框尺寸（与画布强相关）放左侧优先视线位置。
        # 仅改布局，控件创建 / 信号连接 / 样式全部保持原状。
        params_row = QHBoxLayout()
        params_row.setSpacing(8)
        params_row.setContentsMargins(0, 0, 0, 0)

        # ===== 5) 外框尺寸 GroupBox (左侧：先 add → 左) =====
        # 语义：SpinBox 显示 = 画布值 = 设计外框 + 1cm 损耗
        #   - 用户改 SpinBox 画布值（如 144.0）
        #   → _on_param_changed 回写 dict['outer_w_cm'] = 144 - 1 = 143.0（设计真值）
        #   → PropertyPanel 桥接层同步 _pool_raw_outer_w = 143，_sp_w = 144
        # 与水池设计器 _sp_w/_sp_h（画布值）语义完全一致。
        self._gb_outer = QGroupBox("外框尺寸（cm）")
        self._gb_outer.setStyleSheet(self._param_group_style("#5B6CFF"))
        fo = QVBoxLayout(self._gb_outer)
        fo.setSpacing(6)
        self._sp_outer_w = self._dspin(5, 500, 5.0)
        self._sp_outer_h = self._dspin(5, 500, 5.0)
        fo.addLayout(self._row("宽(cm)", self._sp_outer_w))
        fo.addLayout(self._row("高(cm)", self._sp_outer_h))
        params_row.addWidget(self._gb_outer, 1)  # 外框尺寸 → 左

        # ===== 4) L 形挖角参数 GroupBox (右侧：后 add → 右) =====
        self._gb_l = QGroupBox("L 形挖角参数")
        self._gb_l.setStyleSheet(self._param_group_style("#5B6CFF"))
        fl = QVBoxLayout(self._gb_l)
        fl.setSpacing(6)
        self._corner_rows = []
        for row_index in range(4):
            enabled = QCheckBox(f"挖角 {row_index + 1}")
            enabled.setChecked(row_index == 0)
            combo = QComboBox()
            combo.addItem("左上角", "tl")
            combo.addItem("右上角", "tr")
            combo.addItem("左下角", "bl")
            combo.addItem("右下角", "br")
            combo.setCurrentIndex(3 if row_index == 0 else row_index)
            width = self._dspin(0, 450, 0.0)
            height = self._dspin(0, 450, 0.0)
            enabled.toggled.connect(self._on_param_changed)
            combo.currentIndexChanged.connect(self._on_param_changed)
            width.valueChanged.connect(self._on_param_changed)
            height.valueChanged.connect(self._on_param_changed)
            row = QHBoxLayout()
            row.addWidget(enabled, 0)
            row.addWidget(combo, 1)
            row.addWidget(QLabel("宽"), 0)
            row.addWidget(width, 1)
            row.addWidget(QLabel("高"), 0)
            row.addWidget(height, 1)
            fl.addLayout(row)
            self._corner_rows.append((enabled, combo, width, height))
        self._margin_hint = QLabel("边余量：上— · 下— · 左— · 右—")
        self._margin_hint.setObjectName("margin_hint")
        self._margin_hint.setWordWrap(True)
        self._margin_hint.setStyleSheet("color:#667085; padding: 2px 4px;")
        fl.addWidget(self._margin_hint)
        # 旧单角 API 继续指向第一行，避免外部调用方行为变化。
        self._cb_lcorner = self._corner_rows[0][1]
        self._sp_lw = self._corner_rows[0][2]
        self._sp_lh = self._corner_rows[0][3]
        params_row.addWidget(self._gb_l, 1)  # L 形挖角参数 → 右

        self._inner_layout.addLayout(params_row)

        # ===== 4.5) 阶梯挖角参数 GroupBox（默认隐藏，阶梯模式时显示）=====
        self._build_staircase_ui()
        self._inner_layout.addWidget(self._gb_staircase)
        self._gb_staircase.setVisible(False)

        # ===== 6) 一键生成预览 + 导出 JPG（底部主操作行，与水池设计器一致）=====
        row_action = QHBoxLayout()
        row_action.setSpacing(8)
        self._btn_generate = QPushButton("🔍 生成预览")
        self._btn_generate.setToolTip(
            "匹配模板 → 解析草图 → 生成预览（在水池设计器画布上实时渲染）")
        self._btn_generate.setStyleSheet(
            "QPushButton { padding: 10px 12px; font-weight: bold; font-size: 14px;"
            " background: #4A90E2; color: white; border: none; border-radius: 5px; }"
            "QPushButton:hover { background: #357ABD; }"
            "QPushButton:disabled { background: #A0BFE0; color: #eee; }")
        self._btn_generate.clicked.connect(self.generate_requested.emit)
        self._btn_save = QPushButton("💾 导出 JPG")
        self._btn_save.setToolTip(
            "把当前画布设计渲染为全分辨率 JPG 并保存到本地文件。\n"
            "导出文件名优先取上方“目标文件名”，未填写则按画布尺寸自动生成。")
        self._btn_save.setStyleSheet(
            "QPushButton { padding: 10px 12px; font-weight: bold; font-size: 14px;"
            " background: #27AE60; color: white; border: none; border-radius: 5px; }"
            "QPushButton:hover { background: #1F8B4C; }"
            "QPushButton:disabled { background: #A8D8B9; color: #eee; }")
        self._btn_save.clicked.connect(self.save_requested.emit)
        row_action.addWidget(self._btn_generate, 1)
        row_action.addWidget(self._btn_save, 1)
        self._inner_layout.addLayout(row_action)

        self._inner_layout.addStretch(1)

        # 连接参数变化信号（即时预览）
        # 外框尺寸变化也触发即时预览（与水池设计器画布尺寸联动）
        self._sp_outer_w.valueChanged.connect(self._on_param_changed)
        self._sp_outer_h.valueChanged.connect(self._on_param_changed)
        self._update_margin_hint(
            max(0.0, self._sp_outer_w.value() - CUT_LOSS_CM),
            max(0.0, self._sp_outer_h.value() - CUT_LOSS_CM),
            self.get_cuts_cm())
    def _dspin(self, mn, mx, val, decimals=2):
        s = QDoubleSpinBox()
        s.setRange(mn, mx)
        s.setValue(val)
        s.setDecimals(decimals)
        s.setSingleStep(0.5)
        return s

    def _row(self, label: str, widget: QWidget) -> QHBoxLayout:
        lay = QHBoxLayout()
        lay.addWidget(QLabel(label), 0)
        lay.addWidget(widget, 1)
        return lay

    def _param_group_style(self, accent: str) -> str:
        """参数 GroupBox 统一样式：浅色边框 + 标题着色，比识别区（橙色）弱，保持视觉层级。

        accent 为标题/边框主色，默认靛蓝（与生成预览按钮蓝呼应）。
        """
        return (
            "QGroupBox { font-weight: bold; border: 1px solid #C9D0E5;"
            " border-radius: 6px; margin-top: 14px; padding-top: 12px;"
            f" background: #FBFCFF; }} "
            "QGroupBox::title { subcontrol-origin: border; subcontrol-position: top left;"
            " left: 10px; top: -2px; padding: 0 6px;"
            f" color: {accent}; }}")

    def _build_staircase_ui(self):
        """构建阶梯挖角参数 GroupBox（默认隐藏，_set_staircase_mode(True) 时显示）。

        结构：
          - 角位选择器（QComboBox）
          - 2 级子行（默认）~ 3 级子行（上限），每级：offset_x / offset_y / 宽 / 高
          - 「追加一级」/「删除末级」按钮
        """
        self._gb_staircase = QGroupBox("单边阶梯挖角参数")
        self._gb_staircase.setStyleSheet(self._param_group_style("#E67E22"))
        fs = QVBoxLayout(self._gb_staircase)
        fs.setSpacing(6)

        row_corner = QHBoxLayout()
        row_corner.addWidget(QLabel("角位"), 0)
        self._stair_corner = QComboBox()
        self._stair_corner.addItem("左上角", "tl")
        self._stair_corner.addItem("右上角", "tr")
        self._stair_corner.addItem("左下角", "bl")
        self._stair_corner.addItem("右下角", "br")
        self._stair_corner.setCurrentIndex(1)
        self._stair_corner.currentIndexChanged.connect(self._on_staircase_changed)
        row_corner.addWidget(self._stair_corner, 1)
        fs.addLayout(row_corner)

        self._stair_rows_container = QVBoxLayout()
        self._stair_rows_container.setSpacing(4)
        fs.addLayout(self._stair_rows_container)

        self._stair_add_btn = QPushButton("+ 追加一级")
        self._stair_add_btn.setFixedWidth(100)
        self._stair_add_btn.setToolTip("追加一级阶梯（最多 3 级）")
        self._stair_add_btn.setStyleSheet(
            "QPushButton { background:#FFF3E0; color:#B26A00; border:1px solid #E6A23C;"
            " border-radius:3px; padding:3px 6px; font-size:11px; }"
            "QPushButton:hover { background:#FFE8C2; }"
            "QPushButton:disabled { color:#ccc; background:#f5f5f5; }")
        self._stair_add_btn.clicked.connect(self._on_stair_add_level)
        self._stair_remove_btn = QPushButton("- 删除末级")
        self._stair_remove_btn.setFixedWidth(100)
        self._stair_remove_btn.setToolTip("删除最后一级阶梯（至少保留 1 级）")
        self._stair_remove_btn.setStyleSheet(
            "QPushButton { background:#FFF3E0; color:#B26A00; border:1px solid #E6A23C;"
            " border-radius:3px; padding:3px 6px; font-size:11px; }"
            "QPushButton:hover { background:#FFE8C2; }"
            "QPushButton:disabled { color:#ccc; background:#f5f5f5; }")
        self._stair_remove_btn.clicked.connect(self._on_stair_remove_level)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self._stair_add_btn)
        btn_row.addWidget(self._stair_remove_btn)
        btn_row.addStretch(1)
        fs.addLayout(btn_row)

        self._stair_add_level_row()
        self._stair_add_level_row()
        self._update_stair_buttons()

    def _stair_add_level_row(self, offset_x: float = 0.0, offset_y: float = 0.0,
                             w: float = 0.0, h: float = 0.0):
        """向阶梯容器追加一行（offset_x / offset_y / 宽 / 高 SpinBox）。"""
        if len(self._stair_rows) >= self._stair_max_levels:
            return
        level_idx = len(self._stair_rows)
        ox = self._dspin(0, 450, offset_x)
        oy = self._dspin(0, 450, offset_y)
        sp_w = self._dspin(0, 450, w)
        sp_h = self._dspin(0, 450, h)
        for sp in (ox, oy, sp_w, sp_h):
            sp.valueChanged.connect(self._on_staircase_changed)
        row = QHBoxLayout()
        lbl = QLabel(f"L{level_idx + 1}")
        lbl.setFixedWidth(20)
        lbl.setStyleSheet("color:#E67E22; font-weight:bold; font-size:11px;")
        row.addWidget(lbl, 0)
        row.addWidget(QLabel("Δx"), 0)
        row.addWidget(ox, 1)
        row.addWidget(QLabel("Δy"), 0)
        row.addWidget(oy, 1)
        row.addWidget(QLabel("宽"), 0)
        row.addWidget(sp_w, 1)
        row.addWidget(QLabel("高"), 0)
        row.addWidget(sp_h, 1)
        container_widget = QWidget()
        container_widget.setLayout(row)
        container_widget.setStyleSheet("margin-left: 16px;")
        self._stair_rows_container.addWidget(container_widget)
        self._stair_rows.append((ox, oy, sp_w, sp_h, container_widget))

    def _on_stair_add_level(self):
        """用户点「追加一级」→ 新增一行子行。"""
        if len(self._stair_rows) < self._stair_max_levels:
            self._stair_add_level_row()
            self._update_stair_buttons()
            self._on_staircase_changed()

    def _on_stair_remove_level(self):
        """用户点「删除末级」→ 移除最后一行子行（至少保留 1 行）。"""
        if len(self._stair_rows) <= 1:
            return
        ox, oy, sp_w, sp_h, w = self._stair_rows.pop()
        self._stair_rows_container.removeWidget(w)
        w.setParent(None)
        w.deleteLater()
        self._update_stair_buttons()
        self._on_staircase_changed()

    def _update_stair_buttons(self):
        """根据当前子行数更新追加/删除按钮的启用状态。"""
        n = len(self._stair_rows)
        self._stair_add_btn.setEnabled(n < self._stair_max_levels)
        self._stair_remove_btn.setEnabled(n > 1)

    def _on_staircase_changed(self, *_):
        """阶梯控件变化 → 更新 _lshape_params dict（与 _on_param_changed 同语义）。"""
        if not self._staircase_mode:
            return
        cut_rects = self.get_cut_rects_cm()
        outer_w = max(0.0, self._sp_outer_w.value() - CUT_LOSS_CM)
        outer_h = max(0.0, self._sp_outer_h.value() - CUT_LOSS_CM)
        anchor = self._stair_corner.currentData() or 'tr'
        primary_w = cut_rects[0]['w_cm'] if cut_rects else 0.0
        primary_h = cut_rects[0]['h_cm'] if cut_rects else 0.0
        if self._lshape_params is None:
            self._lshape_params = {}
        self._lshape_params.update({
            'corner': anchor,
            'cut_w_cm': primary_w,
            'cut_h_cm': primary_h,
            'cuts_cm': self.get_cuts_cm(),
            'cut_rects': cut_rects,
            'outer_w_cm': outer_w,
            'outer_h_cm': outer_h,
        })
        self._params_source = 'manual'

    def _set_staircase_mode(self, enabled: bool):
        """切换标准多角模式 ↔ 单边阶梯模式。

        enabled=True:  隐藏 _gb_l（4 角行），显示 _gb_staircase
        enabled=False: 显示 _gb_l（4 角行），隐藏 _gb_staircase
        """
        self._staircase_mode = enabled
        self._gb_l.setVisible(not enabled)
        self._gb_staircase.setVisible(enabled)

    def get_cut_rects_cm(self) -> list[dict]:
        """返回阶梯挖角的 CutRect 列表（厘米），按子行顺序。

        每项：{'anchor': str, 'offset_x_cm': float, 'offset_y_cm': float,
               'w_cm': float, 'h_cm': float}
        非阶梯模式返回空列表。
        """
        if not self._staircase_mode:
            return []
        anchor = self._stair_corner.currentData() or 'tr'
        result = []
        for ox_sp, oy_sp, w_sp, h_sp, _ in self._stair_rows:
            w_val = w_sp.value()
            h_val = h_sp.value()
            if w_val <= 0 or h_val <= 0:
                continue
            result.append({
                'anchor': anchor,
                'offset_x_cm': max(0.0, ox_sp.value()),
                'offset_y_cm': max(0.0, oy_sp.value()),
                'w_cm': w_val,
                'h_cm': h_val,
            })
        return result

    def set_cut_rects(self, cut_rects: list[dict]):
        """识别结果回填：把 CutRect 列表写入阶梯子行 SpinBox。

        cut_rects: [{'anchor': str, 'offset_x_cm': float, 'offset_y_cm': float,
                      'w_cm': float, 'h_cm': float}, ...]
        自动切换到阶梯模式（_gb_staircase 可见，_gb_l 隐藏）。
        子行数按输入长度调整（1~3），多余行删除，不足行追加。
        """
        cut_rects = list(cut_rects or [])[:self._stair_max_levels]
        if not cut_rects:
            return
        self._set_staircase_mode(True)
        anchor = cut_rects[0].get('anchor', 'tr')
        idx = self._stair_corner.findData(anchor)
        self._stair_corner.blockSignals(True)
        if idx >= 0:
            self._stair_corner.setCurrentIndex(idx)
        self._stair_corner.blockSignals(False)
        while len(self._stair_rows) > len(cut_rects):
            self._on_stair_remove_level()
        for i, cr in enumerate(cut_rects):
            if i >= len(self._stair_rows):
                self._stair_add_level_row()
            ox_sp, oy_sp, w_sp, h_sp, _ = self._stair_rows[i]
            for sp in (ox_sp, oy_sp, w_sp, h_sp):
                sp.blockSignals(True)
            try:
                ox_sp.setValue(max(0.0, float(cr.get('offset_x_cm', 0))))
                oy_sp.setValue(max(0.0, float(cr.get('offset_y_cm', 0))))
                w_sp.setValue(max(0.0, float(cr.get('w_cm', 0))))
                h_sp.setValue(max(0.0, float(cr.get('h_cm', 0))))
            finally:
                for sp in (ox_sp, oy_sp, w_sp, h_sp):
                    sp.blockSignals(False)
        self._update_stair_buttons()
        self._on_staircase_changed()

    # ====================================================================
    # 目标文件名处理
    # ====================================================================
    def _on_target_text_changed(self, text: str):
        """用户修改目标文件名 → 1) 去抖后发信号给 PropertyPanel；2) 自动同步输出文件名"""
        if self._block_target_signal:
            return
        self.target_changed.emit(text)
        # 自动同步到输出文件名（与水池设计器行为一致：目标名变 → 输出名跟随）
        self._sync_output_from_target()

    def sync_target_from_panel(self, name: str):
        """PropertyPanel 回填目标文件名（双向同步：水池设计器 → L形挖角设计）。

        block_target_signal 避免触发 target_changed → _on_pool_target_changed 递归。
        """
        self._block_target_signal = True
        try:
            if self._target_edit.text() != name:
                self._target_edit.setText(name)
            # 同时把输出文件名同步过去
            self._sync_output_from_target()
        finally:
            self._block_target_signal = False

    def get_target_text(self) -> str:
        """读取当前目标文件名（供 PropertyPanel 读取回填前的值）。"""
        return self._target_edit.text().strip()

    # —— 输出文件名：用于导出 JPG 的文件名，默认与目标文件名同步 ——
    def _sync_output_from_target(self):
        """把目标文件名（去掉扩展名 + 路径）同步到输出文件名框"""
        t = self._target_edit.text().strip()
        if not t:
            return
        base, ext = os.path.splitext(t)
        if ext.lower() in {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.psd', '.psb', '.webp'}:
            t = base
        t = os.path.basename(t)
        if t and t != self._output_name.text():
            self._output_name.setText(t)

    def _btn_sync_output_clicked(self):
        """用户主动点击"同步目标名"按钮"""
        self._sync_output_from_target()

    def get_output_filename(self) -> str:
        """返回本面板用于导出 JPG 的建议文件名（不含扩展名）。
        优先取"输出文件名"框；若为空则回退到目标文件名（与 get_target_text 同处理）；
        再空则返回空字符串，由调用方兜底。
        """
        out_s = self._output_name.text().strip()
        if out_s:
            base, ext = os.path.splitext(out_s)
            if ext.lower() in {'.jpg', '.jpeg', '.png'}:
                return base
            return out_s
        t = self._target_edit.text().strip()
        if t:
            base, ext = os.path.splitext(t)
            if ext.lower() in {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.psd', '.psb', '.webp'}:
                t = base
            t = os.path.basename(t)
            return t
        return ""

    def set_generate_enabled(self, enabled: bool, text: str | None = None):
        """启用/禁用一键生成按钮 + 可选改文字（供 PropertyPanel 在运行时调用）。"""
        self._btn_generate.setEnabled(enabled)
        if text is not None:
            self._btn_generate.setText(text)

    # ====================================================================
    # 草图缩略图 + 拖入处理
    # ====================================================================
    def sync_sketch_preview(self, sketch_path: str):
        """PropertyPanel 回填草图缩略图（双向同步：水池设计器 → L形挖角设计）。

        与原 _pool_load_sketch_from_path 中缩略图显示逻辑一致。
        """
        if not sketch_path or not os.path.isfile(sketch_path):
            self._sk_preview.clear()
            self._sk_preview.setText("（未上传）\n或拖入图片")
            self._sk_preview.setStyleSheet(
                "QLabel { border: 2px dashed #4A90E2; color: #4A90E2; background:#EFF6FF;"
                " qproperty-alignment: AlignCenter; border-radius: 6px; font-size: 11px; }")
            self._sk_preview.set_has_image(False)
            return
        pm = QPixmap(sketch_path)
        if not pm.isNull():
            self._sk_preview.setPixmap(pm.scaled(
                self._sk_preview.size(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self._sk_preview.setStyleSheet(
                "QLabel { border: 1px solid #888; background:#fff; border-radius: 6px; }")
            self._sk_preview.set_has_image(True)
        else:
            self._sk_preview.clear()
            self._sk_preview.setText("（预览失败）\n或拖入图片")
            self._sk_preview.set_has_image(False)

    def _on_sketch_dropped(self, path: str):
        """用户把图片拖入本面板 → 带路径委托给 PropertyPanel 统一处理。

        与水池设计器中 _pool_sk_preview.fileDropped → _pool_load_sketch_from_path
        走同一套代码路径，保证上传 + 自动解析 + 自动 L 形识别行为完全一致。
        """
        self.sketch_load_requested.emit(path)

    def set_sketch_path_for_view(self, path: str):
        """PropertyPanel 回填草图路径（供点击缩略图查看大图用）。"""
        self._sk_preview.setProperty("sketch_path", path)

    # ====================================================================
    # L 形参数变化 → 通知 PropertyPanel 触发预览
    # ====================================================================
    def _on_param_changed(self, *_):
        """参数变化（挖角 + 外框画布）→ 更新 _lshape_params 设计真值 + 发信号触发预览。

        语义转换：
          - 外框 SpinBox 存画布值（= 设计外框 + 1cm 损耗）
          - dict['outer_w_cm'] 存设计真值（SpinBox - 1cm）
          - 挖角 SpinBox 存设计值，dict['cut_w_cm'] 直接取 SpinBox
        """
        _TRIM = CUT_LOSS_CM
        # 外框：SpinBox 画布值 → dict 设计值
        canvas_outer_w = max(0.0, self._sp_outer_w.value())
        canvas_outer_h = max(0.0, self._sp_outer_h.value())
        design_outer_w = max(0.0, canvas_outer_w - _TRIM)
        design_outer_h = max(0.0, canvas_outer_h - _TRIM)
        cuts = self.get_cuts_cm()
        self._update_margin_hint(design_outer_w, design_outer_h, cuts)
        primary = cuts[0] if cuts else {
            'corner': self._cb_lcorner.currentData(),
            'cut_w_cm': max(0.0, self._sp_lw.value()),
            'cut_h_cm': max(0.0, self._sp_lh.value()),
        }
        if self._lshape_params is None:
            self._lshape_params = {
                'corner': primary['corner'],
                'cut_w_cm': primary['cut_w_cm'],
                'cut_h_cm': primary['cut_h_cm'],
                'cuts_cm': cuts,
                'outer_w_cm': design_outer_w,
                'outer_h_cm': design_outer_h,
            }
        else:
            self._lshape_params['corner'] = primary['corner']
            self._lshape_params['cut_w_cm'] = primary['cut_w_cm']
            self._lshape_params['cut_h_cm'] = primary['cut_h_cm']
            self._lshape_params['cuts_cm'] = cuts
            self._lshape_params['outer_w_cm'] = design_outer_w
            self._lshape_params['outer_h_cm'] = design_outer_h
        # 标记为用户手动修改（回填识别值时 blockSignals 已保护不会触发这里）
        self._params_source = 'manual'
        # 状态提示：手动修改标注，与识别值区分
        if self._lshape_params is not None and self._lshape_params.get('outer_w_cm', 0) > 0:
            dp = self._lshape_params
            self._set_status(
                f"✏️ 参数已手动修改：corner={dp.get('corner', '?')}，"
                f"挖角 {dp.get('cut_w_cm', 0):.1f} × {dp.get('cut_h_cm', 0):.1f} cm，"
                f"外框 {dp.get('outer_w_cm', 0):.1f} × {dp.get('outer_h_cm', 0):.1f} cm。\n"
                f"（点击「匹配模板 → 解析草图 → 生成预览」完成素材匹配与渲染）")
        # [2026-09-05 交互范式切换] 不再 emit lshape_params_changed 触发实时渲染
        # SpinBox 修改 → 只更新 _lshape_params dict（参数真值），渲染由显式生成按钮驱动
        # self.lshape_params_changed.emit()

    def _update_margin_hint(self, outer_w_cm: float, outer_h_cm: float,
                            cuts: list[dict]):
        """显示当前挖角输入对应的四边剩余余量。"""
        side_used = {'top': 0.0, 'bottom': 0.0, 'left': 0.0, 'right': 0.0}
        for cut in cuts:
            corner = cut.get('corner')
            if corner in ('tl', 'tr'):
                side_used['top'] += cut.get('cut_w_cm', 0.0)
            if corner in ('bl', 'br'):
                side_used['bottom'] += cut.get('cut_w_cm', 0.0)
            if corner in ('tl', 'bl'):
                side_used['left'] += cut.get('cut_h_cm', 0.0)
            if corner in ('tr', 'br'):
                side_used['right'] += cut.get('cut_h_cm', 0.0)

        remaining = {
            'top': outer_w_cm - side_used['top'],
            'bottom': outer_w_cm - side_used['bottom'],
            'left': outer_h_cm - side_used['left'],
            'right': outer_h_cm - side_used['right'],
        }
        invalid = any(value < 0 for value in remaining.values())
        color = '#C0392B' if invalid else '#667085'
        self._margin_hint.setStyleSheet(f"color:{color}; padding: 2px 4px;")
        values = ' · '.join(
            f"{label}{remaining[key]:.1f} cm"
            for key, label in (
                ('top', '上'), ('bottom', '下'), ('left', '左'), ('right', '右')))
        suffix = "（输入超出外框）" if invalid else ""
        self._margin_hint.setText(f"边余量：{values}{suffix}")

    # ====================================================================
    # L 形挖角识别（Worker 调度 + 确认框，逻辑与原实现一致）
    # ====================================================================
    def _on_recognize_clicked(self):
        """按钮点击 → 通知 PropertyPanel 提供草图路径/目标尺寸，再由本面板启动解析。"""
        self.lshape_recognize_started.emit()

    def try_lshape_parse(self, sketch_path: str, raw_w: float, raw_h: float):
        """启动后台 L 形草图解析（多尺度 OCR，耗时较长）。"""
        if not sketch_path or not os.path.isfile(sketch_path):
            self._set_status("请先上传尺寸草图，再进行 L 形挖角识别", is_error=True)
            return
        if self._btn_lshape is not None:
            self._btn_lshape.setEnabled(False)
            self._btn_lshape.setText("识别中…")
        if raw_w <= 0 or raw_h <= 0:
            self._set_status("请先填写目标文件名（解析出尺寸），再进行 L 形挖角识别", is_error=True)
            if self._btn_lshape is not None:
                self._btn_lshape.setEnabled(True)
                self._btn_lshape.setText("✂️ 识别L形挖角")
            return

        # 取消前一次未完成的 L 形解析
        if self._lshape_parse_worker is not None:
            old = self._lshape_parse_worker
            self._lshape_parse_worker = None
            if old.isRunning():
                try:
                    old.requestInterruption()
                    if not old.wait(2000):
                        # 线程仍在运行：连接 finished→deleteLater 确保结束后释放
                        try:
                            old.finished.connect(old.deleteLater)
                        except TypeError:
                            old.deleteLater()
                    else:
                        old.deleteLater()
                except Exception:
                    old.deleteLater()
            else:
                old.deleteLater()
        worker = _LShapeParseWorker(sketch_path, raw_w, raw_h, self)
        worker.finished_ok.connect(self._on_lshape_parsed)
        worker.finished_err.connect(self._on_lshape_parse_err)
        worker.finished.connect(self._on_lshape_worker_finished)
        self._lshape_parse_worker = worker
        self._set_status("正在识别 L 形挖角（多尺度 OCR，通常约 10 秒~2 分钟，Tesseract 配置异常时最坏可达十余分钟，可随时取消）…")
        worker.start()

    def _on_lshape_worker_finished(self):
        """L 形解析线程结束：恢复按钮状态"""
        if self._btn_lshape is not None:
            self._btn_lshape.setEnabled(True)
            self._btn_lshape.setText("✂️ 识别L形挖角")

    def _on_lshape_parsed(self, result):
        """L 形解析完成：
        - 成功 → 无弹窗，直接把（corner/w/h/外框）写进面板 SpinBox + 状态栏内联摘要 +
          发出 lshape_applied 切到 rect_lshape 模式并触发预览（等价于旧「确认并生成」）。
        - 非 L 形 / 参数无效 / 异常 → 提示并 emit(False)，保证 PropertyPanel 清 auto-pending 标记。

        [2026-09-03 UI 简化] 原流程：Worker→QDialog.exec_()→用户点确认→_apply_lshape_params
                      新流程：Worker→验证→_apply_lshape_params，参数改由面板 SpinBox 就地编辑。
        """
        try:
            # S4 invariant: stale result（用户重新识别导致旧 Worker signal 到达）直接丢弃
            # 不 emit finished，因为新 Worker 会在正确时间 emit。
            if self.sender() is not self._lshape_parse_worker:
                logger.info("[LShapePanel] 忽略已过期的 L 形解析结果")
                return
            # S1 invariant 分支 1：解析层报告 success=False
            # 但识别层已产出 all_corners / notches_detected 时，仍可把候选角位回填到
            # 4 行 GUI 供用户人工修正，不再丢弃已发现的角信息。
            if not result.success:
                suggestions = self._extract_multicorner_suggestions(result)
                if suggestions:
                    primary = suggestions[0]
                    self._apply_lshape_params(
                        primary['corner'],
                        primary['cut_w_cm'],
                        primary['cut_h_cm'],
                        result,
                    )
                    self._set_status(
                        "⚠️ 识别到多个挖角候选，已回填到 4 行参数表（尺寸为建议值，可人工修正后再生成预览）。"
                    )
                    self.lshape_recognize_finished.emit(True)
                    return
                self._set_status(
                    f"ℹ️ L 形识别未成功（已按矩形解析处理）：{result.message}", is_error=False)
                self.lshape_recognize_finished.emit(False)
                return

            # 提取参数（与旧 _LShapeConfirmDialog.__init__ 取值策略一致，fallback 安全值）
            corner_raw = (result.corner or 'tl')
            cut_w_cm = max(0.0, float(result.cut_w_cm or 0))
            cut_h_cm = max(0.0, float(result.cut_h_cm or 0))

            # S3 invariant：与旧 dialog _on_accept 校验同条件 —— 尺寸必须>0 且 corner 合法
            VALID_CORNERS = {'tl', 'tr', 'bl', 'br'}
            if (cut_w_cm <= 0 or cut_h_cm <= 0
                    or corner_raw not in VALID_CORNERS):
                self._set_status(
                    f"ℹ️ L 形识别结果无效（corner={corner_raw!r}，挖角 {cut_w_cm:.1f}×{cut_h_cm:.1f} cm），"
                    f"已按矩形解析处理", is_error=False)
                self.lshape_recognize_finished.emit(False)
                return

            # 成功分支：直接应用（效果 = 用户在旧弹窗点了「确认并生成」）
            # _apply_lshape_params 内部会：填 SpinBox、写 _lshape_params、
            # 状态栏内联摘要、emit lshape_applied → PropertyPanel 切模式 + 同步尺寸 + 预览。
            self._apply_lshape_params(corner_raw, cut_w_cm, cut_h_cm, result)
            self.lshape_recognize_finished.emit(True)
        except Exception as e:
            logger.exception(f"[LShapePanel] _on_lshape_parsed 异常: {e}")
            self._set_status(f"L 形识别回调异常：{e}", is_error=True)
            self.lshape_recognize_finished.emit(False)

    def _on_lshape_parse_err(self, err_msg: str):
        """L 形解析异常：忽略（矩形结果不受影响）"""
        logger.warning(f"[LShapePanel] L 形解析异常（忽略）: {err_msg}")
        self._set_status(f"L 形识别异常（已忽略，保留矩形结果）：{err_msg}")
        self.lshape_recognize_finished.emit(False)

    def _extract_multicorner_suggestions(self, result) -> list[dict]:
        """从识别结果中提取候选角位，按角位映射到四行 GUI 作为建议值。

        规则（按优先级）：
        1. 优先取 result.debug['cuts_cm']（OCR 归属后的真值，含 cut_w_cm/cut_h_cm）。
           —— [Bug Fix 2026-09-15] 这是修复的核心：此前只从 debug['all_corners']
           （仅含 px 值）读取，OCR 真值被像素比例反推值覆盖，导致 45.8→50.5 等
           数值漂移。cuts_cm 由 _attribute_cut_ocr_per_corner 产出，含 OCR 识别的
           真实 cm 值，是最高优先级数据源。
        2. fallback 到 debug['all_corners'] / geometry['all_corners'] 的像素比例换算。
        3. 只保留 tl/tr/bl/br 四个合法角位；允许用户手动修正。
        """
        if result is None:
            return []
        debug = getattr(result, 'debug', {}) or {}
        geo = debug.get('geometry', {}) if isinstance(debug, dict) else {}

        outer_w_cm = max(0.0, float(getattr(result, 'outer_w_cm', 0.0) or 0.0))
        outer_h_cm = max(0.0, float(getattr(result, 'outer_h_cm', 0.0) or 0.0))
        px_outer_w = float(geo.get('outer_w_px', 0) or 0)
        px_outer_h = float(geo.get('outer_h_px', 0) or 0)

        # —— Step 1: 用 debug['cuts_cm'] 建立 corner → cm 值的查找表（最高优先级） ——
        cm_by_corner = {}
        cuts_cm_list = debug.get('cuts_cm', []) if isinstance(debug, dict) else []
        if not isinstance(cuts_cm_list, list):
            cuts_cm_list = []
        for item in cuts_cm_list:
            if not isinstance(item, dict):
                continue
            corner = str(item.get('corner', '')).lower()
            if corner in {'tl', 'tr', 'bl', 'br'}:
                w = float(item.get('cut_w_cm', 0.0) or 0.0)
                h = float(item.get('cut_h_cm', 0.0) or 0.0)
                if w > 0 or h > 0:
                    cm_by_corner[corner] = (w, h)

        # —— Step 2: 用 all_corners 建立 corner → px 值的查找表（用于 fallback） ——
        px_by_corner = {}
        candidates = debug.get('all_corners', []) if isinstance(debug, dict) else []
        if not candidates and isinstance(geo, dict):
            candidates = geo.get('all_corners', [])
        if isinstance(candidates, list):
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                corner = str(item.get('corner', '')).lower()
                if corner in {'tl', 'tr', 'bl', 'br'}:
                    px_by_corner[corner] = (
                        float(item.get('cut_w_px', 0.0) or 0.0),
                        float(item.get('cut_h_px', 0.0) or 0.0),
                    )

        # —— Step 3: 合并出最终候选，优先 cm，fallback 像素比例 ——
        unique = []
        seen = set()
        # cuts_cm 优先（按 OCR 归属顺序），all_corners 补充
        for corner in list(cm_by_corner.keys()) + list(px_by_corner.keys()):
            if corner in seen:
                continue
            seen.add(corner)

            cut_w_cm, cut_h_cm = 0.0, 0.0

            # 优先用 OCR 真值
            if corner in cm_by_corner:
                cut_w_cm, cut_h_cm = cm_by_corner[corner]
            else:
                # fallback: 像素比例反推
                if corner in px_by_corner:
                    cw_px, ch_px = px_by_corner[corner]
                    if cw_px > 0 and px_outer_w > 0 and outer_w_cm > 0:
                        cut_w_cm = outer_w_cm * (cw_px / px_outer_w)
                    if ch_px > 0 and px_outer_h > 0 and outer_h_cm > 0:
                        cut_h_cm = outer_h_cm * (ch_px / px_outer_h)

            unique.append({
                'corner': corner,
                'cut_w_cm': max(0.0, float(cut_w_cm)),
                'cut_h_cm': max(0.0, float(cut_h_cm)),
            })
        return unique[:4]

    def _apply_lshape_params(self, corner: str, cut_w_cm: float, cut_h_cm: float, result):
        """L 形挖角参数应用：保存参数 → 回填 UI（含外框 SpinBox）→ 状态栏内联摘要
        → 发出 lshape_applied 信号给 PropertyPanel（切换到 rect_lshape 模式 + 同步画布 + 预览）。

        [2026-09-03 UI 简化] 调用方不再是旧 QDialog 的「确认并生成」按钮，而是：
          - Worker 成功直接 auto-apply（_on_lshape_parsed）
          - 未来其他程序化回填路径
        因此参数来源使用统一的 `_params_source='recognize'` 标记。

        [V2.2 Phase 4] 阶梯场景：result.debug['pattern'] == 'single_edge_stepped' 时
        走 set_cut_rects() 路径，跳过标准 4 行 set_lshape_cuts()。
        """
        debug = getattr(result, 'debug', {}) or {}
        is_staircase = debug.get('pattern') == 'single_edge_stepped'
        stair_cut_rects = debug.get('cuts_cm', []) if is_staircase else []

        self._lshape_params = {
            'corner': corner,
            'cut_w_cm': max(0.0, cut_w_cm),
            'cut_h_cm': max(0.0, cut_h_cm),
            'outer_w_cm': max(0.0, float(result.outer_w_cm or 0)),
            'outer_h_cm': max(0.0, float(result.outer_h_cm or 0)),
            'cuts_cm': [],
        }
        if is_staircase and stair_cut_rects:
            self._lshape_params['cut_rects'] = stair_cut_rects
            self.set_cut_rects(stair_cut_rects)
        else:
            suggestions = self._extract_multicorner_suggestions(result)
            if suggestions:
                self.set_lshape_cuts(suggestions)
        _corner_label = {
            'tl': '左上角', 'tr': '右上角', 'bl': '左下角', 'br': '右下角',
        }.get(corner, corner or '未知')
        try:
            self._sp_outer_w.blockSignals(True)
            self._sp_outer_h.blockSignals(True)
            try:
                _TRIM = CUT_LOSS_CM
                if result.outer_w_cm > 0:
                    self._sp_outer_w.setValue(max(0.0, float(result.outer_w_cm) + _TRIM))
                if result.outer_h_cm > 0:
                    self._sp_outer_h.setValue(max(0.0, float(result.outer_h_cm) + _TRIM))
            finally:
                self._sp_outer_w.blockSignals(False)
                self._sp_outer_h.blockSignals(False)
            if not is_staircase:
                self._cb_lcorner.blockSignals(True)
                self._sp_lw.blockSignals(True)
                self._sp_lh.blockSignals(True)
                try:
                    ci = self._cb_lcorner.findData(corner)
                    if ci >= 0:
                        self._cb_lcorner.setCurrentIndex(ci)
                    self._sp_lw.setValue(max(0.0, cut_w_cm))
                    self._sp_lh.setValue(max(0.0, cut_h_cm))
                finally:
                    self._cb_lcorner.blockSignals(False)
                    self._sp_lw.blockSignals(False)
                    self._sp_lh.blockSignals(False)
                self._lshape_params['cuts_cm'] = self.get_cuts_cm()
            self._params_source = 'recognize'
            # 画布尺寸 = 外框设计值 + 1cm 损耗
            _canvas_w = float(result.outer_w_cm or 0) + 1.0
            _canvas_h = float(result.outer_h_cm or 0) + 1.0
            lines = [
                f"✅ 成功！",
                f"画布：{_canvas_w:.1f} × {_canvas_h:.1f} cm",
            ]
            if is_staircase and stair_cut_rects:
                lines.append(
                    f"单边阶梯挖角：corner={corner}，{len(stair_cut_rects)} 级阶梯")
                for i, cr in enumerate(stair_cut_rects):
                    lines.append(
                        f"  L{i+1}: {cr.get('w_cm', 0):.1f}×{cr.get('h_cm', 0):.1f}cm "
                        f"(Δx={cr.get('offset_x_cm', 0):.1f}, Δy={cr.get('offset_y_cm', 0):.1f})")
            else:
                lines.append(
                    f"L形挖角：corner={corner}，挖角 {cut_w_cm:.1f} × {cut_h_cm:.1f} cm")
            lines.extend([
                f"外框尺寸：{result.outer_w_cm:.1f} × {result.outer_h_cm:.1f} cm"
                f"（画布含 1cm 裁剪损耗）",
            ])
            if not is_staircase:
                lines.append(
                    f"L形草图识别：corner={corner}，挖角 {cut_w_cm:.1f} × {cut_h_cm:.1f} cm")
            consistency = float(getattr(result, 'self_consistency', 0.0) or 0.0)
            suggestions = self._extract_multicorner_suggestions(result)
            low_confidence = consistency < 0.75
            self._gb_lshape_recog.setProperty('lowConfidence', low_confidence)
            self._gb_lshape_recog.style().unpolish(self._gb_lshape_recog)
            self._gb_lshape_recog.style().polish(self._gb_lshape_recog)
            if low_confidence:
                lines.append(
                    f"⚠️ G2 置信度偏低：结构自洽度 {consistency * 100:.0f}%（阈值 75%），"
                    "请人工核对挖角位置与尺寸")
            if not is_staircase and len(suggestions) > 1:
                summary = '、'.join(
                    f"{item['corner']} {item['cut_w_cm']:.1f}×{item['cut_h_cm']:.1f}cm"
                    for item in suggestions)
                lines.append(f"🔎 G3 角位核对：{summary}")
            self._set_status("\n".join(lines))
            # 切换模式 + 同步画布尺寸（单一入口：PropertyPanel._on_lshape_applied）
            self.lshape_applied.emit(self._lshape_params)
            # ===== [2026-09-03 一键化] 识别成功 → 自动启动素材库匹配 + 完整预览渲染 =====
            # 等价于用户手动点 L 形面板「生成预览」按钮：
            #   generate_requested → PropertyPanel._lshape_run_generate →
            #   _pool_run_generate(source='lshape', target_name_override=<面板当前target>)
            # Safety：内部有 isRunning 去重 + target 空值早返回，不会重复启动 / 崩溃。
            self.generate_requested.emit()
        except Exception as e:
            logger.exception(f"[LShapePanel] _apply_lshape_params 异常: {e}")
            self._set_status(f"L 形参数回填异常：{e}", is_error=True)

    # ====================================================================
    # 外部访问 API（供 PropertyPanel 调用）
    # ====================================================================
    def get_corner(self) -> str:
        """读取挖角位置（阶梯模式从 _stair_corner 读取）。"""
        if self._staircase_mode:
            return self._stair_corner.currentData() or 'tr'
        return self._cb_lcorner.currentData()

    def get_cut_w_cm(self) -> float:
        """读取挖角宽度（阶梯模式取第一级宽）。"""
        if self._staircase_mode and self._stair_rows:
            return self._stair_rows[0][2].value()
        return self._sp_lw.value()

    def get_cut_h_cm(self) -> float:
        """读取挖角高度（阶梯模式取第一级高）。"""
        if self._staircase_mode and self._stair_rows:
            return self._stair_rows[0][3].value()
        return self._sp_lh.value()

    def get_cuts_cm(self) -> list[dict]:
        """返回启用的挖角列表，最多四个；未填写尺寸的行不写入设计。

        阶梯模式下：从 CutRect 子行转换为旧格式 {corner, cut_w_cm, cut_h_cm}。
        """
        if self._staircase_mode:
            cuts = []
            anchor = self._stair_corner.currentData() or 'tr'
            for ox_sp, oy_sp, w_sp, h_sp, _ in self._stair_rows:
                if w_sp.value() <= 0 or h_sp.value() <= 0:
                    continue
                cuts.append({
                    'corner': anchor,
                    'cut_w_cm': w_sp.value(),
                    'cut_h_cm': h_sp.value(),
                })
            return cuts[:4]
        cuts = []
        for enabled, combo, width, height in self._corner_rows:
            if not enabled.isChecked() or width.value() <= 0 or height.value() <= 0:
                continue
            cuts.append({
                'corner': combo.currentData(),
                'cut_w_cm': width.value(),
                'cut_h_cm': height.value(),
            })
        return cuts[:4]

    def get_lshape_params(self):
        """读取 _lshape_params"""
        return self._lshape_params

    def clear_lshape_params(self):
        """清除 L 形参数（草图被清除时调用）。"""
        self._lshape_params = None
        if self._staircase_mode:
            self._set_staircase_mode(False)
        # SpinBox 重置为最小画布值（5cm = 设计值 4cm + 1cm 损耗，clip 到 5cm）
        self._sp_outer_w.blockSignals(True)
        self._sp_outer_h.blockSignals(True)
        try:
            self._sp_outer_w.setValue(5.0)
            self._sp_outer_h.setValue(5.0)
        finally:
            self._sp_outer_w.blockSignals(False)
            self._sp_outer_h.blockSignals(False)

    def set_lshape_params(self, corner: str, cut_w_cm: float, cut_h_cm: float):
        """外部回填 L 形参数（blockSignals 避免触发预览）。

        同时同步 `_lshape_params` dict，确保 Worker 下次读取时拿到回填后的值，
        而非回填前用户手动编辑的旧值。
        """
        self._cb_lcorner.blockSignals(True)
        self._sp_lw.blockSignals(True)
        self._sp_lh.blockSignals(True)
        try:
            ci = self._cb_lcorner.findData(corner)
            if ci >= 0:
                self._cb_lcorner.setCurrentIndex(ci)
            self._sp_lw.setValue(max(0.0, float(cut_w_cm)))
            self._sp_lh.setValue(max(0.0, float(cut_h_cm)))
            self._corner_rows[0][0].setChecked(True)
            for row in self._corner_rows[1:]:
                row[0].setChecked(False)
        finally:
            self._cb_lcorner.blockSignals(False)
            self._sp_lw.blockSignals(False)
            self._sp_lh.blockSignals(False)
        self._lshape_params['corner'] = corner
        self._lshape_params['cut_w_cm'] = max(0.0, float(cut_w_cm))
        self._lshape_params['cut_h_cm'] = max(0.0, float(cut_h_cm))
        self._lshape_params['cuts_cm'] = self.get_cuts_cm()

    def set_lshape_cuts(self, cuts: list[dict] | None):
        """回填多角参数；空列表回退到旧单角控件。"""
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
        self._lshape_params['cuts_cm'] = self.get_cuts_cm()

    def set_outer_dims(self, outer_w_cm: float, outer_h_cm: float):
        """外部回填外框设计真值到 SpinBox（设计值 + 1cm = 画布值）。

        供 PropertyPanel（Worker 回填 / 画布 SpinBox 同步）调用。
        """
        _TRIM = CUT_LOSS_CM
        canvas_w = max(5.0, max(0.0, float(outer_w_cm)) + _TRIM)
        canvas_h = max(5.0, max(0.0, float(outer_h_cm)) + _TRIM)
        self._sp_outer_w.blockSignals(True)
        self._sp_outer_h.blockSignals(True)
        try:
            self._sp_outer_w.setValue(canvas_w)
            self._sp_outer_h.setValue(canvas_h)
        finally:
            self._sp_outer_w.blockSignals(False)
            self._sp_outer_h.blockSignals(False)
        # 同步到 _lshape_params dict（如果存在）
        if self._lshape_params is not None:
            self._lshape_params['outer_w_cm'] = max(0.0, float(outer_w_cm))
            self._lshape_params['outer_h_cm'] = max(0.0, float(outer_h_cm))

    def get_outer_w_cm(self) -> float:
        """读取设计外框宽度（SpinBox画布值 - 1cm损耗）"""
        return max(0.0, self._sp_outer_w.value() - 1.0)

    def get_outer_h_cm(self) -> float:
        """读取设计外框高度（SpinBox画布值 - 1cm损耗）"""
        return max(0.0, self._sp_outer_h.value() - 1.0)

    def cancel_running_parse(self):
        """取消正在运行的 L 形解析"""
        if self._lshape_parse_worker is not None and self._lshape_parse_worker.isRunning():
            try:
                self._lshape_parse_worker.requestInterruption()
                if not self._lshape_parse_worker.wait(2000):
                    # [Fix N-P0-02] 超时未结束则 finished→deleteLater 兜底
                    try:
                        self._lshape_parse_worker.finished.connect(self._lshape_parse_worker.deleteLater)
                    except TypeError:
                        self._lshape_parse_worker.deleteLater()
                else:
                    self._lshape_parse_worker.deleteLater()
            except Exception:
                pass
        self._lshape_parse_worker = None

    def shutdown(self):
        """[Fix N-P0-02] 退役 LShapePanel 持有的后台线程，避免主窗口关闭时析构 running QThread。"""
        if getattr(self, '_lshape_parse_worker', None) is not None:
            old = self._lshape_parse_worker
            self._lshape_parse_worker = None
            if old.isRunning():
                old.requestInterruption()
                try:
                    old.finished.connect(old.deleteLater)
                except TypeError:
                    old.deleteLater()
            else:
                old.deleteLater()

    # ====================================================================
    # 目标文件名历史记录（独立于水池设计器，使用 TARGET_SRC_LSHAPE）
    # ====================================================================
    def _refresh_target_history_ui(self):
        """刷新本面板目标文件名历史菜单：按日期分组显示最近 3 天记录。

        与 cropper_panel.py / property_panel_poolbox.py 同构，但 source=TARGET_SRC_LSHAPE，
        实现物理隔离：L 形挖角面板只显示在本面板输入过的文件名历史。
        """
        self._target_history_menu.clear()
        history = self._app_settings.get_target_name_history(self._app_settings.TARGET_SRC_LSHAPE)
        if not history:
            a_empty = QAction("（暂无历史记录）", self._target_history_menu)
            a_empty.setEnabled(False)
            self._target_history_menu.addAction(a_empty)
            return

        today_iso = date.today().isoformat()
        yesterday_iso = (date.today() - timedelta(days=1)).isoformat()
        day_before_iso = (date.today() - timedelta(days=2)).isoformat()
        date_label = {
            today_iso: "今天",
            yesterday_iso: "昨天",
            day_before_iso: "前天",
        }

        for date_str, items in history.items():
            label = date_label.get(date_str, date_str)
            sub = QAction(f"—— {label}（{date_str}）——", self._target_history_menu)
            sub.setEnabled(False)
            self._target_history_menu.addAction(sub)
            for r in items:
                name = r.get("name", "")
                ts = r.get("timestamp", 0)
                time_str = datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "--:--"
                disp = name if len(name) <= 60 else (name[:57] + "…")
                a = QAction(f"{time_str}  {disp}", self._target_history_menu)
                a.setToolTip(name)
                a.setData(name)
                a.triggered.connect(lambda _=False, n=name: self._apply_target_from_history(n))
                self._target_history_menu.addAction(a)
            a_clear_day = QAction(f"  清空 {label} 的记录", self._target_history_menu)
            a_clear_day.setData(date_str)
            a_clear_day.triggered.connect(
                lambda _=False, d=date_str: self._clear_target_history_by_date(d))
            self._target_history_menu.addAction(a_clear_day)
            self._target_history_menu.addSeparator()

        a_clear = QAction("清空全部历史记录", self._target_history_menu)
        a_clear.triggered.connect(self._clear_target_history)
        self._target_history_menu.addAction(a_clear)

    def _apply_target_from_history(self, name: str):
        """从历史菜单选中目标文件名 → 回填到本面板输入框 + 触发 target_changed。

        本面板自行处理（不再委托 PropertyPanel），保证历史记录选中后
        走本面板的统一 textChanged → target_changed 链路。
        """
        if not name:
            return
        self._target_edit.setText(name)
        self._target_edit.setFocus()
        self._target_edit.setCursorPosition(len(name))

    def _clear_target_history(self):
        """清空全部目标文件名历史记录（仅 L 形挖角设计面板）"""
        self._app_settings.clear_target_name_history(self._app_settings.TARGET_SRC_LSHAPE)
        self._refresh_target_history_ui()

    def _clear_target_history_by_date(self, date_str: str):
        """清空指定日期的目标文件名历史记录（仅 L 形挖角设计面板）"""
        self._app_settings.clear_target_name_history_by_date(self._app_settings.TARGET_SRC_LSHAPE, date_str)
        self._refresh_target_history_ui()

    def _record_target_name_history(self):
        """记录当前目标文件名到本面板历史（生成成功后调用）。

        使用 TARGET_SRC_LSHAPE source，与水池设计器（TARGET_SRC_POOL）物理隔离，
        互不干扰。
        """
        name = self._target_edit.text().strip()
        if name:
            self._app_settings.add_target_name_history(name, self._app_settings.TARGET_SRC_LSHAPE)
            self._refresh_target_history_ui()

    # ====================================================================
    # 状态提示
    # ====================================================================
    def _set_status(self, msg: str, is_error: bool = False):
        color = "#B00020" if is_error else "#388E3C"
        self._lshape_status.setText(msg)
        self._lshape_status.setStyleSheet(
            f"color:{color}; padding:4px 6px; background: {'#FFEBEE' if is_error else '#E8F5E9'};"
            " border-radius: 4px;")
