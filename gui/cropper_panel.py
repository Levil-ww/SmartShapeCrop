"""
gui/cropper_panel.py
裁剪面板：上传成品图/PSD → 自动识别或手动输入裁剪参数 → 预览 → 导出 JPG。
支持从目标文件名自动匹配模板库中的源图。
"""
from __future__ import annotations
import os
import time
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage, QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QDoubleSpinBox,
    QSpinBox, QPushButton, QFileDialog, QLineEdit, QComboBox, QCheckBox,
    QScrollArea, QMessageBox, QGridLayout, QSizePolicy, QColorDialog,
    QToolButton, QMenu, QAction, QProgressDialog,
)
from PIL import Image

from core.name_parser import parse_filename, generate_filename, format_corner_spec, get_image_info, _fmt_num
from core.image_cropper import crop_image, CropConfig, get_corner_name, get_default_corners, get_mode_description
from core.template_matcher import TemplateMatcher, TemplateEntry
from core.config import CUT_LOSS_CM, CORNER_CUT_LOSS_CM, DEFAULT_DPI
from core.app_settings import get_app_settings, TemplateDirHistory


def pil_to_qpixmap(pil_img: Image.Image) -> QPixmap:
    """PIL Image → QPixmap"""
    if pil_img.mode != 'RGB':
        pil_img = pil_img.convert('RGB')
    data = pil_img.tobytes('raw', 'RGB')
    qimg = QImage(data, pil_img.width, pil_img.height, pil_img.width * 3, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(qimg)


class CropWorker(QThread):
    """
    [PERF] 异步裁剪 Worker — 将大图裁剪操作移至后台线程。

    对于印刷级大图（1-2亿像素），裁剪操作（边框检测、圆角重绘）
    在 UI 主线程执行会导致界面卡死。通过 QThread 异步执行，
    同时发送进度信号更新 UI。
    """

    finished_ok = pyqtSignal(object)
    finished_err = pyqtSignal(str)
    progress = pyqtSignal(int, str)

    def __init__(self, config: CropConfig, parent=None):
        super().__init__(parent)
        self._config = config

    def run(self):
        try:
            result = crop_image(self._config)
            self.finished_ok.emit(result)
        except Exception as e:
            self.finished_err.emit(str(e))


class CropperPanel(QWidget):
    """裁剪操作面板"""
    
    image_cropped = pyqtSignal(object)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._src_path: str = ""
        self._src_info: dict = {}
        self._last_result: Image.Image | None = None
        self._bg_color: tuple[int, int, int] = (255, 255, 255)
        self._matcher = TemplateMatcher()
        self._matcher.set_log_callback(self._on_matcher_log)
        self._target_parsed = None
        self._corner_programmatic = False
        self._app_settings = get_app_settings()
        self._worker: CropWorker | None = None
        self._progress: QProgressDialog | None = None
        self._build_ui()
        self._restore_last_template_dir()
    
    def _on_matcher_log(self, msg: str):
        """接收匹配引擎日志"""
        if hasattr(self, '_lbl_match_log'):
            self._lbl_match_log.setText(msg)
    
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(10)
        scroll.setWidget(inner)
        root.addWidget(scroll)
        
        # ===== 1) 源图选择 =====
        gb_file = QGroupBox("1. 选择源图")
        fg = QVBoxLayout(gb_file)
        
        # 1a) 模板库目录（可编辑 ComboBox + 历史记录下拉按钮 + 浏览）
        row_tpl_dir = QHBoxLayout()
        row_tpl_dir.addWidget(QLabel("模板库:"))
        self._ed_template_dir = QComboBox()
        self._ed_template_dir.setEditable(True)
        self._ed_template_dir.setPlaceholderText("模板库目录路径…（点右侧 ▾ 选择历史记录）")
        # 下拉：显示历史记录；编辑：手动输入路径
        le = self._ed_template_dir.lineEdit()
        le.textChanged.connect(self._on_template_dir_changed)
        self._ed_template_dir.currentIndexChanged.connect(self._on_template_history_selected)
        row_tpl_dir.addWidget(self._ed_template_dir, 1)
        # 历史记录按钮（小箭头菜单）
        self._btn_tpl_history = QToolButton()
        self._btn_tpl_history.setText("▾")
        self._btn_tpl_history.setPopupMode(QToolButton.InstantPopup)
        self._btn_tpl_history.setToolTip("最近打开的模板库")
        self._tpl_history_menu = QMenu(self._btn_tpl_history)
        self._btn_tpl_history.setMenu(self._tpl_history_menu)
        row_tpl_dir.addWidget(self._btn_tpl_history)
        # 浏览按钮
        btn_tpl_dir = QPushButton("浏览…")
        btn_tpl_dir.clicked.connect(self._pick_template_dir)
        row_tpl_dir.addWidget(btn_tpl_dir)
        fg.addLayout(row_tpl_dir)
        
        # 1b) 目标文件名输入
        row_target = QHBoxLayout()
        row_target.addWidget(QLabel("目标文件名:"))
        self._ed_target_name = QLineEdit()
        self._ed_target_name.setPlaceholderText("如: 双面格-定制-定制尺寸-简织;竖版55x41cm右下角圆角半径2厘米")
        row_target.addWidget(self._ed_target_name, 1)
        btn_match = QPushButton("🔍 自动匹配")
        btn_match.setStyleSheet("background:#e67e22; color:white; font-weight:bold; padding:5px 10px;")
        btn_match.clicked.connect(self._auto_match)
        row_target.addWidget(btn_match)
        fg.addLayout(row_target)
        
        # 1c) 源图文件路径
        row_file = QHBoxLayout()
        self._ed_file = QLineEdit()
        self._ed_file.setPlaceholderText("JPG / PSD 文件路径…")
        btn_pick = QPushButton("浏览…")
        btn_pick.clicked.connect(self._pick_source)
        row_file.addWidget(self._ed_file, 1)
        row_file.addWidget(btn_pick)
        fg.addLayout(row_file)
        
        # 1d) 源图信息显示
        self._lbl_src_info = QLabel("尚未选择源图")
        self._lbl_src_info.setStyleSheet("color:#666;")
        self._lbl_src_info.setWordWrap(True)
        fg.addWidget(self._lbl_src_info)
        
        # 1e) 匹配日志
        self._lbl_match_log = QLabel("")
        self._lbl_match_log.setStyleSheet("color:#e67e22; font-size:11px;")
        self._lbl_match_log.setWordWrap(True)
        fg.addWidget(self._lbl_match_log)
        
        # 1f) 自动识别按钮
        btn_parse = QPushButton("2. 从文件名自动识别尺寸/圆角")
        btn_parse.setStyleSheet("padding:6px; background:#4a90d9; color:white; font-weight:bold;")
        btn_parse.clicked.connect(self._auto_parse)
        fg.addWidget(btn_parse)
        
        lay.addWidget(gb_file)
        
        # ===== 3) 裁剪参数 =====
        gb_param = QGroupBox("3. 裁剪参数")
        fp = QVBoxLayout(gb_param)
        
        # 产品名称
        row_name = QHBoxLayout()
        row_name.addWidget(QLabel("产品名称:"))
        self._ed_product = QLineEdit()
        self._ed_product.setPlaceholderText("如：双面格-定制-定制尺寸-简织")
        row_name.addWidget(self._ed_product, 1)
        fp.addLayout(row_name)
        
        # 布局 + 尺寸
        grid_size = QGridLayout()
        grid_size.addWidget(QLabel("布局:"), 0, 0)
        self._cb_layout = QComboBox()
        self._cb_layout.addItem("竖版（长边为高）", "竖版")
        self._cb_layout.addItem("横版（长边为宽）", "横版")
        grid_size.addWidget(self._cb_layout, 0, 1)
        
        grid_size.addWidget(QLabel("宽(cm):"), 0, 2)
        self._sp_w = QDoubleSpinBox(); self._sp_w.setRange(1, 500); self._sp_w.setValue(55); self._sp_w.setDecimals(2); self._sp_w.setSingleStep(0.5)
        grid_size.addWidget(self._sp_w, 0, 3)
        
        grid_size.addWidget(QLabel("高(cm):"), 0, 4)
        self._sp_h = QDoubleSpinBox(); self._sp_h.setRange(1, 500); self._sp_h.setValue(41); self._sp_h.setDecimals(2); self._sp_h.setSingleStep(0.5)
        grid_size.addWidget(self._sp_h, 0, 5)
        
        grid_size.addWidget(QLabel("DPI:"), 1, 0)
        self._sp_dpi = QSpinBox(); self._sp_dpi.setRange(72, 600); self._sp_dpi.setValue(DEFAULT_DPI)
        grid_size.addWidget(self._sp_dpi, 1, 1)
        
        grid_size.addWidget(QLabel("裁剪模式:"), 1, 2)
        self._cb_mode = QComboBox()
        self._cb_mode.addItem("简单缩放（推荐）", "simple_resize")
        self._cb_mode.addItem("轻度裁剪", "light_cover")
        self._cb_mode.addItem("智能模式", "auto")
        self._cb_mode.addItem("裁剪填满", "cover")
        self._cb_mode.addItem("留白填充", "contain")
        grid_size.addWidget(self._cb_mode, 1, 3, 1, 3)
        
        fp.addLayout(grid_size)
        
        # 模式说明
        self._lbl_mode_desc = QLabel(get_mode_description('simple_resize'))
        self._lbl_mode_desc.setStyleSheet("color:#666; font-size:11px;")
        self._lbl_mode_desc.setWordWrap(True)
        fp.addWidget(self._lbl_mode_desc)
        
        # 高级选项
        row_adv = QHBoxLayout()
        row_adv.addWidget(QLabel("最大裁剪比例(%):"))
        self._sp_max_crop = QDoubleSpinBox()
        self._sp_max_crop.setRange(1, 50)
        self._sp_max_crop.setValue(15)
        self._sp_max_crop.setSuffix(" %")
        row_adv.addWidget(self._sp_max_crop)
        
        row_adv.addWidget(QLabel("背景色:"))
        self._btn_bg_color = QPushButton()
        self._btn_bg_color.setStyleSheet("background-color: white; border: 1px solid #ccc; padding: 4px;")
        self._btn_bg_color.setFixedWidth(60)
        self._btn_bg_color.clicked.connect(self._pick_bg_color)
        row_adv.addWidget(self._btn_bg_color)
        
        row_adv.addStretch(1)
        fp.addLayout(row_adv)
        
        lay.addWidget(gb_param)
        
        # ===== 4) 圆角设置 =====
        gb_corner = QGroupBox("4. 圆角设置（厘米）")
        fc = QVBoxLayout(gb_corner)
        
        row_quick = QHBoxLayout()
        row_quick.addWidget(QLabel("快速设置:"))
        self._sp_quick_r = QDoubleSpinBox(); self._sp_quick_r.setRange(0, 50); self._sp_quick_r.setValue(0); self._sp_quick_r.setDecimals(2); self._sp_quick_r.setSingleStep(0.5)
        self._sp_quick_r.setSuffix(" cm")
        row_quick.addWidget(self._sp_quick_r)
        
        btn_all = QPushButton("四角相同")
        btn_all.clicked.connect(self._apply_to_all)
        btn_tl_br = QPushButton("左上+右下")
        btn_tl_br.clicked.connect(lambda: self._apply_to_corners(['tl', 'br']))
        btn_tr_bl = QPushButton("右上+左下")
        btn_tr_bl.clicked.connect(lambda: self._apply_to_corners(['tr', 'bl']))
        btn_bl_br = QPushButton("左下+右下")
        btn_bl_br.clicked.connect(lambda: self._apply_to_corners(['bl', 'br']))
        row_quick.addWidget(btn_all)
        row_quick.addWidget(btn_tl_br)
        row_quick.addWidget(btn_tr_bl)
        row_quick.addWidget(btn_bl_br)
        row_quick.addStretch(1)
        fc.addLayout(row_quick)
        
        grid_corner = QGridLayout()
        self._sp_corners = {}
        for i, (key, name) in enumerate([('tl', '左上角'), ('tr', '右上角'), ('bl', '左下角'), ('br', '右下角')]):
            grid_corner.addWidget(QLabel(name), i // 2, (i % 2) * 3)
            sp = QDoubleSpinBox(); sp.setRange(0, 50); sp.setValue(0); sp.setDecimals(2); sp.setSingleStep(0.5); sp.setSuffix(" cm")
            grid_corner.addWidget(sp, i // 2, (i % 2) * 3 + 1)
            self._sp_corners[key] = sp
        
        fc.addLayout(grid_corner)
        lay.addWidget(gb_corner)
        
        # ===== 5) 输出命名预览 =====
        gb_name = QGroupBox("5. 输出文件名预览")
        fn = QVBoxLayout(gb_name)
        
        self._lbl_name_preview = QLabel("（选择源图并设置参数后自动生成）")
        self._lbl_name_preview.setStyleSheet("color:#4a90d9; padding:4px; background:#f0f4f8; border-radius:3px;")
        self._lbl_name_preview.setWordWrap(True)
        fn.addWidget(self._lbl_name_preview)
        
        row_name_opts = QHBoxLayout()
        self._ck_auto_name = QCheckBox("自动命名")
        self._ck_auto_name.setChecked(True)
        row_name_opts.addWidget(self._ck_auto_name)
        
        row_name_opts.addWidget(QLabel("手动命名:"))
        self._ed_custom_name = QLineEdit()
        self._ed_custom_name.setPlaceholderText("覆盖自动命名…")
        row_name_opts.addWidget(self._ed_custom_name, 1)
        fn.addLayout(row_name_opts)
        
        lay.addWidget(gb_name)
        
        # ===== 6) 操作按钮 =====
        row_actions = QHBoxLayout()
        btn_preview = QPushButton("生成预览")
        btn_preview.setStyleSheet("padding:10px; font-weight:bold;")
        btn_export = QPushButton("导出 JPG")
        btn_export.setStyleSheet("padding:10px; background:#27ae60; color:white; font-weight:bold;")
        row_actions.addWidget(btn_preview, 1)
        row_actions.addWidget(btn_export, 1)
        lay.addLayout(row_actions)
        
        btn_preview.clicked.connect(self._generate_preview)
        btn_export.clicked.connect(self._export)
        
        # 信号连接
        self._cb_layout.currentIndexChanged.connect(self._update_name_preview)
        self._sp_w.valueChanged.connect(self._update_name_preview)
        self._sp_h.valueChanged.connect(self._update_name_preview)
        for sp in self._sp_corners.values():
            sp.valueChanged.connect(self._on_corner_value_changed)
        self._ed_product.textChanged.connect(self._update_name_preview)
        self._ck_auto_name.stateChanged.connect(self._update_name_preview)
        self._ed_custom_name.textChanged.connect(self._update_name_preview)
        self._cb_mode.currentIndexChanged.connect(self._on_mode_changed)
        self._ed_target_name.textChanged.connect(self._on_target_name_changed)
        self._ed_target_name.textChanged.connect(self._update_name_preview)
        
        self._update_name_preview()
    
    # ===== 事件处理 =====
    
    def _on_target_name_changed(self, text: str):
        """目标文件名变化时，自动解析并保存原始目标尺寸（不含损耗）"""
        text = text.strip()
        if text:
            try:
                self._target_parsed = parse_filename(text)
            except Exception:
                self._target_parsed = None
        else:
            self._target_parsed = None

    def _on_corner_value_changed(self):
        """圆角 spinbox 值变化处理。

        - 程序设置时（_auto_match/_auto_parse）：不覆盖 _target_parsed.corners，
          保留原始值用于命名。
        - 用户手动修改时：用 spinbox 值覆盖 _target_parsed.corners，
          使文件名预览跟随手动输入（手动输入=实际裁剪半径=文件名值，无补偿）。
        """
        if not self._corner_programmatic and self._target_parsed is not None:
            self._target_parsed.corners = self._get_corners_config()
        self._update_name_preview()
    
    def _on_mode_changed(self):
        """裁剪模式改变时更新说明"""
        mode = self._cb_mode.currentData()
        self._lbl_mode_desc.setText(get_mode_description(mode))
    
    def _pick_bg_color(self):
        """选择背景色"""
        color = QColorDialog.getColor(QColor(*self._bg_color), self, "选择背景色")
        if color.isValid():
            self._bg_color = (color.red(), color.green(), color.blue())
            self._btn_bg_color.setStyleSheet(
                f"background-color: rgb{self._bg_color}; border: 1px solid #ccc; padding: 4px;"
            )
    
    def _pick_source(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择源图", "",
            "图片文件 (*.jpg *.jpeg *.png *.psd *.psb)"
        )
        if path:
            self._set_source_path(path)
    
    def _set_source_path(self, path: str):
        """设置源图路径并显示信息"""
        self._src_path = path
        self._ed_file.setText(path)
        try:
            self._src_info = get_image_info(path)
            size = self._src_info.get('size_cm', None)
            size_str = f"{size[0]}×{size[1]}cm" if size else f"{self._src_info['width_px']}×{self._src_info['height_px']}px"
            self._lbl_src_info.setText(
                f"源图: {size_str} | DPI: {self._src_info['dpi'][0]} | 模式: {self._src_info['mode']}"
            )
        except Exception as e:
            self._lbl_src_info.setText(f"读取失败: {e}")
    
    # ================================================================
    # 模板库目录：历史记录 + 自动恢复
    # ================================================================

    def _restore_last_template_dir(self):
        """启动时：恢复上次使用的模板库目录 + 填充历史记录下拉/菜单"""
        self._refresh_template_history_ui()
        # 恢复上次使用的目录
        last_dir = self._app_settings.get_default_template_dir()
        if last_dir and os.path.isdir(last_dir):
            self._set_template_dir_ui(last_dir)
            # 不立即扫描，避免启动卡；用户点"自动匹配"时再扫描或走磁盘缓存

    def _refresh_template_history_ui(self):
        """刷新历史记录：ComboBox 下拉项 + 历史菜单"""
        history = self._app_settings.get_template_history()
        # -- ComboBox 下拉：清空后重建（保留当前编辑框文本）--
        current_text = self._ed_template_dir.lineEdit().text()
        # block 信号避免在 setCurrentIndex 时触发 currentIndexChanged -> _on_template_history_selected
        self._ed_template_dir.blockSignals(True)
        try:
            self._ed_template_dir.clear()
            for h in history:
                label = h.display_name
                if h.total_files:
                    label += f"  ({h.total_files} 张)"
                label += "    " + h.path
                self._ed_template_dir.addItem(label, h.path)
            self._ed_template_dir.lineEdit().setText(current_text)
        finally:
            self._ed_template_dir.blockSignals(False)

        # -- 历史菜单（带"清除历史记录"）--
        self._tpl_history_menu.clear()
        if not history:
            a_empty = QAction("（暂无历史记录）", self._tpl_history_menu)
            a_empty.setEnabled(False)
            self._tpl_history_menu.addAction(a_empty)
        else:
            for i, h in enumerate(history):
                label = h.display_name
                if h.total_files:
                    label += f"   ({h.total_files} 张图)"
                sub = f"\n{h.path}"
                a = QAction(label, self._tpl_history_menu)
                a.setToolTip(h.path)
                a.setData(h.path)
                a.triggered.connect(lambda _=False, p=h.path: self._apply_template_dir_from_history(p))
                self._tpl_history_menu.addAction(a)
            # 分隔线 + 清除
            self._tpl_history_menu.addSeparator()
            a_clear = QAction("清除历史记录", self._tpl_history_menu)
            a_clear.triggered.connect(self._clear_template_history)
            self._tpl_history_menu.addAction(a_clear)

    def _apply_template_dir_from_history(self, path: str):
        """从历史记录选中：写入 ComboBox 并同步 matcher"""
        self._set_template_dir_ui(path)
        self._on_template_dir_changed(path)  # 手动触发一次

    def _set_template_dir_ui(self, path: str):
        """只改 UI 文本（不触发信号）"""
        le = self._ed_template_dir.lineEdit()
        le.blockSignals(True)
        try:
            le.setText(path)
        finally:
            le.blockSignals(False)

    def _clear_template_history(self):
        """清空历史记录（仅菜单/下拉清空，当前目录文本保留）"""
        self._app_settings.clear_template_history()
        self._refresh_template_history_ui()

    def _pick_template_dir(self):
        """选择模板库目录（浏览...）"""
        dir_path = QFileDialog.getExistingDirectory(self, "选择模板库目录")
        if not dir_path:
            return
        self._set_template_dir_ui(dir_path)
        self._on_template_dir_changed(dir_path)
        # 记录到历史
        self._app_settings.add_template_history(dir_path)
        self._refresh_template_history_ui()

    def _on_template_history_selected(self, idx: int):
        """用户从 ComboBox 下拉选了一条历史"""
        if idx < 0:
            return
        path = self._ed_template_dir.itemData(idx)
        if isinstance(path, str) and path:
            # 写入编辑框（触发 textChanged -> _on_template_dir_changed）
            self._set_template_dir_ui(path)
            self._on_template_dir_changed(path)
            self._app_settings.add_template_history(path)
            self._refresh_template_history_ui()

    def _on_template_dir_changed(self, text: str):
        """模板库目录变更时更新匹配引擎。

        关键点：只有目录真正不同才调用 set_template_dir，否则会清掉内存缓存 + 索引。
        """
        text = (text or "").strip()
        if not text:
            return
        if not os.path.isdir(text):
            return
        abs_dir = os.path.abspath(text)
        if self._matcher.get_template_dir() != abs_dir:
            # 只有不同时才设置（避免清空缓存）
            self._matcher.set_template_dir(abs_dir)

    def _auto_match(self):
        """从目标文件名自动匹配模板库中的源图（高性能版）。

        性能关键点：
        - **不要**每次都 set_template_dir：它会清空内存缓存和倒排索引
        - 磁盘缓存 + 增量扫描：第二次起几乎瞬间完成
        - 倒排索引预过滤：从 20 万全量降到几百条候选再评分
        """
        target_name = self._ed_target_name.text().strip()
        if not target_name:
            QMessageBox.information(self, "提示", "请输入目标文件名")
            return

        template_dir = self._ed_template_dir.lineEdit().text().strip()
        if not template_dir or not os.path.isdir(template_dir):
            QMessageBox.information(self, "提示", "请先设置模板库目录")
            return
        template_dir = os.path.abspath(template_dir)

        self._lbl_match_log.setText("正在匹配中...")

        # 确保 matcher 目录正确（仅当不同时设置，避免清空缓存）
        if self._matcher.get_template_dir() != template_dir:
            self._matcher.set_template_dir(template_dir)

        t0 = time.time()
        # 这里 scan_library 是增量/磁盘缓存的：第一次慢，之后几百毫秒
        # 但 _auto_match 没必要强扫；find_best_match 内部会按需 scan
        self._matcher.scan_library(force=False)
        best, candidates = self._matcher.find_best_match(target_name)
        match_dt = time.time() - t0

        # 记录到历史（带扫描后的总数），并写默认目录
        try:
            stats = self._matcher.get_library_stats()
            total = stats.get("total", 0)
        except Exception:
            total = 0
        self._app_settings.add_template_history(template_dir, total_files=int(total))
        self._refresh_template_history_ui()

        # 解析目标文件名获取完整裁剪参数（含圆角）
        target_parsed = parse_filename(target_name)
        self._target_parsed = target_parsed  # 保存原始解析结果（含目标尺寸，不含损耗）

        if best:
            self._set_source_path(best.path)

            # 优先使用目标文件名中的参数，模板参数作为回退
            if target_parsed.product_name:
                self._ed_product.setText(target_parsed.product_name)
            elif best.parsed and best.parsed.product_name:
                self._ed_product.setText(best.parsed.product_name)

            if target_parsed.layout:
                idx = self._cb_layout.findData(target_parsed.layout)
                if idx >= 0:
                    self._cb_layout.setCurrentIndex(idx)
            elif best.parsed and best.parsed.layout:
                idx = self._cb_layout.findData(best.parsed.layout)
                if idx >= 0:
                    self._cb_layout.setCurrentIndex(idx)

            # 尺寸自动识别 + 切割损耗（CUT_LOSS_CM）
            if target_parsed.width_cm > 0 and target_parsed.height_cm > 0:
                self._sp_w.setValue(target_parsed.width_cm + CUT_LOSS_CM)
                self._sp_h.setValue(target_parsed.height_cm + CUT_LOSS_CM)
            elif best.parsed and best.parsed.width_cm > 0:
                self._sp_w.setValue(best.parsed.width_cm + CUT_LOSS_CM)
                self._sp_h.setValue(best.parsed.height_cm + CUT_LOSS_CM)

            # 圆角只从目标文件名获取（模板通常不含圆角信息）
            if target_parsed.corners:
                self._corner_programmatic = True
                try:
                    for key in ('tl', 'tr', 'bl', 'br'):
                        if key in target_parsed.corners and target_parsed.corners[key] > 0:
                            self._sp_corners[key].setValue(target_parsed.corners[key] + CORNER_CUT_LOSS_CM)
                        else:
                            self._sp_corners[key].setValue(0)
                finally:
                    self._corner_programmatic = False

            msg = (f"✅ 匹配成功！(耗时 {match_dt:.2f}s)\n\n"
                   f"源图: {os.path.basename(best.path)}\n"
                   f"匹配得分: {best.score:.2f}\n\n"
                   f"已自动填充裁剪参数（尺寸 +1cm / 圆角 +0.5cm 切割损耗）：\n"
                   f"- 产品名称: {target_parsed.product_name or (best.parsed.product_name if best.parsed else '-')}\n"
                   f"- 尺寸: {_fmt_num(self._sp_w.value())}×{_fmt_num(self._sp_h.value())}cm 布局: {target_parsed.layout or (best.parsed.layout if best.parsed else '-')}\n"
                   f"- 圆角(命名→裁剪): {self._format_corners_for_msg(target_parsed.corners)} → {self._format_corners_for_msg(self._get_corners_config())}")
            QMessageBox.information(self, "匹配成功", msg)
        else:
            self._lbl_match_log.setText(f"❌ 未找到匹配的模板（耗时 {match_dt:.2f}s）")
            QMessageBox.warning(self, "匹配失败",
                                "未在模板库中找到匹配的源图。\n\n请检查：\n"
                                "1. 模板库目录是否正确\n"
                                "2. 模板库中是否有对应花型的图片\n"
                                "3. 尺寸和方向是否匹配")
    
    def _auto_parse(self):
        """从文件名自动识别参数（优先使用目标文件名输入框，回退到源图文件名）"""
        # 优先使用目标文件名输入
        target_name = self._ed_target_name.text().strip()
        if not target_name and self._src_path:
            target_name = os.path.basename(self._src_path)
        
        if not target_name:
            QMessageBox.information(self, "提示", "请先输入目标文件名或选择源图")
            return
        
        try:
            parsed = parse_filename(target_name)
            self._target_parsed = parsed  # 保存原始解析结果（含目标尺寸，不含损耗）
            
            if parsed.product_name:
                self._ed_product.setText(parsed.product_name)
            
            if parsed.layout:
                idx = self._cb_layout.findData(parsed.layout)
                if idx >= 0:
                    self._cb_layout.setCurrentIndex(idx)
            
            if parsed.width_cm > 0 and parsed.height_cm > 0:
                self._sp_w.setValue(parsed.width_cm + CUT_LOSS_CM)
                self._sp_h.setValue(parsed.height_cm + CUT_LOSS_CM)
            
            # 实际裁剪半径 = 命名半径 + CORNER_CUT_LOSS_CM(0.5cm)，以补偿切割损耗
            # 文件名中有圆角信息时：指定的角设为对应值，未指定的角强制设为0
            # 文件名中无圆角信息时：保持现有设置不变
            if parsed.corners:
                self._corner_programmatic = True
                try:
                    for key in ('tl', 'tr', 'bl', 'br'):
                        if key in parsed.corners and parsed.corners[key] > 0:
                            self._sp_corners[key].setValue(parsed.corners[key] + CORNER_CUT_LOSS_CM)
                        else:
                            self._sp_corners[key].setValue(0)
                finally:
                    self._corner_programmatic = False

            # 如果目标文件名中有尺寸信息但源图已选择，可以更新源图信息
            info_msg = f"产品: {parsed.product_name}\n"
            info_msg += f"原始尺寸: {_fmt_num(parsed.width_cm)}×{_fmt_num(parsed.height_cm)}cm\n"
            info_msg += f"裁剪尺寸(含 +1cm 切割损耗): {_fmt_num(self._sp_w.value())}×{_fmt_num(self._sp_h.value())}cm\n"
            info_msg += f"布局: {parsed.layout}\n"
            info_msg += f"圆角(命名→裁剪, +0.5cm): {self._format_corners_for_msg(parsed.corners)} → {self._format_corners_for_msg(self._get_corners_config())}"
            
            QMessageBox.information(self, "自动识别成功", info_msg)
        except Exception as e:
            QMessageBox.critical(self, "识别失败", str(e))
    
    def _apply_to_all(self):
        r = self._sp_quick_r.value()
        for sp in self._sp_corners.values():
            sp.setValue(r)
    
    def _apply_to_corners(self, keys: list[str]):
        r = self._sp_quick_r.value()
        for key in keys:
            if key in self._sp_corners:
                self._sp_corners[key].setValue(r)
    
    def _format_corners_for_msg(self, corners: dict | None) -> str:
        if not corners:
            return "无"
        parts = []
        for key in ('tl', 'tr', 'bl', 'br'):
            if key in corners and corners[key] > 0:
                parts.append(f"{get_corner_name(key)}: {corners[key]}cm")
        return "、".join(parts) if parts else "无"
    
    def _get_corners_config(self) -> dict[str, float]:
        return {key: sp.value() for key, sp in self._sp_corners.items()}
    
    def _get_layout(self) -> str:
        return self._cb_layout.currentData()
    
    def _update_name_preview(self):
        """更新文件名预览
        - 尺寸优先使用目标文件名原始尺寸（不含+1cm损耗），若无则使用裁剪参数
        - 圆角优先使用目标文件名原始圆角（不含+0.5cm损耗），若无则使用圆角设置
        """
        if self._ck_auto_name.isChecked():
            product = self._ed_product.text().strip() or "产品"

            # 1) 确定用于命名的尺寸和布局：优先使用目标解析结果中的原始尺寸（不含损耗）
            tp = self._target_parsed
            use_target_size = (
                tp is not None
                and tp.width_cm > 0
                and tp.height_cm > 0
            )
            if use_target_size:
                layout = tp.layout or self._get_layout()
                w = tp.width_cm
                h = tp.height_cm
            else:
                layout = self._get_layout()
                w = self._sp_w.value()
                h = self._sp_h.value()

            # 2) 短边 × 长边，竖版加前缀
            short_side = min(w, h)
            long_side = max(w, h)
            size_str = f"{_fmt_num(short_side)}x{_fmt_num(long_side)}cm"
            if layout == '竖版':
                spec_parts = [f"竖版{size_str}"]
            else:
                spec_parts = [size_str]

            # 3) 圆角描述：优先使用目标解析结果中的原始圆角（不含损耗），
            #    无目标解析时回退到圆角设置（手动输入场景）
            if tp is not None and tp.corners:
                corners = tp.corners
            else:
                corners = self._get_corners_config()
            corner_spec = format_corner_spec(corners)
            if corner_spec:
                spec_parts.append(corner_spec)

            spec_str = ''.join(spec_parts)
            name = f"{product};{spec_str}.jpg"
        else:
            name = self._ed_custom_name.text().strip() or "output.jpg"

        self._lbl_name_preview.setText(name)
    
    def _build_crop_config(self) -> CropConfig:
        """构建裁剪配置"""
        corners = self._get_corners_config()
        max_crop_ratio = self._sp_max_crop.value() / 100.0  # 转为比例
        return CropConfig(
            src_path=self._src_path,
            target_w_cm=self._sp_w.value(),
            target_h_cm=self._sp_h.value(),
            corners=corners,
            mode=self._cb_mode.currentData(),
            dpi=self._sp_dpi.value(),
            bg_color=self._bg_color,
            max_crop_ratio=max_crop_ratio,
        )
    
    def _generate_preview(self):
        """生成预览（异步，不阻塞 UI）"""
        if not self._src_path:
            QMessageBox.warning(self, "提示", "请先选择源图")
            return

        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "提示", "正在处理中，请稍候...")
            return

        config = self._build_crop_config()
        config.output_path = ""

        self._worker = CropWorker(config)
        self._worker.finished_ok.connect(self._on_preview_done)
        self._worker.finished_err.connect(self._on_crop_error)
        self._worker.progress.connect(self._on_progress)

        self._show_progress("正在生成预览...")
        self._worker.start()

    def _on_preview_done(self, result: Image.Image):
        self._hide_progress()
        self._last_result = result
        self.image_cropped.emit(result)
        self._worker = None

        QMessageBox.information(
            self, "预览生成",
            f"裁剪完成！\n\n"
            f"输出尺寸: {result.width}×{result.height} px\n"
            f"目标: {self._sp_w.value()}×{self._sp_h.value()}cm @ {self._sp_dpi.value()} DPI\n"
            f"模式: {get_mode_description(self._cb_mode.currentData())}"
        )

    def _export(self):
        """导出 JPG（异步，不阻塞 UI）"""
        if not self._src_path:
            QMessageBox.warning(self, "提示", "请先选择源图")
            return

        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "提示", "正在处理中，请稍候...")
            return

        if self._ck_auto_name.isChecked():
            output_name = self._lbl_name_preview.text()
        else:
            output_name = self._ed_custom_name.text().strip() or "output.jpg"

        default_dir = os.path.dirname(self._src_path) if self._src_path else ""
        default_path = os.path.join(default_dir, output_name)

        path, _ = QFileDialog.getSaveFileName(
            self, "导出为 JPG", default_path,
            "JPEG 图片 (*.jpg *.jpeg)"
        )
        if not path:
            return

        config = self._build_crop_config()
        config.output_path = path

        self._worker = CropWorker(config)
        self._worker.finished_ok.connect(lambda _: self._on_export_done(path, config))
        self._worker.finished_err.connect(self._on_crop_error)
        self._worker.progress.connect(self._on_progress)

        self._show_progress("正在导出 JPG...")
        self._worker.start()

    def _on_export_done(self, path: str, config: CropConfig):
        self._hide_progress()
        self._last_result = None
        self._worker = None

        QMessageBox.information(
            self, "导出成功",
            f"已导出:\n{path}\n\n"
            f"尺寸: {config.target_w_cm}×{config.target_h_cm}cm @ {config.dpi} DPI"
        )

    def _on_crop_error(self, err_msg: str):
        self._hide_progress()
        self._worker = None
        import traceback
        traceback.print_exc()
        QMessageBox.critical(self, "裁剪失败", err_msg)

    def _on_progress(self, value: int, msg: str):
        if self._progress is not None:
            self._progress.setLabelText(msg)
            if value > 0:
                self._progress.setValue(value)

    def _show_progress(self, msg: str):
        self._progress = QProgressDialog(msg, None, 0, 0, self)
        self._progress.setWindowTitle("请稍候")
        self._progress.setWindowModality(Qt.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.setCancelButton(None)
        self._progress.show()

    def _hide_progress(self):
        if self._progress is not None:
            self._progress.close()
            self._progress = None
    
    def get_last_result(self) -> Image.Image | None:
        return self._last_result
    
    def set_source_path(self, path: str):
        self._set_source_path(path)