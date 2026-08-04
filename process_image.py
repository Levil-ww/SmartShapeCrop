"""
图片等比缩放 + 右下角圆角处理脚本
源图：双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg
目标：55x41cm + 右下角圆角2cm
"""
from PIL import Image, ImageDraw
import os

# 提高像素上限：业务常处理印刷级超大图（如 EPS 栅格化后超过 1 亿像素），
# 默认 89478485 像素会触发 DecompressionBombWarning，设为 None 关闭限制。
Image.MAX_IMAGE_PIXELS = None

# ============ 参数配置 ============
src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
output_dir = r"D:\SmartShapeCrop\psd_demo"
output_name = "双面格-定制-定制尺寸-简织;竖版55x41cm右下角圆角半径2cm.jpg"

target_w_cm = 41.0      # 目标宽度（厘米，竖版短边）
target_h_cm = 55.0      # 目标高度（厘米，竖版长边）
corner_r_cm = 2.0       # 右下角圆角半径（厘米）
dpi = 300               # DPI

# ============ 计算像素 ============
target_w_px = int(round(target_w_cm * dpi / 2.54))
target_h_px = int(round(target_h_cm * dpi / 2.54))
corner_r_px = int(round(corner_r_cm * dpi / 2.54))

print(f"目标尺寸: {target_w_px} x {target_h_px} px ({target_w_cm} x {target_h_cm} cm @ {dpi} DPI)")
print(f"圆角半径: {corner_r_px} px ({corner_r_cm} cm)")

# ============ 1. 加载源图 ============
src = Image.open(src_path)
if src.mode != 'RGB':
    src = src.convert('RGB')
print(f"源图尺寸: {src.size} px")

# ============ 2. Cover 模式等比缩放裁剪 ============
sw, sh = src.size
scale = max(target_w_px / sw, target_h_px / sh)
nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
resized = src.resize((nw, nh), Image.LANCZOS)

# 居中裁剪
left = (nw - target_w_px) // 2
top = (nh - target_h_px) // 2
cropped = resized.crop((left, top, left + target_w_px, top + target_h_px))
print(f"缩放后尺寸: {nw} x {nh}, 裁剪区域: ({left}, {top}) - ({left + target_w_px}, {top + target_h_px})")

# ============ 3. 添加右下角圆角 ============
w, h = cropped.size
r = corner_r_px

# 创建遮罩：先全图设为255（不透明）
mask = Image.new('L', (w, h), 255)
draw = ImageDraw.Draw(mask)

# 两步法：1.挖正方形  2.填回 1/4 圆
# 1. 先把右下角 r×r 正方形区域设为0（挖空）
draw.rectangle([w - r, h - r, w, h], fill=0)
# 2. 填回右下 1/4 圆（圆心在 (w-r, h-r)，角度 0°→90° 即右下象限）
# [实测] PIL 屏幕坐标系：0°=右, 90°=下, 180°=左, 270°=上
draw.pieslice([w - 2*r, h - 2*r, w, h], start=0, end=90, fill=255)

# 应用遮罩：圆角处填充白色（可改为透明或其他颜色）
result = Image.new('RGB', (w, h), (255, 255, 255))
result.paste(cropped, mask=mask)

# ============ 4. 保存 ============
output_path = os.path.join(output_dir, output_name)
result.save(output_path, 'JPEG', quality=95, optimize=True, dpi=(dpi, dpi))
print(f"\n[完成] 已保存: {output_path}")
print(f"输出尺寸: {result.size} px")