"""
诊断脚本：花漾之约 38.5x186cm 左下角右下角 5cm 圆角遮挡问题
复现用户报告的 Bug
"""
import os
import sys
import numpy as np

sys.path.insert(0, r"D:\SmartShapeCrop")

from PIL import Image
from core.image_cropper import (
    load_source_image,
    apply_border_only_corners,
    apply_rounded_corners,
    _get_border_layers_robust,
    _build_multi_layer_corner_mask,
    _redraw_border_on_corner,
)
from core.corner.sector_render import _sample_border_color
from core.config import DEFAULT_BG_COLOR, DEFAULT_DPI, cm_to_px, px_to_cm

# 查找源图 - 先检查 psd_demo 目录和常见位置
possible_paths = [
    r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-花漾之约;38.5x186cm.jpg",
    r"D:\SmartShapeCrop\psd_demo",
]
src_path = None
for p in possible_paths:
    if os.path.isfile(p):
        src_path = p
        break
    elif os.path.isdir(p):
        for f in os.listdir(p):
            if "花漾之约" in f and f.lower().endswith(('.jpg', '.png', '.psd')):
                src_path = os.path.join(p, f)
                break
        if src_path:
            break

if not src_path:
    # Fallback: 使用已有测试图模拟
    src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
    print(f"[警告] 未找到花漾之约源图，使用替代图片: {src_path}")
else:
    print(f"[OK] 找到源图: {src_path}")

output_dir = r"D:\SmartShapeCrop\test_cropper_output"
os.makedirs(output_dir, exist_ok=True)
dpi = DEFAULT_DPI
bg_color = DEFAULT_BG_COLOR

print("\n" + "=" * 70)
print("诊断：5cm 半径圆角遮挡问题 (花漾之约 38.5x186cm, BL+BR)")
print("=" * 70)

# 1. 加载源图并缩放到目标尺寸
src = load_source_image(src_path)
target_w_cm = 38.5
target_h_cm = 186.0
target_w_px = cm_to_px(target_w_cm, dpi)
target_h_px = cm_to_px(target_h_cm, dpi)
print(f"\n[目标尺寸] {target_w_cm}x{target_h_cm}cm = {target_w_px}x{target_h_px}px @ {dpi}dpi")

cropped = src.resize((target_w_px, target_h_px), Image.LANCZOS)
w, h = cropped.size
print(f"[缩放后] {w}x{h}px")

# 2. 检测边框层
border_layers = _get_border_layers_robust(cropped, bg_color)
print(f"\n[边框层检测] 共检测到 {len(border_layers)} 层:")
total_border_thickness = 0
cumulative = []
for i, (color, thickness) in enumerate(border_layers):
    cm_val = px_to_cm(thickness, dpi)
    total_border_thickness += thickness
    cumulative.append(total_border_thickness)
    print(f"  第{i+1}层: 颜色={color}, 厚度={thickness}px ({cm_val:.2f}cm), 累计={total_border_thickness}px")

print(f"  [总边框厚度] {total_border_thickness}px ({px_to_cm(total_border_thickness, dpi):.2f}cm)")

# 3. 计算 5cm 圆角半径对应的像素
r_cm_bl_br = 5.0
r_px = cm_to_px(r_cm_bl_br, dpi)
print(f"\n[圆角参数] BL/BR 半径 = {r_cm_bl_br}cm = {r_px}px")
print(f"  阈值 4.0cm = {cm_to_px(4.0, dpi)}px")
print(f"  半径 >= 4cm? {r_cm_bl_br >= 4.0} → 所有嵌套层都要做圆角")

# 各层有效圆角半径
print(f"\n[各层有效圆角半径 (R_eff_i = max(0, R_total - cum_before_i))]:")
for i, (color, thickness) in enumerate(border_layers):
    cum_before_i = cumulative[i-1] if i > 0 else 0
    r_eff_i = max(0, r_px - cum_before_i)
    print(f"  第{i+1}层: R_eff = max(0, {r_px} - {cum_before_i}) = {r_eff_i}px "
          f"({px_to_cm(r_eff_i, dpi):.2f}cm)")

r_eff_inner = max(0, r_px - total_border_thickness)
print(f"  [内层内容] R_eff_inner = max(0, {r_px} - {total_border_thickness}) = {r_eff_inner}px "
      f"({px_to_cm(r_eff_inner, dpi):.2f}cm)")

# 4. 构建 corners 字典并运行完整流程
corners = {'tl': 0.0, 'tr': 0.0, 'bl': r_cm_bl_br, 'br': r_cm_bl_br}
corners_px = {k: cm_to_px(v, dpi) for k, v in corners.items() if v > 0}

# ======== 诊断关键：检查 _redraw_border_on_corner 覆盖范围 ========
print("\n" + "=" * 70)
print("[诊断分析] _redraw_border_on_corner 对角落区域的覆盖情况")
print("=" * 70)

# 以 BL 角为例分析
corner_key = 'bl'
cx = r_px
cy = h - r_px
print(f"\nBL 角圆心: ({cx}, {cy})")
print(f"BL 角角度范围: 90° ~ 180°")
print(f"total_border_depth = {total_border_thickness}px")
print(f"R_total = {r_px}px")
print(f"\n将被 _redraw_border_on_corner 强制绘制纯色覆盖的区域:")
print(f"  → 角度 90°~180° 范围内，所有 depth < {total_border_thickness}px 的像素")
print(f"  → 即 dist <= {r_px}px 且 depth in [0, {total_border_thickness})")
print(f"  → 环形扇区的面积 ≈ 1/4 × π × ({r_px}² - {r_px - total_border_thickness}²)")
area = 0.25 * 3.14159 * (r_px**2 - max(0, r_px - total_border_thickness)**2)
print(f"  → 约 {int(area)} 像素将被纯色覆盖！")
print(f"\n⚠  如果该区域内有花朵、点状线等装饰图案 → 它们会被纯色边框色覆盖 → 造成【遮挡】！")

# ======== 对比：原始直边区域的采样颜色 vs 角落扇区的实际内容 ========
print("\n" + "=" * 70)
print("[诊断分析] 直线边框采样颜色 vs 角落区域实际内容")
print("=" * 70)
cropped_arr = np.array(cropped)

# 检查 BL 角 R×R 区域内的实际像素颜色多样性
x1, y1 = max(0, cx - r_px), max(0, cy - r_px)
x2, y2 = min(w, cx + r_px + 1), min(h, cy + r_px + 1)
roi = cropped_arr[y1:y2, x1:x2, :]
print(f"\nBL 角 R×R 区域内像素统计:")
print(f"  ROI 尺寸: {roi.shape[1]}x{roi.shape[0]}")

# 计算角度和距离，找到 valid_region 内的像素
yy, xx = np.mgrid[y1:y2, x1:x2].astype(np.float64)
dx = xx - float(cx)
dy = yy - float(cy)
dist = np.sqrt(dx * dx + dy * dy)
angle = np.degrees(np.arctan2(dy, dx))
angle = np.mod(angle, 360.0)
depth_val = float(r_px) - dist

ang_min, ang_max = 90.0, 180.0  # BL
# 在边框深度范围内，角度范围内，且 dist <= R_total 的像素 = 将被覆盖区域
cover_region = (angle >= ang_min) & (angle < ang_max) & (dist <= r_px) & (depth_val >= 0) & (depth_val < total_border_thickness)
covered_pixels = roi[cover_region]
print(f"  将被纯色覆盖的像素数: {covered_pixels.shape[0]}")

if covered_pixels.shape[0] > 0:
    # 分析这些像素的颜色分布
    unique_colors = np.unique(covered_pixels.reshape(-1, 3), axis=0)
    print(f"  这些像素中独特颜色数: {unique_colors.shape[0]}")
    
    # 计算与各层边框色接近的像素比例
    for i, (bcolor, thickness) in enumerate(border_layers):
        bcol_arr = np.array(bcolor, dtype=np.float64)
        diffs = np.sqrt(np.sum((covered_pixels.astype(np.float64) - bcol_arr)**2, axis=1))
        close_count = np.sum(diffs <= 15)
        pct = close_count / covered_pixels.shape[0] * 100
        print(f"  与第{i+1}层边框色 {bcolor} 相近的像素: {close_count} ({pct:.1f}%)")
    
    # 白色像素比例
    white = np.array([255, 255, 255], dtype=np.float64)
    diffs_white = np.sqrt(np.sum((covered_pixels.astype(np.float64) - white)**2, axis=1))
    white_count = np.sum(diffs_white <= 30)
    pct_white = white_count / covered_pixels.shape[0] * 100
    print(f"  白色/近白色像素: {white_count} ({pct_white:.1f}%)")
    
    # 其他颜色像素（=装饰图案像素 = 会被错误覆盖的）
    other_count = covered_pixels.shape[0] - white_count
    for _, (bcolor, _) in enumerate(border_layers):
        bcol_arr = np.array(bcolor, dtype=np.float64)
        diffs = np.sqrt(np.sum((covered_pixels.astype(np.float64) - bcol_arr)**2, axis=1))
        other_count -= np.sum(diffs <= 15)
    other_count = max(0, other_count)
    pct_other = other_count / covered_pixels.shape[0] * 100
    print(f"\n  ⚠  既不是白色也不是边框色的装饰像素: {other_count} ({pct_other:.1f}%)")
    if pct_other > 5:
        print(f"  → 这些像素（花朵/点状线等）会被 _redraw_border_on_corner 的纯色填充覆盖 → 造成遮挡！")

# 5. 现在运行完整的 apply_border_only_corners 并保存结果用于目视检查
print("\n" + "=" * 70)
print("执行 apply_border_only_corners 并保存结果")
print("=" * 70)

result_v1 = apply_border_only_corners(cropped, corners, dpi, bg_color)
out1 = os.path.join(output_dir, "diagnose_huayang_5cm_CURRENT.jpg")
result_v1.save(out1, 'JPEG', quality=95, dpi=(dpi, dpi))
print(f"当前版本结果已保存: {out1}")

# 保存 BL/BR 角的局部放大
bl_zoom = result_v1.crop((0, h - min(400, h), min(400, w), h))
br_zoom = result_v1.crop((w - min(400, w), h - min(400, h), w, h))
bl_zoom_path = os.path.join(output_dir, "diagnose_huayang_5cm_BL_zoom_CURRENT.jpg")
br_zoom_path = os.path.join(output_dir, "diagnose_huayang_5cm_BR_zoom_CURRENT.jpg")
bl_zoom.save(bl_zoom_path, 'JPEG', quality=95)
br_zoom.save(br_zoom_path, 'JPEG', quality=95)
print(f"BL 角放大: {bl_zoom_path}")
print(f"BR 角放大: {br_zoom_path}")

print("\n诊断完成。接下来将根据分析结果修复遮挡问题。")
