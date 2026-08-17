"""调试图2的空间推理"""
import sys, os, logging
sys.path.insert(0, '.')

logger = logging.getLogger('core.pool_designer.sketch_parser')
logger.setLevel(logging.DEBUG)

# Capture output
import io
log_capture = io.StringIO()
handler = logging.StreamHandler(log_capture)
handler.setLevel(logging.DEBUG)
handler.setFormatter(logging.Formatter('%(message)s'))
logger.addHandler(handler)

import cv2
import numpy as np
import pytesseract

# Load image
img = cv2.imread('scripts/diagnose/_test_sketch2.png')
print(f"图像尺寸: {img.shape}")

# 1. Get full OCR
print("\n=== 全图OCR ===")
data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, lang='chi_sim+eng')
hits = []
for i in range(len(data['text'])):
    text = data['text'][i].strip()
    conf = int(data['conf'][i])
    if text and conf > 20:
        # Try to extract number
        try:
            v = float(text)
        except:
            # Try without Chinese chars
            import re
            nums = re.findall(r'[\d.]+', text)
            if nums:
                try:
                    v = float(nums[0])
                except:
                    continue
            else:
                continue
        x, y, w, h = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
        cx, cy = x + w // 2, y + h // 2
        hits.append((v, cx, cy, conf))
        print(f"  text='{text}' value={v} conf={conf} pos=({cx},{cy})")

# 2. Load and find outer rect
from core.pool_designer.sketch_parser import _load_image, _find_two_nested_rectangles

img2, err = _load_image('scripts/diagnose/_test_sketch2.png')
gray = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)

top2 = _find_two_nested_rectangles(cv2, gray, img2)
if len(top2) >= 2:
    (ox, oy, ow, oh, os_score), (ix, iy, iw, ih, ins_score) = top2
    print(f"\n外框: ({ox},{oy},{ow},{oh})")
    print(f"内框: ({ix},{iy},{iw},{ih})")
    print(f"外框范围: x=[{ox},{ox+ow}], y=[{oy},{oy+oh}]")
    print(f"外框中心: ({ox+ow//2},{oy+oh//2})")

# 3. Check each OCR value position relative to outer frame
print("\n=== OCR值与外框位置关系 ===")
for v, cx, cy, conf in hits:
    rel = ""
    if cx < ox:
        rel = "LEFT"
    elif cx > ox + ow:
        rel = "RIGHT"
    elif cy < oy:
        rel = "TOP"
    elif cy > oy + oh:
        rel = "BOTTOM"
    else:
        rel = "INSIDE"
    print(f"  v={v:.1f} at ({cx},{cy}) conf={conf} → {rel}")

# 4. Run spatial reasoning
from core.pool_designer.sketch_parser import _assign_margins_by_spatial_reasoning
print("\n=== 空间推理结果 ===")
result = _assign_margins_by_spatial_reasoning(hits, (ox, oy, ow, oh))
for k, v in result.items():
    print(f"  {k}: {v}")

# Print log
log_capture.seek(0)
print("\n=== 日志 ===")
print(log_capture.getvalue())
