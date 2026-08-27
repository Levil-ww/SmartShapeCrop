import cv2
import numpy as np
import tempfile, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.pool_designer.sketch_parser import (
    _safe_import_tesseract, _load_image, _to_gray,
    _find_two_nested_rectangles,
    _find_number_regions,
    _ocr_region,
)

img = np.ones((1000, 800, 3), dtype=np.uint8) * 255
cv2.rectangle(img, (100, 100), (700, 900), (0, 0, 200), 3)
cv2.rectangle(img, (200, 250), (500, 800), (0, 0, 200), 3)
cv2.putText(img, '133', (380, 955), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 0, 200), 3)
cv2.putText(img, '60.5', (30, 520), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 200), 3)
cv2.putText(img, '6', (420, 210), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 200), 4)
cv2.putText(img, '10', (420, 850), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 200), 2)
cv2.putText(img, '14.6', (120, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 200), 2)
cv2.putText(img, '42.4', (600, 540), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 200), 2)
cv2.putText(img, '44.5', (300, 510), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 200), 3)
cv2.putText(img, '76', (340, 680), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 200), 3)

tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False, dir=tempfile.gettempdir())
tmp_path = tmp.name
tmp.close()
cv2.imwrite(tmp_path, img)

tess = _safe_import_tesseract()
loaded, _ = _load_image(tmp_path)
gray = _to_gray(loaded)

top2 = _find_two_nested_rectangles(cv2, gray, loaded)
ox, oy, ow, oh = int(top2[0][0]), int(top2[0][1]), int(top2[0][2]), int(top2[0][3])
ix, iy, iw, ih = int(top2[1][0]), int(top2[1][1]), int(top2[1][2]), int(top2[1][3])
h_img, w_img = gray.shape[:2]

# ROI margin_top: (ox, oy, ow, max(6, iy-oy+int(oh*0.05)))
roi_mt = (ox, oy, ow, max(6, iy - oy + int(oh * 0.05)))
print(f'margin_top ROI: {roi_mt}')
# ROI 的范围：(x=91, y=97, w=612, h=252-97+40=195)
# 即 y: 97..292 区间，里面有 "6"（at y=210）和 "14.6"（at y=240）

regs = _find_number_regions(cv2, gray, roi_mt, max_regions=3)
print(f'Regions found in margin_top ROI: {len(regs)}')
for r in regs:
    print(f'  region: {r[:4]} area={r[4]}')
    val = _ocr_region(cv2, gray, r, tess)
    print(f'    → OCR: {val}')

# 直接把这个 ROI 切出来 3x 放大做识别
x, y, w, h = roi_mt
roi_img = gray[y:y+h, x:x+w]
roi_3x = cv2.resize(roi_img, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
from PIL import Image as PILImage
pil = PILImage.fromarray(roi_3x)
for psm in [6, 7, 8, 10, 11, 13]:
    cfg = f'--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789.'
    txt = tess.image_to_string(pil, config=cfg).strip()
    if txt:
        print(f'  margin_top ROI 3x PSM {psm}: image_to_string = "{txt}"')

# 测试 image_to_data 在这个 ROI 3x 里能读到什么
cfg = r'--oem 3 --psm 11 -c tessedit_char_whitelist=0123456789.'
data = tess.image_to_data(pil, config=cfg, output_type=tess.Output.DICT)
print('  image_to_data 结果:')
for i, t in enumerate(data.get('text', [])):
    if str(t).strip():
        print(f'    "{t}" conf={data.get("conf",[])[i]}  L,T,W,H = {data.get("left",[])[i]},{data.get("top",[])[i]},{data.get("width",[])[i]},{data.get("height",[])[i]}')

os.unlink(tmp_path)
