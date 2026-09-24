"""
tests/core/test_debug_residue_removed.py
[Fix 2026-09-24 P1-4 + P2-5] L 形渲染热路径上的调试残留清理。

被清理的两类残留（均在 ``core/image_ops.py``）
-------------------------------------------
1. **8 处无条件 ``print(..., flush=True)``** —— 原为 2026-09-10 定位 cut 坐标的临时
   打印，却挂在 ``render_design`` 的每次 L 形渲染热路径上，且带 ``flush=True``
   强制刷盘。打包为 ``--windowed``（无控制台）时 stdout 不可见，纯属性能损耗与死代码。
2. **``_dbg = False`` 「临时开关」及其实参传递链** —— 贯穿 ``render_design`` 与
   4 个辅助函数（``_render_outer_background`` / ``_apply_lshape_bg_overlay`` /
   ``_render_lshape_cut`` / ``_apply_unified_black_border`` /
   ``_lshape_border_completion``），注释自称「问题定位后改 False」却遗留 14 天。
   其下全部 ``if _dbg:`` 分支恒不可达。

「不改功能」的论证
-----------------
被删除的 ``if _dbg:`` 块内**只有 logger 输出、print 与纯局部变量赋值**
（无 return、无画布写入、无外部副作用）；且守卫是字面量 ``False``，等价于删除。
本文件用「源码级锁定（AST）+ 渲染行为锁定」双重防回归。
"""
import ast
import inspect
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from core.image_ops import (
    _apply_lshape_bg_overlay,
    _apply_unified_black_border,
    _lshape_border_completion,
    _render_lshape_cut,
    _render_outer_background,
    render_design,
)

IMAGE_OPS = PROJECT_ROOT / 'core' / 'image_ops.py'
HELPERS = (
    _render_outer_background,
    _apply_lshape_bg_overlay,
    _render_lshape_cut,
    _apply_unified_black_border,
    _lshape_border_completion,
)


# ---------------------------------------------------------------------------
# 1) 源码级锁定
# ---------------------------------------------------------------------------

class TestNoDebugResidueAtSourceLevel:
    @staticmethod
    def _tree():
        return ast.parse(IMAGE_OPS.read_text(encoding='utf-8'))

    def test_no_dbg_identifier_in_code(self):
        """AST 只看可执行代码，天然排除注释与文档串。"""
        found = []
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Name) and node.id == '_dbg':
                found.append(node.lineno)
            elif isinstance(node, ast.arg) and node.arg == '_dbg':
                found.append(node.lineno)
        assert found == [], f'core/image_ops.py 仍存在 _dbg 引用（行 {found}）'

    def test_no_bare_print_call(self):
        found = []
        for node in ast.walk(self._tree()):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'print'):
                found.append(node.lineno)
        assert found == [], f'core/image_ops.py 仍存在裸 print 调用（行 {found}）'

    def test_no_debug_rd_logger_tag(self):
        """用 AST 常量而非原文扫描 —— 修复说明注释里会引述被删代码的标签。"""
        found = [
            node.lineno for node in ast.walk(self._tree())
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and 'DEBUG-RD' in node.value
        ]
        assert found == [], f'仍存在 [DEBUG-RD] 调试日志字符串（行 {found}）'

    @pytest.mark.parametrize('func', HELPERS, ids=lambda f: f.__name__)
    def test_helper_signatures_have_no_dbg(self, func):
        assert '_dbg' not in inspect.signature(func).parameters

    def test_render_design_body_has_no_dbg_identifier(self):
        """同样走 AST：``inspect.getsource`` 会把说明注释一并带出，不可用于此断言。"""
        tree = ast.parse(IMAGE_OPS.read_text(encoding='utf-8'))
        target = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == 'render_design')
        found = [
            node.lineno for node in ast.walk(target)
            if (isinstance(node, ast.Name) and node.id == '_dbg')
            or (isinstance(node, ast.arg) and node.arg == '_dbg')
        ]
        assert found == [], f'render_design 内仍引用 _dbg（行 {found}）'


# ---------------------------------------------------------------------------
# 2) 渲染行为锁定（L 形 + 池素材路径会走到被清理的代码块）
# ---------------------------------------------------------------------------

@pytest.fixture()
def pool_material(tmp_path):
    """一张带黑描边的小素材图，使其被识别为「池素材」。"""
    arr = np.full((60, 90, 3), (200, 180, 120), dtype=np.uint8)
    arr[:3, :] = 0
    arr[-3:, :] = 0
    arr[:, :3] = 0
    arr[:, -3:] = 0
    path = tmp_path / 'material.png'
    Image.fromarray(arr, 'RGB').save(path)
    return str(path)


def _design(pool_material, cut_rects=None, corner='tr', cut_w=10.0, cut_h=5.0):
    from core.geometry import CropDesign, CutRect
    d = CropDesign(mode='rect_lshape', canvas_w_cm=8.0, canvas_h_cm=10.0, dpi=150)
    d.outer_margin_cm = 0.0
    d.inner_margin_top_cm = d.inner_margin_bottom_cm = 0.0
    d.inner_margin_left_cm = d.inner_margin_right_cm = 0.0
    d.corner_tl_cm = d.corner_tr_cm = d.corner_bl_cm = d.corner_br_cm = 0.0
    d.l_corner = corner
    d.l_cut_w_cm = cut_w
    d.l_cut_h_cm = cut_h
    d.l_cut_rects = [CutRect(**c) for c in (cut_rects or [])]
    d.pool_outer_material_image = pool_material
    return d


def _strip(anchor, offset_y, w, h):
    return {'anchor': anchor, 'offset_x_cm': 0.0, 'offset_y_cm': offset_y,
            'w_cm': w, 'h_cm': h}


class TestRenderBehaviourUnchanged:
    def test_lshape_pool_render_emits_no_stdout(self, pool_material, capsys):
        """被清理的 print 正位于本路径上 —— 现在必须零 stdout 输出。"""
        d = _design(pool_material, cut_rects=[_strip('tr', 0.0, 10.0, 5.0)])
        img = render_design(d, quality='export', skip_validate=True)
        assert img.size == (d.canvas_w_px, d.canvas_h_px)
        captured = capsys.readouterr()
        assert captured.out == '', f'渲染过程仍有 stdout 输出：{captured.out[:200]!r}'

    def test_render_is_deterministic(self, pool_material):
        d1 = _design(pool_material, cut_rects=[_strip('tr', 0.0, 10.0, 5.0)])
        d2 = _design(pool_material, cut_rects=[_strip('tr', 0.0, 10.0, 5.0)])
        a = render_design(d1, quality='export', skip_validate=True)
        b = render_design(d2, quality='export', skip_validate=True)
        assert a.tobytes() == b.tobytes()

    def test_staircase_geometry_still_applied(self, pool_material):
        """阶梯 cut_rects 仍在渲染中生效：与单级挖角产出不同的画面。"""
        single = render_design(
            _design(pool_material, cut_rects=None, cut_w=10.0, cut_h=5.0),
            quality='export', skip_validate=True)
        stair = render_design(
            _design(pool_material, cut_rects=[
                _strip('tr', 0.0, 10.0, 5.0),
                _strip('tr', 6.0, 10.0, 5.0),
            ]),
            quality='export', skip_validate=True)
        assert single.tobytes() != stair.tobytes(), '阶梯 cut_rects 未进入渲染几何'
