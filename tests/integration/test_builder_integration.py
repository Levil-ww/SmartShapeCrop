"""Integration tests for Builder classes build() methods.

Tests the three Builder classes end-to-end:
- PoolDesignBuilder.build()
- LShapeDesignBuilder.build()
- CompositeDesignBuilder.build()

Verifies context传递, request validation, geometry function invocation,
and design output correctness.
"""
import pytest
from types import SimpleNamespace
from core.geometry import CropDesign
from workers.design_builders import (
    PoolDesignBuilder,
    LShapeDesignBuilder,
    CompositeDesignBuilder,
    DesignBuildRequest,
    DesignBuildContext,
    PoolBuildParams,
    LShapeBuildParams,
    CompositeBuildParams,
    DesignBuildError,
    DesignBuildValidationError,
)


class TestPoolDesignBuilder:
    """Test PoolDesignBuilder.build() end-to-end."""

    def test_build_returns_crop_design(self):
        best = SimpleNamespace(path="/path/to/material.jpg")
        request = DesignBuildRequest(
            mode="pool",
            best=best,
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=PoolBuildParams(
                target="矩形",
                user_margins=None,
                user_multihole_params=None,
            ),
            lshape_params=None,
            composite_params=None,
        )
        context = DesignBuildContext(
            new_design=lambda w, h, trim: CropDesign(canvas_w_cm=w+trim, canvas_h_cm=h+trim, dpi=150),
            log=lambda msg: None,
        )
        builder = PoolDesignBuilder()
        design = builder.build(request, context)
        assert isinstance(design, CropDesign)

    def test_build_sets_pool_mode(self):
        best = SimpleNamespace(path="/path/to/material.jpg")
        request = DesignBuildRequest(
            mode="pool",
            best=best,
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=PoolBuildParams(
                target="矩形",
                user_margins=None,
                user_multihole_params=None,
            ),
            lshape_params=None,
            composite_params=None,
        )
        context = DesignBuildContext(
            new_design=lambda w, h, trim: CropDesign(canvas_w_cm=w+trim, canvas_h_cm=h+trim, dpi=150),
            log=lambda msg: None,
        )
        builder = PoolDesignBuilder()
        design = builder.build(request, context)
        assert design.mode == 'rect_hole'

    def test_build_applies_pool_geometry(self):
        best = SimpleNamespace(path="/path/to/material.jpg")
        request = DesignBuildRequest(
            mode="pool",
            best=best,
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=PoolBuildParams(
                target="矩形",
                user_margins=None,
                user_multihole_params=None,
            ),
            lshape_params=None,
            composite_params=None,
        )
        context = DesignBuildContext(
            new_design=lambda w, h, trim: CropDesign(canvas_w_cm=w+trim, canvas_h_cm=h+trim, dpi=150),
            log=lambda msg: None,
        )
        builder = PoolDesignBuilder()
        design = builder.build(request, context)
        assert design.pool_outer_material_image == "/path/to/material.jpg"

    def test_build_without_pool_params_raises(self):
        best = SimpleNamespace(path="/path/to/material.jpg")
        request = DesignBuildRequest(
            mode="pool",
            best=best,
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=None,
            lshape_params=None,
            composite_params=None,
        )
        context = DesignBuildContext(
            new_design=lambda w, h, trim: CropDesign(canvas_w_cm=w+trim, canvas_h_cm=h+trim, dpi=150),
            log=lambda msg: None,
        )
        builder = PoolDesignBuilder()
        with pytest.raises(AttributeError):
            builder.build(request, context)

    def test_build_uses_context_new_design(self):
        best = SimpleNamespace(path="/path/to/material.jpg")
        request = DesignBuildRequest(
            mode="pool",
            best=best,
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=PoolBuildParams(
                target="矩形",
                user_margins=None,
                user_multihole_params=None,
            ),
            lshape_params=None,
            composite_params=None,
        )
        custom_design = CropDesign(canvas_w_cm=50, canvas_h_cm=50, dpi=150)
        context = DesignBuildContext(
            new_design=lambda w, h, trim: custom_design,
            log=lambda msg: None,
        )
        builder = PoolDesignBuilder()
        design = builder.build(request, context)
        assert design is custom_design


class TestLShapeDesignBuilder:
    """Test LShapeDesignBuilder.build() end-to-end."""

    def test_build_returns_crop_design(self):
        best = SimpleNamespace(path="/path/to/material.jpg")
        request = DesignBuildRequest(
            mode="lshape",
            best=best,
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=PoolBuildParams(),
            lshape_params=LShapeBuildParams({
                "corner": "tr",
                "cut_w_cm": 10.0,
                "cut_h_cm": 12.0,
            }),
            composite_params=None,
        )
        context = DesignBuildContext(
            new_design=lambda w, h, trim: CropDesign(canvas_w_cm=w+trim, canvas_h_cm=h+trim, dpi=150),
            log=lambda msg: None,
        )
        builder = LShapeDesignBuilder()
        design = builder.build(request, context)
        assert isinstance(design, CropDesign)

    def test_build_sets_lshape_mode(self):
        best = SimpleNamespace(path="/path/to/material.jpg")
        request = DesignBuildRequest(
            mode="lshape",
            best=best,
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=PoolBuildParams(),
            lshape_params=LShapeBuildParams({
                "corner": "tr",
                "cut_w_cm": 10.0,
                "cut_h_cm": 12.0,
            }),
            composite_params=None,
        )
        context = DesignBuildContext(
            new_design=lambda w, h, trim: CropDesign(canvas_w_cm=w+trim, canvas_h_cm=h+trim, dpi=150),
            log=lambda msg: None,
        )
        builder = LShapeDesignBuilder()
        design = builder.build(request, context)
        assert design.mode == 'rect_lshape'

    def test_build_applies_lshape_geometry(self):
        best = SimpleNamespace(path="/path/to/material.jpg")
        request = DesignBuildRequest(
            mode="lshape",
            best=best,
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=PoolBuildParams(),
            lshape_params=LShapeBuildParams({
                "corner": "br",
                "cut_w_cm": 15.0,
                "cut_h_cm": 18.0,
            }),
            composite_params=None,
        )
        context = DesignBuildContext(
            new_design=lambda w, h, trim: CropDesign(canvas_w_cm=w+trim, canvas_h_cm=h+trim, dpi=150),
            log=lambda msg: None,
        )
        builder = LShapeDesignBuilder()
        design = builder.build(request, context)
        assert design.l_corner == "br"
        assert design.l_cut_w_cm == 15.0
        assert design.l_cut_h_cm == 18.0

    def test_build_without_lshape_params_raises(self):
        best = SimpleNamespace(path="/path/to/material.jpg")
        request = DesignBuildRequest(
            mode="lshape",
            best=best,
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=PoolBuildParams(),
            lshape_params=None,
            composite_params=None,
        )
        context = DesignBuildContext(
            new_design=lambda w, h, trim: CropDesign(canvas_w_cm=w+trim, canvas_h_cm=h+trim, dpi=150),
            log=lambda msg: None,
        )
        builder = LShapeDesignBuilder()
        with pytest.raises(DesignBuildValidationError, match="L 形模式需要 lshape_params"):
            builder.build(request, context)

    def test_build_sets_material_images(self):
        best = SimpleNamespace(path="/path/to/material.jpg")
        request = DesignBuildRequest(
            mode="lshape",
            best=best,
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=PoolBuildParams(),
            lshape_params=LShapeBuildParams({
                "corner": "tr",
                "cut_w_cm": 10.0,
                "cut_h_cm": 12.0,
            }),
            composite_params=None,
        )
        context = DesignBuildContext(
            new_design=lambda w, h, trim: CropDesign(canvas_w_cm=w+trim, canvas_h_cm=h+trim, dpi=150),
            log=lambda msg: None,
        )
        builder = LShapeDesignBuilder()
        design = builder.build(request, context)
        assert design.pool_outer_material_image == "/path/to/material.jpg"
        assert design.outer_bg_image == "/path/to/material.jpg"
        assert design.pool_inner_material_image == "/path/to/material.jpg"


class TestCompositeDesignBuilder:
    """Test CompositeDesignBuilder.build() end-to-end."""

    def test_build_returns_crop_design(self):
        best = SimpleNamespace(path="/path/to/material.jpg")
        request = DesignBuildRequest(
            mode="composite",
            best=best,
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=PoolBuildParams(),
            lshape_params=None,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 100.0,
                "outer_h_cm": 80.0,
                "cuts_cm": [{"corner": "br", "cut_w_cm": 5.0, "cut_h_cm": 6.0}],
            }),
        )
        context = DesignBuildContext(
            new_design=lambda w, h, trim: CropDesign(canvas_w_cm=w+trim, canvas_h_cm=h+trim, dpi=150),
            log=lambda msg: None,
        )
        builder = CompositeDesignBuilder()
        design = builder.build(request, context)
        assert isinstance(design, CropDesign)

    def test_build_sets_composite_mode(self):
        best = SimpleNamespace(path="/path/to/material.jpg")
        request = DesignBuildRequest(
            mode="composite",
            best=best,
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=PoolBuildParams(),
            lshape_params=None,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 100.0,
                "outer_h_cm": 80.0,
                "cuts_cm": [{"corner": "br", "cut_w_cm": 5.0, "cut_h_cm": 6.0}],
            }),
        )
        context = DesignBuildContext(
            new_design=lambda w, h, trim: CropDesign(canvas_w_cm=w+trim, canvas_h_cm=h+trim, dpi=150),
            log=lambda msg: None,
        )
        builder = CompositeDesignBuilder()
        design = builder.build(request, context)
        assert design.mode == 'rect_lshape_hole'

    def test_build_applies_composite_geometry(self):
        best = SimpleNamespace(path="/path/to/material.jpg")
        request = DesignBuildRequest(
            mode="composite",
            best=best,
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=PoolBuildParams(),
            lshape_params=None,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 100.0,
                "outer_h_cm": 80.0,
                "cuts_cm": [
                    {"corner": "tl", "cut_w_cm": 5.0, "cut_h_cm": 6.0},
                    {"corner": "br", "cut_w_cm": 7.0, "cut_h_cm": 8.0},
                ],
            }),
        )
        context = DesignBuildContext(
            new_design=lambda w, h, trim: CropDesign(canvas_w_cm=w+trim, canvas_h_cm=h+trim, dpi=150),
            log=lambda msg: None,
        )
        builder = CompositeDesignBuilder()
        design = builder.build(request, context)
        assert len(design.l_cuts_cm) == 2
        assert design.l_cuts_cm[0]['corner'] == "tl"
        assert design.l_cuts_cm[1]['corner'] == "br"

    def test_build_without_composite_params_raises(self):
        best = SimpleNamespace(path="/path/to/material.jpg")
        request = DesignBuildRequest(
            mode="composite",
            best=best,
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=PoolBuildParams(),
            lshape_params=None,
            composite_params=None,
        )
        context = DesignBuildContext(
            new_design=lambda w, h, trim: CropDesign(canvas_w_cm=w+trim, canvas_h_cm=h+trim, dpi=150),
            log=lambda msg: None,
        )
        builder = CompositeDesignBuilder()
        with pytest.raises(DesignBuildValidationError, match="综合形状模式需要 composite_params"):
            builder.build(request, context)

    def test_build_sets_material_images(self):
        best = SimpleNamespace(path="/path/to/material.jpg")
        request = DesignBuildRequest(
            mode="composite",
            best=best,
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=PoolBuildParams(),
            lshape_params=None,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 100.0,
                "outer_h_cm": 80.0,
                "cuts_cm": [{"corner": "br", "cut_w_cm": 5.0, "cut_h_cm": 6.0}],
            }),
        )
        context = DesignBuildContext(
            new_design=lambda w, h, trim: CropDesign(canvas_w_cm=w+trim, canvas_h_cm=h+trim, dpi=150),
            log=lambda msg: None,
        )
        builder = CompositeDesignBuilder()
        design = builder.build(request, context)
        assert design.pool_outer_material_image == "/path/to/material.jpg"
        assert design.outer_bg_image == "/path/to/material.jpg"
