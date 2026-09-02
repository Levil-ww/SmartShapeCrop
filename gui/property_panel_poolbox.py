"""gui/property_panel 子模块 —— 水池模式 UI 与槽函数（_PoolBoxMixin，25 方法）（由 property_panel.py 拆分而来，facade 模式）。

原文件 gui/property_panel.py 保留为 facade（PropertyPanel 主类 + 编排），
本模块只包含 水池模式 UI 与槽函数（_PoolBoxMixin，25 方法） 相关的实现，逻辑与原文件完全一致。
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
from PyQt5.QtCore import QMimeData  # noqa: E402  (拖拽支持)
from PIL import Image

from core.geometry import CropDesign, BorderLayer, BorderText
from core.parser.name_parser import parse_filename
from core.parser.template_matcher import TemplateMatcher
from core.app_settings import get_app_settings
from core.pool_designer import validate_sketch_file
from core.pool_designer.sketch_parser import _SKETCH_ACCEPT_EXT, get_tesseract_status

logger = logging.getLogger(__name__)

from .property_panel_widgets import ColorButton, _SketchDropLabel
from .property_panel_workers import PoolRenderWorker, _SketchParseWorker, _WarmupScanWorker
from .property_panel_dialogs import _LayersDialog, _SketchViewerDialog

class _PoolBoxMixin:
    def _build_pool_box(self):
        gb = QGroupBox("🏊 智能水池模式（文件名匹配 + 草图解析 → 一键生成）")
        # 调整：提高 margin-top（原10→14）、title left（原12→14），避免标题与边框重叠
        gb.setStyleSheet("QGroupBox { font-weight: bold; border: 2px solid #4A90E2; "
                         "border-radius: 6px; margin-top: 14px; padding-top: 8px; }"
                         "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; "
                         "left: 14px; top: 0px; padding: 0 6px; color: #4A90E2; }")
        f = QVBoxLayout(gb)
        f.setSpacing(6)

        # A) 模板库目录（可编辑 ComboBox + 历史记录下拉 + 浏览）
        row_tpl = QHBoxLayout()
        row_tpl.addWidget(QLabel("模板库:"), 0)
        self._pool_tpl_dir = QComboBox()
        self._pool_tpl_dir.setEditable(True)
        self._pool_tpl_dir.setPlaceholderText("选择模板图的根目录（含完整矩形花纹图）")
        le_tpl = self._pool_tpl_dir.lineEdit()
        le_tpl.textChanged.connect(self._pool_on_template_dir_changed)
        self._pool_tpl_dir.currentIndexChanged.connect(self._pool_on_template_history_selected)
        row_tpl.addWidget(self._pool_tpl_dir, 1)

        # 历史记录下拉按钮
        self._pool_btn_tpl_history = QToolButton()
        self._pool_btn_tpl_history.setText("▾")
        self._pool_btn_tpl_history.setPopupMode(QToolButton.InstantPopup)
        self._pool_btn_tpl_history.setToolTip("历史记录")
        self._pool_tpl_history_menu = QMenu(self._pool_btn_tpl_history)
        self._pool_btn_tpl_history.setMenu(self._pool_tpl_history_menu)
        row_tpl.addWidget(self._pool_btn_tpl_history, 0)

        btn_tpl = QPushButton("选目录")
        btn_tpl.setFixedWidth(64)
        btn_tpl.clicked.connect(self._pool_pick_template_dir)
        row_tpl.addWidget(btn_tpl, 0)
        f.addLayout(row_tpl)

        # B) 目标文件名
        row_fn = QHBoxLayout()
        row_fn.addWidget(QLabel("目标文件:"), 0)
        self._pool_target = QLineEdit()
        self._pool_target.setPlaceholderText(
            "例：吸水皮革-定制-裁剪有图-克罗印花;60.5x133CM  （花型名+尺寸必须写）"
        )
        btn_fn1 = QPushButton("选文件")
        btn_fn1.setFixedWidth(64)
        btn_fn1.clicked.connect(self._pool_pick_target_file)
        btn_fn2 = QPushButton("清空")
        btn_fn2.setFixedWidth(48)
        btn_fn2.clicked.connect(lambda: self._pool_target.clear())
        # 目标文件名历史记录按钮（保留 3 天）
        self._pool_btn_target_history = QToolButton()
        self._pool_btn_target_history.setText("▾")
        self._pool_btn_target_history.setPopupMode(QToolButton.InstantPopup)
        self._pool_btn_target_history.setToolTip("目标文件名历史记录（保留3天）")
        self._pool_target_history_menu = QMenu(self._pool_btn_target_history)
        self._pool_btn_target_history.setMenu(self._pool_target_history_menu)
        row_fn.addWidget(self._pool_target, 1)
        row_fn.addWidget(btn_fn1, 0)
        row_fn.addWidget(btn_fn2, 0)
        row_fn.addWidget(self._pool_btn_target_history, 0)
        f.addLayout(row_fn)

        # C) 尺寸草图上传 + 缩略预览（支持拖拽 + 点击查看大图）
        row_sk = QHBoxLayout()
        self._pool_sk_preview = _SketchDropLabel("（未上传）\n或拖入图片")
        self._pool_sk_preview.fileDropped.connect(self._pool_load_sketch_from_path)  # 拖拽上传
        self._pool_sk_preview.clicked.connect(self._pool_view_sketch)               # 点击查看大图
        sk_btns = QVBoxLayout()
        btn_sk1 = QPushButton("上传草图…")
        btn_sk1.clicked.connect(self._pool_pick_sketch)
        btn_sk2 = QPushButton("清除草图")
        btn_sk2.clicked.connect(self._pool_clear_sketch)
        # ===== [L-Shape Panel Refactor 2026-09-02] L 形挖角识别按钮已迁移到 LShapePanel =====
        # 原 btn_sk3（✂️ 识别L形挖角）现位于独立的【L形挖角设计】面板；
        # 本面板仅保留上传/清除草图按钮，L 形识别由 LShapePanel 通过
        # lshape_recognize_started 信号回调本面板的 _on_lshape_recognize_started 触发。
        sk_btns.addWidget(btn_sk1)
        sk_btns.addWidget(btn_sk2)
        sk_desc = QLabel(
            "草图格式示例（红色线标注上下左右边距即可，\n"
            "自动识别失败时可在下方【内挖边距】手动调整）\n"
            "💡 支持：点击上传按钮 或 直接将图片拖入左侧框\n"
            "💡 L 形挖角识别请切到右侧【L形挖角设计】面板")
        sk_desc.setStyleSheet("color:#666;")
        sk_desc.setWordWrap(True)
        row_sk.addWidget(self._pool_sk_preview, 0)
        row_sk.addLayout(sk_btns, 0)
        row_sk.addWidget(sk_desc, 1)
        f.addLayout(row_sk)

        # D) 挖空方式
        row_mode = QHBoxLayout()
        self._pool_hole_mode = QComboBox()
        self._pool_hole_mode.addItem("✂️ 空白(挖去不留白)", "blank")
        self._pool_hole_mode.addItem("🖼 素材填充（花型匹配填充）", "image")
        self._pool_hole_mode.currentIndexChanged.connect(self._on_pool_hole_mode_change)
        row_mode.addWidget(QLabel("挖空方式:"), 0)
        row_mode.addWidget(self._pool_hole_mode, 1)
        f.addLayout(row_mode)

        # E) 匹配并生成 大按钮
        self._pool_btn_generate = QPushButton("🔍 匹配模板 → 解析草图 → 生成预览")
        self._pool_btn_generate.setStyleSheet(
            "QPushButton { padding: 10px 12px; font-weight: bold; font-size: 14px;"
            " background: #4A90E2; color: white; border: none; border-radius: 5px; }"
            "QPushButton:hover { background: #357ABD; }"
            "QPushButton:disabled { background: #A0BFE0; color: #eee; }")
        self._pool_btn_generate.clicked.connect(self._pool_run_generate)
        f.addWidget(self._pool_btn_generate)

        # F) 提示信息
        self._pool_status = QLabel("（填写以上参数后点击大按钮）")
        self._pool_status.setWordWrap(True)
        self._pool_status.setStyleSheet("color:#555; padding: 4px 6px;")
        f.addWidget(self._pool_status)

        # G) 输出文件名（默认与目标文件名同步，用于导出JPG命名）
        row_out = QHBoxLayout()
        row_out.addWidget(QLabel("输出文件名:"), 0)
        self._pool_output_name = QLineEdit()
        self._pool_output_name.setPlaceholderText("导出 JPG 时使用的文件名（不含扩展名），默认跟随【目标文件】")
        row_out.addWidget(self._pool_output_name, 1)
        btn_sync = QPushButton("同步目标名")
        btn_sync.setFixedWidth(80)
        btn_sync.clicked.connect(self._pool_sync_output_from_target)
        row_out.addWidget(btn_sync, 0)
        f.addLayout(row_out)

        self._inner_layout.addWidget(gb)

        # —— 连接目标文件名 textChanged：自动解析尺寸 + 同步输出名 ——
        self._pool_target.textChanged.connect(self._on_pool_target_changed)


    def _on_pool_hole_mode_change(self):
        mode = self._pool_hole_mode.currentData()
        # 只改 design.pool_hole_transparent 默认值；后续真正 apply 时 _collect 里再同步
        self._set_pool_status(f"挖空方式切换为：{self._pool_hole_mode.currentText()}")
        # 切换到空白模式：清理内挖素材路径，避免之前素材填充模式的残留值造成渲染混淆
        if mode == "blank":
            d = self.design
            if getattr(d, 'pool_inner_material_image', None) is not None:
                d.pool_inner_material_image = None
            if d.hole_bg_image is not None:
                d.hole_bg_image = None
                self._ed_hole_img.setText("")


    def _on_pool_target_changed(self, text: str):
        """目标文件名变更：1) 自动同步输出文件名；2) 解析尺寸回填到画布宽高；3) 若有草图则自动识别边距；4) 同步到 LShapePanel"""
        # 1) 默认同步输出文件名
        self._pool_sync_output_from_target()

        # 2) 解析尺寸并回填
        name = text.strip()
        if not name:
            # 空文本也要同步到 LShapePanel
            if self._lshape_panel is not None:
                self._lshape_panel.sync_target_from_panel("")
            return
        try:
            parsed = parse_filename(name)
            # 检测水池设计模式（裁剪有图）—— 统一由 ParsedFilename 判定
            is_pool = parsed.is_pool_mode()
            self._pool_mode = is_pool

            w, h = parsed.width_cm, parsed.height_cm
            if w > 0 and h > 0:
                if is_pool:
                    # —— 水池模式：外框(宽,高)直接使用解析结果 ——
                    # 例：58x121cm → width_cm=121, height_cm=58, raw_outer_w=121, raw_outer_h=58
                    # 画布显示尺寸 = 外框 + 1cm 损耗（裁剪余料）
                    raw_outer_w, raw_outer_h = parsed.width_cm, parsed.height_cm
                    gui_w = raw_outer_w + 1.0
                    gui_h = raw_outer_h + 1.0
                    # 范围夹取（上限 500：450cm 外框 + 1cm 损耗 = 451cm 画布）
                    gui_w = max(5.0, min(500.0, float(gui_w)))
                    gui_h = max(5.0, min(500.0, float(gui_h)))
                    raw_outer_w = max(5.0, min(500.0, float(raw_outer_w)))
                    raw_outer_h = max(5.0, min(500.0, float(raw_outer_h)))
                    # 保存原始外框尺寸（无损耗），供草图解析像素→厘米换算用
                    self._pool_raw_outer_w = raw_outer_w
                    self._pool_raw_outer_h = raw_outer_h
                    self._sp_w.setValue(gui_w)
                    self._sp_h.setValue(gui_h)
                    self._set_pool_status(
                        f"已自动识别尺寸：画布 {gui_w:.1f} × {gui_h:.1f} cm"
                        f"（含1cm损耗，目标外框 {raw_outer_w:.1f}×{raw_outer_h:.1f} cm，可在画布尺寸中微调）")
                else:
                    # 普通模式：直接使用解析结果，无 +1cm
                    w = max(5.0, min(500.0, float(w)))
                    h = max(5.0, min(500.0, float(h)))
                    self._pool_raw_outer_w = w
                    self._pool_raw_outer_h = h
                    self._sp_w.setValue(w)
                    self._sp_h.setValue(h)
                    self._set_pool_status(
                        f"已自动识别尺寸：{w:.1f} × {h:.1f} cm（可在画布尺寸中微调）")
                # 3) 如果有草图，自动解析边距
                if self._sketch_path and os.path.isfile(self._sketch_path):
                    self._pool_auto_parse_sketch()
        except Exception:
            pass
        # 4) 同步目标文件名到 LShapePanel（双向同步）
        if self._lshape_panel is not None:
            self._lshape_panel.sync_target_from_panel(text)


    def _pool_sync_output_from_target(self):
        """把目标文件名（去掉扩展名 + 尺寸后缀清理）同步到输出文件名框"""
        t = self._pool_target.text().strip()
        if not t:
            return
        # 去掉扩展名
        base, ext = os.path.splitext(t)
        if ext.lower() in {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.psd', '.psb', '.webp'}:
            t = base
        # 如果目标名有路径（少见），只取 basename
        t = os.path.basename(t)
        if t and t != self._pool_output_name.text():
            self._pool_output_name.setText(t)


    def _pool_pick_template_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择模板库目录")
        if d:
            self._pool_set_template_dir_ui(d)
            self._pool_on_template_dir_changed(d)
            # 记录到历史
            self._app_settings.add_template_history(d)
            self._pool_refresh_template_history_ui()
            self._set_pool_status(f"模板库目录已设置：{d}")


    def _pool_restore_last_template_dir(self):
        """启动时：恢复上次使用的模板库目录 + 填充历史记录下拉/菜单

        [Perf-Opt] 恢复目录后立即触发后台预热扫描（_WarmupScanWorker），
        让磁盘缓存加载和增量扫描在用户操作 UI 的时间内并行完成。
        磁盘缓存命中时预热 <1s 即结束；首次冷启动扫描时，用户在填写
        文件名/上传草图的几分钟里扫描也能并行完成。
        """
        self._pool_refresh_template_history_ui()
        last_dir = self._app_settings.get_default_template_dir()
        if last_dir and os.path.isdir(last_dir):
            self._pool_set_template_dir_ui(last_dir)
            self._pool_on_template_dir_changed(last_dir, warmup=True)

    def _pool_trigger_warmup(self, abs_dir: str):
        """启动后台预热扫描；若已有预热在跑则先等待其停止（避免竞争写 TemplateMatcher）"""
        # 如果已有生成任务在跑，不做预热——生成流程内部会先扫描
        if self._pool_worker is not None and self._pool_worker.isRunning():
            return
        # 已有预热在跑：目录相同就复用；目录不同先停掉旧的
        warmup = getattr(self, '_warmup_worker', None)
        if warmup is not None and warmup.isRunning():
            old_dir = getattr(warmup, '_template_dir', '')
            if os.path.abspath(old_dir) == abs_dir:
                return  # 同一个目录，不用重新预热
            warmup.quit()
            warmup.wait(2000)
            if warmup.isRunning():
                warmup.terminate()
                warmup.wait(1000)

        warmup = _WarmupScanWorker(self._matcher, abs_dir, parent=self)
        @warmup.finished_ok.connect
        def _on_ok(entry_count, dt):
            logger.info(f"[PoolBox] 预热扫描完成：{entry_count} 条目，耗时 {dt:.2f}s")
        @warmup.finished_err.connect
        def _on_err(msg):
            logger.warning(f"[PoolBox] 预热扫描出错：{msg}")
        self._warmup_worker = warmup
        warmup.start()

    def _pool_on_template_dir_changed(self, text: str, warmup: bool = True):
        """模板库目录变更时更新匹配引擎（只有目录真正不同才 set）。

        [Perf-Opt] warmup=True 时立即启动后台预热扫描线程，把等待前移到用户
        填写其他参数的空闲时间。
        """
        text = (text or "").strip()
        if not text or not os.path.isdir(text):
            return
        abs_dir = os.path.abspath(text)
        dir_changed = (self._matcher.get_template_dir() != abs_dir)
        if dir_changed:
            self._matcher.set_template_dir(abs_dir)
        if warmup:
            # 目录变更或仍无内存缓存 → 启动预热（目录相同但缓存为空也会触发，
            # 例：用户切到另一个历史目录后又切回，此时内部缓存已被清空）
            if dir_changed or not self._matcher._cache:
                self._pool_trigger_warmup(abs_dir)

    def _pool_refresh_template_history_ui(self):
        """刷新历史记录：ComboBox 下拉项 + 历史菜单"""
        history = self._app_settings.get_template_history()
        current_text = self._pool_tpl_dir.lineEdit().text()
        self._pool_tpl_dir.blockSignals(True)
        try:
            self._pool_tpl_dir.clear()
            for h in history:
                label = h.display_name
                if h.total_files:
                    label += f"  ({h.total_files} 张)"
                label += "    " + h.path
                self._pool_tpl_dir.addItem(label, h.path)
            self._pool_tpl_dir.lineEdit().setText(current_text)
        finally:
            self._pool_tpl_dir.blockSignals(False)

        # 历史菜单
        self._pool_tpl_history_menu.clear()
        if not history:
            a_empty = QAction("（暂无历史记录）", self._pool_tpl_history_menu)
            a_empty.setEnabled(False)
            self._pool_tpl_history_menu.addAction(a_empty)
        else:
            for h in history:
                label = h.display_name
                if h.total_files:
                    label += f"   ({h.total_files} 张图)"
                a = QAction(label, self._pool_tpl_history_menu)
                a.setToolTip(h.path)
                a.setData(h.path)
                a.triggered.connect(lambda _=False, p=h.path: self._pool_apply_template_dir_from_history(p))
                self._pool_tpl_history_menu.addAction(a)
            self._pool_tpl_history_menu.addSeparator()
            a_clear = QAction("清除历史记录", self._pool_tpl_history_menu)
            a_clear.triggered.connect(self._pool_clear_template_history)
            self._pool_tpl_history_menu.addAction(a_clear)


    def _pool_apply_template_dir_from_history(self, path: str):
        """从历史记录选中：写入 ComboBox 并同步 matcher"""
        self._pool_set_template_dir_ui(path)
        self._pool_on_template_dir_changed(path)
        self._app_settings.add_template_history(path)
        self._pool_refresh_template_history_ui()


    def _pool_set_template_dir_ui(self, path: str):
        """只改 UI 文本（不触发信号）"""
        le = self._pool_tpl_dir.lineEdit()
        le.blockSignals(True)
        try:
            le.setText(path)
        finally:
            le.blockSignals(False)


    def _pool_clear_template_history(self):
        """清空历史记录（仅菜单/下拉清空，当前目录文本保留）"""
        self._app_settings.clear_template_history()
        self._pool_refresh_template_history_ui()


    def _refresh_target_history_ui(self):
        """刷新目标文件名历史菜单：按日期分组显示最近 3 天记录（仅水池设计器的历史）"""
        self._pool_target_history_menu.clear()
        history = self._app_settings.get_target_name_history(self._app_settings.TARGET_SRC_POOL)
        if not history:
            a_empty = QAction("（暂无历史记录）", self._pool_target_history_menu)
            a_empty.setEnabled(False)
            self._pool_target_history_menu.addAction(a_empty)
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
            sub = QAction(f"—— {label}（{date_str}）——", self._pool_target_history_menu)
            sub.setEnabled(False)
            self._pool_target_history_menu.addAction(sub)
            for r in items:
                name = r.get("name", "")
                ts = r.get("timestamp", 0)
                time_str = datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "--:--"
                disp = name if len(name) <= 60 else (name[:57] + "…")
                a = QAction(f"{time_str}  {disp}", self._pool_target_history_menu)
                a.setToolTip(name)
                a.setData(name)
                a.triggered.connect(lambda _=False, n=name: self._pool_apply_target_from_history(n))
                self._pool_target_history_menu.addAction(a)
            a_clear_day = QAction(f"  清空 {label} 的记录", self._pool_target_history_menu)
            a_clear_day.setData(date_str)
            a_clear_day.triggered.connect(
                lambda _=False, d=date_str: self._pool_clear_target_history_by_date(d))
            self._pool_target_history_menu.addAction(a_clear_day)
            self._pool_target_history_menu.addSeparator()

        a_clear = QAction("清空全部历史记录", self._pool_target_history_menu)
        a_clear.triggered.connect(self._pool_clear_target_history)
        self._pool_target_history_menu.addAction(a_clear)


    def _pool_apply_target_from_history(self, name: str):
        """从历史菜单选中目标文件名，回填到目标文件输入框"""
        if not name:
            return
        self._pool_target.setText(name)
        self._pool_target.setFocus()
        self._pool_target.setCursorPosition(len(name))


    def _pool_clear_target_history(self):
        """清空全部目标文件名历史记录（仅水池设计器）"""
        self._app_settings.clear_target_name_history(self._app_settings.TARGET_SRC_POOL)
        self._refresh_target_history_ui()


    def _pool_clear_target_history_by_date(self, date_str: str):
        """清空指定日期的目标文件名历史记录（仅水池设计器）"""
        self._app_settings.clear_target_name_history_by_date(self._app_settings.TARGET_SRC_POOL, date_str)
        self._refresh_target_history_ui()


    def _pool_record_target_name_history(self):
        """记录当前目标文件名到历史（生成成功后调用，仅水池设计器）"""
        name = self._pool_target.text().strip()
        if name:
            self._app_settings.add_target_name_history(name, self._app_settings.TARGET_SRC_POOL)
            self._refresh_target_history_ui()


    def _pool_on_template_history_selected(self, idx: int):
        """用户从 ComboBox 下拉选了一条历史"""
        if idx < 0:
            return
        path = self._pool_tpl_dir.itemData(idx)
        if isinstance(path, str) and path:
            self._pool_set_template_dir_ui(path)
            self._pool_on_template_dir_changed(path)
            self._app_settings.add_template_history(path)
            self._pool_refresh_template_history_ui()


    def _pool_pick_target_file(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "选择目标文件（或任意文件，程序只用文件名解析）",
            "", "所有文件 (*.*);;JPG 图片 (*.jpg *.jpeg);;PNG 图片 (*.png)"
        )
        if p:
            # 只取文件名（不包含路径）做解析，和 CropperPanel 保持一致
            self._pool_target.setText(os.path.basename(p))
            self._set_pool_status(f"已选择目标文件名：{os.path.basename(p)}")


    def _pool_pick_sketch(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "选择尺寸草图",
            "", "图片 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)"
        )
        if not p:
            return
        self._pool_load_sketch_from_path(p)


    def _pool_load_sketch_from_path(self, p: str):
        """通用：加载草图路径 → 保存 + [立即] 显示缩略图与主画布 → [后台] 启动草图解析回填边距。

        关键：图片显示（QPixmap + 主画布 PIL）在主线程立即完成，不等待 OCR/几何检测；
        解析操作放到 _SketchParseWorker 后台线程，避免阻塞 Qt 重绘事件，保证"上传即显示"。
        """
        # 输入校验：在设置路径/启动后台解析前，拒绝超大/损坏/不支持格式（防 OCR 卡死/内存爆炸）
        ok, reason = validate_sketch_file(p)
        if not ok:
            QMessageBox.warning(self, "草图无法上传", reason)
            self._set_pool_status(f"草图上传被拒：{reason}")
            return
        self._sketch_path = p
        # —— 1) 立即显示：侧栏缩略图 ——
        pm = QPixmap(p)
        if not pm.isNull():
            self._pool_sk_preview.setPixmap(pm.scaled(
                self._pool_sk_preview.size(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self._pool_sk_preview.setStyleSheet(
                "QLabel { border: 1px solid #888; background:#fff; border-radius: 6px; }")
            self._pool_sk_preview.set_has_image(True)
        else:
            self._pool_sk_preview.clear()
            self._pool_sk_preview.setText("（预览失败）\n或拖入图片")
            self._pool_sk_preview.set_has_image(False)
        self._set_pool_status(f"已上传草图：{os.path.basename(p)}（正在识别尺寸…）")
        # —— 2) 立即显示：主画布大图（避免小缩略图悬浮在侧栏）——
        try:
            pil_img = Image.open(p)
            if pil_img.mode not in ('RGB', 'RGBA'):
                pil_img = pil_img.convert('RGB')
            self.sketch_loaded.emit(pil_img)
        except Exception as e:
            logger.warning(f"草图加载为 PIL Image 失败: {e}")
            self.sketch_loaded.emit(None)
        # —— 3) 后台异步解析草图（若目标尺寸已知）——
        # 矩形解析：上传即跑，快速回填 4 边距（现有行为）
        self._pool_auto_parse_sketch()
        # ===== [L-Shape Panel Refactor 2026-09-02] L 形解析委托给 LShapePanel =====
        # 原 _pool_try_lshape_parse 已迁移到 LShapePanel.try_lshape_parse；
        # 上传草图后自动尝试 L 形识别的行为保持一致：同步草图到 LShapePanel 缩略图 + 触发识别。
        if self._lshape_panel is not None:
            self._lshape_panel.sync_sketch_preview(p)
            self._lshape_panel.set_sketch_path_for_view(p)
            # 自动尝试 L 形解析（与原 _pool_load_sketch_from_path 末尾行为一致）
            self._on_lshape_recognize_started()


    def _pool_auto_parse_sketch(self):
        """当草图和目标尺寸都已知时，启动后台线程解析并回填边距 UI。

        草图像素→厘米换算参考：使用文件名解析出的「原始外框尺寸」（不含1cm损耗），
        因为草图上的外框标注与目标文件名尺寸一一对应，不包含裁剪余料。
        """
        if not self._sketch_path or not os.path.isfile(self._sketch_path):
            return
        # 优先使用「原始外框尺寸」（文件名解析得到的、无1cm损耗的尺寸）
        raw_w = getattr(self, '_pool_raw_outer_w', 0.0)
        raw_h = getattr(self, '_pool_raw_outer_h', 0.0)
        if raw_w > 0 and raw_h > 0:
            target_w, target_h = raw_w, raw_h
        else:
            # 回退：使用当前画布尺寸 SpinBox 的值
            target_w = self._sp_w.value()
            target_h = self._sp_h.value()
        if target_w <= 0 or target_h <= 0:
            self._set_pool_status("已上传草图，请先填写或选择目标尺寸以启用自动识别")
            return

        # —— 启动后台解析（立即返回，不阻塞 UI 重绘）——
        if self._sketch_parse_worker is not None and self._sketch_parse_worker.isRunning():
            # 取消前一次未完成的解析（避免旧结果覆盖新图）
            # requestInterruption() 对纯 run() 的 QThread 生效（quit() 仅对有事件循环的线程有效）
            try:
                self._sketch_parse_worker.requestInterruption()
                self._sketch_parse_worker.wait(2000)
            except Exception:
                pass
        worker = _SketchParseWorker(self._sketch_path, target_w, target_h, self)
        worker.finished_ok.connect(self._on_sketch_parsed)
        worker.finished_err.connect(self._on_sketch_parse_err)
        self._sketch_parse_worker = worker
        worker.start()


    def _on_sketch_parsed(self, result):
        """后台解析成功：回填边距 UI + 更新状态提示"""
        try:
            # 防御：忽略已被新解析取代的旧 worker 发来的结果（sender 不再是当前 worker）
            if self.sender() is not self._sketch_parse_worker:
                logger.info("[PropertyPanel] 忽略已过期的草图解析结果")
                return
            self._sketch_parse_result = result
            if result.success:
                # —— 回填 4 个边距（核心需求）——
                # [契约变更 2026-08-27] 水池模式下 UI 直接显示草图识别到的边距值，
                # 不再追加 +1cm 偏移（画布已含 1cm 损耗）。
                # 内挖矩形由 canvas - sum(margins) 自动推导。
                is_pool = getattr(self, '_pool_mode', False)
                mt_ui = max(0.0, result.margin_top_cm)
                mb_ui = max(0.0, result.margin_bottom_cm)
                ml_ui = max(0.0, result.margin_left_cm)
                mr_ui = max(0.0, result.margin_right_cm)
                self._sp_mt.blockSignals(True)
                self._sp_mb.blockSignals(True)
                self._sp_ml.blockSignals(True)
                self._sp_mr.blockSignals(True)
                try:
                    self._sp_mt.setValue(mt_ui)
                    self._sp_mb.setValue(mb_ui)
                    self._sp_ml.setValue(ml_ui)
                    self._sp_mr.setValue(mr_ui)
                    if not is_pool:
                        if result.outer_w_cm > 0:
                            self._sp_w.setValue(result.outer_w_cm)
                        if result.outer_h_cm > 0:
                            self._sp_h.setValue(result.outer_h_cm)
                finally:
                    self._sp_mt.blockSignals(False)
                    self._sp_mb.blockSignals(False)
                    self._sp_ml.blockSignals(False)
                    self._sp_mr.blockSignals(False)

                # ===== [MULTI-HOLE Add-On 2026-08-29] 草图解析完成后回填多洞 UI =====
                # 仅当 sketch_result.is_multi_hole=True 且 holes>=2 时，填多洞 SpinBox 并显示 GroupBox；
                # 单洞场景：调用 _hide_multi_hole_ui() → 视觉零影响，只 hide 本就隐藏的 GroupBox。
                try:
                    is_mh_result = (getattr(result, 'is_multi_hole', False)
                                    and hasattr(result, 'holes')
                                    and isinstance(result.holes, list)
                                    and len(result.holes) >= 2)
                    if is_mh_result:
                        # 从 sketch_result 构建一份"假的"画布相对 holes_cm（因为 PoolWorker 还没跑，
                        # design 还没最终确定），给用户预览多洞参数。值仅用于 UI 显示，不会触发渲染。
                        holes = result.holes
                        gaps = list(getattr(result, 'hole_gaps_cm', []) or [])
                        layout = getattr(result, 'layout_type', 'horizontal') or 'horizontal'
                        fake_holes_cm = []
                        ox_cm = self.design.outer_margin_cm
                        oy_cm = self.design.outer_margin_cm
                        mt = result.margin_top_cm
                        mb = result.margin_bottom_cm
                        ml = holes[0].margin_left_cm if holes else result.margin_left_cm
                        if layout == 'horizontal':
                            y_cm = oy_cm + mt
                            cursor_x = ox_cm + ml
                            for i, h in enumerate(holes):
                                if i > 0 and i - 1 < len(gaps):
                                    cursor_x += gaps[i - 1]
                                fake_holes_cm.append({
                                    'x_cm': cursor_x, 'y_cm': y_cm,
                                    'w_cm': max(0.0, h.w_cm), 'h_cm': max(0.0, h.h_cm),
                                })
                                cursor_x += h.w_cm
                        elif layout == 'vertical':
                            x_cm = ox_cm + ml
                            cursor_y = oy_cm + mt
                            for i, h in enumerate(holes):
                                if i > 0 and i - 1 < len(gaps):
                                    cursor_y += gaps[i - 1]
                                fake_holes_cm.append({
                                    'x_cm': x_cm, 'y_cm': cursor_y,
                                    'w_cm': max(0.0, h.w_cm), 'h_cm': max(0.0, h.h_cm),
                                })
                                cursor_y += h.h_cm
                        else:
                            y_cm = oy_cm + mt
                            cursor_x = ox_cm + ml
                            for i, h in enumerate(holes):
                                if i > 0 and i - 1 < len(gaps):
                                    cursor_x += gaps[i - 1]
                                fake_holes_cm.append({
                                    'x_cm': cursor_x, 'y_cm': y_cm,
                                    'w_cm': max(0.0, h.w_cm), 'h_cm': max(0.0, h.h_cm),
                                })
                                cursor_x += h.w_cm
                        self._fill_multi_hole_ui(fake_holes_cm, gaps, layout)
                    else:
                        self._hide_multi_hole_ui()
                except Exception as e:
                    import logging as _lgg2
                    _lgg2.getLogger(__name__).warning(f"[Multi-hole UI] 草图解析后回填多洞 UI 失败: {e}")

                # 构造状态消息（带 try/except 防护）
                try:
                    dir_vals = result.debug.get("direction_margins", {}) if isinstance(result.debug, dict) else {}

                    dir_info = ""
                    if dir_vals:
                        dir_mt = self._safe_dir_val2(dir_vals.get("margin_top", 0))
                        dir_mb = self._safe_dir_val2(dir_vals.get("margin_bottom", 0))
                        dir_ml = self._safe_dir_val2(dir_vals.get("margin_left", 0))
                        dir_mr = self._safe_dir_val2(dir_vals.get("margin_right", 0))
                        if any(v > 0 for v in [dir_mt, dir_mb, dir_ml, dir_mr]):
                            dir_info = (
                                f"\n  🔤 方向标注: 上{dir_mt:.1f}/下{dir_mb:.1f}/左{dir_ml:.1f}/右{dir_mr:.1f} cm"
                            )

                    # ===== [MULTI-HOLE Add-On 2026-08-29] 多洞状态栏（替换单洞"内挖/边距"行）=====
                    # 仅当 is_multi_hole=True 且 holes>=2 时，显示每洞尺寸 + 洞间距；
                    # 单洞场景保持原文本一字不变。
                    is_mh = (getattr(result, 'is_multi_hole', False)
                             and hasattr(result, 'holes')
                             and isinstance(result.holes, list)
                             and len(result.holes) >= 2)
                    if is_mh:
                        holes = result.holes
                        gaps = list(getattr(result, 'hole_gaps_cm', []) or [])
                        layout = getattr(result, 'layout_type', 'horizontal') or 'horizontal'
                        # ===== [MULTI-HOLE Add-On 2026-08-29] 防御性过滤：仅 w>0 or h>0 才打印 =====
                        valid_holes = [h for h in holes if (float(getattr(h,'w_cm',0))>0) or (float(getattr(h,'h_cm',0))>0)]
                        valid_gaps = gaps[:max(0,len(valid_holes)-1)]
                        # ===== [PER-HOLE Add-On] 多洞每洞独立 mt/mb/ml/mr 显示 =====
                        # 把全局边距行替换为每洞独立边距（带 mt/mb/ml/mr）。
                        # Case A（异边距）→ 洞1 上20.5/下40.0、洞2 上21.7/下35.0 分别显示；
                        # Case B（同边距）→ 每洞显示相同 mt/mb，视觉等价共享模式。
                        holes_txt_lines = []
                        for i, h in enumerate(valid_holes):
                            holes_txt_lines.append(
                                f"  洞{i+1}：{h.w_cm:.1f} × {h.h_cm:.1f} cm"
                            )
                        gaps_txt = "，".join(f"间{i+1}_{i+2}={valid_gaps[i]:.1f}" for i in range(len(valid_gaps))) if valid_gaps else "无"
                        mh_margin_lines = []
                        for i, h in enumerate(valid_holes):
                            mt_h = getattr(h, 'margin_top_cm', 0.0)
                            mb_h = getattr(h, 'margin_bottom_cm', 0.0)
                            ml_h = getattr(h, 'margin_left_cm', 0.0)
                            mr_h = getattr(h, 'margin_right_cm', 0.0)
                            mh_margin_lines.append(
                                f"  洞{i+1}边距：上{mt_h:.1f}/下{mb_h:.1f}"
                                f"/左{ml_h:.1f}/右{mr_h:.1f} cm"
                            )
                        gaps_txt_line = (f"  中距：{gaps_txt}" if gaps_txt != "无"
                                         else "  中距：无")
                        mh_info = (
                            f"✅ 识别草图成功（{layout}，{len(valid_holes)}洞）：\n"
                            f"  外框：{result.outer_w_cm:.1f} × {result.outer_h_cm:.1f} cm\n"
                        )
                        for line in holes_txt_lines:
                            mh_info += line + "\n"
                        for mline in mh_margin_lines:
                            mh_info += mline + "\n"
                        mh_info += gaps_txt_line + dir_info + "\n"
                        mh_info += "（已按识别值填入【内挖边距】栏和【多洞参数】区，画布已含 1cm 裁剪损耗）"
                        self._set_pool_status(mh_info)
                    else:
                        # ===== 单洞原有代码（一字未改） =====
                        self._set_pool_status(
                            f"✅ 识别草图成功：\n"
                            f"  外框：{result.outer_w_cm:.1f} × {result.outer_h_cm:.1f} cm\n"
                            f"  内挖（按画布+边距派生）：{(result.outer_w_cm - result.margin_left_cm - result.margin_right_cm):.1f} × {(result.outer_h_cm - result.margin_top_cm - result.margin_bottom_cm):.1f} cm\n"
                            f"  边距：上{result.margin_top_cm:.1f}/下{result.margin_bottom_cm:.1f}/左{result.margin_left_cm:.1f}/右{result.margin_right_cm:.1f} cm"
                            f"{dir_info}"
                            f"\n（已按识别值填入【内挖边距】栏，画布已含 1cm 裁剪损耗，内挖会自动外扩 1cm）"
                        )
                except Exception as e:
                    logger.exception(f"[PropertyPanel] 草图状态消息构造失败: {e}")
                    self._set_pool_status(
                    f"✅ 识别草图成功：外框 {result.outer_w_cm:.1f}×{result.outer_h_cm:.1f}，"
                    f"内挖 {(result.outer_w_cm - result.margin_left_cm - result.margin_right_cm):.1f}×{(result.outer_h_cm - result.margin_top_cm - result.margin_bottom_cm):.1f}，"
                    f"边距 上{result.margin_top_cm:.1f}/下{result.margin_bottom_cm:.1f}/左{result.margin_left_cm:.1f}/右{result.margin_right_cm:.1f}"
                )
            else:
                # V2.0 修复：解析失败时区分"OCR引擎根本没装"和"OCR识别了但没匹配上"两类，
                # 避免用户看到笼统的"草图未识别"而不知道需要安装Tesseract
                try:
                    t_status = get_tesseract_status()
                    if not t_status.get("available"):
                        extra = f"\n💡 原因：{t_status.get('reason', 'OCR引擎未安装')}"
                        extra += "\n（无OCR时仅靠几何估算边距，可手动在【内挖边距】栏输入）"
                    else:
                        extra = "（OCR引擎已加载但未匹配到有效数值，可手动在【内挖边距】栏输入）"
                except Exception:
                    extra = "（可手动在【内挖边距】栏输入）"
                self._set_pool_status(
                    f"⚠️ 草图解析未成功：{result.message}{extra}", is_error=True)
        except Exception as e:
            logger.exception(f"[PropertyPanel] _on_sketch_parsed 异常: {e}")
            self._set_pool_status(f"⚠️ 草图解析异常：{e}", is_error=True)


    def _on_sketch_parse_err(self, err_msg: str):
        """后台解析异常：更新状态提示，不阻塞已显示的草图"""
        self._set_pool_status(f"草图解析异常：{err_msg}")


    # ================= L 形挖角识别（已迁移到 LShapePanel） =================
    # 原 _pool_try_lshape_parse / _on_lshape_parsed / _on_lshape_parse_err /
    # _on_lshape_worker_finished / _apply_lshape_params 已整体迁移到
    # gui/lshape_panel.py 的 LShapePanel 类中。PropertyPanel 通过
    # set_lshape_panel() 注入引用 + 信号桥接（_on_lshape_params_changed /
    # _on_lshape_applied / _on_lshape_recognize_started）保持原功能逻辑不变。
    # 此处保留接口注释，避免误删调用点。

    def _pool_clear_sketch(self):
        self._sketch_path = ""
        self._sketch_parse_result = None
        # ===== [L-Shape Panel Refactor 2026-09-02] L 形参数清理由 LShapePanel 承载 =====
        # 原 self._lshape_params = None + 取消 _lshape_parse_worker 已迁移到
        # LShapePanel.clear_lshape_params() + cancel_running_parse()。
        if self._lshape_panel is not None:
            self._lshape_panel.clear_lshape_params()
            self._lshape_panel.cancel_running_parse()
            self._lshape_panel.sync_sketch_preview("")
        self._pool_sk_preview.clear()
        # 恢复默认的拖拽提示样式和文字
        self._pool_sk_preview.setText("（未上传）\n或拖入图片")
        self._pool_sk_preview.setStyleSheet(
            "QLabel { border: 2px dashed #4A90E2; color: #4A90E2; background:#EFF6FF;"
            " qproperty-alignment: AlignCenter; border-radius: 6px; font-size: 11px; }")
        self._pool_sk_preview.set_has_image(False)
        self._set_pool_status("草图已清除，将按默认 10% 短边距推断")
        # 通知主画布清除草图显示
        self.sketch_loaded.emit(None)


    def _pool_view_sketch(self):
        """点击缩略图：打开大图查看对话框"""
        if not self._sketch_path or not os.path.isfile(self._sketch_path):
            self._set_pool_status("当前没有可查看的草图", is_error=True)
            return
        try:
            dlg = _SketchViewerDialog(self._sketch_path, self)
            dlg.exec_()
        except Exception as e:
            logger.warning(f"打开草图大图失败: {e}")
            QMessageBox.warning(self, "查看草图失败", f"无法打开草图：\n{e}")

