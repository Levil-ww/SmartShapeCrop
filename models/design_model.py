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

from core.geometry import (
    CropDesign, BorderText, CutRect, limit_l_cut_rects_per_anchor,
    COMPOSITE_MODE, is_lshape_layout,
)


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
        """应用 CompositePanel 的纯参数快照，不影响旧模式快照路径。

        canvas_*_cm 由调用方给出画布值（外框设计真值 + CUT_LOSS_CM）；
        cuts_cm 非空时是挖角的唯一几何真值，并同步首个挖角到单角字段。
        """
        d = copy.deepcopy(self._design)
        d.mode = 'rect_lshape_hole'
        d.canvas_w_cm = float(params.get('canvas_w_cm', d.canvas_w_cm))
        d.canvas_h_cm = float(params.get('canvas_h_cm', d.canvas_h_cm))
        d.outer_margin_cm = float(params.get('outer_margin_cm', d.outer_margin_cm))
        d.inner_margin_top_cm = float(params.get('hole_margin_top_cm', d.inner_margin_top_cm))
        d.inner_margin_bottom_cm = float(params.get('hole_margin_bottom_cm', d.inner_margin_bottom_cm))
        d.inner_margin_left_cm = float(params.get('hole_margin_left_cm', d.inner_margin_left_cm))
        d.inner_margin_right_cm = float(params.get('hole_margin_right_cm', d.inner_margin_right_cm))
        d.l_corner = params.get('corner', d.l_corner)
        d.l_cut_w_cm = float(params.get('cut_w_cm', d.l_cut_w_cm))
        d.l_cut_h_cm = float(params.get('cut_h_cm', d.l_cut_h_cm))
        cuts = self._normalize_l_cuts(params.get('cuts_cm'))
        if cuts:
            d.l_cuts_cm = cuts
            d.l_corner = cuts[0]['corner']
            d.l_cut_w_cm = cuts[0]['cut_w_cm']
            d.l_cut_h_cm = cuts[0]['cut_h_cm']
        # 清理从旧模式继承的几何：l_cut_rects 优先级高于 l_cuts_cm（阶梯矩形会静默覆盖
        # 综合挖角），多洞分支也未按 mode 守卫（会被误当作多洞设计渲染）
        d.l_cut_rects = []
        d.pool_is_multi_hole = False
        d.pool_holes_cm = []
        d.pool_holes_gaps_cm = []
        d.pool_hole_transparent = params.get('hole_fill_mode', 'blank') != 'image'
        self._design = d

    @staticmethod
    def _normalize_l_cuts(cuts) -> list[dict]:
        """规整面板挖角列表：最多 4 个，宽高转 float。

        重复角位 / 非法尺寸原样保留，交由 CropDesign.validate() 拒绝（与 L 形口径一致）。
        """
        picked: list[dict] = []
        for cut in cuts or []:
            if not isinstance(cut, dict):
                continue
            picked.append({
                'corner': cut.get('corner'),
                'cut_w_cm': float(cut.get('cut_w_cm', 0) or 0),
                'cut_h_cm': float(cut.get('cut_h_cm', 0) or 0),
            })
            if len(picked) >= 4:
                break
        return picked

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
    # 集中在以下方法内执行，行为与原 _collect() 完全等价。
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
        d.dpi = snap['dpi']
        d.mode = snap['mode']
        _is_composite = d.mode == COMPOSITE_MODE
        self._apply_common_snapshot(d, snap, _is_composite)
        self._apply_lshape_snapshot(d, snap, _is_composite)
        self._apply_pool_snapshot(d, snap, _is_composite)

    # ---- 子方法 ----

    def _apply_common_snapshot(
        self, d: CropDesign, snap: dict, is_composite: bool,
    ) -> None:
        """通用字段：画布 / 边距 / 圆角 / 椭圆 / 颜色 / 素材 / 文字。"""
        if not is_composite:
            d.canvas_w_cm = snap['canvas_w_cm']
            d.canvas_h_cm = snap['canvas_h_cm']
        if d.mode == 'ellipse_hole':
            d.outer_margin_cm = snap['outer_margin_cm']
        if not is_lshape_layout(d.mode):
            d.inner_margin_top_cm = snap['inner']['top']
            d.inner_margin_bottom_cm = snap['inner']['bottom']
            d.inner_margin_left_cm = snap['inner']['left']
            d.inner_margin_right_cm = snap['inner']['right']
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
        if snap['text']['enabled']:
            bt = d.border_text or BorderText()
            bt.text = snap['text']['text']
            bt.font_size_px = snap['text']['font_size_px']
            bt.color = snap['text']['color']
            bt.mirror_bottom = snap['text']['mirror_bottom']
            d.border_text = bt
        else:
            d.border_text = None

    def _apply_lshape_snapshot(
        self, d: CropDesign, snap: dict, is_composite: bool,
    ) -> None:
        """L 形挖角参数（复合守卫：CompositePanel 独占写入时跳过）。"""
        _lp = snap.get('lshape')
        if is_composite:
            return
        if _lp is not None:
            d.l_corner = _lp.get('corner', 'br')
            d.l_cut_w_cm = _lp.get('cut_w_cm', 0.0)
            d.l_cut_h_cm = _lp.get('cut_h_cm', 0.0)
            _cr = _lp.get('cut_rects') or []
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

    def _apply_pool_snapshot(
        self, d: CropDesign, snap: dict, is_composite: bool,
    ) -> None:
        """水池模式字段同步：素材 / 透明 / 多洞重建。"""
        try:
            if d.mode == 'rect_lshape':
                d.pool_hole_transparent = True
            elif is_composite:
                pass
            else:
                hm = snap['hole_mode']
                if hm == "blank":
                    d.pool_hole_transparent = True
                    if getattr(d, 'pool_inner_material_image', None) is not None:
                        d.pool_inner_material_image = None
                    if d.hole_bg_image is not None:
                        d.hole_bg_image = None
                elif hm == "image":
                    d.pool_hole_transparent = False
        except Exception:
            pass
        if d.outer_bg_image and (d.pool_outer_material_image is None
                                 or d.pool_outer_material_image != d.outer_bg_image):
            d.pool_outer_material_image = d.outer_bg_image
        hm_now = snap['hole_mode']
        if hm_now == "image":
            cur_inner = getattr(d, 'pool_inner_material_image', None)
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
        try:
            self._rebuild_multihole(d, snap)
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "[Multi-hole Model] 多洞字段回写 design 失败"
            )

    # ---- 多洞重建 ----

    def _rebuild_multihole(self, d: CropDesign, snap: dict) -> None:
        """严格按激活洞数重建 pool_holes_cm / pool_holes_gaps_cm。"""
        _mh = snap.get('multihole')
        if not (getattr(d, 'pool_is_multi_hole', False)
                and _mh is not None
                and isinstance(_mh, dict)):
            return
        active_count = int(_mh.get('active_count', 0) or 0)
        if active_count < 2:
            active_count = 0
        holes_w = _mh['holes_w']
        n_holes = max(0, min(active_count, len(holes_w)))
        n_gaps = max(0, min(n_holes - 1, len(_mh.get('gaps', []) or [])))
        if n_holes < 2:
            try:
                d.pool_holes_cm = []
                d.pool_holes_gaps_cm = []
                setattr(d, 'pool_is_multi_hole', False)
            except Exception:
                pass
            return
        layout = getattr(d, 'pool_layout_type', None) or 'horizontal'
        ox_cm = d.outer_margin_cm
        oy_cm = d.outer_margin_cm
        new_wh = [
            (max(0.0, float(holes_w[i])), max(0.0, float(_mh['holes_h'][i])))
            for i in range(n_holes)
        ]
        new_gaps = [
            max(0.0, float(_mh['gaps'][i])) for i in range(n_gaps)
        ]
        old_holes = getattr(d, 'pool_holes_cm', []) or []
        shared_mt = d.inner_margin_top_cm
        shared_mb = d.inner_margin_bottom_cm
        shared_ml = d.inner_margin_left_cm
        shared_mr = d.inner_margin_right_cm
        _MATERIAL_KEYS = ('inner_material_path', '_cached_inner_image',
                          '_src_design_w_cm', '_src_design_h_cm')

        def _mt_i(i, default):
            vals = _mh.get('mt') or []
            if 0 <= i < len(vals) and vals[i]:
                v = float(vals[i])
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
            return default

        def _mb_i(i, default):
            vals = _mh.get('mb') or []
            if 0 <= i < len(vals) and vals[i]:
                v = float(vals[i])
                if v and v > 0:
                    return v
            if 0 <= i < len(old_holes):
                v = old_holes[i].get('mb_cm', 0.0)
                if v and v > 0:
                    return v
            return default

        def _ml_i(i, default):
            vals = _mh.get('ml') or []
            if 0 <= i < len(vals) and vals[i]:
                v = float(vals[i])
                if v and v > 0:
                    return v
            if 0 <= i < len(old_holes):
                v = old_holes[i].get('ml_cm', 0.0)
                if v and v > 0:
                    return v
            return default if i == 0 else 0.0

        def _mr_i(i, default):
            vals = _mh.get('mr') or []
            if 0 <= i < len(vals) and vals[i]:
                v = float(vals[i])
                if v and v > 0:
                    return v
            if 0 <= i < len(old_holes):
                v = old_holes[i].get('mr_cm', 0.0)
                if v and v > 0:
                    return v
            return default if i == n_holes - 1 else 0.0

        def _inherit_material(i):
            src = (old_holes[i]
                   if 0 <= i < len(old_holes) and isinstance(old_holes[i], dict)
                   else {})
            return {k: src[k] for k in _MATERIAL_KEYS if k in src}

        def _build_hole(x_cm, y_cm, wv, hv, hmt, hmb, hml, hmr, i):
            hole = {
                'x_cm': x_cm, 'y_cm': y_cm,
                'w_cm': wv, 'h_cm': hv,
                'mt_cm': hmt, 'mb_cm': hmb,
                'ml_cm': hml, 'mr_cm': hmr,
            }
            hole.update(_inherit_material(i))
            return hole

        new_holes_cm = []
        if layout == 'vertical':
            cursor_y = oy_cm + _mt_i(0, shared_mt)
            for i, (wv, hv) in enumerate(new_wh):
                if i > 0 and i - 1 < len(new_gaps):
                    cursor_y += new_gaps[i - 1]
                hmt = _mt_i(i, shared_mt)
                hmb = _mb_i(i, shared_mb)
                hml = _ml_i(i, shared_ml)
                hmr = _mr_i(i, shared_mr)
                new_holes_cm.append(
                    _build_hole(ox_cm + hml, cursor_y, wv, hv, hmt, hmb, hml, hmr, i)
                )
                cursor_y += hv
        else:
            cursor_x = ox_cm + _ml_i(0, shared_ml)
            for i, (wv, hv) in enumerate(new_wh):
                if i > 0 and i - 1 < len(new_gaps):
                    cursor_x += new_gaps[i - 1]
                hmt = _mt_i(i, shared_mt)
                hmb = _mb_i(i, shared_mb)
                hml = _ml_i(i, shared_ml)
                hmr = _mr_i(i, shared_mr)
                new_holes_cm.append(
                    _build_hole(cursor_x, oy_cm + hmt, wv, hv, hmt, hmb, hml, hmr, i)
                )
                cursor_x += wv
        d.pool_holes_cm = new_holes_cm
        d.pool_holes_gaps_cm = new_gaps
