"""阶段 2 集成测试：L 形挖角（裁剪有图）数据流。

覆盖：
1. 渲染语义：rect_lshape + 池素材 → L 形区域保留花纹素材、挖掉的角显示洞色（裁剪有图）
2. 回归：rect_hole + 池素材 → 挖洞区域显示洞色（行为不变）
3. Worker 数据流：PoolRenderWorker 带 lshape_params → design 字段正确（mode/l_corner/l_cut/canvas/素材）
4. Worker 回归：不带 lshape_params → 仍为 rect_hole（行为不变）
"""
import os
import sys
sys.path.insert(0, '.')
import numpy as np
import pytest
from PIL import Image, ImageDraw

from core.geometry import CropDesign
from core.image_ops import render_design


# ---------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def material_path(tmp_path_factory):
    """合成一张模拟花型素材（红蓝格子，便于像素判断）。"""
    d = tmp_path_factory.mktemp("tpl")
    mat = Image.new('RGB', (300, 60), (200, 30, 30))
    dd = ImageDraw.Draw(mat)
    for x in range(0, 300, 30):
        dd.rectangle([x, 0, x + 14, 59], fill=(30, 30, 200))
    p = d / "双面格-定制-定制尺寸-戴安娜;31.7x433cm.jpg"
    mat.save(str(p))
    return str(p)


def _lshape_design(material: str, **kw):
    base = dict(
        canvas_w_cm=31.0, canvas_h_cm=4.0, dpi=150,
        mode='rect_lshape',
        outer_margin_cm=0.0,
        inner_margin_top_cm=0.0, inner_margin_bottom_cm=0.0,
        inner_margin_left_cm=0.0, inner_margin_right_cm=0.0,
        l_corner='tr', l_cut_w_cm=1.0, l_cut_h_cm=1.0,
        hole_bg_color=(250, 245, 230),
        outer_bg_color=(0, 0, 0),
        pool_outer_material_image=material,
        outer_bg_image=material,
        pool_inner_material_image=material,
        pool_hole_transparent=True,
    )
    base.update(kw)
    return CropDesign(**base)


# ---------------------------------------------------------------- render

def test_lshape_pool_render_cut_is_hole_material_in_body(material_path):
    """裁剪有图语义：L 形区域保留花纹素材，挖掉的角显示洞色。"""
    design = _lshape_design(material_path)
    out = render_design(design, quality='preview')
    a = np.array(out)
    ir = design.inner_rect_px()
    cut_w_px = design.cm2px(design.l_cut_w_cm)
    cut_h_px = design.cm2px(design.l_cut_h_cm)
    # tr 角 cut 区域中心
    cx = int(ir.right - cut_w_px / 2)
    cy = int(ir.y + cut_h_px / 2)
    # L 形保留区（左下）
    lx, ly = int(ir.x + ir.w * 0.1), int(ir.y + ir.h * 0.9)
    cut_px = a[cy, cx]
    body_px = a[ly, lx]
    hb = design.hole_bg_color
    # cut = 洞色
    assert all(abs(int(cut_px[k]) - hb[k]) <= 3 for k in range(3)), cut_px
    # body = 花纹（非洞色、非纯白）
    assert not all(abs(int(body_px[k]) - hb[k]) <= 3 for k in range(3)), body_px


def test_lshape_pool_render_all_corners(material_path):
    """四个挖角位置都满足：L 形区域花纹 + cut 洞色。"""
    for corner in ('tl', 'tr', 'bl', 'br'):
        design = _lshape_design(material_path, l_corner=corner)
        out = render_design(design, quality='preview')
        a = np.array(out)
        ir = design.inner_rect_px()
        cw = design.cm2px(design.l_cut_w_cm)
        ch = design.cm2px(design.l_cut_h_cm)
        if corner in ('tr', 'br'):
            cx = int(ir.right - cw / 2)
        else:
            cx = int(ir.x + cw / 2)
        if corner in ('tr', 'tl'):
            cy = int(ir.y + ch / 2)
        else:
            cy = int(ir.bottom - ch / 2)
        # L 形保留区（对角）
        if corner in ('tr', 'br'):
            bx = int(ir.x + ir.w * 0.1)
        else:
            bx = int(ir.right - ir.w * 0.1)
        if corner in ('tr', 'tl'):
            by = int(ir.bottom - ir.h * 0.1)
        else:
            by = int(ir.y + ir.h * 0.1)
        hb = design.hole_bg_color
        assert all(abs(int(a[cy, cx][k]) - hb[k]) <= 3 for k in range(3)), (corner, a[cy, cx])
        assert not all(abs(int(a[by, bx][k]) - hb[k]) <= 3 for k in range(3)), (corner, a[by, bx])


def test_rect_hole_pool_render_unchanged(material_path):
    """回归：rect_hole + 池素材 → 挖洞区域（inner_rect）显示洞色，行为不变。

    注：pool_hole_transparent=True 时内洞按设计渲染为纯白（JPG 透明表示），
    故本用例用 False 锁定"洞色"语义，同时验证环形带保留素材花纹。
    """
    design = CropDesign(
        canvas_w_cm=31.0, canvas_h_cm=4.0, dpi=150,
        mode='rect_hole',
        outer_margin_cm=0.0,
        inner_margin_top_cm=0.3, inner_margin_bottom_cm=0.3,
        inner_margin_left_cm=0.3, inner_margin_right_cm=0.3,
        hole_bg_color=(250, 245, 230),
        outer_bg_color=(0, 0, 0),
        pool_outer_material_image=material_path,
        outer_bg_image=material_path,
        pool_hole_transparent=False,
    )
    out = render_design(design, quality='preview')
    a = np.array(out)
    ir = design.inner_rect_px()
    hb = design.hole_bg_color
    cx = int(ir.x + ir.w * 0.5)
    cy = int(ir.y + ir.h * 0.5)
    px = a[cy, cx]
    assert all(abs(int(px[k]) - hb[k]) <= 3 for k in range(3)), px
    # 环形带（洞外区域）仍保留素材花纹（非洞色、非纯白）
    ring_px = a[int(ir.y - max(2, ir.h * 0.05)), int(ir.x + ir.w * 0.1)]
    assert not all(abs(int(ring_px[k]) - hb[k]) <= 3 for k in range(3)), ring_px
    assert not all(v > 250 for v in ring_px), ring_px


# ---------------------------------------------------------------- worker

def _run_worker(target_name, template_dir, sketch_path, lshape_params):
    from gui.property_panel_workers import PoolRenderWorker
    from core.parser.template_matcher import TemplateMatcher
    results = {}

    def _ok(design, sketch_result, log_text):
        results['design'] = design
        results['log'] = log_text

    def _err(msg):
        results['err'] = msg

    w = PoolRenderWorker(
        TemplateMatcher(), str(template_dir), target_name,
        sketch_path=sketch_path,
        lshape_params=lshape_params,
    )
    w.finished_ok.connect(_ok)
    w.finished_err.connect(_err)
    w.run()
    assert 'err' not in results, results.get('err')
    return results['design']


def test_worker_lshape_params_builds_design(tmp_path, material_path):
    """PoolRenderWorker 带 lshape_params → design 为 rect_lshape 且字段正确。"""
    design = _run_worker(
        '双面革-定制-裁剪有图-戴安娜;33x450cm裁剪有图.jpg',
        os.path.dirname(material_path),
        material_path,
        {'corner': 'tr', 'cut_w_cm': 100.0, 'cut_h_cm': 2.0,
         'outer_w_cm': 450.0, 'outer_h_cm': 33.0},
    )
    assert design.mode == 'rect_lshape'
    assert design.l_corner == 'tr'
    # 损耗补偿语义（2026-09-03 业务确认）：外框补 1cm，挖角不补。
    #   依据 gui/property_panel_layers.py:87「挖角值直接取草图识别的成品真值，
    #   不做额外损耗补偿」与 gui/lshape_panel.py:414-416「外框 SpinBox 存画布值
    #   （= 设计外框 + 1cm 损耗）；挖角 SpinBox 存设计值」。
    assert design.l_cut_w_cm == pytest.approx(100.0)  # 挖角存成品真值，不加损耗
    assert design.l_cut_h_cm == pytest.approx(2.0)
    assert design.canvas_w_cm == pytest.approx(451.0)  # 外框 + 1cm 损耗
    assert design.canvas_h_cm == pytest.approx(34.0)
    # 成品保留段 = 画布 - cut = (451-100)=351; (34-2)=32（外框侧 1cm 为余料）
    assert design.inner_margin_top_cm == 0.0
    assert design.inner_margin_bottom_cm == 0.0
    assert design.inner_margin_left_cm == 0.0
    assert design.inner_margin_right_cm == 0.0
    assert design.pool_outer_material_image == material_path
    assert design.pool_inner_material_image == material_path
    assert design.outer_bg_image == material_path
    assert design.pool_hole_transparent is True


def test_worker_without_lshape_keeps_rect_hole(tmp_path, material_path):
    """回归：不带 lshape_params → 仍为 rect_hole 水池模式。"""
    design = _run_worker(
        '双面革-定制-裁剪有图-戴安娜;33x450cm裁剪有图.jpg',
        os.path.dirname(material_path),
        material_path,
        None,
    )
    assert design.mode == 'rect_hole'
    assert design.l_corner is None or design.l_corner in ('tl', 'tr', 'bl', 'br')
    # 无草图结果 → 默认 10% 短边边距
    default_m = min(33.0, 450.0) * 0.10
    assert design.inner_margin_top_cm == pytest.approx(default_m)
