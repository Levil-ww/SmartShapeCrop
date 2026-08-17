"""测试方向标签识别 - 分离标注场景（方向字和数字分开写）。

模拟用户将方向标签和数字分写的情况：上 6 / 下 9 / 左 36 / 右 112
"""
import cv2
import numpy as np
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

W, H = 800, 600
img = np.ones((H, W, 3), dtype=np.uint8) * 255
black = (0, 0, 0)

pad = 80
ox, oy = pad, pad + 40
ow, oh = W - 2 * pad, H - 2 * pad - 40

ml_frac = 36.0 / (36.0 + 112.0)
mt_frac = 6.0 / (6.0 + 9.0)
ix = int(ox + ow * ml_frac)
iy = int(oy + oh * mt_frac)
iw = int(ow * (86.0 / 234.0))
ih = int(oh * (45.0 / 60.0))

cv2.rectangle(img, (ox, oy), (ox + ow, oy + oh), black, 2)
cv2.rectangle(img, (ix, iy), (ix + iw, iy + ih), black, 2)

from PIL import Image, ImageDraw, ImageFont

pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
draw = ImageDraw.Draw(pil_img)

cn_font_path = None
for fp in [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc']:
    if os.path.isfile(fp):
        cn_font_path = fp
        break
cn_font = ImageFont.truetype(cn_font_path, 20)
num_font = ImageFont.truetype("arial.ttf", 18)

# 分离标注：方向字和数字有间距
# 上 6 - 上边距区域，方向字在左数字在右
draw.text((ox + ow // 2 - 25, oy + 5), "上", fill=black, font=cn_font)
draw.text((ox + ow // 2 + 5, oy + 8), "6", fill=black, font=num_font)
# 下 9
draw.text((ox + ow // 2 - 25, oy + oh - 25), "下", fill=black, font=cn_font)
draw.text((ox + ow // 2 + 5, oy + oh - 22), "9", fill=black, font=num_font)
# 左 36
draw.text((ox + 5, oy + oh // 2 - 20), "左", fill=black, font=cn_font)
draw.text((ox + 5, oy + oh // 2 + 5), "36", fill=black, font=num_font)
# 右 112
draw.text((ix + iw + 5, oy + oh // 2 - 20), "右", fill=black, font=cn_font)
draw.text((ix + iw + 5, oy + oh // 2 + 5), "112", fill=black, font=num_font)

img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_test_dir_separate.png")
cv2.imwrite(out_path, img)
print(f"分离标注草图已保存: {out_path}")

from core.pool_designer.sketch_parser import (
    _detect_direction_labels_by_template,
    _match_direction_labels_to_numbers,
    _try_direction_label_fast_track,
)

gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
outer_rect = (ox, oy, ow, oh)

# 模拟 OCR 数字 hits（数字位置略偏离方向字位置）
ocr_hits = [
    (6.0, ox + ow // 2 + 12, oy + 12, 0.9),
    (9.0, ox + ow // 2 + 12, oy + oh - 18, 0.9),
    (36.0, ox + 12, oy + oh // 2 + 12, 0.9),
    (112.0, ix + iw + 12, oy + oh // 2 + 12, 0.9),
]

print("\n=== 模板匹配检测方向标签 ===")
labels = _detect_direction_labels_by_template(cv2, gray, outer_rect)
print(f"检测到 {len(labels)} 个方向标签:")
for dchar, mfield, xc, yc, conf, val in labels:
    print(f"  {dchar} → {mfield}  位置=({xc:.0f},{yc:.0f})  置信度={conf:.3f}")

print("\n=== 关联数字 ===")
margin_result = _match_direction_labels_to_numbers(labels, ocr_hits, outer_rect)
if margin_result:
    print("关联成功!")
    for field, (val, conf) in margin_result.items():
        print(f"  {field} = {val}")
else:
    print("关联失败")

print("\n=== 完整快速通道 ===")
fast_result = _try_direction_label_fast_track(
    cv2, gray, None, outer_rect, ocr_hits,
    target_w_hint=234.0, target_h_hint=60.0
)
if fast_result:
    print("快速通道命中!")
    for key, (val, conf) in fast_result.items():
        print(f"  {key} = {val}")
else:
    print("快速通道未命中")
