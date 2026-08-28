"""gui/property_panel 子模块 —— 一键生成与进度回调（_GenerateMixin，4 方法）（由 property_panel.py 拆分而来，facade 模式）。

原文件 gui/property_panel.py 保留为 facade（PropertyPanel 主类 + 编排），
本模块只包含 一键生成与进度回调（_GenerateMixin，4 方法） 相关的实现，逻辑与原文件完全一致。
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

class _GenerateMixin:
    def _pool_run_generate(self):
        if self._pool_worker is not None and self._pool_worker.isRunning():
            QMessageBox.information(self, "提示", "正在处理中，请稍候…")
            return

        # V2.0 修复：EXE 模式下用户机器可能未安装 Tesseract-OCR，在执行草图解析前
        # 先主动检查引擎状态，给出明确的安装指引，而不是解析后模糊提示"未识别"
        if self._sketch_path and os.path.isfile(self._sketch_path):
            try:
                status = get_tesseract_status()
                if not status.get("available"):
                    hint = status.get("reason", "OCR 引擎不可用")
                    self._set_pool_status(
                        f"⚠️ 草图OCR引擎未就绪：\n{hint}\n"
                        f"（无OCR也可生成预览，但边距仅靠几何估算，建议安装Tesseract-OCR后重试）",
                        is_error=True,
                    )
                    logger.warning(f"[PropertyPanel] OCR引擎未就绪，将跳过OCR层：{hint}")
            except Exception:
                pass

        target_name = self._pool_target.text().strip()
        if not target_name:
            self._set_pool_status("请先填写或选择目标文件名", is_error=True)
            return

        tpl_dir = self._pool_tpl_dir.lineEdit().text().strip()
        if not tpl_dir or not os.path.isdir(tpl_dir):
            self._set_pool_status("请先选择正确的模板库目录", is_error=True)
            return

        # [Fix 2026-08-28] 检测用户手动修改的边距值
        # 当用户在 UI 上修改了边距 SpinBox 值（与草图识别结果不同），
        # 将修改后的值传递给 Worker，确保 CropDesign 和素材匹配都使用
        # 用户修正后的内挖尺寸。
        user_margins = self._detect_user_margin_edits()

        # 启动 Worker
        self._pool_btn_generate.setEnabled(False)
        self._pool_btn_generate.setText("处理中…请稍候")
        worker = PoolRenderWorker(
            self._matcher, tpl_dir, target_name, self._sketch_path,
            pre_parsed_result=self._sketch_parse_result,
            user_margins=user_margins,
            parent=self)
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
        logger.info(f"[PropertyPanel] _on_pool_finished_ok 被调用: sketch_result.success={getattr(sketch_result, 'success', 'N/A')}")
        try:
            # 1) 把 Worker 构建的设计写回 self.design，并同步到所有 SpinBox / 控件
            self.design = design
            # 同步挖空方式 ComboBox
            hm = self._pool_hole_mode.currentData()
            if hm == "blank":
                self.design.pool_hole_transparent = True
                # 空白模式：清理内挖素材字段，防御 sketch 解析流程中可能的残留值
                if getattr(self.design, 'pool_inner_material_image', None) is not None:
                    self.design.pool_inner_material_image = None
                if self.design.hole_bg_image is not None:
                    self.design.hole_bg_image = None
                self._ed_hole_img.setText("")
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

            # 3) 内挖素材自动匹配（仅当挖空方式为"素材填充"时）
            inner_match_info = ""
            if hm == "image":
                try:
                    inner_w_cm = self.design.canvas_w_cm - self.design.inner_margin_left_cm - self.design.inner_margin_right_cm
                    inner_h_cm = self.design.canvas_h_cm - self.design.inner_margin_top_cm - self.design.inner_margin_bottom_cm
                    target_name = self._pool_target.text().strip()
                    if target_name and inner_w_cm > 0 and inner_h_cm > 0:
                        import re
                        # 用正则替换原目标文件名中的尺寸部分为内挖尺寸
                        # 原格式: ...-{W}x{H}CM... → 替换为内挖尺寸
                        dim_match = re.search(
                            r'(\d+\.?\d*)\s*[xX×]\s*(\d+\.?\d*)\s*[Cc][Mm]',
                            target_name
                        )
                        if dim_match:
                            new_dim = f'{inner_w_cm:.1f}x{inner_h_cm:.1f}CM'
                            inner_query = (target_name[:dim_match.start()]
                                           + new_dim
                                           + target_name[dim_match.end():])
                        else:
                            # 兜底：构造标准查询格式
                            from core.parser.name_parser import parse_filename
                            p = parse_filename(target_name)
                            pat = p.pool_pattern_name or p.pattern_name or ""
                            inner_query = f"{pat}-裁剪有图-{inner_w_cm:.1f}x{inner_h_cm:.1f}CM" if pat else ""

                        if inner_query:
                            self._set_pool_status(f"正在匹配内挖素材…", is_error=False)
                            QApplication.processEvents()
                            self._matcher.scan_library(force=False)
                            best_inner, _ = self._matcher.find_best_match(inner_query)
                            if best_inner is not None:
                                self.design.pool_inner_material_image = best_inner.path
                                self.design.hole_bg_image = best_inner.path
                                inner_match_info = f"内挖素材：{os.path.basename(best_inner.path)} (score={best_inner.score:.1f})\n"
                                self._ed_hole_img.setText(best_inner.path)
                            else:
                                inner_match_info = "内挖素材：未找到匹配（请手动选择）\n"
                        else:
                            inner_match_info = "内挖素材：无花型名，跳过自动匹配\n"
                except Exception as e:
                    logger.warning(f"[PropertyPanel] 内挖素材自动匹配失败: {e}")
                    inner_match_info = f"内挖素材匹配异常：{e}\n"

            # 4) 触发预览（先于复杂状态消息，确保即使消息失败也能预览）
            self._set_pool_status("正在生成预览图…", is_error=False)
            QApplication.processEvents()
            self._apply_quiet()
            logger.info("[PropertyPanel] 预览已生成")
            # 记录目标文件名到历史（保留 3 天）
            self._pool_record_target_name_history()

            # 5) 结果提示（try/except 防止状态消息失败导致整个流程中断）
            try:
                info = f"✅ 生成成功！\n"
                info += f"画布：{self.design.canvas_w_cm:.1f} × {self.design.canvas_h_cm:.1f} cm\n"
                # 内挖由设计派生值：canvas - margin_left - margin_right
                inner_w_cm = self.design.canvas_w_cm - self.design.inner_margin_left_cm - self.design.inner_margin_right_cm
                inner_h_cm = self.design.canvas_h_cm - self.design.inner_margin_top_cm - self.design.inner_margin_bottom_cm
                info += f"内挖：{inner_w_cm:.1f} × {inner_h_cm:.1f} cm\n"
                info += (f"边距：上{self.design.inner_margin_top_cm:.1f}/下{self.design.inner_margin_bottom_cm:.1f}/"
                         f"左{self.design.inner_margin_left_cm:.1f}/右{self.design.inner_margin_right_cm:.1f} cm\n")
                # 内挖素材匹配结果
                if inner_match_info:
                    info += inner_match_info
                if sketch_result is not None and sketch_result.success:
                    sr = sketch_result
                    info += f"识别草图成功：\n"
                    info += f"  外框：{sr.outer_w_cm:.1f} × {sr.outer_h_cm:.1f} cm\n"
                    info += f"  内挖：{(sr.outer_w_cm - sr.margin_left_cm - sr.margin_right_cm):.1f} × {(sr.outer_h_cm - sr.margin_top_cm - sr.margin_bottom_cm):.1f} cm\n"
                    info += f"  边距：上{sr.margin_top_cm:.1f}/下{sr.margin_bottom_cm:.1f}/左{sr.margin_left_cm:.1f}/右{sr.margin_right_cm:.1f} cm\n"
                    if hasattr(sr, 'debug') and sr.debug:
                        dir_vals = sr.debug.get("direction_margins", {}) if isinstance(sr.debug, dict) else {}
                        if dir_vals:
                            try:
                                dir_mt = self._safe_dir_val2(dir_vals.get("margin_top", 0))
                                dir_mb = self._safe_dir_val2(dir_vals.get("margin_bottom", 0))
                                dir_ml = self._safe_dir_val2(dir_vals.get("margin_left", 0))
                                dir_mr = self._safe_dir_val2(dir_vals.get("margin_right", 0))
                                if any(v > 0 for v in [dir_mt, dir_mb, dir_ml, dir_mr]):
                                    info += f"  🔤 方向标注：上{dir_mt:.1f}/下{dir_mb:.1f}/左{dir_ml:.1f}/右{dir_mr:.1f} cm\n"
                            except Exception:
                                pass
                elif sketch_result is not None and not sketch_result.success:
                    info += f"草图未识别（请检查/手动调整边距）：{sketch_result.message}\n"
                else:
                    info += f"草图未上传或未识别\n"
                if self.design.pool_outer_material_image:
                    info += f"匹配素材：{os.path.basename(self.design.pool_outer_material_image)}\n"
                self._set_pool_status(info)
            except Exception as e:
                logger.exception(f"[PropertyPanel] 状态消息构造失败: {e}")
                self._set_pool_status(f"✅ 生成成功！（预览已生成，状态消息解析失败：{e}）")

        except Exception as e:
            logger.exception(f"[PropertyPanel] _on_pool_finished_ok 异常: {e}")
            try:
                self._set_pool_status(f"✅ 生成完成（部分失败：{e}）")
                self._apply_quiet()
            except Exception:
                pass

    def _detect_user_margin_edits(self) -> dict:
        """检测用户是否手动修改了边距 SpinBox 值（相对于草图识别结果）。

        当草图识别成功后，用户可能在 UI 上手动修正边距值。此方法
        对比当前 SpinBox 值与 _sketch_parse_result 中的值，若不同则
        返回包含用户修正值的 dict，供 PoolRenderWorker 使用。

        Returns:
            dict: {'top': float, 'bottom': float, 'left': float, 'right': float}
                  若没有用户修改，返回空 dict。
        """
        result = {}
        sr = self._sketch_parse_result
        if sr is None or not getattr(sr, 'success', False):
            return result

        # 对比容差：0.01cm 以内视为相同（避免浮点精度问题）
        TOL = 0.01

        pairs = [
            ('top', self._sp_mt.value(), getattr(sr, 'margin_top_cm', 0)),
            ('bottom', self._sp_mb.value(), getattr(sr, 'margin_bottom_cm', 0)),
            ('left', self._sp_ml.value(), getattr(sr, 'margin_left_cm', 0)),
            ('right', self._sp_mr.value(), getattr(sr, 'margin_right_cm', 0)),
        ]

        for key, current_val, sr_val in pairs:
            if abs(current_val - sr_val) > TOL:
                result[key] = current_val
                logger.info(f"[PropertyPanel] 检测到用户修改边距 {key}: "
                            f"{sr_val:.2f} → {current_val:.2f}")

        return result

