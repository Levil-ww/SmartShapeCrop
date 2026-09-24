"""
tests/gui/test_property_panel_write_paths.py
[Fix 2026-09-24 P2-1 + P2-4] 面板 / Worker 的写入路径修复锁定。

P2-1 —— 模式回填硬编码索引
    ``gui/property_panel.PropertyPanel.sync_from_design`` 原用
    ``{'rect_hole': 0, 'rect_lshape': 1, 'ellipse_hole': 2}.get(d.mode, 0)``
    硬编码索引。一旦 combo 增删项或调整顺序而忘记同步该表，模板加载后模式会
    **静默回落**到索引 0（rect_hole），且不报任何错。
    改为按 userData 反查（``findData``），与同文件 line 560 的
    ``findData('rect_lshape')`` 及 ``_on_mode_change`` 的 ``currentData()`` 对称。

P2-4 —— CutRect 截断口径
    ``workers.property_panel_workers.PoolRenderWorker._apply_lshape_params`` 原按
    **总数** ``[:3]`` 截断，与 ``validate()`` 的「同角位 ≤3」不一致。
    改为 ``limit_l_cut_rects_per_anchor()``；对单锚定输入逐例等价。

（P2-4 的 core 侧语义与等价性证明见 tests/core/test_lshape_cut_rect_anchor_limit.py）
"""
from types import SimpleNamespace

import pytest

from core.geometry import CropDesign, CutRect

MODE_TABLE = [('rect_hole', 0), ('rect_lshape', 1), ('ellipse_hole', 2)]


# ---------------------------------------------------------------------------
# P2-1：模式回填
# ---------------------------------------------------------------------------

class TestModeBackfillByUserData:
    @pytest.mark.parametrize('mode,expected', MODE_TABLE)
    def test_known_modes_select_their_combo_entry(self, property_panel, qapp, mode, expected):
        property_panel.sync_from_design(CropDesign(mode=mode))
        qapp.processEvents()
        assert property_panel._cb_mode.currentData() == mode
        assert property_panel._cb_mode.currentIndex() == expected

    @pytest.mark.parametrize('mode,expected', MODE_TABLE)
    def test_equivalent_to_legacy_hardcoded_table(self, property_panel, qapp, mode, expected):
        """旧口径 = 硬编码索引表；新口径 = findData 反查。两者逐例一致。"""
        legacy_idx = {'rect_hole': 0, 'rect_lshape': 1, 'ellipse_hole': 2}.get(mode, 0)
        property_panel.sync_from_design(CropDesign(mode=mode))
        qapp.processEvents()
        assert property_panel._cb_mode.currentIndex() == legacy_idx == expected

    def test_combo_userdata_covers_all_three_modes(self, property_panel):
        combo = property_panel._cb_mode
        assert [combo.itemData(i) for i in range(combo.count())] == [
            m for m, _ in MODE_TABLE]

    def test_unknown_mode_falls_back_to_index_zero(self, property_panel, qapp):
        """反查失败（-1）时必须保留原「回落索引 0」语义，不得变成 -1 / 空选。"""
        design = CropDesign()
        design.mode = 'not_a_real_mode'
        property_panel.sync_from_design(design)
        qapp.processEvents()
        assert property_panel._cb_mode.currentIndex() == 0

    def test_source_no_longer_hardcodes_index_dict(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[2] / 'gui' / 'property_panel.py'
               ).read_text(encoding='utf-8')
        assert "{'rect_hole': 0" not in src, '仍存在硬编码 mode 索引表'
        assert 'findData(d.mode)' in src


# ---------------------------------------------------------------------------
# P2-4：Worker 写入路径
# ---------------------------------------------------------------------------

def _worker(cut_rects, corner='tr', cut_w=20.0, cut_h=14.0):
    from workers.property_panel_workers import PoolRenderWorker
    return PoolRenderWorker(
        matcher=None, template_dir='', target_filename='x',
        lshape_params={
            'corner': corner, 'cut_w_cm': cut_w, 'cut_h_cm': cut_h,
            'cuts_cm': [], 'cut_rects': list(cut_rects),
            'outer_w_cm': 188.5, 'outer_h_cm': 74.0,
        })


def _apply(worker, canvas_w=188.5, canvas_h=74.0):
    design = CropDesign()
    try:
        worker._apply_lshape_params(
            design, SimpleNamespace(path='x'), canvas_w, canvas_h, 1.0)
    finally:
        worker.deleteLater()
    return design


def _bands(anchor, n, w=20.0, h=7.0):
    return [
        {'anchor': anchor, 'offset_x_cm': 0.0, 'offset_y_cm': float(i * (h + 1)),
         'w_cm': w, 'h_cm': h}
        for i in range(n)
    ]


class TestPoolRenderWorkerCutRectAnchorLimit:
    def test_single_anchor_three_identical_to_old_behaviour(self):
        rects = _bands('tr', 3)
        design = _apply(_worker(rects))
        assert len(design.l_cut_rects) == 3
        assert all(isinstance(c, CutRect) for c in design.l_cut_rects)
        assert [c.offset_y_cm for c in design.l_cut_rects] == [0.0, 8.0, 16.0]

    def test_multi_anchor_fourth_rect_kept(self):
        rects = _bands('tr', 3) + _bands('tl', 1)
        design = _apply(_worker(rects))
        assert len(design.l_cut_rects) == 4, '按总数截断会丢掉第 4 条（tl）'
        assert [c.anchor for c in design.l_cut_rects] == ['tr', 'tr', 'tr', 'tl']

    def test_per_anchor_cap_still_enforced(self):
        design = _apply(_worker(_bands('tr', 5)))
        assert len(design.l_cut_rects) == 3

    def test_invalid_entries_still_filtered(self):
        """分组截断不得放松原有的合法性过滤。"""
        rects = _bands('tr', 2) + [
            {'anchor': 'xx', 'offset_x_cm': 0, 'offset_y_cm': 20,
             'w_cm': 5.0, 'h_cm': 7.0},
            {'anchor': 'tr', 'offset_x_cm': 0, 'offset_y_cm': 20,
             'w_cm': 0.0, 'h_cm': 7.0},
        ]
        design = _apply(_worker(rects))
        assert len(design.l_cut_rects) == 2
        assert all(c.anchor == 'tr' for c in design.l_cut_rects)
