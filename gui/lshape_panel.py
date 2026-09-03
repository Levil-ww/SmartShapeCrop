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
  4) PropertyPanel 通过 set_lshape_params() / sync_sketch_to_lshape() /
     sync_target_to_lshape() 回填本面板 UI，实现双向同步；
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
    QDoubleSpinBox, QComboBox, QPushButton, QScrollArea, QMessageBox,
    QLineEdit, QToolButton, QMenu, QAction, QFileDialog,
)

from core.app_settings import get_app_settings
from .property_panel_widgets import _SketchDropLabel
from .property_panel_workers import _LShapeParseWorker
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
        lshape_params_changed()     —— 用户改动挖角参数（替代原 _apply_quiet）
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
        self._gb_lshape_recog = QGroupBox("✂️ L 形挖角识别")
        self._gb_lshape_recog.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 2px solid #E6A23C; "
            "border-radius: 6px; margin-top: 14px; padding-top: 8px; }"
            "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; "
            "left: 14px; top: 0px; padding: 0 6px; color: #B26A00; }")
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

        # ===== 4) L 形参数 GroupBox =====
        self._gb_l = QGroupBox("📐 L 形挖角参数")
        self._gb_l.setStyleSheet(self._param_group_style("#5B6CFF"))
        fl = QVBoxLayout(self._gb_l)
        fl.setSpacing(6)
        self._cb_lcorner = QComboBox()
        self._cb_lcorner.addItem("左上角", "tl")
        self._cb_lcorner.addItem("右上角", "tr")
        self._cb_lcorner.addItem("左下角", "bl")
        self._cb_lcorner.addItem("右下角", "br")
        self._sp_lw = self._dspin(0, 450, 0.0)
        self._sp_lh = self._dspin(0, 450, 0.0)
        fl.addLayout(self._row("挖角位置", self._cb_lcorner))
        fl.addLayout(self._row("挖角宽度(cm)", self._sp_lw))
        fl.addLayout(self._row("挖角高度(cm)", self._sp_lh))
        self._inner_layout.addWidget(self._gb_l)

        # ===== 5) 外框尺寸（画布值，与水池设计器画布尺寸联动） =====
        # 语义：SpinBox 显示 = 画布值 = 设计外框 + 1cm 损耗
        #   - 用户改 SpinBox 画布值（如 144.0）
        #   → _on_param_changed 回写 dict['outer_w_cm'] = 144 - 1 = 143.0（设计真值）
        #   → PropertyPanel 桥接层同步 _pool_raw_outer_w = 143，_sp_w = 144
        # 与水池设计器 _sp_w/_sp_h（画布值）语义完全一致。
        self._gb_outer = QGroupBox("📐 外框尺寸（cm）")
        self._gb_outer.setStyleSheet(self._param_group_style("#5B6CFF"))
        fo = QVBoxLayout(self._gb_outer)
        fo.setSpacing(6)
        self._sp_outer_w = self._dspin(5, 500, 5.0)
        self._sp_outer_h = self._dspin(5, 500, 5.0)
        fo.addLayout(self._row("宽(cm)", self._sp_outer_w))
        fo.addLayout(self._row("高(cm)", self._sp_outer_h))
        self._inner_layout.addWidget(self._gb_outer)

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
        self._cb_lcorner.currentIndexChanged.connect(self._on_param_changed)
        self._sp_lw.valueChanged.connect(self._on_param_changed)
        self._sp_lh.valueChanged.connect(self._on_param_changed)
        # 外框尺寸变化也触发即时预览（与水池设计器画布尺寸联动）
        self._sp_outer_w.valueChanged.connect(self._on_param_changed)
        self._sp_outer_h.valueChanged.connect(self._on_param_changed)

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
            " border-radius: 6px; margin-top: 12px; padding-top: 8px;"
            f" background: #FBFCFF; }} "
            "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;"
            " left: 10px; top: 0px; padding: 0 6px;"
            f" color: {accent}; background: #FBFCFF; }}")

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
        _TRIM = 1.0
        # 外框：SpinBox 画布值 → dict 设计值
        canvas_outer_w = max(0.0, self._sp_outer_w.value())
        canvas_outer_h = max(0.0, self._sp_outer_h.value())
        design_outer_w = max(0.0, canvas_outer_w - _TRIM)
        design_outer_h = max(0.0, canvas_outer_h - _TRIM)
        if self._lshape_params is None:
            self._lshape_params = {
                'corner': self._cb_lcorner.currentData(),
                'cut_w_cm': max(0.0, self._sp_lw.value()),
                'cut_h_cm': max(0.0, self._sp_lh.value()),
                'outer_w_cm': design_outer_w,
                'outer_h_cm': design_outer_h,
            }
        else:
            self._lshape_params['corner'] = self._cb_lcorner.currentData()
            self._lshape_params['cut_w_cm'] = max(0.0, self._sp_lw.value())
            self._lshape_params['cut_h_cm'] = max(0.0, self._sp_lh.value())
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
        self.lshape_params_changed.emit()

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
        if self._lshape_parse_worker is not None and self._lshape_parse_worker.isRunning():
            try:
                self._lshape_parse_worker.requestInterruption()
                self._lshape_parse_worker.wait(2000)
            except Exception:
                pass
        worker = _LShapeParseWorker(sketch_path, raw_w, raw_h, self)
        worker.finished_ok.connect(self._on_lshape_parsed)
        worker.finished_err.connect(self._on_lshape_parse_err)
        worker.finished.connect(self._on_lshape_worker_finished)
        self._lshape_parse_worker = worker
        self._set_status("正在识别 L 形挖角（多尺度 OCR，约 10~20 秒）…")
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
            if not result.success:
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

    def _apply_lshape_params(self, corner: str, cut_w_cm: float, cut_h_cm: float, result):
        """L 形挖角参数应用：保存参数 → 回填 UI（含外框 SpinBox）→ 状态栏内联摘要
        → 发出 lshape_applied 信号给 PropertyPanel（切换到 rect_lshape 模式 + 同步画布 + 预览）。

        [2026-09-03 UI 简化] 调用方不再是旧 QDialog 的「确认并生成」按钮，而是：
          - Worker 成功直接 auto-apply（_on_lshape_parsed）
          - 未来其他程序化回填路径
        因此参数来源使用统一的 `_params_source='recognize'` 标记。
        """
        self._lshape_params = {
            'corner': corner,
            'cut_w_cm': max(0.0, cut_w_cm),
            'cut_h_cm': max(0.0, cut_h_cm),
            'outer_w_cm': max(0.0, float(result.outer_w_cm or 0)),
            'outer_h_cm': max(0.0, float(result.outer_h_cm or 0)),
        }
        # corner code → 中文显示名（与 _cb_lcorner addItem 顺序一致，defensive 兜底未知）
        _corner_label = {
            'tl': '左上角', 'tr': '右上角', 'bl': '左下角', 'br': '右下角',
        }.get(corner, corner or '未知')
        try:
            # 1) L 形参数组回填（blockSignals 避免触发预览）
            self._cb_lcorner.blockSignals(True)
            self._sp_lw.blockSignals(True)
            self._sp_lh.blockSignals(True)
            self._sp_outer_w.blockSignals(True)
            self._sp_outer_h.blockSignals(True)
            try:
                ci = self._cb_lcorner.findData(corner)
                if ci >= 0:
                    self._cb_lcorner.setCurrentIndex(ci)
                self._sp_lw.setValue(max(0.0, cut_w_cm))
                self._sp_lh.setValue(max(0.0, cut_h_cm))
                # 外框尺寸回填到 SpinBox（设计值 + 1cm = 画布值）
                _TRIM = 1.0
                if result.outer_w_cm > 0:
                    self._sp_outer_w.setValue(max(0.0, float(result.outer_w_cm) + _TRIM))
                if result.outer_h_cm > 0:
                    self._sp_outer_h.setValue(max(0.0, float(result.outer_h_cm) + _TRIM))
            finally:
                self._cb_lcorner.blockSignals(False)
                self._sp_lw.blockSignals(False)
                self._sp_lh.blockSignals(False)
                self._sp_outer_w.blockSignals(False)
                self._sp_outer_h.blockSignals(False)
            # 3) 标记为识别值 + 状态栏内联摘要（信息密度 ≥ 旧弹窗蓝色摘要块）
            self._params_source = 'recognize'
            lines = [
                f"✅ 已识别到 L 形挖角草图：位置={_corner_label}（{corner}），"
                f"挖角 {cut_w_cm:.1f} × {cut_h_cm:.1f} cm，"
                f"外框 {result.outer_w_cm:.1f} × {result.outer_h_cm:.1f} cm"
            ]
            _sc = float(getattr(result, 'self_consistency', 0) or 0)
            if _sc > 0:
                lines.append(f"　结构自洽度：{_sc * 100:.0f}%")
            _msg = str(getattr(result, 'message', '') or '').strip()
            if _msg:
                lines.append(f"　{_msg}")
            lines.append("（可直接修改下方「挖角参数 / 外框尺寸」，改动后自动触发软重绘预览；或点「生成预览」全量刷新）")
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
        """读取挖角位置"""
        return self._cb_lcorner.currentData()

    def get_cut_w_cm(self) -> float:
        """读取挖角宽度"""
        return self._sp_lw.value()

    def get_cut_h_cm(self) -> float:
        """读取挖角高度"""
        return self._sp_lh.value()

    def get_lshape_params(self):
        """读取 _lshape_params"""
        return self._lshape_params

    def clear_lshape_params(self):
        """清除 L 形参数（草图被清除时调用）。"""
        self._lshape_params = None
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
        """外部回填 L 形参数（blockSignals 避免触发预览）。"""
        self._cb_lcorner.blockSignals(True)
        self._sp_lw.blockSignals(True)
        self._sp_lh.blockSignals(True)
        try:
            ci = self._cb_lcorner.findData(corner)
            if ci >= 0:
                self._cb_lcorner.setCurrentIndex(ci)
            self._sp_lw.setValue(max(0.0, float(cut_w_cm)))
            self._sp_lh.setValue(max(0.0, float(cut_h_cm)))
        finally:
            self._cb_lcorner.blockSignals(False)
            self._sp_lw.blockSignals(False)
            self._sp_lh.blockSignals(False)

    def set_outer_dims(self, outer_w_cm: float, outer_h_cm: float):
        """外部回填外框设计真值到 SpinBox（设计值 + 1cm = 画布值）。

        供 PropertyPanel（Worker 回填 / 画布 SpinBox 同步）调用。
        """
        _TRIM = 1.0
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
                self._lshape_parse_worker.wait(2000)
            except Exception:
                pass
        self._lshape_parse_worker = None

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
