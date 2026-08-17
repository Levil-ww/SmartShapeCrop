"""测试方向标签识别功能（上/下/左/右 + 数值）。

生成一张带方向标签的草图，验证模板匹配检测和数字关联。
"""
import cv2
import numpy as np
import os
import sys

# 生成测试草图
W, H = 800, 600
img = np.ones((H, W, 3), dtype=np.uint8) * 255
black = (0, 0, 0)

# 外框位置
pad = 80
ox, oy = pad, pad + 40
ow, oh = W - 2 * pad, H - 2 * pad - 40

# 内框位置（模拟 234×60 画布，边距 上6/下9/左36/右112）
# 比例: 左36/(36+112)=24.3%, 右112/(36+112)=75.7%
# 上6/(6+9)=40%, 下9/(6+9)=60%
ml_frac = 36.0 / (36.0 + 112.0)
mr_frac = 112.0 / (36.0 + 112.0)
mt_frac = 6.0 / (6.0 + 9.0)
mb_frac = 9.0 / (6.0 + 9.0)

ix = int(ox + ow * ml_frac)
iy = int(oy + oh * mt_frac)
iw = int(ow * (86.0 / 234.0))   # inner_w = 234-36-112=86
ih = int(oh * (45.0 / 60.0))    # inner_h = 60-6-9=45

# 绘制外框和内框
cv2.rectangle(img, (ox, oy), (ox + ow, oy + oh), black, 2)
cv2.rectangle(img, (ix, iy), (ix + iw, iy + ih), black, 2)

# 用 PIL 绘制中文方向标签 + 数字
from PIL import Image, ImageDraw, ImageFont

pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
draw = ImageDraw.Draw(pil_img)

# 尝试加载中文字体
cn_font = None
for fp in [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simsun.ttc']:
    if os.path.isfile(fp):
        cn_font = ImageFont.truetype(fp, 20)
        break
if cn_font is None:
    print("ERROR: 未找到中文字体")
    sys.exit(1)

num_font = ImageFont.truetype("arial.ttf", 18)

# 标注方向标签 + 数值（方向字和数字紧邻）
# 上6 - 在上边距区域
draw.text((ox + ow // 2 - 15, oy + 5), "上6", fill=black, font=cn_font)
# 下9 - 在下边距区域
draw.text((ox + ow // 2 - 15, oy + oh - 25), "下9", fill=black, font=cn_font)
# 左36 - 在左边距区域
draw.text((ox + 5, oy + oh // 2 - 10), "左36", fill=black, font=cn_font)
# 右112 - 在右边距区域
draw.text((ix + iw + 5, oy + oh // 2 - 10), "右112", fill=black, font=cn_font)

img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

# 保存测试图
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_test_dir_labels.png")
cv2.imwrite(out_path, img)
print(f"测试草图已保存: {out_path}")
print(f"外框像素: ({ox},{oy}) {ow}x{oh}")
print(f"内框像素: ({ix},{iy}) {iw}x{ih}")

# 测试方向标签检测
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from core.pool_designer.sketch_parser import (
    _detect_direction_labels_by_template,
    _detect_direction_labels_by_ocr,
    _match_direction_labels_to_numbers,
    _try_direction_label_fast_track,
)

gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
outer_rect = (ox, oy, ow, oh)
inner_rect = (ix, iy, iw, ih)

print("\n=== 测试1: 模板匹配检测方向标签 ===")
labels = _detect_direction_labels_by_template(cv2, gray, outer_rect)
print(f"检测到 {len(labels)} 个方向标签:")
for dchar, mfield, xc, yc, conf, val in labels:
    print(f"  {dchar} → {mfield}  位置=({xc:.0f},{yc:.0f})  置信度={conf:.3f}")

print("\n=== 测试2: OCR检测方向标签（Tesseract可能不可用）===")
try:
    import pytesseract
    tesseract = pytesseract
    labels_ocr = _detect_direction_labels_by_ocr(cv2, gray, tesseract, outer_rect)
    print(f"检测到 {len(labels_ocr)} 个方向标签:")
    for dchar, mfield, xc, yc, conf, val in labels_ocr:
        print(f"  {dchar} → {mfield}  位置=({xc:.0f},{yc:.0f})  置信度={conf}  值={val}")
except Exception as e:
    print(f"  Tesseract不可用: {e}")
    labels_ocr = []

print("\n=== 测试3: 模拟OCR数字hits（模拟数字识别结果）===")
# 模拟 OCR 识别到的数字及其位置
# 上6的位置: (ox + ow//2, oy + 15)
# 下9的位置: (ox + ow//2, oy + oh - 15)
# 左36的位置: (ox + 25, oy + oh//2)
# 右112的位置: (ix + iw + 25, oy + oh//2)
ocr_hits = [
    (6.0, ox + ow // 2 + 5, oy + 15, 0.9),
    (9.0, ox + ow // 2 + 5, oy + oh - 15, 0.9),
    (36.0, ox + 25, oy + oh // 2, 0.9),
    (112.0, ix + iw + 30, oy + oh // 2, 0.9),
]
print(f"模拟OCR hits: {[(h[0], f'({h[1]:.0f},{h[2]:.0f})') for h in ocr_hits]}")

# 合并检测到的方向标签（模板匹配 + OCR）
all_labels = labels + labels_ocr
# 去重：同一字段只保留一个
seen_fields = {}
for dl in all_labels:
    if dl[1] not in seen_fields:
        seen_fields[dl[1]] = dl
all_labels = list(seen_fields.values())

print(f"\n合并后方向标签: {len(all_labels)} 个")
print("\n=== 测试4: 方向标签与数字关联 ===")
margin_result = _match_direction_labels_to_numbers(all_labels, ocr_hits, outer_rect)
if margin_result:
    print("关联成功!")
    for field, (val, conf) in margin_result.items():
        print(f"  {field} = {val}  (置信度={conf})")
else:
    print("关联失败: 未找到全部4个边距")

print("\n=== 测试5: 完整快速通道 ===")
fast_result = _try_direction_label_fast_track(
    cv2, gray, None, outer_rect, ocr_hits,
    target_w_hint=234.0, target_h_hint=60.0
)
if fast_result:
    print("快速通道命中!")
    for key, (val, conf) in fast_result.items():
        print(f"  {key} = {val}  (置信度={conf})")
else:
    print("快速通道未命中")
