"""End-to-end validation chain contract tests.

Tests the complete validation flow from DesignBuildRequest through
Builders to CropDesign.validate(), ensuring no constraints are lost
in the intermediate layers.
"""
import pytest
from unittest.mock import Mock
from core.geometry import CropDesign
from workers.design_builders import (
    DesignBuildRequest,
    DesignBuildContext,
    PoolBuildParams,
    LShapeBuildParams,
    CompositeBuildParams,
    PoolDesignBuilder,
    LShapeDesignBuilder,
    CompositeDesignBuilder,
    DesignBuildValidationError,
)


class TestRequestValidationChain:
    """Test DesignBuildRequest.validate() constraints."""

    def _make_request(self, **kwargs):
        defaults = {
            "mode": "pool",
            "best": Mock(path="/test.jpg"),
            "sketch_result": None,
            "canvas_w_cm": 100.0,
            "canvas_h_cm": 80.0,
            "trim_cm": 1.0,
        }
        defaults.update(kwargs)
        return DesignBuildRequest(**defaults)

    def test_valid_pool_mode_passes(self):
        request = self._make_request(mode="pool")
        request.validate()

    def test_valid_lshape_mode_passes(self):
        request = self._make_request(
            mode="lshape",
            lshape_params=LShapeBuildParams({"corner": "br"}))
        request.validate()

    def test_valid_composite_mode_passes(self):
        request = self._make_request(
            mode="composite",
            composite_params=CompositeBuildParams({"outer_w_cm": 100.0}))
        request.validate()

    def test_unknown_mode_raises(self):
        request = self._make_request(mode="unknown")
        with pytest.raises(DesignBuildValidationError, match="未知构建模式"):
            request.validate()

    def test_negative_trim_raises(self):
        request = self._make_request(trim_cm=-1.0)
        with pytest.raises(DesignBuildValidationError, match="trim_cm 不能为负"):
            request.validate()

    def test_zero_trim_passes(self):
        request = self._make_request(trim_cm=0.0)
        request.validate()

    def test_lshape_without_params_raises(self):
        request = self._make_request(mode="lshape", lshape_params=None)
        with pytest.raises(DesignBuildValidationError, match="L 形模式需要"):
            request.validate()

    def test_composite_without_params_raises(self):
        request = self._make_request(mode="composite", composite_params=None)
        with pytest.raises(DesignBuildValidationError, match="综合形状模式需要"):
            request.validate()

    def test_pool_without_optional_params_passes(self):
        request = self._make_request(mode="pool")
        request.validate()


class TestPoolParamsValidationChain:
    """Test PoolBuildParams.validate() constraints."""

    def test_valid_params_pass(self):
        params = PoolBuildParams(
            target="圆角水池",
            user_margins={"top": 2.0},
            user_multihole_params={"active_count": 2})
        params.validate()

    def test_non_string_target_raises(self):
        params = PoolBuildParams(target=123)
        with pytest.raises(DesignBuildValidationError, match="target 必须是字符串"):
            params.validate()

    def test_non_dict_user_margins_raises(self):
        params = PoolBuildParams(user_margins="invalid")
        with pytest.raises(DesignBuildValidationError, match="user_margins 必须是 dict"):
            params.validate()

    def test_non_dict_user_multihole_raises(self):
        params = PoolBuildParams(user_multihole_params=[1, 2, 3])
        with pytest.raises(DesignBuildValidationError, match="user_multihole_params 必须是 dict"):
            params.validate()

    def test_none_values_pass(self):
        params = PoolBuildParams(
            target="",
            user_margins=None,
            user_multihole_params=None)
        params.validate()


class TestLShapeParamsValidationChain:
    """Test LShapeBuildParams.validate() constraints."""

    def test_valid_params_pass(self):
        params = LShapeBuildParams({
            "corner": "br",
            "cut_w_cm": 5.0,
            "cut_h_cm": 6.0
        })
        params.validate()

    def test_non_dict_values_raises(self):
        params = LShapeBuildParams("invalid")
        with pytest.raises(DesignBuildValidationError, match="values 必须是 dict"):
            params.validate()

    def test_invalid_corner_raises(self):
        params = LShapeBuildParams({"corner": "invalid"})
        with pytest.raises(DesignBuildValidationError, match="corner 必须是"):
            params.validate()

    def test_valid_corners_pass(self):
        for corner in ("tl", "tr", "bl", "br"):
            params = LShapeBuildParams({"corner": corner})
            params.validate()

    def test_negative_cut_w_raises(self):
        params = LShapeBuildParams({"cut_w_cm": -1.0})
        with pytest.raises(DesignBuildValidationError, match="cut_w_cm 不能为负"):
            params.validate()

    def test_negative_cut_h_raises(self):
        params = LShapeBuildParams({"cut_h_cm": -1.0})
        with pytest.raises(DesignBuildValidationError, match="cut_h_cm 不能为负"):
            params.validate()

    def test_zero_cut_passes(self):
        params = LShapeBuildParams({"cut_w_cm": 0.0, "cut_h_cm": 0.0})
        params.validate()


class TestCompositeParamsValidationChain:
    """Test CompositeBuildParams.validate() constraints."""

    def test_valid_params_pass(self):
        params = CompositeBuildParams({
            "outer_w_cm": 100.0,
            "outer_h_cm": 80.0,
            "cuts_cm": [{"corner": "br"}]
        })
        params.validate()

    def test_non_dict_values_raises(self):
        params = CompositeBuildParams("invalid")
        with pytest.raises(DesignBuildValidationError, match="values 必须是 dict"):
            params.validate()

    def test_zero_outer_w_raises(self):
        params = CompositeBuildParams({"outer_w_cm": 0.0})
        with pytest.raises(DesignBuildValidationError, match="outer_w_cm 必须为正数"):
            params.validate()

    def test_negative_outer_h_raises(self):
        params = CompositeBuildParams({"outer_h_cm": -1.0})
        with pytest.raises(DesignBuildValidationError, match="outer_h_cm 必须为正数"):
            params.validate()

    def test_non_list_cuts_raises(self):
        params = CompositeBuildParams({"cuts_cm": "invalid"})
        with pytest.raises(DesignBuildValidationError, match="cuts_cm 必须是 list"):
            params.validate()

    def test_non_dict_cut_item_raises(self):
        params = CompositeBuildParams({"cuts_cm": ["invalid"]})
        with pytest.raises(DesignBuildValidationError, match="cuts_cm\\[0\\] 必须是 dict"):
            params.validate()

    def test_invalid_cut_corner_raises(self):
        params = CompositeBuildParams({"cuts_cm": [{"corner": "invalid"}]})
        with pytest.raises(DesignBuildValidationError, match="corner 必须是"):
            params.validate()

    def test_empty_cuts_list_passes(self):
        params = CompositeBuildParams({"cuts_cm": []})
        params.validate()


class TestBuilderValidationIntegration:
    """Test that Builders call validate() on request and params."""

    def _context(self):
        def new_design(w, h, trim):
            d = CropDesign(canvas_w_cm=w + trim, canvas_h_cm=h + trim, dpi=150)
            d.outer_margin_cm = 0.0
            return d
        return DesignBuildContext(new_design=new_design, log=lambda msg: None)

    def test_pool_builder_calls_request_validate(self):
        builder = PoolDesignBuilder()
        request = DesignBuildRequest(
            mode="invalid",
            best=Mock(path="/test.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0)
        with pytest.raises(DesignBuildValidationError, match="未知构建模式"):
            builder.build(request, self._context())

    def test_pool_builder_calls_pool_params_validate(self):
        builder = PoolDesignBuilder()
        request = DesignBuildRequest(
            mode="pool",
            best=Mock(path="/test.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=PoolBuildParams(target=123))
        with pytest.raises(DesignBuildValidationError, match="target 必须是字符串"):
            builder.build(request, self._context())

    def test_lshape_builder_calls_lshape_params_validate(self):
        builder = LShapeDesignBuilder()
        request = DesignBuildRequest(
            mode="lshape",
            best=Mock(path="/test.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            lshape_params=LShapeBuildParams({"corner": "invalid"}))
        with pytest.raises(DesignBuildValidationError, match="corner 必须是"):
            builder.build(request, self._context())

    def test_composite_builder_calls_composite_params_validate(self):
        builder = CompositeDesignBuilder()
        request = DesignBuildRequest(
            mode="composite",
            best=Mock(path="/test.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            composite_params=CompositeBuildParams({"outer_w_cm": 0.0}))
        with pytest.raises(DesignBuildValidationError, match="outer_w_cm 必须为正数"):
            builder.build(request, self._context())


class TestCropDesignValidationIntegration:
    """Test that Builders produce designs that pass CropDesign.validate()."""

    def _context(self):
        def new_design(w, h, trim):
            d = CropDesign(canvas_w_cm=w + trim, canvas_h_cm=h + trim, dpi=150)
            d.outer_margin_cm = 0.0
            return d
        return DesignBuildContext(new_design=new_design, log=lambda msg: None)

    def test_pool_builder_produces_valid_design(self):
        builder = PoolDesignBuilder()
        request = DesignBuildRequest(
            mode="pool",
            best=Mock(path="/test.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0)
        design = builder.build(request, self._context())
        design.validate()

    def test_lshape_builder_produces_valid_design(self):
        builder = LShapeDesignBuilder()
        request = DesignBuildRequest(
            mode="lshape",
            best=Mock(path="/test.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            lshape_params=LShapeBuildParams({
                "corner": "br",
                "cut_w_cm": 5.0,
                "cut_h_cm": 6.0
            }))
        design = builder.build(request, self._context())
        design.validate()

    def test_composite_builder_produces_valid_design(self):
        builder = CompositeDesignBuilder()
        request = DesignBuildRequest(
            mode="composite",
            best=Mock(path="/test.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            composite_params=CompositeBuildParams({
                "outer_w_cm": 100.0,
                "outer_h_cm": 80.0,
                "cuts_cm": [{"corner": "br", "cut_w_cm": 5.0, "cut_h_cm": 6.0}]
            }))
        design = builder.build(request, self._context())
        design.validate()


class TestValidationChainCompleteness:
    """Test that validation catches errors at the right layer."""

    def _context(self):
        def new_design(w, h, trim):
            d = CropDesign(canvas_w_cm=w + trim, canvas_h_cm=h + trim, dpi=150)
            d.outer_margin_cm = 0.0
            return d
        return DesignBuildContext(new_design=new_design, log=lambda msg: None)

    def test_request_validation_before_builder_logic(self):
        builder = PoolDesignBuilder()
        request = DesignBuildRequest(
            mode="pool",
            best=Mock(path="/test.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=-1.0)
        with pytest.raises(DesignBuildValidationError, match="trim_cm 不能为负"):
            builder.build(request, self._context())

    def test_params_validation_before_geometry(self):
        builder = PoolDesignBuilder()
        request = DesignBuildRequest(
            mode="pool",
            best=Mock(path="/test.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0,
            pool_params=PoolBuildParams(target=123))
        with pytest.raises(DesignBuildValidationError, match="target 必须是字符串"):
            builder.build(request, self._context())

    def test_design_validation_after_geometry(self):
        builder = PoolDesignBuilder()
        request = DesignBuildRequest(
            mode="pool",
            best=Mock(path="/test.jpg"),
            sketch_result=None,
            canvas_w_cm=100.0,
            canvas_h_cm=80.0,
            trim_cm=1.0)
        design = builder.build(request, self._context())
        design.validate()
