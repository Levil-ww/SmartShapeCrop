"""水池四角控件不得改变 L 形/综合形状渲染，返回水池后仍保留输入。"""
import numpy as np
import pytest

from core.geometry import CropDesign, COMPOSITE_MODE
from core.image_ops import render_design
from models.design_model import DesignModel


@pytest.mark.parametrize("mode", ["rect_lshape", COMPOSITE_MODE])
def test_pool_corners_do_not_change_other_panel_render(main_window, mode):
    panel = main_window.panel
    panel.design = CropDesign(
        canvas_w_cm=24, canvas_h_cm=18, dpi=50, mode=mode,
        inner_margin_top_cm=4, inner_margin_bottom_cm=4,
        inner_margin_left_cm=4, inner_margin_right_cm=4,
        l_corner="tl", l_cut_w_cm=8, l_cut_h_cm=6,
        hole_corner_tl_cm=1, hole_corner_tr_cm=2,
    )
    panel.sync_from_design(panel.design)
    panel._collect()
    baseline = np.array(render_design(panel.design))
    hole_corners = (panel.design.hole_corner_tl_cm, panel.design.hole_corner_tr_cm)
    radii = dict(tl=1, tr=2, bl=3, br=4)
    for key, value in radii.items():
        panel._sp_design_corners[key].setValue(value)
    panel._collect()
    assert np.array_equal(np.array(render_design(panel.design)), baseline)
    assert tuple(getattr(panel.design, f"corner_{key}_cm") for key in radii) == (0, 0, 0, 0)
    assert (panel.design.hole_corner_tl_cm, panel.design.hole_corner_tr_cm) == hole_corners
    # 重新回填另一个面板的设计也不清空水池输入。
    panel.sync_from_design(panel.design)
    assert {key: spin.value() for key, spin in panel._sp_design_corners.items()} == radii
    panel._cb_mode.setCurrentIndex(panel._cb_mode.findData("rect_hole"))
    panel._collect()
    assert {key: getattr(panel.design, f"corner_{key}_cm") for key in radii} == radii


def test_composite_creation_does_not_inherit_pool_outer_corners():
    pool = CropDesign(corner_tl_cm=1, corner_tr_cm=2, corner_bl_cm=3, corner_br_cm=4)
    model = DesignModel(pool)
    model.apply_composite_params(dict(canvas_w_cm=24, canvas_h_cm=18,
                                     hole_corner_tl_cm=2))
    composite = model.design
    assert tuple(getattr(composite, f"corner_{key}_cm")
                 for key in ("tl", "tr", "bl", "br")) == (0, 0, 0, 0)
    assert composite.hole_corner_tl_cm == 2
    assert pool.corner_tl_cm == 1
