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
                 user_multihole_params: dict = None,
                 lshape_params: dict = None,
                 parent=None):
        """
        user_margins: 可选 dict，包含用户手动修改的边距值。
            键: 'top', 'bottom', 'left', 'right'
            值: float (cm)
        当 user_margins 提供时，会覆盖 pre_parsed_result 中的对应边距值，
        确保素材匹配使用用户修正后的内挖尺寸。

        user_multihole_params: 可选 dict，包含用户在多洞参数面板上手动修改后的
            洞宽/洞高/洞间距真值（仅多洞场景使用，单洞必须为 None）。
            形状：{
                'active_count': int,          # 当前激活洞数（>=2 才生效）
                'holes_wh': [(w,h),(w,h),..], # 每洞宽/高，单位 cm；长度==active_count
                'gaps_cm':   [g12, g23,..],   # 洞1↔洞2 / 洞2↔洞3 间距，单位 cm；len==active_count-1
                'layout_type': 'horizontal'|'vertical'|'mixed',
            }
        提供时：在 sketch 多洞分支构建完 design.pool_holes_cm 之后，再用 UI 真值覆盖
        每洞 w/h/间距 + 重算 x/y 画布坐标，确保后续每洞素材匹配与预览几何一致。
        单洞/多洞数据不足 2 洞 → 静默忽略。

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
        # ===== [MULTI-HOLE Add-On 2026-08-29] UI 多洞改动 → Worker 覆盖 =====
        # 单洞（None 或 active_count<2）→ 零行为影响，旧分支不变。
        self._user_multihole = user_multihole_params or None
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
                    # [MULTI-HOLE EXPANSION Add-On] 每个洞尺寸 +1cm（往外扩），间距 -1cm 补偿
                    # 不变量：ml + Σ(w_i+1) + Σ(gap_j-1) + mr = outer + 1 = canvas_w
                    # 单洞已自动 +1（inner=canvas-margins）；多洞需显式扩 + 间距补偿
                    gaps = [max(0.0, g - TRIM_CM) for g in gaps]

                    # ===== [MULTI-HOLE SANITY Add-On 2026-08-29] 全局 mt/mb/ml/mr 覆盖 =====
                    # Bug fix (2026-08-29): 优先使用 sketch_result 的全局已方向锁定值，
                    # 不再从 per-hole HoleInfo 取 min。根因：per-hole margin_left_0
                    # 在 decimal 移位后变成 3.6（应为 36.0），min(36.0, 3.6) = 3.6 → GUI 左边距显示 3.6。
                    # 全局值 sketch_result.margin_left 已被方向/箭头锁定为正确的 36.0，直接使用。
                    # SketchParseResult 属性名是 margin_left_cm / margin_right_cm / ...
                    # （不是 margin_left）。另外兼容 MultiHoleParseResult 的 margin_left。
                    _sr_ml = (getattr(sketch_result, 'margin_left_cm', 0)
                              or getattr(sketch_result, 'margin_left', 0) or 0)
                    _sr_mr = (getattr(sketch_result, 'margin_right_cm', 0)
                              or getattr(sketch_result, 'margin_right', 0) or 0)
                    _sr_mt = (getattr(sketch_result, 'margin_top_cm', 0)
                              or getattr(sketch_result, 'margin_top', 0) or 0)
                    _sr_mb = (getattr(sketch_result, 'margin_bottom_cm', 0)
                              or getattr(sketch_result, 'margin_bottom', 0) or 0)
                    # Per-hole fallback（仅当全局值为 0 时兜底）
                    _all_mt = [getattr(h, 'margin_top_cm', 0) for h in holes if getattr(h, 'margin_top_cm', 0) > 0]
                    _all_mb = [getattr(h, 'margin_bottom_cm', 0) for h in holes if getattr(h, 'margin_bottom_cm', 0) > 0]
                    if _sr_mt > 0:
                        design.inner_margin_top_cm = _sr_mt
                    elif _all_mt:
                        design.inner_margin_top_cm = min(_all_mt)
                    if _sr_mb > 0:
                        design.inner_margin_bottom_cm = _sr_mb
                    elif _all_mb:
                        design.inner_margin_bottom_cm = min(_all_mb)
                    # 左右边距：**优先全局值**（方向锁定的正确性远高于 per-hole）
                    if _sr_ml > 0:
                        design.inner_margin_left_cm = _sr_ml
                    else:
                        _all_ml = [getattr(h, 'margin_left_cm', 0) for h in holes if getattr(h, 'margin_left_cm', 0) > 0]
                        if _all_ml:
                            design.inner_margin_left_cm = min(_all_ml)
                    if _sr_mr > 0:
                        design.inner_margin_right_cm = _sr_mr
                    else:
                        _all_mr = [getattr(h, 'margin_right_cm', 0) for h in holes if getattr(h, 'margin_right_cm', 0) > 0]
                        if _all_mr:
                            design.inner_margin_right_cm = min(_all_mr)
                    self._log(
                        f"[多洞全局边距修正] mt={design.inner_margin_top_cm:.1f} "
                        f"mb={design.inner_margin_bottom_cm:.1f} "
                        f"ml={design.inner_margin_left_cm:.1f} "
                        f"mr={design.inner_margin_right_cm:.1f}"
                    )

                    # 画布坐标原点 = (outer_margin_cm, outer_margin_cm)。水池模式下通常=0。
                    ox_cm = design.outer_margin_cm
                    oy_cm = design.outer_margin_cm
                    # ===== [MULTI-HOLE PER-HOLE Add-On 2026-08-29] per-hole mt_i/ml_i =====
                    # 每个 hole.margin_top_cm 已由 parser 填充：
                    #   Case A (异边距) → per-hole 桶命中 → 独立 mt=20.5 / 21.7
                    #   Case B (同边距) → per-hole 桶空 → fallback 全局 mt=11.5
                    # 防御性 fallback：若某洞 margin_top_cm==0 → 退回共享 mt
                    shared_mt = design.inner_margin_top_cm
                    shared_ml = holes[0].margin_left_cm if holes else design.inner_margin_left_cm
                    shared_mr = holes[-1].margin_right_cm if holes else design.inner_margin_right_cm

                    def _mt_of(h):
                        v = getattr(h, 'margin_top_cm', 0.0)
                        return v if v > 0 else shared_mt
                    def _mb_of(h):
                        v = getattr(h, 'margin_bottom_cm', 0.0)
                        return v if v > 0 else design.inner_margin_bottom_cm
                    def _ml_of(h):
                        v = getattr(h, 'margin_left_cm', 0.0)
                        return v if v > 0 else shared_ml

                    if layout == 'horizontal':
                        # ===== [PER-HOLE] y 轴：每洞独立 mt_i；x 轴连续（ml→w→gap→w→mr）=====
                        cursor_x = ox_cm + _ml_of(holes[0])
                        for i, h in enumerate(holes):
                            if i > 0 and i - 1 < len(gaps):
                                cursor_x += gaps[i - 1]
                            x_cm = cursor_x
                            y_cm = oy_cm + _mt_of(h)   # 每洞独立 y
                            w_cm = max(0.0, h.w_cm) + TRIM_CM  # 往外扩1cm
                            h_cm = max(0.0, h.h_cm) + TRIM_CM  # 往外扩1cm
                            # ===== [PER-HOLE Add-On] 同时存 per-hole mt/mb/ml/mr =====
                            design.pool_holes_cm.append({
                                'x_cm': x_cm, 'y_cm': y_cm,
                                'w_cm': w_cm, 'h_cm': h_cm,
                                'mt_cm': _mt_of(h),
                                'mb_cm': _mb_of(h),
                                'ml_cm': _ml_of(h),
                                'mr_cm': max(0.0, getattr(h, 'margin_right_cm', 0.0)),
                            })
                            cursor_x += w_cm
                    elif layout == 'vertical':
                        # ===== [PER-HOLE] x 轴：每洞独立 ml_i；y 轴连续（mt→h→gap→h→mb）=====
                        cursor_y = oy_cm + _mt_of(holes[0])
                        for i, h in enumerate(holes):
                            if i > 0 and i - 1 < len(gaps):
                                cursor_y += gaps[i - 1]
                            x_cm = ox_cm + _ml_of(h)   # 每洞独立 x
                            y_cm = cursor_y
                            w_cm = max(0.0, h.w_cm) + TRIM_CM  # 往外扩1cm
                            h_cm = max(0.0, h.h_cm) + TRIM_CM  # 往外扩1cm
                            design.pool_holes_cm.append({
                                'x_cm': x_cm, 'y_cm': y_cm,
                                'w_cm': w_cm, 'h_cm': h_cm,
                                'mt_cm': _mt_of(h),
                                'mb_cm': _mb_of(h),
                                'ml_cm': _ml_of(h),
                                'mr_cm': max(0.0, getattr(h, 'margin_right_cm', 0.0)),
                            })
                            cursor_y += h_cm
                    else:  # mixed：退化按横排
                        cursor_x = ox_cm + _ml_of(holes[0])
                        for i, h in enumerate(holes):
                            if i > 0 and i - 1 < len(gaps):
                                cursor_x += gaps[i - 1]
                            _w_exp = max(0.0, h.w_cm) + TRIM_CM  # 往外扩1cm
                            _h_exp = max(0.0, h.h_cm) + TRIM_CM  # 往外扩1cm
                            design.pool_holes_cm.append({
                                'x_cm': cursor_x,
                                'y_cm': oy_cm + _mt_of(h),  # 每洞独立 y
                                'w_cm': _w_exp,
                                'h_cm': _h_exp,
                                'mt_cm': _mt_of(h),
                                'mb_cm': _mb_of(h),
                                'ml_cm': _ml_of(h),
                                'mr_cm': max(0.0, getattr(h, 'margin_right_cm', 0.0)),
                            })
                            cursor_x += _w_exp

                    # 标记：image_ops Add-On 检查该标记和 holes>=2 才触发
                    design.pool_is_multi_hole = True
                    design.pool_holes_gaps_cm = gaps

                    self._log(
                        f"多洞模式写入: N={len(holes)} layout={layout} "
                        f"gaps={[round(g,1) for g in gaps]}"
                    )
                    self._log(
                        f"  [挖洞扩展] 每洞尺寸+{TRIM_CM:.1f}cm，间距-{TRIM_CM:.1f}cm补偿 "
                        f"（保证 ml+Σ(w+1)+Σ(gap-1)+mr = outer+1 = canvas）"
                    )
                    for i, hc in enumerate(design.pool_holes_cm):
                        self._log(
                            f"  Hole[{i}] 画布位置 x={hc['x_cm']:.1f} y={hc['y_cm']:.1f} "
                            f"size={hc['w_cm']:.1f}x{hc['h_cm']:.1f} cm"
                        )

                    # ===== [MULTI-HOLE UI OVERRIDE Add-On 2026-08-29] =====
                    # 用户在多洞参数面板上改动后：UI → _detect_multihole_edits() →
                    # self._user_multihole → 覆盖每洞 w/h/间距并重算 x/y，保证
                    # 后续每洞素材匹配 (_on_pool_finished_ok) 和预览都使用 UI 最新值。
                    # 单洞（_user_multihole 为 None 或 active_count<2）→ 直接跳过。
                    _ump = self._user_multihole
                    if isinstance(_ump, dict):
                        _n = int(_ump.get('active_count', 0) or 0)
                        _wh = _ump.get('holes_wh', []) or []
                        _gs = _ump.get('gaps_cm', []) or []
                        if _n >= 2 and len(_wh) >= _n and len(_gs) >= (_n - 1):
                            # layout 优先级：UI 传入 > sketch_result > design.pool_layout_type
                            _lo = (_ump.get('layout_type')
                                   or getattr(sketch_result, 'layout_type', None)
                                   or getattr(design, 'pool_layout_type', None)
                                   or 'horizontal')
                            design.pool_layout_type = _lo
                            # 截取严格 == _n 段数据（避免 UI 传长了误写）
                            _new_wh = [(max(0.0, float(w)), max(0.0, float(h)))
                                       for (w, h) in list(_wh)[:_n]]
                            _new_gaps = [max(0.0, float(g)) for g in list(_gs)[:(_n - 1)]]
                            # 保留原 per-hole 边距 (mt/mb/ml/mr) 作为 y/x 起点的真值
                            # —— 这些来自 sketch 方向锁定，用户没改就不变。
                            _old = list(design.pool_holes_cm or [])
                            def _mt_i(i):
                                if 0 <= i < len(_old):
                                    v = _old[i].get('mt_cm', 0.0)
                                    if v and v > 0:
                                        return float(v)
                                return design.inner_margin_top_cm
                            def _mb_i(i):
                                if 0 <= i < len(_old):
                                    v = _old[i].get('mb_cm', 0.0)
                                    if v and v > 0:
                                        return float(v)
                                return design.inner_margin_bottom_cm
                            def _ml_i(i, shared_ml):
                                if 0 <= i < len(_old):
                                    v = _old[i].get('ml_cm', 0.0)
                                    if v and v > 0:
                                        return float(v)
                                return shared_ml if i == 0 else 0.0
                            def _mr_i(i, shared_mr):
                                if 0 <= i < len(_old):
                                    v = _old[i].get('mr_cm', 0.0)
                                    if v and v > 0:
                                        return float(v)
                                return shared_mr if i == _n - 1 else 0.0
                            _ox = design.outer_margin_cm
                            _oy = design.outer_margin_cm
                            _s_ml = design.inner_margin_left_cm
                            _s_mt = design.inner_margin_top_cm
                            _s_mr = design.inner_margin_right_cm
                            _new_holes = []
                            if _lo == 'vertical':
                                cursor_y = _oy + _mt_i(0)
                                for i, (_w, _h) in enumerate(_new_wh):
                                    if i > 0:
                                        cursor_y += _new_gaps[i - 1]
                                    hmt = _mt_i(i)
                                    hmb = _mb_i(i)
                                    hml = _ml_i(i, _s_ml)
                                    hmr = _mr_i(i, _s_mr)
                                    _new_holes.append({
                                        'x_cm': _ox + hml,
                                        'y_cm': cursor_y,
                                        'w_cm': _w, 'h_cm': _h,
                                        'mt_cm': hmt, 'mb_cm': hmb,
                                        'ml_cm': hml, 'mr_cm': hmr,
                                    })
                                    cursor_y += _h
                            else:  # horizontal / mixed → 横排语义（占 90% 业务）
                                cursor_x = _ox + _ml_i(0, _s_ml)
                                for i, (_w, _h) in enumerate(_new_wh):
                                    if i > 0:
                                        cursor_x += _new_gaps[i - 1]
                                    hmt = _mt_i(i)
                                    hmb = _mb_i(i)
                                    hml = _ml_i(i, _s_ml)
                                    hmr = _mr_i(i, _s_mr)
                                    _new_holes.append({
                                        'x_cm': cursor_x,
                                        'y_cm': _oy + hmt,
                                        'w_cm': _w, 'h_cm': _h,
                                        'mt_cm': hmt, 'mb_cm': hmb,
                                        'ml_cm': hml, 'mr_cm': hmr,
                                    })
                                    cursor_x += _w
                            design.pool_holes_cm = _new_holes
                            design.pool_holes_gaps_cm = _new_gaps
                            design.pool_is_multi_hole = True
                            self._log(
                                f"[多洞UI覆盖] 应用用户手动修改的多洞参数: "
                                f"N={_n} layout={_lo} "
                                f"wh={[(round(w,1),round(h,1)) for w,h in _new_wh]} "
                                f"gaps={[round(g,1) for g in _new_gaps]}"
                            )
                            for i, hc in enumerate(_new_holes):
                                self._log(
                                    f"  Hole[{i}] UI覆盖后 x={hc['x_cm']:.1f} y={hc['y_cm']:.1f} "
                                    f"size={hc['w_cm']:.1f}x{hc['h_cm']:.1f} cm"
                                )
                    # ===== [END UI OVERRIDE Add-On] =====
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

