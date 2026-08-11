"""
诊断脚本：多层边框修复验证 —— 外实线+内虚线的双层边框场景
验证：所有边框层在圆角处都被正确重绘，无C形缺口
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

# =========== 场景参数（模拟用户：葵花生 48x136cm 左下角5cm圆角）===========
target_w_cm = 48.0
target_h_cm = 136.0
r_cm_all = 5.0
target_w_px = cm_to_px(target_w_cm, dpi)
target_h_px = cm_to_px(target_h_cm, dpi)

print("=" * 70)
print(f"多层边框诊断：外实线+内虚线 双层边框")
print(f"目标: {target_w_cm}x{target_h_cm}cm, 左下角 {r_cm_all}cm圆角")
print("=" * 70)

# =========== 构造合成图像：米白底 + 外实线 + 内虚线（点状）===========
CREAM_BG = (252, 248, 240)    # 米白底色
DARK_BROWN = (30, 25, 20)     # 深棕色（边框色）
# 外层：实线边框（约8px）
OUTER_SOLID_THICK = 8
# 中间间隙
GAP_THICK = 6
# 内层：点状/虚线边框（约6px）
INNER_DASHED_THICK = 6

w, h = target_w_px, target_h_px
img = Image.new('RGB', (w, h), CREAM_BG)
d = ImageDraw.Draw(img)

# 1. 外层实线边框
d.rectangle([0, 0, w-1, h-1], outline=DARK_BROWN, width=OUTER_SOLID_THICK)

# 2. 内层虚线边框（用点组成的虚线）
inner_x1 = OUTER_SOLID_THICK + GAP_THICK
inner_y1 = OUTER_SOLID_THICK + GAP_THICK
inner_x2 = w - 1 - OUTER_SOLID_THICK - GAP_THICK
inner_y2 = h - 1 - OUTER_SOLID_THICK - GAP_THICK

# 画虚线效果（用短线段+间隙模拟）
dash_len = 20
gap_len = 10
step = dash_len + gap_len

# 顶边虚线
x = inner_x1
while x <= inner_x2:
    x_end = min(x + dash_len, inner_x2)
    d.rectangle([x, inner_y1, x_end, inner_y1 + INNER_DASHED_THICK - 1], fill=DARK_BROWN)
    x += step

# 底边虚线
x = inner_x1
while x <= inner_x2:
    x_end = min(x + dash_len, inner_x2)
    d.rectangle([x, inner_y2 - INNER_DASHED_THICK + 1, x_end, inner_y2], fill=DARK_BROWN)
    x += step

# 左边虚线（在转角处加小圆点连接）
y = inner_y1
while y <= inner_y2:
    y_end = min(y + dash_len, inner_y2)
    d.rectangle([inner_x1, y, inner_x1 + INNER_DASHED_THICK - 1, y_end], fill=DARK_BROWN)
    y += step

# 右边虚线
y = inner_y1
while y <= inner_y2:
    y_end = min(y + dash_len, inner_y2)
    d.rectangle([inner_x2 - INNER_DASHED_THICK + 1, y, inner_x2, y_end], fill=DARK_BROWN)
    y += step

# 角落连接点（用小圆）
for corner_x, corner_y in [(inner_x1, inner_y1), (inner_x2, inner_y1), 
                             (inner_x1, inner_y2), (inner_x2, inner_y2)]:
    r = INNER_DASHED_THICK // 2 + 1
    d.ellipse([corner_x - r, corner_y - r, corner_x + r, corner_y + r], fill=DARK_BROWN)

# 添加一些装饰内容
np.random.seed(42)
content_x1 = inner_x1 + INNER_DASHED_THICK + 20
content_y1 = inner_y1 + INNER_DASHED_THICK + 20
content_x2 = inner_x2 - INNER_DASHED_THICK - 20
content_y2 = inner_y2 - INNER_DASHED_THICK - 20

# 画一些花朵
for _ in range(10):
    cx = np.random.randint(content_x1 + 50, content_x2 - 50)
    cy = np.random.randint(content_y1 + 50, content_y2 - 50)
    size = np.random.randint(25, 50)
    for petal_i in range(5):
        ang = petal_i * 72
        rad = np.radians(ang)
        px = cx + int(np.cos(rad) * size * 0.4)
        py = cy + int(np.sin(rad) * size * 0.4)
        d.ellipse([px - size//4, py - size//4, px + size//4, py + size//4],
                  outline=(60, 50, 40), width=1)
    d.ellipse([cx - size//6, cy - size//6, cx + size//6, cy + size//6],
              fill=(70, 60, 50), outline=DARK_BROWN, width=1)

src_path = os.path.join(output_dir, "multi_layer_border_src.jpg")
img.save(src_path, 'JPEG', quality=95, dpi=(dpi, dpi))
print(f"[源图] 保存: {src_path}")

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

# =========== 应用左下角圆角 ===========
corners = {'tl': 0, 'tr': 0, 'bl': r_cm_all, 'br': 0}
r_px = cm_to_px(r_cm_all, dpi)
print(f"\n[执行圆角] 左下角 {r_cm_all}cm = {r_px}px")

# 使用整体圆角模式
result = apply_rounded_corners(img, corners, dpi, bg_color)

out_path = os.path.join(output_dir, "diagnose_multi_layer_border_CURRENT.jpg")
result.save(out_path, 'JPEG', quality=92, dpi=(dpi, dpi))
print(f"[结果] 保存: {out_path}")

# =========== 放大左下角检查 ===========
zoom_size = min(600, h // 4)
bl_box = (0, h - zoom_size, zoom_size, h)
bl_zoom = result.crop(bl_box)
bl_zoom_path = os.path.join(output_dir, "diagnose_multi_layer_BL_zoom_CURRENT.jpg")
bl_zoom.save(bl_zoom_path, 'JPEG', quality=95)
print(f"[放大] 左下角: {bl_zoom_path}")

# =========== 测量验证：内层边框在圆角处的覆盖情况 ===========
print("\n" + "=" * 70)
print("[验证] 内层边框在圆角处是否完整")
print("=" * 70)

result_arr = np.array(result)
bl_cx = r_px  # 左下角圆心 x
bl_cy = h - r_px  # 左下角圆心 y

# 沿45度方向检查各层边框
print(f"\n左下角圆心: ({bl_cx}, {bl_cy})")
print(f"圆角半径: {r_px}px")

# 从外缘向内扫描各层
inner_layer_found = False
for step in range(1, r_px + 10):
    dist = float(r_px) - step + 0.5
    if dist < 0:
        break
    ang = np.radians(225)  # 45度方向（从左下角向外）
    px = int(round(bl_cx + dist * np.cos(ang)))
    py = int(round(bl_cy + dist * np.sin(ang)))
    if px < 0 or px >= w or py < 0 or py >= h:
        break
    pc = tuple(int(c) for c in result_arr[py, px, :])
    pdist = np.sqrt(sum((a - b) ** 2 for a, b in zip(pc, DARK_BROWN)))
    
    # 检查位置
    layer_pos = "外边框区" if dist >= r_px - OUTER_SOLID_THICK else (
        "间隙区" if dist >= r_px - OUTER_SOLID_THICK - GAP_THICK else "内边框区"
    )
    
    if pdist < 50:
        if dist < r_px - OUTER_SOLID_THICK - GAP_THICK:
            inner_layer_found = True
        print(f"  dist={dist:.1f}px 位置={layer_pos} 颜色=({pc[0]},{pc[1]},{pc[2]}) ✅ 有边框")
    elif step <= 30:
        print(f"  dist={dist:.1f}px 位置={layer_pos} 颜色=({pc[0]},{pc[1]},{pc[2]}) ❌ 无边框")

print(f"\n内层边框在圆角45°方向: {'✅ 存在' if inner_layer_found else '❌ 缺失!'}")

# 如果内层边框缺失，统计缺失的范围
if not inner_layer_found:
    print("\n⚠️  内层边框在圆角处缺失！")
    missing_start = None
    missing_end = None
    for step in range(1, r_px + 10):
        dist = float(r_px) - step + 0.5
        if dist < 0:
            break
        ang = np.radians(225)
        px = int(round(bl_cx + dist * np.cos(ang)))
        py = int(round(bl_cy + dist * np.sin(ang)))
        if px < 0 or px >= w or py < 0 or py >= h:
            break
        pc = tuple(int(c) for c in result_arr[py, px, :])
        pdist = np.sqrt(sum((a - b) ** 2 for a, b in zip(pc, DARK_BROWN)))
        
        # 内边框应该在这个距离范围内
        inner_min = r_px - OUTER_SOLID_THICK - GAP_THICK - INNER_DASHED_THICK
        inner_max = r_px - OUTER_SOLID_THICK - GAP_THICK
        
        if inner_min <= dist <= inner_max:
            if pdist >= 50:
                if missing_start is None:
                    missing_start = dist
                missing_end = dist
    
    if missing_start is not None:
        print(f"  缺失范围: dist={missing_start:.1f}px ~ {missing_end:.1f}px")
        print(f"  内边框应在: dist={inner_min:.1f}px ~ {inner_max:.1f}px")
else:
    print("\n✅ 内层边框在圆角处完整！")

print("\n诊断完成。请打开放大图目视检查内层虚线边框是否在圆角处连续。")
