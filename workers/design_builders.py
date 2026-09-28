"""Design construction boundaries used by :mod:`property_panel_workers`.

The first extraction deliberately keeps the existing ``CropDesign`` schema and
the worker's public API intact.  These small objects provide a stable seam for
moving the mode-specific construction code out of the worker incrementally.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core.geometry import CropDesign, CutRect, limit_l_cut_rects_per_anchor


def apply_lshape_geometry(design: CropDesign, params: dict, best_path: str,
                          canvas_w_cm: float, canvas_h_cm: float,
                          trim_cm: float, log: Callable[[str], None] | None = None) -> None:
    """Apply the legacy L-shape field mapping without UI/worker dependencies."""
    log = log or (lambda _msg: None)
    design.outer_margin_cm = 0.0
    lw = float(params.get('outer_w_cm') or 0)
    lh = float(params.get('outer_h_cm') or 0)
    if lw > 0 and lh > 0:
        canvas_w_cm, canvas_h_cm = lw, lh
        design.canvas_w_cm = canvas_w_cm + trim_cm
        design.canvas_h_cm = canvas_h_cm + trim_cm
    design.mode = 'rect_lshape'
    design.l_corner = params.get('corner', 'tr')
    design.l_cut_w_cm = max(0.0, float(params.get('cut_w_cm', 0)))
    design.l_cut_h_cm = max(0.0, float(params.get('cut_h_cm', 0)))
    cut_rects = params.get('cut_rects') or []
    if cut_rects:
        design.l_cut_rects = limit_l_cut_rects_per_anchor([
            CutRect(anchor=str(r['anchor']),
                    offset_x_cm=max(0.0, float(r.get('offset_x_cm', 0))),
                    offset_y_cm=max(0.0, float(r.get('offset_y_cm', 0))),
                    w_cm=max(0.0, float(r.get('w_cm', 0))),
                    h_cm=max(0.0, float(r.get('h_cm', 0))))
            for r in cut_rects
            if isinstance(r, dict) and r.get('anchor') in {'tl', 'tr', 'bl', 'br'}
            and float(r.get('w_cm', 0) or 0) > 0
            and float(r.get('h_cm', 0) or 0) > 0
        ])
        log(f"L 形阶梯挖角：corner={design.l_corner}, "
            f"{len(design.l_cut_rects)} 级 CutRect（同角位条带）")
    else:
        design.l_cuts_cm = [
            {'corner': str(c['corner']),
             'cut_w_cm': max(0.0, float(c['cut_w_cm'])),
             'cut_h_cm': max(0.0, float(c['cut_h_cm']))}
            for c in (params.get('cuts_cm') or [])
            if isinstance(c, dict) and c.get('corner') in {'tl', 'tr', 'bl', 'br'}
            and float(c.get('cut_w_cm', 0) or 0) > 0
            and float(c.get('cut_h_cm', 0) or 0) > 0
        ][:4]
    design.inner_margin_top_cm = design.inner_margin_bottom_cm = 0.0
    design.inner_margin_left_cm = design.inner_margin_right_cm = 0.0
    design.pool_hole_transparent = True
    design.pool_outer_material_image = best_path
    design.outer_bg_image = best_path
    design.pool_inner_material_image = best_path
    edge = params.get('manual_edge_px')
    band = params.get('manual_band_px')
    color = params.get('manual_band_color')
    design.lshape_manual_edge_px = int(edge) if edge is not None else None
    design.lshape_manual_band_px = int(band) if band is not None else None
    design.lshape_manual_band_color = tuple(int(c) for c in color) if color is not None else None
    log(f"L 形挖角模式：corner={design.l_corner}, "
        f"挖角 {design.l_cut_w_cm:.1f}x{design.l_cut_h_cm:.1f} cm, "
        f"外框 {canvas_w_cm:.1f}x{canvas_h_cm:.1f} cm（画布含1cm损耗）")


@dataclass(frozen=True)
class DesignBuildRequest:
    """Pure input snapshot for one design construction operation."""

    mode: str
    best: Any
    sketch_result: Any
    canvas_w_cm: float
    canvas_h_cm: float
    trim_cm: float
    target: str = ""
    user_margins: dict | None = None
    user_multihole_params: dict | None = None
    lshape_params: dict | None = None
    composite_params: dict | None = None


class LegacyRequestAdapter:
    """Translate the worker's legacy optional dictionaries to one request."""

    @staticmethod
    def from_worker(worker, best, sketch_result, canvas_w_cm, canvas_h_cm,
                    is_lshape, trim_cm) -> DesignBuildRequest:
        if worker._composite_params is not None:
            mode = "composite"
        elif is_lshape:
            mode = "lshape"
        else:
            mode = "pool"
        return DesignBuildRequest(
            mode=mode,
            best=best,
            sketch_result=sketch_result,
            canvas_w_cm=float(canvas_w_cm),
            canvas_h_cm=float(canvas_h_cm),
            trim_cm=float(trim_cm),
            target=str(worker._target or ""),
            user_margins=worker._user_margins,
            user_multihole_params=worker._user_multihole,
            lshape_params=worker._lshape_params,
            composite_params=worker._composite_params,
        )


class _WorkerBuilder:
    """Compatibility base: construction callbacks remain worker-owned for now."""

    mode = ""

    def build(self, request: DesignBuildRequest, callbacks: dict[str, Callable]):
        raise NotImplementedError


class PoolDesignBuilder(_WorkerBuilder):
    mode = "pool"

    def build(self, request, callbacks):
        return callbacks["pool"](
            request.best, request.sketch_result, request.canvas_w_cm,
            request.canvas_h_cm, False, request.trim_cm)


class LShapeDesignBuilder(_WorkerBuilder):
    mode = "lshape"

    def build(self, request, callbacks):
        design = callbacks["new_design"](request.canvas_w_cm, request.canvas_h_cm,
                                          request.trim_cm)
        callbacks["lshape"](design, request.best, request.canvas_w_cm,
                             request.canvas_h_cm, request.trim_cm)
        return design


class CompositeDesignBuilder(_WorkerBuilder):
    mode = "composite"

    def build(self, request, callbacks):
        return callbacks["composite"](request)


BUILDERS = {
    PoolDesignBuilder.mode: PoolDesignBuilder(),
    LShapeDesignBuilder.mode: LShapeDesignBuilder(),
    CompositeDesignBuilder.mode: CompositeDesignBuilder(),
}

