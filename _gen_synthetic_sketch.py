"""生成一张合成草图，模拟用户描述的手绘草图：
  外框 133cm (宽) x 60.5cm (高)
  内挖 76cm (宽) x 44.5cm (高)
  边距: 上6/下10/左14.6/右42.4
  在白色背景上绘制黑色线条和数字标注
"""
import cv2
import numpy as np
import os

# 画布大小 (像素) - 模拟用户草图 556x406 比例
W, H = 800, 600

# 创建白色背景
img = np.ones((H, W, 3), dtype=np.uint8) * 255

# 颜色
black = (0, 0, 0)
red = (0, 0, 255)
green = (0, 128, 0)

# 外框位置 (留出标注空间)
pad = 80
ox, oy = pad, pad + 40
ow, oh = W - 2 * pad, H - 2 * pad - 40

# 内框位置 (基于边距比例)
# 比例: 左边距14.6, 右边距42.4 -> 左占14.6/57=25.6%, 右占42.4/57=74.4%
# 上边距6, 下边距10 -> 上占6/16=37.5%, 下占10/16=62.5%
ml_frac = 14.6 / (14.6 + 42.4)  # 0.256
mr_frac = 42.4 / (14.6 + 42.4)  # 0.744
mt_frac = 6.0 / (6.0 + 10.0)    # 0.375
mb_frac = 10.0 / (6.0 + 10.0)   # 0.625

ix = int(ox + ow * ml_frac)
iy = int(oy + oh * mt_frac)
iw = int(ow * (76.0 / 133.0))  # inner width ratio
ih = int(oh * (44.5 / 60.5))   # inner height ratio

# 绘制外框 (黑色矩形)
cv2.rectangle(img, (ox, oy), (ox + ow, oy + oh), black, 2)

# 绘制内框 (黑色矩形)
cv2.rectangle(img, (ix, iy), (ix + iw, iy + ih), black, 2)

# 标注数值 - 使用 PIL 绘制中文和数字
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

# 转换为 PIL
pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
draw = ImageDraw.Draw(pil_img)

# 尝试加载字体
try:
    font = ImageFont.truetype("arial.ttf", 22)
    small_font = ImageFont.truetype("arial.ttf", 18)
except:
    font = ImageFont.load_default()
    small_font = ImageFont.load_default()

# === 标注外框尺寸 ===
# 底部: 133 (外框总宽)
draw.text((ox + ow // 2 - 25, oy + oh + 10), "133", fill=black, font=font)
# 左侧: 60.5 (外框总高) - 竖排
draw.text((ox - 50, oy + oh // 2 - 10), "60.5", fill=black, font=font)

# === 标注边距 ===
# 上边距: 6
draw.text((ox + ow // 2 - 5, oy + 5), "6", fill=black, font=small_font)
# 下边距: 10
draw.text((ox + ow // 2 - 8, oy + oh - 22), "10", fill=black, font=small_font)
# 左边距: 14.6
draw.text((ox - 55, oy + oh // 2 - 8), "14.6", fill=black, font=small_font)
# 右边距: 42.4
draw.text((ox + ow + 5, oy + oh // 2 - 8), "42.4", fill=black, font=small_font)

# === 标注内框尺寸 ===
# 内框宽: 76
draw.text((ix + iw // 2 - 10, iy + 5), "76", fill=black, font=small_font)
# 内框高: 44.5
draw.text((ix + iw + 5, iy + ih // 2 - 8), "44.5", fill=black, font=small_font)

# 转回 OpenCV
img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

# 保存
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_synthetic_sketch.png")
cv2.imwrite(out_path, img)
print(f"合成草图已保存: {out_path}")
print(f"图像尺寸: {W}x{H}")
print(f"外框像素: ({ox},{oy}) {ow}x{oh}")
print(f"内框像素: ({ix},{iy}) {iw}x{ih}")

# 运行诊断
print(f"\n运行诊断脚本...")
import subprocess
result = subprocess.run(
    [sys.executable, "_diag_ocr.py", out_path, "133.0", "60.5"],
    capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__))
)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr)
