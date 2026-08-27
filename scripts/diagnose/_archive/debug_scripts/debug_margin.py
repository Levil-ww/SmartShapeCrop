"""调试边距OCR识别"""
import cv2
import numpy as np
from PIL import Image as PILImage
import pytesseract

# 加载测试草图
img = cv2.imread(r'd:\SmartShapeCrop\scripts\diagnose\_test_user_sketch.png')
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

h, w = gray.shape[:2]

# 外框信息
ox, oy, ow, oh = 46, 46, 971, 475

# 仔细检查右边距区域
print('=== 右边距区域详细分析 ===')

# 保存右边距高分辨率图像
right_region = gray[oy-10:oy+oh+10, ox+ow-10:w]
cv2.imwrite(r'd:\SmartShapeCrop\scripts\diagnose\debug_right_hires.png', right_region)

# 尝试更精确的OCR
for scale in [4, 6, 8]:
    scaled = cv2.resize(right_region, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    # 尝试多种预处理
    variants = [
        ('orig', scaled),
        ('binary', cv2.threshold(scaled, 127, 255, cv2.THRESH_BINARY)[1]),
        ('otsu', cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]),
    ]
    
    for vname, variant in variants:
        for psm in [6, 7, 8, 11, 13]:
            config = f'--oem 3 --psm {psm}'
            data = pytesseract.image_to_data(PILImage.fromarray(variant), config=config, output_type=pytesseract.Output.DICT)
            for i in range(len(data.get('text', []))):
                text = data['text'][i].strip()
                if text:
                    try:
                        conf = int(data['conf'][i])
                    except:
                        conf = 0
                    if conf >= 20:
                        print(f'scale={scale}, {vname}, psm={psm}: text="{text}", conf={conf}')

# 也检查左边距
print('\n=== 左边距区域详细分析 ===')
left_region = gray[oy-10:oy+oh+10, 0:ox+10]
cv2.imwrite(r'd:\SmartShapeCrop\scripts\diagnose\debug_left_hires.png', left_region)

for scale in [4, 6, 8]:
    scaled = cv2.resize(left_region, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    variants = [
        ('orig', scaled),
        ('binary', cv2.threshold(scaled, 127, 255, cv2.THRESH_BINARY)[1]),
        ('otsu', cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]),
    ]
    
    for vname, variant in variants:
        for psm in [6, 7, 8, 11, 13]:
            config = f'--oem 3 --psm {psm}'
            data = pytesseract.image_to_data(PILImage.fromarray(variant), config=config, output_type=pytesseract.Output.DICT)
            for i in range(len(data.get('text', []))):
                text = data['text'][i].strip()
                if text:
                    try:
                        conf = int(data['conf'][i])
                    except:
                        conf = 0
                    if conf >= 20:
                        print(f'scale={scale}, {vname}, psm={psm}: text="{text}", conf={conf}')
