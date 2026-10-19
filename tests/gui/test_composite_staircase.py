"""综合阶梯 L 形：控件、模型、Worker 与实际渲染的完整参数链。"""
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from core.geometry import CropDesign
from core.image_ops import render_design
from gui.composite_panel import CompositePanel
from models.design_model import DesignModel
from services.sketch_parser.composite_sketch_parser import CompositeSketchParseResult
from workers.design_builders import (
    CompositeBuildParams, DesignBuildRequest, apply_composite_geometry,
)


def _rects(corner='tr', levels=2):
    rects = [
        dict(anchor=corner, offset_x_cm=0, offset_y_cm=0, w_cm=30, h_cm=10),
        dict(anchor=corner, offset_x_cm=0, offset_y_cm=10, w_cm=20, h_cm=10),
    ]
    if levels == 3:
        rects.append(dict(anchor=corner, offset_x_cm=0, offset_y_cm=20,
                          w_cm=10, h_cm=10))
    return rects


@pytest.fixture
def composite_panel(qapp):
    panel = CompositePanel()
    panel.set_outer_dims(185, 88)
    yield panel
    panel.shutdown()
    panel.close()
    panel.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
@pytest.mark.parametrize('levels', [2, 3])
@pytest.mark.parametrize('fill', ['blank', 'image'])
def test_staircase_panel_snapshot_worker_and_render(composite_panel, tmp_path, corner, levels, fill):
    panel = composite_panel
    panel.set_cut_rects(_rects(corner, levels))
    panel._hole_material.setChecked(fill == 'image')
    params = panel.get_composite_params()
    assert params['cuts_cm'] == []
    assert params['cut_rects'] == _rects(corner, levels)

    # 独立面板快照与 Worker 都应接受阶梯；不能以 cuts_cm 为空拒绝。
    direct = panel.to_crop_design(dpi=2.54)
    direct.validate()
    assert [asdict(r) for r in direct.l_cut_rects] == _rects(corner, levels)
    assert direct.pool_hole_transparent == (fill == 'blank')
    material = tmp_path / 'outer.png'
    src = Image.new('RGB', (186, 89), (180, 140, 100))
    ImageDraw.Draw(src).rectangle((0, 0, 185, 88), outline='black', width=2)
    src.save(material)
    inner = tmp_path / 'inner.png'
    Image.new('RGB', (40, 30), 'blue').save(inner)
    request = DesignBuildRequest(
        mode='composite', best=SimpleNamespace(path=str(material)),
        sketch_result=None, canvas_w_cm=185, canvas_h_cm=88, trim_cm=1,
        composite_params=CompositeBuildParams(params),
    )
    design = apply_composite_geometry(request)
    design.dpi = 2.54
    design.hole_bg_image = str(inner)
    assert [asdict(r) for r in design.l_cut_rects] == _rects(corner, levels)
    assert design.l_cuts_cm == []
    assert design.pool_hole_transparent == (fill == 'blank')
    output = render_design(design)
    assert output.size == (186, 89)
    assert output.getpixel((80, 40)) == ((255, 255, 255) if fill == 'blank' else (0, 0, 255))
    # 从锚定角向内量的探针：第一级挖空，第二级侧边保留阶梯台阶。
    x = lambda distance: distance if corner in ('tl', 'bl') else 185 - distance
    y = lambda distance: distance if corner in ('tl', 'tr') else 88 - distance
    assert output.getpixel((x(15), y(5))) == (255, 255, 255)
    assert output.getpixel((x(27), y(16))) == (180, 140, 100)
    # 同素材、同条带的切边必须与独立阶梯 L 形处理一致。
    standalone = CropDesign(
        mode='rect_lshape', canvas_w_cm=186, canvas_h_cm=89, dpi=2.54,
        l_corner=corner, l_cut_w_cm=30, l_cut_h_cm=levels * 10,
        l_cut_rects=design.l_cut_rects, pool_outer_material_image=str(material),
        pool_hole_transparent=True, inner_margin_top_cm=0, inner_margin_bottom_cm=0,
        inner_margin_left_cm=0, inner_margin_right_cm=0,
    )
    box = (0, 0, 40, 89) if corner in ('tl', 'bl') else (146, 0, 186, 89)
    assert output.crop(box).tobytes() == render_design(standalone).crop(box).tobytes()


@pytest.mark.parametrize('fill', ['blank', 'image'])
def test_staircase_generate_reaches_shared_canvas(main_window, gui_helpers, fill):
    panel = main_window.composite_panel
    main_window.panel.design.dpi = 2.54
    panel.set_outer_dims(185, 88)
    panel.set_cut_rects(_rects())
    panel._hole_material.setChecked(fill == 'image')
    gui_helpers.assert_button_exists(panel, '🔍 生成预览').click()
    design = main_window.canvas._design
    assert design is not None and design.mode == 'rect_lshape_hole'
    assert [asdict(r) for r in design.l_cut_rects] == _rects()
    assert design.pool_hole_transparent == (fill == 'blank')
    assert main_window.canvas.full_image().size == (186, 89)


def test_recognition_switches_between_staircase_and_standard(composite_panel):
    panel = composite_panel
    result = CompositeSketchParseResult(
        success=True, outer_w_cm=186, outer_h_cm=89, hole_w_cm=81, hole_h_cm=61,
        margin_left_cm=45, margin_right_cm=60, margin_top_cm=10, margin_bottom_cm=18,
    )
    # 委派 L 形识别的阶梯条带位于 debug，不能当标准同角挖角回填。
    result.cuts_cm = _rects()
    result.debug = {'lshape': {'pattern': 'single_edge_stepped', 'cuts_cm': _rects()}}
    panel._on_composite_parsed(result)
    assert panel._staircase_mode
    assert panel.get_cut_rects_cm() == _rects()
    assert '2' in panel._composite_status.text()
    result.cuts_cm = [dict(corner='bl', cut_w_cm=12, cut_h_cm=8)]
    result.debug = {'lshape': {'pattern': 'multi_edge'}}
    panel._on_composite_parsed(result)
    assert not panel._staircase_mode
    design = panel.to_crop_design(dpi=2.54)
    design.validate()
    assert design.l_cut_rects == []
    assert design.l_cuts_cm == [dict(corner='bl', cut_w_cm=12, cut_h_cm=8)]


def test_composite_model_clears_old_geometry_when_switching(composite_panel):
    panel = composite_panel
    panel.set_cut_rects(_rects())
    model = DesignModel()
    params = panel.get_composite_params()
    params.update(canvas_w_cm=186, canvas_h_cm=89)
    model.apply_composite_params(params)
    assert len(model.to_design().l_cut_rects) == 2
    panel._set_staircase_mode(False)
    panel.set_lshape_cuts([dict(corner='tr', cut_w_cm=12, cut_h_cm=8)])
    params = panel.get_composite_params()
    params.update(canvas_w_cm=186, canvas_h_cm=89)
    model.apply_composite_params(params)
    assert model.to_design().l_cut_rects == []
    assert model.to_design().l_cuts_cm == [dict(corner='tr', cut_w_cm=12, cut_h_cm=8)]


@pytest.mark.parametrize('rects', [
    [dict(anchor='tr', w_cm=190, h_cm=10)],
    [dict(anchor='tr', w_cm=30, h_cm=10, offset_y_cm=-1)],
    [dict(anchor='tr', w_cm=30, h_cm=10), dict(anchor='tr', w_cm=20, h_cm=10)],
])
def test_composite_rejects_invalid_staircase_geometry(rects):
    model = DesignModel(CropDesign(canvas_w_cm=186, canvas_h_cm=89))
    model.apply_composite_params(dict(corner='tr', cut_w_cm=30, cut_h_cm=20,
                                     cuts_cm=[], cut_rects=rects))
    with pytest.raises(ValueError):
        model.to_design().validate()


@pytest.mark.parametrize('fill', ['blank', 'image'])
def test_standard_multicorner_pool_render_is_preserved(composite_panel, tmp_path, fill):
    panel = composite_panel
    cuts = [dict(corner='tr', cut_w_cm=30, cut_h_cm=10),
            dict(corner='bl', cut_w_cm=20, cut_h_cm=10)]
    panel.set_lshape_cuts(cuts)
    panel._hole_material.setChecked(fill == 'image')
    material = tmp_path / 'outer.png'
    Image.new('RGB', (186, 89), 'red').save(material)
    inner = tmp_path / 'inner.png'
    Image.new('RGB', (40, 30), 'blue').save(inner)
    design = panel.to_crop_design(dpi=2.54)
    design.validate()
    assert design.l_cuts_cm == cuts
    assert design.l_cut_rects == []
    design.pool_outer_material_image = str(material)
    design.hole_bg_image = str(inner)
    output = render_design(design)
    assert output.getpixel((175, 5)) == (255, 255, 255)
    assert output.getpixel((5, 84)) == (255, 255, 255)
    assert output.getpixel((150, 40)) == (255, 0, 0)
    assert output.getpixel((80, 40)) == ((255, 255, 255) if fill == 'blank' else (0, 0, 255))


def test_real_staircase_sketch_recognition_and_echo(composite_panel, tmp_path, monkeypatch):
    from services.sketch_parser import composite_sketch_parser as csp
    from services.sketch_parser import lshape_sketch_parser as lsp

    # 只隔离外部 OCR，保留真实轮廓提取、阶梯分类与条带转换。
    monkeypatch.setattr(csp, '_safe_import_tesseract', lambda: None)
    monkeypatch.setattr(lsp, '_safe_import_tesseract', lambda: None)
    monkeypatch.setattr(lsp, '_multi_scale_ocr_scan', lambda *a, **k: [])
    sketch = Image.new('RGB', (925, 440), 'white')
    draw = ImageDraw.Draw(sketch)
    draw.polygon([(0, 0), (774, 0), (774, 49), (824, 49),
                  (824, 99), (924, 99), (924, 439), (0, 439)], fill='black')
    draw.rectangle((225, 50, 624, 349), fill='white')
    path = tmp_path / 'staircase_pool.png'
    sketch.save(path)
    result = csp.parse_composite_sketch(str(path), target_outer_w_cm=185,
                                       target_outer_h_cm=88)
    assert result.success, result.message
    assert result.debug['lshape']['pattern'] == 'single_edge_stepped'
    composite_panel._on_composite_parsed(result)
    rects = composite_panel.get_cut_rects_cm()
    assert len(rects) == 2
    assert [r['anchor'] for r in rects] == ['tr', 'tr']
    assert rects[0]['w_cm'] == pytest.approx(30, abs=1)
    assert rects[1]['w_cm'] == pytest.approx(20, abs=1)
    assert rects[1]['offset_y_cm'] == pytest.approx(10, abs=1)
    assert composite_panel._hole_w.value() == pytest.approx(81, abs=1)
    assert composite_panel._hole_h.value() == pytest.approx(61, abs=1)
    composite_panel.to_crop_design(dpi=2.54).validate()
