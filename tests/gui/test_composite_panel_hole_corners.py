"""真实综合面板生成、填充切换和共享 UI 快照中的圆角隔离。"""
from types import SimpleNamespace
from PIL import Image

from gui.composite_panel import CompositePanel
from workers.design_builders import (
    CompositeBuildParams, DesignBuildRequest, apply_composite_geometry,
)


def _setup(panel):
    panel.set_outer_dims(100, 80)
    enabled, combo, width, height = panel._corner_rows[0]
    enabled.setChecked(True)
    combo.setCurrentIndex(combo.findData('tr'))
    width.setValue(5)
    height.setValue(5)
    for spin in (panel._hole_mt, panel._hole_mb, panel._hole_ml, panel._hole_mr):
        spin.setValue(10)


def test_controls_default_to_zero_in_center_group(qapp):
    panel = CompositePanel()
    try:
        assert panel._gb_hole_corners.title() == '中心水池圆角（厘米）'
        assert panel._gb_hole_corners.parent() is panel._gb_composite.parent()
        assert {key: spin.value() for key, spin in panel._hole_corners.items()} == dict(
            tl=0, tr=0, bl=0, br=0)
    finally:
        panel.shutdown()
        panel.close()


def test_generate_fill_switch_and_collect_keep_independent_corners(main_window, tmp_path, monkeypatch):
    cp = main_window.composite_panel
    panel = main_window.panel
    _setup(cp)
    panel.design.dpi = 2.54
    radii = dict(tl=1, tr=2, bl=3, br=4)
    # 验证参数链路时使用小画布，避免共享 DPI 控件回写 150 后启动大图预览。
    collect_snapshot = panel._collect_ui_snapshot
    def small_snapshot():
        snapshot = collect_snapshot()
        snapshot['dpi'] = 2.54
        return snapshot
    monkeypatch.setattr(panel, '_collect_ui_snapshot', small_snapshot)
    for key, value in radii.items():
        cp._hole_corners[key].setValue(value)
        panel._sp_design_corners[key].setValue(9)
    monkeypatch.setattr(panel, '_pool_finish_tail', lambda *args: None)
    for image_fill in (False, True, False, True):
        cp._hole_material.setChecked(image_fill)
        panel._composite_run_generate()
        panel._collect()
        assert panel.design.pool_hole_transparent is (not image_fill)
        assert {key: getattr(panel.design, f'hole_corner_{key}_cm') for key in radii} == radii
        assert {key: spin.value() for key, spin in cp._hole_corners.items()} == radii
        if image_fill:
            for name in ('first.png', 'second.png'):
                path = tmp_path / name
                Image.new('RGB', (20, 20), 'red').save(path)
                panel._on_inner_match_done({'single': {'path': str(path)}})
                assert panel.design.pool_inner_material_image == str(path)
                assert {key: getattr(panel.design, f'hole_corner_{key}_cm') for key in radii} == radii
    assert {key: spin.value() for key, spin in panel._sp_design_corners.items()} == dict.fromkeys(radii, 9)


def test_panel_snapshot_and_worker_builder_transfer_corners(qapp):
    panel = CompositePanel()
    try:
        _setup(panel)
        for key, value in dict(tl=1, tr=2, bl=3, br=4).items():
            panel._hole_corners[key].setValue(value)
        params = panel.get_composite_params()
        worker_design = apply_composite_geometry(DesignBuildRequest(
            mode='composite', best=SimpleNamespace(path='material.png'),
            sketch_result=None, canvas_w_cm=100, canvas_h_cm=80, trim_cm=1,
            composite_params=CompositeBuildParams(params),
        ))
        direct_design = panel.to_crop_design()
        for key, value in dict(tl=1, tr=2, bl=3, br=4).items():
            assert getattr(worker_design, f'hole_corner_{key}_cm') == value
            assert getattr(direct_design, f'hole_corner_{key}_cm') == value
            assert getattr(worker_design, f'corner_{key}_cm') == 0
    finally:
        panel.shutdown()
        panel.close()
