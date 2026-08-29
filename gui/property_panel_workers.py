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
                 user_margins: dict = None,
                 lshape_params: dict = None,
                 parent=None):
        """
        user_margins: 可选 dict，包含用户手动修改的边距值。
            键: 'top', 'bottom', 'left', 'right'
            值: float (cm)
        当 user_margins 提供时，会覆盖 pre_parsed_result 中的对应边距值，
        确保素材匹配使用用户修正后的内挖尺寸。

        lshape_params: 可选 dict，L 形挖角模式参数。
            键: 'corner' (tl/tr/bl/br), 'cut_w_cm', 'cut_h_cm', 'outer_w_cm', 'outer_h_cm'
            提供时进入 L 形挖角模式：mode='rect_lshape'，margins 全 0，
            L 形区域保留外框素材、挖掉的角显示洞色（裁剪有图语义）。
        """
        super().__init__(parent)
        self._matcher = matcher
        self._template_dir = template_dir
        self._target = target_filename
        self._sketch = sketch_path
        self._pre_parsed = pre_parsed_result  # 预解析结果（来自 PropertyPanel 自动解析）
        self._user_margins = user_margins  # 用户手动修改的边距（覆盖 pre_parsed）
        self._lshape_params = lshape_params or None  # L 形挖角参数（None = 矩形/普通水池模式）
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
            #    L 形模式：草图已在 UI 层由 _LShapeParseWorker 解析并经用户确认，
            #    参数已传入 lshape_params，此处不再跑矩形草图解析。
            is_lshape = self._lshape_params is not None
            sketch_result = None
            canvas_w_cm = file_w   # 原始文件名外框宽（横边）
            canvas_h_cm = file_h   # 原始文件名外框高（竖边）
            if self._sketch and os.path.isfile(self._sketch) and not is_lshape:
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

            # [Fix 2026-08-28] 用户手动修改的边距优先于草图识别结果
            # 当用户在 UI 上手动调整了边距值（SpinBox），这些值通过 user_margins
            # 传入 Worker，在此覆盖 sketch_result 中的对应字段，确保：
            # 1) CropDesign 使用用户修正后的边距
            # 2) 内挖尺寸（canvas - margins）正确
            # 3) 后续素材匹配（内挖素材）基于正确的内挖尺寸
            # L 形模式：margins 恒为 0（L 形 = 画布挖角），跳过该覆盖逻辑。
            if self._user_margins and sketch_result and sketch_result.success and not is_lshape:
                um = self._user_margins
                changed = []
                if 'top' in um and um['top'] is not None:
                    sketch_result.margin_top_cm = float(um['top'])
                    changed.append(f"上:{um['top']:.1f}")
                if 'bottom' in um and um['bottom'] is not None:
                    sketch_result.margin_bottom_cm = float(um['bottom'])
                    changed.append(f"下:{um['bottom']:.1f}")
                if 'left' in um and um['left'] is not None:
                    sketch_result.margin_left_cm = float(um['left'])
                    changed.append(f"左:{um['left']:.1f}")
                if 'right' in um and um['right'] is not None:
                    sketch_result.margin_right_cm = float(um['right'])
                    changed.append(f"右:{um['right']:.1f}")
                if changed:
                    self._log(f"应用用户手动修改的边距：{', '.join(changed)}")

            # 4) 构建 CropDesign
            #    画布尺寸 = 目标尺寸 + 1cm 损耗（裁剪余料用）
            TRIM_CM = 1.0
            self.progress.emit(85, "构建设计参数…")
            design = CropDesign()
            design.canvas_w_cm = canvas_w_cm + TRIM_CM
            design.canvas_h_cm = canvas_h_cm + TRIM_CM
            design.dpi = 150
            design.outer_margin_cm = 0.0   # 水池默认不额外留白（花纹图本身就是外框）

            if is_lshape:
                # —— L 形挖角（裁剪有图）模式 ——
                # 语义：L 形区域保留外框素材花纹，被切掉的角显示洞色。
                # 配置：margins 全 0（L 形 = 画布挖角），mode='rect_lshape'。
                lp = self._lshape_params
                # 外框尺寸优先用 L 形解析结果（已与文件名校验），否则用文件名解析值
                lw = float(lp.get('outer_w_cm') or 0)
                lh = float(lp.get('outer_h_cm') or 0)
                if lw > 0 and lh > 0:
                    canvas_w_cm, canvas_h_cm = lw, lh
                    design.canvas_w_cm = canvas_w_cm + TRIM_CM
                    design.canvas_h_cm = canvas_h_cm + TRIM_CM
                design.mode = 'rect_lshape'
                design.l_corner = lp.get('corner', 'tr')
                design.l_cut_w_cm = max(0.0, float(lp.get('cut_w_cm', 0)))
                design.l_cut_h_cm = max(0.0, float(lp.get('cut_h_cm', 0)))
                design.inner_margin_top_cm = 0.0
                design.inner_margin_bottom_cm = 0.0
                design.inner_margin_left_cm = 0.0
                design.inner_margin_right_cm = 0.0
                # 挖掉的角 = 洞（白色；JPG 不支持透明）
                design.pool_hole_transparent = True
                # 外框素材 = 匹配到的完整矩形花纹图（铺满画布，L 形区域保留）
                design.pool_outer_material_image = best.path
                design.outer_bg_image = best.path
                design.pool_inner_material_image = best.path
                self._log(
                    f"L 形挖角模式：corner={design.l_corner}, "
                    f"挖角 {design.l_cut_w_cm:.1f}x{design.l_cut_h_cm:.1f} cm, "
                    f"外框 {canvas_w_cm:.1f}x{canvas_h_cm:.1f} cm（画布含1cm损耗）"
                )
            else:
                design.mode = 'rect_hole'
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

                # ===== [MULTI-HOLE Add-On 2026-08-29] PURE ADD-ON GUARD =====
                # 仅当 sketch_result.is_multi_hole=True 且 holes>=2 时触发。
                # 将 sketch 解析的洞列表转换为「画布相对厘米坐标」绝对位置，
                # 供 image_ops._get_inner_pixel_mask 的 Add-On 分支渲染 UNION mask。
                # 单洞场景下 pool_holes_cm 默认为空 → Add-On 分支跳过 → 旧代码零影响。
                if (sketch_result
                        and sketch_result.success
                        and getattr(sketch_result, 'is_multi_hole', False)
                        and hasattr(sketch_result, 'holes')
                        and isinstance(sketch_result.holes, list)
                        and len(sketch_result.holes) >= 2):
                    holes = sketch_result.holes
                    gaps = list(getattr(sketch_result, 'hole_gaps_cm', []) or [])
                    layout = getattr(sketch_result, 'layout_type', 'horizontal') or 'horizontal'

                    # 画布坐标原点 = (outer_margin_cm, outer_margin_cm)。水池模式下通常=0。
                    ox_cm = design.outer_margin_cm
                    oy_cm = design.outer_margin_cm
                    # 共享 y 起点与洞高：横排洞等高且上下边距共用。
                    # 用 design.inner_margin_top_cm（已被上方 sketch 赋值）作为共享 mt。
                    # 注意：TRIM_CM 已加到 canvas_w/h，但 hole 的 w/h 本身是 草图 真值(cm)，
                    # 我们希望 hole 的绝对位置 = 草图上的绝对位置 + TRIM/2 分布（通过
                    # inner_margin 保持不变来让渲染器在 canvas_w/h = outer+TRIM 的大画布
                    # 上以相同边距定位 hole，即 hole 本身的位置相对画布边缘自然分摊了 TRIM）。
                    shared_mt = design.inner_margin_top_cm
                    shared_ml = holes[0].margin_left_cm if holes else design.inner_margin_left_cm
                    shared_mr = holes[-1].margin_right_cm if holes else design.inner_margin_right_cm

                    if layout == 'horizontal':
                        # y 轴：每个 hole 从 ox_cm + shared_mt 起，高度取 hole[i].h_cm
                        y_cm = oy_cm + shared_mt
                        cursor_x = ox_cm + shared_ml   # 第一个洞的 x 起点
                        for i, h in enumerate(holes):
                            # gap
                            if i > 0 and i - 1 < len(gaps):
                                cursor_x += gaps[i - 1]
                            x_cm = cursor_x
                            w_cm = max(0.0, h.w_cm)
                            h_cm = max(0.0, h.h_cm)
                            design.pool_holes_cm.append({
                                'x_cm': x_cm, 'y_cm': y_cm,
                                'w_cm': w_cm, 'h_cm': h_cm,
                            })
                            cursor_x += w_cm
                    elif layout == 'vertical':
                        # 竖排：x 起点共享 ml；沿 y 轴用 gap 分开
                        shared_mb = design.inner_margin_bottom_cm
                        x_cm = ox_cm + shared_ml
                        cursor_y = oy_cm + shared_mt
                        for i, h in enumerate(holes):
                            if i > 0 and i - 1 < len(gaps):
                                cursor_y += gaps[i - 1]
                            y_cm = cursor_y
                            w_cm = max(0.0, h.w_cm)
                            h_cm = max(0.0, h.h_cm)
                            design.pool_holes_cm.append({
                                'x_cm': x_cm, 'y_cm': y_cm,
                                'w_cm': w_cm, 'h_cm': h_cm,
                            })
                            cursor_y += h_cm
                    else:  # mixed：退化按横排
                        y_cm = oy_cm + shared_mt
                        cursor_x = ox_cm + shared_ml
                        for i, h in enumerate(holes):
                            if i > 0 and i - 1 < len(gaps):
                                cursor_x += gaps[i - 1]
                            design.pool_holes_cm.append({
                                'x_cm': cursor_x, 'y_cm': y_cm,
                                'w_cm': max(0.0, h.w_cm), 'h_cm': max(0.0, h.h_cm),
                            })
                            cursor_x += h.w_cm

                    # 标记：image_ops Add-On 检查该标记和 holes>=2 才触发
                    design.pool_is_multi_hole = True
                    design.pool_holes_gaps_cm = gaps

                    self._log(
                        f"多洞模式写入: N={len(holes)} layout={layout} "
                        f"gaps={[round(g,1) for g in gaps]}"
                    )
                    for i, hc in enumerate(design.pool_holes_cm):
                        self._log(
                            f"  Hole[{i}] 画布位置 x={hc['x_cm']:.1f} y={hc['y_cm']:.1f} "
                            f"size={hc['w_cm']:.1f}x{hc['h_cm']:.1f} cm"
                        )
                # ===== [END ADD-ON] =====

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


class _LShapeParseWorker(QThread):
    """L 形草图异步解析 Worker：后台跑 parse_lshape_sketch（多尺度 OCR，耗时较长）。

    输出：
        finished_ok(LSketchParseResult)  → UI 弹出确认框
        finished_err(str)                → 异常消息
    """

    finished_ok = pyqtSignal(object)
    finished_err = pyqtSignal(str)

    def __init__(self, sketch_path: str, target_w: float, target_h: float, parent=None):
        super().__init__(parent)
        self._sketch_path = sketch_path
        self._target_w = target_w
        self._target_h = target_h

    def run(self):
        try:
            from core.pool_designer import parse_lshape_sketch
            result = parse_lshape_sketch(
                self._sketch_path,
                target_outer_w_cm=self._target_w,
                target_outer_h_cm=self._target_h,
            )
            # 若已被新解析取代（requestInterruption），不再发射旧结果，避免覆盖新结果
            if self.isInterruptionRequested():
                logger.info("[LShapeParseWorker] 已被取消，丢弃旧解析结果")
                return
            self.finished_ok.emit(result)
        except Exception as e:
            logger.exception("L 形草图后台解析异常")
            if self.isInterruptionRequested():
                return
            self.finished_err.emit(str(e))

