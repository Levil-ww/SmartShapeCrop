"""Design construction boundaries used by :mod:`property_panel_workers`.

The first extraction deliberately keeps the existing ``CropDesign`` schema and
the worker's public API intact.  These small objects provide a stable seam for
moving the mode-specific construction code out of the worker incrementally.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


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

