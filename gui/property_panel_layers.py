"""gui/property_panel 子模块 —— 图层编辑与收集（_LayersMixin，9 方法）（由 property_panel.py 拆分而来，facade 模式）。

原文件 gui/property_panel.py 保留为 facade（PropertyPanel 主类 + 编排），
本模块只包含 图层编辑与收集（_LayersMixin，9 方法） 相关的实现，逻辑与原文件完全一致。
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
from .property_panel_workers import PoolRenderWorker, _SketchParseWorker
from .property_panel_dialogs import _LayersDialog, _SketchViewerDialog

class _LayersMixin:
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
        if self.design.borders:
            self.design.borders.pop()
            self._update_layers_label()
            self._apply_quiet()


    def _edit_layers(self):
        dlg = _LayersDialog(self.design.borders, self)
        if dlg.exec_():
            self.design.borders = dlg.result_layers
            self._update_layers_label()
            self._apply_quiet()


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
                # 空白模式：清空内挖素材相关字段（与 _on_pool_hole_mode_change 保持一致）
                if getattr(d, 'pool_inner_material_image', None) is not None:
                    d.pool_inner_material_image = None
                if d.hole_bg_image is not None:
                    d.hole_bg_image = None
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
        # 内挖素材：同步 pool_inner_material_image 与 hole_bg_image（仅素材填充模式）
        hm_now = self._pool_hole_mode.currentData() if hasattr(self, '_pool_hole_mode') else None
        if hm_now == "image":
            # 防御性 getattr：兼容旧 CropDesign 实例（未声明 pool_inner_material_image 字段）
            cur_inner = getattr(d, 'pool_inner_material_image', None)
            if d.hole_bg_image and (cur_inner is None or cur_inner != d.hole_bg_image):
                d.pool_inner_material_image = d.hole_bg_image

        # ===== [MULTI-HOLE Add-On 2026-08-29] 多洞 SpinBox → design.pool_holes_cm/gaps =====
        # 仅当 pool_is_multi_hole=True 且 UI 已构建多洞控件时，才把控件值同步回 design。
        # 单洞模式下：pool_is_multi_hole=False → pool_holes_cm 默认为空 → 零行为影响；
        # 旧单洞 L 形/椭圆/矩形 代码完全不经过这里。
        try:
            if (getattr(d, 'pool_is_multi_hole', False)
                    and hasattr(self, '_mh_sp_hole_w')
                    and isinstance(self._mh_sp_hole_w, list)
                    and len(self._mh_sp_hole_w) >= 2):
                # ==== 严格按「激活洞数」取数据：避免 8 个 SpinBox 预分配 0 值被整体写回 ====
                # active_count 由 _fill_multi_hole_ui(n) / _hide_multi_hole_ui() 维护；
                # 检测为 2 洞 → N=2 → 只写回洞1/洞2 + 间1_2，其他洞3..洞8 不写入 status / mask。
                active_count = int(getattr(self, '_mh_active_count', 0) or 0)
                if active_count < 2:
                    active_count = 0
                n_holes = max(0, min(active_count, len(self._mh_sp_hole_w)))
                n_gaps = max(0, min(n_holes - 1, len(getattr(self, '_mh_sp_gaps', []) or [])))
                if n_holes < 2:
                    # 激活洞数不足 2 → 把多洞字段清空（后续 mask 退回单洞分支，保证单洞语义正确）
                    try:
                        d.pool_holes_cm = []
                        d.pool_holes_gaps_cm = []
                        setattr(d, 'pool_is_multi_hole', False)
                    except Exception:
                        pass
                else:
                    # 优先用 design 上存的 pool_layout_type；取不到则退化 horizontal（横排占 90% 业务）
                    layout = getattr(d, 'pool_layout_type', None) or 'horizontal'
                    ox_cm = d.outer_margin_cm
                    oy_cm = d.outer_margin_cm
                    # 洞宽/高：range(n_holes) 限定前 N 个 SpinBox
                    new_wh = []
                    for i in range(n_holes):
                        wv = max(0.0, self._mh_sp_hole_w[i].value())
                        hv = max(0.0, self._mh_sp_hole_h[i].value())
                        new_wh.append((wv, hv))
                    # 间距：range(n_gaps) 限定前 N-1 个 SpinBox
                    new_gaps = []
                    for i in range(n_gaps):
                        new_gaps.append(max(0.0, self._mh_sp_gaps[i].value()))
                    # 按 layout 重算绝对坐标（画布相对 cm）
                    # ===== [PER-HOLE Add-On 2026-08-29] 每洞独立 mt/ml 坐标 =====
                    # Staff Engineer Mode：不改变外层 if-elif-else 分支结构，只在各分支
                    # 内部把「共享全局 mt/ml」改为「每洞独立 mt_i/ml_i」。
                    # 来源优先级：原 pool_holes_cm[i].mt_cm > 设计文件 hole[i].margin_top_cm
                    # > 全局 inner_margin_top_cm（保证用户手动微调 SpinBox 时不会崩）。
                    old_holes = getattr(d, 'pool_holes_cm', []) or []

                    def _mt_i(i, default_mt):
                        if 0 <= i < len(old_holes):
                            v = old_holes[i].get('mt_cm', 0.0)
                            if v and v > 0:
                                return v
                        # 若原 pool_holes_cm 没存：尝试从 sketch hole attr 再 fallback
                        h_attr = getattr(d, '_mh_hole_margins', None)
                        if isinstance(h_attr, list) and 0 <= i < len(h_attr):
                            v = h_attr[i].get('mt_cm', 0.0)
                            if v and v > 0:
                                return v
                        return default_mt

                    def _mb_i(i, default_mb):
                        if 0 <= i < len(old_holes):
                            v = old_holes[i].get('mb_cm', 0.0)
                            if v and v > 0:
                                return v
                        return default_mb

                    def _ml_i(i, default_ml):
                        if 0 <= i < len(old_holes):
                            v = old_holes[i].get('ml_cm', 0.0)
                            if v and v > 0:
                                return v
                        # 洞0 特殊：左距就是全局 ml；其他洞 ml_i=0（由中距保证位置）
                        if i == 0:
                            return default_ml
                        return 0.0

                    def _mr_i(i, default_mr):
                        if 0 <= i < len(old_holes):
                            v = old_holes[i].get('mr_cm', 0.0)
                            if v and v > 0:
                                return v
                        # 最后一洞特殊：右距就是全局 mr
                        if i == n_holes - 1:
                            return default_mr
                        return 0.0

                    new_holes_cm = []
                    if layout == 'horizontal':
                        shared_ml = d.inner_margin_left_cm
                        shared_mt = d.inner_margin_top_cm
                        cursor_x = ox_cm + _ml_i(0, shared_ml)
                        for i, (wv, hv) in enumerate(new_wh):
                            if i > 0 and i - 1 < len(new_gaps):
                                cursor_x += new_gaps[i - 1]
                            hmt = _mt_i(i, shared_mt)
                            hmb = _mb_i(i, d.inner_margin_bottom_cm)
                            hml = _ml_i(i, shared_ml)
                            hmr = _mr_i(i, d.inner_margin_right_cm)
                            new_holes_cm.append({
                                'x_cm': cursor_x,
                                'y_cm': oy_cm + hmt,   # 每洞独立 mt_i！
                                'w_cm': wv, 'h_cm': hv,
                                'mt_cm': hmt, 'mb_cm': hmb,
                                'ml_cm': hml, 'mr_cm': hmr,
                            })
                            cursor_x += wv
                    elif layout == 'vertical':
                        shared_ml = d.inner_margin_left_cm
                        shared_mt = d.inner_margin_top_cm
                        cursor_y = oy_cm + _mt_i(0, shared_mt)
                        for i, (wv, hv) in enumerate(new_wh):
                            if i > 0 and i - 1 < len(new_gaps):
                                cursor_y += new_gaps[i - 1]
                            hmt = _mt_i(i, shared_mt)
                            hmb = _mb_i(i, d.inner_margin_bottom_cm)
                            hml = _ml_i(i, shared_ml)
                            hmr = _mr_i(i, d.inner_margin_right_cm)
                            new_holes_cm.append({
                                'x_cm': ox_cm + hml,  # 每洞独立 ml_i！
                                'y_cm': cursor_y,
                                'w_cm': wv, 'h_cm': hv,
                                'mt_cm': hmt, 'mb_cm': hmb,
                                'ml_cm': hml, 'mr_cm': hmr,
                            })
                            cursor_y += hv
                    else:  # mixed：退化横排
                        shared_ml = d.inner_margin_left_cm
                        shared_mt = d.inner_margin_top_cm
                        cursor_x = ox_cm + _ml_i(0, shared_ml)
                        for i, (wv, hv) in enumerate(new_wh):
                            if i > 0 and i - 1 < len(new_gaps):
                                cursor_x += new_gaps[i - 1]
                            hmt = _mt_i(i, shared_mt)
                            hmb = _mb_i(i, d.inner_margin_bottom_cm)
                            hml = _ml_i(i, shared_ml)
                            hmr = _mr_i(i, d.inner_margin_right_cm)
                            new_holes_cm.append({
                                'x_cm': cursor_x,
                                'y_cm': oy_cm + hmt,
                                'w_cm': wv, 'h_cm': hv,
                                'mt_cm': hmt, 'mb_cm': hmb,
                                'ml_cm': hml, 'mr_cm': hmr,
                            })
                            cursor_x += wv
                    # 写回 design：pool_holes_cm 长度严格 == n_holes，不会含 0 值洞
                    d.pool_holes_cm = new_holes_cm
                    d.pool_holes_gaps_cm = new_gaps
        except Exception as e:
            # 静默失败：不影响主预览流程
            import logging as _logging
            _logging.getLogger(__name__).warning(f"[Multi-hole UI] 多洞字段回写 design 失败: {e}")


    def _apply_quiet(self):
        """属性变动时：静默触发预览，按钮统一 apply 也会调用"""
        self._collect()
        self._update_layers_label()
        self.design_changed.emit(self.design)


    def apply(self):
        self._apply_quiet()


    def _load_from_design(self):
        self._update_layers_label()


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

