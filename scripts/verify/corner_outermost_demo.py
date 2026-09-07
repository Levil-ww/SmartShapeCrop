"""验证 apply_border_only_corners 只重绘最外层边框的简易脚本。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
from PIL import Image
from core.image_cropper import apply_border_only_corners

w, h = 1200, 800
img = Image.new('RGB', (w, h), (255, 255, 255))
arr = np.array(img)

# 外层黑边 4px
arr[:, :4] = (0, 0, 0)
arr[:, -4:] = (0, 0, 0)
arr[:4, :] = (0, 0, 0)
arr[-4:, :] = (0, 0, 0)

# 浅色间隙 12px (4-16)
arr[:, 4:16] = (245, 235, 220)
arr[:, -16:-4] = (245, 235, 220)
arr[4:16, :] = (245, 235, 220)
arr[-16:-4, :] = (245, 235, 220)

# 内层深棕装饰边 6px (16-22)
arr[:, 16:22] = (120, 90, 70)
arr[:, -22:-16] = (120, 90, 70)
arr[16:22, :] = (120, 90, 70)
arr[-22:-16, :] = (120, 90, 70)

img = Image.fromarray(arr, 'RGB')

result = apply_border_only_corners(img, {'tl': 3.0, 'tr': 3.0, 'bl': 3.0, 'br': 3.0}, dpi=150)

out_path = os.path.join(os.path.dirname(__file__), 'corner_outermost_demo.jpg')
result.save(out_path, 'JPEG', quality=95)
print(f'已保存: {out_path}')
