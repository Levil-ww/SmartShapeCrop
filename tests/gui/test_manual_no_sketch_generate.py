"""无草图时，面板输入仍能进入现有生成链路。"""
from types import SimpleNamespace

from workers.property_panel_workers import PoolRenderWorker


def test_pool_manual_margins_reach_design(property_panel, tmp_path, monkeypatch):
    panel = property_panel
    panel._pool_tpl_dir.lineEdit().setText(str(tmp_path))
    for spin, value in zip((panel._sp_mt, panel._sp_mb, panel._sp_ml, panel._sp_mr),
                           (5, 6, 7, 8)):
        spin.setValue(value)
    monkeypatch.setattr(PoolRenderWorker, 'start', lambda self: None)
    panel._pool_start_generate_worker('pool', '花型-100x60CM')
    worker = panel._pool_worker
    is_lshape, result, w, h = worker._step_resolve_sketch(100, 60)
    design = worker._build_design(SimpleNamespace(path='material.png'), result, w, h, is_lshape)
    assert (design.inner_margin_top_cm, design.inner_margin_bottom_cm,
            design.inner_margin_left_cm, design.inner_margin_right_cm) == (5, 6, 7, 8)


def test_lshape_manual_parameters_without_sketch(property_panel, lshape_panel, tmp_path, monkeypatch):
    panel = property_panel
    panel.set_lshape_panel(lshape_panel)
    lshape_panel.set_outer_dims(100, 60)
    lshape_panel.set_lshape_params('tr', 20, 10)
    lshape_panel._target_edit.setText('花型-100x60CM')
    monkeypatch.setattr(panel, '_pool_trigger_warmup', lambda *args: None)
    panel._pool_tpl_dir.lineEdit().setText(str(tmp_path))
    monkeypatch.setattr(PoolRenderWorker, 'start', lambda self: None)
    panel._lshape_run_generate()
    assert panel._pool_worker._sketch == ''
    assert panel._pool_worker._lshape_params['cut_w_cm'] == 20


def test_composite_manual_parameters_without_sketch(property_panel):
    from gui.composite_panel import CompositePanel
    composite_panel = CompositePanel()
    panel = property_panel
    panel.set_composite_panel(composite_panel)
    composite_panel.set_outer_dims(185, 88)
    composite_panel.set_lshape_params('tr', 35, 10)
    panel._pool_tpl_dir.lineEdit().setText('')
    panel._composite_run_generate()
    assert panel.design.mode == 'rect_lshape_hole'
    assert panel.design.l_cut_w_cm == 35
