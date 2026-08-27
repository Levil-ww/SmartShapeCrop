"""深度调试草图1"""
import sys, os
import traceback
sys.path.insert(0, '.')

print("Step 1: Importing modules...")
import cv2
print("  cv2 imported")

print("Step 2: Loading sketch_parser...")
from core.pool_designer.sketch_parser import parse_sketch, _assess_complexity, _find_two_nested_rectangles, _load_image, _to_gray
print("  sketch_parser imported")

print("Step 3: Loading image...")
img, err = _load_image('scripts/diagnose/_test_sketch1.png')
if err:
    print(f"  ERROR: {err}")
    sys.exit(1)
print(f"  Image loaded: {img.shape}")

gray = _to_gray(img)
print(f"  Gray: {gray.shape}")

print("Step 4: Complexity assessment...")
is_complex, reason = _assess_complexity(gray)
print(f"  is_complex={is_complex}, reason={reason}")

if is_complex:
    print("  SKIPPING - too complex")
    sys.exit(1)

print("Step 5: Rectangle detection...")
top2 = _find_two_nested_rectangles(cv2, gray, img)
print(f"  Found {len(top2)} rectangles")
if len(top2) >= 2:
    for i, r in enumerate(top2):
        print(f"    Rect {i}: {r[:4]} score={r[4]:.3f}")
else:
    print("  FAILED - not enough rectangles")

print("Step 6: Running parse_sketch...")
try:
    result = parse_sketch('scripts/diagnose/_test_sketch1.png', target_outer_w_cm=120.0, target_outer_h_cm=58.0)
    print(f"\nResult:")
    print(f"  outer: {result.outer_w_cm} x {result.outer_h_cm} cm")
    print(f"  inner: {result.inner_w_cm} x {result.inner_h_cm} cm")
    print(f"  margins: top={result.margin_top_cm}, bottom={result.margin_bottom_cm}, left={result.margin_left_cm}, right={result.margin_right_cm}")
    print(f"  message: {result.message}")
    print(f"  method: {result.method}")
    print(f"  debug keys: {list(result.debug.keys())}")
except Exception as e:
    print(f"  EXCEPTION: {e}")
    traceback.print_exc()
