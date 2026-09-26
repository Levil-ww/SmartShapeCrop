from core.geometry import CropDesign
from models.design_model import DesignModel


def test_apply_composite_params_builds_composite_design():
    model = DesignModel(CropDesign(canvas_w_cm=185, canvas_h_cm=88))
    model.apply_composite_params({
        'canvas_w_cm': 186, 'canvas_h_cm': 89,
        'hole_margin_top_cm': 10, 'hole_margin_bottom_cm': 18,
        'hole_margin_left_cm': 45, 'hole_margin_right_cm': 60,
        'corner': 'tr', 'cut_w_cm': 35, 'cut_h_cm': 10,
        'hole_fill_mode': 'blank',
    })
    d = model.to_design()
    assert d.mode == 'rect_lshape_hole'
    assert d.canvas_w_cm == 186
    assert d.canvas_h_cm == 89
    assert d.inner_margin_left_cm == 45
    assert d.pool_hole_transparent is True
