"""
tests/gui/test_lshape_panel_lazy_guard.py
D8 回归：LShapePanel.set_lshape_params / set_lshape_cuts 的懒初始化守卫。

缺陷（2026-09-26 D6 实施过程中发现的既有缺陷）：
`_lshape_params` 是懒初始化的（__init__ 置 None，首次参数交互才建 dict）。
但模板菜单回填走 main.py _apply_preset → _apply_design →
PropertyPanel.sync_from_design → set_lshape_params / set_lshape_cuts，
在面板从未交互的 fresh 窗口上直接对 None 写字典：
    TypeError: 'NoneType' object does not support item assignment
异常发生在 Qt 槽内会被 PyQt5 升级为 qFatal() 直接中止进程。

既有先例：_on_staircase_changed / _on_param_changed 均已带 None 守卫，
本文件守护 set_lshape_params / set_lshape_cuts 补上同款守卫后不回退。
"""

import pytest


class TestSetLShapeParamsLazyGuard:
    """set_lshape_params 在 fresh 面板（_lshape_params=None）上可安全回填。"""

    def test_writes_params_on_fresh_panel(self, qapp, lshape_panel):
        assert lshape_panel._lshape_params is None, '前置条件：fresh 面板应为懒初始化 None'
        lshape_panel.set_lshape_params('tr', 35.0, 10.0)
        assert lshape_panel._lshape_params['corner'] == 'tr'
        assert lshape_panel._lshape_params['cut_w_cm'] == pytest.approx(35.0)
        assert lshape_panel._lshape_params['cut_h_cm'] == pytest.approx(10.0)
        assert isinstance(lshape_panel._lshape_params['cuts_cm'], list)

    def test_preserves_existing_dict_entries(self, qapp, lshape_panel):
        """已有 dict（如含 outer_w_cm/outer_h_cm）不被回填清掉。"""
        lshape_panel._lshape_params = {'outer_w_cm': 185.0, 'outer_h_cm': 88.0}
        lshape_panel.set_lshape_params('bl', 20.0, 15.0)
        assert lshape_panel._lshape_params['outer_w_cm'] == 185.0
        assert lshape_panel._lshape_params['outer_h_cm'] == 88.0
        assert lshape_panel._lshape_params['corner'] == 'bl'
        assert lshape_panel._lshape_params['cut_w_cm'] == pytest.approx(20.0)


class TestSetLShapeCutsLazyGuard:
    """set_lshape_cuts 在 fresh 面板上可安全回填。"""

    def test_writes_cuts_on_fresh_panel(self, qapp, lshape_panel):
        assert lshape_panel._lshape_params is None, '前置条件：fresh 面板应为懒初始化 None'
        lshape_panel.set_lshape_cuts([
            {'corner': 'tr', 'cut_w_cm': 35.0, 'cut_h_cm': 10.0},
        ])
        cuts = lshape_panel._lshape_params['cuts_cm']
        assert len(cuts) == 1
        assert cuts[0]['corner'] == 'tr'
        assert cuts[0]['cut_w_cm'] == pytest.approx(35.0)

    def test_empty_cuts_on_fresh_panel(self, qapp, lshape_panel):
        lshape_panel.set_lshape_cuts([])
        assert lshape_panel._lshape_params['cuts_cm'] == []


class TestTemplatePresetBackfill:
    """真实主窗口 + 真实模板菜单（main.py PRESETS）——D8 原始崩溃路径端到端。

    修复前：任一预设均触发 TypeError（qFatal 中止）；修复后：回填真实落盘。
    """

    @pytest.mark.parametrize('preset_index', [0, 1, 2])  # 矩形嵌套挖洞 / L形挖角 / 椭圆嵌套
    def test_apply_preset_backfills_lshape_params(self, qapp, main_window, preset_index):
        import main
        name, factory = main.PRESETS[preset_index]
        main_window._apply_preset(factory, name)
        params = main_window.lshape_panel._lshape_params
        assert params is not None, f'应用预设「{name}」后 L 形面板参数不应为 None'
        assert 'corner' in params, f'应用预设「{name}」后应回填 corner'
        assert 'cuts_cm' in params, f'应用预设「{name}」后应回填 cuts_cm'
