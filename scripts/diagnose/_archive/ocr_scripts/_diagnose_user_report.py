"""
诊断脚本：用户反馈的两个新问题
1. 中古雨林 71x130cm 4圆角2cm裁剪不正确
2. 塞纳时光 70x120cm 4圆角8cm裁剪时内外边框合并
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
    _build_multi_layer_corner_mask,
)
from core.config import DEFAULT_BG_COLOR, DEFAULT_DPI, cm_to_px, px_to_cm

output_dir = _os.path.join(_D, 'logs', 'output')
os.makedirs(output_dir, exist_ok=True)
dpi = DEFAULT_DPI
bg_color = DEFAULT_BG_COLOR

# ============================================================
# 场景1：中古雨林（模拟）
# 尺寸 71x130cm, 4个圆角 2cm
# 特征：外黑色边框 + 内部装饰，边框上有文字
# ============================================================
print("=" * 70)
print("场景1：中古雨林 71x130cm 4圆角2cm")
print("=" * 70)

# 构造合成图像：米色底 + 黑色边框 + 装饰图案
CREAM_BG = (252, 248, 240)
DARK = (25, 20, 18)  # 黑色边框

w1 = cm_to_px(71.0, dpi)
h1 = cm_to_px(130.0, dpi)
img1 = Image.new('RGB', (w1, h1), CREAM_BG)
d = ImageDraw.Draw(img1)

# 外黑色边框（2cm厚度 ≈ 118px）
border_t = cm_to_px(2.0, dpi)
d.rectangle([0, 0, w1-1, h1-1], outline=DARK, width=border_t)

# 在边框上画文字装饰（模拟原图的文字环绕边框）
# 用小线条模拟文字
import random
random.seed(42)
for _ in range(50):
    side = random.randint(0, 3)
    if side == 0:  # top
        x = random.randint(border_t + 10, w1 - border_t - 10)
        y = random.randint(5, border_t - 5)
    elif side == 1:  # bottom
        x = random.randint(border_t + 10, w1 - border_t - 10)
        y = random.randint(h1 - border_t + 5, h1 - 5)
    elif side == 2:  # left
        x = random.randint(5, border_t - 5)
        y = random.randint(border_t + 10, h1 - border_t - 10)
    else:  # right
        x = random.randint(w1 - border_t + 5, w1 - 5)
        y = random.randint(border_t + 10, h1 - border_t - 10)
    # 画小装饰线条（模拟文字）
    for k in range(3):
        if random.random() > 0.5:
            d.point([x + k, y], fill=(180, 170, 160))
        else:
            d.point([x, y + k], fill=(180, 170, 160))

# 内部装饰 - 叶子/植物线条
inner_x1 = border_t + 30
inner_y1 = border_t + 30
inner_x2 = w1 - border_t - 30
inner_y2 = h1 - border_t - 30
for _ in range(20):
    cx = random.randint(inner_x1 + 50, inner_x2 - 50)
    cy = random.randint(inner_y1 + 50, inner_y2 - 50)
    for r in range(5, 15):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(60, 55, 50), width=1)

src1_path = os.path.join(output_dir, "zhonggu_yulin_src.jpg")
img1.save(src1_path, 'JPEG', quality=95, dpi=(dpi, dpi))
print(f"[源图] {src1_path} 尺寸: {w1}x{h1}px")

# 检测边框层
layers1 = _get_border_layers_robust(img1, bg_color)
print(f"\n[边框层检测] 共 {len(layers1)} 层:")
total_t = 0
for i, (color, t) in enumerate(layers1):
    cm = px_to_cm(t, dpi)
    total_t += t
    print(f"  L{i+1}: color={color} thick={t}px ({cm:.2f}cm) cum={total_t}px")

# 应用 2cm 圆角（四角相同）
corners1 = {'tl': 2.0, 'tr': 2.0, 'bl': 2.0, 'br': 2.0}
r_2cm = cm_to_px(2.0, dpi)
print(f"\n[执行圆角] 4角 2cm = {r_2cm}px")

result1 = apply_border_only_corners(img1, corners1, dpi, bg_color)

out1_path = os.path.join(output_dir, "zhonggu_yulin_2cm_CURRENT.jpg")
result1.save(out1_path, 'JPEG', quality=92, dpi=(dpi, dpi))
print(f"[结果] {out1_path}")

# 放大角落检查
for corner, name in [('tl', 'TL'), ('tr', 'TR'), ('bl', 'BL'), ('br', 'BR')]:
    zoom = min(400, r_2cm * 2)
    if corner == 'tl':
        box = (0, 0, zoom, zoom)
    elif corner == 'tr':
        box = (w1 - zoom, 0, w1, zoom)
    elif corner == 'bl':
        box = (0, h1 - zoom, zoom, h1)
    else:
        box = (w1 - zoom, h1 - zoom, w1, h1)
    zoom_img = result1.crop(box)
    zoom_path = os.path.join(output_dir, f"zhonggu_yulin_{name}_zoom_CURRENT.jpg")
    zoom_img.save(zoom_path, 'JPEG', quality=95)
    print(f"[放大] {name} -> {zoom_path}")

# ============================================================
# 场景2：塞纳时光（模拟）
# 尺寸 70x120cm, 4个圆角 8cm
# 特征：外黑色边框 + 内棕色边框（双层）
# ============================================================
print("\n" + "=" * 70)
print("场景2：塞纳时光 70x120cm 4圆角8cm")
print("=" * 70)

w2 = cm_to_px(70.0, dpi)
h2 = cm_to_px(120.0, dpi)
img2 = Image.new('RGB', (w2, h2), CREAM_BG)
d2 = ImageDraw.Draw(img2)

# 外黑色边框（细，约3mm ≈ 18px）
OUTER_BLACK_T = cm_to_px(0.3, dpi)
BLACK = (25, 22, 20)
d2.rectangle([0, 0, w2-1, h2-1], outline=BLACK, width=OUTER_BLACK_T)

# 中间间隙
GAP = cm_to_px(0.5, dpi)

# 内棕色边框（中等，约8mm ≈ 47px）
INNER_BROWN_T = cm_to_px(0.8, dpi)
BROWN = (150, 95, 65)

inner_x1 = OUTER_BLACK_T + GAP
inner_y1 = OUTER_BLACK_T + GAP
inner_x2 = w2 - OUTER_BLACK_T - GAP
inner_y2 = h2 - OUTER_BLACK_T - GAP
d2.rectangle([inner_x1, inner_y1, inner_x2, inner_y2], outline=BROWN, width=INNER_BROWN_T)

# 内部装饰内容 - 一些卡通图案块
# 模拟原图的装饰卡片
card_w = w2 // 4
card_h = h2 // 4
cards = [
    (inner_x1 + INNER_BROWN_T + 40, inner_y1 + INNER_BROWN_T + 40,
     inner_x1 + INNER_BROWN_T + 40 + card_w, inner_y1 + INNER_BROWN_T + 40 + card_h,
     (100, 130, 100)),  # 绿色卡片
    (w2 // 2 - card_w // 2, h2 // 2 - card_h // 2,
     w2 // 2 + card_w // 2, h2 // 2 + card_h // 2,
     (180, 140, 90)),  # 棕色卡片
    (inner_x2 - INNER_BROWN_T - 40 - card_w, inner_y1 + INNER_BROWN_T + 40,
     inner_x2 - INNER_BROWN_T - 40, inner_y1 + INNER_BROWN_T + 40 + card_h,
     (40, 40, 40)),  # 黑色卡片
]
for x1, y1, x2, y2, c in cards:
    d2.rectangle([x1, y1, x2, y2], fill=c)

src2_path = os.path.join(output_dir, "sainashiguang_src.jpg")
img2.save(src2_path, 'JPEG', quality=95, dpi=(dpi, dpi))
print(f"[源图] {src2_path} 尺寸: {w2}x{h2}px")

# 检测边框层
layers2 = _get_border_layers_robust(img2, bg_color)
print(f"\n[边框层检测] 共 {len(layers2)} 层:")
total_t = 0
for i, (color, t) in enumerate(layers2):
    cm = px_to_cm(t, dpi)
    total_t += t
    print(f"  L{i+1}: color={color} thick={t}px ({cm:.2f}cm) cum={total_t}px")

# 应用 8cm 圆角（四角相同）
corners2 = {'tl': 8.0, 'tr': 8.0, 'bl': 8.0, 'br': 8.0}
r_8cm = cm_to_px(8.0, dpi)
print(f"\n[执行圆角] 4角 8cm = {r_8cm}px")

result2 = apply_border_only_corners(img2, corners2, dpi, bg_color)

out2_path = os.path.join(output_dir, "sainashiguang_8cm_CURRENT.jpg")
result2.save(out2_path, 'JPEG', quality=92, dpi=(dpi, dpi))
print(f"[结果] {out2_path}")

# 放大角落检查
for corner, name in [('tl', 'TL'), ('tr', 'TR'), ('bl', 'BL'), ('br', 'BR')]:
    zoom = min(500, r_8cm)
    if corner == 'tl':
        box = (0, 0, zoom, zoom)
    elif corner == 'tr':
        box = (w2 - zoom, 0, w2, zoom)
    elif corner == 'bl':
        box = (0, h2 - zoom, zoom, h2)
    else:
        box = (w2 - zoom, h2 - zoom, w2, h2)
    zoom_img = result2.crop(box)
    zoom_path = os.path.join(output_dir, f"sainashiguang_{name}_zoom_CURRENT.jpg")
    zoom_img.save(zoom_path, 'JPEG', quality=95)
    print(f"[放大] {name} -> {zoom_path}")

# ============================================================
# 分析：对场景2的结果进行颜色分析
# ============================================================
print("\n" + "=" * 70)
print("[详细分析] 场景2 - 检查每个角落的边框层保留情况")
print("=" * 70)

arr2 = np.array(result2)
for corner, name in [('tl', 'TL'), ('tr', 'TR'), ('bl', 'BL'), ('br', 'BR')]:
    if corner == 'tl':
        cx, cy = r_8cm, r_8cm
    elif corner == 'tr':
        cx, cy = w2 - r_8cm, r_8cm
    elif corner == 'bl':
        cx, cy = r_8cm, h2 - r_8cm
    else:
        cx, cy = w2 - r_8cm, h2 - r_8cm
    
    print(f"\n--- {name} 角 (圆心={cx},{cy}, R={r_8cm}) ---")
    
    # 沿 45° 方向（从圆心向外）扫描
    # TL: 225°方向, TR: 315°, BL: 135°, BR: 45°
    angle_map = {'tl': 225, 'tr': 315, 'bl': 135, 'br': 45}
    scan_angle = np.radians(angle_map[corner])
    
    # 统计黑色和棕色的像素数量（在环形区域内）
    black_count = 0
    brown_count = 0
    other_count = 0
    
    for d in range(1, r_8cm + 1):
        px = int(round(cx + d * np.cos(scan_angle)))
        py = int(round(cy + d * np.sin(scan_angle)))
        if 0 <= px < w2 and 0 <= py < h2:
            pc = arr2[py, px, :]
            d_black = np.sqrt(sum((a - b) ** 2 for a, b in zip(pc, BLACK)))
            d_brown = np.sqrt(sum((a - b) ** 2 for a, b in zip(pc, BROWN)))
            if d_black < 30:
                black_count += 1
            elif d_brown < 30:
                brown_count += 1
            else:
                other_count += 1
    
    print(f"  黑色像素: {black_count}  棕色像素: {brown_count}  其他: {other_count}")
    
    # 检查：黑色边框应该在外层，棕色在中层
    # 问题现象：可能所有层都被画成同一颜色

print("\n诊断完成。请目视检查放大图，确认：")
print("1. 场景1：圆角裁剪是否正确（不应出现异常白色扇形区域）")
print("2. 场景2：黑色边框和棕色边框是否在圆角处都清晰保留")
