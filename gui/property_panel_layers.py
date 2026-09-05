"""gui/property_panel 子模块 —— 图层编辑与收集（_LayersMixin，9 方法）（由 property_panel.py 拆分而来，facade 模式）。

原文件 gui/property_panel.py 保留为 facade（PropertyPanel 主类 + 编排），
本模块只包含 图层编辑与收集（_LayersMixin，9 方法） 相关的实现，逻辑与原文件完全一致。
"""
from __future__ import annotations
import logging
import os
import time
from datetime import date, datetime, timedelta
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QSize, QTimer
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
        # ===== [L-Shape Panel Refactor 2026-09-02] L 形参数从 LShapePanel 读取 =====
        # 原 self._cb_lcorner / _sp_lw / _sp_lh 已迁移到 LShapePanel；
        # 通过 self._lshape_panel.get_corner()/get_cut_w_cm()/get_cut_h_cm() 读取，
        # 语义与原直读控件完全一致。
        if self._lshape_panel is not None:
            d.l_corner = self._lshape_panel.get_corner()
            # 挖角值直接取草图识别的成品真值，不做额外损耗补偿
            d.l_cut_w_cm = self._lshape_panel.get_cut_w_cm()
            d.l_cut_h_cm = self._lshape_panel.get_cut_h_cm()
        else:
            d.l_corner = 'br'
            d.l_cut_w_cm = 0.0
            d.l_cut_h_cm = 0.0
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
            # [2026-09-04 Fix] L 形挖角模式：cut 区域语义就是"挖空/白色"，
            # 强制 pool_hole_transparent=True，跳过 UI 控件覆盖。
            # 否则 PoolWorker 正确设置的 True 会被 _pool_hole_mode=="image" 覆盖为 False，
            # 导致 cut 区域显示米色 hole_bg_color(250,245,230) 而非纯白。
            if d.mode == 'rect_lshape':
                d.pool_hole_transparent = True
            else:
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
            # ===== [SINGLE-HOLE Add-On 2026-08-31] basename 防御 —— basename-only 不覆盖全路径 =====
            # 背景：_on_pool_finished_ok 将 _ed_hole_img 用于显示，有时写入 basename（文件名）、
            #   有时写入 path（全路径）。若 UI 控件中当前仅保留 basename（非有效路径），
            #   却直接用它覆盖 pool_inner（已有的全路径真值） → 后续 render_design 中
            #   os.path.isfile(pool_inner) 失败 → 内挖素材加载链路断裂，退回纯色占位。
            # 修复：只有当 hole_bg_image 本身是有效路径（isfile / isabs / 含目录分隔符）时，
            #   才允许同步覆盖 pool_inner_material_image；否则保留已有全路径真值。
            # 纯加法条件收窄，不改变 hole_bg_image=有效全路径 场景的任何行为。
            _bg_path = d.hole_bg_image
            _bg_path_valid = False
            if _bg_path:
                _bg_path_valid = (
                    os.path.isfile(_bg_path)
                    or os.path.isabs(_bg_path)
                    or (os.path.dirname(_bg_path) != '')
                )
            if _bg_path_valid:
                if _bg_path and (cur_inner is None or cur_inner != _bg_path):
                    d.pool_inner_material_image = _bg_path
            # ===== [END SINGLE-HOLE Add-On basename 防御] =====

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
                    # ===== [PER-HOLE Add-On 2026-08-29 + 2026-09-05 FIX] 每洞独立 mt/ml 坐标 =====
                    # 2026-09-05 FIX: 优先从 UI SpinBox 直接读取（用户手动编辑的值），
                    # 再 fallback 到 old_holes 存储值，最后才用全局默认。
                    old_holes = getattr(d, 'pool_holes_cm', []) or []

                    # 防御性检查：SpinBox 列表是否初始化且足够长
                    _has_mt_sp = (hasattr(self, '_mh_sp_mt') and isinstance(self._mh_sp_mt, list)
                                  and len(self._mh_sp_mt) > i) if 'i' in dir() else False

                    def _mt_i(i, default_mt):
                        # [2026-09-05] 优先从 UI SpinBox 读取（用户手动编辑）
                        if (hasattr(self, '_mh_sp_mt') and isinstance(self._mh_sp_mt, list)
                                and 0 <= i < len(self._mh_sp_mt)):
                            v = self._mh_sp_mt[i].value()
                            if v and v > 0:
                                return v
                        if 0 <= i < len(old_holes):
                            v = old_holes[i].get('mt_cm', 0.0)
                            if v and v > 0:
                                return v
                        h_attr = getattr(d, '_mh_hole_margins', None)
                        if isinstance(h_attr, list) and 0 <= i < len(h_attr):
                            v = h_attr[i].get('mt_cm', 0.0)
                            if v and v > 0:
                                return v
                        return default_mt

                    def _mb_i(i, default_mb):
                        # [2026-09-05] 优先从 UI SpinBox 读取
                        if (hasattr(self, '_mh_sp_mb') and isinstance(self._mh_sp_mb, list)
                                and 0 <= i < len(self._mh_sp_mb)):
                            v = self._mh_sp_mb[i].value()
                            if v and v > 0:
                                return v
                        if 0 <= i < len(old_holes):
                            v = old_holes[i].get('mb_cm', 0.0)
                            if v and v > 0:
                                return v
                        return default_mb

                    def _ml_i(i, default_ml):
                        # [2026-09-05] 优先从 UI SpinBox 读取
                        if (hasattr(self, '_mh_sp_ml') and isinstance(self._mh_sp_ml, list)
                                and 0 <= i < len(self._mh_sp_ml)):
                            v = self._mh_sp_ml[i].value()
                            if v and v > 0:
                                return v
                        if 0 <= i < len(old_holes):
                            v = old_holes[i].get('ml_cm', 0.0)
                            if v and v > 0:
                                return v
                        if i == 0:
                            return default_ml
                        return 0.0

                    def _mr_i(i, default_mr):
                        # [2026-09-05] 优先从 UI SpinBox 读取
                        if (hasattr(self, '_mh_sp_mr') and isinstance(self._mh_sp_mr, list)
                                and 0 <= i < len(self._mh_sp_mr)):
                            v = self._mh_sp_mr[i].value()
                            if v and v > 0:
                                return v
                        if 0 <= i < len(old_holes):
                            v = old_holes[i].get('mr_cm', 0.0)
                            if v and v > 0:
                                return v
                        if i == n_holes - 1:
                            return default_mr
                        return 0.0

                    # ===== [INNER MATERIAL Add-On 2026-08-29] 继承 per-hole 素材字段 =====
                    # _collect() 完全重建 pool_holes_cm（L219-278），会丢失 UI 层素材匹配写入的
                    # inner_material_path / _cached_inner_image / _src_design_w_cm 等。
                    # 在每个洞构建完几何字段后，从 old_holes[i] 继承素材相关键。
                    _MATERIAL_KEYS = ('inner_material_path', '_cached_inner_image',
                                      '_src_design_w_cm', '_src_design_h_cm')

                    def _inherit_material(i):
                        """从 old_holes[i] 继承素材相关字段（_collect 重建会丢失）。"""
                        src = old_holes[i] if (0 <= i < len(old_holes) and isinstance(old_holes[i], dict)) else {}
                        return {k: src[k] for k in _MATERIAL_KEYS if k in src}

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
                            _hole_dict = {
                                'x_cm': cursor_x,
                                'y_cm': oy_cm + hmt,
                                'w_cm': wv, 'h_cm': hv,
                                'mt_cm': hmt, 'mb_cm': hmb,
                                'ml_cm': hml, 'mr_cm': hmr,
                            }
                            _hole_dict.update(_inherit_material(i))
                            new_holes_cm.append(_hole_dict)
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
                            _hole_dict = {
                                'x_cm': ox_cm + hml,
                                'y_cm': cursor_y,
                                'w_cm': wv, 'h_cm': hv,
                                'mt_cm': hmt, 'mb_cm': hmb,
                                'ml_cm': hml, 'mr_cm': hmr,
                            }
                            _hole_dict.update(_inherit_material(i))
                            new_holes_cm.append(_hole_dict)
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
                            _hole_dict = {
                                'x_cm': cursor_x,
                                'y_cm': oy_cm + hmt,
                                'w_cm': wv, 'h_cm': hv,
                                'mt_cm': hmt, 'mb_cm': hmb,
                                'ml_cm': hml, 'mr_cm': hmr,
                            }
                            _hole_dict.update(_inherit_material(i))
                            new_holes_cm.append(_hole_dict)
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

    # ====================================================================
    # 防抖机制（2026-09-03 SpinBox 参数修改卡顿优化）
    # ====================================================================
    # 适用范围：mt/mb/ml/mr 4 条内挖边距 + L 形面板 5 个参数（corner/w/h/outer_w/outer_h）
    # 的 valueChanged 信号连续触发时，不要每一次步进都同步跑 LOD 渲染。
    # 规则：200ms 防抖窗口（连续修改重置）+ 800ms max-wait（最长等 0.8 秒一定渲染）。
    # 显式调用点（用户点 Apply / 生成完成 / 确认对话框 OK / 文件选择 / 模式切换）继续直调
    # _apply_quiet()，不经过防抖，保证即时响应。
    #
    # 时序：用户长按步进（比如连续 8 次 0.1cm 步进）
    #   before: _apply_quiet × 8 次 (主线程 800~4000ms 阻塞) → 用户感觉卡死
    #   after : _schedule_apply_quiet × 8 次（轻量，仅重置 timer）→ 停手 200ms 后
    #           _flush_apply_quiet → _apply_quiet × 1 次（主线程 100~500ms，仅一次阻塞）
    def _init_apply_debouncer(self) -> None:
        """在 PropertyPanel.__init__ 中调用一次，构建防抖 QTimer 与元数据字段。"""
        self._apply_debounce_timer = QTimer(self)
        self._apply_debounce_timer.setSingleShot(True)
        self._apply_debounce_timer.setInterval(200)   # 防抖窗口：200ms
        self._apply_debounce_timer.timeout.connect(self._flush_apply_quiet)
        self._apply_first_pending_ts = 0.0           # 首个 pending 调用的 monotonic 时间戳
        self._APPLY_MAX_WAIT_SEC = 0.800              # 最多等 800ms：强制冲刷，避免长按永远不显示

    def _schedule_apply_quiet(self) -> None:
        """计划一次防抖的 _apply_quiet 调用。主线程本地计算，无 I/O，μs 级返回。"""
        now = time.monotonic()
        if not self._apply_debounce_timer.isActive():
            # 第一个 pending：记录起点
            self._apply_first_pending_ts = now
        # 已经等超过 max-wait：立即同步执行（避免用户持续操作看不到预览）
        if now - self._apply_first_pending_ts >= self._APPLY_MAX_WAIT_SEC:
            self._apply_debounce_timer.stop()
            self._apply_quiet()
            self._apply_first_pending_ts = 0.0
            return
        # （重）启动 200ms 倒计时，等待参数稳定
        self._apply_debounce_timer.start()

    def _flush_apply_quiet(self) -> None:
        """防抖 timer 到期：真正执行一次 _apply_quiet（与直接调用等价）。"""
        self._apply_first_pending_ts = 0.0
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

