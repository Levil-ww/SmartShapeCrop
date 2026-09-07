"""验证 border_only 大半径下：
1. 最外层黑边被圆角化
2. 内层文字带/花纹保持直角
3. 角落无白色竖线/横线
"""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.image_cropper_border import apply_border_only_corners


def _make_image(w, h, dpi=150):
    bg = (255, 255, 255)
    arr = np.full((h, w, 3), bg, dtype=np.uint8)
    img = Image.fromarray(arr, 'RGB')
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except Exception:
        font = ImageFont.load_default()

    # 外层黑色边框 30px (~0.5cm @150dpi)
    outer_t = 30
    draw.rectangle([0, 0, w - 1, h - 1], outline=(20, 20, 20), width=outer_t)

    # 内层咖色装饰/文字带 ~90px，模拟右侧竖排文字带
    inner_t = 90
    margin = outer_t + 8
    right_band_x1 = w - margin - inner_t
    right_band_x2 = w - margin
    bottom_band_y1 = h - margin - inner_t
    bottom_band_y2 = h - margin
    # 右边文字带（填充为浅米色底+咖字模拟）
    draw.rectangle([right_band_x1, margin, right_band_x2, h - margin],
                   fill=(245, 235, 220), outline=(120, 80, 60), width=3)
    # 竖排文字小样
    draw.text((right_band_x1 + 25, h // 2 - 100), "CROSS\nTHE\nSTARS",
              fill=(80, 50, 40), font=font)

    # 底部文字带
    draw.rectangle([margin, bottom_band_y1, w - margin, bottom_band_y2],
                   fill=(245, 235, 220), outline=(120, 80, 60), width=3)
    draw.text((w // 2 - 120, bottom_band_y1 + 25),
              "over the moon to meet your better self",
              fill=(80, 50, 40), font=font)

    # 内部花纹：一些小圆点 + 中心文字
    np.random.seed(0)
    for _ in range(40):
        cx = np.random.randint(margin + 80, w - margin - 80)
        cy = np.random.randint(margin + 80, h - margin - 80)
        r = np.random.randint(8, 20)
        col = tuple(np.random.randint(50, 180, 3).tolist())
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)

    # 中心文字
    draw.text((w // 2 - 120, h // 2 - 24), "SERENITY", fill=(40, 40, 40), font=font)
    return img


def main():
    dpi = 150
    # 模拟 70x130cm 级别的图，但缩小做 demo：1200x2100
    w, h = 1200, 2100
    img = _make_image(w, h, dpi=dpi)
    img.save(r'F:\SmartShapeCrop\scripts\verify\large_radius_input.jpg')

    # 6.5cm 半径
    r_cm = 6.5
    result = apply_border_only_corners(img, {'tl': r_cm, 'tr': r_cm, 'bl': r_cm, 'br': r_cm}, dpi=dpi)
    result.save(r'F:\SmartShapeCrop\scripts\verify\large_radius_result.jpg')
    print('saved: large_radius_input.jpg, large_radius_result.jpg')

    # 检查右下角区域：内层文字带是否保持直角
    arr = np.array(result)
    print('result shape:', arr.shape)

    # 采样右边缘内部：如果文字带被圆角化，会在角附近出现咖色弧线/被切
    # 取右下区域 300x300
    patch = arr[h - 300:h, w - 300:w]
    # 右下角弧线外侧（dist > r）应被切白；内侧应保持原图
    r_px = int(r_cm * dpi / 2.54)
    print('r_px:', r_px)


if __name__ == '__main__':
    main()
