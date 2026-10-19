"""综合多角阶梯：复用独立 L 形编辑和渲染，水池填充保持独立。"""
from copy import deepcopy
from dataclasses import asdict
from itertools import combinations
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from core.image_ops import render_design
from gui.composite_panel import CompositePanel
from workers.design_builders import (
    CompositeBuildParams, DesignBuildRequest, apply_composite_geometry,
)


CORNERS = ('tl', 'tr', 'bl', 'br')
ANCHOR_GROUPS = [anchors for n in (2, 3, 4) for anchors in combinations(CORNERS, n)]


def _strips(anchor, levels=2):
    return [dict(anchor=anchor, offset_x_cm=0, offset_y_cm=y, w_cm=w, h_cm=h)
            for y, w, h in ((0, 12, 3), (3, 8, 4), (7, 3, 2))[:levels]]


@pytest.fixture
def composite_panel(qapp):
    panel = CompositePanel()
    panel.set_outer_dims(185, 88)
    yield panel
    panel.shutdown()
    panel.close()
    panel.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize('anchors', ANCHOR_GROUPS)
@pytest.mark.parametrize('fill', ['blank', 'image'])
def test_multicorner_snapshot_worker_and_render(composite_panel, tmp_path, anchors, fill):
    panel = composite_panel
    # 每角级数不同，乱序回填后仍按角位独立排序，不能全局截为三级。
    rects = [rect for anchor in anchors
             for rect in _strips(anchor, CORNERS.index(anchor) % 3 + 1)]
    panel.set_cut_rects(list(reversed(rects)))
    panel._hole_material.setChecked(fill == 'image')
    params = panel.get_composite_params()
    assert panel._mode_combo.currentData() == 'multicorner_staircase'
    assert params['cut_rects'] == rects
    assert params['cuts_cm'] == []
    direct = panel.to_crop_design(dpi=2.54)
    direct.validate()
    assert [asdict(rect) for rect in direct.l_cut_rects] == rects
    assert direct.pool_hole_transparent == (fill == 'blank')

    source = Image.new('RGB', (186, 89), (180, 140, 100))
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 185, 88), outline='black', width=1)
    draw.rectangle((1, 1, 184, 87), outline=(240, 220, 180), width=3)
    material = tmp_path / 'outer.png'
    source.save(material)
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
    design.validate()
    assert [asdict(rect) for rect in design.l_cut_rects] == rects
    assert design.l_cuts_cm == []
    assert design.pool_hole_transparent == (fill == 'blank')
    output = render_design(design)
    assert output.size == (186, 89)
    assert output.getpixel((80, 40)) == ((255, 255, 255) if fill == 'blank' else (0, 0, 255))

    standalone = deepcopy(design)
    standalone.mode = 'rect_lshape'
    standalone.pool_hole_transparent = True
    standalone.inner_margin_top_cm = standalone.inner_margin_bottom_cm = 0
    standalone.inner_margin_left_cm = standalone.inner_margin_right_cm = 0
    reference = render_design(standalone)
    for anchor in anchors:
        x = 0 if anchor in ('tl', 'bl') else 166
        y = 0 if anchor in ('tl', 'tr') else 77
        box = (x, y, x + 20, y + 12)
        assert output.crop(box).tobytes() == reference.crop(box).tobytes()
        probe_x = 5 if anchor in ('tl', 'bl') else 180
        probe_y = 1 if anchor in ('tl', 'tr') else 87
        assert output.getpixel((probe_x, probe_y)) == (255, 255, 255)


def test_manual_levels_and_enabled_corners_are_independent(composite_panel):
    panel = composite_panel
    index = panel._mode_combo.findData('multicorner_staircase')
    assert index >= 0
    panel._mode_combo.setCurrentIndex(index)
    groups = panel._corner_control._multi_stair_groups
    for anchor in CORNERS:
        group = groups[anchor]
        group['box'].setChecked(True)
        for row, values in zip(group['rows'], ((4, 3), (8, 4))):
            row[0].setValue(values[0])
            row[1].setValue(values[1])
    expected = [rect for anchor in CORNERS for rect in _strips(anchor)]
    assert panel.get_composite_params()['cut_rects'] == expected
    groups['tl']['buttons'][-1].click()
    groups['tl']['rows'][-1][0].setValue(3)
    groups['tl']['rows'][-1][1].setValue(2)
    assert len(groups['tl']['rows']) == 3
    assert all(len(groups[a]['rows']) == 2 for a in CORNERS[1:])
    groups['tl']['remove'].click()
    assert panel.get_composite_params()['cut_rects'] == expected
    groups['tr']['box'].setChecked(False)
    assert panel.get_composite_params()['cut_rects'] == [
        rect for rect in expected if rect['anchor'] != 'tr']
    groups['tr']['box'].setChecked(True)
    assert panel.get_composite_params()['cut_rects'] == expected


@pytest.mark.parametrize('fill', ['blank', 'image'])
def test_switch_modes_keeps_pool_and_clears_inactive_geometry(composite_panel, fill):
    panel = composite_panel
    panel._hole_material.setChecked(fill == 'image')
    panel._hole_corners['tl'].setValue(2)
    pool = {key: value for key, value in panel.get_composite_params().items()
            if key.startswith('hole_')}
    panel.set_cut_rects(_strips('tl', 3) + _strips('br'))
    panel._mode_combo.setCurrentIndex(panel._mode_combo.findData('staircase'))
    assert panel.get_composite_params()['cut_rects'] == _strips('tl', 3)
    panel._mode_combo.setCurrentIndex(panel._mode_combo.findData('standard'))
    params = panel.get_composite_params()
    assert not params.get('cut_rects')
    assert params['cuts_cm'] == [dict(corner='tl', cut_w_cm=12, cut_h_cm=3)]
    panel.to_crop_design().validate()
    panel._mode_combo.setCurrentIndex(panel._mode_combo.findData('multicorner_staircase'))
    assert panel.get_composite_params()['cut_rects'] == []
    assert {key: value for key, value in panel.get_composite_params().items()
            if key.startswith('hole_')} == pool
    with pytest.raises(ValueError, match='至少需要'):
        panel.to_crop_design()


@pytest.mark.parametrize('fill', ['blank', 'image'])
def test_multicorner_generate_reaches_canvas(main_window, gui_helpers, fill):
    panel = main_window.composite_panel
    main_window.panel.design.dpi = 2.54
    panel.set_outer_dims(185, 88)
    rects = _strips('tl', 3) + _strips('tr') + _strips('bl', 1) + _strips('br', 3)
    panel.set_cut_rects(rects)
    panel._hole_material.setChecked(fill == 'image')
    gui_helpers.assert_button_exists(panel, '🔍 生成预览').click()
    design = main_window.canvas._design
    assert design is not None and design.mode == 'rect_lshape_hole'
    assert [asdict(rect) for rect in design.l_cut_rects] == rects
    assert design.pool_hole_transparent == (fill == 'blank')
    assert main_window.canvas.full_image().size == (186, 89)


@pytest.mark.parametrize('fill', ['blank', 'image'])
@pytest.mark.parametrize('anchors', [('tr',), ('tl', 'br')])
def test_standard_lshape_pool_fill_regression(composite_panel, tmp_path, fill, anchors):
    panel = composite_panel
    # 先经过新增模式，再回填标准挖角，确保旧条带不会污染标准路径。
    panel.set_cut_rects(_strips('tl', 3) + _strips('br'))
    cuts = [dict(corner=anchor, cut_w_cm=12, cut_h_cm=7) for anchor in anchors]
    panel._echo_composite_cuts(cuts)
    panel._hole_material.setChecked(fill == 'image')
    params = panel.get_composite_params()
    assert panel._mode_combo.currentData() == 'standard'
    assert params['cuts_cm'] == cuts
    assert not params.get('cut_rects')
    design = panel.to_crop_design(dpi=2.54)
    design.validate()
    assert design.l_cuts_cm == cuts
    assert design.l_cut_rects == []
    assert design.pool_hole_transparent == (fill == 'blank')
    material = tmp_path / 'standard_outer.png'
    Image.new('RGB', (186, 89), (180, 140, 100)).save(material)
    inner = tmp_path / 'standard_inner.png'
    Image.new('RGB', (40, 30), 'blue').save(inner)
    design.pool_outer_material_image = str(material)
    design.hole_bg_image = str(inner)
    output = render_design(design)
    assert output.getpixel((80, 40)) == ((255, 255, 255) if fill == 'blank' else (0, 0, 255))
    for anchor in anchors:
        x = 5 if anchor in ('tl', 'bl') else 180
        y = 2 if anchor in ('tl', 'tr') else 86
        assert output.getpixel((x, y)) == (255, 255, 255)
