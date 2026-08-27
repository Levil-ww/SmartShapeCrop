
# ============================================================
# PROJECT_ROOT auto-inject (added by test-dir cleanup 2026-08-11)
# 脚本从 scripts/ 子目录运行时仍能正确定位 core/, psd_demo/, Test/output 等
import sys as _sys
import os as _os
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))
_D = str(_PROJECT_ROOT)
# ============================================================

import sys
sys.path.insert(0, 'd:/SmartShapeCrop')
from PIL import Image, ImageDraw
import numpy as np

# Simulate the mask merge logic
w, h = 100, 80
full_mask = Image.new('L', (w, h), 255)
inner_mask = Image.new('L', (w, h), 0)
inner_draw = ImageDraw.Draw(inner_mask)
inner_rect = [20, 20, w-20, h-20]
inner_draw.rectangle(inner_rect, fill=255)

# OLD (buggy) logic
zero_img = Image.new('L', (w, h), 0)
border_region_mask = Image.composite(zero_img, full_mask, inner_mask)
old_final = Image.composite(inner_mask, border_region_mask, inner_mask)

# NEW (fixed) logic
new_final = Image.composite(full_mask, inner_mask, inner_mask)

old_arr = np.array(old_final)
new_arr = np.array(new_final)

print('OLD logic:')
print('  Border region value (should be 255):', old_arr[10, 10])
print('  Interior value:', old_arr[40, 40])
print('NEW logic:')
print('  Border region value (should be 255):', new_arr[10, 10])
print('  Interior value:', new_arr[40, 40])
print('Are they different?', not np.array_equal(old_arr, new_arr))
print('Fix is correct:', new_arr[10, 10] == 255)