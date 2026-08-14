"""Test render_design with pool mode (mimicking actual usage)."""
import sys
sys.path.insert(0, 'd:/SmartShapeCrop')

from core.image_ops import render_design
from core.geometry import CropDesign
import traceback

d = CropDesign()
d.canvas_w_cm = 89.0
d.canvas_h_cm = 65.0
d.dpi = 150
d.mode = 'rect_hole'
d.outer_margin_cm = 0.0
d.inner_margin_top_cm = 6.4
d.inner_margin_bottom_cm = 6.4
d.inner_margin_left_cm = 6.4
d.inner_margin_right_cm = 6.4
d.pool_hole_transparent = True
d.borders = []
d.corner_tl_cm = 0.0
d.corner_tr_cm = 0.0
d.corner_bl_cm = 0.0
d.corner_br_cm = 0.0

# Test 1: No material image - should work
print("Test 1: Basic (no material)...")
try:
    result = render_design(d)
    print(f"  OK: {result.size}, mode={result.mode}")
    result.save('debug_output/test1_basic.jpg')
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()

# Test 2: With a non-existent material path (simulating a missing file)
print("Test 2: With non-existent material path...")
d.pool_outer_material_image = r'C:\nonexistent\material.jpg'
try:
    result = render_design(d)
    print(f"  OK: {result.size}, mode={result.mode}")
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()

# Test 3: With rounded corners (like the user's scenario)
print("Test 3: With rounded corners (8cm all corners)...")
d.corner_tl_cm = 8.0
d.corner_tr_cm = 8.0
d.corner_bl_cm = 8.0
d.corner_br_cm = 5.0
d.pool_outer_material_image = None  # no material, pure color
try:
    result = render_design(d)
    print(f"  OK: {result.size}, mode={result.mode}")
    result.save('debug_output/test3_corners.jpg')
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()

# Test 4: Full scenario - pool mode with material path (even if it doesn't exist, should handle)
print("Test 4: Pool mode with material path...")
d.pool_outer_material_image = r'd:\SmartShapeCrop\test_material.jpg'
d.corner_tl_cm = 8.0
d.corner_tr_cm = 8.0
d.corner_bl_cm = 8.0
d.corner_br_cm = 5.0
try:
    result = render_design(d)
    print(f"  OK: {result.size}, mode={result.mode}")
    result.save('debug_output/test4_pool.jpg')
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()

# Test 5: Check _get_inner_pixel_mask directly
print("\nTest 5: _get_inner_pixel_mask...")
from core.image_ops import _get_inner_pixel_mask
import numpy as np
try:
    mask = _get_inner_pixel_mask(d)
    print(f"  OK: shape={mask.shape}, dtype={mask.dtype}, True_count={mask.sum()}")
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()

print("\nDone!")
