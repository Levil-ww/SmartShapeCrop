"""长条素材在 L 形面板标准/阶梯模式下的渲染回归。"""
import numpy as np
import pytest

from tests.core.test_lshape_long_material import long_material, directional_material


@pytest.mark.parametrize('mode', ['standard', 'staircase', 'multicorner_staircase'])
@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
@pytest.mark.parametrize('quality', ['export', 'lod'])
def test_long_material_panel_to_render_keeps_cut_outline(
        lshape_panel, tmp_path, mode, corner, quality):
    """实际面板→几何 Builder→预览/导出：标准、单边/四角阶梯均不丢补边。"""
    from core.geometry import CropDesign
    from core.image_ops import render_design, render_design_lod
    from workers.design_builders import LShapeBuildParams, apply_lshape_geometry

    path = tmp_path / 'long_material.png'
    long_material().save(path)
    panel = lshape_panel
    panel.set_outer_dims(299, 59)
    anchors = ({'tl': ('tl', 'br'), 'tr': ('tr', 'bl'),
                'bl': ('tl', 'tr', 'bl'), 'br': ('tl', 'tr', 'bl', 'br')}[corner]
               if mode == 'multicorner_staircase' else (corner,))
    if mode == 'standard':
        panel.set_lshape_params(corner, 32, 27)
    else:
        panel.set_cut_rects([
            dict(anchor=anchor, offset_x_cm=0, offset_y_cm=oy, w_cm=w, h_cm=h)
            for anchor in anchors for oy, w, h in ((0, 32, 10), (10, 20, 17))])
    design = CropDesign(canvas_w_cm=300, canvas_h_cm=60, dpi=25.4)
    apply_lshape_geometry(design, LShapeBuildParams(panel.get_lshape_params()), str(path))
    design.validate()
    image = (render_design_lod(design, scale=0.5) if quality == 'lod'
             else render_design(design, quality='export'))
    assert image.size == (3000, 600)
    # 0.5cm 损耗边距：各角切口底边在275px，保留侧应重走色带及内框。
    for anchor in anchors:
        view = np.array(image)
        if anchor in ('tr', 'br'):
            view = np.fliplr(view)
        if anchor in ('bl', 'br'):
            view = np.flipud(view)
        assert tuple(view[305, 150]) == (243, 236, 220)
        # 多角位时底边内框线可能落入对侧挖空区，应检查未被挖掉的竖边。
        line = (view[180, 278:296] if mode == 'multicorner_staircase'
                else view[343:352, 150])
        # 预览放大采用 Lanczos，实际8px细线会产生少量振铃色差。
        tolerance = 12 if quality == 'lod' else 0
        assert np.any(np.all(np.abs(line.astype(int) - (70, 60, 50)) <= tolerance, axis=1))
        assert tuple(view[50, 50]) == (255, 255, 255)
        if mode != 'standard':
            # 阶梯第二级右侧有补边；第一级下方超出第二级的区域保留素材。
            assert tuple(view[170, 235]) == (243, 236, 220)
            assert tuple(view[200, 300]) == (205, 185, 155)


@pytest.mark.parametrize('corner', ['tl', 'tr', 'bl', 'br'])
@pytest.mark.parametrize('quality', ['export', 'lod'])
def test_directional_material_standard_join_in_preview_and_export(
        lshape_panel, tmp_path, corner, quality):
    from core.geometry import CropDesign
    from core.image_ops import render_design, render_design_lod
    from workers.design_builders import LShapeBuildParams, apply_lshape_geometry

    path = tmp_path / 'directional_material.png'
    directional_material().save(path)
    lshape_panel.set_outer_dims(299, 59)
    lshape_panel.set_lshape_params(corner, 32, 27)
    design = CropDesign(canvas_w_cm=300, canvas_h_cm=60, dpi=25.4)
    apply_lshape_geometry(design, LShapeBuildParams(lshape_panel.get_lshape_params()), str(path))
    image = (render_design_lod(design, scale=0.5) if quality == 'lod'
             else render_design(design, quality='export'))
    view = np.array(image)
    if corner in ('tr', 'br'):
        view = np.fliplr(view)
    if corner in ('bl', 'br'):
        view = np.flipud(view)
    np.testing.assert_array_equal(view[76:96, 455], [(70, 60, 50)] * 20)
    assert tuple(view[345, 120]) == (243, 236, 220)
    tolerance = 12 if quality == 'lod' else 0
    assert np.all(np.abs(view[345, 130].astype(int) - (70, 60, 50)) <= tolerance)
    assert tuple(view[100, 100]) == (255, 255, 255)
