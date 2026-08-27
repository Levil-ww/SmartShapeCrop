import logging, sys
logging.basicConfig(level=logging.DEBUG, stream=sys.stdout, format='%(message)s')
from core.pool_designer.sketch_parser import _7step_parse, _safe_import_cv2, _safe_import_tesseract, _load_image, _to_gray, _find_all_rectangles, _select_best_nested_pair, _compute_gaps, _divide_8_zones, _multi_scale_ocr_scan, _merge_split_decimals, _extract_direction_label_numbers, _spatial_map_values, _build_assignment

cv2 = _safe_import_cv2()
tesseract = _safe_import_tesseract()
img, _ = _load_image('scripts/diagnose/_test_6sketch_1.png')
gray = _to_gray(img)

h_img, w_img = gray.shape[:2]

all_rects = _find_all_rectangles(cv2, gray, img)
outer, inner = _select_best_nested_pair(all_rects)
ox, oy, ow, oh = outer[:4]
ix, iy, iw, ih = inner[:4]

gaps = _compute_gaps(ox, oy, ow, oh, ix, iy, iw, ih)
zone_of = _divide_8_zones(outer, inner, w_img, h_img)

ocr_raw = _multi_scale_ocr_scan(cv2, tesseract, gray, target_w_cm=0, target_h_cm=0)
ocr_merged = _merge_split_decimals(ocr_raw)
dir_locked = _extract_direction_label_numbers(cv2, tesseract, gray)
excluded_fields = set(dir_locked.keys())
excluded_values = [v[0] for v in dir_locked.values()]
buckets = _spatial_map_values(ocr_merged, zone_of, excluded_fields, excluded_values)

print()
print("=== inner_w bucket ===")
for v, c, b in buckets.get('inner_w', []):
    print(f"  val={v} conf={c} bbox={b}")

print()
print("=== inner_h bucket ===")
for v, c, b in buckets.get('inner_h', []):
    print(f"  val={v} conf={c} bbox={b}")

print()
print("=== outer_w bucket ===")
for v, c, b in buckets.get('outer_w', []):
    print(f"  val={v} conf={c} bbox={b}")

print()
print("=== outer_h bucket ===")
for v, c, b in buckets.get('outer_h', []):
    print(f"  val={v} conf={c} bbox={b}")

print()
print("=== margin_left bucket ===")
for v, c, b in buckets.get('margin_left', []):
    print(f"  val={v} conf={c} bbox={b}")

print()
print("=== margin_right bucket ===")
for v, c, b in buckets.get('margin_right', []):
    print(f"  val={v} conf={c} bbox={b}")

print()
print("=== margin_top bucket ===")
for v, c, b in buckets.get('margin_top', []):
    print(f"  val={v} conf={c} bbox={b}")

print()
print("=== margin_bottom bucket ===")
for v, c, b in buckets.get('margin_bottom', []):
    print(f"  val={v} conf={c} bbox={b}")

print()
print("=== _build_assignment(150, 60) ===")
asg = _build_assignment(dir_locked, buckets, 150, 60)
for k, (v, c) in sorted(asg.items()):
    print(f"  {k}: {v} (conf={c})")
