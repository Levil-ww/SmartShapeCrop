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
print(f'全部 {len(hits)} 个 hits:')
for v, xc, yc, c in hits:
    print(f'  val={v} at ({xc:.1f},{yc:.1f}) conf={c:.2f}')
print()

# 直接重跑 _assign_ocr_values_to_fields 的 anchors 匹配逻辑，打印每个 hit 的所有候选
ox, oy, ow, oh = outer
ix, iy, iw, ih = inner

anchors = [
    ('total_w',   (ox + ow / 2), (oy + oh + oh * 0.12),
        (ow * 0.6), (oh * 0.3)),
    ('total_h',   (ox - ow * 0.12), (oy + oh / 2),
        (ow * 0.3), (oh * 0.6)),
    ('margin_top',    (ox + ow / 2), (oy + max(0, iy - oy) / 2),
        (ow * 0.8), max(oh * 0.35, (iy - oy) + oh * 0.15)),
    ('margin_bottom', (ox + ow / 2), (iy + ih + max(0, (oy + oh) - (iy + ih)) / 2),
        (ow * 0.8), max(oh * 0.35, ((oy + oh) - (iy + ih)) + oh * 0.15)),
    ('margin_left',   (ox + (ix - ox) / 2), (iy + ih / 2),
        max(ow * 0.3, (ix - ox) + ow * 0.15), (ih * 0.9)),
    ('margin_right',  (ix + iw + ((ox + ow) - (ix + iw)) / 2), (iy + ih / 2),
        max(ow * 0.3, ((ox + ow) - (ix + iw)) + ow * 0.15), (ih * 0.9)),
    ('inner_w',   (ix + iw / 2), (iy + ih * 0.35),
        (iw * 0.8), (ih * 0.5)),
    ('inner_h',   (ix + iw / 2), (iy + ih * 0.65),
        (iw * 0.8), (ih * 0.5)),
    ('total_w', (ox + ow * 0.5), (oy + oh + oh * 0.12),
        (ow * 0.95), (oh * 0.3)),
    ('total_w', (ox + ow / 2), (oy - oh * 0.12),
        (ow * 0.95), (oh * 0.4)),
    ('total_h', (ox - ow * 0.08), (oy + oh * 0.5),
        (ow * 0.35), (oh * 0.95)),
    ('total_h', (ox + ow * 1.08), (oy + oh * 0.5),
        (ow * 0.35), (oh * 0.95)),
    ('total_h', (ox + ow / 2), (oy - oh * 0.08),
        (ow * 0.5), (oh * 0.25)),
    ('total_h', (ox + ow / 2), (oy + oh + oh * 0.08),
        (ow * 0.5), (oh * 0.25)),
]

def _value_pref_weight(val, key):
    if key in ('total_w', 'total_h'):
        if val >= 50:
            return 0.15
        elif val < 15:
            return -0.2
        return 0.0
    elif key in ('inner_w', 'inner_h'):
        if 20 <= val <= 300:
            return 0.1
        return 0.0
    elif key.startswith('margin_'):
        if 2 <= val <= 40:
            return 0.15
        elif val > 80:
            return -0.3
        return 0.0
    return 0.0

for val, xc, yc, conf in hits:
    print(f'--- hit val={val} ({xc:.1f},{yc:.1f}) ---')
    cands = []
    for (key, ax, ay, x_tol, y_tol) in anchors:
        dx = abs(xc - ax)
        dy = abs(yc - ay)
        if dx > x_tol or dy > y_tol:
            continue
        dist_score = (1 - dx / max(1, x_tol)) * 0.4 + (1 - dy / max(1, y_tol)) * 0.4
        s = dist_score + conf * 0.2 + _value_pref_weight(val, key)
        cands.append((s, key, ax, ay, x_tol, y_tol, dx, dy))
    cands.sort(reverse=True)
    for s, key, ax, ay, xt, yt, dx, dy in cands[:5]:
        print(f'  score={s:.3f} → {key}  anchor=({ax:.1f},{ay:.1f})  tol=({xt:.1f},{yt:.1f})  dxdy=({dx:.1f},{dy:.1f})')
    if not cands:
        print(f'  (无匹配)')

os.unlink(tmp_path)
