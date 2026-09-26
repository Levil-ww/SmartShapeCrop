"""models/design_model.py

DesignModel：CropDesign 的薄包装器。

职责：
  - 持有一个 CropDesign 实例（单一数据源）
  - 提供 sync_from_design(d) 更新数据（替代 main.py 直接操作 Panel 私有控件）
  - 提供 to_design() 返回克隆（防竞态快照）
  - 提供 get/set 属性访问（_collect() 通过 apply_ui_snapshot() Model 驱动，替代 SpinBox 直读组装）

不含 UI 引用、不含业务逻辑（apply_ui_snapshot 只接收 UI 层提取的纯值 dict）。
"""
from __future__ import annotations
import copy
import os

from core.geometry import CropDesign, BorderText, CutRect, limit_l_cut_rects_per_anchor


class DesignModel:
    """CropDesign 包装器。

    用法：
        model = DesignModel(design)
        model.sync_from_design(new_design)   # 更新数据
        snapshot = model.to_design()          # 获取克隆快照
        w = model.canvas_w_cm                 # 属性读取
        model.apply_ui_snapshot(snap)         # [H-10] UI 纯值 → design（Model 驱动组装）
    """

    def __init__(self, design: CropDesign | None = None):
        if design is None:
            design = CropDesign(
                canvas_w_cm=80.0, canvas_h_cm=130.0, dpi=150,
                mode='rect_hole',
            )
        self._design = design

    # ---- 数据源 ----
    @property
    def design(self) -> CropDesign:
        return self._design

    @design.setter
    def design(self, value: CropDesign):
        self._design = value

    def sync_from_design(self, d: CropDesign) -> None:
        """用外部 design 更新内部数据源（直接替换引用，不逐字段复制）。"""
        self._design = d

    def apply_composite_params(self, params: dict) -> None:
        """应用 CompositePanel 的纯参数快照，不影响旧模式快照路径。"""
        d = copy.deepcopy(self._design)
        d.mode = 'rect_lshape_hole'
        d.canvas_w_cm = float(params.get('canvas_w_cm', d.canvas_w_cm))
        d.canvas_h_cm = float(params.get('canvas_h_cm', d.canvas_h_cm))
        d.inner_margin_top_cm = float(params.get('hole_margin_top_cm', d.inner_margin_top_cm))
        d.inner_margin_bottom_cm = float(params.get('hole_margin_bottom_cm', d.inner_margin_bottom_cm))
        d.inner_margin_left_cm = float(params.get('hole_margin_left_cm', d.inner_margin_left_cm))
        d.inner_margin_right_cm = float(params.get('hole_margin_right_cm', d.inner_margin_right_cm))
        d.l_corner = params.get('corner', d.l_corner)
        d.l_cut_w_cm = float(params.get('cut_w_cm', d.l_cut_w_cm))
        d.l_cut_h_cm = float(params.get('cut_h_cm', d.l_cut_h_cm))
        d.pool_hole_transparent = params.get('hole_fill_mode', 'blank') != 'image'
        self._design = d

    def to_design(self) -> CropDesign:
        """返回 design 的深拷贝快照（防竞态：Worker 拿到的快照不会被后续 UI 改动影响）。"""
        return copy.deepcopy(self._design)

    # ---- 常用属性快捷访问 ----
    @property
    def canvas_w_cm(self) -> float:
        return self._design.canvas_w_cm

    @property
    def canvas_h_cm(self) -> float:
        return self._design.canvas_h_cm

    @property
    def dpi(self) -> int:
        return self._design.dpi

    @property
    def mode(self) -> str:
        return self._design.mode

    # =====================================================================
    # [H-10] Model 驱动组装：apply_ui_snapshot(snap)
    # ---------------------------------------------------------------------
    # 原 gui/property_panel_layers.py _collect() 直读 SpinBox/ComboBox/颜色按钮
    # 组装 CropDesign，业务规则（模式判断、素材同步、多洞几何重建）混入 UI 层。
    # 现改为：UI 层只负责把控件当前值提取为纯值 dict（snap），业务规则全部
    # 集中在本方法内执行，行为与原 _collect() 完全等价。
    # =====================================================================
    def apply_ui_snapshot(self, snap: dict) -> None:
        """按 UI 提取的纯值快照组装 design。

        snap 键约定（由 PropertyPanel._collect() 构造，不含任何 UI 控件引用）：
          canvas_w_cm / canvas_h_cm / dpi / mode
          outer_margin_cm
          inner: {'top','bottom','left','right'}
          lshape: {'corner','cut_w_cm','cut_h_cm'} 或 None（None=无 LShapePanel → 默认值）
          corners: {'tl','tr','bl','br'}
          ellipse: {'diameter_w_cm','diameter_h_cm'}
          colors: {'outer','hole'}
          images: {'outer','hole'}（str 或 None）
          text: {'enabled','text','font_size_px','color','mirror_bottom'}
          hole_mode: 'blank'|'image'|None（None=控件不可用）
          multihole: {
              'active_count': int,
              'holes_w': [float], 'holes_h': [float], 'gaps': [float],
              'mt': [float|None]*n, 'mb': [float|None]*n,
              'ml': [float|None]*n, 'mr': [float|None]*n,
          } 或 None
        """
        d = self._design
        d.canvas_w_cm = snap['canvas_w_cm']
        d.canvas_h_cm = snap['canvas_h_cm']
        d.dpi = snap['dpi']
        d.mode = snap['mode']
        # outer_margin: rect_lshape 和 rect_hole 都由 Worker 强制设为 0.0（水池花纹素材
        #   本身就是外框，不需要额外留白），不从 SpinBox 覆盖。仅 ellipse_hole 模式
        #   从 SpinBox 读取（Worker 未对椭圆模式设 outer_margin）。
        if d.mode == 'ellipse_hole':
            d.outer_margin_cm = snap['outer_margin_cm']
        # inner_margins: 仅 rect_lshape 由 Worker 固定为 0.0（L 形语义），
        # 其他模式允许 SpinBox 覆盖（property_panel_generate.py:204 也有同样的保护）
        if d.mode != 'rect_lshape':
            d.inner_margin_top_cm = snap['inner']['top']
            d.inner_margin_bottom_cm = snap['inner']['bottom']
            d.inner_margin_left_cm = snap['inner']['left']
            d.inner_margin_right_cm = snap['inner']['right']
        # ===== [L-Shape Panel Refactor 2026-09-02] L 形参数 =====
        # 原 self._cb_lcorner / _sp_lw / _sp_lh 已迁移到 LShapePanel；
        # UI 层提取 get_corner()/get_cut_w_cm()/get_cut_h_cm() 为纯值传入。
        _lp = snap.get('lshape')
        if _lp is not None:
            d.l_corner = _lp.get('corner', 'br')
            # 挖角值直接取草图识别的成品真值，不做额外损耗补偿
            d.l_cut_w_cm = _lp.get('cut_w_cm', 0.0)
            d.l_cut_h_cm = _lp.get('cut_h_cm', 0.0)
            # 阶梯路径：cut_rects 非空时是唯一几何来源（同角位多级），
            # 旧格式 l_cuts_cm 不允许同角位重复，必须保持为空
            _cr = _lp.get('cut_rects') or []
            # [Fix 2026-09-24 P2-4] 原为 [:3]（按总数截断）——与 validate() 的
            #   「同角位 ≤3」口径不一致，会把多锚定输入的第 4 条静默丢弃。
            #   改用按锚定角分组截断；单锚定输入（全部可达路径）逐例等价。
            d.l_cut_rects = limit_l_cut_rects_per_anchor([
                CutRect(
                    anchor=r.get('anchor', 'tr'),
                    offset_x_cm=float(r.get('offset_x_cm', 0)),
                    offset_y_cm=float(r.get('offset_y_cm', 0)),
                    w_cm=float(r.get('w_cm', 0)),
                    h_cm=float(r.get('h_cm', 0)),
                )
                for r in _cr
            ])
            if not d.l_cut_rects:
                d.l_cuts_cm = [dict(cut) for cut in (_lp.get('cuts_cm') or [])][:4]
                if d.l_cuts_cm:
                    primary = d.l_cuts_cm[0]
                    d.l_corner = primary['corner']
                    d.l_cut_w_cm = float(primary['cut_w_cm'])
                    d.l_cut_h_cm = float(primary['cut_h_cm'])
            else:
                d.l_cuts_cm = []
        else:
            d.l_corner = 'br'
            d.l_cut_w_cm = 0.0
            d.l_cut_h_cm = 0.0
            d.l_cuts_cm = []
            d.l_cut_rects = []
        # 圆角设置
        d.corner_tl_cm = snap['corners']['tl']
        d.corner_tr_cm = snap['corners']['tr']
        d.corner_bl_cm = snap['corners']['bl']
        d.corner_br_cm = snap['corners']['br']
        d.ellipse_diameter_w_cm = snap['ellipse']['diameter_w_cm']
        d.ellipse_diameter_h_cm = snap['ellipse']['diameter_h_cm']
        d.outer_bg_color = snap['colors']['outer']
        d.hole_bg_color = snap['colors']['hole']
        d.outer_bg_image = snap['images']['outer'] or None
        d.hole_bg_image = snap['images']['hole'] or None
        # 文字
        if snap['text']['enabled']:
            bt = d.border_text or BorderText()
            bt.text = snap['text']['text']
            bt.font_size_px = snap['text']['font_size_px']
            bt.color = snap['text']['color']
            bt.mirror_bottom = snap['text']['mirror_bottom']
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
                hm = snap['hole_mode']
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
        hm_now = snap['hole_mode']
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

        # ===== [MULTI-HOLE Add-On 2026-08-29] 多洞数据 → design.pool_holes_cm/gaps =====
        # 仅当 pool_is_multi_hole=True 且 UI 已提取多洞数据时，才把控件值同步回 design。
        # 单洞模式下：pool_is_multi_hole=False → pool_holes_cm 默认为空 → 零行为影响；
        # 旧单洞 L 形/椭圆/矩形 代码完全不经过这里。
        try:
            _mh = snap.get('multihole')
            # 注意：进入条件只要求 UI 提取了 multihole（UI 层已保证原始
            # SpinBox 数 >=2）。active_count<2 时 holes_w 截取后不足，
            # 走下方"激活洞数不足 2 → 清空多洞字段"分支，与原 _collect 一致。
            if (getattr(d, 'pool_is_multi_hole', False)
                    and _mh is not None
                    and isinstance(_mh, dict)):
                # ==== 严格按「激活洞数」取数据：避免 8 个 SpinBox 预分配 0 值被整体写回 ====
                # active_count 由 _fill_multi_hole_ui(n) / _hide_multi_hole_ui() 维护；
                # 检测为 2 洞 → N=2 → 只写回洞1/洞2 + 间1_2，其他洞3..洞8 不写入 status / mask。
                active_count = int(_mh.get('active_count', 0) or 0)
                if active_count < 2:
                    active_count = 0
                holes_w = _mh['holes_w']
                n_holes = max(0, min(active_count, len(holes_w)))
                n_gaps = max(0, min(n_holes - 1, len(_mh.get('gaps', []) or [])))
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
                        wv = max(0.0, float(holes_w[i]))
                        hv = max(0.0, float(_mh['holes_h'][i]))
                        new_wh.append((wv, hv))
                    # 间距：range(n_gaps) 限定前 N-1 个 SpinBox
                    new_gaps = []
                    for i in range(n_gaps):
                        new_gaps.append(max(0.0, float(_mh['gaps'][i])))
                    # 按 layout 重算绝对坐标（画布相对 cm）
                    # ===== [PER-HOLE Add-On 2026-08-29 + 2026-09-05 FIX] 每洞独立 mt/ml 坐标 =====
                    # 2026-09-05 FIX: 优先从 UI SpinBox 直接读取（用户手动编辑的值），
                    # 再 fallback 到 old_holes 存储值，最后才用全局默认。
                    old_holes = getattr(d, 'pool_holes_cm', []) or []

                    def _mt_i(i, default_mt):
                        # [2026-09-05] 优先从 UI SpinBox 读取（用户手动编辑）
                        mt_vals = _mh.get('mt') or []
                        if 0 <= i < len(mt_vals) and mt_vals[i]:
                            v = float(mt_vals[i])
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
                        mb_vals = _mh.get('mb') or []
                        if 0 <= i < len(mb_vals) and mb_vals[i]:
                            v = float(mb_vals[i])
                            if v and v > 0:
                                return v
                        if 0 <= i < len(old_holes):
                            v = old_holes[i].get('mb_cm', 0.0)
                            if v and v > 0:
                                return v
                        return default_mb

                    def _ml_i(i, default_ml):
                        # [2026-09-05] 优先从 UI SpinBox 读取
                        ml_vals = _mh.get('ml') or []
                        if 0 <= i < len(ml_vals) and ml_vals[i]:
                            v = float(ml_vals[i])
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
                        mr_vals = _mh.get('mr') or []
                        if 0 <= i < len(mr_vals) and mr_vals[i]:
                            v = float(mr_vals[i])
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
                    # 此处完全重建 pool_holes_cm，需要继承 UI 层素材匹配写入的
                    # inner_material_path / _cached_inner_image / _src_design_w_cm 等。
                    # 在每个洞构建完几何字段后，从 old_holes[i] 继承素材相关键。
                    _MATERIAL_KEYS = ('inner_material_path', '_cached_inner_image',
                                      '_src_design_w_cm', '_src_design_h_cm')

                    def _inherit_material(i):
                        """从 old_holes[i] 继承素材相关字段（重建会丢失）。"""
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
        except Exception:
            # 静默失败：不影响主预览流程
            import logging as _logging
            _logging.getLogger(__name__).warning(f"[Multi-hole Model] 多洞字段回写 design 失败")
