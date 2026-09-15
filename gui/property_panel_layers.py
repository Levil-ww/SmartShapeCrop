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
from services.parser.name_parser import parse_filename
from services.parser.template_matcher import TemplateMatcher
from core.app_settings import get_app_settings
from services.sketch_parser import validate_sketch_file
from services.sketch_parser.sketch_parser import _SKETCH_ACCEPT_EXT, get_tesseract_status

logger = logging.getLogger(__name__)

from .property_panel_widgets import ColorButton, _SketchDropLabel
from workers.property_panel_workers import PoolRenderWorker, _SketchParseWorker
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
        # ===== [H-10] Model 驱动组装 =====
        # 原实现直接读 SpinBox/ComboBox/颜色按钮并内嵌模式判断、素材同步、
        # 多洞几何重建等业务规则。现改为：UI 层只提取纯值 dict（snap），
        # 业务规则全部由 DesignModel.apply_ui_snapshot() 执行（行为等价）。
        # model 惰性创建并每次 sync_from_design：self.design 可能被
        # _on_pool_finished_ok / main.py 整体替换引用，必须跟随最新引用。
        from models.design_model import DesignModel
        model = getattr(self, '_model', None)
        if model is None:
            model = DesignModel(self.design)
            self._model = model
        model.sync_from_design(self.design)
        model.apply_ui_snapshot(self._collect_ui_snapshot())

    def _collect_ui_snapshot(self) -> dict:
        """[H-10] 从控件提取纯值快照（不含任何业务规则，只读控件当前值）。

        返回值直接传给 DesignModel.apply_ui_snapshot()。所有模式判断、
        素材同步、多洞几何重建逻辑均位于 Model 层。
        """
        # L 形参数：无 LShapePanel 时传 None（Model 内写默认值 br/0.0/0.0）
        if self._lshape_panel is not None:
            lshape = {
                'corner': self._lshape_panel.get_corner(),
                'cut_w_cm': self._lshape_panel.get_cut_w_cm(),
                'cut_h_cm': self._lshape_panel.get_cut_h_cm(),
                'cuts_cm': self._lshape_panel.get_cuts_cm(),
            }
        else:
            lshape = None
        # 水池挖空方式：控件可缺失（_build_ui 中途）时传 None
        _pool_hole_mode = getattr(self, '_pool_hole_mode', None)
        hole_mode = _pool_hole_mode.currentData() if _pool_hole_mode is not None else None
        snap = {
            'canvas_w_cm': self._sp_w.value(),
            'canvas_h_cm': self._sp_h.value(),
            'dpi': self._sp_dpi.value(),
            'mode': self._cb_mode.currentData(),
            'outer_margin_cm': self._sp_outer_margin.value(),
            'inner': {
                'top': self._sp_mt.value(),
                'bottom': self._sp_mb.value(),
                'left': self._sp_ml.value(),
                'right': self._sp_mr.value(),
            },
            'lshape': lshape,
            'corners': {
                'tl': self._sp_design_corners['tl'].value(),
                'tr': self._sp_design_corners['tr'].value(),
                'bl': self._sp_design_corners['bl'].value(),
                'br': self._sp_design_corners['br'].value(),
            },
            'ellipse': {'rx': self._sp_erx.value(), 'ry': self._sp_ery.value()},
            'colors': {
                'outer': self._btn_outer_color.color(),
                'hole': self._btn_hole_color.color(),
            },
            'images': {
                'outer': self._ed_outer_img.text().strip() or None,
                'hole': self._ed_hole_img.text().strip() or None,
            },
            'text': {
                'enabled': self._gb_txt.isChecked(),
                'text': self._ed_txt.text(),
                'font_size_px': self._sp_fs.value(),
                'color': self._btn_txt_color.color(),
                'mirror_bottom': self._ck_mirror.isChecked(),
            },
            'hole_mode': hole_mode,
        }
        # —— 多洞控件值提取（严格限 active_count 范围内取前 N 个）——
        # 与原 _collect 相同的防御：任一前置不满足 → multihole=None → Model 跳过。
        # 注意：active_count<2 也构造 multihole（holes_w 为空/不足），
        # 由 Model 侧走"激活洞数不足 2 → 清空多洞字段"分支，语义与原 _collect 一致。
        try:
            if (getattr(self.design, 'pool_is_multi_hole', False)
                    and hasattr(self, '_mh_sp_hole_w')
                    and isinstance(self._mh_sp_hole_w, list)
                    and len(self._mh_sp_hole_w) >= 2):
                active_count = int(getattr(self, '_mh_active_count', 0) or 0)
                _W = self._mh_sp_hole_w
                _H = self._mh_sp_hole_h
                _G = getattr(self, '_mh_sp_gaps', []) or []
                snap['multihole'] = {
                    'active_count': active_count,
                    'holes_w': [float(_W[i].value()) for i in range(min(active_count, len(_W)))],
                    'holes_h': [float(_H[i].value()) for i in range(min(active_count, len(_H)))],
                    'gaps': [float(_G[i].value()) for i in range(min(active_count - 1, len(_G)))],
                    # 每洞独立边距 SpinBox（可缺失；值非正时 Model 内 fallback 到 old_holes）
                    'mt': [float(_W[i].value()) and (getattr(self, '_mh_sp_mt', None) or [])[i].value()
                           if hasattr(self, '_mh_sp_mt') and isinstance(getattr(self, '_mh_sp_mt', None), list)
                           and i < len(self._mh_sp_mt) else None
                           for i in range(min(active_count, len(_W)))],
                    'mb': [float(_W[i].value()) and (getattr(self, '_mh_sp_mb', None) or [])[i].value()
                           if hasattr(self, '_mh_sp_mb') and isinstance(getattr(self, '_mh_sp_mb', None), list)
                           and i < len(self._mh_sp_mb) else None
                           for i in range(min(active_count, len(_W)))],
                    'ml': [float(_W[i].value()) and (getattr(self, '_mh_sp_ml', None) or [])[i].value()
                           if hasattr(self, '_mh_sp_ml') and isinstance(getattr(self, '_mh_sp_ml', None), list)
                           and i < len(self._mh_sp_ml) else None
                           for i in range(min(active_count, len(_W)))],
                    'mr': [float(_W[i].value()) and (getattr(self, '_mh_sp_mr', None) or [])[i].value()
                           if hasattr(self, '_mh_sp_mr') and isinstance(getattr(self, '_mh_sp_mr', None), list)
                           and i < len(self._mh_sp_mr) else None
                           for i in range(min(active_count, len(_W)))],
                }
        except Exception:
            # 控件未初始化或结构异常：按单洞语义跳过多洞回写
            pass
        return snap


    def _apply_quiet(self):
        """属性变动时：静默触发预览，按钮统一 apply 也会调用"""
        self._collect()
        self._update_layers_label()
        self.design_changed.emit(self.design)


    def apply(self):
        self._apply_quiet()

    # ====================================================================
    # [N-P2-13] 防抖机制已删除（2026-09-05 交互范式切换后 valueChanged 信号
    # 全部 DISCONNECTED，渲染由显式生成按钮驱动，_schedule_apply_quiet 无活跃调用方，
    # 属死代码清理，见 property_panel.py 交互范式说明）。
    # ====================================================================

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
            from services.psd.loader import export_psd_layers_as_jpgs
            paths = export_psd_layers_as_jpgs(psd, out_dir, auto_crop=True)
            QMessageBox.information(self, "导出完成",
                                    f"成功导出 {len(paths)} 个图层到:\n{out_dir}")
            self.export_psd_requested.emit(out_dir)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

