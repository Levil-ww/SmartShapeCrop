"""workers/property_panel_workers.py —— 水池设计后台 Worker 层。

包含 PoolRenderWorker / _SketchParseWorker / _InnerMatchWorker /
_SketchDecodeWorker / _WarmupScanWorker / _LShapeParseWorker。

从 gui/property_panel_workers.py 迁移至 workers/ 目录，逻辑与原文件完全一致。
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
from core.config import CUT_LOSS_CM
from services.parser.name_parser import parse_filename
from services.parser.template_matcher import TemplateMatcher
from core.app_settings import get_app_settings
from services.sketch_parser import validate_sketch_file
from services.sketch_parser.sketch_parser import _SKETCH_ACCEPT_EXT, get_tesseract_status

logger = logging.getLogger(__name__)


class _SketchDecodeWorker(QThread):
    """[Perf-Opt P1-07] 草图大图解码后台线程：Image.open + convert 移出 GUI 线程。

    大图 Image.open/convert('RGB') 可能耗时数百 ms ~ 秒级，原先在
    _pool_load_sketch_from_path 主线程同步执行会阻塞 Qt 重绘（上传/拖拽大图时界面卡死）。
    本 Worker 在后台完成解码，decoded 信号回传 PIL Image（None 表示失败），
    GUI 线程负责 emit sketch_loaded —— 显示语义与原实现完全一致，只是解码不再卡 UI。
    """
    decoded = pyqtSignal(object)   # PIL Image；解码失败为 None

    def __init__(self, sketch_path: str, parent=None):
        super().__init__(parent)
        self._path = sketch_path

    # [H-14] 中断检查语义澄清：isInterruptionRequested() 来自 QThread 基类，
    # 本 Worker 继承 QThread，self 同时是 Worker 与 Thread 对象。
    # 统一经 _cancel_requested() 访问，避免与类自身成员语义混淆。
    def _cancel_requested(self) -> bool:
        """线程取消请求检查（QThread.isInterruptionRequested 的显式别名）。"""
        return self.isInterruptionRequested()

    def run(self):
        try:
            if self._cancel_requested():
                return
            pil_img = Image.open(self._path)
            if pil_img.mode not in ('RGB', 'RGBA'):
                pil_img = pil_img.convert('RGB')
            # 强制完成整图解码（Image.open 是惰性的），确保耗时代码全部在后台线程
            pil_img.load()
            if self._cancel_requested():
                return
            self.decoded.emit(pil_img)
        except Exception as e:
            if self._cancel_requested():
                return
            logger.warning(f"草图加载为 PIL Image 失败: {e}")
            self.decoded.emit(None)


class _InnerMatchWorker(QThread):
    """[Perf-Opt P1-08] 内挖素材自动匹配后台线程：scan_library + find_best_match 移出 GUI 线程。

    原逻辑位于 _GenerateMixin._on_pool_finished_ok（GUI 线程直接执行）：
    模板目录 mtime 变化时 scan_library 会全量扫描（数百~上千文件），
    find_best_match 要对候选逐条做损失计算，整段可能耗时数秒~数十秒，
    期间 Qt 事件循环被阻塞 → 界面冻结（"模板扫描卡死"）。
    本 Worker 在后台完成扫描与匹配（含素材图预加载），
    结果（路径/评分/预加载图）通过 finished_ok 信号回传主线程，
    由 _on_inner_match_done 回填 design 并继续"预览 + 状态"收尾 ——
    匹配数据、回填字段与失败语义与原实现完全一致，只是不再卡 UI。
    """
    finished_ok = pyqtSignal(object)   # result dict
    finished_err = pyqtSignal(str)

    def __init__(self, matcher: TemplateMatcher, template_dir: str,
                 target_name: str,
                 is_multi_hole: bool, holes_wh: list,
                 single_wh: tuple | None,
                 parent=None):
        """参数均为纯数据（matcher 引用 + 文本/数值），不携带 GUI 对象。

        is_multi_hole: 多洞模式（洞数≥2）标志
        holes_wh:      多洞时每洞 (w_cm, h_cm) 列表
        single_wh:     单洞时 (inner_w_cm, inner_h_cm)
        """
        super().__init__(parent)
        self._matcher = matcher
        self._template_dir = template_dir
        self._target_name = target_name or ""
        self._is_multi = bool(is_multi_hole)
        self._holes_wh = list(holes_wh or [])
        self._single_wh = single_wh

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            import re as _re
            from services.parser.name_parser import parse_filename as _parse_fn
            from core.image_ops import load_image_rgb
            if not self._template_dir or not os.path.isdir(self._template_dir):
                self.finished_err.emit("模板库目录无效")
                return
            if self._matcher.get_template_dir() != os.path.abspath(self._template_dir):
                self._matcher.set_template_dir(os.path.abspath(self._template_dir))
            self._matcher.scan_library(force=False, check_cancel=self.isInterruptionRequested)
            if self.isInterruptionRequested():
                return

            target_name = self._target_name
            # ===== 查询名构造（与单洞/多洞原逻辑完全对齐） =====
            # 优先：正则替换 target 名中的尺寸部分 → 保留花型名（如"安妮森林"）
            # 兜底：parse_filename 取 pattern_name（去掉冒号后的尺寸）
            _tmpl = ""
            _dim_match = None
            if target_name:
                _dim_match = _re.search(
                    r'(\d+\.?\d*)\s*[xX×]\s*(\d+\.?\d*)\s*[Cc][Mm]',
                    target_name
                )
            if _dim_match:
                # ✅ 正确路径：正则替换 → 保留原始花型名（如 "安妮森林"）
                _tmpl = (target_name[:_dim_match.start()]
                         + '{W:.1f}x{H:.1f}CM'
                         + target_name[_dim_match.end():])
            elif target_name:
                # 兜底：无尺寸 → 尝试 parse_filename 提取 pattern_name
                _p = _parse_fn(target_name)
                _flower = _p.pattern_name or _p.pool_pattern_name or ""
                _flower = _flower.split(':')[0].strip() if _flower else ""
                if _flower:
                    _tmpl = f"{_flower}-裁剪有图-{{W:.1f}}x{{H:.1f}}CM"

            result: dict = {'multi': self._is_multi, 'inner_match_info': ''}
            if self._is_multi:
                # ===== 多洞：每洞独立匹配 + 素材图预加载 =====
                preload_cache: dict[str, object] = {}
                holes_out = []
                info_lines = []
                for _i, (_w, _h) in enumerate(self._holes_wh):
                    if self.isInterruptionRequested():
                        return
                    if _w <= 0 or _h <= 0:
                        holes_out.append({'path': None, 'score': 0.0})
                        continue
                    _q = _tmpl.format(W=_w, H=_h) if _tmpl else ""
                    if _q:
                        _best, _ = self._matcher.find_best_match(_q)
                        if _best is not None:
                            _path = _best.path
                            holes_out.append({'path': _path, 'score': float(_best.score)})
                            info_lines.append(f"洞{_i+1}素材："
                                             f"{os.path.basename(_path)}"
                                             f" (score={_best.score:.1f})\n")
                            if _path and os.path.isfile(_path) and _path not in preload_cache:
                                preload_cache[_path] = load_image_rgb(_path)
                        else:
                            holes_out.append({'path': None, 'score': 0.0})
                            info_lines.append(f"洞{_i+1}素材：未找到匹配\n")
                    else:
                        holes_out.append({'path': None, 'score': 0.0})
                        info_lines.append(f"洞{_i+1}素材：无花型名跳过\n")
                result['holes'] = holes_out
                result['preload'] = preload_cache
                result['inner_match_info'] = "".join(info_lines)
            else:
                # ===== 单洞：原有内挖素材匹配（逻辑一字未改） =====
                best_inner = None
                inner_w_cm, inner_h_cm = (self._single_wh or (0.0, 0.0))
                inner_query = ""
                if target_name and inner_w_cm > 0 and inner_h_cm > 0:
                    # 用正则替换原目标文件名中的尺寸部分为内挖尺寸
                    dim_match = _re.search(
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
                        p = _parse_fn(target_name)
                        pat = p.pool_pattern_name or p.pattern_name or ""
                        inner_query = (f"{pat}-裁剪有图-{inner_w_cm:.1f}x{inner_h_cm:.1f}CM"
                                       if pat else "")
                if inner_query:
                    best_inner, _ = self._matcher.find_best_match(inner_query)
                    if best_inner is not None:
                        result['inner_match_info'] = (f"内挖素材：{os.path.basename(best_inner.path)} "
                                                      f"(score={best_inner.score:.1f})\n")
                    else:
                        result['inner_match_info'] = "内挖素材：未找到匹配（请手动选择）\n"
                else:
                    result['inner_match_info'] = "内挖素材：无花型名，跳过自动匹配\n"
                result['single'] = {
                    'path': best_inner.path if best_inner is not None else None,
                    'score': float(best_inner.score) if best_inner is not None else 0.0,
                    'w_cm': float(getattr(best_inner, 'w_cm', 0.0) or 0.0) if best_inner else 0.0,
                    'h_cm': float(getattr(best_inner, 'h_cm', 0.0) or 0.0) if best_inner else 0.0,
                }
            if self.isInterruptionRequested():
                return
            self.finished_ok.emit(result)
        except Exception as e:
            if self.isInterruptionRequested():
                return
            logger.warning(f"[InnerMatchWorker] failed: {e}")
            self.finished_err.emit(str(e))


class _WarmupScanWorker(QThread):
    """[Perf-Opt] 后台预热扫描：用户选择/恢复模板库目录后立即在后台执行 scan_library，
    把磁盘缓存加载到内存、做增量扫描。这样用户填写文件名/上传草图的时间与扫描
    并行执行，点"生成预览"时命中缓存即可秒出。

    与 PoolRenderWorker 互斥：启动渲染前若发现预热仍在跑，等待结束即可复用结果。
    TemplateMatcher.scan_library 内部有缓存命中检查（_try_quick_skip + 内存缓存），
    重复调用幂等且接近零开销。
    """
    finished_ok = pyqtSignal(int, float)   # (entry_count, elapsed_sec)
    finished_err = pyqtSignal(str)

    def __init__(self, matcher: TemplateMatcher, template_dir: str, parent=None):
        super().__init__(parent)
        self._matcher = matcher
        self._template_dir = template_dir

    def run(self):
        try:
            import time
            t0 = time.time()
            abs_dir = os.path.abspath(self._template_dir)
            if self._matcher.get_template_dir() != abs_dir:
                self._matcher.set_template_dir(abs_dir)
            self._matcher.scan_library(force=False, check_cancel=self.isInterruptionRequested)
            if self.isInterruptionRequested():
                logger.info("[WarmupScan] 扫描被取消，丢弃结果")
                return
            dt = time.time() - t0
            self.finished_ok.emit(len(self._matcher._cache), dt)
        except Exception as e:
            logger.exception(f"[WarmupScan] failed: {e}")
            self.finished_err.emit(str(e))


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
            step1 = self._step_parse_filename()
            if step1 is None:
                return
            parsed, file_w, file_h = step1

            # 2) 匹配模板
            best = self._step_match_template(parsed)
            if best is None:
                return

            # 3) 解析草图（如果提供了）：L 形模式参数已由 UI 层传入，此处跳过
            is_lshape, sketch_result, canvas_w_cm, canvas_h_cm = \
                self._step_resolve_sketch(file_w, file_h)

            # [Fix 2026-08-28 / 09-02] 用户手动修改的边距：仅记录日志，
            # 实际覆盖在 _build_design 内应用（不修改 sketch_result 对象本身）。
            self._step_log_user_margins(sketch_result, is_lshape)

            # 4) 构建 CropDesign
            design = self._build_design(best, sketch_result, canvas_w_cm,
                                        canvas_h_cm, is_lshape)

            # [Fix 2026-08-26] 素材设计方向尺寸（文件名原始方向，供渲染旋转判断）
            self._step_write_material_design_size(best, design)

            # 预加载模板图到内存缓存（渲染时直接使用，避免主线程阻塞）
            self._step_preload_material(best, design)

            # 多层边框：水池模式下保留默认边框层（黑-白-黑），用户可在【多层边框】区修改或删除
            # 若素材图本身已有边框，用户可手动清空 borders 列表

            self.progress.emit(100, "完成！")
            self.finished_ok.emit(design, sketch_result, "\n".join(self._log_lines))

        except Exception as e:
            logger.exception("PoolRenderWorker 异常终止")
            self.finished_err.emit(f"处理失败：{e}")

    def _step_parse_filename(self):
        """步骤1：解析目标文件名并校验尺寸。失败时 emit finished_err 并返回 None。"""
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
            return None
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
        return parsed, file_w, file_h
    def _step_match_template(self, parsed):
        """步骤2：扫描模板库并匹配最佳素材。失败时 emit finished_err 并返回 None。"""
        # 2) 匹配模板
        self.progress.emit(25, "扫描模板库并匹配最佳素材…")
        if not self._template_dir or not os.path.isdir(self._template_dir):
            self.finished_err.emit("请先选择模板库目录")
            return None
        if self._matcher.get_template_dir() != os.path.abspath(self._template_dir):
            self._matcher.set_template_dir(os.path.abspath(self._template_dir))
        self._matcher.scan_library(force=False)
        best, candidates = self._matcher.find_best_match(self._target)
        if best is None:
            self.finished_err.emit(
                f"在模板库中未找到匹配的花型（目标花型={parsed.pool_pattern_name or parsed.pattern_name}）。\n"
                "请确认模板库目录是否正确，或模板文件名包含该花型名。"
            )
            return None
        self._log(
            f"最佳匹配：{os.path.basename(best.path)}  "
            f"(score={best.score:.1f}, 比例差={best.ratio_diff:.3f})"
        )
        return best
    def _step_resolve_sketch(self, file_w, file_h):
        """步骤3：解析草图（L 形模式跳过），返回 (is_lshape, sketch_result, canvas_w_cm, canvas_h_cm)。"""
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
                    from services.sketch_parser import parse_sketch
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
        return is_lshape, sketch_result, canvas_w_cm, canvas_h_cm
    def _step_log_user_margins(self, sketch_result, is_lshape):
        """步骤3.5：用户手动修改的边距仅记录日志（覆盖在 _build_design 中应用）。"""
        # [Fix 2026-08-28] 用户手动修改的边距优先于草图识别结果
        # 当用户在 UI 上手动调整了边距值（SpinBox），这些值通过 user_margins
        # 传入 Worker，用于覆盖 design 中的对应边距字段。
        #
        # [Fix 2026-09-02] 不再修改 sketch_result 对象（引用传递会污染
        #   PropertyPanel 的 self._sketch_parse_result，导致后续
        #   _detect_user_margin_edits 对比基准被篡改，检测不到用户修改）。
        #   改为在下面构建设计时直接从 user_margins 取值。
        #
        # L 形模式：margins 恒为 0（L 形 = 画布挖角），跳过该覆盖逻辑。
        if self._user_margins and sketch_result and sketch_result.success and not is_lshape:
            um = self._user_margins
            changed = []
            if 'top' in um and um['top'] is not None:
                changed.append(f"上:{um['top']:.1f}")
            if 'bottom' in um and um['bottom'] is not None:
                changed.append(f"下:{um['bottom']:.1f}")
            if 'left' in um and um['left'] is not None:
                changed.append(f"左:{um['left']:.1f}")
            if 'right' in um and um['right'] is not None:
                changed.append(f"右:{um['right']:.1f}")
            if changed:
                self._log(f"应用用户手动修改的边距：{', '.join(changed)}")
    def _build_design(self, best, sketch_result, canvas_w_cm, canvas_h_cm, is_lshape):
        """步骤4：构建 CropDesign（L 形挖角 / 矩形+多洞 两条路径）。"""
        # 4) 构建 CropDesign
        TRIM_CM = CUT_LOSS_CM
        TRIM_CM = 1.0
        self.progress.emit(85, "构建设计参数…")
        design = CropDesign()
        design.canvas_w_cm = canvas_w_cm + TRIM_CM
        design.canvas_h_cm = canvas_h_cm + TRIM_CM
        design.dpi = 150
        design.outer_margin_cm = 0.0   # 水池默认不额外留白（花纹图本身就是外框）

        if is_lshape:
            self._apply_lshape_params(design, best, canvas_w_cm, canvas_h_cm, TRIM_CM)
        else:
            # 矩形/水池模式：边距 → 多洞 → 外框素材
            self._apply_rect_hole_params(design, best, sketch_result,
                                       canvas_w_cm, canvas_h_cm, is_lshape, TRIM_CM)
        return design
    def _apply_lshape_params(self, design, best, canvas_w_cm, canvas_h_cm, TRIM_CM):
        """L 形挖角（裁剪有图）模式：保留外框素材、切角显示洞色；TRIM 不作用于挖角。"""
        # —— L 形挖角（裁剪有图）模式 ——
        # 语义：L 形区域保留外框素材花纹，被切掉的角显示洞色。
        # 配置：outer_margin=0, inner_margin 全 0（cut 定位直接基于 canvas 边缘）。
        # 关键不变量：挖角尺寸 = 用户输入值（草图/手动），完全不受 TRIM 影响。
        # TRIM 只是画布比外框多出的余量，cut 自然延伸到 canvas 边缘。
        design.outer_margin_cm = 0.0
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
        # 挖角值直接用草图识别的成品真值，不做额外损耗补偿
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
        # [V13 集成 2026-09-04] 手动边框覆盖参数透传到 design
        # 缺失字段（None）→ design 字段保留默认 None → 走原有自动检测路径
        # 已设值（int / tuple）→ design 字段写入 → 走 V13 路径
        _me = lp.get('manual_edge_px', None)
        _mb = lp.get('manual_band_px', None)
        _mc = lp.get('manual_band_color', None)
        design.lshape_manual_edge_px = (
            int(_me) if _me is not None else None)
        design.lshape_manual_band_px = (
            int(_mb) if _mb is not None else None)
        if _mc is not None:
            design.lshape_manual_band_color = tuple(int(c) for c in _mc)
        else:
            design.lshape_manual_band_color = None
        self._log(
            f"L 形挖角模式：corner={design.l_corner}, "
            f"挖角 {design.l_cut_w_cm:.1f}x{design.l_cut_h_cm:.1f} cm, "
            f"外框 {canvas_w_cm:.1f}x{canvas_h_cm:.1f} cm（画布含1cm损耗）"
        )
    def _apply_rect_hole_params(self, design, best, sketch_result, canvas_w_cm, canvas_h_cm, is_lshape, TRIM_CM):
        """矩形/水池模式：边距（草图 + 用户覆盖）→ 多洞 Add-On → 外框素材。"""
        design.mode = 'rect_hole'
        # 边距优先用草图，否则用默认等比例值（10% 短边）
        # [契约变更 2026-08-27] 画布已 +TRIM_CM(1cm) 作为裁剪损耗，
        # 草图识别到的 4 个边距视为设计真值，不再追加 +TRIM_CM 偏移。
        # 内挖由 inner = canvas - sum(margins) 自动推导，因此 inner 相对
        # sketch 原始内框自动 +1cm（损耗分摊到内挖区域，不挤占边距）。
        # 新不变量：(outer+1) = ml + inner_w + mr；(outer+1)_v = mt + inner_h + mb
        if sketch_result and sketch_result.success:
            # 先取草图值，再用 user_margins 覆盖（如果提供了）
            # [Fix 2026-09-02] 不再在上面修改 sketch_result 对象本身，
            #   保持 PropertyPanel._sketch_parse_result 的原始值不被污染。
            _mt = sketch_result.margin_top_cm
            _mb = sketch_result.margin_bottom_cm
            _ml = sketch_result.margin_left_cm
            _mr = sketch_result.margin_right_cm
            if self._user_margins and not is_lshape:
                um = self._user_margins
                if 'top' in um and um['top'] is not None:
                    _mt = float(um['top'])
                if 'bottom' in um and um['bottom'] is not None:
                    _mb = float(um['bottom'])
                if 'left' in um and um['left'] is not None:
                    _ml = float(um['left'])
                if 'right' in um and um['right'] is not None:
                    _mr = float(um['right'])
            design.inner_margin_top_cm = _mt
            design.inner_margin_bottom_cm = _mb
            design.inner_margin_left_cm = _ml
            design.inner_margin_right_cm = _mr
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

        # ===== [MULTI-HOLE Add-On] 由 _apply_multihole_addon 统一处理 =====
        self._apply_multihole_addon(design, sketch_result, TRIM_CM)
        # 水池模式开关 + 外框素材图
        design.pool_hole_transparent = True
        design.pool_outer_material_image = best.path
        # 同时也写到外框素材字段（方便用户在"背景设置"里看到并编辑）
        design.outer_bg_image = best.path
    def _apply_multihole_addon(self, design, sketch_result, TRIM_CM):
        """[MULTI-HOLE Add-On] 仅当 is_multi_hole 且 holes>=2 时生效；单洞零影响。"""
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
    def _step_write_material_design_size(self, best, design):
        """素材设计方向尺寸（文件名原始方向），供渲染判断旋转/缩放。"""
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
    def _step_preload_material(self, best, design):
        """预加载模板图到内存缓存。"""
        # 预加载模板图到内存缓存（渲染时直接使用，避免主线程网络读取阻塞）
        self.progress.emit(92, "预加载素材图…")
        try:
            from core.image_ops import load_image_rgb
            cached_img = load_image_rgb(best.path)
            design._cached_outer_image = cached_img
            design._cached_outer_src = best.path
            self._log(f"素材预加载完成: {cached_img.size[0]}x{cached_img.size[1]}px")
        except Exception as e:
            self._log(f"素材预加载失败（渲染时重试）: {e}")




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
            from services.sketch_parser import parse_sketch
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
            from services.sketch_parser import parse_lshape_sketch
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

