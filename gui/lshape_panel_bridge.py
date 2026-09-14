"""gui/lshape_panel_bridge.py — [H-13] LShapePanel 信号桥接适配器。

背景：LShapePanel 对外暴露 13 个细粒度信号（sketch_* / target_* / lshape_* 等），
PropertyPanel.set_lshape_panel() 需要逐一连接，对外契约较重（见审查报告 H-13）。

本 Bridge 把所有信号翻译合并为统一的粗粒度信号：

    lshape_action_requested(action: str, params: object)

PropertyPanel 只连接这一个信号，再按 action 分派。LShapePanel 本身零改动：
所有信号定义、emit 点保持原样；桥接为纯加法、行为等价。
"""
from PyQt5.QtCore import QObject, pyqtSignal


class LShapePanelBridge(QObject):
    """把 LShapePanel 的细粒度信号翻译为统一 action 信号。

    params 约定：
      - 无参信号 → None
      - 单参信号（str / dict / bool）→ 原样透传第一个参数
    """

    lshape_action_requested = pyqtSignal(str, object)  # (action, params)

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self._panel = panel
        # (action 名, 参数个数)；0 = 无参信号
        _SIGNALS = (
            ('lshape_params_changed', 0),
            ('lshape_applied', 1),
            ('lshape_recognize_started', 0),
            ('lshape_recognize_finished', 1),
            ('sketch_pick_requested', 0),
            ('sketch_clear_requested', 0),
            ('sketch_view_requested', 0),
            ('sketch_load_requested', 1),
            ('target_changed', 1),
            ('target_pick_requested', 0),
            ('target_clear_requested', 0),
            ('generate_requested', 0),
            ('save_requested', 0),
        )
        for action, nargs in _SIGNALS:
            sig = getattr(panel, action)
            if nargs == 0:
                sig.connect(lambda a=action: self.lshape_action_requested.emit(a, None))
            else:
                sig.connect(
                    lambda *args, a=action: self.lshape_action_requested.emit(
                        a, args[0] if args else None
                    )
                )