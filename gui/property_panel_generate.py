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
    def _pool_run_generate(self, source: str = 'pool', target_name_override: str | None = None):
        """水池模式一键生成预览。

        参数:
            source: 触发来源面板，'pool'=水池设计器，'lshape'=L形挖角设计面板。
                决定「历史记录写入哪个 source 键」（Safety 2 不变式）。
            target_name_override: 当 source != 'pool' 时，传入来源面板自己的
                目标文件名文本。None 表示回退到 Pool 面板 _pool_target。
                这保证了 LShape 面板即使持有与 Pool 面板不同的 target 文本，
                生成逻辑仍用来源面板的有效值（Safety 1 不变式）。
        """
        if self._pool_worker is not None and self._pool_worker.isRunning():
            QMessageBox.information(self, "提示", "正在处理中，请稍候…")
            return

        # ===== [2026-09-03 状态隔离] 统一 target 名：优先 override，回退 pool 面板 =====
        # 本函数后续所有 self._pool_target.text().strip() 读取都替换为该变量。
        if target_name_override is not None:
            effective_target_name = target_name_override.strip()
        else:
            effective_target_name = self._pool_target.text().strip()

        # [Perf-Opt] 如果后台预热扫描仍在进行，等待其完成后再启动渲染 Worker。
        # 避免两个子线程竞争写 TemplateMatcher（_cache/索引/子目录 mtime）。
        # 预热完成后内存缓存已就绪，PoolRenderWorker 内的 scan_library 会
        # 走 quick-skip 路径（毫秒级），总等待时间 = 预热剩余时间而不是从头扫描。
        warmup = getattr(self, '_warmup_worker', None)
        if warmup is not None and warmup.isRunning():
            self._set_pool_status("⏳ 模板库预热扫描即将完成…请稍候")
            QApplication.processEvents()  # 刷新状态栏文字
            # 使用 wait(30 分钟超时) 等待预热自然结束；极端超时也允许继续（scan_library 会做正确的事）
            warmup.wait(30 * 60 * 1000)

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

        target_name = effective_target_name
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

        # ===== [MULTI-HOLE Add-On 2026-08-29] UI 多洞改动 → 传入 Worker =====
        # 类比单洞 user_margins：只要多洞 GroupBox 处于激活（active_count>=2），
        # 就把当前 SpinBox 的真值传给 Worker。Worker 内"多洞UI覆盖 Add-On"分支
        # 再覆盖 sketch 解析的每洞 w/h/间距 + 重算 x/y，保证：
        #   1) design.pool_holes_cm[i].w/h = 用户修改值
        #   2) _on_pool_finished_ok 多洞独立素材匹配，自然按覆盖后的 w/h 做匹配
        #   3) 预览/渲染（_apply_quiet → _collect → image_ops）使用同一套洞几何。
        # 单洞场景（GroupBox 隐藏，active_count==0）→ 返回 None → Worker 零影响。
        user_multihole_params = self._detect_multihole_edits()

        # ===== [2026-09-03 状态隔离 Safety 2] 记录来源面板，供回调 _on_pool_finished_ok
        # 判断历史记录写入哪个 TARGET_SRC_* 键。必须在 worker.start() 前写，
        # 因为 Worker 在子线程发射 finished_ok 信号（排队到主线程），写/读都在主线程无竞争。
        self._last_generate_source = source
        # 启动 Worker
        self._pool_btn_generate.setEnabled(False)
        self._pool_btn_generate.setText("处理中…请稍候")
        # ===== [L-Shape Panel Refactor] 同步一键生成按钮状态到 LShapePanel =====
        if self._lshape_panel is not None:
            self._lshape_panel.set_generate_enabled(False, "处理中…请稍候")
        worker = PoolRenderWorker(
            self._matcher, tpl_dir, target_name, self._sketch_path,
            pre_parsed_result=self._sketch_parse_result,
            user_margins=user_margins,
            user_multihole_params=user_multihole_params,
            # ===== [2026-09-03 双向隔离 Bug B 修复] lshape_params 只在 source='lshape' 时传给 Worker =====
            # 之前无条件读 LShapePanel._lshape_params：用户先在 L 面板识别过一次 L 形 →
            # LShapePanel 持有有效参数 → 切到水池面板点生成 → Worker 误进 L 形模式 →
            # 日志写 "L 形挖角模式" → 水池面板状态栏混入 L 形数据。
            # 正确：source='pool' 强制 None（矩形/多洞模式），source='lshape' 才读 LShapePanel。
            lshape_params=(self._get_lshape_params() if source == 'lshape' else None),
            parent=self)
        worker.progress.connect(self._on_pool_progress)
        worker.finished_ok.connect(self._on_pool_finished_ok)
        worker.finished_err.connect(self._on_pool_finished_err)
        worker.finished.connect(lambda: (
            self._pool_btn_generate.setEnabled(True),
            self._pool_btn_generate.setText("🔍 匹配模板 → 解析草图 → 生成预览"),
            # ===== [L-Shape Panel Refactor] 恢复 LShapePanel 一键生成按钮 =====
            self._lshape_panel.set_generate_enabled(True, "🔍 生成预览") if self._lshape_panel is not None else None,
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
            # ===== [2026-09-03 修复 NameError] 重建来源面板的 target 名
            # 内挖素材匹配代码（下方 L268 / L341）需要 effective_target_name，
            # 但那是 _pool_run_generate 栈帧里的局部变量，Worker.finished_ok 不会带回来。
            # 复用 _last_generate_source + UI 重建：与启动时的 effective_target_name 语义一致。
            _src = getattr(self, '_last_generate_source', 'pool')
            if _src == 'lshape' and self._lshape_panel is not None:
                _resolved_target_name = self._lshape_panel.get_target_text().strip()
            else:
                _resolved_target_name = self._pool_target.text().strip()
            # 1) 把 Worker 构建的设计写回 self.design，并同步到所有 SpinBox / 控件
            self.design = design
            # ===== [SINGLE-HOLE Add-On 2026-08-31] 记录草图解析成功时的原始边距 =====
            # render_design 中的 Stale Decor Invalidation Add-On（core/image_ops.py）
            # 需要比对"当前边距 vs 草图解析原始边距"，当差异 > 0.1cm 时清理
            #   outer 成品图内嵌的旧装饰黑线（否则"旧边框 + 新边框"两条黑线并存）。
            # 本记录在 sketch_result.success=True 时写入新字段；失败路径默认字段为 None
            #   → invalidation 不激活；用户后续点击"生成预览"前可以继续调整。
            try:
                _sr_ok = bool(sketch_result is not None
                              and getattr(sketch_result, 'success', False))
                if _sr_ok:
                    self.design._pool_sketch_original_margins_cm = (
                        float(getattr(self.design, 'inner_margin_top_cm', 0.0) or 0.0),
                        float(getattr(self.design, 'inner_margin_bottom_cm', 0.0) or 0.0),
                        float(getattr(self.design, 'inner_margin_left_cm', 0.0) or 0.0),
                        float(getattr(self.design, 'inner_margin_right_cm', 0.0) or 0.0),
                    )
            except Exception:
                pass
            # ===== [END SINGLE-HOLE Add-On 记录草图解析原始边距] =====
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
            # ===== [L-Shape Panel Refactor 2026-09-02] L 形参数同步到 LShapePanel =====
            # 原 self._cb_lcorner / _sp_lw / _sp_lh 已迁移到 LShapePanel；
            # 通过 self._lshape_panel.set_lshape_params() 回填，语义与原直设控件一致。
            if design.mode == 'rect_lshape' and self._lshape_panel is not None:
                self._lshape_panel.set_lshape_params(
                    design.l_corner, design.l_cut_w_cm, design.l_cut_h_cm)
                # 外框 SpinBox 也需要回填（画布 = 外框 + 1cm 损耗 → 外框 = 画布 - 1cm）
                self._lshape_panel.set_outer_dims(
                    max(0.0, design.canvas_w_cm - 1.0),
                    max(0.0, design.canvas_h_cm - 1.0))
            self._sp_outer_margin.setValue(max(0, design.outer_margin_cm))
            # [Fix 2026-09-02] blockSignals 保护：避免 Worker 回填 4 次 setValue 触发
            #   4 次冗余的 valueChanged → _apply_quiet 渲染。Worker 完成后最终会调
            #   一次 _apply_quiet（L354），所以这里的 setValue 不需要触发预览。
            self._sp_mt.blockSignals(True)
            self._sp_mb.blockSignals(True)
            self._sp_ml.blockSignals(True)
            self._sp_mr.blockSignals(True)
            self._sp_mt.setValue(max(0, design.inner_margin_top_cm))
            self._sp_mb.setValue(max(0, design.inner_margin_bottom_cm))
            self._sp_ml.setValue(max(0, design.inner_margin_left_cm))
            self._sp_mr.setValue(max(0, design.inner_margin_right_cm))
            self._sp_mt.blockSignals(False)
            self._sp_mb.blockSignals(False)
            self._sp_ml.blockSignals(False)
            self._sp_mr.blockSignals(False)

            # ===== [MULTI-HOLE Add-On 2026-08-29] 回填多洞 UI =====
            # pool_is_multi_hole=True 且 holes>=2 → 显示/填 多洞 GroupBox；否则隐藏。
            # 单洞流程 pool_is_multi_hole=False（默认）→ 只 _hide_multi_hole_ui，不改变旧 UI。
            try:
                is_mh = (getattr(design, 'pool_is_multi_hole', False)
                         and isinstance(getattr(design, 'pool_holes_cm', []), list)
                         and len(design.pool_holes_cm) >= 2)
                if is_mh:
                    layout_m = getattr(design, 'pool_layout_type', None)
                    if layout_m is None and sketch_result is not None:
                        layout_m = getattr(sketch_result, 'layout_type', 'horizontal')
                    layout_m = layout_m or 'horizontal'
                    self._fill_multi_hole_ui(
                        design.pool_holes_cm,
                        list(getattr(design, 'pool_holes_gaps_cm', []) or []),
                        layout_m,
                    )
                else:
                    self._hide_multi_hole_ui()
            except Exception as e:
                import logging as _lgg
                _lgg.getLogger(__name__).warning(f"[Multi-hole UI] Worker 回填多洞 UI 失败: {e}")
            # 外框素材路径写到"背景设置"编辑框
            if design.outer_bg_image:
                self._ed_outer_img.setText(design.outer_bg_image)

            # 3) 内挖素材自动匹配（仅当挖空方式为"素材填充"且非 L 形模式时）
            #    L 形模式：L 形区域保留外框素材，挖掉的角显示洞色，不涉及内挖素材。
            inner_match_info = ""
            if hm == "image" and self.design.mode != 'rect_lshape':
                # ===== [MULTI-HOLE Add-On 2026-08-29] 多洞：每洞独立匹配内挖素材 =====
                # 触发条件：pool_is_multi_hole=True 且 pool_holes_cm>=2
                # 为每洞用独立的 w_cm/h_cm + pool_pattern_name 构造查询文件名，
                # 调用 matcher.find_best_match()，结果存入 pool_holes_cm[i]['inner_material_path']。
                # 单洞场景 pool_is_multi_hole=False → 跳过，走下面的原有单洞逻辑一字不变。
                _mh = (getattr(self.design, 'pool_is_multi_hole', False)
                       and isinstance(getattr(self.design, 'pool_holes_cm', []), list)
                       and len(self.design.pool_holes_cm) >= 2)
                if _mh:
                    try:
                        self._matcher.scan_library(force=False)
                        import re as _re
                        # [2026-09-03 修复 NameError] effective_target_name 是 _pool_run_generate
                        # 的局部变量，Worker.finished_ok 不带它，改用回调顶部重建的 _resolved_target_name
                        _target_name = _resolved_target_name
                        # ===== 查询名构造：与单洞逻辑完全对齐（关键修复） =====
                        # 优先：正则替换 target 名中的尺寸部分 → 保留花型名（如"安妮森林"）
                        # 兜底：parse_filename 取 pattern_name（去掉冒号后的尺寸）
                        _tmpl = ""  # 最终查询模板（带原始花型名、占位尺寸）
                        _dim_match = None
                        if _target_name:
                            _dim_match = _re.search(
                                r'(\d+\.?\d*)\s*[xX×]\s*(\d+\.?\d*)\s*[Cc][Mm]',
                                _target_name
                            )
                        if _dim_match:
                            # ✅ 正确路径：正则替换 → 保留原始花型名（如 "安妮森林"）
                            # 例："双面草-定制-裁剪有图-安妮森林:50x247cm裁剪有图"
                            #           → template = "双面草-定制-裁剪有图-安妮森林:{W}x{H}CM裁剪有图"
                            _tmpl = (_target_name[:_dim_match.start()]
                                     + '{W:.1f}x{H:.1f}CM'
                                     + _target_name[_dim_match.end():])
                        elif _target_name:
                            # 兜底：无尺寸 → 尝试 parse_filename 提取 pattern_name（比 pool_pattern_name 更可靠）
                            from core.parser.name_parser import parse_filename as _parse_fn
                            _p = _parse_fn(_target_name)
                            # pattern_name 是 "安妮森林:50x247cm" → 去掉冒号后的尺寸
                            _flower = _p.pattern_name or _p.pool_pattern_name or ""
                            _flower = _flower.split(':')[0].strip() if _flower else ""
                            if _flower:
                                _tmpl = f"{_flower}-裁剪有图-{{W:.1f}}x{{H:.1f}}CM"
                        # ===== 为每洞填充独立尺寸 =====
                        _preload_cache = {}  # path -> PIL.Image 避免重复磁盘 IO
                        for _i, _hc in enumerate(self.design.pool_holes_cm):
                            _w = float(_hc.get('w_cm', 0))
                            _h = float(_hc.get('h_cm', 0))
                            if _w <= 0 or _h <= 0:
                                _hc['inner_material_path'] = None
                                continue
                            if _tmpl:
                                _q = _tmpl.format(W=_w, H=_h)
                            else:
                                _q = ""
                            if _q:
                                self._set_pool_status(f"正在匹配洞{_i+1}素材…", is_error=False)
                                QApplication.processEvents()
                                _best, _ = self._matcher.find_best_match(_q)
                                if _best is not None:
                                    _hc['inner_material_path'] = _best.path
                                    inner_match_info += (f"洞{_i+1}素材："
                                                         f"{os.path.basename(_best.path)}"
                                                         f" (score={_best.score:.1f})\n")
                                else:
                                    _hc['inner_material_path'] = None
                                    inner_match_info += f"洞{_i+1}素材：未找到匹配\n"
                            else:
                                _hc['inner_material_path'] = None
                                inner_match_info += f"洞{_i+1}素材：无花型名跳过\n"
                        # 预加载所有匹配到的素材图
                        try:
                            from core.image_ops import load_image_rgb
                            for _hc in self.design.pool_holes_cm:
                                _p = _hc.get('inner_material_path')
                                if _p and os.path.isfile(_p):
                                    if _p not in _preload_cache:
                                        _preload_cache[_p] = load_image_rgb(_p)
                                    _hc['_cached_inner_image'] = _preload_cache[_p]
                        except Exception as _pre_e:
                            logger.warning(f"[Multi-hole inner material] 预加载素材失败: {_pre_e}")
                    except Exception as _mh_e:
                        logger.warning(f"[Multi-hole inner material] 多洞独立匹配失败: {_mh_e}")
                        inner_match_info = f"多洞素材匹配异常：{_mh_e}\n"
                else:
                    # ===== 单洞原有内挖素材匹配（一字未改） =====
                    try:
                        inner_w_cm = self.design.canvas_w_cm - self.design.inner_margin_left_cm - self.design.inner_margin_right_cm
                        inner_h_cm = self.design.canvas_h_cm - self.design.inner_margin_top_cm - self.design.inner_margin_bottom_cm
                        # [2026-09-03 修复 NameError] effective_target_name 跨栈帧不可见，
                        # 改用回调顶部用 _last_generate_source 重建的 _resolved_target_name
                        target_name = _resolved_target_name
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
                                    # ===== [SINGLE-HOLE Add-On 2026-08-31] 记录内挖素材设计尺寸 =====
                                    # 与多洞 pool_holes_cm[i] 存 _src_design_w_cm/_src_design_h_cm 语义对齐；
                                    # 供 _render_inner_area 单洞 Add-On 调用 adapt_pool_material 时
                                    #   做"文件名设计方向 ≠ 像素存储方向"的硬校正（cond_C）。
                                    # 纯写入新字段，不修改已有字段，零行为影响。
                                    try:
                                        self.design.pool_inner_src_design_w_cm = float(
                                            getattr(best_inner, 'w_cm', 0.0) or 0.0)
                                        self.design.pool_inner_src_design_h_cm = float(
                                            getattr(best_inner, 'h_cm', 0.0) or 0.0)
                                    except Exception:
                                        self.design.pool_inner_src_design_w_cm = 0.0
                                        self.design.pool_inner_src_design_h_cm = 0.0
                                    # ===== [END SINGLE-HOLE Add-On 记录内挖素材设计尺寸] =====
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

            # ===== [2026-09-03 状态隔离] 历史记录仅写入来源面板（Safety 2 不变式）=====
            # source='pool'  → 仅调 _pool_record_target_name_history（读 _pool_target，写 TARGET_SRC_POOL）
            # source='lshape' → 仅调 LShapePanel._record_target_name_history（读自己的 LineEdit，写 TARGET_SRC_LSHAPE）
            # 两边都从各自的 LineEdit 读值，所以即使两个面板 target 文本不同也互不串台。
            # 【关键】从 _last_generate_source 读取，而不是参数：Worker.finished_ok 只发 3 个参数，
            # 没有把 source 带过来；默认值 'pool' 保证旧代码路径（无 source 标记的调用）不会崩。
            _src = getattr(self, '_last_generate_source', 'pool')
            if _src == 'pool':
                self._pool_record_target_name_history()
            elif _src == 'lshape' and self._lshape_panel is not None:
                self._lshape_panel._record_target_name_history()

            # 保留信号（不再用于历史记录；未来可挂接状态灯/埋点等副作用）
            self.pool_generate_succeeded.emit()

            # 5) 结果提示（try/except 防止状态消息失败导致整个流程中断）
            try:
                info = f"✅ 生成成功！\n"
                info += f"画布：{self.design.canvas_w_cm:.1f} × {self.design.canvas_h_cm:.1f} cm\n"
                if self.design.mode == 'rect_lshape':
                    info += (f"L形挖角：corner={self.design.l_corner}，"
                             f"挖角 {self.design.l_cut_w_cm:.1f} × {self.design.l_cut_h_cm:.1f} cm\n")
                    info += (f"外框尺寸：{max(0, self.design.canvas_w_cm - 1.0):.1f} × "
                             f"{max(0, self.design.canvas_h_cm - 1.0):.1f} cm"
                             f"（画布含 1cm 裁剪损耗）\n")
                else:
                    # ===== [MULTI-HOLE Add-On 2026-08-29] 多洞显示替换单洞内挖行 =====
                    # 仅当 pool_is_multi_hole=True 且 pool_holes_cm>=2 时显示每洞 + 间距；
                    # 否则走原单洞代码一字不变。
                    is_mh_design = (getattr(self.design, 'pool_is_multi_hole', False)
                                    and isinstance(getattr(self.design, 'pool_holes_cm', []), list)
                                    and len(self.design.pool_holes_cm) >= 2)
                    if is_mh_design:
                        holes = self.design.pool_holes_cm
                        gaps = list(getattr(self.design, 'pool_holes_gaps_cm', []) or [])
                        # ===== [MULTI-HOLE Add-On 2026-08-29] 防御性过滤：仅 w>0 or h>0 才打印 =====
                        # 极端情况下 design 残留 0 值洞（历史 state / 旧缓存）也不会显示。
                        valid_holes = [hc for hc in holes if (float(hc.get('w_cm',0))>0) or (float(hc.get('h_cm',0))>0)]
                        valid_gaps = gaps[:max(0, len(valid_holes)-1)]
                        for i, hc in enumerate(valid_holes):
                            info += f"洞{i+1}：{hc['w_cm']:.1f} × {hc['h_cm']:.1f} cm\n"
                        gaps_txt = "，".join(f"间{i+1}_{i+2}={g:.1f}" for i, g in enumerate(valid_gaps)) if valid_gaps else "无"
                        # ===== [PER-HOLE Add-On] 多洞每洞独立 mt/mb/ml/mr =====
                        # 从 pool_holes_cm dict（PoolWorker 已填充 mt_cm/mb_cm/ml_cm/mr_cm）读 per-hole 边距
                        for i, hc in enumerate(valid_holes):
                            hmt = hc.get('mt_cm', self.design.inner_margin_top_cm)
                            hmb = hc.get('mb_cm', self.design.inner_margin_bottom_cm)
                            hml = hc.get('ml_cm', self.design.inner_margin_left_cm)
                            hmr = hc.get('mr_cm', self.design.inner_margin_right_cm)
                            info += (f"  洞{i+1}边距：上{hmt:.1f}/下{hmb:.1f}/"
                                     f"左{hml:.1f}/右{hmr:.1f} cm\n")
                        info += f"  中距：{gaps_txt}\n"
                    else:
                        # ===== 单洞原有代码（一字未改） =====
                        inner_w_cm = self.design.canvas_w_cm - self.design.inner_margin_left_cm - self.design.inner_margin_right_cm
                        inner_h_cm = self.design.canvas_h_cm - self.design.inner_margin_top_cm - self.design.inner_margin_bottom_cm
                        info += f"内挖：{inner_w_cm:.1f} × {inner_h_cm:.1f} cm\n"
                        info += (f"边距：上{self.design.inner_margin_top_cm:.1f}/下{self.design.inner_margin_bottom_cm:.1f}/"
                                 f"左{self.design.inner_margin_left_cm:.1f}/右{self.design.inner_margin_right_cm:.1f} cm\n")
                # 内挖素材匹配结果
                if inner_match_info:
                    info += inner_match_info
                if self.design.mode == 'rect_lshape':
                    # ===== [L-Shape Panel Refactor 2026-09-02] 从 LShapePanel 读取 L 形参数 =====
                    # 原 getattr(self, '_lshape_params', None) 已迁移到 LShapePanel；
                    # 通过 self._get_lshape_params() 间接访问，语义与原直读字段一致。
                    lp = self._get_lshape_params() or {}
                    info += (f"L形草图识别：corner={lp.get('corner', '?')}，"
                             f"挖角 {float(lp.get('cut_w_cm', 0)):.1f} × "
                             f"{float(lp.get('cut_h_cm', 0)):.1f} cm\n")
                elif sketch_result is not None and sketch_result.success:
                    sr = sketch_result
                    # ===== [MULTI-HOLE Add-On 2026-08-29] 草图结果行 多洞替换 =====
                    sr_is_mh = (getattr(sr, 'is_multi_hole', False)
                                and hasattr(sr, 'holes')
                                and isinstance(sr.holes, list)
                                and len(sr.holes) >= 2)
                    if sr_is_mh:
                        info += f"识别草图成功（{getattr(sr,'layout_type','horizontal')}，{len(sr.holes)}洞）：\n"
                        info += f"  外框：{sr.outer_w_cm:.1f} × {sr.outer_h_cm:.1f} cm\n"
                        # ===== [MULTI-HOLE Add-On 2026-08-29] 防御性过滤：仅 w>0 or h>0 才打印 =====
                        valid_holes = [h for h in sr.holes if (float(getattr(h,'w_cm',0))>0) or (float(getattr(h,'h_cm',0))>0)]
                        for i, h in enumerate(valid_holes):
                            info += f"  洞{i+1}：{h.w_cm:.1f} × {h.h_cm:.1f} cm\n"
                        sr_gaps = list(getattr(sr, 'hole_gaps_cm', []) or [])[:max(0,len(valid_holes)-1)]
                        g_txt = "，".join(f"间{i+1}_{i+2}={g:.1f}" for i, g in enumerate(sr_gaps)) if sr_gaps else "无"
                        # ===== [PER-HOLE Add-On] 草图结果每洞独立边距 =====
                        for i, h in enumerate(valid_holes):
                            smt = getattr(h, 'margin_top_cm', sr.margin_top_cm)
                            smb = getattr(h, 'margin_bottom_cm', sr.margin_bottom_cm)
                            sml = getattr(h, 'margin_left_cm', sr.margin_left_cm)
                            smr = getattr(h, 'margin_right_cm', sr.margin_right_cm)
                            info += (f"  洞{i+1}边距：上{smt:.1f}/下{smb:.1f}/"
                                     f"左{sml:.1f}/右{smr:.1f} cm\n")
                        info += f"  中距：{g_txt}\n"
                    else:
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
        # L 形模式：margins 恒为 0（L 形 = 画布挖角），不检测用户边距修改
        # ===== [L-Shape Panel Refactor 2026-09-02] 从 LShapePanel 读取 L 形参数 =====
        if self._get_lshape_params() is not None:
            return result
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

    # ===== [MULTI-HOLE Add-On 2026-08-29] 多洞 UI → Worker 传值采集 =====
    def _detect_multihole_edits(self):
        """读取多洞参数面板当前的洞宽/洞高/洞间距真值。

        与单洞 _detect_user_margin_edits 语义一致：
        - 单洞/多洞 GroupBox 未激活（active_count<2）→ 返回 None，Worker 不做覆盖。
        - 多洞激活时：即使"没改"，也返回当前 SpinBox 的值。这样能保证：
          1) 用户先点一次"匹配模板→解析草图→生成预览"回填 UI，
          2) 用户改洞2宽或间距1↔2后再次点按钮，
          3) Worker 直接用 UI 真值重建 design.pool_holes_cm，完全与单洞"边距覆盖
             sketch"链路对齐。
        返回形状见 PoolRenderWorker.__init__ 中 user_multihole_params 的 docstring。
        """
        try:
            # —— 防御性前置条件（任何一条不满足 → 返回 None，零侵入）——
            if not hasattr(self, '_mh_active_count'):
                return None
            active_count = int(getattr(self, '_mh_active_count', 0) or 0)
            if active_count < 2:
                return None
            holes_w = getattr(self, '_mh_sp_hole_w', None)
            holes_h = getattr(self, '_mh_sp_hole_h', None)
            gaps = getattr(self, '_mh_sp_gaps', None)
            if not isinstance(holes_w, list) or not isinstance(holes_h, list):
                return None
            if len(holes_w) < active_count or len(holes_h) < active_count:
                return None
            n_gaps_needed = active_count - 1
            if n_gaps_needed > 0:
                if not isinstance(gaps, list) or len(gaps) < n_gaps_needed:
                    return None
            # —— 真值采集：前 active_count 个洞 + 前 active_count-1 间距 ——
            wh = []
            for i in range(active_count):
                wv = max(0.0, float(holes_w[i].value()))
                hv = max(0.0, float(holes_h[i].value()))
                wh.append((wv, hv))
            gs = []
            for i in range(n_gaps_needed):
                gs.append(max(0.0, float(gaps[i].value())))
            layout = None
            if hasattr(self, 'design') and self.design is not None:
                layout = getattr(self.design, 'pool_layout_type', None)
            if (getattr(self, '_sketch_parse_result', None) is not None
                    and getattr(self._sketch_parse_result, 'success', False)):
                layout = (getattr(self._sketch_parse_result, 'layout_type', None)
                          or layout)
            layout = layout or 'horizontal'
            return {
                'active_count': active_count,
                'holes_wh': wh,
                'gaps_cm': gs,
                'layout_type': layout,
            }
        except Exception as e:
            logger.warning(f"[Multi-hole UI] _detect_multihole_edits 失败（忽略，按 sketch 默认跑）: {e}")
            return None

