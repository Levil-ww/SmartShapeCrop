"""Reproduce the design_changed signal delivery issue."""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QObject, pyqtSignal

app = QApplication(sys.argv)

# Simulate the PropertyPanel signal setup
from gui.property_panel import PropertyPanel

panel = PropertyPanel()
call_count = [0]

def on_design_changed(design):
    call_count[0] += 1
    print(f"[on_design_changed] CALLED! design={design}, count={call_count[0]}")

panel.design_changed.connect(on_design_changed)

# Check receivers
n = panel.receivers(panel.design_changed)
print(f"receivers(design_changed) = {n}")

# Try emitting directly
print("--- Emitting design_changed directly ---")
panel.design_changed.emit("test_design")
print(f"After direct emit: call_count={call_count[0]}")

# Now try via _apply_quiet (need a design first)
print("\n--- Emitting via _apply_quiet ---")
from core.geometry import CropDesign
panel.design = CropDesign(canvas_w_cm=100, canvas_h_cm=50, dpi=96, mode='rect_lshape')
call_count[0] = 0
try:
    panel._apply_quiet()
    print(f"After _apply_quiet: call_count={call_count[0]}")
except Exception as e:
    print(f"_apply_quiet raised: {e}")

print("\nDone.")
