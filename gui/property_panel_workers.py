"""gui/property_panel 子模块 —— 后台 Worker 层（水池渲染 / 草图解析线程）（由 property_panel.py 拆分而来，facade 模式）。

原文件 gui/property_panel.py 保留为 facade（PropertyPanel 主类 + 编排），
本模块只包含 后台 Worker 层（水池渲染 / 草图解析线程） 相关的实现，逻辑与原文件完全一致。
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
                 pre_parsed_result=None,
                 parent=None):
        super().__init__(parent)
        self._matcher = matcher
        self._template_dir = template_dir
        self._target = target_filename
        self._sketch = sketch_path
        self._pre_parsed = pre_parsed_result  # 预解析结果（来自 PropertyPanel 自动解析）
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

            # —— 水池/裁剪有图 模式：尺寸方向已由 name_parser 统一为标准规则 ——
            # 所有模式（包括水池）都使用 "长边为宽、短边为高" 规则
            # oriented_outer_w_h_cm() 现在直接返回 (width_cm, height_cm)，不再做二次交换
            is_pool = parsed.is_pool_mode()
            file_w, file_h = parsed.oriented_outer_w_h_cm()
            if is_pool:
                self._log(
                    f"水池模式尺寸解析：文件名尺寸 {parsed.width_cm}x{parsed.height_cm} "
                    f"→ 画布 宽{file_w} x 高{file_h}"
                )

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
            canvas_w_cm = file_w   # 原始文件名外框宽（横边）
            canvas_h_cm = file_h   # 原始文件名外框高（竖边）
            if self._sketch and os.path.isfile(self._sketch):
                # 优先使用 PropertyPanel 预解析结果（避免重复解析、保留用户调整）
                if self._pre_parsed is not None and self._pre_parsed.success:
                    sketch_result = self._pre_parsed
                    self._log("使用界面已解析的草图结果（跳过重复解析）")
                else:
                    self.progress.emit(60, "解析尺寸草图（几何检测 + OCR 识别）…")
                    try:
                        from core.pool_designer import parse_sketch
                        def _sketch_progress(pct, msg):
                            self.progress.emit(int(60 + pct * 0.25), msg)
                        sketch_result = parse_sketch(
                            self._sketch,
                            target_outer_w_cm=file_w,   # 外框参考宽（横边，无损耗）
                            target_outer_h_cm=file_h,   # 外框参考高（竖边，无损耗）
                            progress_callback=_sketch_progress,
                        )
                        self._log(f"草图解析：success={sketch_result.success}")
                        self._log(f"  {sketch_result.message}")
                    except Exception as e:
                        self._log(f"草图解析异常（忽略，仍可继续手动输入）：{e}")
                # 如果草图识别出了外框总尺寸，优先使用（以草图为准）
                if sketch_result and sketch_result.success and sketch_result.outer_w_cm > 0:
                    canvas_w_cm = sketch_result.outer_w_cm
                if sketch_result and sketch_result.success and sketch_result.outer_h_cm > 0:
                    canvas_h_cm = sketch_result.outer_h_cm

            # 4) 构建 CropDesign
            #    画布尺寸 = 目标尺寸 + 1cm 损耗（裁剪余料用）
            TRIM_CM = 1.0
            self.progress.emit(85, "构建设计参数…")
            design = CropDesign()
            design.canvas_w_cm = canvas_w_cm + TRIM_CM
            design.canvas_h_cm = canvas_h_cm + TRIM_CM
            design.dpi = 150
            design.mode = 'rect_hole'
            design.outer_margin_cm = 0.0   # 水池默认不额外留白（花纹图本身就是外框）

            # 边距优先用草图，否则用默认等比例值（10% 短边）
            # [契约变更 2026-08-27] 画布已 +TRIM_CM(1cm) 作为裁剪损耗，
            # 草图识别到的 4 个边距视为设计真值，不再追加 +TRIM_CM 偏移。
            # 内挖由 inner = canvas - sum(margins) 自动推导，因此 inner 相对
            # sketch 原始内框自动 +1cm（损耗分摊到内挖区域，不挤占边距）。
            # 新不变量：(outer+1) = ml + inner_w + mr；(outer+1)_v = mt + inner_h + mb
            if sketch_result and sketch_result.success:
                design.inner_margin_top_cm = sketch_result.margin_top_cm
                design.inner_margin_bottom_cm = sketch_result.margin_bottom_cm
                design.inner_margin_left_cm = sketch_result.margin_left_cm
                design.inner_margin_right_cm = sketch_result.margin_right_cm
            else:
                default_m = min(canvas_w_cm, canvas_h_cm) * 0.10
                design.inner_margin_top_cm = default_m
                design.inner_margin_bottom_cm = default_m
                design.inner_margin_left_cm = default_m
                design.inner_margin_right_cm = default_m

            # 水池模式开关 + 外框素材图
            design.pool_hole_transparent = True
            design.pool_outer_material_image = best.path
            # 同时也写到外框素材字段（方便用户在"背景设置"里看到并编辑）
            design.outer_bg_image = best.path
            # [Fix 2026-08-26] 传递素材原始设计方向尺寸（文件名方向，未经过oriented交换）
            # 渲染时用它判断素材是否需要旋转90度后再等比缩放（避免cover过度裁剪 / stretch变形）
            try:
                # best 是 TemplateEntry；取文件名字段中 _width_cm / _height_cm（原始方向）
                from_core = getattr(best, '_width_cm', 0) or 0
                from_core_h = getattr(best, '_height_cm', 0) or 0
                if from_core > 0 and from_core_h > 0:
                    design.pool_material_design_w_cm = float(from_core)
                    design.pool_material_design_h_cm = float(from_core_h)
                else:
                    # 兜底：从已匹配文件名再解析一次（parse_filename 已在文件顶部全局导入）
                    re_parsed = parse_filename(os.path.splitext(os.path.basename(best.path))[0])
                    if re_parsed and re_parsed.width_cm and re_parsed.height_cm:
                        design.pool_material_design_w_cm = float(re_parsed.width_cm)
                        design.pool_material_design_h_cm = float(re_parsed.height_cm)
                self._log(f"素材设计方向尺寸(原始文件名): {design.pool_material_design_w_cm:.1f}x{design.pool_material_design_h_cm:.1f}cm "
                         f"→ 画布方向: {design.canvas_w_cm:.1f}x{design.canvas_h_cm:.1f}cm")
            except Exception as e:
                self._log(f"素材设计方向写入失败（渲染退化）: {e}")

            # 预加载模板图到内存缓存（渲染时直接使用，避免主线程网络读取阻塞）
            self.progress.emit(92, "预加载素材图…")
            try:
                from core.image_ops import load_image_rgb
                cached_img = load_image_rgb(best.path)
                design._cached_outer_image = cached_img
                self._log(f"素材预加载完成: {cached_img.size[0]}x{cached_img.size[1]}px")
            except Exception as e:
                self._log(f"素材预加载失败（渲染时重试）: {e}")

            # 多层边框：水池模式下保留默认边框层（黑-白-黑），用户可在【多层边框】区修改或删除
            # 若素材图本身已有边框，用户可手动清空 borders 列表

            self.progress.emit(100, "完成！")
            self.finished_ok.emit(design, sketch_result, "\n".join(self._log_lines))

        except Exception as e:
            logger.exception("PoolRenderWorker 异常终止")
            self.finished_err.emit(f"处理失败：{e}")



class _SketchParseWorker(QThread):
    """草图异步解析 Worker：在后台线程跑 parse_sketch，避免阻塞主线程导致草图图片不能立即显示。"""

    finished_ok = pyqtSignal(object)      # 成功：SketchParseResult
    finished_err = pyqtSignal(str)        # 异常：错误消息

    def __init__(self, sketch_path: str, target_w: float, target_h: float, parent=None):
        super().__init__(parent)
        self._sketch_path = sketch_path
        self._target_w = target_w
        self._target_h = target_h

    def run(self):
        try:
            from core.pool_designer import parse_sketch
            result = parse_sketch(
                self._sketch_path,
                target_outer_w_cm=self._target_w,
                target_outer_h_cm=self._target_h,
            )
            # 若已被新解析取代（requestInterruption），不再发射旧结果，避免覆盖新结果
            if self.isInterruptionRequested():
                logger.info("[SketchParseWorker] 已被取消，丢弃旧解析结果")
                return
            self.finished_ok.emit(result)
        except Exception as e:
            logger.exception("草图后台解析异常")
            if self.isInterruptionRequested():
                return
            self.finished_err.emit(str(e))

