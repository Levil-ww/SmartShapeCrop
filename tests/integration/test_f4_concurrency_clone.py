"""
F4 回归测试：修复 CropDesign 跨线程读写竞争

问题（来自分析报告 F4, core/property_panel.py:PoolRenderWorker + gui/canvas_widget.py）：
    PoolRenderWorker 在子线程构建 design 并写 design._cached_outer_image，
    经信号回传到主线程 -> self.design = design -> canvas.set_design(design)。
    canvas._render_async 把 self._design 按引用传给新建的后台 PreviewRenderWorker，
    后者在子线程 render_design(self._design) 读取它。
    此时主线程仍可原地改 self.design（用户改边距 / _add_border 改 borders 列表 /
    再次 set_design 传入同一对象引用），导致后台渲染线程与主线程同时读写同一
    CropDesign —— 字段撕裂、borders 列表迭代中变更（崩溃）、渲染结果错乱。

修复：
    - core/geometry.py: 新增 CropDesign.clone()（克隆标量 / borders / border_text，
      只读共享 _cached_outer_image）
    - gui/canvas_widget.py: 后台渲染改为传 self._design.clone()，使 PreviewRenderWorker
      持有独立快照，与后续主线程改动解耦

本测试断言：
    1) clone() 对可变字段做深拷贝、对 _cached_outer_image 共享只读引用；
    2) 模拟“后台拿到快照后主线程突变 design”时，render_design(clone) 的尺寸取自
       快照而非突变后的值 —— 即竞争已被消除（这是 F4 的核心不变量）。
"""
import sys
import os
sys.path.insert(0, '.')

import pytest

from core.geometry import CropDesign, BorderLayer, BorderText
from core.image_ops import render_design


def _make_minimal_design(canvas_w_cm=10.0, canvas_h_cm=10.0, dpi=10):
    d = CropDesign()
    d.canvas_w_cm = canvas_w_cm
    d.canvas_h_cm = canvas_h_cm
    d.dpi = dpi
    d.mode = 'rect_hole'
    # 清空一切外部素材，确保 render_design 纯色合成、无需任何磁盘资源
    d.outer_bg_image = None
    d.hole_bg_image = None
    d.pool_outer_material_image = None
    d._cached_outer_image = None
    d.border_text = None
    return d


# ---------------------------------------------------------------------------
# 1) clone() 隔离性
# ---------------------------------------------------------------------------

def test_clone_is_independent_for_scalars():
    d = _make_minimal_design()
    d.canvas_w_cm = 12.5
    d.dpi = 300
    d.inner_margin_top_cm = 3.3
    c = d.clone()
    # 修改原始对象不应影响克隆
    d.canvas_w_cm = 99.0
    d.dpi = 72
    d.inner_margin_top_cm = 0.0
    assert c.canvas_w_cm == 12.5
    assert c.dpi == 300
    assert c.inner_margin_top_cm == 3.3


def test_clone_copies_borders_independently():
    d = _make_minimal_design()
    original_len = len(d.borders)
    assert original_len >= 1
    c = d.clone()
    # 在原始对象上增删 / 替换 borders 元素
    d.borders.append(BorderLayer(offset_cm=5.0, fill_type='solid', color=(1, 2, 3)))
    d.borders[0] = BorderLayer(offset_cm=9.0, fill_type='image', image_path='x.jpg')
    # 克隆的 borders 不受影响（长度与首个元素都被独立复制）
    assert len(c.borders) == original_len
    assert c.borders[0].offset_cm != 9.0
    # 克隆内部元素本身也是新对象（修改它不影响原始）
    c.borders[0].offset_cm = 123.0
    assert d.borders[0].offset_cm == 9.0


def test_clone_copies_border_text_independently():
    d = _make_minimal_design()
    d.border_text = BorderText(text="hello")
    c = d.clone()
    d.border_text.text = "mutated"
    d.border_text.font_size_px = 999
    assert c.border_text is not d.border_text
    assert c.border_text.text == "hello"
    assert c.border_text.font_size_px != 999


def test_clone_shares_readonly_cached_image():
    d = _make_minimal_design()
    sentinel = object()  # 任意对象，仅用于身份比较
    d._cached_outer_image = sentinel
    c = d.clone()
    # _cached_outer_image 必须共享引用（避免每次渲染复制大图），且只读使用
    assert c._cached_outer_image is d._cached_outer_image
    # 克隆是可独立对象：改标量不影响彼此（再次确认整体独立）
    d.canvas_w_cm = 77.0
    assert c.canvas_w_cm != 77.0


def test_clone_is_fully_usable_design():
    d = _make_minimal_design()
    c = d.clone()
    assert isinstance(c, CropDesign)
    # clone 后仍可正常完成像素级换算
    assert c.canvas_w_px == d.canvas_w_px
    assert c.canvas_h_px == d.canvas_h_px


# ---------------------------------------------------------------------------
# 2) 竞争消除的核心不变量：后台拿到快照后，主线程突变不影响在途渲染
# ---------------------------------------------------------------------------

def test_background_render_uses_clone_snapshot_not_mutated_original():
    """
    模拟 F4 的真实时序：
      主线程：handoff = design.clone()   # 交给后台
      后台  ：render_design(handoff)      # 在子线程渲染快照
      主线程（并发）：design.canvas_w_cm 突变 / borders 增删 / border_text 重建
    断言：后台渲染结果取自“快照时刻”的 design，尺寸与突变后的 design 不同。
    """
    design = _make_minimal_design(canvas_w_cm=10.0, canvas_h_cm=10.0, dpi=10)
    # 快照：模拟 canvas 把 clone 交给 PreviewRenderWorker
    handoff = design.clone()

    # —— 主线程在后台渲染“进行中”突变 self.design ——
    design.canvas_w_cm = 50.0          # 39px -> 197px
    design.canvas_h_cm = 50.0
    design.borders.append(BorderLayer(offset_cm=2.0, fill_type='solid', color=(9, 9, 9)))
    design.border_text = BorderText(text="late edit")

    # 后台渲染使用快照（而非被突变的原始对象）
    img = render_design(handoff, quality='preview')

    # 快照尺寸：10cm @10dpi -> 39px；突变后：50cm -> 197px
    w_snap, h_snap = handoff.canvas_w_px, handoff.canvas_h_px
    assert (w_snap, h_snap) == (39, 39), f"快照尺寸异常: {(w_snap, h_snap)}"
    assert img.size == (39, 39), (
        f"后台渲染读了被突变的原始对象！尺寸应为快照 (39,39)，实际 {img.size}"
    )
    # 快照本身未被后台渲染改动
    assert len(handoff.borders) == 3  # 原始设计默认 3 层边框


def test_without_clone_original_mutation_would_corrupt_render():
    """
    对照组：若 canvas 仍按“引用”传 design（F4 修复前的旧行为），
    主线程突变会让在途的后台渲染读到错误尺寸——说明 clone 是必要的。
    该测试仅用于文档化“为什么必须 clone”，断言旧行为确实会出错。
    """
    design = _make_minimal_design(canvas_w_cm=10.0, canvas_h_cm=10.0, dpi=10)
    # 旧行为：直接传引用（非 clone）
    handoff_ref = design
    design.canvas_w_cm = 50.0
    design.canvas_h_cm = 50.0
    img = render_design(handoff_ref, quality='preview')
    # 引用语义下，渲染会反映突变后的值（197px），即竞争的确存在
    assert img.size == (197, 197)


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
