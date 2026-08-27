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
h_img, w_img = gray.shape[:2]

top2 = _find_two_nested_rectangles(cv2, gray, loaded)
outer = (int(top2[0][0]), int(top2[0][1]), int(top2[0][2]), int(top2[0][3]))
inner = (int(top2[1][0]), int(top2[1][1]), int(top2[1][2]), int(top2[1][3]))
ox, oy, ow, oh = outer
print(f'Outer: x={ox} y={oy} w={ow} h={oh}')
print(f'Inner: x={inner[0]} y={inner[1]} w={inner[2]} h={inner[3]}')
print(f'Image: w={w_img} h={h_img}')
print()

# ====== 直接看 image_to_data 中每个字符的位置 ======
from PIL import Image as PILImage

scale = 2.0 if max(h_img, w_img) < 800 else 1.5
gray_scaled = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
pil_img = PILImage.fromarray(gray_scaled)

config_data = r'--oem 3 --psm 11 -c tessedit_char_whitelist=0123456789.'
data = tesseract.image_to_data(
    pil_img, config=config_data,
    output_type=tesseract.Output.DICT,
)

print('image_to_data 检测到的字符（未合并）:')
for i, text in enumerate(data.get('text', [])):
    text = str(text).strip()
    if not text:
        continue
    conf = data.get('conf', [0] * len(data['text']))[i]
    try:
        conf = int(conf)
    except Exception:
        conf = 50
    if conf < 20:
        continue
    x_left_s = data.get('left', [0] * len(data['text']))[i]
    y_top_s = data.get('top', [0] * len(data['text']))[i]
    ww_s = data.get('width', [0] * len(data['text']))[i]
    hh_s = data.get('height', [0] * len(data['text']))[i]
    try:
        x_left = int(x_left_s) / scale
        y_top = int(y_top_s) / scale
        ww = int(ww_s) / scale
        hh = int(hh_s) / scale
    except Exception:
        continue
    xc = x_left + ww / 2
    yc = y_top + hh / 2
    # 真实的期望位置
    # 60.5 在左边 (30, 520) 附近
    # 133 在底部 (380, 955) 附近
    # 6 在上方 (420, 210)
    # 10 在下方 (420, 850)
    # 14.6 左中 (120, 240)
    # 42.4 右中 (600, 540)
    # 44.5 内上 (300, 510)
    # 76 内下 (340, 680)
    print(f'  文字="{text}" xc={xc:.1f} yc={yc:.1f} conf={conf}  (box=({x_left:.1f},{y_top:.1f},{ww:.1f},{hh:.1f}))')

print()
print('=' * 60)
hits = _ocr_full_image(cv2, gray, tesseract)
print(f'全图 OCR 合并后得到 {len(hits)} 个数字:')
for val, xc, yc, conf in sorted(hits, key=lambda h: h[2]):
    # 计算这个 hit 落在哪个区域
    which = []
    # 外框外边区
    if yc < oy + oh * 0.2:
        which.append('顶部外侧/上方')
    elif yc > oy + oh * 0.8:
        which.append('底部外侧/下方')
    if xc < ox + ow * 0.2:
        which.append('左侧外/内')
    elif xc > ox + ow * 0.8:
        which.append('右侧外/内')
    # 外框与内框之间的间隙
    if ox < xc < ox + ow and oy < yc < oy + oh:
        if not (inner[0] < xc < inner[0] + inner[2] and inner[1] < yc < inner[1] + inner[3]):
            which.append('内外间隙')
        else:
            which.append('内框内部')
    print(f'  val={val} at xc={xc:.1f} yc={yc:.1f} conf={conf:.2f} 区域={which}')

# 打印各锚点位置，看看哪个 hit 归错了
print()
print('锚点坐标参考:')
ix, iy, iw, ih = inner
anchors_ref = [
    ('total_w 底', ox + ow/2,  oy + oh + oh*0.12),
    ('total_h 左', ox - ow*0.12, oy + oh/2),
    ('margin_top', ox + ow/2,  oy + max(0, iy-oy)/2),
    ('margin_bot', ox + ow/2,  iy + ih + max(0, (oy+oh)-(iy+ih))/2),
    ('margin_left',ox + (ix-ox)/2, iy + ih/2),
    ('margin_r',   ix + iw + ((ox+ow)-(ix+iw))/2, iy + ih/2),
    ('inner_w',    ix + iw/2, iy + ih*0.35),
    ('inner_h',    ix + iw/2, iy + ih*0.65),
]
for name, ax, ay in anchors_ref:
    print(f'  {name}: ({ax:.1f}, {ay:.1f})')

os.unlink(tmp_path)
