from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import TYPE_CHECKING, Callable, Mapping, Protocol, runtime_checkable

from services.parser.template_matcher import TemplateEntry

if TYPE_CHECKING:
    from core.geometry import CropDesign
from core.multihole_layout import layout_holes


class DesignBuildError(ValueError):
    """Base error for invalid design-build inputs."""


class DesignBuildValidationError(DesignBuildError):
    """Raised when a design cannot satisfy geometric validation."""


@dataclass(frozen=True)
class PoolBuildParams:
    target: str = ""
    user_margins: dict | None = None
    user_multihole_params: dict | None = None

    def validate(self) -> None:
        if not isinstance(self.target, str):
            raise DesignBuildValidationError(
                f"PoolBuildParams.target 必须是字符串，实际: {type(self.target).__name__}")
        if self.user_margins is not None and not isinstance(self.user_margins, dict):
            raise DesignBuildValidationError(
                f"PoolBuildParams.user_margins 必须是 dict 或 None，"
                f"实际: {type(self.user_margins).__name__}")
        if (self.user_multihole_params is not None
                and not isinstance(self.user_multihole_params, dict)):
            raise DesignBuildValidationError(
                f"PoolBuildParams.user_multihole_params 必须是 dict 或 None，"
                f"实际: {type(self.user_multihole_params).__name__}")


@dataclass(frozen=True)
class LShapeBuildParams:
    values: dict

    def validate(self) -> None:
        if not isinstance(self.values, dict):
            raise DesignBuildValidationError(
                f"LShapeBuildParams.values 必须是 dict，实际: {type(self.values).__name__}")
        corner = self.values.get('corner')
        if corner is not None and corner not in ('tl', 'tr', 'bl', 'br'):
            raise DesignBuildValidationError(
                f"LShapeBuildParams.corner 必须是 tl/tr/bl/br，实际: {corner!r}")
        for key in ('cut_w_cm', 'cut_h_cm'):
            val = self.values.get(key)
            if val is not None and float(val) < 0:
                raise DesignBuildValidationError(
                    f"LShapeBuildParams.{key} 不能为负: {val}")


@dataclass(frozen=True)
class CompositeBuildParams:
    values: dict

    def validate(self) -> None:
        if not isinstance(self.values, dict):
            raise DesignBuildValidationError(
                f"CompositeBuildParams.values 必须是 dict，"
                f"实际: {type(self.values).__name__}")
        for key in ('outer_w_cm', 'outer_h_cm'):
            val = self.values.get(key)
            if val is not None and float(val) <= 0:
                raise DesignBuildValidationError(
                    f"CompositeBuildParams.{key} 必须为正数: {val}")
        cuts = self.values.get('cuts_cm')
        if cuts is not None:
            if not isinstance(cuts, list):
                raise DesignBuildValidationError(
                    f"CompositeBuildParams.cuts_cm 必须是 list，"
                    f"实际: {type(cuts).__name__}")
            for i, cut in enumerate(cuts):
                if not isinstance(cut, dict):
                    raise DesignBuildValidationError(
                        f"CompositeBuildParams.cuts_cm[{i}] 必须是 dict")
                corner = cut.get('corner')
                if corner is not None and corner not in ('tl', 'tr', 'bl', 'br'):
                    raise DesignBuildValidationError(
                        f"CompositeBuildParams.cuts_cm[{i}].corner 必须是 tl/tr/bl/br，"
                        f"实际: {corner!r}")


@dataclass(frozen=True)
class LegacyBuildSnapshot:
    target: str = ""
    user_margins: dict | None = None
    user_multihole_params: dict | None = None
    lshape_params: dict | None = None
    composite_params: dict | None = None


def apply_lshape_geometry(
    design: CropDesign,
    params: dict,
    best_path: str,
) -> None:
    from core.geometry import CutRect, limit_l_cut_rects_per_anchor
    if isinstance(params, LShapeBuildParams):
        params = params.values
    design.mode = 'rect_lshape'
    design.outer_margin_cm = 0.0
    design.l_corner = params.get('corner', 'tr')
    design.l_cut_w_cm = float(params.get('cut_w_cm', 0))
    design.l_cut_h_cm = float(params.get('cut_h_cm', 0))
    rects = params.get('cut_rects') or []
    if rects:
        design.l_cut_rects = limit_l_cut_rects_per_anchor([
            CutRect(
                anchor=str(r['anchor']),
                offset_x_cm=float(r.get('offset_x_cm', 0)),
                offset_y_cm=float(r.get('offset_y_cm', 0)),
                w_cm=float(r.get('w_cm', 0)),
                h_cm=float(r.get('h_cm', 0)),
            )
            for r in rects
            if isinstance(r, dict)
            and r.get('anchor') in {'tl', 'tr', 'bl', 'br'}
            and float(r.get('w_cm', 0) or 0) > 0
            and float(r.get('h_cm', 0) or 0) > 0
        ])
    else:
        design.l_cuts_cm = [
            {
                'corner': str(c['corner']),
                'cut_w_cm': float(c['cut_w_cm']),
                'cut_h_cm': float(c['cut_h_cm']),
            }
            for c in (params.get('cuts_cm') or [])
            if isinstance(c, dict) and c.get('corner') in {'tl', 'tr', 'bl', 'br'}
        ][:4]
    design.inner_margin_top_cm = 0.0
    design.inner_margin_bottom_cm = 0.0
    design.inner_margin_left_cm = 0.0
    design.inner_margin_right_cm = 0.0
    design.pool_hole_transparent = True
    design.pool_outer_material_image = best_path
    design.outer_bg_image = best_path
    design.pool_inner_material_image = best_path


def build_multihole_geometry(
    design: CropDesign,
    sketch_result: object | None,
    trim_cm: float,
    user_multihole: dict | None = None,
    log: Callable[[str], None] | None = None,
) -> None:
    """[MULTI-HOLE Add-On] 仅当 is_multi_hole 且 holes>=2 时生效；单洞零影响。"""
    if log is None:
        log = lambda _msg: None
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
        gaps = [max(0.0, g - trim_cm) for g in gaps]

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
        log(
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
                w_cm = max(0.0, h.w_cm) + trim_cm  # 往外扩1cm
                h_cm = max(0.0, h.h_cm) + trim_cm  # 往外扩1cm
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
                w_cm = max(0.0, h.w_cm) + trim_cm  # 往外扩1cm
                h_cm = max(0.0, h.h_cm) + trim_cm  # 往外扩1cm
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
                _w_exp = max(0.0, h.w_cm) + trim_cm  # 往外扩1cm
                _h_exp = max(0.0, h.h_cm) + trim_cm  # 往外扩1cm
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

        log(
            f"多洞模式写入: N={len(holes)} layout={layout} "
            f"gaps={[round(g,1) for g in gaps]}"
        )
        log(
            f"  [挖洞扩展] 每洞尺寸+{trim_cm:.1f}cm，间距-{trim_cm:.1f}cm补偿 "
            f"（保证 ml+Σ(w+1)+Σ(gap-1)+mr = outer+1 = canvas）"
        )
        for i, hc in enumerate(design.pool_holes_cm):
            log(
                f"  Hole[{i}] 画布位置 x={hc['x_cm']:.1f} y={hc['y_cm']:.1f} "
                f"size={hc['w_cm']:.1f}x{hc['h_cm']:.1f} cm"
            )

        # ===== [END ADD-ON] =====

    # ===== [MULTI-HOLE UI OVERRIDE Add-On 2026-08-29] =====
    # 用户在多洞参数面板上改动后：UI → _detect_multihole_edits() →
    # user_multihole → 覆盖每洞 w/h/间距并重算 x/y，保证
    # 后续每洞素材匹配 (_on_pool_finished_ok) 和预览都使用 UI 最新值。
    # 单洞（_user_multihole 为 None 或 active_count<2）→ 直接跳过。
    # **关键修复**：此段必须在 sketch gate 之外独立运行，确保即使 sketch
    # 未识别出多洞，用户手动配置的多洞参数仍能生效，使 pool_holes_cm
    # 被正确填充，从而触发下游的逐洞素材匹配逻辑。
    _ump = user_multihole
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
            # ===== [MULTI-HOLE PER-HOLE MARGIN FIX 2026-09-29] =====
            # 优先级：UI 传入的每洞边距 > sketch gate 的 pool_holes_cm > 共享 margin。
            # _detect_multihole_edits() 收集了用户手动修改的 mt/mb/ml/mr，
            # 此前这些值被 silently discarded，现正确使用。
            _ui_mt = _ump.get('mt') or []
            _ui_mb = _ump.get('mb') or []
            _ui_ml = _ump.get('ml') or []
            _ui_mr = _ump.get('mr') or []
            _old = list(design.pool_holes_cm or [])

            def _ui_margin(ui_list, i):
                """从 UI 传入的每洞边距列表取值，无效则返回 None。"""
                if 0 <= i < len(ui_list):
                    v = ui_list[i]
                    if v is not None and float(v) > 0:
                        return float(v)
                return None

            def _old_margin(old_list, i, key):
                """从 sketch gate 的 pool_holes_cm 取值，无效则返回 None。"""
                if 0 <= i < len(old_list):
                    v = old_list[i].get(key, 0.0)
                    if v and float(v) > 0:
                        return float(v)
                return None

            def _mt_i(i):
                return (_ui_margin(_ui_mt, i)
                        or _old_margin(_old, i, 'mt_cm')
                        or design.inner_margin_top_cm)

            def _mb_i(i):
                return (_ui_margin(_ui_mb, i)
                        or _old_margin(_old, i, 'mb_cm')
                        or design.inner_margin_bottom_cm)

            def _ml_i(i, shared_ml):
                return (_ui_margin(_ui_ml, i)
                        or _old_margin(_old, i, 'ml_cm')
                        or (shared_ml if i == 0 else 0.0))

            def _mr_i(i, shared_mr):
                return (_ui_margin(_ui_mr, i)
                        or _old_margin(_old, i, 'mr_cm')
                        or (shared_mr if i == _n - 1 else 0.0))
            # ===== [END PER-HOLE MARGIN FIX] =====
            _ox = design.outer_margin_cm
            _oy = design.outer_margin_cm
            _s_ml = design.inner_margin_left_cm
            _s_mt = design.inner_margin_top_cm
            _s_mr = design.inner_margin_right_cm
            _new_holes = layout_holes(
                _lo, _ox, _oy, _new_wh, _new_gaps,
                lambda i: _mt_i(i), lambda i: _mb_i(i),
                lambda i: _ml_i(i, _s_ml), lambda i: _mr_i(i, _s_mr))
            design.pool_holes_cm = _new_holes
            design.pool_holes_gaps_cm = _new_gaps
            design.pool_is_multi_hole = True
            log(
                f"[多洞UI覆盖] 应用用户手动修改的多洞参数: "
                f"N={_n} layout={_lo} "
                f"wh={[(round(w,1),round(h,1)) for w,h in _new_wh]} "
                f"gaps={[round(g,1) for g in _new_gaps]}"
            )
            for i, hc in enumerate(_new_holes):
                log(
                    f"  Hole[{i}] UI覆盖后 x={hc['x_cm']:.1f} y={hc['y_cm']:.1f} "
                    f"size={hc['w_cm']:.1f}x{hc['h_cm']:.1f} cm"
                )
    # ===== [END UI OVERRIDE Add-On] =====


def apply_pool_geometry(
    design: CropDesign,
    target: str,
    sketch_result: object | None,
    canvas_w_cm: float,
    canvas_h_cm: float,
    user_margins: dict | None,
    trim_cm: float,
    best_path: str,
    user_multihole: dict | None = None,
    log: Callable[[str], None] | None = None,
    is_lshape: bool = False,
) -> None:
    design.mode = ('ellipse_hole'
                   if '椭圆' in str(target or '').lower()
                   or 'ellipse' in str(target or '').lower()
                   else 'rect_hole')
    if sketch_result and sketch_result.success:
        vals = [sketch_result.margin_top_cm, sketch_result.margin_bottom_cm,
                sketch_result.margin_left_cm, sketch_result.margin_right_cm]
        if user_margins and not is_lshape:
            vals = [float(user_margins[k]) if user_margins.get(k) is not None else v
                    for k, v in zip(('top', 'bottom', 'left', 'right'), vals)]
        (design.inner_margin_top_cm, design.inner_margin_bottom_cm,
         design.inner_margin_left_cm, design.inner_margin_right_cm) = vals
    else:
        m = min(canvas_w_cm, canvas_h_cm) * .10
        (design.inner_margin_top_cm, design.inner_margin_bottom_cm,
         design.inner_margin_left_cm, design.inner_margin_right_cm) = (m, m, m, m)
    build_multihole_geometry(design, sketch_result, trim_cm, user_multihole, log)
    design.pool_hole_transparent = True
    design.pool_outer_material_image = best_path
    design.outer_bg_image = best_path


def apply_composite_geometry(
    request: DesignBuildRequest,
    log: Callable[[str], None] | None = None,
) -> CropDesign:
    """Build the composite shape from its pure parameter snapshot."""
    from core.geometry import CropDesign
    from models.design_model import DesignModel
    log = log or (lambda _msg: None)
    design = CropDesign(canvas_w_cm=request.canvas_w_cm + request.trim_cm,
                        canvas_h_cm=request.canvas_h_cm + request.trim_cm,
                        dpi=150)
    params = dict(request.composite_params.values or {})
    params['canvas_w_cm'] = float(params['outer_w_cm']) + request.trim_cm
    params['canvas_h_cm'] = float(params['outer_h_cm']) + request.trim_cm
    params['outer_margin_cm'] = 0.0
    if not params.get('cuts_cm'):
        raise DesignBuildValidationError("综合形状至少需要 1 处挖角")
    model = DesignModel(design)
    model.apply_composite_params(params)
    design = model.to_design()
    design.validate()
    design.pool_outer_material_image = request.best.path
    design.outer_bg_image = request.best.path
    log("综合形状：外框挖角 + 单中心洞（参数来自综合面板）")
    return design


@dataclass(frozen=True)
class DesignBuildRequest:
    mode: str
    best: TemplateEntry
    sketch_result: object | None
    canvas_w_cm: float
    canvas_h_cm: float
    trim_cm: float
    pool_params: PoolBuildParams = PoolBuildParams()
    lshape_params: LShapeBuildParams | None = None
    composite_params: CompositeBuildParams | None = None

    def validate(self) -> None:
        if self.mode not in ("pool", "lshape", "composite"):
            raise DesignBuildValidationError(f"未知构建模式: {self.mode!r}")
        if self.trim_cm < 0:
            raise DesignBuildValidationError(f"trim_cm 不能为负: {self.trim_cm}")
        if self.mode == "lshape" and self.lshape_params is None:
            raise DesignBuildValidationError("L 形模式需要 lshape_params")
        if self.mode == "composite" and self.composite_params is None:
            raise DesignBuildValidationError("综合形状模式需要 composite_params")


@dataclass(frozen=True)
class DesignBuildContext:
    new_design: Callable[[float, float, float], CropDesign]
    log: Callable[[str], None]


@runtime_checkable
class DesignBuilder(Protocol):
    def build(self, request: DesignBuildRequest,
              context: DesignBuildContext) -> CropDesign: ...


class LegacyRequestAdapter:
    @staticmethod
    def from_snapshot(
        snapshot: LegacyBuildSnapshot,
        best: TemplateEntry,
        sketch_result: object | None,
        canvas_w_cm: float,
        canvas_h_cm: float,
        is_lshape: bool,
        trim_cm: float,
    ) -> DesignBuildRequest:
        composite_params = snapshot.composite_params
        mode = ('composite' if composite_params is not None
                else 'lshape' if is_lshape
                else 'pool')
        return DesignBuildRequest(
            mode=mode,
            best=best,
            sketch_result=sketch_result,
            canvas_w_cm=float(canvas_w_cm),
            canvas_h_cm=float(canvas_h_cm),
            trim_cm=float(trim_cm),
            pool_params=PoolBuildParams(
                target=snapshot.target or "",
                user_margins=snapshot.user_margins,
                user_multihole_params=snapshot.user_multihole_params),
            lshape_params=(LShapeBuildParams(snapshot.lshape_params)
                           if snapshot.lshape_params is not None else None),
            composite_params=(CompositeBuildParams(composite_params)
                              if composite_params is not None else None),
        )


class PoolDesignBuilder:
    def build(self, request: DesignBuildRequest,
              context: DesignBuildContext) -> CropDesign:
        request.validate()
        request.pool_params.validate()
        design = context.new_design(
            request.canvas_w_cm, request.canvas_h_cm, request.trim_cm)
        apply_pool_geometry(
            design, request.pool_params.target, request.sketch_result,
            request.canvas_w_cm, request.canvas_h_cm,
            request.pool_params.user_margins, request.trim_cm,
            request.best.path,
            request.pool_params.user_multihole_params, context.log)
        return design


class LShapeDesignBuilder:
    def build(self, request: DesignBuildRequest,
              context: DesignBuildContext) -> CropDesign:
        request.validate()
        if request.lshape_params is not None:
            request.lshape_params.validate()
        design = context.new_design(
            request.canvas_w_cm, request.canvas_h_cm, request.trim_cm)
        apply_lshape_geometry(
            design, request.lshape_params.values if request.lshape_params else {},
            request.best.path)
        return design


class CompositeDesignBuilder:
    def build(self, request: DesignBuildRequest,
              context: DesignBuildContext) -> CropDesign:
        request.validate()
        if request.composite_params is not None:
            request.composite_params.validate()
        return apply_composite_geometry(request, context.log)


BUILDERS: dict[str, DesignBuilder] = {
    "pool": PoolDesignBuilder(),
    "lshape": LShapeDesignBuilder(),
    "composite": CompositeDesignBuilder(),
}
