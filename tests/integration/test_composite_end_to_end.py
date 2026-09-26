from core.app_settings import AppSettings
from core.image_ops import render_design
from gui.composite_panel import CompositePanel
from models.design_model import DesignModel
from PyQt5.QtWidgets import QApplication


def test_composite_parameters_flow_to_render():
    app = QApplication.instance() or QApplication([])
    panel = CompositePanel()
    panel.setParent(None)
    panel._hole_w.setValue(81)
    panel._hole_h.setValue(61)
    panel._hole_mt.setValue(10)
    panel._hole_mb.setValue(18)
    panel._hole_ml.setValue(45)
    panel._hole_mr.setValue(60)
    params = panel.get_composite_params()
    params.update({'canvas_w_cm': 186, 'canvas_h_cm': 89})

    model = DesignModel()
    model.apply_composite_params(params)
    design = model.to_design()
    design.dpi = 2.54
    output = render_design(design)

    assert design.mode == 'rect_lshape_hole'
    assert output.size == (186, 89)


def test_composite_history_source_isolated():
    settings = AppSettings()
    source = settings.TARGET_SRC_COMPOSITE
    assert source in settings.TARGET_SRC_LABEL
    assert settings._target_name_key(source) != settings._target_name_key(settings.TARGET_SRC_LSHAPE)
