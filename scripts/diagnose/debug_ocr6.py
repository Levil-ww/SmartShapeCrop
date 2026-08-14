import cv2
import numpy as np
import tempfile
import os
import sys
import re
from PIL import Image as PILImage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.pool_designer.sketch_parser import (
    _safe_import_tesseract,
    _load_image,
    _to_gray,
    _find_two_nested_rectangles,
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

h_img, w_img = gray.shape[:2]
max_side = max(h_img, w_img)
scales = [1.5, 2.5, 1.0]  # 对应我们当前代码用的

for scale in scales:
    if abs(scale - 1.0) < 1e-6:
        gs = gray
    else:
        gs = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    pil_img = PILImage.fromarray(gs)
    try:
        data = tesseract.image_to_data(
            pil_img, config=r'--oem 3 --psm 11 -c tessedit_char_whitelist=0123456789.',
            output_type=tesseract.Output.DICT,
        )
    except Exception as e:
        print(f'scale={scale}: image_to_data 失败 {e}')
        continue
    print(f'--- scale={scale}x ---')
    n = len(data.get('text', []))
    for i in range(n):
        text = str(data['text'][i]).strip()
        if not text:
            continue
        try:
            conf = int(data.get('conf', [50] * n)[i])
        except Exception:
            conf = 50
        if conf < 10:
            continue
        try:
            x_left = int(data['left'][i]) / scale
            y_top = int(data['top'][i]) / scale
            ww = int(data['width'][i]) / scale
            hh = int(data['height'][i]) / scale
        except Exception:
            continue
        x_c = x_left + ww / 2
        y_c = y_top + hh / 2
        if re.fullmatch(r'[.\s]+', text):
            continue
        print(f'  text="{text}"  conf={conf}  xc={x_c:7.2f}  yc={y_c:7.2f}  w={ww:5.1f} h={hh:5.1f}')

os.unlink(tmp_path)
