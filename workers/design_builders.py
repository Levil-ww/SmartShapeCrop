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


from core.design_build_geometry import (
    apply_lshape_geometry, build_multihole_geometry,
    apply_pool_geometry as _apply_pool_geometry,
)


def apply_pool_geometry(*args, **kwargs):
    """Compatibility wrapper retaining the worker patch/import seam."""
    kwargs['multihole_builder'] = build_multihole_geometry
    return _apply_pool_geometry(*args, **kwargs)

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
