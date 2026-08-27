"""
测试 force_clear_gap 不触发导致的米黄色残留
"""
import sys
sys.path.insert(0, "D:/SmartShapeCrop")

import numpy as np
from PIL import Image, ImageDraw
from core.corner.sector_render import _redraw_border_on_corner
from core.corner.algorithm import carve_corner_on_mask
from core.config import DEFAULT_BG_COLOR, DEFAULT_DPI, cm_to_px
from core.corner.sector_render import CORNER_ANGLES

output_dir = r"D:\SmartShapeCrop\scripts\logs\output"

dpi = DEFAULT_DPI
bg_color = DEFAULT_BG_COLOR

target_w_cm = 70.0
target_h_cm = 105.0
r_cm = 2.3

w = cm_to_px(target_w_cm, dpi)
h = cm_to_px(target_h_cm, dpi)
r_px = cm_to_px(r_cm, dpi)

BLACK_OUTER = (25, 22, 20)
CREAM_GAP_DETECTED = (245, 235, 220)  # 检测到的间隙层颜色
CREAM_GAP_ACTUAL = (220, 210, 195)    # 实际间隙颜色有较大偏差
BROWN_INNER = (150, 95, 65)
CREAM_CONTENT = (245, 235, 220)

t_black = 6
t_gap = 20
t_brown = 40

img = Image.new('RGB', (w, h), CREAM_CONTENT)
draw = ImageDraw.Draw(img)
for off in range(t_black):
    draw.rectangle([off, off, w-1-off, h-1-off], outline=BLACK_OUTER, width=1)
cum2 = t_black + t_gap
# 用实际偏差颜色画间隙
for off in range(t_black, cum2):
    draw.rectangle([off, off, w-1-off, h-1-off], outline=CREAM_GAP_ACTUAL, width=1)
for off in range(cum2, cum2 + t_brown):
    draw.rectangle([off, off, w-1-off, h-1-off], outline=BROWN_INNER, width=1)

# 手动指定 border_layers，间隙层颜色用检测到的颜色（可能与实际不同）
border_layers = [(BLACK_OUTER, t_black), (CREAM_GAP_DETECTED, t_gap), (BROWN_INNER, t_brown)]

# 构造 mask
mask = Image.new('L', (w, h), 255)
carve_corner_on_mask(mask, (0, 0, w, h), {'tl': r_px, 'tr': r_px, 'bl': r_px, 'br': r_px})

result = Image.new('RGB', (w, h), bg_color)
result.paste(img, mask=mask)

# 调用 _redraw_border_on_corner
for ck in ['tl', 'tr', 'bl', 'br']:
    _redraw_border_on_corner(
        result, ck, r_px, border_layers,
        src_img=img, validity_mask=mask,
        only_outermost=False, bg_color=bg_color
    )

out = f"{output_dir}/force_clear_test_TL.jpg"
result.crop((0, 0, min(400, w), min(400, h))).save(out, 'JPEG', quality=95)
print(f"saved {out}")

# 检查间隙层区域
R = min(r_px, max(1, min(w, h)//2))
cx, cy = R, R
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

cum_depths = [0]
for _, t in border_layers:
    cum_depths.append(cum_depths[-1] + t)

gap_start, gap_end = cum_depths[1], cum_depths[2]
gap_depth_mask = (depth_v >= gap_start) & (depth_v < gap_end)
in_sector = (angle_v >= ang_min) & (angle_v <= ang_max) & (dist_v <= R + 1)
check_mask = gap_depth_mask & in_sector
pixels = roi[check_mask]
N = len(pixels)
if N > 0:
    actual_c = np.array(CREAM_GAP_ACTUAL, dtype=np.float64)
    detected_c = np.array(CREAM_GAP_DETECTED, dtype=np.float64)
    bg_c = np.array(bg_color, dtype=np.float64)
    d_actual = np.sqrt(np.sum((pixels.astype(np.float64) - actual_c.reshape(1,3))**2, axis=1))
    d_detected = np.sqrt(np.sum((pixels.astype(np.float64) - detected_c.reshape(1,3))**2, axis=1))
    d_bg = np.sqrt(np.sum((pixels.astype(np.float64) - bg_c.reshape(1,3))**2, axis=1))
    n_actual = np.sum(d_actual < 25)
    n_detected = np.sum(d_detected < 28)  # COLOR_DIST_THRESHOLD + 10
    n_bg = np.sum(d_bg < 30)
    print(f"gap pixels N={N}, near_actual={n_actual}, near_detected={n_detected} (clear), near_bg={n_bg}")
