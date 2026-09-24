"""
测试：Stale-Decor 清理块的守卫可达性 + 死字段移除（P0-3）

背景
----
`core/image_ops.py` 的两个 Stale-Decor 清理函数内各有一对「死守卫」：

    (getattr(design, 'lshape_cut_w', 0.0) or 0.0) == 0.0
    (getattr(design, 'lshape_cut_h', 0.0) or 0.0) == 0.0

`CropDesign` 从无 `lshape_cut_w` / `lshape_cut_h` 字段（真名是
`l_cut_w_cm` / `l_cut_h_cm`），故 `getattr` 恒取默认 `0.0` → 条件恒真、等同不存在。
本次修复**删除这两个恒真合取项**（判定结果逐例等价，不改任何功能）。

⚠️ 关键判定依据（本文件用测试固化，防止后人"恢复原意"引入回归）
  原审查报告建议「改用正确字段名 `l_cut_w_cm`」。**该建议是错误的**：
  `l_cut_w_cm` / `l_cut_h_cm` 的默认值是 **15.0 / 10.0**（`core/geometry.py:175-176`），
  且 `main.py` 的 rect_hole 预设保持默认非零 → 改用它会让这两个清理块
  在单洞水池路径上**永不触发**。「非 L 形」的原意已由 `design.mode == 'rect_hole'`
  完全覆盖（L 形仅在 `mode == 'rect_lshape'` 下渲染）。

覆盖
----
  - 两个清理块在单洞水池场景下**仍然可达**（行为锁，修复前后同结论）
  - 其余条件仍然生效（证明守卫并非"删成了恒真"）
  - 死字段确已从生产代码的可执行部分移除（防重命名回归）
"""
import ast
import dataclasses
from pathlib import Path

import numpy as np

from core.geometry import CropDesign
from core.image_ops import (
    _get_inner_pixel_mask,
    _stale_decor_black_border_invalidation,
    _stale_decor_residual_cleaner,
)

FILL = (200, 180, 160)  # 外框花纹采样源颜色（纯色便于确定性断言）
NEAR_BLACK_THRESHOLD = 20


def _production_sources():
    """生产代码范围（不含 tests/scripts/_archive）。"""
    root = Path(__file__).resolve().parent.parent.parent
    files = []
    for layer in ("core", "services", "gui", "workers", "models"):
        files += [p for p in (root / layer).rglob("*.py") if "__pycache__" not in p.parts]
    files.append(root / "main.py")
    return files


def _near_black_count(arr: np.ndarray) -> int:
    """与 image_ops 两个清理块内部一致的近黑判据（max 通道 < 20）。"""
    return int(np.all(arr < NEAR_BLACK_THRESHOLD, axis=2).sum())


def _base_design(mode: str = "rect_hole", **overrides) -> CropDesign:
    """单洞水池：画布 20×15cm，当前内边距 2cm，无外留白。"""
    params = dict(
        canvas_w_cm=20.0, canvas_h_cm=15.0, dpi=150, mode=mode,
        outer_margin_cm=0.0,
        inner_margin_top_cm=2.0, inner_margin_bottom_cm=2.0,
        inner_margin_left_cm=2.0, inner_margin_right_cm=2.0,
    )
    params.update(overrides)
    return CropDesign(**params)


def _paint_stale_black_band(design: CropDesign, canvas: np.ndarray) -> slice:
    """在「旧内挖矩形」下边缘内侧贴一条 10px 黑带（模拟成品图内嵌的旧装饰黑线）。

    旧边距取 1cm（比当前 2cm 小 1cm）→ 旧矩形更大，该黑带落在
    stale 区（旧矩形 ∖ 新矩形）内，且不与左上角 2cm 采样 patch 重叠。

    Returns:
        黑带所在的 y 方向 slice，便于调用方复核。
    """
    cm2px = design.cm2px(1.0)
    outer = design.outer_rect_px()
    old_x0 = int(round(outer.x + 1.0 * cm2px))
    old_w = int(round(outer.w - 2.0 * cm2px))
    old_y1 = int(round(outer.y + outer.h - 1.0 * cm2px))
    band = slice(old_y1 - 10, old_y1)
    canvas[band, old_x0:old_x0 + old_w] = (0, 0, 0)
    return band


class TestDeadGuardRemoval:
    """死字段确已移除，且真实字段未被误删。"""

    def test_cropdesign_has_no_lshape_cut_fields(self):
        names = {f.name for f in dataclasses.fields(CropDesign)}
        assert "lshape_cut_w" not in names, "lshape_cut_w 意外出现，需复核 P0-3 判定"
        assert "lshape_cut_h" not in names
        # 真字段仍在（防误删）
        assert "l_cut_w_cm" in names and "l_cut_h_cm" in names

    def test_no_executable_reference_to_dead_guard_fields(self):
        """AST 级：生产代码的可执行部分不得再引用 lshape_cut_w/h。"""
        offenders = []
        for path in _production_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value in (
                        "lshape_cut_w", "lshape_cut_h"):
                    offenders.append(f"{path.name}:{node.lineno}")
                elif isinstance(node, ast.Attribute) and node.attr in (
                        "lshape_cut_w", "lshape_cut_h"):
                    offenders.append(f"{path.name}:{node.lineno}")
        assert offenders == [], f"死字段仍被可执行代码引用: {offenders}"

    def test_l_cut_defaults_are_non_zero(self):
        """固化 P0-3 的核心判定依据：l_cut_w/h_cm 默认非零。

        正因如此，「把死守卫恢复为 l_cut_w_cm != 0」会让清理块在单洞水池
        路径上永不触发 —— 若将来有人这样改，本用例会立即失败并给出理由。
        """
        d = CropDesign()
        assert d.l_cut_w_cm == 15.0, "默认值已变，请重新评估 P0-3 的修复选择"
        assert d.l_cut_h_cm == 10.0
        # rect_hole 模式下这两个字段不会被清零（main.py 预设即保持默认）
        assert CropDesign(mode="rect_hole").l_cut_w_cm != 0.0
        assert CropDesign(mode="rect_hole").l_cut_h_cm != 0.0


class TestStaleDecorBlackBorderInvalidation:
    """V1 清理块（_stale_decor_black_border_invalidation）可达性。"""

    def test_triggers_for_single_hole_with_changed_margins(self):
        """单洞 + 边距已改：清理块必须触发并抹掉旧装饰黑线。"""
        d = _base_design()
        d._pool_sketch_original_margins_cm = (1.0, 1.0, 1.0, 1.0)
        W, H = d.canvas_w_px, d.canvas_h_px
        canvas = np.full((H, W, 3), FILL, dtype=np.uint8)
        band = _paint_stale_black_band(d, canvas)

        assert _near_black_count(canvas) > 0, "前置条件失败：未成功铺出旧黑线"

        _stale_decor_black_border_invalidation(canvas, d, W, H, True)

        assert _near_black_count(canvas[band]) == 0, (
            "V1 清理块未触发 —— 守卫可达性被改变（P0-3 回归）"
        )
        assert _near_black_count(canvas) == 0

    def test_skipped_for_lshape_mode(self):
        """L 形模式必须整块跳过（mode 守卫才是真正的「非 L 形」闸门）。"""
        d = _base_design(mode="rect_lshape")
        d._pool_sketch_original_margins_cm = (1.0, 1.0, 1.0, 1.0)
        W, H = d.canvas_w_px, d.canvas_h_px
        canvas = np.full((H, W, 3), FILL, dtype=np.uint8)
        _paint_stale_black_band(d, canvas)
        original = canvas.copy()

        _stale_decor_black_border_invalidation(canvas, d, W, H, True)

        assert np.array_equal(canvas, original), "mode 守卫失效：L 形模式不应被清理"

    def test_skipped_when_transparent(self):
        """挖空（transparent）模式仍跳过 —— 证明守卫未退化为恒真。"""
        d = _base_design()
        d.pool_hole_transparent = True
        d._pool_sketch_original_margins_cm = (1.0, 1.0, 1.0, 1.0)
        W, H = d.canvas_w_px, d.canvas_h_px
        canvas = np.full((H, W, 3), FILL, dtype=np.uint8)
        _paint_stale_black_band(d, canvas)
        original = canvas.copy()

        _stale_decor_black_border_invalidation(canvas, d, W, H, True)

        assert np.array_equal(canvas, original)

    def test_skipped_when_no_outer_material(self):
        """无外框素材时仍跳过 —— 证明守卫未退化为恒真。"""
        d = _base_design()
        d._pool_sketch_original_margins_cm = (1.0, 1.0, 1.0, 1.0)
        W, H = d.canvas_w_px, d.canvas_h_px
        canvas = np.full((H, W, 3), FILL, dtype=np.uint8)
        _paint_stale_black_band(d, canvas)
        original = canvas.copy()

        _stale_decor_black_border_invalidation(canvas, d, W, H, False)

        assert np.array_equal(canvas, original)

    def test_skipped_when_margins_unchanged(self):
        """边距未变（差异 ≤ 0.1cm）时跳过 —— 无 stale 区，不应做任何覆盖。"""
        d = _base_design()
        d._pool_sketch_original_margins_cm = (2.0, 2.0, 2.0, 2.0)
        W, H = d.canvas_w_px, d.canvas_h_px
        canvas = np.full((H, W, 3), FILL, dtype=np.uint8)
        _paint_stale_black_band(d, canvas)
        original = canvas.copy()

        _stale_decor_black_border_invalidation(canvas, d, W, H, True)

        assert np.array_equal(canvas, original)


class TestStaleDecorResidualCleaner:
    """V2 清理块（_stale_decor_residual_cleaner）可达性。"""

    def _build_masks(self, d: CropDesign):
        W, H = d.canvas_w_px, d.canvas_h_px
        inner_mask = _get_inner_pixel_mask(d)
        inner = d.inner_rect_px()
        x0, y0 = int(round(inner.x)), int(round(inner.y))
        x1, y1 = int(round(inner.x + inner.w)), int(round(inner.y + inner.h))
        bw = 10
        border_mask = np.zeros((H, W), dtype=bool)
        border_mask[max(0, y0 - bw):min(H, y1 + bw),
                    max(0, x0 - bw):min(W, x1 + bw)] = True
        border_mask[y0:y1, x0:x1] = False
        return W, H, inner_mask, border_mask, (x0, y0, x1, y1)

    def test_triggers_for_single_hole_thin_residual(self):
        """单洞 + 洞口附近细条残留：v2 清理块必须触发并抹掉残留。"""
        d = _base_design()
        W, H, inner_mask, border_mask, (x0, y0, x1, _y1) = self._build_masks(d)
        canvas = np.full((H, W, 3), FILL, dtype=np.uint8)

        # 残留旧黑线：内挖矩形上方 15px 处的 4px 细带
        #   - 在 border_mask 的 20px 膨胀带内（可达 near_hole 判定）
        #   - 不在 border_mask / inner_mask 内（不被排除）
        #   - min(宽,高)=4 ≤ 30px（通过细条过滤）
        line_rows = slice(max(0, y0 - 15), max(0, y0 - 11))
        line_cols = slice(x0 + 50, x1 - 50)
        canvas[line_rows, line_cols] = (0, 0, 0)
        assert _near_black_count(canvas) > 0, "前置条件失败：未铺出残留细条"

        _stale_decor_residual_cleaner(
            canvas, d, W, H, border_mask, inner_mask, True)

        assert _near_black_count(canvas[line_rows, line_cols]) == 0, (
            "V2 清理块未触发 —— 守卫可达性被改变（P0-3 回归）"
        )

    def test_skipped_when_transparent(self):
        """挖空（transparent）模式仍跳过 —— 证明守卫未退化为恒真。"""
        d = _base_design()
        d.pool_hole_transparent = True
        W, H, inner_mask, border_mask, (x0, y0, x1, _y1) = self._build_masks(d)
        canvas = np.full((H, W, 3), FILL, dtype=np.uint8)
        line_rows = slice(max(0, y0 - 15), max(0, y0 - 11))
        line_cols = slice(x0 + 50, x1 - 50)
        canvas[line_rows, line_cols] = (0, 0, 0)
        original = canvas.copy()

        _stale_decor_residual_cleaner(
            canvas, d, W, H, border_mask, inner_mask, True)

        assert np.array_equal(canvas, original)

    def test_skipped_when_lshape_mode(self):
        """L 形模式必须整块跳过（mode 守卫才是真正的「非 L 形」闸门）。"""
        d = _base_design(mode="rect_lshape")
        W, H, inner_mask, border_mask, (x0, y0, x1, _y1) = self._build_masks(d)
        canvas = np.full((H, W, 3), FILL, dtype=np.uint8)
        line_rows = slice(max(0, y0 - 15), max(0, y0 - 11))
        line_cols = slice(x0 + 50, x1 - 50)
        canvas[line_rows, line_cols] = (0, 0, 0)
        original = canvas.copy()

        _stale_decor_residual_cleaner(
            canvas, d, W, H, border_mask, inner_mask, True)

        assert np.array_equal(canvas, original)
