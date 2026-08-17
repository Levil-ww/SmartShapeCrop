"""Quick diagnostic to check OCR positions and direction label positions."""
import cv2, pytesseract, re
import numpy as np
from PIL import Image as PILImage

img = cv2.imread('scripts/diagnose/_test_user_sketch.png')
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
h_img, w_img = gray.shape[:2]
print(f'Image size: {w_img}x{h_img}')

# Full image OCR with PSM 11
pil_img = PILImage.fromarray(gray)
data = pytesseract.image_to_data(pil_img, config='--oem 3 --psm 11', output_type=pytesseract.Output.DICT)

print()
print('Full image OCR results with numeric values (PSM 11):')
n = len(data.get('text', []))
for i in range(n):
    text = str(data['text'][i]).strip()
    if text:
        try:
            conf = int(data.get('conf', [0]*n)[i])
        except:
            conf = 0
        if conf >= 10:
            x = int(data['left'][i])
            y = int(data['top'][i])
            w = int(data['width'][i])
            h = int(data['height'][i])
            cx = x + w//2
            cy = y + h//2
            nums = re.findall(r'(\d+\.?\d*)', text)
            if nums:
                print(f'  "{text}" conf={conf:3d} box=({x},{y})-({x+w},{y+h}) center=({cx:4d},{cy:4d}) values={nums}')

# Also run with PSM 6
print()
print('Full image OCR with PSM 6:')
data2 = pytesseract.image_to_data(pil_img, config='--oem 3 --psm 6', output_type=pytesseract.Output.DICT)
n2 = len(data2.get('text', []))
for i in range(n2):
    text = str(data2['text'][i]).strip()
    if text:
        try:
            conf = int(data2.get('conf', [0]*n2)[i])
        except:
            conf = 0
        if conf >= 10:
            x = int(data2['left'][i])
            y = int(data2['top'][i])
            w = int(data2['width'][i])
            h = int(data2['height'][i])
            cx = x + w//2
            cy = y + h//2
            nums = re.findall(r'(\d+\.?\d*)', text)
            if nums:
                print(f'  "{text}" conf={conf:3d} box=({x},{y})-({x+w},{y+h}) center=({cx:4d},{cy:4d}) values={nums}')

# Also detect direction labels by template matching
print()
print('Template matching direction labels:')
import sys
sys.path.insert(0, '.')
from core.pool_designer.sketch_parser import _detect_direction_labels_by_template

# Find outer rect first
from core.pool_designer.sketch_parser import parse_sketch
# Let's just manually check positions

# Run the sketch parser to get the outer rect
import logging
logging.basicConfig(level=logging.WARNING)
result = parse_sketch('scripts/diagnose/_test_user_sketch.png', target_outer_w_cm=120.0, target_outer_h_cm=58.0)
print(f'Parse result: outer={result.outer_w_cm}x{result.outer_h_cm}, inner={result.inner_w_cm}x{result.inner_h_cm}')
print(f'Margins: top={result.margin_top_cm}, bottom={result.margin_bottom_cm}, left={result.margin_left_cm}, right={result.margin_right_cm}')
