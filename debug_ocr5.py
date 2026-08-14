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
    _ocr_full_image,
    _assign_ocr_values_to_fields,
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
h_img, w_img = gray.shape[:2]

hits = _ocr_full_image(cv2, gray, tesseract)
print(f'=== _ocr_full_image 结果（{len(hits)} 个） ===')
for v, xc, yc, c in sorted(hits, key=lambda h: h[0]):
    print(f'  val={v:8.3f}  xc={xc:7.2f}  yc={yc:7.2f}  conf={c:.2f}')

print()
print('=== _assign_ocr_values_to_fields ===')
res = _assign_ocr_values_to_fields(hits, outer, inner, h_img, w_img)
for k, (v, c) in res.items():
    print(f'  {k:15s}: {v:8.3f} (conf={c})')

os.unlink(tmp_path)
