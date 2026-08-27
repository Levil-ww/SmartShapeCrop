"""
诊断脚本：塞纳时光 78.5x128.5cm 四角 4cm 圆角 之 C 形缺口问题
模拟：米色外区 + 细棕色双线边框 + 米色内区
验证 Fix D (混合策略) 是否正确把细棕色线掰弯成连续圆弧，消除 C 形缺口。
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
    apply_border_only_corners,
    apply_rounded_corners,
    _get_border_layers_robust,
)
from core.config import DEFAULT_BG_COLOR, DEFAULT_DPI, cm_to_px, px_to_cm

output_dir = _os.path.join(_D, 'logs', 'output')
os.makedirs(output_dir, exist_ok=True)
dpi = DEFAULT_DPI
bg_color = DEFAULT_BG_COLOR  # (255, 255, 255)

# =========== 目标尺寸与参数（塞纳时光 规格） ===========
target_w_cm = 78.5
target_h_cm = 128.5
r_cm_all = 4.0
target_w_px = cm_to_px(target_w_cm, dpi)
target_h_px = cm_to_px(target_h_cm, dpi)

print("=" * 70)
print(f"合成场景：塞纳时光风格 细边框圆角诊断")
print("=" * 70)
print(f"目标尺寸: {target_w_cm}x{target_h_cm}cm = {target_w_px}x{target_h_px}px @ {dpi}dpi")
print(f"四角圆角半径: {r_cm_all}cm = {cm_to_px(r_cm_all, dpi)}px")

# =========== 构造合成图像：米色外区 + 两条细棕色平行线 + 米色内区 ===========
# 典型边框样式：外奶油(≈20px空白) → 棕线1(≈5px) → 奶油间隙(≈8px) → 棕线2(≈5px) → 奶油内区
CREAM_OUTER = (248, 244, 234)   # 外区米色（与产品底色接近）
BROWN_DARK = (153, 107, 60)      # 深棕色（主线）
BROWN_LIGHT = (178, 130, 82)     # 次棕色（副线）
CREAM_INNER = (250, 246, 236)    # 内区米色（略浅于外区，模拟两张不同的纸）

outer_margin_px = 20      # 外空白边
brown1_thick = 5
gap_between = 8
brown2_thick = 5

w, h = target_w_px, target_h_px
img = Image.new('RGB', (w, h), CREAM_OUTER)
d = ImageDraw.Draw(img)

# 画棕色双线框（矩形）
# 外棕线（brown2 外层更粗的那条，靠近外边）
x1_o, y1_o = outer_margin_px, outer_margin_px
x2_o, y2_o = w - outer_margin_px - 1, h - outer_margin_px - 1
for off in range(brown2_thick):
    d.rectangle([x1_o + off, y1_o + off, x2_o - off, y2_o - off],
                outline=BROWN_LIGHT, width=1)

# 内棕线（brown1 内层，较深）
inner_offset = outer_margin_px + brown2_thick + gap_between
x1_i, y1_i = inner_offset, inner_offset
x2_i, y2_i = w - inner_offset - 1, h - inner_offset - 1
for off in range(brown1_thick):
    d.rectangle([x1_i + off, y1_i + off, x2_i - off, y2_i - off],
                outline=BROWN_DARK, width=1)

# 内区用 CREAM_INNER 填充矩形（覆盖掉双线框之间和内部的外米色）
fill_x1 = outer_margin_px + brown2_thick
fill_y1 = outer_margin_px + brown2_thick
fill_x2 = w - outer_margin_px - brown2_thick - 1
fill_y2 = h - outer_margin_px - brown2_thick - 1
d.rectangle([fill_x1, fill_y1, fill_x2, fill_y2], fill=CREAM_INNER)

# 重画两条棕线（因为上面 fill 覆盖了）
for off in range(brown2_thick):
    d.rectangle([x1_o + off, y1_o + off, x2_o - off, y2_o - off],
                outline=BROWN_LIGHT, width=1)
for off in range(brown1_thick):
    d.rectangle([x1_i + off, y1_i + off, x2_i - off, y2_i - off],
                outline=BROWN_DARK, width=1)

# 在内区放三张简单卡片，模拟用户的 花/花瓶/豹 三张装饰图
mid_y = h // 2
card_w = target_w_px // 5
card_h = card_w
gap = card_w // 2
total_3_w = card_w * 3 + gap * 2
cx_left = (w - total_3_w) // 2

# 左卡：墨绿色底 + 花纹示意
card1 = Image.new('RGB', (card_w, card_h), (70, 110, 90))
d1 = ImageDraw.Draw(card1)
for _ in range(12):
    rx = np.random.randint(0, card_w - 1)
    ry = np.random.randint(0, card_h - 1)
    rr = np.random.randint(8, 25)
    d1.ellipse([rx - rr, ry - rr, rx + rr, ry + rr], fill=(230, 228, 200), outline=(40, 50, 40), width=2)
img.paste(card1, (cx_left, mid_y - card_h // 2))

# 中卡：米色底 + 棕色花瓶示意
card2 = Image.new('RGB', (card_w, card_h), (230, 220, 195))
d2 = ImageDraw.Draw(card2)
d2.rectangle([card_w * 0.25, card_h * 0.1, card_w * 0.75, card_h * 0.9],
             outline=(160, 110, 60), width=4)
d2.polygon(
    [(card_w // 2, int(card_h * 0.15)),
     (int(card_w * 0.15), int(card_h * 0.85)),
     (int(card_w * 0.85), int(card_h * 0.85))],
    fill=(180, 120, 70), outline=(130, 85, 40)
)
img.paste(card2, (cx_left + card_w + gap, mid_y - card_h // 2))

# 右卡：黑色底 + 点状虚线（模拟豹纹卡四周虚线边框）
card3 = Image.new('RGB', (card_w, card_h), (30, 28, 28))
d3 = ImageDraw.Draw(card3)
# 四周虚线框
step = 10
dash = 5
for xs in range(0, card_w - 1, step):  # 顶边
    d3.line([(xs, 5), (min(xs + dash, card_w - 1), 5)], fill=(240, 230, 200), width=2)
    d3.line([(xs, card_h - 6), (min(xs + dash, card_w - 1), card_h - 6)], fill=(240, 230, 200), width=2)
for ys in range(0, card_h - 1, step):  # 侧边
    d3.line([(5, ys), (5, min(ys + dash, card_h - 1))], fill=(240, 230, 200), width=2)
    d3.line([(card_w - 6, ys), (card_w - 6, min(ys + dash, card_h - 1))], fill=(240, 230, 200), width=2)
img.paste(card3, (cx_left + (card_w + gap) * 2, mid_y - card_h // 2))

src_synth_path = os.path.join(output_dir, "synthetic_sainashiguang_src.jpg")
img.save(src_synth_path, 'JPEG', quality=95, dpi=(dpi, dpi))
print(f"\n[构造完成] 合成源图保存: {src_synth_path}")

# =========== 检测边框层 ===========
border_layers = _get_border_layers_robust(img, bg_color)
print(f"\n[边框层检测] 共检测到 {len(border_layers)} 层:")
cumulative = []
total_t = 0
for i, (color, thickness) in enumerate(border_layers):
    cm_val = px_to_cm(thickness, dpi)
    total_t += thickness
    cumulative.append(total_t)
    print(f"  L{i+1}: color={color} thick={thickness:>4}px ({cm_val:.2f}cm)  cum={total_t:>5}px")
print(f"  总检测厚度 T = {total_t}px = {px_to_cm(total_t, dpi):.2f}cm")
print(f"  设计期望值: 外米色~{outer_margin_px} + 棕2~{brown2_thick} + 间隙~{gap_between} + 棕1~{brown1_thick} + 内米色(大)")
print(f"  设计总边框厚度(到内棕线内沿) ≈ {outer_margin_px + brown2_thick + gap_between + brown1_thick}px")

# =========== 应用四角 4cm 圆角 ===========
corners = {'tl': r_cm_all, 'tr': r_cm_all, 'bl': r_cm_all, 'br': r_cm_all}
print(f"\n[执行圆角] 四角 {r_cm_all}cm = {cm_to_px(r_cm_all, dpi)}px")

result_simple = apply_rounded_corners(img, corners, dpi, bg_color)
# 保存完整结果
out_simple = os.path.join(output_dir, "diagnose_sainashiguang_4corners_CURRENT.jpg")
result_simple.save(out_simple, 'JPEG', quality=92, dpi=(dpi, dpi))
print(f"整体圆角模式结果: {out_simple}")

# 保存四个角的放大图（关键！用于目视检查是否有 C 形缺口）
zoom_side = min(500, w, h)
corner_specs = [
    ('TL', (0, 0, zoom_side, zoom_side)),
    ('TR', (w - zoom_side, 0, w, zoom_side)),
    ('BL', (0, h - zoom_side, zoom_side, h)),
    ('BR', (w - zoom_side, h - zoom_side, w, h)),
]
print(f"\n[四角放大图] 请在以下位置检查棕色双线是否沿圆角平滑连续（无 C 形缺口）：")
for cname, box in corner_specs:
    zoom = result_simple.crop(box)
    zp = os.path.join(output_dir, f"diagnose_sainashiguang_{cname}_zoom_CURRENT.jpg")
    zoom.save(zp, 'JPEG', quality=95)
    print(f"  {cname}: {zp}")

# =========== 诊断分析：按层打印对角内区 vs 直边扩展带的统计 ===========
print("\n" + "=" * 70)
print("[Fix D 空间分区统计 + 升级后内容色判断] TL 角示例")
print("=" * 70)

# 复现 _sample_content_ref：15%~85% 密集格点 21x21 + RGB 中值
img_arr = np.array(img)
h_img, w_img = img_arr.shape[:2]

def _sample_content_ref(arr, ww, hh):
    x_start = int(ww * 0.15)
    x_end = int(ww * 0.85)
    y_start = int(hh * 0.15)
    y_end = int(hh * 0.85)
    STEPS = 21
    xs = np.linspace(x_start, x_end, STEPS, dtype=np.int64).clip(0, ww - 1)
    ys = np.linspace(y_start, y_end, STEPS, dtype=np.int64).clip(0, hh - 1)
    gx, gy = np.meshgrid(xs, ys)
    samples = arr[gy, gx, :].reshape(-1, 3).astype(np.float64)
    med = np.median(samples, axis=0)
    # 简单统计：多少样本与中值的色差 <30 视为命中同色
    dists = np.sqrt(np.sum((samples - med.reshape(1, 3))**2, axis=1))
    majority = int(np.sum(dists < 30))
    print(f"  密集采样 {samples.shape[0]} 像素，其中 {majority}"
          f" 与中值色差<30 (占 {majority*100/samples.shape[0]:.0f}%)")
    print(f"  (多数样本为内容奶油色 → 中值应接近 CREAM_INNER ≈ {list(CREAM_INNER)})")
    return med

content_ref = _sample_content_ref(img_arr, w_img, h_img).astype(np.float64)
print(f"\n  [密集采样 + RGB 中值] content_ref = {content_ref.astype(int).tolist()}")

from core.corner.sector_render import CORNER_ANGLES

R_total = min(cm_to_px(r_cm_all, dpi), max(1, min(w, h) // 2))
print(f"\nR_total (clamped) = {R_total}px")
# 构造 cumulative_depths 映射
cum_dep = [0]
for _, t in border_layers:
    cum_dep.append(cum_dep[-1] + t)
T_total = cum_dep[-1]

cx, cy = R_total, R_total   # TL corner
ang_min, ang_max = CORNER_ANGLES['tl']
x1, y1 = max(0, cx - R_total), max(0, cy - R_total)
x2, y2 = min(w, cx + R_total + 1), min(h, cy + R_total + 1)
roi = img_arr[y1:y2, x1:x2, :]

yy, xx = np.mgrid[y1:y2, x1:x2].astype(np.float64)
dx = xx - float(cx)
dy = yy - float(cy)
dist = np.sqrt(dx * dx + dy * dy)
angle = np.degrees(np.arctan2(dy, dx))
angle = np.mod(angle, 360.0)
depth_val = float(R_total) - dist

in_border_depth = (angle >= ang_min) & (angle < ang_max) & (dist <= R_total) & (depth_val >= 0) & (depth_val < T_total)
N = int(np.sum(in_border_depth))
print(f"\nTL 角扇形区(深度<T={T_total})内总像素: {N}")

for i, (color, thickness) in enumerate(border_layers):
    cum_before = cum_dep[i]
    cum_after = cum_dep[i + 1]
    layer_mask = in_border_depth & (depth_val >= cum_before) & (depth_val < cum_after)
    if not np.any(layer_mask):
        continue
    xs = xx[layer_mask].astype(np.int64)
    ys = yy[layer_mask].astype(np.int64)
    in_xstrip = (xs >= cum_before) & (xs < cum_after)
    in_ystrip = (ys >= cum_before) & (ys < cum_after)
    in_ext = in_xstrip | in_ystrip
    diag_interior = ~in_ext

    col_arr = np.array(color, dtype=np.float64)
    dist_vs_content = np.sqrt(np.sum((col_arr - content_ref) ** 2))

    layer_pixels = roi[layer_mask].astype(np.float64)
    d_match = np.sqrt(np.sum((layer_pixels - col_arr.reshape(1, 3)) ** 2, axis=1))
    n_match = int(np.sum(d_match <= 15))
    N_layer = int(layer_pixels.shape[0])

    force_on = (dist_vs_content > 15.0)
    print(f"\n  L{i+1} color={color} thick={thickness}px")
    print(f"    与内容参考色 content_ref 的欧氏距离 = {dist_vs_content:.1f}")
    print(f"    → Fix D 判断：{'有色边框层 → 对角内区强制绘制 ✓' if force_on else '内容填充色 → 对角内区不强制 ✓'}")
    print(f"    层内像素 {N_layer}：")
    print(f"      x 条带扩展带(左直边覆盖): {int(np.sum(in_xstrip))}  占 {int(np.sum(in_xstrip))*100/N_layer:.0f}%")
    print(f"      y 条带扩展带(上直边覆盖): {int(np.sum(in_ystrip))}  占 {int(np.sum(in_ystrip))*100/N_layer:.0f}%")
    print(f"      直边并集 (match_filter 区): {int(np.sum(in_ext))}  占 {int(np.sum(in_ext))*100/N_layer:.0f}%")
    print(f"      对角内区   (force_paint 区): {int(np.sum(diag_interior))}  占 {int(np.sum(diag_interior))*100/N_layer:.0f}%")
    print(f"      原图像素与本层色匹配数: {n_match}/{N_layer}  ({n_match*100/N_layer:.0f}%)")
    if force_on:
        will_fill = int(np.sum(diag_interior))
        print(f"      [Fix D 效果] 对角内区 {will_fill} 像素将被强制补色，消除 C 形缺口！")
        orig_hole = int(np.sum(diag_interior)) - int(
            np.sum(np.sqrt(np.sum(
                (layer_pixels[diag_interior] - col_arr.reshape(1,3))**2, axis=1
            )) <= 15)
        )
        print(f"         其中原本漏绘的缺口大小 ≈ {orig_hole} 像素（约占对角内区 "
              f"{orig_hole*100/int(np.sum(diag_interior)):.0f}%）")

print("\n诊断完成。请打开四角放大图目视棕色双线圆角处是否平滑连续（无 C 形缺口）。")
