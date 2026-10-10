"""多角阶梯沿用单角条带语义，编辑与回填不得丢失其他角位。"""
from itertools import combinations
from copy import deepcopy

import numpy as np
import pytest

from core.geometry import CropDesign, CutRect
from workers.design_builders import LShapeBuildParams, apply_lshape_geometry


CORNERS = ('tl', 'tr', 'bl', 'br')
COMBINATIONS = [pair for n in (2, 3, 4) for pair in combinations(CORNERS, n)]


def strips(anchor, levels=2):
    return [
        {'anchor': anchor, 'offset_x_cm': 0, 'offset_y_cm': oy,
         'w_cm': w, 'h_cm': h}
        for oy, w, h in ((0, 12, 3), (3, 8, 4), (7, 3, 2))[:levels]
    ]


@pytest.mark.parametrize('anchors', COMBINATIONS)
def test_multicorner_refill_preserves_every_anchor(lshape_panel, anchors):
    panel = lshape_panel
    panel.set_outer_dims(80, 60)
    rects = [rect for anchor in anchors for rect in strips(anchor)]
    # 乱序回填必须在各角内排序，不得混角计算步进或全局截成三级。
    panel.set_cut_rects(list(reversed(rects)))
    assert panel.get_cut_rects_cm() == rects
    assert panel._mode_combo.currentData() == 'multicorner_staircase'
    assert panel.get_lshape_params()['cut_rects'] == rects
    assert panel.get_cuts_cm() == []
    assert panel.get_corner() == anchors[0]
    assert panel.get_cut_w_cm() == 12
    assert panel.get_cut_h_cm() == 7
    design = CropDesign(canvas_w_cm=81, canvas_h_cm=61, dpi=25)
    apply_lshape_geometry(design, LShapeBuildParams(panel.get_lshape_params()), '')
    assert len(design.l_cut_rects) == 2 * len(anchors)
    design.validate()


def test_multicorner_manual_edit_and_disable_are_independent(lshape_panel):
    panel = lshape_panel
    panel.set_outer_dims(80, 60)
    panel._mode_combo.setCurrentIndex(panel._mode_combo.findData('multicorner_staircase'))
    groups = panel._corner_control._multi_stair_groups
    for anchor in ('tl', 'br'):
        groups[anchor]['box'].setChecked(True)
        for row, values in zip(groups[anchor]['rows'], ((4, 3), (8, 4))):
            row[0].setValue(values[0])
            row[1].setValue(values[1])
    assert panel.get_cut_rects_cm() == strips('tl') + strips('br')
    groups['tl']['box'].setChecked(False)
    assert panel.get_lshape_params()['cut_rects'] == strips('br')
    groups['tl']['box'].setChecked(True)
    groups['tl']['rows'][0][0].setValue(5)
    expected = strips('tl') + strips('br')
    expected[0]['w_cm'] = 13
    assert panel.get_cut_rects_cm() == expected
    panel._sp_outer_w.setValue(91)
    assert panel.get_lshape_params()['outer_w_cm'] == 90
    assert panel.get_lshape_params()['cut_rects'] == expected


def test_multicorner_each_group_add_remove_and_limit(lshape_panel):
    panel = lshape_panel
    panel.set_cut_rects(strips('tl') + strips('br'))
    groups = panel._corner_control._multi_stair_groups
    group = groups['tl']
    group['buttons'][-1].click()
    group['rows'][-1][0].setValue(3)
    group['rows'][-1][1].setValue(2)
    assert len(panel.get_cut_rects_cm()) == 5
    panel._corner_control.add_level(group)
    assert len(group['rows']) == 3
    group['remove'].click()
    assert panel.get_cut_rects_cm() == strips('tl') + strips('br')
    for _ in range(3):
        group['remove'].click()
    assert len(group['rows']) == 1
    assert len(groups['br']['rows']) == 2


def test_refill_caps_each_corner_and_clears_absent_groups(lshape_panel):
    panel = lshape_panel
    extra = dict(strips('tl', 3)[-1], offset_y_cm=9, w_cm=1)
    rects = strips('tl', 3) + [extra] + strips('br', 3)
    panel.set_cut_rects(rects)
    assert panel.get_cut_rects_cm() == strips('tl', 3) + strips('br', 3)
    panel.set_cut_rects(strips('tr') + strips('bl'))
    assert panel.get_cut_rects_cm() == strips('tr') + strips('bl')
    groups = panel._corner_control._multi_stair_groups
    assert not groups['tl']['box'].isChecked()
    assert not groups['br']['box'].isChecked()


def test_switch_single_multi_single_preserves_primary_steps(lshape_panel):
    panel = lshape_panel
    panel.set_cut_rects(strips('br', 3))
    panel._mode_combo.setCurrentIndex(panel._mode_combo.findData('multicorner_staircase'))
    assert panel.get_cut_rects_cm() == strips('br', 3)
    assert panel._gb_staircase.isHidden()
    assert not panel._corner_control._gb_multi_staircase.isHidden()
    panel._mode_combo.setCurrentIndex(panel._mode_combo.findData('staircase'))
    assert panel.get_cut_rects_cm() == strips('br', 3)
    assert not panel._gb_staircase.isHidden()
    assert panel._corner_control._gb_multi_staircase.isHidden()


def test_clear_then_reenter_multi_has_no_stale_steps(lshape_panel):
    panel = lshape_panel
    panel.set_cut_rects(strips('tl') + strips('br'))
    panel.clear_lshape_params()
    assert panel.get_cut_rects_cm() == []
    assert panel._mode_combo.currentData() == 'standard'
    panel._mode_combo.setCurrentIndex(panel._mode_combo.findData('multicorner_staircase'))
    assert panel.get_lshape_params()['cut_rects'] == []


def test_switch_empty_single_to_multi_clears_previous_multi_values(lshape_panel):
    panel = lshape_panel
    panel.set_cut_rects(strips('tl') + strips('br'))
    panel._mode_combo.setCurrentIndex(panel._mode_combo.findData('staircase'))
    for width, height, _ in panel._stair_rows:
        width.setValue(0)
        height.setValue(0)
    panel._mode_combo.setCurrentIndex(panel._mode_combo.findData('multicorner_staircase'))
    assert panel.get_cut_rects_cm() == []


def test_switch_multi_to_standard_preserves_first_strip(lshape_panel):
    panel = lshape_panel
    panel.set_cut_rects(strips('bl') + strips('br'))
    panel._mode_combo.setCurrentIndex(panel._mode_combo.findData('standard'))
    assert panel.get_cut_rects_cm() == []
    assert panel.get_cuts_cm() == [{'corner': 'bl', 'cut_w_cm': 12, 'cut_h_cm': 3}]
    assert panel.get_lshape_params().get('cut_rects', []) == []


@pytest.mark.parametrize('kind', ['overlap', 'out_of_bounds'])
def test_multicorner_invalid_geometry_uses_existing_validation(lshape_panel, kind):
    rects = strips('tl') + strips('tr')
    if kind == 'overlap':
        rects[0]['w_cm'] = 70
        rects[2]['w_cm'] = 70
    else:
        rects[0]['w_cm'] = 100
    lshape_panel.set_cut_rects(rects)
    design = CropDesign(canvas_w_cm=81, canvas_h_cm=61)
    design.mode = 'rect_lshape'
    design.l_cut_rects = [CutRect(**rect) for rect in lshape_panel.get_cut_rects_cm()]
    with pytest.raises(ValueError, match='重叠|超出'):
        design.validate()


def test_composite_panel_retains_existing_modes(qapp):
    from gui.composite_panel import CompositePanel
    panel = CompositePanel()
    try:
        assert panel._mode_combo.findData('multicorner_staircase') == -1
    finally:
        panel.shutdown()
        panel.close()
        panel.deleteLater()


@pytest.mark.parametrize('levels', [1, 3])
def test_four_corners_keep_independent_level_counts(lshape_panel, levels):
    rects = [rect for anchor in CORNERS for rect in strips(anchor, levels)]
    lshape_panel.set_cut_rects(rects)
    assert lshape_panel.get_cut_rects_cm() == rects
    assert len(lshape_panel.get_cut_rects_cm()) == 4 * levels
    groups = lshape_panel._corner_control._multi_stair_groups
    assert all(len(group['rows']) == levels for group in groups.values())


def test_multicorner_zero_and_partial_steps_are_excluded(lshape_panel):
    panel = lshape_panel
    panel.set_cut_rects(strips('tl') + strips('br'))
    groups = panel._corner_control._multi_stair_groups
    groups['tl']['rows'][0][1].setValue(0)
    assert panel.get_cut_rects_cm() == [dict(strips('tl')[1], offset_y_cm=0)] + strips('br')
    groups['tl']['box'].setChecked(False)
    groups['br']['box'].setChecked(False)
    assert panel.get_lshape_params()['cut_rects'] == []
    assert panel.get_cut_w_cm() == 0
    assert panel.get_cut_h_cm() == 0


@pytest.mark.parametrize('radius', [0, 1])
@pytest.mark.parametrize('manual_border', [False, True])
def test_multicorner_render_matches_single_corner_pipeline(lshape_panel, tmp_path, radius, manual_border):
    """真实参数→Worker 几何→素材渲染，各角圆角与补边应与单边相同。"""
    from PIL import Image, ImageDraw
    from core.image_ops import render_design
    src = Image.new('RGB', (800, 600), (215, 201, 174))
    draw = ImageDraw.Draw(src)
    draw.rectangle((0, 0, 799, 599), outline=(0, 0, 0), width=8)
    draw.rectangle((8, 8, 791, 591), outline=(247, 233, 206), width=60)
    draw.rectangle((68, 68, 731, 531), outline=(168, 148, 123), width=15)
    path = tmp_path / 'material.png'
    src.save(path)
    panel = lshape_panel
    panel.set_outer_dims(80, 60)
    rects = [rect for anchor in CORNERS for rect in strips(anchor, 3)]
    panel.set_cut_rects(rects)
    design = CropDesign(canvas_w_cm=81, canvas_h_cm=61, dpi=25)
    for anchor in CORNERS:
        setattr(design, f'corner_{anchor}_cm', radius)
    apply_lshape_geometry(design, LShapeBuildParams(panel.get_lshape_params()), str(path))
    if manual_border:
        design.lshape_manual_edge_px = 2
        design.lshape_manual_band_px = 10
        design.lshape_manual_band_color = (247, 233, 206)
    design.validate()
    combined = np.array(render_design(design))
    h, w = combined.shape[:2]
    for anchor in CORNERS:
        single = deepcopy(design)
        single.l_corner = anchor
        single.l_cut_rects = [cut for cut in single.l_cut_rects if cut.anchor == anchor]
        isolated = np.array(render_design(single))
        ys = slice(0, h//2) if anchor in ('tl', 'tr') else slice(h//2, h)
        xs = slice(0, w//2) if anchor in ('tl', 'bl') else slice(w//2, w)
        np.testing.assert_array_equal(combined[ys, xs], isolated[ys, xs])
