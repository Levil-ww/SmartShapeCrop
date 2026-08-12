"""
验证脚本：修复后塞纳时光 80x160cm 四角 5cm 圆角
对比修复前后，确认间隙层不再产生米黄色多余弧线
"""
import sys
import os
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
_D = str(_PROJECT_ROOT)

import numpy as np
from PIL import Image, ImageDraw
from core.image_cropper import (
    apply_border_only_corners,
    _get_border_layers_robust,
)
from core.config import DEFAULT_BG_COLOR, DEFAULT_DPI, cm_to_px, px_to_cm

output_dir = os.path.join(_D, 'logs', 'output')
os.makedirs(output_dir, exist_ok=True)
dpi = DEFAULT_DPI
bg_color = DEFAULT_BG_COLOR

# =========== 目标：80x160cm 四角 5cm 圆角 ===========
target_w_cm = 80.0
target_h_cm = 160.0
r_cm_all = 5.0
target_w_px = cm_to_px(target_w_cm, dpi)
target_h_px = cm_to_px(target_h_cm, dpi)
r_px = cm_to_px(r_cm_all, dpi)

print("=" * 70)
print(f"[验证] 塞纳时光修复后：{target_w_cm}x{target_h_cm}cm 四角 {r_cm_all}cm 圆角")
print("=" * 70)

# =========== 构造同样的塞纳时光风格边框图 ===========
BLACK_OUTER = (25, 22, 20)
CREAM_GAP = (245, 235, 220)
BROWN_INNER = (150, 95, 65)
CREAM_CONTENT = (245, 235, 220)

t_black = 6
t_gap = 20
t_brown = 40

w, h = target_w_px, target_h_px
img = Image.new('RGB', (w, h), CREAM_CONTENT)
draw = ImageDraw.Draw(img)
cum1 = t_black
for off in range(cum1):
    draw.rectangle([off, off, w-1-off, h-1-off], outline=BLACK_OUTER, width=1)
cum3 = cum1 + t_gap + t_brown
for off in range(cum1 + t_gap, cum3):
    draw.rectangle([off, off, w-1-off, h-1-off], outline=BROWN_INNER, width=1)

# =========== 检测边框层 + 确认间隙层识别 ===========
border_layers = _get_border_layers_robust(img, bg_color)
print(f"\n[检测] {len(border_layers)} 层边框：")
for i, (color, thickness) in enumerate(border_layers):
    print(f"  L{i+1}: {color}  {thickness}px ({px_to_cm(thickness, dpi):.2f}cm)")

# =========== 应用圆角（修复后） ===========
corners = {'tl': r_cm_all, 'tr': r_cm_all, 'bl': r_cm_all, 'br': r_cm_all}
print(f"\n[修复后圆角裁剪] 运行中...")
result = apply_border_only_corners(img.copy(), corners, dpi, bg_color)
out_path = os.path.join(output_dir, "seine_gap_VERIFY_result_AFTER_FIX.jpg")
result.save(out_path, 'JPEG', quality=95, dpi=(dpi, dpi))
print(f"  结果：{out_path}")

# 保存四角放大
zoom = min(600, w, h)
for cname, box in [('TL', (0,0,zoom,zoom)), ('TR', (w-zoom,0,w,zoom)),
                   ('BL', (0,h-zoom,zoom,h)), ('BR', (w-zoom,h-zoom,w,h))]:
    z = result.crop(box)
    zp = os.path.join(output_dir, f"seine_gap_VERIFY_{cname}_AFTER_FIX.jpg")
    z.save(zp, 'JPEG', quality=95)
    print(f"  {cname}放大：{zp}")

# =========== 像素级验证：检查圆角区域内的颜色分布 ===========
print(f"\n[像素级验证] 检查 TL 角扇形区内是否还有间隙层的米黄色绘制：")
R = min(r_px, max(1, min(w, h)//2))
cx, cy = R, R
from core.corner.sector_render import CORNER_ANGLES
ang_min, ang_max = CORNER_ANGLES['tl']

x1, y1 = max(0, cx-R), max(0, cy-R)
x2, y2 = min(w, cx+R+1), min(h, cy+R+1)
result_arr = np.array(result)
roi = result_arr[y1:y2, x1:x2, :]
yy_g, xx_g = np.mgrid[y1:y2, x1:x2].astype(np.float64)
dx = xx_g - float(cx)
dy = yy_g - float(cy)
dist_v = np.sqrt(dx*dx + dy*dy)
angle_v = np.degrees(np.arctan2(dy, dx))
angle_v = np.mod(angle_v, 360.0)
depth_v = float(R) - dist_v

# 间隙层深度范围（L2: cum=7~27px）
gap_depth_mask = (depth_v >= 7) & (depth_v < 27)
in_sector = (angle_v >= ang_min) & (angle_v <= ang_max) & (dist_v <= R + 1)
check_mask = gap_depth_mask & in_sector

pixels_in_gap_sector = roi[check_mask]
N = len(pixels_in_gap_sector)

if N == 0:
    print("  间隙层扇形区内无像素（正确）")
else:
    gap_color_arr = np.array(CREAM_GAP, dtype=np.float64)
    bg_color_arr = np.array(bg_color, dtype=np.float64)
    d_to_gap = np.sqrt(np.sum((pixels_in_gap_sector.astype(np.float64) - gap_color_arr.reshape(1,3))**2, axis=1))
    d_to_bg = np.sqrt(np.sum((pixels_in_gap_sector.astype(np.float64) - bg_color_arr.reshape(1,3))**2, axis=1))

    n_match_gap = int(np.sum(d_to_gap < 15.0))  # 与米黄色接近
    n_match_bg = int(np.sum(d_to_bg < 30.0))    # 与背景色接近

    print(f"  间隙层扇形区内像素 N = {N}")
    print(f"    与间隙色(米黄)接近 d<15  的像素: {n_match_gap}  ({n_match_gap*100/N:.1f}%)")
    print(f"    与背景色(白)  接近 d<30  的像素: {n_match_bg}  ({n_match_bg*100/N:.1f}%)")
    print(f"    其他有色像素: {N - n_match_gap - n_match_bg}")

    if n_match_bg * 1.0 / N > 0.90:
        print("\n  ✓ ✓✓ 验证通过！间隙层区域 >90% 是背景色(白色)，")
        print("       没有多余的米黄色弧线被绘制出来！")
    else:
        print(f"\n  ✗ 验证失败！仍有 {n_match_gap} 个间隙色像素，")
        print(f"    占比 {n_match_gap*100/N:.1f}%，可能还存在米黄色弧线。")

# =========== 同时运行项目的 pytest 回归测试 ===========
print("\n" + "=" * 70)
print("[回归测试] 运行 pytest 确保修复不破坏其他功能...")
print("=" * 70)
import subprocess
result_pytest = subprocess.run(
    ["python", "-m", "pytest", "tests/", "-v", "--tb=short", "-x"],
    cwd=_D, capture_output=True, text=True, timeout=300
)
print(f"pytest 返回码：{result_pytest.returncode}")
if result_pytest.stdout:
    # 只显示最后 30 行
    lines = result_pytest.stdout.strip().splitlines()
    print("\n".join(lines[-30:]))
if result_pytest.returncode != 0 and result_pytest.stderr:
    print("\n--- STDERR ---")
    print(result_pytest.stderr[-1500:])

if result_pytest.returncode == 0:
    print("\n✓ 所有 pytest 回归测试通过！修复没有破坏其他功能逻辑。")
else:
    print("\n✗ 部分回归测试失败，请检查。")
