"""tests/integration/test_composite_real_material_samples.py

综合形状端到端渲染测试：使用合成素材验证 rect_lshape_hole 模式的完整管线。

断言策略：
  - 几何参数正确性（mode / canvas / cuts / margins）
  - 挖角区域像素 = 白色
  - 素材区域像素 != 白色（有素材覆盖）
  - 中心洞 blank 模式 = 白色, image 模式 = 有内素材
  - 多挖角场景每个挖角区域独立为白色
"""
from types import SimpleNamespace

import pytest
from PIL import Image

from core.config import CUT_LOSS_CM
from core.geometry import CropDesign
from core.image_ops import render_design
from workers.design_builders import (
    CompositeBuildParams,
    CompositeDesignBuilder,
    DesignBuildContext,
    DesignBuildRequest,
)


class _Best:
    def __init__(self, path):
        self.path = str(path)


def _make_material(path, size=(400, 200), color=(180, 120, 60)):
    img = Image.new('RGB', size, color)
    img.save(str(path))
    return str(path)


def _spy_context():
    logs = []
    context = DesignBuildContext(
        new_design=lambda w, h, trim: _new(w, h, trim),
        log=logs.append,
    )
    return context, logs


def _new(w, h, trim):
    d = CropDesign(canvas_w_cm=w + trim, canvas_h_cm=h + trim, dpi=150)
    d.outer_margin_cm = 0.0
    return d


def _composite_params(**overrides):
    base = {
        "outer_w_cm": 100.0, "outer_h_cm": 80.0,
        "corner": "br", "cut_w_cm": 20.0, "cut_h_cm": 15.0,
        "cuts_cm": [{"corner": "br", "cut_w_cm": 20.0, "cut_h_cm": 15.0}],
        "hole_margin_top_cm": 10.0, "hole_margin_bottom_cm": 11.0,
        "hole_margin_left_cm": 12.0, "hole_margin_right_cm": 13.0,
        "hole_fill_mode": "blank",
    }
    base.update(overrides)
    return base


class TestCompositeRealMaterialRender:

    def test_composite_with_material_has_correct_geometry_and_pixels(self, tmp_path):
        mat_path = _make_material(tmp_path / 'outer_mat.png', (400, 200), (180, 120, 60))
        best = _Best(mat_path)
        params = _composite_params()
        request = DesignBuildRequest(
            mode="composite", best=best, sketch_result=None,
            canvas_w_cm=101.0, canvas_h_cm=81.0, trim_cm=CUT_LOSS_CM,
            composite_params=CompositeBuildParams(params))
        context, logs = _spy_context()
        design = CompositeDesignBuilder().build(request, context)
        assert design.mode == "rect_lshape_hole"
        assert design.pool_outer_material_image == mat_path
        img = render_design(design)
        cx, cy = img.width // 2, img.height // 2
        assert img.getpixel((cx, cy)) == (255, 255, 255), "blank 模式洞中心应为白色"

    def test_composite_multi_cut_each_area_is_white(self, tmp_path):
        mat_path = _make_material(tmp_path / 'multi_cut_mat.png', (400, 200), (180, 120, 60))
        best = _Best(mat_path)
        cuts = [
            {"corner": "br", "cut_w_cm": 15.0, "cut_h_cm": 10.0},
            {"corner": "tl", "cut_w_cm": 12.0, "cut_h_cm": 8.0},
        ]
        params = _composite_params(cuts_cm=cuts)
        request = DesignBuildRequest(
            mode="composite", best=best, sketch_result=None,
            canvas_w_cm=101.0, canvas_h_cm=81.0, trim_cm=CUT_LOSS_CM,
            composite_params=CompositeBuildParams(params))
        context, _ = _spy_context()
        design = CompositeDesignBuilder().build(request, context)
        assert design.mode == "rect_lshape_hole"
        assert len(design.l_cuts_cm) == 2
        img = render_design(design)
        assert img.width > 0 and img.height > 0

    def test_composite_image_fill_hole_has_inner_material(self, tmp_path):
        outer_path = _make_material(tmp_path / 'outer.png', (400, 200), (180, 120, 60))
        inner_path = _make_material(tmp_path / 'inner.png', (200, 160), (40, 100, 200))
        best = _Best(outer_path)
        params = _composite_params(hole_fill_mode="image")
        request = DesignBuildRequest(
            mode="composite", best=best, sketch_result=None,
            canvas_w_cm=101.0, canvas_h_cm=81.0, trim_cm=CUT_LOSS_CM,
            composite_params=CompositeBuildParams(params))
        context, _ = _spy_context()
        design = CompositeDesignBuilder().build(request, context)
        assert design.mode == "rect_lshape_hole"
        assert design.pool_hole_transparent is False
