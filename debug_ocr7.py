import cv2
import numpy as np
import tempfile, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.pool_designer.sketch_parser import _safe_import_tesseract, _load_image, _to_gray
from PIL import Image as PILImage

img = np.ones((1000, 800, 3), dtype=np.uint8) * 255
cv2.rectangle(img, (100, 100), (700, 900), (0, 0, 200), 3)
cv2.rectangle(img, (200, 250), (500, 800), (0, 0, 200), 3)
cv2.putText(img, '6', (420, 210), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 200), 4)

tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False, dir=tempfile.gettempdir())
tmp_path = tmp.name
tmp.close()
cv2.imwrite(tmp_path, img)

tess = _safe_import_tesseract()
loaded, _ = _load_image(tmp_path)
gray = _to_gray(loaded)

# 只含 6 的图，测试各种预处理和 scale
scales = [1.5, 2.5, 3.0, 1.0]
for s in scales:
    gs = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    variants = [gs]
    try:
        variants.append(cv2.adaptiveThreshold(gs, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                               cv2.THRESH_BINARY, 25, 8))
    except Exception:
        pass
    try:
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        variants.append(clahe.apply(gs))
    except Exception:
        pass
    for i, v in enumerate(variants):
        for psm in [6, 7, 8, 10, 11, 13]:
            try:
                pil = PILImage.fromarray(v)
                cfg = f'--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789.'
                s11 = tess.image_to_string(pil, config=cfg).strip()
                if '6' in s11.replace(' ','').replace('\n',''):
                    print(f'scale={s} variant={i} psm={psm}: "{s11}"  ✓ 读到了 6!')
            except Exception as e:
                pass

# 单独把 ROI 切出来 (400, 180, 100, 80)
roi = gray[170:240, 380:460]
roi_2x = cv2.resize(roi, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
pil = PILImage.fromarray(roi_2x)
print()
print('只切 ROI (上边距 6 的位置 3x 放大):')
for psm in [6,7,8,10,11,13]:
    cfg = f'--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789.'
    print(f'  PSM {psm}: "{tess.image_to_string(pil, config=cfg).strip()}"')

os.unlink(tmp_path)
