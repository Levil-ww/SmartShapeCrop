"""
gui/property_panel.py
右侧属性面板：修改 CropDesign 参数后，通知主窗口重新渲染。
"""
from __future__ import annotations
import logging
import os
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QSize
from PyQt5.QtGui import QColor, QPixmap
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel, QDoubleSpinBox,
    QSpinBox, QComboBox, QPushButton, QCheckBox, QFileDialog, QLineEdit,
    QColorDialog, QFrame, QScrollArea, QMessageBox, QProgressDialog,
    QToolButton, QMenu, QAction, QDialog,
)
from PyQt5.QtGui import QColor
from PyQt5.QtCore import QMimeData  # noqa: E402  (拖拽支持)

from core.geometry import CropDesign, BorderLayer, BorderText
from core.parser.name_parser import parse_filename
from core.parser.template_matcher import TemplateMatcher
from core.app_settings import get_app_settings

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


# ---------------------------------------------------------------------------
# 水池设计器：后台 Worker（防止大图匹配/解析/渲染卡死 UI）
# ---------------------------------------------------------------------------

class PoolRenderWorker(QThread):
    """智能水池一键流程：解析文件名 → 匹配模板 → 解析草图 → 构建 CropDesign。

    输出：
        finished_ok(design: CropDesign, sketch_result, log_text)
            → UI 把边距/尺寸写回控件，再触发预览
        finished_err(str)  → QMessageBox 提示
        progress(int, str) → QProgressDialog 更新
    """
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(object, object, str)   # design, sketch_result, log_text
    finished_err = pyqtSignal(str)

    def __init__(self,
                 matcher: TemplateMatcher,
                 template_dir: str,
                 target_filename: str,
                 sketch_path: str = "",
                 parent=None):
        super().__init__(parent)
        self._matcher = matcher
        self._template_dir = template_dir
        self._target = target_filename
        self._sketch = sketch_path
        self._log_lines: list[str] = []

    def _log(self, msg: str):
        self._log_lines.append(msg)
        logger.info(f"[PoolWorker] {msg}")

    def run(self):
        try:
            # 1) 解析文件名
            self.progress.emit(5, "解析目标文件名…")
            parsed = parse_filename(self._target)
            self._log(
                f"解析结果：产品={parsed.product_name}, 花型={parsed.pattern_name}, "
                f"水池模式={parsed.pool_mode}, 水池花型={parsed.pool_pattern_name}, "
                f"尺寸={parsed.width_cm}x{parsed.height_cm}cm"
            )
            if parsed.width_cm <= 0 or parsed.height_cm <= 0:
                self.finished_err.emit(
                    "文件名中未解析出尺寸（格式示例：吸水皮革-定制-裁剪有图-克罗印花;60.5x133CM）。\n"
                    "请检查文件名是否包含尺寸，或先在画布尺寸区手动填入。"
                )
                return

            # 2) 匹配模板
            self.progress.emit(25, "扫描模板库并匹配最佳素材…")
            if not self._template_dir or not os.path.isdir(self._template_dir):
                self.finished_err.emit("请先选择模板库目录")
                return
            if self._matcher.get_template_dir() != os.path.abspath(self._template_dir):
                self._matcher.set_template_dir(os.path.abspath(self._template_dir))
            self._matcher.scan_library(force=False)
            best, candidates = self._matcher.find_best_match(self._target)
            if best is None:
                self.finished_err.emit(
                    f"在模板库中未找到匹配的花型（目标花型={parsed.pool_pattern_name or parsed.pattern_name}）。\n"
                    "请确认模板库目录是否正确，或模板文件名包含该花型名。"
                )
                return
            self._log(
                f"最佳匹配：{os.path.basename(best.path)}  "
                f"(score={best.score:.1f}, 比例差={best.ratio_diff:.3f})"
            )

            # 3) 解析草图（如果提供了）
            sketch_result = None
            if self._sketch and os.path.isfile(self._sketch):
                self.progress.emit(60, "解析尺寸草图几何…")
                try:
                    from core.pool_designer import parse_sketch
                    sketch_result = parse_sketch(
                        self._sketch,
                        target_outer_w_cm=parsed.width_cm,
                        target_outer_h_cm=parsed.height_cm,
                    )
                    self._log(f"草图解析结果：success={sketch_result.success}, msg={sketch_result.message}")
                except Exception as e:
                    self._log(f"草图解析异常（忽略，仍可继续手动输入）：{e}")

            # 4) 构建 CropDesign
            #    画布尺寸 = 目标尺寸 + 1cm 损耗（裁剪余料用）
            TRIM_CM = 1.0
            self.progress.emit(85, "构建设计参数…")
            design = CropDesign()
            design.canvas_w_cm = parsed.width_cm + TRIM_CM
            design.canvas_h_cm = parsed.height_cm + TRIM_CM
            design.dpi = 150
            design.mode = 'rect_hole'
            design.outer_margin_cm = 0.0   # 水池默认不额外留白（花纹图本身就是外框）

            # 边距优先用草图，否则用默认等比例值（10% 短边）
            if sketch_result and sketch_result.success:
                design.inner_margin_top_cm = sketch_result.margin_top_cm
                design.inner_margin_bottom_cm = sketch_result.margin_bottom_cm
                design.inner_margin_left_cm = sketch_result.margin_left_cm
                design.inner_margin_right_cm = sketch_result.margin_right_cm
            else:
                default_m = min(parsed.width_cm, parsed.height_cm) * 0.10
                design.inner_margin_top_cm = default_m
                design.inner_margin_bottom_cm = default_m
                design.inner_margin_left_cm = default_m
                design.inner_margin_right_cm = default_m

            # 水池模式开关 + 外框素材图
            design.pool_hole_transparent = True
            design.pool_outer_material_image = best.path
            # 同时也写到外框素材字段（方便用户在"背景设置"里看到并编辑）
            design.outer_bg_image = best.path

            # 多层边框：水池一键生成默认清空（花纹素材图本身就是完整的、自带圆角/层级边框的矩形图，
            # 再叠代码画的黑/白/黑边框会遮挡素材）；用户仍可在【多层边框】区手动加层。
            design.borders = []

            self.progress.emit(100, "完成！")
            self.finished_ok.emit(design, sketch_result, "\n".join(self._log_lines))

        except Exception as e:
            logger.exception("PoolRenderWorker 异常终止")
            self.finished_err.emit(f"处理失败：{e}")


class PropertyPanel(QWidget):
    """右侧属性面板"""

    design_changed = pyqtSignal(object)   # 发送更新后的 CropDesign
    save_requested = pyqtSignal()
    export_psd_requested = pyqtSignal(str)

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
        self._sketch_path = ""
        # —— 持久化设置（与圆角裁剪工具共用同一份 QSettings）——
        self._app_settings = get_app_settings()
        self._build_ui()
        self._load_from_design()
        self._pool_restore_last_template_dir()

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

        # 4.5) 圆角设置（支持折叠：勾选=展开，取消勾选=折叠）
        self._gb_corner = QGroupBox("圆角设置（厘米）")
        self._gb_corner.setCheckable(True)
        self._gb_corner.setChecked(False)  # 默认折叠，需要用时再展开
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
        # 折叠时隐藏内容（Qt 的 checkable 默认只禁用不禁内容显示，这里手动控制可见性）
        self._gb_corner.toggled.connect(lambda c: self._toggle_group_content(self._gb_corner, c))
        # 初始化时保持折叠
        self._toggle_group_content(self._gb_corner, self._gb_corner.isChecked())

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
        row_fn.addWidget(self._pool_target, 1)
        row_fn.addWidget(btn_fn1, 0)
        row_fn.addWidget(btn_fn2, 0)
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
        sk_btns.addWidget(btn_sk1)
        sk_btns.addWidget(btn_sk2)
        sk_desc = QLabel(
            "草图格式示例（红色线标注上下左右边距即可，\n"
            "自动识别失败时可在下方【内挖边距】手动调整）\n"
            "💡 支持：点击上传按钮 或 直接将图片拖入左侧框")
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
        self._pool_hole_mode.addItem("🎨 纯色填充（用内部背景色）", "solid")
        self._pool_hole_mode.addItem("🖼 素材填充（用内部背景图）", "image")
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

    # -------------- 智能水池：事件处理 --------------

    def _on_pool_hole_mode_change(self):
        mode = self._pool_hole_mode.currentData()
        # 只改 design.pool_hole_transparent 默认值；后续真正 apply 时 _collect 里再同步
        self._set_pool_status(f"挖空方式切换为：{self._pool_hole_mode.currentText()}")

    def _on_pool_target_changed(self, text: str):
        """目标文件名变更：1) 自动同步输出文件名；2) 解析尺寸回填到画布宽高"""
        # 1) 默认同步输出文件名（用户手动修改过输出名时，可通过按钮重新同步）
        self._pool_sync_output_from_target()

        # 2) 解析尺寸并回填（解析失败不报错，静默忽略）
        name = text.strip()
        if not name:
            return
        try:
            parsed = parse_filename(name)
            w, h = parsed.width_cm, parsed.height_cm
            if w > 0 and h > 0:
                # 校验在 SpinBox 合法范围内
                w = max(5.0, min(200.0, float(w)))
                h = max(5.0, min(200.0, float(h)))
                self._sp_w.setValue(w)
                self._sp_h.setValue(h)
                self._set_pool_status(
                    f"已自动识别尺寸：{w:.1f} × {h:.1f} cm（可在画布尺寸中微调）")
        except Exception:
            # 解析异常静默忽略
            pass

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

    # ================================================================
    # 模板库目录：持久化 / 历史记录（与圆角裁剪工具共用 AppSettings）
    # ================================================================

    def _pool_restore_last_template_dir(self):
        """启动时：恢复上次使用的模板库目录 + 填充历史记录下拉/菜单"""
        self._pool_refresh_template_history_ui()
        last_dir = self._app_settings.get_default_template_dir()
        if last_dir and os.path.isdir(last_dir):
            self._pool_set_template_dir_ui(last_dir)
            # 同步到 matcher（不立即扫描，用户点"生成"时再走磁盘缓存）
            self._pool_on_template_dir_changed(last_dir)

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

    def _pool_on_template_dir_changed(self, text: str):
        """模板库目录变更时更新匹配引擎（只有目录真正不同才 set）"""
        text = (text or "").strip()
        if not text or not os.path.isdir(text):
            return
        abs_dir = os.path.abspath(text)
        if self._matcher.get_template_dir() != abs_dir:
            self._matcher.set_template_dir(abs_dir)

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
            "", "图片 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)"
        )
        if not p:
            return
        self._pool_load_sketch_from_path(p)

    def _pool_load_sketch_from_path(self, p: str):
        """通用：加载草图路径 → 保存 + 预览更新（按钮上传 & 拖拽上传共用）"""
        if not p or not os.path.isfile(p):
            return
        self._sketch_path = p
        # 预览（注意 setPixmap 后会覆盖文字/样式，但拖入提示会在清除时恢复）
        pm = QPixmap(p)
        if not pm.isNull():
            self._pool_sk_preview.setPixmap(pm.scaled(
                self._pool_sk_preview.size(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation))
            # 有图片时把虚线改为普通实线避免视觉混乱，并标记可点击状态
            self._pool_sk_preview.setStyleSheet(
                "QLabel { border: 1px solid #888; background:#fff; border-radius: 6px; }")
            self._pool_sk_preview.set_has_image(True)
        else:
            self._pool_sk_preview.clear()
            self._pool_sk_preview.setText("（预览失败）\n或拖入图片")
            self._pool_sk_preview.set_has_image(False)
        self._set_pool_status(f"已上传草图：{os.path.basename(p)}（点击缩略图查看大图）")

    def _pool_clear_sketch(self):
        self._sketch_path = ""
        self._pool_sk_preview.clear()
        # 恢复默认的拖拽提示样式和文字
        self._pool_sk_preview.setText("（未上传）\n或拖入图片")
        self._pool_sk_preview.setStyleSheet(
            "QLabel { border: 2px dashed #4A90E2; color: #4A90E2; background:#EFF6FF;"
            " qproperty-alignment: AlignCenter; border-radius: 6px; font-size: 11px; }")
        self._pool_sk_preview.set_has_image(False)
        self._set_pool_status("草图已清除，将按默认 10% 短边距推断")

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

    def _set_pool_status(self, msg: str, is_error: bool = False):
        color = "#B00020" if is_error else "#388E3C"
        self._pool_status.setText(msg)
        self._pool_status.setStyleSheet(
            f"color:{color}; padding:4px 6px; background: {'#FFEBEE' if is_error else '#E8F5E9'};"
            " border-radius: 4px;")

    def _pool_run_generate(self):
        if self._pool_worker is not None and self._pool_worker.isRunning():
            QMessageBox.information(self, "提示", "正在处理中，请稍候…")
            return

        target_name = self._pool_target.text().strip()
        if not target_name:
            self._set_pool_status("请先填写或选择目标文件名", is_error=True)
            return

        tpl_dir = self._pool_tpl_dir.lineEdit().text().strip()
        if not tpl_dir or not os.path.isdir(tpl_dir):
            self._set_pool_status("请先选择正确的模板库目录", is_error=True)
            return

        # 启动 Worker
        self._pool_btn_generate.setEnabled(False)
        self._pool_btn_generate.setText("处理中…请稍候")
        worker = PoolRenderWorker(
            self._matcher, tpl_dir, target_name, self._sketch_path, self)
        worker.progress.connect(self._on_pool_progress)
        worker.finished_ok.connect(self._on_pool_finished_ok)
        worker.finished_err.connect(self._on_pool_finished_err)
        worker.finished.connect(lambda: (
            self._pool_btn_generate.setEnabled(True),
            self._pool_btn_generate.setText("🔍 匹配模板 → 解析草图 → 生成预览"),
        ))
        self._pool_worker = worker
        worker.start()

    def _on_pool_progress(self, pct: int, msg: str):
        # 简单显示在 status 里，避免 QProgressDialog 抢焦点；另外写 debug log
        self._set_pool_status(f"[{pct}%] {msg}")

    def _on_pool_finished_err(self, msg: str):
        self._set_pool_status(msg, is_error=True)
        QMessageBox.critical(self, "智能水池：失败", msg)

    def _on_pool_finished_ok(self, design: CropDesign, sketch_result, log_text: str):
        # 1) 把 Worker 构建的设计写回 self.design，并同步到所有 SpinBox / 控件
        self.design = design
        # 同步挖空方式 ComboBox
        hm = self._pool_hole_mode.currentData()
        if hm == "blank":
            self.design.pool_hole_transparent = True
        elif hm == "solid":
            self.design.pool_hole_transparent = False
            self.design.hole_bg_image = None
        elif hm == "image":
            self.design.pool_hole_transparent = False

        # 2) 把数值写回 UI 控件（让用户看到并能继续编辑）
        self._sp_w.setValue(max(5.0, design.canvas_w_cm))
        self._sp_h.setValue(max(5.0, design.canvas_h_cm))
        self._sp_dpi.setValue(max(72, design.dpi))
        # 裁剪模式
        idx = self._cb_mode.findData(design.mode)
        if idx >= 0:
            self._cb_mode.setCurrentIndex(idx)
        self._on_mode_change()
        self._sp_outer_margin.setValue(max(0, design.outer_margin_cm))
        self._sp_mt.setValue(max(0, design.inner_margin_top_cm))
        self._sp_mb.setValue(max(0, design.inner_margin_bottom_cm))
        self._sp_ml.setValue(max(0, design.inner_margin_left_cm))
        self._sp_mr.setValue(max(0, design.inner_margin_right_cm))
        # 外框素材路径写到"背景设置"编辑框
        if design.outer_bg_image:
            self._ed_outer_img.setText(design.outer_bg_image)

        # 3) 结果提示
        info = f"✅ 生成成功！\n"
        info += f"画布：{design.canvas_w_cm:.1f} × {design.canvas_h_cm:.1f} cm\n"
        info += (f"内挖边距：上{design.inner_margin_top_cm:.1f}/下{design.inner_margin_bottom_cm:.1f}/"
                 f"左{design.inner_margin_left_cm:.1f}/右{design.inner_margin_right_cm:.1f} cm\n")
        if sketch_result is not None and sketch_result.success:
            info += f"草图：{sketch_result.message}\n"
        elif sketch_result is not None and not sketch_result.success:
            info += f"草图未识别（请检查/手动调整边距）：{sketch_result.message}\n"
        if design.pool_outer_material_image:
            info += f"匹配素材：{os.path.basename(design.pool_outer_material_image)}\n"
        self._set_pool_status(info)

        # 4) 触发预览
        self._apply_quiet()

    # ---- 模式切换显示/隐藏 L 形 / 椭圆 ----
    def _on_mode_change(self):
        mode = self._cb_mode.currentData()
        self._gb_l.setVisible(mode == 'rect_lshape')
        self._gb_e.setVisible(mode == 'ellipse_hole')

    # ---- QGroupBox 折叠/展开辅助 ----
    def _toggle_group_content(self, gb: QGroupBox, show: bool):
        """控制 QGroupBox 内部子控件的显示/隐藏（真正的折叠效果，不是只禁用）"""
        # 控制 layout 中的 item
        lay = gb.layout()
        if lay is None:
            return
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.setVisible(show)
            elif item.layout() is not None:
                # 遍历嵌套 layout
                sub = item.layout()
                for j in range(sub.count()):
                    sw = sub.itemAt(j).widget()
                    if sw is not None:
                        sw.setVisible(show)

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
        # —— 水池模式字段同步 ——
        try:
            hm = self._pool_hole_mode.currentData()
            if hm == "blank":
                d.pool_hole_transparent = True
            elif hm == "solid":
                d.pool_hole_transparent = False
            elif hm == "image":
                d.pool_hole_transparent = False
        except Exception:
            # 控件未初始化（_build_ui 中途），忽略
            pass
        # 外框素材：如果用户在"背景设置"里直接改了路径，同步到 pool_outer_material_image
        # （否则水池一键生成路径是反向写入 pool_outer_material_image → outer_bg_image）
        if d.outer_bg_image and (d.pool_outer_material_image is None
                                 or d.pool_outer_material_image != d.outer_bg_image):
            d.pool_outer_material_image = d.outer_bg_image

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


# ---- 草图预览：支持拖拽上传 + 点击查看大图的 QLabel ----
class _SketchDropLabel(QLabel):
    """带拖拽支持的草图预览标签；拖入图片文件或点击按钮均可上传；有图时点击可查看大图。"""

    fileDropped = pyqtSignal(str)   # 拖入文件成功时发出路径
    clicked = pyqtSignal()          # 点击时发出（用于打开大图预览）

    _ACCEPT_EXT = {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp'}

    def __init__(self, text="（未上传）\n或拖入图片", parent=None):
        super().__init__(text, parent)
        self.setAcceptDrops(True)
        self.setFixedSize(96, 96)  # 略微加高，便于拖入
        self.setStyleSheet(
            "QLabel { border: 2px dashed #4A90E2; color: #4A90E2; background:#EFF6FF;"
            " qproperty-alignment: AlignCenter; border-radius: 6px; font-size: 11px; }")
        self.setScaledContents(True)
        self._has_image = False

    # ---- 公开：设置/清除图片状态 ----
    def set_has_image(self, has: bool):
        """设置是否已有图片，影响点击光标、悬浮提示与样式。"""
        self._has_image = has
        if has:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("点击查看草图大图")
        else:
            self.setCursor(Qt.ArrowCursor)
            self.setToolTip("")

    # ---- 点击事件：有图时触发 clicked 信号 ----
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._has_image:
            self.clicked.emit()
        else:
            super().mousePressEvent(event)

    # ---- 拖拽事件 ----
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile():
                ext = os.path.splitext(urls[0].toLocalFile())[1].lower()
                if ext in self._ACCEPT_EXT:
                    event.acceptProposedAction()
                    # 拖入时高亮边框提示
                    self.setStyleSheet(
                        "QLabel { border: 2px dashed #2E7DD1; color: #2E7DD1; background:#DBEAFE;"
                        " qproperty-alignment: AlignCenter; border-radius: 6px; font-size: 11px; }")
                    return
        event.ignore()

    def dragLeaveEvent(self, event):
        # 恢复默认样式（根据是否有图片）
        if self._has_image:
            self.setStyleSheet(
                "QLabel { border: 1px solid #888; background:#fff; border-radius: 6px; }")
        else:
            self.setStyleSheet(
                "QLabel { border: 2px dashed #4A90E2; color: #4A90E2; background:#EFF6FF;"
                " qproperty-alignment: AlignCenter; border-radius: 6px; font-size: 11px; }")

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not urls or not urls[0].isLocalFile():
            event.ignore()
            return
        path = urls[0].toLocalFile()
        ext = os.path.splitext(path)[1].lower()
        if ext not in self._ACCEPT_EXT:
            event.ignore()
            return
        event.acceptProposedAction()
        # 恢复样式（无图状态）并通知；具体样式/has_image 由主逻辑 setPixmap 后设置
        self.setStyleSheet(
            "QLabel { border: 2px dashed #4A90E2; color: #4A90E2; background:#EFF6FF;"
            " qproperty-alignment: AlignCenter; border-radius: 6px; font-size: 11px; }")
        self.fileDropped.emit(path)


# ---- 草图大图查看对话框 ----
class _SketchViewerDialog(QDialog):
    """查看上传草图的大图对话框：支持适应窗口、原尺寸、放大缩小、滚动查看。"""

    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self._image_path = image_path
        self._scale_factor = 1.0
        self._pm_original = QPixmap(image_path)

        # —— 窗口基础设置 ——
        self.setWindowTitle(f"查看草图 - {os.path.basename(image_path)}")
        self.resize(900, 700)
        self.setMinimumSize(400, 300)

        # —— 顶部工具栏 ——
        toolbar = QHBoxLayout()
        self._btn_fit = QPushButton("🔍 适应窗口")
        self._btn_actual = QPushButton("📐 原尺寸 (100%)")
        self._btn_zoom_in = QPushButton("➕ 放大")
        self._btn_zoom_out = QPushButton("➖ 缩小")
        self._btn_close = QPushButton("✕ 关闭")
        self._btn_close.setStyleSheet("background:#f44336;color:white;padding:6px 12px;border-radius:4px;")
        toolbar.addWidget(self._btn_fit)
        toolbar.addWidget(self._btn_actual)
        toolbar.addWidget(self._btn_zoom_in)
        toolbar.addWidget(self._btn_zoom_out)
        toolbar.addStretch(1)
        toolbar.addWidget(self._btn_close)

        # —— 图片信息栏 ——
        self._info_label = QLabel()
        self._info_label.setStyleSheet("color:#555;padding:4px 8px;background:#f5f5f5;border-radius:4px;")

        # —— 滚动区 + 图片显示 ——
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)  # 图片自己控制缩放
        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignCenter)
        self._img_label.setStyleSheet("background:#2b2b2b;")
        self._scroll.setWidget(self._img_label)

        # —— 布局 ——
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)
        root.addLayout(toolbar)
        root.addWidget(self._info_label)
        root.addWidget(self._scroll, 1)

        # —— 连接按钮 ——
        self._btn_fit.clicked.connect(self._zoom_fit)
        self._btn_actual.clicked.connect(self._zoom_actual)
        self._btn_zoom_in.clicked.connect(lambda: self._zoom_step(1.25))
        self._btn_zoom_out.clicked.connect(lambda: self._zoom_step(1 / 1.25))
        self._btn_close.clicked.connect(self.accept)

        # —— 初始化显示 ——
        if self._pm_original.isNull():
            self._info_label.setText(f"❌ 无法加载图片：{image_path}")
            self._img_label.setText("图片加载失败")
        else:
            self._update_info()
            # 首次显示：延迟到事件循环后执行适应窗口，因为布局尺寸还没确定
            QTimer(self).singleShot(0, self._zoom_fit)

    # ---- 辅助：更新图片信息 ----
    def _update_info(self):
        if self._pm_original.isNull():
            return
        w, h = self._pm_original.width(), self._pm_original.height()
        try:
            fsize = os.path.getsize(self._image_path)
            if fsize >= 1024 * 1024:
                fsize_str = f"{fsize / 1024 / 1024:.2f} MB"
            else:
                fsize_str = f"{fsize / 1024:.2f} KB"
        except Exception:
            fsize_str = "未知大小"
        self._info_label.setText(
            f"📄 {os.path.basename(self._image_path)}　|　"
            f"📏 原始尺寸：{w} × {h} px　|　"
            f"🗂 文件大小：{fsize_str}　|　"
            f"🔎 当前缩放：{self._scale_factor * 100:.0f}%"
        )

    # ---- 辅助：应用当前缩放系数到图片显示 ----
    def _apply_scale(self):
        if self._pm_original.isNull():
            return
        new_w = int(self._pm_original.width() * self._scale_factor)
        new_h = int(self._pm_original.height() * self._scale_factor)
        if new_w <= 0 or new_h <= 0:
            return
        scaled = self._pm_original.scaled(
            new_w, new_h,
            Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self._img_label.setPixmap(scaled)
        self._img_label.resize(scaled.size())
        self._update_info()

    # ---- 缩放：适应窗口 ----
    def _zoom_fit(self):
        if self._pm_original.isNull():
            return
        # 用滚动区 viewport 的可用尺寸计算
        vw = self._scroll.viewport().width() - 4
        vh = self._scroll.viewport().height() - 4
        if vw <= 0 or vh <= 0:
            return
        sw = vw / self._pm_original.width()
        sh = vh / self._pm_original.height()
        self._scale_factor = min(sw, sh, 3.0)  # 适应窗口，但不超过 300% 避免过模糊
        self._apply_scale()

    # ---- 缩放：原尺寸 100% ----
    def _zoom_actual(self):
        self._scale_factor = 1.0
        self._apply_scale()

    # ---- 缩放：按倍率步进 ----
    def _zoom_step(self, factor: float):
        if self._pm_original.isNull():
            return
        new_scale = self._scale_factor * factor
        # 限制在 10% ~ 800% 范围内
        new_scale = max(0.1, min(8.0, new_scale))
        # 没有实质变化就不重绘
        if abs(new_scale - self._scale_factor) < 0.001:
            return
        self._scale_factor = new_scale
        self._apply_scale()

    # ---- 滚轮缩放（辅助体验）----
    def wheelEvent(self, event):
        # 只有在光标位于图片区域附近时才触发；这里简化为整窗口支持 Ctrl+滚轮
        if event.modifiers() & Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self._zoom_step(1.15)
            else:
                self._zoom_step(1 / 1.15)
            event.accept()
        else:
            super().wheelEvent(event)

    # ---- 窗口大小变化时，如果之前是"适应窗口"模式，重新适应 ----
    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 如果当前缩放比例非常接近当前视口下的适应值，就重新适应
        if not self._pm_original.isNull():
            vw = max(1, self._scroll.viewport().width() - 4)
            vh = max(1, self._scroll.viewport().height() - 4)
            sw = vw / self._pm_original.width()
            sh = vh / self._pm_original.height()
            fit_factor = min(sw, sh, 3.0)
            # 若当前系数与 fit_factor 偏差在 1% 内，认为处于"适应窗口"状态，随窗口大小重新适应
            if abs(self._scale_factor - fit_factor) / max(0.0001, fit_factor) < 0.01:
                self._scale_factor = fit_factor
                self._apply_scale()


# ---- 边框层编辑对话框 ----
from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
from PyQt5.QtCore import QTimer


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
