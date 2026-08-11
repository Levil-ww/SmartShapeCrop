"""
调试边框检测和圆角重绘的完整流程
"""
import sys
import numpy as np

sys.path.insert(0, r"D:\SmartShapeCrop")

from PIL import Image
from core.image_cropper import (
    _get_border_layers_robust,
    _build_multi_layer_corner_mask,
    _redraw_border_on_corner,
)
from core.config import DEFAULT_BG_COLOR

src_path = r"D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
dpi = 150
bg_color = DEFAULT_BG_COLOR

print("=" * 60)
print("调试边框检测和圆角重绘")
print("=" * 60)

# 加载源图
src = Image.open(src_path)
w, h = src.size
src_arr = np.array(src)

# 模拟裁剪后的尺寸 (35.5cm x 256cm)
target_w_px = int(round(35.5 * dpi / 2.54))
target_h_px = int(round(256 * dpi / 2.54))
print(f"目标尺寸: {target_w_px} x {target_h_px} px")

# 简单 resize 模拟裁剪
from PIL import Image
cropped = src.resize((target_w_px, target_h_px), Image.LANCZOS)
cw, ch = cropped.size
print(f"裁剪后尺寸: {cw} x {ch}")

# 检测边框层
border_layers = _get_border_layers_robust(cropped, bg_color)
print(f"\n[边框检测] 检测到 {len(border_layers)} 层边框:")
cumulative = 0
for i, (color, thickness) in enumerate(border_layers):
    cm = thickness * 2.54 / dpi
    print(f"  第{i+1}层: 深度[{cumulative}, {cumulative+thickness})px, 厚度={thickness}px ({cm:.2f}cm), 颜色={color}")
    cumulative += thickness

# 设置圆角
R_px = int(round(3.5 * dpi / 2.54))  # 3.5cm
print(f"\n[圆角设置] 半径={R_px}px")

# 构建圆角遮罩
corners_px = {'bl': R_px, 'br': R_px}
final_mask = _build_multi_layer_corner_mask(cw, ch, corners_px, border_layers)

# 创建结果图
result = Image.new('RGB', (cw, ch), bg_color)
result.paste(cropped, mask=final_mask)

# 检查遮罩效果
mask_arr = np.array(final_mask)
print(f"\n[遮罩检查]")
print(f"  遮罩尺寸: {mask_arr.shape}")
print(f"  有效像素数量: {np.sum(mask_arr > 0)}")

# 在结果图上检查右下角区域
result_arr = np.array(result)
print(f"\n[结果图检查 - 调用 _redraw_border_on_corner 之前]")
for depth in [0, 50, 100, 150, 200]:
    x = cw - 1 - depth
    y = ch - 1 - depth
    if 0 <= x < cw and 0 <= y < ch:
        pixel = tuple(result_arr[y, x].tolist())
        mask_val = mask_arr[y, x]
        print(f"  深度 {depth}px: 像素 {pixel}, 遮罩值 {mask_val}")

# 调用 _redraw_border_on_corner
print(f"\n[调用 _redraw_border_on_corner]")
print(f"  border_layers: {border_layers}")

# 处理右下角
_redraw_border_on_corner(
    result, 'br', R_px, border_layers,
    src_img=cropped, validity_mask=final_mask
)

# 检查结果
result_arr = np.array(result)
print(f"\n[结果图检查 - 调用 _redraw_border_on_corner 之后]")
for depth in [0, 50, 100, 150, 200]:
    x = cw - 1 - depth
    y = ch - 1 - depth
    if 0 <= x < cw and 0 <= y < ch:
        pixel = tuple(result_arr[y, x].tolist())
        print(f"  深度 {depth}px: 像素 {pixel}")

# 检查圆角弧线区域（角度315°，即对角线方向）
print(f"\n[圆角弧线检查 - 45°方向]")
import math
for r in [50, 100, 150, 200]:
    # 右下角 (br): 圆心在 (w-R, h-R)
    cx = cw - R_px
    cy = ch - R_px
    # 角度315° = 对角线方向（从圆心向外）
    angle = math.radians(315)
    x = int(round(cx + r * math.cos(angle)))
    y = int(round(cy + r * math.sin(angle)))
    if 0 <= x < cw and 0 <= y < ch:
        pixel = tuple(result_arr[y, x].tolist())
        src_pixel = tuple(np.array(cropped)[y, x].tolist())
        print(f"  半径 {r}px: 结果={pixel}, 原图={src_pixel}")

print("\n" + "=" * 60)
