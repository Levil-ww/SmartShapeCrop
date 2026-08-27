"""
验证 Mask 剪切策略：塞纳时光 4cm 四角圆角
目标：只有最外层边框形成圆角，内部线条保持直线并被自然截断。
"""

# ============================================================
# PROJECT_ROOT auto-inject (added by test-dir cleanup 2026-08-11)
# 脚本从 scripts/ 子目录运行时仍能正确定位 core/, psd_demo/, Test/output 等
import sys as _sys
import os as _os
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))
_D = str(_PROJECT_ROOT)
# ============================================================

import os
import sys
import numpy as np
from PIL import Image, ImageDraw
# (removed by cleanup: see PROJECT_ROOT auto-inject above)
from core.image_cropper import apply_rounded_corners
from core.config import DEFAULT_DPI, cm_to_px

output_dir = _os.path.join(_D, 'logs', 'output')
os.makedirs(output_dir, exist_ok=True)
dpi = DEFAULT_DPI

target_w_cm = 78.5
target_h_cm = 128.5
r_cm_all = 4.0
target_w_px = cm_to_px(target_w_cm, dpi)
target_h_px = cm_to_px(target_h_cm, dpi)

print("=" * 70)
print(f"[验证] 新 Mask 剪切策略：塞纳时光风格")
print("=" * 70)
print(f"目标尺寸: {target_w_cm}x{target_h_cm}cm = {target_w_px}x{target_h_px}px @ {dpi}dpi")

# 构造合成图像：外黑色边框 + 内部两条棕色平行线 + 米色内区
# 重点：内部线条保持矩形，不应被绘制为圆弧
OUTER_BLACK = (20, 20, 20)
BROWN_DARK = (153, 107, 60)
BROWN_LIGHT = (178, 130, 82)
CREAM_INNER = (252, 248, 238)

# 尺寸
w, h = target_w_px, target_h_px
img = Image.new('RGB', (w, h), CREAM_INNER)
d = ImageDraw.Draw(img)

# 1. 画内部米色区域 (留出边框空间)
margin = int(round(20 * dpi / 2.54))  # 20px approx
inner_x1, inner_y1 = margin, margin
inner_x2, inner_y2 = w - margin - 1, h - margin - 1

# 2. 画棕色双线 (保持直角矩形)
brown1_width = int(round(5 * dpi / 2.54))  # 5px
brown2_width = int(round(5 * dpi / 2.54))  # 5px
gap = int(round(3 * dpi / 2.54))  # 3px gap

# 外棕线
d.rectangle([inner_x1, inner_y1, inner_x2, inner_y2], outline=BROWN_LIGHT, width=brown2_width)
# 内棕线 (比外棕线内缩 gap)
inner_x1b = inner_x1 + brown2_width + gap
inner_y1b = inner_y1 + brown2_width + gap
inner_x2b = inner_x2 - brown2_width - gap
inner_y2b = inner_y2 - brown2_width - gap
d.rectangle([inner_x1b, inner_y1b, inner_x2b, inner_y2b], outline=BROWN_DARK, width=brown1_width)

# 3. 画最外层黑色边框 (最靠外)
outer_width = int(round(30 * dpi / 2.54))  # 30px
# 黑色外框紧贴图像边缘
d.rectangle([0, 0, w - 1, h - 1], outline=OUTER_BLACK, width=outer_width)

# 保存原图
src_path = os.path.join(output_dir, "verify_sainashiguang_src.jpg")
img.save(src_path, 'JPEG', quality=95, dpi=(dpi, dpi))

# 4. 应用四角 4cm 圆角
corners = {'tl': r_cm_all, 'tr': r_cm_all, 'bl': r_cm_all, 'br': r_cm_all}
result = apply_rounded_corners(img, corners, dpi, (255, 255, 255))

# 保存结果
out_path = os.path.join(output_dir, "verify_sainashiguang_4cm_rounded_CURRENT.jpg")
result.save(out_path, 'JPEG', quality=95, dpi=(dpi, dpi))

# 5. 放大图检查
zoom = min(500, w, h)
for cname, (x1, y1, x2, y2) in [
    ('TL', (0, 0, zoom, zoom)),
    ('TR', (w - zoom, 0, w, zoom)),
    ('BL', (0, h - zoom, zoom, h)),
    ('BR', (w - zoom, h - zoom, w, h)),
]:
    cropped = result.crop((x1, y1, x2, y2))
    zpath = os.path.join(output_dir, f"verify_sainashiguang_{cname}_zoom_CURRENT.jpg")
    cropped.save(zpath, 'JPEG', quality=95)
    print(f"\n{cname} 放大图: {zpath}")

print("\n" + "=" * 70)
print("验证完成。请检查：")
print("1. 最外层黑色边框是否形成了圆弧（圆角）。")
print("2. 内部两条棕色线条是否保持了直线，但在圆角处被自然截断。")
print("3. 是否消除了色差和拼接感（颜色应与原图一致）。")
print("=" * 70)
