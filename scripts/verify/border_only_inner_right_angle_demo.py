"""演示 apply_border_only_corners 仅对外层边框圆角，内层矩形框保持直角。"""
import os
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.image_cropper import apply_border_only_corners

w, h = 1000, 700
bg = (255, 255, 255)
img = Image.new('RGB', (w, h), bg)
draw = ImageDraw.Draw(img)

# 外层黑色边框 6px
draw.rectangle([0, 0, w-1, h-1], outline=(0, 0, 0), width=6)

# 白色间隙 12px（从 6 到 18 不画）

# 内层咖色装饰边框 3px
draw.rectangle([18, 18, w-1-18, h-1-18], outline=(139, 90, 43), width=3)

# 内部米色内容区
arr = np.array(img)
arr[21:h-21, 21:w-21] = (250, 245, 235)

# 在内部画一个黑色矩形框（模拟内层矩形框/文字区域）
arr[180:220, 120:880] = (0, 0, 0)

# 文字
img = Image.fromarray(arr, 'RGB')
draw = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype("arial.ttf", 48)
except Exception:
    font = ImageFont.load_default()
draw.text((w//2 - 150, h//2 - 24), "Inner content keeps right angles", fill=(80, 80, 80), font=font)

result = apply_border_only_corners(img, {'tl': 4.0, 'tr': 4.0, 'bl': 4.0, 'br': 4.0}, dpi=150, bg_color=bg)

out_path = os.path.join(os.path.dirname(__file__), 'border_only_inner_right_angle_demo.jpg')
result.save(out_path, 'JPEG', quality=95)
print(f'已保存: {out_path}')
