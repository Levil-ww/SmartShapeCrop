import cv2, pytesseract, re
import numpy as np
from PIL import Image as PILImage

img = cv2.imread('scripts/diagnose/_test_user_sketch.png')
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

ox, oy, ow, oh = 46, 46, 971, 475
h_img, w_img = gray.shape[:2]

pad = max(10, int(0.05 * max(ow, oh)))
sx1 = max(0, ox - pad)
sy1 = max(0, oy - pad)
sx2 = min(w_img, ox + ow + pad)
sy2 = min(h_img, oy + oh + pad)
sub = gray[sy1:sy2, sx1:sx2]

print(f'Crop: ({sx1},{sy1}) to ({sx2},{sy2}), size={sx2-sx1}x{sy2-sy1}')

pil_img = PILImage.fromarray(sub)

hits = []
for psm in [6, 11]:
    config = f'--oem 3 --psm {psm}'
    data = pytesseract.image_to_data(pil_img, config=config, output_type=pytesseract.Output.DICT)
    n = len(data.get('text', []))
    for i in range(n):
        text = str(data['text'][i]).strip()
        if not text:
            continue
        try:
            conf = int(data.get('conf', [0]*n)[i])
        except:
            conf = 0
        if conf < 10:
            continue
        for m in re.finditer(r'(\d+\.?\d*)', text):
            try:
                val = float(m.group(1))
                if not (0.5 <= val <= 500):
                    continue
                xl = int(data.get('left', [0]*n)[i])
                yt = int(data.get('top', [0]*n)[i])
                ww = int(data.get('width', [0]*n)[i])
                hh = int(data.get('height', [0]*n)[i])
                x_c = xl + ww/2 + sx1
                y_c = yt + hh/2 + sy1
                hits.append((val, x_c, y_c, conf, psm, text))
            except ValueError:
                pass

merged = []
for v, xc, yc, cf, psm, text in hits:
    found = False
    for j, (mv, mx, my, mc, mp, mt) in enumerate(merged):
        if abs(mv - v) < 0.5 and abs(mx - xc) < 30 and abs(my - yc) < 30:
            if cf > mc:
                merged[j] = (v, xc, yc, cf, psm, text)
            found = True
            break
    if not found:
        merged.append((v, xc, yc, cf, psm, text))

print(f'\nAll unique hits ({len(merged)}):')
for v, xc, yc, cf, psm, text in sorted(merged, key=lambda t: t[1]):
    print(f'  val={v:6.1f} x={xc:5.0f} y={yc:5.0f} conf={cf:3d} psm={psm} text="{text}"')
