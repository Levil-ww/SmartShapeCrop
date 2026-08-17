"""检查工作草图的OCR输出，理解其文本布局"""
import cv2
import pytesseract
import numpy as np
from PIL import Image

# 读取工作草图
img = cv2.imread('scripts/diagnose/_test_user_sketch.png')
print(f"图像尺寸: {img.shape}")

# 全图OCR
print("\n=== 全图OCR结果 ===")
data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, lang='eng')
for i in range(len(data['text'])):
    text = data['text'][i].strip()
    conf = int(data['conf'][i])
    if text and conf > 30:
        x, y, w, h = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
        print(f"  text='{text}' conf={conf} pos=({x},{y}) size=({w},{h})")

# 检查是否有中文
print("\n=== 尝试中文OCR ===")
try:
    data_cn = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, lang='chi_sim')
    for i in range(len(data_cn['text'])):
        text = data_cn['text'][i].strip()
        conf = int(data_cn['conf'][i])
        if text and conf > 30:
            x, y, w, h = data_cn['left'][i], data_cn['top'][i], data_cn['width'][i], data_cn['height'][i]
            print(f"  text='{text}' conf={conf} pos=({x},{y}) size=({w},{h})")
except Exception as e:
    print(f"  中文OCR失败: {e}")

# 显示图像基本信息
h, w = img.shape[:2]
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# 检查红色像素位置
hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
mask_r1 = cv2.inRange(hsv, np.array([0, 20, 30]), np.array([15, 255, 255]))
mask_r2 = cv2.inRange(hsv, np.array([165, 20, 30]), np.array([180, 255, 255]))
mask_red = cv2.bitwise_or(mask_r1, mask_r2)

# 红色像素的边界框
ys, xs = np.where(mask_red > 0)
if len(xs) > 0:
    print(f"\n=== 红色元素 ===")
    print(f"  红色像素: {len(xs)}")
    print(f"  红色边界: x=[{xs.min()},{xs.max()}], y=[{ys.min()},{ys.max()}]")

# 检查蓝色像素位置
mask_b = cv2.inRange(hsv, np.array([100, 20, 30]), np.array([130, 255, 255]))
ys, xs = np.where(mask_b > 0)
if len(xs) > 0:
    print(f"\n=== 蓝色元素 ===")
    print(f"  蓝色像素: {len(xs)}")
    print(f"  蓝色边界: x=[{xs.min()},{xs.max()}], y=[{ys.min()},{ys.max()}]")
