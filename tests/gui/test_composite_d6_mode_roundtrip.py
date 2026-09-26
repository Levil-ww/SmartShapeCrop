"""
tests/gui/test_composite_d6_mode_roundtrip.py
D6「正式生成链路接入」回归锁：模式下拉框第 4 项 + 快照守卫 + 生成路由。

为什么值得测——D6 之前的三处失效都是「静默」的，不抛异常、不崩溃：

  D6-a 模式往返：`_cb_mode` 只有 3 项，复合设计 mode='rect_lshape_hole' 在
       `findData()` 反查时命中 -1 → 静默回落到索引 0（矩形嵌套挖洞）。
       画布渲染的是综合形状，下拉框却写「矩形嵌套挖洞」。

  D6-b 快照守卫：`_collect()` → `apply_ui_snapshot()` 按水池 / L 形控件把复合设计
       改写成水池 / L 形几何，共四处：外框画布、内边距（= 中心洞四边距）、
       l_*（挖角）、pool_hole_transparent（洞填充）。触发路径全是日常操作：
       图层对话框 OK（`_apply_quiet`）、`apply()`、L 形面板「应用」。

  D6-c 生成路由：模式为综合形状时点水池面板的大按钮，必须先走复合专用链路；
       否则 PoolRenderWorker 会把 rect_lshape_hole 当池 / L 形渲染并丢掉中心洞。

本文件把「模式往返 + 四处守卫 + 路由不误伤 L 形链路」钉死。
"""
from __future__ import annotations

import pytest

from core.config import CUT_LOSS_CM
from core.geometry import COMPOSITE_MODE
from models.design_model import DesignModel

_TARGET_NAME = '花型_185x88cm.jpg'
_BASIS_W_CM = 185.0
_BASIS_H_CM = 88.0
# 复合画布 = 外框设计真值 + 1cm 裁剪损耗（与 CompositePanel.get_outer_*_cm 同口径）
_CANVAS_W_CM = _BASIS_W_CM + CUT_LOSS_CM
_CANVAS_H_CM = _BASIS_H_CM + CUT_LOSS_CM
# 中心洞四边距（复合语义：写进 design.inner_margin_*）
_HOLE_MARGINS = {'top': 10.0, 'bottom': 18.0, 'left': 45.0, 'right': 60.0}
_CUT = {'corner': 'tr', 'cut_w_cm': 35.0, 'cut_h_cm': 10.0}
# 水池面板「匹配模板」大按钮的精确文本
_POOL_BTN_TEXT = '🔍 匹配模板 → 解析草图 → 生成预览'


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _composite_params(*, hole_fill: str = 'blank') -> dict:
    """一组合法的复合参数（不经 UI，等价于 CompositePanel.get_composite_params）。"""
    return {
        'canvas_w_cm': _CANVAS_W_CM,
        'canvas_h_cm': _CANVAS_H_CM,
        'outer_margin_cm': 0.0,
        'hole_margin_top_cm': _HOLE_MARGINS['top'],
        'hole_margin_bottom_cm': _HOLE_MARGINS['bottom'],
        'hole_margin_left_cm': _HOLE_MARGINS['left'],
        'hole_margin_right_cm': _HOLE_MARGINS['right'],
        'corner': _CUT['corner'],
        'cut_w_cm': _CUT['cut_w_cm'],
        'cut_h_cm': _CUT['cut_h_cm'],
        'cuts_cm': [dict(_CUT)],
        'hole_fill_mode': hole_fill,
    }


def _clone_composite_design(base, *, hole_fill: str = 'blank'):
    """在 base 的副本上应用复合参数，返回深拷贝快照（不改动 base）。"""
    model = DesignModel(base)
    model.apply_composite_params(_composite_params(hole_fill=hole_fill))
    return model.to_design()


def _setup_composite_panel(cp) -> None:
    """按真实用户操作把 CompositePanel 填成「可生成」状态（外框 + 目标名 + 1 处挖角）。"""
    cp.set_outer_dims(_BASIS_W_CM, _BASIS_H_CM)
    cp._target_edit.setText(_TARGET_NAME)
    enabled, combo, width, height = cp._corner_rows[0]
    enabled.setChecked(True)
    index = combo.findData(_CUT['corner'])
    assert index >= 0, f'挖角角位下拉缺少 {_CUT["corner"]}'
    combo.setCurrentIndex(index)
    width.setValue(_CUT['cut_w_cm'])
    height.setValue(_CUT['cut_h_cm'])


def _user_set_l_panel_cut(lpanel, corner: str, w_cm: float, h_cm: float) -> None:
    """按真实用户操作在 L 形面板第 1 行填挖角。

    必须走控件（而不是 set_lshape_cuts）：一是与用户路径同源，二是触碰控件会让
    `_lshape_params` 惰性初始化——LShapePanel 的 set_lshape_params()/set_lshape_cuts()
    在 `_lshape_params is None` 时会 TypeError（既有缺陷，非 D6 引入，见报告）。
    """
    enabled, combo, width, height = lpanel._corner_rows[0]
    enabled.setChecked(True)
    index = combo.findData(corner)
    assert index >= 0, f'L 形面板挖角角位下拉缺少 {corner}'
    combo.setCurrentIndex(index)
    width.setValue(w_cm)
    height.setValue(h_cm)


def _assert_no_warmup(panel) -> None:
    """用例前提：模板库预热扫描未在运行（否则 _pool_run_generate 会延后到 finished）。"""
    warmup = getattr(panel, '_warmup_worker', None)
    assert warmup is None or not warmup.isRunning(), (
        '用例前提被破坏：模板库预热扫描仍在运行（conftest 已清空默认模板目录）'
    )


def _assert_composite_geometry_intact(d) -> None:
    """复合设计四处几何真值的完整断言（守卫失效时逐项定位）。"""
    assert d.mode == COMPOSITE_MODE
    assert d.canvas_w_cm == pytest.approx(_CANVAS_W_CM)
    assert d.canvas_h_cm == pytest.approx(_CANVAS_H_CM)
    assert d.inner_margin_top_cm == pytest.approx(_HOLE_MARGINS['top'])
    assert d.inner_margin_bottom_cm == pytest.approx(_HOLE_MARGINS['bottom'])
    assert d.inner_margin_left_cm == pytest.approx(_HOLE_MARGINS['left'])
    assert d.inner_margin_right_cm == pytest.approx(_HOLE_MARGINS['right'])
    assert [c['corner'] for c in d.l_cuts_cm] == [_CUT['corner']]
    assert d.l_cut_w_cm == pytest.approx(_CUT['cut_w_cm'])
    assert d.l_cut_h_cm == pytest.approx(_CUT['cut_h_cm'])


# ---------------------------------------------------------------------------
# D6-a 模式下拉框第 4 项
# ---------------------------------------------------------------------------

class TestModeComboCompositeItem:
    def test_combo_has_four_items_and_first_three_unchanged(self, main_window, gui_helpers):
        """第 4 项必须是综合形状，且前 3 项的文本 / userData / 顺序原样保留。"""
        combo = main_window.panel._cb_mode
        assert combo.count() == 4
        assert [combo.itemData(i) for i in range(combo.count())] == [
            'rect_hole', 'rect_lshape', 'ellipse_hole', COMPOSITE_MODE]
        assert gui_helpers.combo_items(combo) == [
            '矩形嵌套挖洞', 'L形挖角', '椭圆挖洞', '综合形状(挖角+中心洞)']

    def test_sync_from_design_roundtrips_composite_and_isolates_l_panel(self, main_window):
        """复合设计回填：下拉框按 userData 命中第 4 项，且不污染 L 形面板的挖角。"""
        panel = main_window.panel
        lpanel = main_window.lshape_panel
        # L 面板上放一份"用户正在编辑"的挖角：两条状态线互不干扰
        _user_set_l_panel_cut(lpanel, 'tl', 20.0, 5.0)

        panel.sync_from_design(_clone_composite_design(panel.design))

        assert panel._cb_mode.currentData() == COMPOSITE_MODE, (
            'findData 反查未命中第 4 项：模式会静默回落到索引 0（D6-a）'
        )
        assert panel._sp_w.value() == pytest.approx(_CANVAS_W_CM)
        assert lpanel.get_corner() == 'tl', 'L 形面板的挖角被复合回填覆盖'
        assert lpanel.get_cut_w_cm() == pytest.approx(20.0)
        assert lpanel.get_cut_h_cm() == pytest.approx(5.0)

    def test_sync_from_design_still_echoes_l_panel_for_rect_lshape(self, main_window):
        """守卫必须是复合专属：rect_lshape 回填仍然写 L 形面板（不过度拦截）。"""
        panel = main_window.panel
        lpanel = main_window.lshape_panel
        _user_set_l_panel_cut(lpanel, 'tl', 20.0, 5.0)
        design = DesignModel(panel.design).to_design()
        design.mode = 'rect_lshape'
        design.l_corner = 'bl'
        design.l_cut_w_cm = 12.0
        design.l_cut_h_cm = 34.0
        design.l_cuts_cm = [{'corner': 'bl', 'cut_w_cm': 12.0, 'cut_h_cm': 34.0}]

        panel.sync_from_design(design)

        assert panel._cb_mode.currentData() == 'rect_lshape'
        assert lpanel.get_corner() == 'bl'
        assert lpanel.get_cut_w_cm() == pytest.approx(12.0)
        assert lpanel.get_cut_h_cm() == pytest.approx(34.0)

    def test_composite_generate_writes_mode_combo_and_canvas(self, main_window, gui_helpers):
        """复合「生成预览」后：下拉框 = 综合形状，画布 SpinBox 跟上设计值。"""
        panel = main_window.panel
        panel.design.dpi = 2.54
        _setup_composite_panel(main_window.composite_panel)

        gui_helpers.assert_button_exists(main_window.composite_panel, '🔍 生成预览').click()

        assert panel._last_generate_source == 'composite'
        assert panel._cb_mode.currentData() == COMPOSITE_MODE
        assert panel._cb_mode.currentIndex() == panel._cb_mode.findData(COMPOSITE_MODE)
        assert panel._sp_w.value() == pytest.approx(_CANVAS_W_CM)
        assert panel._sp_h.value() == pytest.approx(_CANVAS_H_CM)
        assert main_window.canvas._design.mode == COMPOSITE_MODE


# ---------------------------------------------------------------------------
# D6-b 快照守卫：_collect() 不得改写复合几何
# ---------------------------------------------------------------------------

class TestSnapshotGuardOnComposite:
    def test_collect_does_not_rewrite_composite_geometry(self, main_window):
        """水池 / L 形控件全部改成"有攻击性"的值后 _collect()，复合几何必须原封不动。

        守卫失效时：外框被 300/400 覆盖、洞四边距被 1/2/3/4 覆盖、
        l_* 被 L 面板的 tl 20x5 覆盖、pool_hole_transparent 被水池下拉框改成 False。
        """
        panel = main_window.panel
        lpanel = main_window.lshape_panel
        panel.design.dpi = 2.54
        _setup_composite_panel(main_window.composite_panel)
        panel._composite_run_generate()
        design = panel.design
        assert design.pool_hole_transparent is True, '用例前提：空白填充 → 洞透明'

        panel._sp_w.setValue(300.0); panel._sp_h.setValue(400.0)
        panel._sp_mt.setValue(1.0); panel._sp_mb.setValue(2.0)
        panel._sp_ml.setValue(3.0); panel._sp_mr.setValue(4.0)
        lpanel.set_outer_dims(90.0, 60.0)
        _user_set_l_panel_cut(lpanel, 'tl', 20.0, 5.0)
        image_idx = panel._pool_hole_mode.findData('image')
        assert image_idx >= 0
        panel._pool_hole_mode.setCurrentIndex(image_idx)

        panel._collect()

        assert panel.design is design
        _assert_composite_geometry_intact(design)
        assert design.pool_hole_transparent is True, (
            '水池「素材填充」把复合的空白洞改写成了素材洞'
        )

    def test_apply_keeps_composite_geometry_and_image_fill(self, property_panel):
        """apply()（公开入口）同样受守卫保护；素材填充的洞不被水池下拉框改写。"""
        panel = property_panel
        assert panel._lshape_panel is None, '用例前提：独立面板未注入 L 形面板'
        panel.design = _clone_composite_design(panel.design, hole_fill='image')
        idx = panel._cb_mode.findData(COMPOSITE_MODE)
        assert idx >= 0
        panel._cb_mode.setCurrentIndex(idx)
        panel._sp_w.setValue(300.0); panel._sp_h.setValue(400.0)
        panel._sp_mt.setValue(1.0); panel._sp_mb.setValue(2.0)
        panel._sp_ml.setValue(3.0); panel._sp_mr.setValue(4.0)

        panel.apply()

        _assert_composite_geometry_intact(panel.design)
        assert panel.design.pool_hole_transparent is False, (
            '素材填充语义丢失（水池下拉框默认 blank 会把它改成空白）'
        )

    def test_apply_follows_mode_switch_back_to_rect_hole(self, property_panel):
        """守卫按模式生效而非把设计锁死：切回矩形嵌套挖洞后 apply() 必须写回外框。"""
        panel = property_panel
        panel.design = _clone_composite_design(panel.design)
        idx = panel._cb_mode.findData('rect_hole')
        assert idx >= 0
        panel._cb_mode.setCurrentIndex(idx)
        panel._sp_w.setValue(120.0)

        panel.apply()

        assert panel.design.mode == 'rect_hole'
        assert panel.design.canvas_w_cm == pytest.approx(120.0)


# ---------------------------------------------------------------------------
# D6-c 生成路由：水池大按钮在复合模式下走复合链路
# ---------------------------------------------------------------------------

class TestPoolGenerateRouting:
    def test_pool_button_routes_composite_to_composite_link(self, main_window, gui_helpers, monkeypatch):
        """模式 = 综合形状时点水池大按钮：不启动模板匹配 Worker，直接走复合链路。"""
        panel = main_window.panel
        panel.design.dpi = 2.54
        _setup_composite_panel(main_window.composite_panel)
        _assert_no_warmup(panel)
        started = []
        monkeypatch.setattr(panel, '_pool_start_generate_worker',
                            lambda *a, **k: started.append(a))
        idx = panel._cb_mode.findData(COMPOSITE_MODE)
        assert idx >= 0
        panel._cb_mode.setCurrentIndex(idx)

        gui_helpers.assert_button_exists(panel, _POOL_BTN_TEXT).click()

        assert started == [], '综合形状仍走了模板匹配 Worker（中心洞会在渲染时丢失）'
        assert panel._last_generate_source == 'composite'
        assert main_window.canvas._design.mode == COMPOSITE_MODE

    def test_lshape_source_is_not_hijacked(self, main_window, monkeypatch):
        """source='lshape'（L 形面板链路）即使下拉框停在综合形状也必须走 Worker。"""
        panel = main_window.panel
        _setup_composite_panel(main_window.composite_panel)
        _assert_no_warmup(panel)
        started = []
        monkeypatch.setattr(panel, '_pool_start_generate_worker',
                            lambda *a, **k: started.append(a))
        idx = panel._cb_mode.findData(COMPOSITE_MODE)
        assert idx >= 0
        panel._cb_mode.setCurrentIndex(idx)

        panel._pool_run_generate(source='lshape', target_name_override=_TARGET_NAME)

        assert started == [('lshape', _TARGET_NAME)], 'L 形链路被复合分支误伤'

    def test_pool_button_without_composite_panel_falls_back_to_worker(self, main_window, gui_helpers, monkeypatch):
        """未注入 CompositePanel（降级装配）时不得劫持：照旧走 Worker，且不抛异常。"""
        panel = main_window.panel
        monkeypatch.setattr(panel, '_composite_panel', None)
        _assert_no_warmup(panel)
        started = []
        monkeypatch.setattr(panel, '_pool_start_generate_worker',
                            lambda *a, **k: started.append(a))
        idx = panel._cb_mode.findData(COMPOSITE_MODE)
        assert idx >= 0
        panel._cb_mode.setCurrentIndex(idx)

        gui_helpers.assert_button_exists(panel, _POOL_BTN_TEXT).click()

        assert len(started) == 1 and started[0][0] == 'pool'
