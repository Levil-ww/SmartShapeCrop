"""
诊断脚本：塞纳时光 80x160cm 四角 5cm 圆角 - 米黄色多余弧线问题

用户描述：圆弧角处还有米黄色的缺口弧线，应该只有最外层的黑色圆角边框线，
没有多余的米黄色弧线（就像人工PS的一样）。

本脚本构造与塞纳时光相同的边框结构：
  最外：黑色细线 ≈ 6px (0.1cm)
  间隙：米色 ≈ 20px (与内容色相同)
  内层：棕色粗线 ≈ 40px (0.7cm)
  内容：米色
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
    apply_rounded_corners,
    _get_border_layers_robust,
)
from core.corner.sector_render import _redraw_border_on_corner, CORNER_ANGLES
from core.config import DEFAULT_BG_COLOR, DEFAULT_DPI, cm_to_px, px_to_cm

output_dir = os.path.join(_D, 'logs', 'output')
os.makedirs(output_dir, exist_ok=True)
dpi = DEFAULT_DPI
bg_color = DEFAULT_BG_COLOR  # (255, 255, 255)

# =========== 目标：80x160cm 四角 5cm 圆角 ===========
target_w_cm = 80.0
target_h_cm = 160.0
r_cm_all = 5.0
target_w_px = cm_to_px(target_w_cm, dpi)
target_h_px = cm_to_px(target_h_cm, dpi)
r_px = cm_to_px(r_cm_all, dpi)

print("=" * 70)
print(f"塞纳时光诊断：{target_w_cm}x{target_h_cm}cm 四角 {r_cm_all}cm 圆角")
print("=" * 70)
print(f"像素尺寸: {target_w_px}x{target_h_px}px @ {dpi}dpi")
print(f"圆角半径: {r_cm_all}cm = {r_px}px")
print(f"5cm >= 4cm → 所有嵌套层都应做圆角")

# =========== 构造塞纳时光风格边框图 ===========
# 真实塞纳时光结构（从用户截图推断）：
#   外黑边(约6px) → 米色间隙(约20px) → 棕内边(约40px) → 内容米色
BLACK_OUTER = (25, 22, 20)
CREAM_GAP = (245, 235, 220)      # 间隙米色 ≈ 内容色
BROWN_INNER = (150, 95, 65)      # 内层棕色
CREAM_CONTENT = (245, 235, 220)  # 内容色 ≈ 间隙色

t_black = 6
t_gap = 20
t_brown = 40

w, h = target_w_px, target_h_px
img = Image.new('RGB', (w, h), CREAM_CONTENT)
draw = ImageDraw.Draw(img)

# 外层黑色边（第1层，最外）
cum1 = t_black
for off in range(cum1):
    draw.rectangle([off, off, w-1-off, h-1-off], outline=BLACK_OUTER, width=1)

# 间隙层（第2层，米色，与内容色同）→ 不用画，背景就是
cum2 = cum1 + t_gap

# 内层棕色边（第3层）
cum3 = cum2 + t_brown
for off in range(cum2, cum3):
    draw.rectangle([off, off, w-1-off, h-1-off], outline=BROWN_INNER, width=1)

src_path = os.path.join(output_dir, "seine_gap_diag_src.jpg")
img.save(src_path, 'JPEG', quality=95, dpi=(dpi, dpi))
print(f"\n[构造完成] 源图: {src_path}")
print(f"  边框结构 (由外→内):")
print(f"    L1 黑色: {t_black}px ({px_to_cm(t_black, dpi):.2f}cm)  color={BLACK_OUTER}")
print(f"    L2 米色间隙: {t_gap}px ({px_to_cm(t_gap, dpi):.2f}cm)  color={CREAM_GAP} (≈内容色)")
print(f"    L3 棕色: {t_brown}px ({px_to_cm(t_brown, dpi):.2f}cm)  color={BROWN_INNER}")

# =========== Step 1: 检测边框层 ===========
border_layers = _get_border_layers_robust(img, bg_color)
print(f"\n[边框层检测] 程序检测到 {len(border_layers)} 层:")
cum = []
total_t = 0
for i, (color, thickness) in enumerate(border_layers):
    total_t += thickness
    cum.append(total_t)
    cm_val = px_to_cm(thickness, dpi)
    print(f"  L{i+1}: color={color}  thick={thickness:>4}px ({cm_val:.2f}cm)  cum={total_t:>5}px")
print(f"  总厚度 T_total = {total_t}px = {px_to_cm(total_t, dpi):.2f}cm")

# =========== Step 2: 识别间隙层（基于 content_ref 距离） ===========
arr = np.array(img)
def _sample_content_ref(arr, ww, hh):
    x_start = int(ww * 0.15)
    x_end = int(ww * 0.85)
    y_start = int(hh * 0.15)
    y_end = int(hh * 0.85)
    STEPS = 21
    xs = np.linspace(x_start, x_end, STEPS, dtype=np.int64).clip(0, ww-1)
    ys = np.linspace(y_start, y_end, STEPS, dtype=np.int64).clip(0, hh-1)
    gx, gy = np.meshgrid(xs, ys)
    samples = arr[gy, gx, :].reshape(-1, 3).astype(np.float64)
    return np.median(samples, axis=0)

content_ref = _sample_content_ref(arr, w, h)
print(f"\n[内容参考色] content_ref = {content_ref.astype(int).tolist()}")
GAP_DIST = 30.0
is_gap_layer = []
for i, (color, t) in enumerate(border_layers):
    d = float(np.sqrt(np.sum((np.array(color, dtype=np.float64) - content_ref)**2)))
    is_gap = d < GAP_DIST
    is_gap_layer.append(is_gap)
    status = "✓ 间隙层 (跳过绘制)" if is_gap else "✗ 边框层 (需要绘制)"
    print(f"  L{i+1} color={color}  与内容色距离={d:.1f}  → {status}")

# =========== Step 3: 运行圆角裁剪（当前程序行为） ===========
corners = {'tl': r_cm_all, 'tr': r_cm_all, 'bl': r_cm_all, 'br': r_cm_all}
print(f"\n[运行圆角裁剪] 当前程序行为...")
result = apply_border_only_corners(img.copy(), corners, dpi, bg_color)
out_path = os.path.join(output_dir, "seine_gap_diag_result_CURRENT.jpg")
result.save(out_path, 'JPEG', quality=95, dpi=(dpi, dpi))
print(f"  结果: {out_path}")

# 保存四角放大
zoom = min(600, w, h)
for cname, box in [('TL', (0,0,zoom,zoom)), ('TR', (w-zoom,0,w,zoom)),
                   ('BL', (0,h-zoom,zoom,h)), ('BR', (w-zoom,h-zoom,w,h))]:
    z = result.crop(box)
    zp = os.path.join(output_dir, f"seine_gap_diag_{cname}_CURRENT.jpg")
    z.save(zp, 'JPEG', quality=95)
    print(f"  {cname}放大: {zp}")

# =========== Step 4: 详细分析 TL 角每层的绘制行为 ===========
print(f"\n" + "=" * 70)
print(f"[详细分析] TL 角每层绘制行为（是否产生米黄色弧线？）")
print("=" * 70)
R = min(r_px, max(1, min(w, h)//2))
cx, cy = R, R
ang_min, ang_max = CORNER_ANGLES['tl']

x1, y1 = max(0, cx-R), max(0, cy-R)
x2, y2 = min(w, cx+R+1), min(h, cy+R+1)
yy, xx = np.mgrid[y1:y2, x1:x2].astype(np.float64)
dx = xx - float(cx)
dy = yy - float(cy)
dist_vals = np.sqrt(dx*dx + dy*dy)
angle_vals = np.degrees(np.arctan2(dy, dx))
angle_vals = np.mod(angle_vals, 360.0)
depth_vals = float(R) - dist_vals

cum_depths = [0]
for _, t in border_layers:
    cum_depths.append(cum_depths[-1] + t)

for i, (color, thickness) in enumerate(border_layers):
    cum_before = cum_depths[i]
    cum_after = cum_depths[i+1]
    # 该层在扇形中的像素
    in_ang = (angle_vals >= ang_min) & (angle_vals <= ang_max)
    in_layer = (depth_vals >= cum_before) & (depth_vals < cum_after)
    in_valid = (dist_vals <= R + 2.0)
    mask = in_ang & in_layer & in_valid
    
    N_layer = int(np.sum(mask))
    if N_layer == 0:
        continue
    
    # 直边扩展带 vs 对角内区
    xs = xx[mask].astype(np.int64)
    ys = yy[mask].astype(np.int64)
    if 'tl' == 'tl':
        in_xstrip = (xs >= cum_before) & (xs < cum_after)
        in_ystrip = (ys >= cum_before) & (ys < cum_after)
    in_ext = in_xstrip | in_ystrip
    diag_interior = ~in_ext
    
    is_gap = is_gap_layer[i]
    col_arr = np.array(color, dtype=np.float64)
    layer_pixels_src = arr[ys + y1, xs + x1, :].astype(np.float64)
    
    d_to_content = float(np.sqrt(np.sum((col_arr - content_ref)**2)))
    
    # 估算：有多少像素会被 force_paint 画出来
    if is_gap:
        # 间隙层：line 664-705 有兜底填充
        n_diag = int(np.sum(diag_interior))
        # 需要深度落在本层范围内才会填
        di_depths = depth_vals[mask]
        di_in = (di_depths >= cum_before) & (di_depths < cum_after)
        n_fill = int(np.sum(diag_interior & di_in))
        print(f"\n  L{i+1} 间隙层 color={color}  thick={thickness}px")
        print(f"    与内容色距离={d_to_content:.1f}  → is_gap=True")
        print(f"    扇形区内像素总数: {N_layer}")
        print(f"    直边扩展带(match_filter区): {int(np.sum(in_ext))} ({int(np.sum(in_ext))*100/N_layer:.0f}%)")
        print(f"    对角内区(force_paint区): {int(np.sum(diag_interior))} ({int(np.sum(diag_interior))*100/N_layer:.0f}%)")
        print(f"    ★ [问题所在] line 664-705 间隙层兜底填充会在对角内区填 {n_fill} 像素")
        print(f"       颜色 = {color} (米黄色) → 这就是用户看到的多余米黄色弧线！")
    else:
        n_force = int(np.sum(diag_interior))
        print(f"\n  L{i+1} 边框层 color={color}  thick={thickness}px")
        print(f"    与内容色距离={d_to_content:.1f}  → is_gap=False")
        print(f"    扇形区内像素总数: {N_layer}")
        print(f"    对角内区强制绘制数: {n_force} (这是正确的，需要有色边框圆弧)")

print("\n" + "=" * 70)
print("根因分析结论：")
print("=" * 70)
print("""
  当 is_gap_layer=True（间隙层与内容色接近）时：
  sector_render.py line 664-705 的兜底逻辑会在对角内区强制填充
  间隙层自身的颜色（米黄色）。这在视觉上形成了一条独立的米黄色弧线。

  正确的行为（人工PS效果）：
  间隙层在圆角处应该表现为"空"（即背景色/内容色），不应该被画成
  独立的弧线。圆角处只需要最外层有色边框的圆弧线。

  修复方案：
  对于 is_gap_layer=True 的层，在圆角重绘时：
    1) 跳过对角内区的兜底填充（不填充间隙色）
    2) 整体跳过该层的 force_paint 和 match_filter 绘制
  这样间隙层区域就保持为裁切后的背景色，没有多余的米黄色弧线。
""")
