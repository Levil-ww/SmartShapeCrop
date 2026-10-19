"""水池单洞/多洞选择必须约束解析和再次生成。"""
from types import SimpleNamespace

import pytest

from services.sketch_parser.sketch_parser import SketchParseResult
from workers.property_panel_workers import PoolRenderWorker, _SketchParseWorker


def test_single_mode_does_not_submit_retained_multihole_parameters(property_panel):
    panel = property_panel
    panel._pool_shape_mode.setCurrentIndex(panel._pool_shape_mode.findData('multi'))
    assert panel._detect_multihole_edits()['active_count'] >= 2
    panel._pool_shape_mode.setCurrentIndex(panel._pool_shape_mode.findData('single'))
    assert panel._mh_active_count >= 2  # 切回多洞时仍可恢复参数
    assert panel._detect_multihole_edits() is None


@pytest.mark.parametrize('mode,old_multi', [('single', True), ('multi', False)])
def test_generate_reparses_result_in_conflicting_mode(tmp_path, monkeypatch, mode, old_multi):
    path = tmp_path / 'sketch.png'
    path.write_bytes(b'fixture')
    old = SketchParseResult(success=True, is_multi_hole=old_multi)
    expected = SketchParseResult(success=True, is_multi_hole=(mode == 'multi'))
    calls = []

    def parse(path, **kwargs):
        calls.append(kwargs)
        return expected

    monkeypatch.setattr('services.sketch_parser.parse_sketch', parse)
    worker = PoolRenderWorker(None, '', '', str(path), pre_parsed_result=old,
                              pool_shape_mode=mode)
    _, result, _, _ = worker._step_resolve_sketch(100, 60)
    assert result is expected
    assert calls[0]['hole_mode'] == mode
    assert old.is_multi_hole == old_multi


def test_late_multi_result_cannot_override_single_selection(property_panel):
    panel = property_panel
    panel._sketch_parse_worker = None
    result = SketchParseResult(success=True, is_multi_hole=True,
                               holes=[SimpleNamespace(), SimpleNamespace()])
    panel._on_sketch_parsed(result)
    assert panel._pool_shape_mode.currentData() == 'single'
    assert panel.design.pool_is_multi_hole is False
    assert panel._sketch_parse_result is not result


@pytest.mark.parametrize('mode', ['single', 'multi'])
def test_generate_button_passes_selected_mode(property_panel, tmp_path, monkeypatch, mode):
    panel = property_panel
    monkeypatch.setattr(PoolRenderWorker, 'start', lambda self: None)
    panel._pool_tpl_dir.lineEdit().setText(str(tmp_path))
    panel._pool_shape_mode.setCurrentIndex(panel._pool_shape_mode.findData(mode))
    panel._pool_start_generate_worker('pool', '水池-101x61CM')
    worker = panel._pool_worker
    assert worker._pool_shape_mode == mode
    assert (worker._user_multihole is not None) == (mode == 'multi')


@pytest.mark.parametrize('mode', ['single', 'multi'])
def test_upload_passes_selected_mode(property_panel, tmp_path, monkeypatch, mode):
    panel = property_panel
    path = tmp_path / 'sketch.png'
    path.write_bytes(b'fixture')
    monkeypatch.setattr(_SketchParseWorker, 'start', lambda self: None)
    panel._sketch_path = str(path)
    panel._pool_raw_outer_w, panel._pool_raw_outer_h = 100, 60
    panel._pool_shape_mode.setCurrentIndex(panel._pool_shape_mode.findData(mode))
    panel._pool_auto_parse_sketch()
    calls = []
    monkeypatch.setattr('services.sketch_parser.parse_sketch',
                        lambda path, **kw: calls.append(kw) or SketchParseResult())
    panel._sketch_parse_worker.run()
    assert calls[0]['hole_mode'] == mode


def test_manual_multi_preview_survives_failed_recognition(property_panel, tmp_path, monkeypatch):
    panel = property_panel
    panel._pool_shape_mode.setCurrentIndex(panel._pool_shape_mode.findData('multi'))
    path = tmp_path / 'sketch.png'
    path.write_bytes(b'fixture')
    monkeypatch.setattr('services.sketch_parser.parse_sketch',
                        lambda *a, **kw: SketchParseResult(message='未检测到两个洞'))
    worker = PoolRenderWorker(None, '', '水池-101x61CM', str(path),
                              pool_shape_mode='multi',
                              user_multihole_params=panel._detect_multihole_edits())
    _, result, w, h = worker._step_resolve_sketch(100, 60)
    design = worker._build_design(SimpleNamespace(path='material.png'), result, w, h, False)
    assert design.pool_is_multi_hole
    assert len(design.pool_holes_cm) == 2
