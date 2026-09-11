"""
tests/gui/test_property_panel.py
水池设计器面板（PropertyPanel）的初始状态与轻量交互。

覆盖思路：只验证"UI 契约"——控件在不在、取值范围对不对、切换会不会崩，
不触碰业务流程（不做真实渲染、不读网络模板库）。
"""
from PyQt5.QtWidgets import QComboBox

EXPECTED_MODES = ['矩形嵌套挖洞', 'L形挖角', '椭圆挖洞']
EXPECTED_FILL_MODES = ['✂️ 空白(挖去不留白)', '🖼 素材填充（花型匹配填充）']


class TestPropertyPanelInitialState:

    def test_mode_combo_offers_all_design_modes(self, property_panel, gui_helpers):
        combo = gui_helpers.find_combo_containing(property_panel, EXPECTED_MODES[0])
        assert combo is not None, (
            f'未找到含「{EXPECTED_MODES[0]}」的模式下拉。'
            f'现有下拉：{[gui_helpers.combo_items(c) for c in property_panel.findChildren(QComboBox)]}'
        )
        items = gui_helpers.combo_items(combo)
        for mode in EXPECTED_MODES:
            assert mode in items, f'模式下拉缺少「{mode}」，现有：{items}'

    def test_fill_mode_combo_present(self, property_panel, gui_helpers):
        combo = gui_helpers.find_combo_containing(property_panel, EXPECTED_FILL_MODES[0])
        assert combo is not None, '未找到挖洞填充方式下拉'
        items = gui_helpers.combo_items(combo)
        for m in EXPECTED_FILL_MODES:
            assert m in items, f'填充方式下拉缺少「{m}」，现有：{items}'

    def test_dpi_spinbox_range(self, property_panel, gui_helpers):
        """DPI 控件范围 72~600（低于 72 打印会糊，高于 600 无意义且极慢）。"""
        spin = gui_helpers.find_spinbox(property_panel, 72, 600)
        assert spin is not None, '未找到范围为 [72, 600] 的 DPI 控件'
        assert 72 <= spin.value() <= 600, f'DPI 默认值 {spin.value()} 超出合法范围'

    def test_canvas_size_spinboxes_in_range(self, property_panel, gui_helpers):
        """画布宽高控件范围 5~500 cm，默认值必须落在范围内。"""
        spin = gui_helpers.find_spinbox(property_panel, 5.0, 500.0)
        assert spin is not None, '未找到范围为 [5, 500] 的画布尺寸控件'
        assert 5.0 <= spin.value() <= 500.0, f'画布尺寸默认值 {spin.value()} 越界'

    def test_key_buttons_exist(self, property_panel, gui_helpers):
        for text in ['上传草图…', '清除草图', '＋ 添加洞', '－ 删除末洞',
                     '+ 加一层', '- 删一层']:
            gui_helpers.assert_button_exists(property_panel, text)

    def test_generate_button_exists(self, property_panel, gui_helpers):
        gui_helpers.assert_button_exists(property_panel, '🔍 匹配模板 → 解析草图 → 生成预览')


class TestPropertyPanelInteraction:
    """轻量交互：切换下拉不应抛异常（异常会被 Qt 吞掉，需 processEvents 后确认存活）。"""

    def test_switching_design_mode_does_not_crash(self, qapp, property_panel, gui_helpers):
        combo = gui_helpers.find_combo_containing(property_panel, EXPECTED_MODES[0])
        assert combo is not None
        original = combo.currentIndex()
        for idx in range(combo.count()):
            combo.setCurrentIndex(idx)
            qapp.processEvents()
            assert combo.currentIndex() == idx, (
                f'切换到第 {idx} 项失败（可能内部抛异常被 Qt 吞掉）'
            )
        combo.setCurrentIndex(original)
        qapp.processEvents()

    def test_switching_fill_mode_does_not_crash(self, qapp, property_panel, gui_helpers):
        combo = gui_helpers.find_combo_containing(property_panel, EXPECTED_FILL_MODES[0])
        assert combo is not None
        for idx in range(combo.count()):
            combo.setCurrentIndex(idx)
            qapp.processEvents()
        assert combo.count() >= 2, '填充方式至少应有 2 项'

    def test_changing_dpi_does_not_crash(self, qapp, property_panel, gui_helpers):
        spin = gui_helpers.find_spinbox(property_panel, 72, 600)
        assert spin is not None
        old = spin.value()
        spin.setValue(300)
        qapp.processEvents()
        assert spin.value() == 300
        spin.setValue(old)
        qapp.processEvents()
