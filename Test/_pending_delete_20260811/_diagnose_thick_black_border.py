"""
诊断脚本：模拟用户场景 —— 80x160cm 产品，米白底 + 粗黑边框，四角4.5cm圆角
验证：自动检测原边框的颜色和粗细，绘制时保持一致
"""
import os
import sys
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, r"D:\SmartShapeCrop")

from core.image_cropper import (
    apply_border_only_corners,
    apply_rounded_corners,
    _get_border_layers_robust,
)
from core.config import DEFAULT_BG_COLOR, DEFAULT_DPI, cm_to_px, px_to_cm

output_dir = r"D:\SmartShapeCrop\test_cropper_output"
os.makedirs(output_dir, exist_ok=True)
dpi = DEFAULT_DPI
bg_color = DEFAULT_BG_COLOR  # (255, 255, 255)

# =========== 目标尺寸与参数（花僈 80x160cm 场景） ===========
target_w_cm = 80.0
target_h_cm = 160.0
r_cm_all = 4.5
target_w_px = cm_to_px(target_w_cm, dpi)
target_h_px = cm_to_px(target_h_cm, dpi)

print("=" * 70)
print(f"合成场景：粗黑边框圆角诊断（{target_w_cm}x{target_h_cm}cm 四角{r_cm_all}cm）")
print("=" * 70)
print(f"目标尺寸: {target_w_cm}x{target_h_cm}cm = {target_w_px}x{target_h_px}px @ {dpi}dpi")
print(f"四角圆角半径: {r_cm_all}cm = {cm_to_px(r_cm_all, dpi)}px")

# =========== 构造合成图像：米白底 + 粗黑色边框（模拟用户截图）===========
CREAM_BG = (252, 248, 240)    # 米白底色
BLACK_BORDER = (0, 0, 0)       # 纯黑色边框
# 设计边框厚度：与用户截图一致，约6-8px（粗边框）
BORDER_THICKNESS_DESIGN = 8

w, h = target_w_px, target_h_px
img = Image.new('RGB', (w, h), CREAM_BG)
d = ImageDraw.Draw(img)

# 画粗黑色矩形边框（画多层模拟粗线）
x1_o, y1_o = 0, 0
x2_o, y2_o = w - 1, h - 1
for off in range(BORDER_THICKNESS_DESIGN):
    d.rectangle([x1_o + off, y1_o + off, x2_o - off, y2_o - off],
                outline=BLACK_BORDER, width=1)

# 在内部画一些装饰内容（模拟花/蝴蝶）
inner_margin = 40
card_area_x1 = inner_margin
card_area_y1 = inner_margin
card_area_x2 = w - inner_margin - 1
card_area_y2 = h - inner_margin - 1

# 画一个标题区域（模拟WONDERFUL LIFE）
title_h = 180
title_y1 = h // 2 - title_h // 2
title_y2 = h // 2 + title_h // 2
d.rectangle([card_area_x1, title_y1, card_area_x2, title_y2],
            outline=BLACK_BORDER, width=2)

# 画一些花朵示意（简单圆形+椭圆）
np.random.seed(42)
for _ in range(15):
    cx = np.random.randint(card_area_x1 + 100, card_area_x2 - 100)
    cy = np.random.randint(card_area_y1 + 80, title_y1 - 80)
    if cy > title_y2 + 80:
        cy = np.random.randint(title_y2 + 80, card_area_y2 - 80)
    size = np.random.randint(40, 90)
    # 花瓣
    for petal_i in range(5):
        ang = petal_i * 72
        rad = np.radians(ang)
        px = cx + int(np.cos(rad) * size * 0.5)
        py = cy + int(np.sin(rad) * size * 0.5)
        d.ellipse([px - size//3, py - size//3, px + size//3, py + size//3],
                  outline=(30, 30, 30), width=2)
    # 花蕊
    d.ellipse([cx - size//5, cy - size//5, cx + size//5, cy + size//5],
              fill=(40, 40, 40), outline=(0, 0, 0), width=1)

src_synth_path = os.path.join(output_dir, "synthetic_thick_black_border_src.jpg")
img.save(src_synth_path, 'JPEG', quality=95, dpi=(dpi, dpi))
print(f"\n[构造完成] 合成源图保存: {src_synth_path}")
print(f"设计边框厚度: {BORDER_THICKNESS_DESIGN}px")

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
print(f"  设计期望值: 黑边框厚度 ≈ {BORDER_THICKNESS_DESIGN}px")

# =========== 应用四角圆角 ===========
corners = {'tl': r_cm_all, 'tr': r_cm_all, 'bl': r_cm_all, 'br': r_cm_all}
print(f"\n[执行圆角] 四角 {r_cm_all}cm = {cm_to_px(r_cm_all, dpi)}px")

result_simple = apply_rounded_corners(img, corners, dpi, bg_color)
# 保存完整结果
out_simple = os.path.join(output_dir, "diagnose_thick_black_border_4corners_CURRENT.jpg")
result_simple.save(out_simple, 'JPEG', quality=92, dpi=(dpi, dpi))
print(f"整体圆角模式结果: {out_simple}")

# 保存四个角的放大图（检查边框线厚度是否一致）
zoom_side = min(500, w, h)
corner_specs = [
    ('TL', (0, 0, zoom_side, zoom_side)),
    ('TR', (w - zoom_side, 0, w, zoom_side)),
    ('BL', (0, h - zoom_side, zoom_side, h)),
    ('BR', (w - zoom_side, h - zoom_side, w, h)),
]
print(f"\n[四角放大图] 请检查黑色边框厚度是否与直边一致：")
for cname, box in corner_specs:
    zoom = result_simple.crop(box)
    zp = os.path.join(output_dir, f"diagnose_thick_black_border_{cname}_zoom_CURRENT.jpg")
    zoom.save(zp, 'JPEG', quality=95)
    print(f"  {cname}: {zp}")

# =========== 实际测量：圆角边框 vs 直边边框厚度 ===========
print("\n" + "=" * 70)
print("[实际测量] 直边边框厚度 vs 圆角边框厚度")
print("=" * 70)

# 直边厚度测量（顶边）
top_arr = np.array(img)
mid_x = w // 2
straight_count = 0
for y in range(min(h - 1, 50)):
    pc = tuple(int(c) for c in top_arr[y, mid_x, :])
    pdist = np.sqrt(sum((a - b) ** 2 for a, b in zip(pc, BLACK_BORDER)))
    if pdist < 50:
        straight_count += 1
    else:
        break
print(f"直边测量（顶边中部）: 黑色边框厚度 = {straight_count}px (设计值={BORDER_THICKNESS_DESIGN}px)")

# 圆角厚度测量（TL角，沿45度方向）
r_px = cm_to_px(r_cm_all, dpi)
cx, cy = r_px, r_px
result_arr = np.array(result_simple)
corner_count = 0
# 沿45度方向从外向内采样
for step in range(1, 50):
    # dist = r_px - step（从外缘向内）
    dist = r_px - step + 0.5
    if dist < 0:
        break
    ang = np.radians(225)  # TL角45°方向
    px = int(round(cx + dist * np.cos(ang)))
    py = int(round(cy + dist * np.sin(ang)))
    if px < 0 or px >= w or py < 0 or py >= h:
        break
    pc = tuple(int(c) for c in result_arr[py, px, :])
    pdist = np.sqrt(sum((a - b) ** 2 for a, b in zip(pc, BLACK_BORDER)))
    if pdist < 80:  # 稍微放宽阈值（因为抗锯齿）
        corner_count += 1
    else:
        break
print(f"圆角测量（TL角45°方向）: 黑色边框厚度 = {corner_count}px")
print(f"厚度差异: 直边{straight_count}px vs 圆角{corner_count}px "
      f"(差异{abs(straight_count - corner_count)}px)")

if abs(straight_count - corner_count) <= 1:
    print("✅ 边框厚度匹配良好！")
elif corner_count < straight_count:
    print(f"⚠️  圆角边框过细，差了 {straight_count - corner_count}px")
else:
    print(f"⚠️  圆角边框过粗，差了 {corner_count - straight_count}px")

print("\n诊断完成。请打开四角放大图目视检查边框线粗细是否一致。")
