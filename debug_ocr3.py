import cv2
import numpy as np
import tempfile
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.pool_designer.sketch_parser import (
    _safe_import_tesseract,
    _load_image,
    _to_gray,
    _find_two_nested_rectangles,
    _find_and_read_numbers,
)

img = np.ones((1000, 800, 3), dtype=np.uint8) * 255
cv2.rectangle(img, (100, 100), (700, 900), (0, 0, 200), 3)
cv2.rectangle(img, (200, 250), (500, 800), (0, 0, 200), 3)
cv2.putText(img, '133', (380, 955), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 0, 200), 3)
cv2.putText(img, '60.5', (30, 520), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 200), 3)
cv2.putText(img, '6', (420, 210), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 200), 2)
cv2.putText(img, '10', (420, 850), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 200), 2)
cv2.putText(img, '14.6', (120, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 200), 2)
cv2.putText(img, '42.4', (600, 540), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 200), 2)
cv2.putText(img, '44.5', (300, 510), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 200), 3)
cv2.putText(img, '76', (340, 680), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 200), 3)

tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False, dir=tempfile.gettempdir())
tmp_path = tmp.name
tmp.close()
cv2.imwrite(tmp_path, img)

tesseract = _safe_import_tesseract()
loaded, err = _load_image(tmp_path)
gray = _to_gray(loaded)
top2 = _find_two_nested_rectangles(cv2, gray, loaded)
outer = (int(top2[0][0]), int(top2[0][1]), int(top2[0][2]), int(top2[0][3]))
inner = (int(top2[1][0]), int(top2[1][1]), int(top2[1][2]), int(top2[1][3]))

# 分别测试：只用 A（全图OCR） vs 只用 B（ROI OCR） vs 合并
# 我们先看 _find_and_read_numbers，在里面区分 A 和 B：
from core.pool_designer.sketch_parser import (
    _ocr_full_image,
    _assign_ocr_values_to_fields,
    _find_number_regions,
    _ocr_region,
)

h_img, w_img = gray.shape[:2]
hits = _ocr_full_image(cv2, gray, tesseract)
print('=== 策略 A：全图 OCR + 位置映射（仅 A） ===')
a_res = _assign_ocr_values_to_fields(hits, outer, inner, h_img, w_img)
for k, (v, c) in a_res.items():
    print(f'  {k}: {v} (conf={c})')

print()
print('=== 策略 B：仅 ROI OCR ===')
ox, oy, ow, oh = outer
ix, iy, iw, ih = inner
rois_to_check = [
    ('total_w',   (ox,                    max(0, oy - int(oh * 0.3)),  ow,                 max(6, int(oh * 0.35)))),
    ('total_w',   (ox,                    min(h_img - 1, oy + oh),    ow,                 max(6, int(oh * 0.35)))),
    ('total_h',   (max(0, ox - int(ow * 0.35)), oy,                   max(6, int(ow * 0.35)), oh)),
    ('total_h',   (min(w_img - 1, ox + ow),     oy,                   max(6, int(ow * 0.35)), oh)),
    ('margin_top',    (ox,                  oy,                      ow,  max(6, iy - oy + int(oh * 0.05)))),
    ('margin_bottom', (ox,                  iy + ih,                ow,  max(6, (oy + oh) - (iy + ih) + int(oh * 0.05)))),
    ('margin_left',   (ox,                  oy,                      max(6, ix - ox + int(ow * 0.05)), oh)),
    ('margin_right',  (ix + iw,             oy,                      max(6, (ox + ow) - (ix + iw) + int(ow * 0.05)), oh)),
    ('inner_w',       (ix,                  iy,                      iw,  max(6, int(ih * 0.7)))),
    ('inner_h',       (ix,                  iy + int(ih * 0.3),     iw,  max(6, int(ih * 0.7)))),
]
b_res = {k:(0.0, 0) for k in a_res}
ix, iy, iw, ih = inner
for key, roi in rois_to_check:
    regions = _find_number_regions(cv2, gray, roi, max_regions=3)
    for reg in regions:
        val = _ocr_region(cv2, gray, reg, tesseract)
        if val is None:
            continue
        print(f'  ROI {key}: OCR 读出 {val}  (roi={roi}, region_area={reg[4]})')
        old_v, old_c = b_res[key]
        new_c = 7 if reg[4] > 200 else 5
        if old_c < new_c or old_v == 0:
            b_res[key] = (val, new_c)

print()
print('B 最终:')
for k, (v, c) in b_res.items():
    print(f'  {k}: {v} (conf={c})')

os.unlink(tmp_path)
