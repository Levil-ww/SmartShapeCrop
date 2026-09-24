"""
tests/core/test_lshape_cut_rect_anchor_limit.py
[Fix 2026-09-24 P2-4] CutRect 截断口径：按锚定角分组（取代历史的按总数 ``[:3]``）。

缺陷本质
--------
``CropDesign._validate_l_cut_rects()`` 的约束是「**同一锚定角** ≤ 3 级」，
而两个写入端
  - ``models.design_model.DesignModel.apply_ui_snapshot``
  - ``workers.property_panel_workers.PoolRenderWorker._apply_lshape_params``
却按**总数** ``[:3]`` 截断 —— 两套口径不一致。对 validate() 允许的
「tr×3 + tl×1」（合计 4 条）输入，第 4 条会被**静默丢弃**，用户看不到任何报错。

为什么这不是功能变更
--------------------
UI（``LShapePanel.get_cut_rects_cm``）与草图解析器
（``lshape_sketch_parser._convert_stepped_to_cut_rects``）的**全部可达路径**都只产出
单一锚定角（条带 ``'anchor'`` 恒为同一个角），故本文件第 2 组用例逐例锁定
「单锚定输入下新实现与旧 ``[:3]`` 完全等价」。

本文件锁住四件事
----------------
1. ``limit_l_cut_rects_per_anchor()`` 的分组语义 / 顺序 / 类型无关；
2. 与旧 ``[:3]`` 在单锚定输入上的逐例等价性（可达行为不变）；
3. ``validate()`` 的每角上限与截断逻辑共用同一常量，不再各自硬编码；
4. 两个写入端已不再按总数截断，且 DesignModel 写入路径行为符合预期。
"""
import ast
import re
from collections import Counter
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from core.geometry import (
    MAX_L_CUT_RECTS_PER_ANCHOR,
    CropDesign,
    CutRect,
    limit_l_cut_rects_per_anchor,
)


def _cr(anchor, offset_y=0.0, offset_x=0.0, w=10.0, h=5.0):
    return CutRect(anchor=anchor, offset_x_cm=offset_x, offset_y_cm=offset_y,
                   w_cm=w, h_cm=h)


def _bands(anchor, n, w=10.0, h=5.0):
    """同一锚定角的 n 条互不重叠的水平条带（dict 形态，与 UI/JSON 一致）。"""
    return [
        {'anchor': anchor, 'offset_x_cm': 0.0, 'offset_y_cm': float(i * (h + 1)),
         'w_cm': w, 'h_cm': h}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# 1) 辅助函数语义
# ---------------------------------------------------------------------------

class TestHelperSemantics:
    def test_constant_is_three(self):
        assert MAX_L_CUT_RECTS_PER_ANCHOR == 3

    @pytest.mark.parametrize('value', [None, [], ()])
    def test_empty_inputs_return_empty_list(self, value):
        assert limit_l_cut_rects_per_anchor(value) == []

    @pytest.mark.parametrize('n', [0, 1, 2, 3, 4, 5, 8])
    def test_single_anchor_equivalent_to_total_slice(self, n):
        """单锚定输入（全部可达路径）下与旧 ``[:3]`` 逐例等价。"""
        cuts = _bands('tr', n)
        assert limit_l_cut_rects_per_anchor(cuts) == cuts[:MAX_L_CUT_RECTS_PER_ANCHOR]

    def test_multi_anchor_keeps_every_group(self):
        cuts = _bands('tr', 3) + _bands('tl', 1)
        picked = limit_l_cut_rects_per_anchor(cuts)
        assert len(picked) == 4, '旧实现 [:3] 会丢掉 tl 那一条'
        assert [c['anchor'] for c in picked] == ['tr', 'tr', 'tr', 'tl']
        assert picked == cuts

    def test_cap_applies_per_group_not_in_total(self):
        cuts = _bands('tr', 4) + _bands('tl', 4) + _bands('br', 2)
        picked = limit_l_cut_rects_per_anchor(cuts)
        assert Counter(c['anchor'] for c in picked) == {'tr': 3, 'tl': 3, 'br': 2}
        assert len(picked) == 8

    def test_total_may_exceed_cap(self):
        """总数可以超过 3（每角各自 ≤3）—— 这正是与旧口径的分歧点。"""
        cuts = _bands('tr', 3) + _bands('bl', 3)
        assert len(limit_l_cut_rects_per_anchor(cuts)) == 6

    def test_order_and_object_identity_preserved(self):
        cuts = [_cr('tr'), _cr('tl'), _cr('tr'), _cr('tl'), _cr('tr')]
        picked = limit_l_cut_rects_per_anchor(cuts)
        assert len(picked) == 5
        assert all(a is b for a, b in zip(picked, cuts))

    def test_keeps_first_n_per_anchor_for_interleaved_input(self):
        cuts = [{'anchor': a} for a in ['tr', 'tl', 'tr', 'bl', 'tr', 'tl', 'tr']]
        picked = limit_l_cut_rects_per_anchor(cuts)
        assert [c['anchor'] for c in picked] == ['tr', 'tl', 'tr', 'bl', 'tr', 'tl']

    def test_accepts_cutrect_instances(self):
        cuts = [_cr('tr'), _cr('tr'), _cr('tr'), _cr('tr')]
        picked = limit_l_cut_rects_per_anchor(cuts)
        assert len(picked) == 3
        assert picked[0] is cuts[0]

    def test_custom_cap(self):
        cuts = _bands('tr', 5)
        assert len(limit_l_cut_rects_per_anchor(cuts, max_per_anchor=1)) == 1
        assert limit_l_cut_rects_per_anchor(cuts, max_per_anchor=0) == []


# ---------------------------------------------------------------------------
# 2) validate() 与截断共用同一上限
# ---------------------------------------------------------------------------

class TestValidateSharesTheSameCap:
    @staticmethod
    def _design(rects):
        d = CropDesign(mode='rect_lshape', canvas_w_cm=80.0, canvas_h_cm=130.0, dpi=150)
        d.outer_margin_cm = 0.0
        d.inner_margin_top_cm = d.inner_margin_bottom_cm = 0.0
        d.inner_margin_left_cm = d.inner_margin_right_cm = 0.0
        d.l_cut_rects = list(rects)
        return d

    def _stack(self, anchor, n, w=10.0, h=5.0):
        return [_cr(anchor, offset_y=float(i * (h + 1)), w=w, h=h) for i in range(n)]

    def test_same_anchor_four_levels_rejected(self):
        d = self._design(self._stack('tr', 4))
        with pytest.raises(ValueError, match=f'最多支持 {MAX_L_CUT_RECTS_PER_ANCHOR} 级'):
            d.validate()

    def test_same_anchor_three_levels_accepted(self):
        self._design(self._stack('tr', 3)).validate()

    def test_multi_anchor_four_total_is_legal(self):
        """tr×3 + tl×1 合计 4 条 —— validate() 允许，故写入端不应按总数截断。"""
        d = self._design(self._stack('tr', 3) + self._stack('tl', 1))
        d.validate()
        assert len(d.l_cut_rects) == 4


# ---------------------------------------------------------------------------
# 3) 源码级防回归：写入端不得再按总数截断
# ---------------------------------------------------------------------------

WRITE_PATH_FILES = [
    PROJECT_ROOT / 'models' / 'design_model.py',
    PROJECT_ROOT / 'workers' / 'property_panel_workers.py',
]


class TestWritePathsNoLongerTruncateByTotal:
    @pytest.mark.parametrize('path', WRITE_PATH_FILES, ids=lambda p: p.name)
    def test_no_total_slice(self, path):
        src = path.read_text(encoding='utf-8')
        assert not re.search(r'\]\s*\[\s*:\s*3\s*\]', src), (
            f'{path.name} 仍存在按总数 [:3] 截断 —— '
            f'应改用 limit_l_cut_rects_per_anchor()，与 validate() 的每角 ≤3 对齐')

    @pytest.mark.parametrize('path', WRITE_PATH_FILES, ids=lambda p: p.name)
    def test_imports_helper(self, path):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported |= {a.name for a in node.names}
        assert 'limit_l_cut_rects_per_anchor' in imported, (
            f'{path.name} 未从 core.geometry 导入分组截断辅助函数')


# ---------------------------------------------------------------------------
# 4) DesignModel 写入路径行为
# ---------------------------------------------------------------------------

def _base_snap(**over):
    """与 tests/gui/test_lshape_panel_staircase.py::_base_snap 保持同构。"""
    snap = {
        'canvas_w_cm': 80.0, 'canvas_h_cm': 130.0, 'dpi': 150, 'mode': 'rect_lshape',
        'outer_margin_cm': 0.0,
        'inner': {'top': 0.0, 'bottom': 0.0, 'left': 0.0, 'right': 0.0},
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


class TestDesignModelWritePath:
    @staticmethod
    def _apply(cut_rects):
        from models.design_model import DesignModel
        m = DesignModel()
        m.apply_ui_snapshot(_base_snap(lshape={
            'corner': 'tr', 'cut_w_cm': 20.0, 'cut_h_cm': 7.0,
            'cuts_cm': [], 'cut_rects': list(cut_rects),
        }))
        return m.design

    def test_single_anchor_three_identical_to_old_behaviour(self):
        rects = _bands('tr', 3, w=20.0, h=7.0)
        kept = self._apply(rects).l_cut_rects
        assert len(kept) == 3
        assert [(c.anchor, c.offset_y_cm, c.w_cm, c.h_cm) for c in kept] == [
            (r['anchor'], r['offset_y_cm'], r['w_cm'], r['h_cm']) for r in rects]

    def test_multi_anchor_fourth_rect_kept(self):
        rects = _bands('tr', 3, w=20.0, h=7.0) + _bands('tl', 1, w=20.0, h=7.0)
        kept = self._apply(rects).l_cut_rects
        assert len(kept) == 4, '按总数截断会丢掉第 4 条（tl）'
        assert kept[-1].anchor == 'tl'
        assert all(isinstance(c, CutRect) for c in kept)

    def test_per_anchor_cap_still_enforced(self):
        rects = _bands('tr', 5, w=20.0, h=7.0)
        assert len(self._apply(rects).l_cut_rects) == MAX_L_CUT_RECTS_PER_ANCHOR
