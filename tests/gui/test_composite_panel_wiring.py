"""
tests/gui/test_composite_panel_wiring.py
综合形状（CompositePanel）装配接线 + 端到端渲染的回归锁。

为什么值得测：阶段 0 修掉的三类缺陷全部是「静默失效」——
  D1 面板未被 PropertyPanel 注册：13 条细粒度信号 receivers=0，
     点「生成预览」「上传草图」都没反应，共享画布永远空白；
  D2 识别基准隐式取面板默认外框（4cm）：整张草图被等比缩到 4cm，
     产出「看似成功」的噪声参数（洞宽 ≈ 2.7cm 而不是 80cm）；
  D4/D5 识别结果只回填 6/10 项，且 to_crop_design() 的 canvas / l_cut 口径不对，
     过不了 CropDesign.validate()。

三类问题都不抛异常、也不会崩溃，只在真实装配（MainWindow + 双面板）下驱动真实
用户路径才会暴露：填目标文件名 → 上传草图 → 自动识别 → 10 项参数回显 → 生成预览
→ 画布渲染出 rect_lshape_hole 设计。本文件把这条路径钉死，任何一环断掉都会红。
"""
from __future__ import annotations

import os
import time

import pytest
from PIL import Image, ImageDraw

from gui.composite_panel import CompositePanel
from gui.lshape_panel_bridge import LShapePanelBridge

# LShapePanel 对外暴露的 13 条细粒度信号（CompositePanel 继承同构，LShapePanel 零改动）
_LSHAPE_SIGNALS = (
    'lshape_params_changed', 'lshape_applied',
    'lshape_recognize_started', 'lshape_recognize_finished',
    'sketch_pick_requested', 'sketch_clear_requested', 'sketch_view_requested',
    'sketch_load_requested',
    'target_changed', 'target_pick_requested', 'target_clear_requested',
    'generate_requested', 'save_requested',
)
# 综合面板专有信号（Bridge 不覆盖，由 PropertyPanel.set_composite_panel 单独连接）
_COMPOSITE_SIGNALS = ('composite_params_changed', 'composite_recognize_finished')

# 合成草图：5 px/cm，925×440 px ↔ 185×88 cm 外框真值（与报告参考草图等价）
_SKETCH_SIZE_PX = (925, 440)
_TARGET_NAME = '花型_185x88cm.jpg'
_BASIS_W_CM = 185.0
_BASIS_H_CM = 88.0
# 画布值 = 外框真值 + 1cm 损耗；dpi 取 2.54 → 1px/cm，渲染同步且足够小
_CANVAS_PX = (186, 89)
# 识别回显容差：期望值 = 草图标注真值（洞 80×60、边距 45/60/10/18、挖角 35×10）
_ECHO_TOL_CM = 2.0


@pytest.fixture(autouse=True)
def _hermetic_ocr(monkeypatch):
    """屏蔽 OCR 扫描，强制走像素比例回退。

    本机 venv 已装 Tesseract，不屏蔽则识别结果依赖本机 OCR 环境；
    合成草图无文字标注，屏蔽后 parse_lshape_sketch 的挖角来源稳定为 pixel_ratio。
    """
    import services.sketch_parser.lshape_sketch_parser as lsp
    monkeypatch.setattr(lsp, '_multi_scale_ocr_scan', lambda *a, **k: [], raising=True)


def _draw_composite_sketch(path: str) -> str:
    """生成合成综合形状草图：实心黑色 L 多边形 + 挖出的白色中心矩形。

    必须画「实心块 + 白色挖空」，不能画成描边轮廓：描边会让轮廓检测把外框内边缘
    当成中心洞，洞尺寸退化成整张外框（实测 185.6×88.6cm），就区分不出 D2 缺陷了。
    """
    img = Image.new('RGB', _SKETCH_SIZE_PX, 'white')
    draw = ImageDraw.Draw(img)
    draw.polygon(
        [(0, 0), (749, 0), (749, 49), (924, 49), (924, 439), (0, 439)],
        fill='black')
    draw.rectangle([225, 50, 624, 349], fill='white')
    img.save(path)
    return path


def _set_composite_cut(panel, corner: str = 'tr', w_cm: float = 35.0, h_cm: float = 10.0):
    """按真实用户操作填写第 1 行挖角：勾选 → 选角位 → 填宽高。"""
    enabled, combo, width, height = panel._corner_rows[0]
    enabled.setChecked(True)
    index = combo.findData(corner)
    assert index >= 0, f'挖角角位下拉缺少 {corner}'
    combo.setCurrentIndex(index)
    width.setValue(w_cm)
    height.setValue(h_cm)


def _connection_count(obj, name: str) -> int:
    """返回 obj 上信号 name 的接收者个数。

    Bridge 用 lambda 连接，无法用 disconnect 探测；pyqtBoundSignal 又没有
    receivers()，只有 QObject.receivers(未绑定信号对象) 能安全计数。
    """
    unbound = getattr(type(obj), name, None)
    assert unbound is not None, f'{type(obj).__name__} 缺少信号 {name}'
    return obj.receivers(unbound)


def _wait_composite_result(qapp, panel, timeout_ms: int = 30000):
    """等待综合解析线程结束，并把 queued 信号派发回面板。

    worker.wait() 会阻塞主线程，槽函数只能在回到事件循环后才执行，
    所以 wait() 之后必须补 processEvents()，否则结果永远「回不来」。
    """
    worker = panel._composite_parse_worker
    assert worker is not None, '未发起综合解析线程（上传草图后自动识别没有触发）'
    assert worker.wait(timeout_ms), '综合解析线程未在超时内结束'
    deadline = time.monotonic() + timeout_ms / 1000.0
    while panel._composite_parse_result is None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert panel._composite_parse_result is not None, (
        '解析线程已结束但结果未回到面板：finished_ok -> _on_composite_parsed 接线断开'
    )
    return panel._composite_parse_result


class TestCompositePanelWiring:
    """装配与信号接线（D1 回归锁）。"""

    def test_property_panel_holds_composite_panel_and_bridge(self, main_window, gui_helpers):
        """复合面板必须被 PropertyPanel 注册，且桥接的 action 分发已连接。"""
        panel = main_window.panel
        cp = main_window.composite_panel
        assert isinstance(cp, CompositePanel)
        assert getattr(panel, '_composite_panel', None) is cp, (
            'PropertyPanel 未持有 CompositePanel 引用：main.py 漏调 set_composite_panel()'
        )
        assert isinstance(getattr(panel, '_composite_bridge', None), LShapePanelBridge), (
            'PropertyPanel 应持有 LShapePanelBridge（H-13 桥接）'
        )
        gui_helpers.assert_signal_connected(
            panel._composite_bridge.lshape_action_requested, panel._on_composite_action,
            'LShapePanelBridge.lshape_action_requested -> PropertyPanel._on_composite_action',
        )
        gui_helpers.assert_signal_connected(
            cp.composite_params_changed, panel._on_composite_params_changed,
            'CompositePanel.composite_params_changed -> PropertyPanel._on_composite_params_changed',
        )
        gui_helpers.assert_signal_connected(
            cp.composite_recognize_finished, panel._on_composite_recognize_finished,
            'CompositePanel.composite_recognize_finished -> PropertyPanel._on_composite_recognize_finished',
        )

    def test_every_panel_signal_has_receiver(self, main_window):
        """13 条 L 形细粒度信号 + 2 条综合专有信号都必须有接收者。

        修复前 13 条细粒度信号 receivers=0：界面不报错、不崩溃，只是「点了没反应」。
        """
        cp = main_window.composite_panel
        silent = [name for name in _LSHAPE_SIGNALS + _COMPOSITE_SIGNALS
                  if _connection_count(cp, name) == 0]
        assert silent == [], (
            f'以下信号无接收者（CompositePanel 与主程序接线断开）：{silent}'
        )

    def test_save_requested_bubbles_up(self, main_window):
        """「导出 JPG」必须冒泡成 PropertyPanel.save_requested（D1 症状之一）。

        先摘掉 MainWindow._on_save，避免真跑导出线程并在结束时弹模态框阻塞用例；
        本用例只验证「综合面板 → 常驻导出链」这一段是否通。
        """
        panel = main_window.panel
        cp = main_window.composite_panel
        panel.save_requested.disconnect(main_window._on_save)
        seen = []
        panel.save_requested.connect(lambda: seen.append(True))

        cp.save_requested.emit()

        assert seen == [True], '综合面板的导出请求没有到达 PropertyPanel.save_requested'


class TestCompositeGenerateOnSharedCanvas:
    """「生成预览」必须把 rect_lshape_hole 设计送到共享画布并真的渲染出图。"""

    def test_click_generate_puts_composite_design_on_canvas(self, main_window, gui_helpers, tmp_path):
        panel = main_window.panel
        cp = main_window.composite_panel
        canvas = main_window.canvas
        # 1px/cm：让画布渲染走主线程同步分支（<20 万像素），结果立即可断言
        panel.design.dpi = 2.54
        cp.set_outer_dims(_BASIS_W_CM, _BASIS_H_CM)
        cp._target_edit.setText(_TARGET_NAME)
        _set_composite_cut(cp, 'tr', 35.0, 10.0)

        btn = gui_helpers.assert_button_exists(cp, '🔍 生成预览')
        btn.click()

        assert panel._last_generate_source == 'composite', '生成来源标记错误，导出文件名会串到水池/L 形'
        assert canvas._design is not None, '画布没有收到设计'
        assert canvas._design.mode == 'rect_lshape_hole', (
            f'画布上的模式不是综合形状，而是 {canvas._design.mode!r}'
        )
        assert canvas._design.canvas_w_cm == pytest.approx(_BASIS_W_CM + 1.0)
        assert canvas._design.canvas_h_cm == pytest.approx(_BASIS_H_CM + 1.0)
        assert [c['corner'] for c in canvas._design.l_cuts_cm] == ['tr']

        img = canvas.full_image()
        assert img is not None, '生成后画布没有渲染出图像（GUI 空白）'
        assert img.size == _CANVAS_PX, (
            f'画布渲染尺寸 {img.size} 不等于设计画布 {_CANVAS_PX}（dpi={canvas._design.dpi}）'
        )
        assert canvas._design.canvas_w_px == _CANVAS_PX[0]

        assert panel.get_output_filename() == os.path.splitext(_TARGET_NAME)[0], (
            '导出文件名未跟随综合面板的目标文件名'
        )
        assert '已生成预览' in cp._composite_status.text()

    def test_generate_with_empty_cuts_is_refused_visibly(self, main_window, gui_helpers):
        """挖角为空时 validate() 拒绝，且拒绝必须是可见的、画布保持不变。"""
        panel = main_window.panel
        cp = main_window.composite_panel
        canvas = main_window.canvas
        cp.set_outer_dims(_BASIS_W_CM, _BASIS_H_CM)
        assert cp.get_cuts_cm() == [], '用例前提：默认挖角行为空（宽高均为 0）'

        before = canvas._design
        gui_helpers.assert_button_exists(cp, '🔍 生成预览').click()

        assert canvas._design is before, '非法参数被送到了画布（渲染层可能抛异常）'
        assert getattr(panel, '_last_generate_source', None) != 'composite'
        assert '无法生成综合形状' in cp._composite_status.text(), (
            f'拒绝原因没有可见反馈，当前状态：{cp._composite_status.text()!r}'
        )


class TestCompositeSketchRecognitionEcho:
    """上传 → 自动识别 → 参数回显 → 生成渲染（D2 / D4 / D5 回归锁）。"""

    def test_upload_without_basis_does_not_recognize(self, main_window, tmp_path):
        """没有尺寸基准时只给可见提示，不发识别线程（避免 4cm 基准的噪声结果）。"""
        cp = main_window.composite_panel
        canvas = main_window.canvas
        sketch = _draw_composite_sketch(str(tmp_path / 'sketch.png'))
        assert not cp._has_composite_basis(), '用例前提：默认外框等于下限，无基准'

        cp.sketch_load_requested.emit(sketch)

        assert cp._composite_parse_worker is None, '无基准却发起了识别（D2 缺陷回归）'
        assert '草图已加载' in cp._composite_status.text()
        assert cp._sk_preview.property('sketch_path') == sketch, '缩略图未记录草图路径'
        # 草图经 Bridge 投到共享画布：画布上是草图原图而不是空白
        img = canvas.full_image()
        assert img is not None and img.size == _SKETCH_SIZE_PX, '共享画布没有显示上传的草图'

    def test_target_edit_provides_basis(self, main_window):
        """目标文件名里的尺寸就是识别基准（回填外框 + 标记来源）。"""
        cp = main_window.composite_panel
        cp._target_edit.setText(_TARGET_NAME)

        assert cp.get_outer_w_cm() == pytest.approx(_BASIS_W_CM)
        assert cp.get_outer_h_cm() == pytest.approx(_BASIS_H_CM)
        assert cp._composite_basis_source == 'target'
        assert '已从目标文件名解析' in cp._composite_status.text()
        assert cp._has_composite_basis()

    def test_upload_with_basis_auto_recognizes_and_echoes(self, main_window, qapp, tmp_path):
        cp = main_window.composite_panel
        sketch = _draw_composite_sketch(str(tmp_path / 'sketch.png'))
        cp._target_edit.setText(_TARGET_NAME)
        cp.sketch_load_requested.emit(sketch)

        result = _wait_composite_result(qapp, cp)
        assert result.success, f'识别未通过：{result.message!r}'
        assert '识别完成' in cp._composite_status.text()

        # 10 项回显：中心洞 2 + 四边内边距 4 + 外框 2 + 挖角 N
        assert cp._hole_w.value() == pytest.approx(80.0, abs=_ECHO_TOL_CM)
        assert cp._hole_h.value() == pytest.approx(60.0, abs=_ECHO_TOL_CM)
        assert cp._hole_ml.value() == pytest.approx(45.0, abs=_ECHO_TOL_CM)
        assert cp._hole_mr.value() == pytest.approx(60.0, abs=_ECHO_TOL_CM)
        assert cp._hole_mt.value() == pytest.approx(10.0, abs=_ECHO_TOL_CM)
        assert cp._hole_mb.value() == pytest.approx(18.0, abs=_ECHO_TOL_CM)
        assert cp.get_outer_w_cm() == pytest.approx(_BASIS_W_CM)
        assert cp.get_outer_h_cm() == pytest.approx(_BASIS_H_CM)
        cuts = cp.get_cuts_cm()
        assert [c['corner'] for c in cuts] == ['tr']
        assert cuts[0]['cut_w_cm'] == pytest.approx(35.0, abs=_ECHO_TOL_CM)
        assert cuts[0]['cut_h_cm'] == pytest.approx(10.0, abs=_ECHO_TOL_CM)
        assert cp._composite_basis_source == 'recognize'

    def test_recognize_then_generate_renders_composite_preview(self, main_window, qapp, gui_helpers, tmp_path):
        """识别 → 生成：画布上的图从草图换成设计渲染图（尺寸即证据）。"""
        panel = main_window.panel
        cp = main_window.composite_panel
        canvas = main_window.canvas
        panel.design.dpi = 2.54
        sketch = _draw_composite_sketch(str(tmp_path / 'sketch.png'))
        cp._target_edit.setText(_TARGET_NAME)
        cp.sketch_load_requested.emit(sketch)
        _wait_composite_result(qapp, cp)
        assert canvas.full_image().size == _SKETCH_SIZE_PX, '用例前提：画布当前显示的是草图'

        gui_helpers.assert_button_exists(cp, '🔍 生成预览').click()

        assert canvas._design.mode == 'rect_lshape_hole'
        img = canvas.full_image()
        assert img.size == _CANVAS_PX, (
            f'画布仍是草图或旧图：{img.size}（期望设计渲染 {_CANVAS_PX}）'
        )
        assert cp.to_crop_design(dpi=2.54).canvas_w_cm == pytest.approx(_BASIS_W_CM + 1.0), (
            'to_crop_design() 的 canvas 口径必须与外框真值 + 1cm 损耗一致（D5）'
        )

    def test_clear_sketch_clears_thumbnail_and_status(self, main_window, tmp_path):
        cp = main_window.composite_panel
        main_window.panel.design.dpi = 2.54
        sketch = _draw_composite_sketch(str(tmp_path / 'sketch.png'))
        cp.sketch_load_requested.emit(sketch)
        assert cp._sk_preview.property('sketch_path') == sketch

        cp.sketch_clear_requested.emit()

        assert cp._sk_preview.property('sketch_path') in ('', None)
        assert cp._composite_parse_result is None
        assert '草图已清除' in cp._composite_status.text()
