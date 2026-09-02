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
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QDoubleSpinBox, QComboBox, QPushButton, QScrollArea, QMessageBox,
    QLineEdit, QToolButton, QMenu, QAction, QFileDialog,
)

from .property_panel_widgets import _SketchDropLabel
from .property_panel_workers import _LShapeParseWorker
from .property_panel_dialogs import _LShapeConfirmDialog

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
    target_history_pick = pyqtSignal(str)
    generate_requested = pyqtSignal()
    # —— L 形挖角原有信号 ——
    lshape_params_changed = pyqtSignal()
    lshape_applied = pyqtSignal(dict)
    lshape_recognize_started = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # —— L 形挖角参数（确认后写入：corner/cut_w_cm/cut_h_cm/outer_w_cm/outer_h_cm）——
        self._lshape_params = None
        self._lshape_parse_worker = None  # type: _LShapeParseWorker | None
        # —— 防止 target_changed 信号在 PropertyPanel 回填时触发递归 ——
        self._block_target_signal = False
        self._build_ui()

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
        self._gb_l = QGroupBox("L形挖角参数")
        fl = QVBoxLayout(self._gb_l)
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

        # ===== 5) 外框尺寸只读展示 =====
        self._gb_outer_info = QGroupBox("外框尺寸（识别结果，只读）")
        fo = QVBoxLayout(self._gb_outer_info)
        self._lbl_outer = QLabel("（尚未识别）")
        self._lbl_outer.setStyleSheet("font-weight:bold;font-size:14px;color:#555;")
        fo.addWidget(self._lbl_outer)
        self._inner_layout.addWidget(self._gb_outer_info)

        # ===== 6) 一键生成预览 =====
        self._btn_generate = QPushButton("🔍 匹配模板 → 解析草图 → 生成预览")
        self._btn_generate.setStyleSheet(
            "QPushButton { padding: 10px 12px; font-weight: bold; font-size: 14px;"
            " background: #4A90E2; color: white; border: none; border-radius: 5px; }"
            "QPushButton:hover { background: #357ABD; }"
            "QPushButton:disabled { background: #A0BFE0; color: #eee; }")
        self._btn_generate.clicked.connect(self.generate_requested.emit)
        self._inner_layout.addWidget(self._btn_generate)

        self._inner_layout.addStretch(1)

        # 连接参数变化信号（即时预览）
        self._cb_lcorner.currentIndexChanged.connect(self._on_param_changed)
        self._sp_lw.valueChanged.connect(self._on_param_changed)
        self._sp_lh.valueChanged.connect(self._on_param_changed)

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

    # ====================================================================
    # 目标文件名处理
    # ====================================================================
    def _on_target_text_changed(self, text: str):
        """用户修改目标文件名 → 去抖后发信号给 PropertyPanel。

        block_target_signal 保护 PropertyPanel 回填时不触发递归。
        """
        if self._block_target_signal:
            return
        self.target_changed.emit(text)

    def sync_target_from_panel(self, name: str):
        """PropertyPanel 回填目标文件名（双向同步：水池设计器 → L形挖角设计）。

        block_target_signal 避免触发 target_changed → _on_pool_target_changed 递归。
        """
        self._block_target_signal = True
        try:
            if self._target_edit.text() != name:
                self._target_edit.setText(name)
        finally:
            self._block_target_signal = False

    def get_target_text(self) -> str:
        """读取当前目标文件名（供 PropertyPanel 读取回填前的值）。"""
        return self._target_edit.text().strip()

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
        """挖角参数变化 → 更新 _lshape_params + 发信号给 PropertyPanel 触发预览。"""
        if self._lshape_params is None:
            self._lshape_params = {
                'corner': self._cb_lcorner.currentData(),
                'cut_w_cm': max(0.0, self._sp_lw.value()),
                'cut_h_cm': max(0.0, self._sp_lh.value()),
                'outer_w_cm': 0.0,
                'outer_h_cm': 0.0,
            }
        else:
            self._lshape_params['corner'] = self._cb_lcorner.currentData()
            self._lshape_params['cut_w_cm'] = max(0.0, self._sp_lw.value())
            self._lshape_params['cut_h_cm'] = max(0.0, self._sp_lh.value())
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
        """L 形解析完成：成功 → 弹确认框；非 L 形 → 提示并保留矩形结果。"""
        try:
            if self.sender() is not self._lshape_parse_worker:
                logger.info("[LShapePanel] 忽略已过期的 L 形解析结果")
                return
            if not result.success:
                self._set_status(
                    f"ℹ️ L 形识别未成功（已按矩形解析处理）：{result.message}", is_error=False)
                return
            dlg = _LShapeConfirmDialog(result, self)
            if dlg.exec_():
                corner, cut_w_cm, cut_h_cm = dlg.values()
                self._apply_lshape_params(corner, cut_w_cm, cut_h_cm, result)
            else:
                self._set_status(
                    "已取消 L 形挖角（保留矩形解析结果，可点「识别L形挖角」重新识别）")
        except Exception as e:
            logger.exception(f"[LShapePanel] _on_lshape_parsed 异常: {e}")
            self._set_status(f"L 形识别回调异常：{e}", is_error=True)

    def _on_lshape_parse_err(self, err_msg: str):
        """L 形解析异常：忽略（矩形结果不受影响）"""
        logger.warning(f"[LShapePanel] L 形解析异常（忽略）: {err_msg}")
        self._set_status(f"L 形识别异常（已忽略，保留矩形结果）：{err_msg}")

    def _apply_lshape_params(self, corner: str, cut_w_cm: float, cut_h_cm: float, result):
        """用户确认 L 形挖角：保存参数 → 回填 UI → 发信号给 PropertyPanel。"""
        self._lshape_params = {
            'corner': corner,
            'cut_w_cm': max(0.0, cut_w_cm),
            'cut_h_cm': max(0.0, cut_h_cm),
            'outer_w_cm': max(0.0, float(result.outer_w_cm or 0)),
            'outer_h_cm': max(0.0, float(result.outer_h_cm or 0)),
        }
        try:
            # 1) L 形参数组回填（blockSignals 避免触发预览）
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
            # 2) 外框尺寸只读展示
            if result.outer_w_cm > 0 and result.outer_h_cm > 0:
                self._lbl_outer.setText(
                    f"{float(result.outer_w_cm):.1f} × {float(result.outer_h_cm):.1f} cm")
                self._lbl_outer.setStyleSheet("font-weight:bold;font-size:14px;color:#388E3C;")
            # 3) 发信号给 PropertyPanel
            self._set_status(
                f"✅ L 形挖角已确认：corner={corner}，挖角 {cut_w_cm:.1f} × {cut_h_cm:.1f} cm，"
                f"外框 {result.outer_w_cm:.1f} × {result.outer_h_cm:.1f} cm。\n"
                f"点击下方「匹配模板 → 解析草图 → 生成预览」完成素材匹配。")
            self.lshape_applied.emit(self._lshape_params)
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
        """清除 L 形参数（草图被清除时调用）"""
        self._lshape_params = None
        self._lbl_outer.setText("（尚未识别）")
        self._lbl_outer.setStyleSheet("font-weight:bold;font-size:14px;color:#555;")

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

    def cancel_running_parse(self):
        """取消正在运行的 L 形解析"""
        if self._lshape_parse_worker is not None and self._lshape_parse_worker.isRunning():
            try:
                self._lshape_parse_worker.requestInterruption()
                self._lshape_parse_worker.wait(2000)
            except Exception:
                pass
        self._lshape_parse_worker = None

    def set_history_menu(self, menu: QMenu):
        """PropertyPanel 注入目标文件名历史菜单（PropertyPanel 侧维护数据）。"""
        self._target_history_menu.clear()
        # 复制菜单项（QMenu 不能被两个 QToolButton 同时持有，所以复制一份）
        for action in menu.actions():
            # 创建新 Action，保持原文本与触发器
            new_action = QAction(action.text(), self._target_history_menu)
            if action.isSeparator():
                self._target_history_menu.addSeparator()
                continue
            new_action.triggered.connect(lambda checked=False, name=action.text(): self._on_history_pick(name))
            # 复制 submenu
            if action.menu():
                sub = QMenu(action.menu().title(), self._target_history_menu)
                for sub_action in action.menu().actions():
                    if sub_action.isSeparator():
                        sub.addSeparator()
                    else:
                        new_sub_action = QAction(sub_action.text(), sub)
                        new_sub_action.triggered.connect(
                            lambda checked=False, n=sub_action.text(): self._on_history_pick(n))
                        sub.addAction(new_sub_action)
                self._target_history_menu.addMenu(sub)
            else:
                self._target_history_menu.addAction(new_action)

    def _on_history_pick(self, name: str):
        """从历史菜单选中一条 → 委托 PropertyPanel 写入目标文件名。"""
        self.target_history_pick.emit(name)

    # ====================================================================
    # 状态提示
    # ====================================================================
    def _set_status(self, msg: str, is_error: bool = False):
        color = "#B00020" if is_error else "#388E3C"
        self._lshape_status.setText(msg)
        self._lshape_status.setStyleSheet(
            f"color:{color}; padding:4px 6px; background: {'#FFEBEE' if is_error else '#E8F5E9'};"
            " border-radius: 4px;")
