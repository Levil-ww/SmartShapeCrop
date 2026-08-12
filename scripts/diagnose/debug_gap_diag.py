import sys
sys.path.insert(0, '.')
from PIL import Image, ImageDraw
import numpy as np
from core.config import DEFAULT_DPI
from core.corner.sector_render import _redraw_border_on_corner

dpi = DEFAULT_DPI
w = int(round(80 * dpi / 2.54))
h = int(round(140 * dpi / 2.54))
r_8cm = int(round(8 * dpi / 2.54))

# 简单案例模拟：只有一层间隙层（米色）
img = Image.new('RGB', (w, h), (255, 255, 255))
draw = ImageDraw.Draw(img)

# 外层白 + 米色间隙 + 内层装饰
gap_thick = int(round(0.3 * dpi / 2.54))  # 0.3cm 米色间隙
outer_white = 2

# 模拟检测结果：只有一层米色间隙
# 外层白框
for i in range(outer_white):
    draw.rectangle([i, i, w-1-i, h-1-i], outline=(255,255,255))

# 装饰带（白色背景+花纹）
decor_start = outer_white + gap_thick
decor_end = int(round(7.5 * dpi / 2.54))
for side in range(4):
    if side == 0:
        draw.rectangle([decor_start, 0, w-decor_start-1, decor_end], fill=(255,255,255))
    elif side == 1:
        draw.rectangle([decor_start, h-decor_end-1, w-decor_start-1, h-1], fill=(255,255,255))
    elif side == 2:
        draw.rectangle([0, decor_start, decor_end, h-decor_start-1], fill=(255,255,255))
    else:
        draw.rectangle([w-decor_end-1, decor_start, w-1, h-decor_start-1], fill=(255,255,255))
# 花纹
for d in range(decor_start + 10, decor_end, 25):
    for x in range(decor_end, w-decor_end, 50):
        draw.ellipse([x, d-4, x+8, d+4], fill=(0,0,0))
        draw.ellipse([x, h-1-d-4, x+8, h-1-d+4], fill=(0,0,0))
    for y in range(decor_end, h-decor_end, 50):
        draw.ellipse([d-4, y, d+4, y+8], fill=(0,0,0))
        draw.ellipse([w-1-d-4, y, w-1-d+4, y+8], fill=(0,0,0))

border_layers = [((250, 245, 230), gap_thick)]  # 一层米色间隙
content_ref = np.array([250, 245, 230])

# 构造模拟 result
result = img.copy()
# 先模拟圆角 mask 剪切
from core.corner.algorithm import carve_corner_on_mask
for c, r in [('tl', r_8cm), ('tr', r_8cm), ('bl', r_8cm), ('br', r_8cm)]:
    mask = Image.new('L', (w, h), 0)
    carve_corner_on_mask(mask, c, r)
    cutout = Image.new('RGBA', (w, h), (255,255,255,255))
    cutout.paste(img, (0,0), mask)
    cutout_rgb = cutout.convert('RGB')
    mask_arr = np.array(mask)
    result_arr = np.array(result)
    result_arr[mask_arr > 0] = np.array(cutout_rgb)[mask_arr > 0]
    result = Image.fromarray(result_arr)

# 调用 _redraw_border_on_corner 并打补丁检查 diagonal_interior
from core.corner.sector_render import COLOR_DIST_THRESHOLD

print('=== 验证假设：间隙层 diagonal_interior 是否覆盖装饰带 ===')
print('边框层：1层米色间隙，厚度 = %dpx (%.2fcm)' % (gap_thick, gap_thick*2.54/dpi))
print('圆角半径：R = %dpx (%.2fcm)' % (r_8cm, r_8cm*2.54/dpi))
print()

# 手动计算 TR 角的 diagonal_interior 范围
R_total = r_8cm
cx = w - R_total
cy = R_total

# 间隙层 cumulative_depths
cumulative_depths = [0, gap_thick]
for ci in range(len(border_layers)):
    color = border_layers[ci][0]
    x_left = w - cumulative_depths[ci+1]   # TR
    x_right = w - cumulative_depths[ci]
    y_top = cumulative_depths[ci]
    y_bottom = cumulative_depths[ci+1]
    print('层%d (米色间隙):' % ci)
    print('  直边延伸带: x=[%d, %d) y=[%d, %d)' % (x_left, x_right, y_top, y_bottom))
    print('  即：仅最右边 %d~%dpx 竖带，最上边 %d~%dpx 横带' % 
          (w-x_right, w-x_left, y_top, y_bottom))
    print('  diagonal_interior = 整个扇形 - 这两个小条带')
    print('  → 对角内区对角线覆盖范围: depth from 0 to %dpx (0~%.2fcm)' % 
          (R_total, R_total*2.54/dpi))
    print()
    print('  ⚠️ 问题：间隙层 diagonal_interior 会把装饰带区域（depth=%d~%dpx）' %
          (decor_start, decor_end))
    print('     的所有像素强制填成米色！花纹全部消失 → 这就是"内层底色弧形缺口"')

# 验证 TR 角 45°对角线上装饰带区域的像素
img_arr = np.array(img)
result_arr = np.array(result)
print()
print('=== TR 角 45°对角线 装饰带区域 像素变化（间隙层重绘前） ===')
print('%8s  %6s  %12s  %12s' % ('depth', 'dist', 'src', 'result'))
for factor in np.arange(0.3, 0.7, 0.05):
    px = int(round(cx - r_8cm * factor * np.cos(np.radians(45))))
    py = int(round(cy - r_8cm * factor * np.sin(np.radians(45))))
    dist = np.sqrt((px-cx)**2 + (py-cy)**2)
    depth_val = r_8cm - dist
    src = img_arr[py, px]
    res = result_arr[py, px]
    is_decor = decor_start <= depth_val <= decor_end
    tag = ' [装饰带]' if is_decor else ''
    print('%8.1f  %6.1f  (%3d,%3d,%3d)  (%3d,%3d,%3d)%s' % (
        depth_val, dist,
        src[0], src[1], src[2],
        res[0], res[1], res[2], tag))
