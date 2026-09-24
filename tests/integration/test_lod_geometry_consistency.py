"""
P0-2 回归测试：LOD 预览几何必须与全分辨率导出几何一致

背景（core/image_ops.py:_make_lod_design）：
    CropDesign.cm2px() 只依赖 dpi（core/geometry.py:271-272），与 canvas_w_cm 无关。
    LOD 渲染把 canvas_w_cm / canvas_h_cm 按 scale 缩小，若几何长度量不随之缩放，
    则该长度量"占画布的比例"会被放大 1/scale 倍 → 预览几何 ≠ 导出几何。
    默认 LOD_SCALE_FACTOR = 0.5（gui/canvas_widget.py:29）→ 偏差 2 倍。
    该偏差被 render_design(..., skip_validate=True)（core/image_ops.py:487）静默放行，
    既不报错也不崩溃，因此只能靠本文件的断言拦住。

覆盖字段族（4 族，2026-09-24 实测确认）：
    1. l_cut_rects[].{w_cm,h_cm,offset_x_cm,offset_y_cm}  阶梯 L 形
    2. corner_{tl,tr,bl,br}_cm                            四角圆角
    3. ellipse_diameter_{w,h}_cm                          椭圆直径
    4. pool_holes_cm[].{x_cm,y_cm,w_cm,h_cm}              水池多洞

同时守护"不该动"的字段（防止过度修复）：
    - pool_material_design_{w,h}_cm：仅参与方向判据与倒数 AR，等比缩放不影响结论
    - pool_holes_gaps_cm：仅 UI 展示
    - ellipse_rx_ratio / ellipse_ry_ratio：不再参与几何计算

实测参考值（scale=0.5 / 0.25 各族掩膜 IoU）：
    修复前 [0.0000, 0.2885]  ← 最坏情况掩膜完全不重叠
    修复后 [0.9692, 0.9946]  ← 剩余差异仅为 LOD 栅格化取整
"""
import sys
sys.path.insert(0, '.')

import numpy as np
import pytest
from PIL import Image

from core.geometry import CropDesign, CutRect
from core.image_ops import _make_lod_design, _get_inner_pixel_mask


# 0.5 = GUI 实际值（gui/canvas_widget.py:LOD_SCALE_FACTOR）；0.25 = 更激进的降采样
LOD_SCALES = (0.5, 0.25)
IOU_MIN = 0.95        # 修复后实测最低 0.9692；修复前最高仅 0.2885
RATIO_TOL = 0.005     # 归一化占画布比的相对容差 0.5%
CM_PER_INCH = 2.54


# ---------- 工具 ----------

def _lod_of(design, scale):
    """按 scale 生成 LOD 副本（与 render_design_lod 内部口径一致）。"""
    w, h = design.canvas_w_px, design.canvas_h_px
    return _make_lod_design(design, max(1, int(w * scale)), max(1, int(h * scale)))


def _mask_iou(full_mask, lod_design):
    """全分辨率洞掩膜 vs LOD 洞掩膜（最近邻放大回原尺寸）的 IoU。"""
    lod_mask = _get_inner_pixel_mask(lod_design)
    h, w = full_mask.shape
    up = np.array(
        Image.fromarray((lod_mask * 255).astype(np.uint8)).resize((w, h), Image.NEAREST)
    ) > 127
    inter = np.logical_and(full_mask, up).sum()
    union = np.logical_or(full_mask, up).sum()
    return 1.0 if union == 0 else float(inter) / float(union)


def _simulate_pre_fix(design, lod):
    """把 lod 中已缩放的几何字段还原为未缩放原值 = 还原 P0-2 修复前状态。

    用于自校验：证明本文件的断言确实能捕获该缺陷，而不是恒真。
    """
    for i, cut in enumerate(getattr(lod, 'l_cut_rects', None) or []):
        src = design.l_cut_rects[i]
        cut.w_cm, cut.h_cm = src.w_cm, src.h_cm
        cut.offset_x_cm, cut.offset_y_cm = src.offset_x_cm, src.offset_y_cm
    for hole, src in zip(getattr(lod, 'pool_holes_cm', None) or [], design.pool_holes_cm or []):
        for key in ('x_cm', 'y_cm', 'w_cm', 'h_cm'):
            hole[key] = src[key]
    lod.corner_tl_cm = design.corner_tl_cm
    lod.corner_tr_cm = design.corner_tr_cm
    lod.corner_bl_cm = design.corner_bl_cm
    lod.corner_br_cm = design.corner_br_cm
    lod.ellipse_diameter_w_cm = design.ellipse_diameter_w_cm
    lod.ellipse_diameter_h_cm = design.ellipse_diameter_h_cm
    return lod


def _assert_ratio_consistent(export_ratio, preview_ratio, label):
    """断言同一几何量在导出/预览两条路径下占画布的比例一致。

    该量在导出侧为 0 时（如阶梯第一级 offset_x = 0），要求预览侧同样为 0。
    """
    if export_ratio == 0.0:
        assert preview_ratio == 0.0, (
            f"{label}: 导出侧占比为 0，预览侧应为 0，实际 {preview_ratio:.4%}"
        )
        return
    deviation = preview_ratio / export_ratio
    assert abs(deviation - 1.0) < RATIO_TOL, (
        f"{label}: LOD 预览与导出几何不一致 —— "
        f"导出 {export_ratio:.4%} vs 预览 {preview_ratio:.4%}"
        f"（偏差 {deviation:.3f}x，容差 {RATIO_TOL:.1%}）"
    )


# ---------- 设计构造器 ----------

def make_staircase_design():
    """阶梯 L 形：同角两级，第二级带 offset。"""
    return CropDesign(
        canvas_w_cm=20.0, canvas_h_cm=15.0, dpi=150, mode='rect_lshape',
        inner_margin_top_cm=2.0, inner_margin_bottom_cm=2.0,
        inner_margin_left_cm=2.0, inner_margin_right_cm=2.0,
        l_cut_rects=[
            CutRect(anchor='tr', offset_x_cm=0.0, offset_y_cm=0.0, w_cm=6.0, h_cm=4.0),
            CutRect(anchor='tr', offset_x_cm=6.0, offset_y_cm=0.0, w_cm=4.0, h_cm=7.0),
        ],
    )


def make_corner_design():
    """四角圆角，半径各不相同以便逐角校验。"""
    return CropDesign(
        canvas_w_cm=20.0, canvas_h_cm=15.0, dpi=150, mode='rect_hole',
        corner_tl_cm=2.0, corner_tr_cm=3.0, corner_bl_cm=1.5, corner_br_cm=2.5,
    )


def make_ellipse_design(diameter_w_cm=12.0, diameter_h_cm=8.0):
    """椭圆洞；直径传 0 表示自动模式。"""
    return CropDesign(
        canvas_w_cm=20.0, canvas_h_cm=15.0, dpi=150, mode='ellipse_hole',
        inner_margin_top_cm=2.0, inner_margin_bottom_cm=2.0,
        inner_margin_left_cm=2.0, inner_margin_right_cm=2.0,
        ellipse_diameter_w_cm=diameter_w_cm, ellipse_diameter_h_cm=diameter_h_cm,
    )


def make_multi_hole_design():
    """水池多洞：两洞横向排布，含非几何键用于校验键保留。"""
    design = CropDesign(
        canvas_w_cm=20.0, canvas_h_cm=15.0, dpi=150, mode='rect_hole',
        inner_margin_top_cm=2.0, inner_margin_bottom_cm=2.0,
        inner_margin_left_cm=2.0, inner_margin_right_cm=2.0,
    )
    design.pool_is_multi_hole = True
    design.pool_holes_cm = [
        {'x_cm': 2.0, 'y_cm': 2.0, 'w_cm': 8.0, 'h_cm': 5.0,
         'mt_cm': 2.0, 'mb_cm': 2.0, 'ml_cm': 2.0, 'mr_cm': 2.0,
         'inner_material_path': '洞1-8x5.jpg'},
        {'x_cm': 11.0, 'y_cm': 2.0, 'w_cm': 7.0, 'h_cm': 5.0,
         'mt_cm': 2.0, 'mb_cm': 2.0, 'ml_cm': 2.0, 'mr_cm': 2.0},
    ]
    return design


# ---------- 族 1：阶梯 L 形 l_cut_rects ----------

@pytest.mark.parametrize('scale', LOD_SCALES)
def test_lod_scales_staircase_cut_rects(scale):
    """阶梯 CutRect 的宽高与偏移在 LOD 下必须等比缩放。"""
    design = make_staircase_design()
    design.validate()
    w, h = design.canvas_w_px, design.canvas_h_px
    lod = _lod_of(design, scale)

    full_specs = design.l_shapes_px().cut_rect_specs()
    lod_specs = lod.l_shapes_px().cut_rect_specs()
    assert len(full_specs) == len(lod_specs) == 2

    for i, (a, b) in enumerate(zip(full_specs, lod_specs)):
        _assert_ratio_consistent(
            float(a['cut_w']) / w, float(b['cut_w']) / lod.canvas_w_px,
            f'cut[{i}].切宽 scale={scale}')
        _assert_ratio_consistent(
            float(a['cut_h']) / h, float(b['cut_h']) / lod.canvas_h_px,
            f'cut[{i}].切高 scale={scale}')
        _assert_ratio_consistent(
            float(a['offset_x']) / w, float(b['offset_x']) / lod.canvas_w_px,
            f'cut[{i}].x偏移 scale={scale}')
        _assert_ratio_consistent(
            float(a['offset_y']) / h, float(b['offset_y']) / lod.canvas_h_px,
            f'cut[{i}].y偏移 scale={scale}')


# ---------- 族 2：四角圆角 ----------

@pytest.mark.parametrize('scale', LOD_SCALES)
def test_lod_scales_corner_radii(scale):
    """四角圆角半径在 LOD 下必须等比缩放。"""
    design = make_corner_design()
    design.validate()
    w, h = design.canvas_w_px, design.canvas_h_px
    lod = _lod_of(design, scale)

    full_corners = design.corners_px
    lod_corners = lod.corners_px
    for key in ('tl', 'tr', 'bl', 'br'):
        _assert_ratio_consistent(
            full_corners[key] / w, lod_corners[key] / lod.canvas_w_px,
            f'corner_{key} scale={scale}')


# ---------- 族 3：椭圆直径 ----------

@pytest.mark.parametrize('scale', LOD_SCALES)
def test_lod_scales_ellipse_diameter(scale):
    """椭圆直径在 LOD 下必须等比缩放。"""
    design = make_ellipse_design(12.0, 8.0)
    design.validate()
    w, h = design.canvas_w_px, design.canvas_h_px
    lod = _lod_of(design, scale)

    _assert_ratio_consistent(
        design.cm2px(12.0) / w, lod.cm2px(lod.ellipse_diameter_w_cm) / lod.canvas_w_px,
        f'椭圆直径宽 scale={scale}')
    _assert_ratio_consistent(
        design.cm2px(8.0) / h, lod.cm2px(lod.ellipse_diameter_h_cm) / lod.canvas_h_px,
        f'椭圆直径高 scale={scale}')


@pytest.mark.parametrize('scale', LOD_SCALES)
def test_lod_preserves_ellipse_auto_mode(scale):
    """椭圆直径为 0（自动模式）时，LOD 后必须仍为 0，否则会破坏自动回退语义。"""
    design = make_ellipse_design(0.0, 0.0)
    design.validate()
    lod = _lod_of(design, scale)
    assert lod.ellipse_diameter_w_cm == 0.0, '自动模式宽直径被破坏'
    assert lod.ellipse_diameter_h_cm == 0.0, '自动模式高直径被破坏'


# ---------- 族 4：水池多洞 ----------

@pytest.mark.parametrize('scale', LOD_SCALES)
def test_lod_scales_pool_holes(scale):
    """多洞坐标与尺寸在 LOD 下必须等比缩放。"""
    design = make_multi_hole_design()
    design.validate()
    w, h = design.canvas_w_px, design.canvas_h_px
    lod = _lod_of(design, scale)

    assert len(lod.pool_holes_cm) == 2
    for i, (a, b) in enumerate(zip(design.pool_holes_cm, lod.pool_holes_cm)):
        _assert_ratio_consistent(
            float(a['x_cm']) * design.dpi / CM_PER_INCH / w,
            float(b['x_cm']) * lod.dpi / CM_PER_INCH / lod.canvas_w_px,
            f'hole[{i}].x scale={scale}')
        _assert_ratio_consistent(
            float(a['y_cm']) * design.dpi / CM_PER_INCH / h,
            float(b['y_cm']) * lod.dpi / CM_PER_INCH / lod.canvas_h_px,
            f'hole[{i}].y scale={scale}')
        _assert_ratio_consistent(
            float(a['w_cm']) * design.dpi / CM_PER_INCH / w,
            float(b['w_cm']) * lod.dpi / CM_PER_INCH / lod.canvas_w_px,
            f'hole[{i}].w scale={scale}')
        _assert_ratio_consistent(
            float(a['h_cm']) * design.dpi / CM_PER_INCH / h,
            float(b['h_cm']) * lod.dpi / CM_PER_INCH / lod.canvas_h_px,
            f'hole[{i}].h scale={scale}')


@pytest.mark.parametrize('scale', LOD_SCALES)
def test_lod_preserves_hole_non_geometry_keys(scale):
    """洞 dict 的非几何键（素材路径、per-hole 边距）必须原样保留。"""
    design = make_multi_hole_design()
    design.validate()
    lod = _lod_of(design, scale)

    assert sorted(lod.pool_holes_cm[0].keys()) == sorted(design.pool_holes_cm[0].keys())
    assert lod.pool_holes_cm[0]['inner_material_path'] == '洞1-8x5.jpg'
    for key in ('mt_cm', 'mb_cm', 'ml_cm', 'mr_cm'):
        assert lod.pool_holes_cm[0][key] == design.pool_holes_cm[0][key], (
            f'{key} 不应被 LOD 缩放（当前不参与渲染几何）')


# ---------- 端到端：真实掩膜 IoU ----------

@pytest.mark.parametrize('maker', [make_staircase_design, make_multi_hole_design,
                                   make_ellipse_design])
@pytest.mark.parametrize('scale', LOD_SCALES)
def test_lod_mask_matches_full_resolution(maker, scale):
    """LOD 洞掩膜放大回原尺寸后，与全分辨率掩膜的 IoU 必须达标。

    这是 P0-2 的直接判据：修复前该值最低为 0.0（掩膜完全不重叠）。
    """
    design = maker()
    design.validate()
    full_mask = _get_inner_pixel_mask(design)
    lod = _lod_of(design, scale)
    iou = _mask_iou(full_mask, lod)
    assert iou >= IOU_MIN, (
        f'{maker.__name__} scale={scale}: LOD 与全分辨率掩膜 IoU={iou:.4f} < {IOU_MIN}'
    )


def test_assertion_catches_pre_fix_state():
    """自校验：把几何还原成修复前状态时，IoU 断言必须失败。

    防止"测试写了但恒真"—— 若本用例失败，说明上面的断言已失去分辨能力。
    """
    design = make_multi_hole_design()
    design.validate()
    full_mask = _get_inner_pixel_mask(design)
    lod = _simulate_pre_fix(design, _lod_of(design, 0.5))
    iou = _mask_iou(full_mask, lod)
    assert iou < IOU_MIN, (
        f'修复前状态的 IoU={iou:.4f} 竟然通过了阈值 {IOU_MIN}，'
        f'说明断言已无法分辨该缺陷'
    )


# ---------- 隔离性与"不该动"的字段 ----------

@pytest.mark.parametrize('scale', LOD_SCALES)
def test_lod_does_not_mutate_original_design(scale):
    """_make_lod_design 必须通过 clone() 隔离，绝不污染原 design（导出路径安全）。"""
    designs = [make_staircase_design(), make_corner_design(),
               make_ellipse_design(), make_multi_hole_design()]
    for design in designs:
        design.validate()
        before_corners = (design.corner_tl_cm, design.corner_tr_cm,
                          design.corner_bl_cm, design.corner_br_cm)
        before_ellipse = (design.ellipse_diameter_w_cm, design.ellipse_diameter_h_cm)
        before_cuts = [(c.w_cm, c.h_cm, c.offset_x_cm, c.offset_y_cm)
                       for c in design.l_cut_rects]
        before_holes = [dict(h) for h in design.pool_holes_cm]

        _lod_of(design, scale)

        assert (design.corner_tl_cm, design.corner_tr_cm,
                design.corner_bl_cm, design.corner_br_cm) == before_corners
        assert (design.ellipse_diameter_w_cm,
                design.ellipse_diameter_h_cm) == before_ellipse
        assert [(c.w_cm, c.h_cm, c.offset_x_cm, c.offset_y_cm)
                for c in design.l_cut_rects] == before_cuts
        assert design.pool_holes_cm == before_holes


@pytest.mark.parametrize('scale', LOD_SCALES)
def test_lod_leaves_direction_only_field_unscaled(scale):
    """pool_material_design_*_cm 仅参与方向判据与比值，LOD 不应缩放它。

    守护性断言：防止后续"顺手修全"把它也缩了，反而改变素材方向校正结论。
    """
    design = make_multi_hole_design()
    design.pool_material_design_w_cm = 58.0
    design.pool_material_design_h_cm = 121.0
    design.validate()
    lod = _lod_of(design, scale)

    assert lod.pool_material_design_w_cm == 58.0
    assert lod.pool_material_design_h_cm == 121.0
    # 方向判据与倒数 AR 必须与导出侧一致
    assert (lod.pool_material_design_w_cm > lod.pool_material_design_h_cm) == \
           (design.pool_material_design_w_cm > design.pool_material_design_h_cm)
    assert (lod.pool_material_design_h_cm / lod.pool_material_design_w_cm) == \
           pytest.approx(design.pool_material_design_h_cm / design.pool_material_design_w_cm)


def test_export_path_never_touches_lod_helper(monkeypatch):
    """导出路径不得经过 _make_lod_design —— 这是"修复未改变程序功能"的运行时证明。

    _make_lod_design 仅由 render_design_lod 调用（GUI 预览专用，core/image_ops.py:482）；
    render_design(quality='export') 走全分辨率分支，因此 P0-2 的修改对导出像素输出零影响。
    """
    import core.image_ops as image_ops

    touched = []
    original = image_ops._make_lod_design

    def spy(*args, **kwargs):
        touched.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(image_ops, '_make_lod_design', spy)

    design = make_staircase_design()
    design.validate()
    image_ops.render_design(design, quality='export')

    assert not touched, '导出路径意外经过了 _make_lod_design，修复可能影响导出输出'
