import cv2
import numpy as np
import pytesseract

img = cv2.imread('scripts/diagnose/_test_user_sketch.png')
print('尺寸:', img.shape[:2])

# 检测文字
data = pytesseract.image_to_data(img, config='--oem 3 --psm 11', lang='chi_sim+eng', output_type=pytesseract.Output.DICT)
print('\n检测到的文字:')
for i in range(len(data.get('text', []))):
    text = str(data['text'][i]).strip()
    conf = int(data.get('conf', [0] * len(data.get('text')))[i])
    if text and conf > 10:
        try:
            x = int(data.get('left', [0] * len(data.get('text')))[i])
            y = int(data.get('top', [0] * len(data.get('text')))[i])
            w = int(data.get('width', [0] * len(data.get('text')))[i])
            h = int(data.get('height', [0] * len(data.get('text')))[i])
            print(f'  text={repr(text):20s} conf={conf:3d} pos=({x:4d},{y:4d}) size=({w:3d}x{h:3d})')
        except:
            print(f'  text={repr(text):20s} conf={conf:3d}')
