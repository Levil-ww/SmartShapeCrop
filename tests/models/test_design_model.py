"""
DesignModel.apply_ui_snapshot 单元测试 [T-01]

覆盖 apply_ui_snapshot() 的多洞布局、素材同步、模式分支等关键业务逻辑。
测试目标：Model 驱动组装的纯值快照 → CropDesign 字段映射正确性。

无需 GUI / Tesseract / 真实素材图：所有输入均为纯值 dict。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.geometry import CropDesign, BorderText
from models.design_model import DesignModel


# ---------------------------------------------------------------------------
# 通用快照构造器
# ---------------------------------------------------------------------------

def _base_snap(**over):
    snap = {
        'canvas_w_cm': 80.0, 'canvas_h_cm': 130.0, 'dpi': 150, 'mode': 'rect_hole',
        'outer_margin_cm': 0.0,
        'inner': {'top': 2.0, 'bottom': 2.0, 'left': 2.0, 'right': 2.0},
        'lshape': None,
        'corners': {'tl': 0.0, 'tr': 0.0, 'bl': 0.0, 'br': 0.0},
        'ellipse': {'diameter_w_cm': 0.0, 'diameter_h_cm': 0.0},
        'colors': {'outer': (255, 255, 255), 'hole': (255, 255, 255)},
        'images': {'outer': None, 'hole': None},
        'text': {'enabled': False, 'text': '', 'font_size_px': 12,
                 'color': (0, 0, 0), 'mirror_bottom': False},
        'hole_mode': None,
        'multihole': None,
    }
    snap.update(over)
    return snap


# ---------------------------------------------------------------------------
# 基础字段映射
# ---------------------------------------------------------------------------

def test_basic_fields():
    m = DesignModel()
    m.apply_ui_snapshot(_base_snap(canvas_w_cm=50.0, canvas_h_cm=70.0, dpi=300))
    assert m.canvas_w_cm == 50.0
    assert m.canvas_h_cm == 70.0
    assert m.dpi == 300


def test_images_empty_string_coerced_to_none():
    m = DesignModel()
    m.apply_ui_snapshot(_base_snap(images={'outer': '', 'hole': ''}))
    assert m.design.outer_bg_image is None
    assert m.design.hole_bg_image is None


# ---------------------------------------------------------------------------
# outer_margin：仅 ellipse_hole 模式写入
# ---------------------------------------------------------------------------

def test_outer_margin_only_for_ellipse():
    m = DesignModel()
    m.apply_ui_snapshot(_base_snap(mode='rect_hole', outer_margin_cm=5.0))
    assert m.design.outer_margin_cm != 5.0  # 未被覆盖

    m.apply_ui_snapshot(_base_snap(mode='ellipse_hole', outer_margin_cm=5.0))
    assert m.design.outer_margin_cm == 5.0


# ---------------------------------------------------------------------------
# inner margins：rect_lshape 模式不写入
# ---------------------------------------------------------------------------

def test_inner_margins_skipped_for_rect_lshape():
    m = DesignModel()
    m.design.inner_margin_top_cm = 99.0
    m.apply_ui_snapshot(_base_snap(mode='rect_lshape',
                                   inner={'top': 5.0, 'bottom': 5.0,
                                          'left': 5.0, 'right': 5.0}))
    assert m.design.inner_margin_top_cm == 99.0  # 未变


# ---------------------------------------------------------------------------
# L 形参数
# ---------------------------------------------------------------------------

def test_lshape_none_reset():
    m = DesignModel()
    m.design.l_corner = 'tl'
    m.design.l_cut_w_cm = 5.0
    m.apply_ui_snapshot(_base_snap(lshape=None))
    assert m.design.l_corner == 'br'
    assert m.design.l_cut_w_cm == 0.0
    assert m.design.l_cut_h_cm == 0.0
    assert m.design.l_cuts_cm == []


def test_lshape_single_cut():
    m = DesignModel()
    m.apply_ui_snapshot(_base_snap(lshape={
        'corner': 'tr', 'cut_w_cm': 10.0, 'cut_h_cm': 5.0, 'cuts_cm': []
    }))
    assert m.design.l_corner == 'tr'
    assert m.design.l_cut_w_cm == 10.0
    assert m.design.l_cut_h_cm == 5.0


def test_lshape_cuts_cm_primary_overrides():
    m = DesignModel()
    m.apply_ui_snapshot(_base_snap(lshape={
        'corner': 'tl', 'cut_w_cm': 1.0, 'cut_h_cm': 1.0,
        'cuts_cm': [
            {'corner': 'br', 'cut_w_cm': 8.0, 'cut_h_cm': 6.0},
            {'corner': 'tr', 'cut_w_cm': 4.0, 'cut_h_cm': 3.0},
        ]
    }))
    # primary = first cut → 覆盖 l_corner/l_cut_w_cm/l_cut_h_cm
    assert m.design.l_corner == 'br'
    assert m.design.l_cut_w_cm == 8.0
    assert m.design.l_cut_h_cm == 6.0
    assert len(m.design.l_cuts_cm) == 2


def test_lshape_cuts_cm_capped_at_four():
    m = DesignModel()
    cuts = [{'corner': 'br', 'cut_w_cm': 1.0, 'cut_h_cm': 1.0} for _ in range(6)]
    m.apply_ui_snapshot(_base_snap(lshape={
        'corner': 'br', 'cut_w_cm': 1.0, 'cut_h_cm': 1.0, 'cuts_cm': cuts
    }))
    assert len(m.design.l_cuts_cm) == 4


# ---------------------------------------------------------------------------
# 文字装饰
# ---------------------------------------------------------------------------

def test_text_enabled_reuses_existing_border_text():
    m = DesignModel()
    existing = BorderText(font_name='x.ttf')
    m.design.border_text = existing
    m.apply_ui_snapshot(_base_snap(text={
        'enabled': True, 'text': 'Hello', 'font_size_px': 20,
        'color': (255, 0, 0), 'mirror_bottom': True
    }))
    assert m.design.border_text is existing
    assert m.design.border_text.text == 'Hello'
    assert m.design.border_text.font_size_px == 20
    assert m.design.border_text.color == (255, 0, 0)
    assert m.design.border_text.mirror_bottom is True


def test_text_disabled_clears():
    m = DesignModel()
    m.design.border_text = BorderText()
    m.apply_ui_snapshot(_base_snap(text={
        'enabled': False, 'text': '', 'font_size_px': 12,
        'color': (0, 0, 0), 'mirror_bottom': False
    }))
    assert m.design.border_text is None


# ---------------------------------------------------------------------------
# 水池模式：rect_lshape 强制 transparent
# ---------------------------------------------------------------------------

def test_rect_lshape_forces_transparent():
    m = DesignModel()
    m.apply_ui_snapshot(_base_snap(mode='rect_lshape', hole_mode='image'))
    assert m.design.pool_hole_transparent is True


def test_hole_mode_blank_clears_inner():
    m = DesignModel()
    m.design.pool_hole_transparent = False
    m.design.pool_inner_material_image = 'x.png'
    m.design.hole_bg_image = 'y.png'
    m.apply_ui_snapshot(_base_snap(hole_mode='blank'))
    assert m.design.pool_hole_transparent is True
    assert m.design.pool_inner_material_image is None
    assert m.design.hole_bg_image is None


def test_hole_mode_image_not_transparent():
    m = DesignModel()
    m.apply_ui_snapshot(_base_snap(mode='rect_hole', hole_mode='image',
                                   images={'outer': None, 'hole': None}))
    assert m.design.pool_hole_transparent is False


def test_hole_mode_none_no_change():
    m = DesignModel()
    m.design.pool_hole_transparent = True
    m.design.pool_inner_material_image = 'keep.png'
    m.apply_ui_snapshot(_base_snap(hole_mode=None,
                                   images={'outer': None, 'hole': 'keep.png'}))
    assert m.design.pool_hole_transparent is True
    assert m.design.pool_inner_material_image == 'keep.png'


# ---------------------------------------------------------------------------
# 外框素材同步
# ---------------------------------------------------------------------------

def test_outer_bg_image_syncs_to_pool_outer():
    m = DesignModel()
    m.design.pool_outer_material_image = None
    m.apply_ui_snapshot(_base_snap(images={'outer': 'F:/x/outer.jpg', 'hole': None}))
    assert m.design.pool_outer_material_image == 'F:/x/outer.jpg'


def test_outer_bg_image_noop_when_equal():
    m = DesignModel()
    m.design.pool_outer_material_image = 'F:/x/outer.jpg'
    m.apply_ui_snapshot(_base_snap(images={'outer': 'F:/x/outer.jpg', 'hole': None}))
    assert m.design.pool_outer_material_image == 'F:/x/outer.jpg'


# ---------------------------------------------------------------------------
# 内挖素材同步（basename 防御）
# ---------------------------------------------------------------------------

def test_inner_material_sync_valid_path():
    m = DesignModel()
    m.design.pool_inner_material_image = None
    # isabs=True → 路径合法 → 同步
    m.apply_ui_snapshot(_base_snap(hole_mode='image',
                                   images={'outer': None, 'hole': 'F:/x/inner.jpg'}))
    assert m.design.pool_inner_material_image == 'F:/x/inner.jpg'


def test_inner_material_basename_rejected():
    m = DesignModel()
    m.design.pool_inner_material_image = 'F:/x/real.png'
    # basename-only → dirname 为空 → 非法 → pool_inner 保留
    m.apply_ui_snapshot(_base_snap(hole_mode='image',
                                   images={'outer': None, 'hole': 'basename.jpg'}))
    assert m.design.pool_inner_material_image == 'F:/x/real.png'


# ---------------------------------------------------------------------------
# 多洞布局
# ---------------------------------------------------------------------------

def _multihole_snap(active=2, holes_w=(10.0, 12.0), holes_h=(20.0, 20.0),
                    gaps=(2.5,), mt=(None, None), mb=(None, None),
                    ml=(None, None), mr=(None, None)):
    return {
        'active_count': active,
        'holes_w': list(holes_w), 'holes_h': list(holes_h),
        'gaps': list(gaps),
        'mt': list(mt), 'mb': list(mb), 'ml': list(ml), 'mr': list(mr),
    }


def test_multihole_inactive_clears():
    m = DesignModel()
    m.design.pool_is_multi_hole = True
    m.design.pool_holes_cm = [{'x_cm': 1.0, 'y_cm': 1.0, 'w_cm': 5.0, 'h_cm': 5.0}]
    m.design.pool_holes_gaps_cm = [1.0]
    m.apply_ui_snapshot(_base_snap(multihole=_multihole_snap(active=1)))
    assert m.design.pool_holes_cm == []
    assert m.design.pool_holes_gaps_cm == []
    assert m.design.pool_is_multi_hole is False


def test_multihole_horizontal_layout():
    m = DesignModel()
    m.design.pool_is_multi_hole = True
    m.apply_ui_snapshot(_base_snap(
        outer_margin_cm=0.0,
        inner={'top': 2.0, 'bottom': 2.0, 'left': 2.0, 'right': 2.0},
        multihole=_multihole_snap()))
    h = m.design.pool_holes_cm
    assert len(h) == 2
    assert h[0]['x_cm'] == 2.0 and h[0]['y_cm'] == 2.0
    assert h[0]['w_cm'] == 10.0 and h[0]['h_cm'] == 20.0
    assert h[0]['ml_cm'] == 2.0 and h[0]['mr_cm'] == 0.0
    # hole1: x = 2 + 10 + 2.5 = 14.5
    assert h[1]['x_cm'] == 14.5 and h[1]['y_cm'] == 2.0
    assert h[1]['ml_cm'] == 0.0 and h[1]['mr_cm'] == 2.0
    assert m.design.pool_holes_gaps_cm == [2.5]


def test_multihole_vertical_layout():
    m = DesignModel()
    m.design.pool_is_multi_hole = True
    setattr(m.design, 'pool_layout_type', 'vertical')
    m.apply_ui_snapshot(_base_snap(
        outer_margin_cm=0.0,
        inner={'top': 2.0, 'bottom': 2.0, 'left': 2.0, 'right': 2.0},
        multihole=_multihole_snap()))
    h = m.design.pool_holes_cm
    assert h[0]['x_cm'] == 2.0 and h[0]['y_cm'] == 2.0
    # hole1: y = 2 + 20 + 2.5 = 24.5; x = ml(1) = 0 (非首洞默认 0)
    assert h[1]['x_cm'] == 0.0 and h[1]['y_cm'] == 24.5


def test_multihole_ui_margin_priority():
    m = DesignModel()
    m.design.pool_is_multi_hole = True
    # 预置 old_holes 作为 fallback
    m.design.pool_holes_cm = [
        {'mt_cm': 7.0},  # hole0: UI None → old=7.0
        {},               # hole1: UI None → old 无 → default=2.0
    ]
    m.apply_ui_snapshot(_base_snap(
        outer_margin_cm=0.0,
        inner={'top': 2.0, 'bottom': 2.0, 'left': 2.0, 'right': 2.0},
        multihole=_multihole_snap(mt=(5.0, None))))
    h = m.design.pool_holes_cm
    # hole0: UI=5.0 优先
    assert h[0]['mt_cm'] == 5.0
    # hole1: UI=None → old 无 → default=2.0
    assert h[1]['mt_cm'] == 2.0


def test_multihole_material_inheritance():
    m = DesignModel()
    m.design.pool_is_multi_hole = True
    m.design.pool_holes_cm = [
        {'inner_material_path': 'a.png', '_cached_inner_image': None,
         '_src_design_w_cm': 58.0, '_src_design_h_cm': 121.0},
        {},
    ]
    m.apply_ui_snapshot(_base_snap(
        outer_margin_cm=0.0,
        inner={'top': 2.0, 'bottom': 2.0, 'left': 2.0, 'right': 2.0},
        multihole=_multihole_snap()))
    h = m.design.pool_holes_cm
    assert h[0]['inner_material_path'] == 'a.png'
    assert h[0]['_src_design_w_cm'] == 58.0
    # hole1 无素材 → 不继承
    assert 'inner_material_path' not in h[1]


def test_multihole_active_count_clamp():
    m = DesignModel()
    m.design.pool_is_multi_hole = True
    # active_count=5 但只有 2 个洞数据 → n_holes = min(5, 2) = 2
    m.apply_ui_snapshot(_base_snap(
        outer_margin_cm=0.0,
        inner={'top': 2.0, 'bottom': 2.0, 'left': 2.0, 'right': 2.0},
        multihole=_multihole_snap(active=5)))
    assert len(m.design.pool_holes_cm) == 2


def test_multihole_inactive_flag_no_write():
    m = DesignModel()
    m.design.pool_is_multi_hole = False
    m.design.pool_holes_cm = [{'x_cm': 99.0}]
    # 即使传入 multihole 数据，pool_is_multi_hole=False → 不写回
    m.apply_ui_snapshot(_base_snap(multihole=_multihole_snap()))
    assert m.design.pool_holes_cm == [{'x_cm': 99.0}]


# ---------------------------------------------------------------------------
# to_design / sync_from_design
# ---------------------------------------------------------------------------

def test_to_design_deepcopy_isolation():
    m = DesignModel()
    m.apply_ui_snapshot(_base_snap(canvas_w_cm=50.0))
    snap = m.to_design()
    snap.canvas_w_cm = 999.0
    assert m.canvas_w_cm == 50.0  # 不影响 model


def test_sync_from_design_replaces_reference():
    m = DesignModel()
    new_design = CropDesign(canvas_w_cm=42.0, canvas_h_cm=60.0)
    m.sync_from_design(new_design)
    assert m.design is new_design
    assert m.canvas_w_cm == 42.0
