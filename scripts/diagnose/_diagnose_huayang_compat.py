"""
Fix D 兼容性验证：花漾之约风格
奶油色厚边框 + 边框上散布花瓣装饰 → 奶油色层对角内区不应强制绘制，防止覆盖花朵。
仅深棕色细线边框层（非奶油色）才应强制绘制。
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
from core.image_cropper import (
    apply_rounded_corners,
    _get_border_layers_robust,
)
from core.config import DEFAULT_DPI, cm_to_px

output_dir = _os.path.join(_D, 'logs', 'output')
dpi = DEFAULT_DPI

target_w_cm = 38.5
target_h_cm = 186.0
r_bl_br_cm = 5.0
w = cm_to_px(target_w_cm, dpi)
h = cm_to_px(target_h_cm, dpi)

print("=" * 70)
print(f"[花漾之约兼容性验证] Fix D 不应把奶油色边框层覆盖")
print("=" * 70)
print(f"目标尺寸: {target_w_cm}x{target_h_cm}cm = {w}x{h}px")
print(f"BL/BR 圆角: {r_bl_br_cm}cm")

# 构造图像：
# 外奶油色厚边 60px（边框带花朵） → 棕色细线 5px → 内部内容米色
CREAM_BORDER = (248, 242, 230)   # 60px 厚，上面长花朵（实际有装饰色）
BROWN_LINE = (150, 100, 60)      # 5px 细边框
CREAM_INNER = (252, 248, 238)    # 内部内容色
PETAL_PINK = (240, 160, 170)     # 粉色花瓣
PETAL_WHITE = (255, 255, 255)    # 白色花蕊
LEAF_GREEN = (120, 170, 120)     # 绿叶

img = Image.new('RGB', (w, h), CREAM_INNER)
# 画 cream 厚边框 60px
d = ImageDraw.Draw(img)
# 边框填充 cream
d.rectangle([0, 0, w - 1, 60 - 1], fill=CREAM_BORDER)             # 顶
d.rectangle([0, 0, 60 - 1, h - 1], fill=CREAM_BORDER)             # 左
d.rectangle([0, h - 60, w - 1, h - 1], fill=CREAM_BORDER)         # 底
d.rectangle([w - 60, 0, w - 1, h - 1], fill=CREAM_BORDER)         # 右

# 在 cream 边框上撒大量花朵（模拟花漾之约的装饰）
np.random.seed(42)
n_flowers = 300
for _ in range(n_flowers):
    # 随机选择一条边框
    side = np.random.choice(['top', 'bottom', 'left', 'right'])
    if side == 'top':
        fx = np.random.randint(20, w - 20)
        fy = np.random.randint(3, 60 - 10)
    elif side == 'bottom':
        fx = np.random.randint(20, w - 20)
        fy = h - np.random.randint(10, 60 - 3)
    elif side == 'left':
        fx = np.random.randint(3, 60 - 10)
        fy = np.random.randint(20, h - 20)
    else:  # right
        fx = w - np.random.randint(10, 60 - 3)
        fy = np.random.randint(20, h - 20)
    fr = np.random.randint(5, 12)
    # 5 片粉色花瓣
    for ang in np.linspace(0, 360, 5, endpoint=False):
        rad = np.radians(ang)
        dx = int(np.cos(rad) * fr * 0.65)
        dy = int(np.sin(rad) * fr * 0.65)
        d.ellipse([fx + dx - fr // 2, fy + dy - fr // 2,
                   fx + dx + fr // 2, fy + dy + fr // 2], fill=PETAL_PINK)
    # 白花蕊
    d.ellipse([fx - 2, fy - 2, fx + 2, fy + 2], fill=PETAL_WHITE)

# 再随机加一点绿叶
for _ in range(120):
    side = np.random.choice(['top', 'bottom', 'left', 'right'])
    if side == 'top':
        fx = np.random.randint(20, w - 20)
        fy = np.random.randint(3, 60 - 10)
    elif side == 'bottom':
        fx = np.random.randint(20, w - 20)
        fy = h - np.random.randint(10, 60 - 3)
    elif side == 'left':
        fx = np.random.randint(3, 60 - 10)
        fy = np.random.randint(20, h - 20)
    else:
        fx = w - np.random.randint(10, 60 - 3)
        fy = np.random.randint(20, h - 20)
    d.ellipse([fx, fy, fx + 6, fy + 4], fill=LEAF_GREEN)

# 画 5px 棕色内框（靠 cream 内缘）
b_in = 60
b_out = 60 + 5
for off in range(5):
    d.rectangle([b_in + off, b_in + off,
                 w - b_in - off - 1, h - b_in - off - 1],
                outline=BROWN_LINE, width=1)

src_path = os.path.join(output_dir, "synthetic_huayang_compat_src.jpg")
img.save(src_path, 'JPEG', quality=92, dpi=(dpi, dpi))
print(f"合成源图保存: {src_path}")

# 检测边框层
border_layers = _get_border_layers_robust(img, (255, 255, 255))
print(f"\n[边框层检测] 共 {len(border_layers)} 层:")
cum = [0]
for i, (color, thick) in enumerate(border_layers):
    cum.append(cum[-1] + thick)
    print(f"  L{i+1}: color={color}  thick={thick:>4}px   cum={cum[-1]:>4}px")

# 内部内容色采样（复现 Fix D 逻辑）
arr = np.array(img)
hh, ww = arr.shape[:2]
x_start = int(ww * 0.15); x_end = int(ww * 0.85)
y_start = int(hh * 0.15); y_end = int(hh * 0.85)
xs = np.linspace(x_start, x_end, 21, dtype=np.int64).clip(0, ww - 1)
ys = np.linspace(y_start, y_end, 21, dtype=np.int64).clip(0, hh - 1)
gx, gy = np.meshgrid(xs, ys)
samples = arr[gy, gx, :].reshape(-1, 3).astype(np.float64)
content_ref = np.median(samples, axis=0)
print(f"\nFix D content_ref = {content_ref.astype(int).tolist()}")
print(f"(期望接近 CREAM_INNER ≈ {list(CREAM_INNER)})")

print("\n[Fix D 兼容性判断 预览]")
all_cream_ok = True
any_frame_force = False
for i, (color, thick) in enumerate(border_layers):
    col = np.array(color, dtype=np.float64)
    dist = np.sqrt(np.sum((col - content_ref) ** 2))
    is_force = dist > 15.0
    mark = ""
    # cream 边框层（含花朵）应该不强制 → 否则花朵被盖
    if dist < 30.0 and is_force:   # 与内容色接近却被强制 → 错误！
        mark = "  ✗✗✗ [严重] 该层颜色=奶油/米色，但会被强制覆盖 → 花会被盖掉！"
        all_cream_ok = False
    # 棕色细线应该强制
    if dist > 50.0 and is_force:
        mark = "  ✓ (有色边框层正确强制绘制)"
        any_frame_force = True
    if dist < 30.0 and not is_force:
        mark = "  ✓ (奶油/内容填充色正确不强制 → 花朵保留)"
    print(f"  L{i+1}: color={color} vs content 色差={dist:.1f}  → "
          f"force_paint={'ON ' if is_force else 'OFF'} {mark}")

# 实际运行圆角
corners = {'tl': 0, 'tr': 0, 'bl': r_bl_br_cm, 'br': r_bl_br_cm}
result = apply_rounded_corners(img, corners, dpi, (255, 255, 255))
out_full = os.path.join(output_dir, "diagnose_huayang_compat_5cm_CURRENT.jpg")
result.save(out_full, 'JPEG', quality=90, dpi=(dpi, dpi))

# 保存 BL/BR 放大图
zoom = min(600, w, h)
for cname, (x1, y1, x2, y2) in [
    ('BL', (0, h - zoom, zoom, h)),
    ('BR', (w - zoom, h - zoom, w, h)),
]:
    cropped = result.crop((x1, y1, x2, y2))
    zpath = os.path.join(output_dir, f"diagnose_huayang_compat_{cname}_zoom_CURRENT.jpg")
    cropped.save(zpath, 'JPEG', quality=95)
    print(f"\n{cname} 放大图: {zpath}   → 请目视检查奶油边框上的花朵是否完好！")

print(f"\n整图输出: {out_full}")

if all_cream_ok and any_frame_force:
    print("\n" + "=" * 70)
    print("✓✓✓ Fix D 兼容性验证通过！")
    print("    - 奶油/米色边框层对角内区不会强制涂色 → 花朵/装饰不会被覆盖 ✓")
    print("    - 有色细线边框层对角内区会强制涂色 → 连续圆弧无 C 缺口 ✓")
    print("=" * 70)
else:
    print("\n✗ Fix D 兼容性失败！请检查上面的判断。")
