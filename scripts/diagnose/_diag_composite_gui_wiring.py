"""诊断：综合形状面板 GUI 接线（不显示画面 / 不触发预览）根因定位。

用法：
    $env:QT_QPA_PLATFORM="offscreen"
    python scripts/diagnose/_diag_composite_gui_wiring.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from main import MainWindow  # noqa: E402

w = MainWindow()
cp = w.composite_panel

print("=== [1] CompositePanel 信号连接数 ===")
for sig_name in (
    "generate_requested", "save_requested", "sketch_pick_requested",
    "sketch_clear_requested", "sketch_view_requested", "sketch_load_requested",
    "target_changed", "target_pick_requested", "target_clear_requested",
    "composite_params_changed", "composite_recognize_finished",
    "lshape_applied", "lshape_recognize_started",
):
    sig = getattr(cp, sig_name, None)
    if sig is None:
        print(f"  {sig_name:28s} <缺失>")
        continue
    try:
        n = cp.receivers(sig)
    except TypeError:
        n = -1
    print(f"  {sig_name:28s} receivers={n}")

print()
print("=== [2] 主画布是否收到设计 ===")
print(f"  canvas._design = {w.canvas._design}")

print()
print("=== [3] 点击【生成预览】后画布设计 ===")
before = w.canvas._design
cp._btn_generate.click()
app.processEvents()
after = w.canvas._design
print(f"  before = {before}")
print(f"  after  = {after}")
print(f"  变化 = {after is not before}")

print()
print("=== [4] 面板初始（未上传草图）点击【识别综合形状草图】 ===")
print(f"  _sk_preview.property('sketch_path') = {cp._sk_preview.property('sketch_path')!r}")
cp._btn_composite_recognize.click()
app.processEvents()
print(f"  _btn_composite_recognize.isEnabled() = {cp._btn_composite_recognize.isEnabled()}")

print()
print("=== [5] to_crop_design 快照自检 ===")
try:
    d = cp.to_crop_design(dpi=150)
    print(f"  mode={d.mode} canvas={d.canvas_w_cm}x{d.canvas_h_cm} "
          f"inner={d.inner_margin_top_cm}/{d.inner_margin_bottom_cm}/"
          f"{d.inner_margin_left_cm}/{d.inner_margin_right_cm}")
    print(f"  cut={d.l_corner} {d.l_cut_w_cm}x{d.l_cut_h_cm}")
    try:
        d.validate()
        print("  validate() = OK")
    except Exception as e:
        print(f"  validate() 抛错: {e}")
except Exception as e:
    print(f"  to_crop_design 抛错: {e!r}")

print()
print("=== [6] 面板可见性 / 尺寸 ===")
print(f"  composite tab index = {w._tabs.indexOf(cp)} / count={w._tabs.count()}")
print(f"  cp.isVisible() = {cp.isVisible()}")
print(f"  cp sizeHint = {cp.sizeHint()}")
print(f"  中心洞 GroupBox visible = {getattr(cp, '_gb_composite', None) is not None and cp._gb_composite.isVisible()}")
print(f"  L形识别区 visible = {cp._gb_lshape_recog.isVisible()}")

print()
print("=== [7] PropertyPanel 是否持有 composite 引用 ===")
print(f"  panel._lshape_panel is cp = {w.panel._lshape_panel is cp}")
print(f"  panel._lshape_panel is w.lshape_panel = {w.panel._lshape_panel is w.lshape_panel}")
