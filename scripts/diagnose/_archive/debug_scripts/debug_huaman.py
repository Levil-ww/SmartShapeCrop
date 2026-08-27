import sys
sys.path.insert(0, '.')
from PIL import Image, ImageDraw
import numpy as np
from core.config import DEFAULT_DPI
from core.image_cropper import _get_border_layers_robust
COLOR_DIST_THRESHOLD = 15.0

dpi = DEFAULT_DPI
w = int(round(40 * dpi / 2.54))
h = int(round(160 * dpi / 2.54))
img = Image.new('RGB', (w, h), (248, 240, 220))
draw = ImageDraw.Draw(img)
outer = int(round(0.3 * dpi / 2.54))
for i in range(outer):
    draw.rectangle([i, i, w-1-i, h-1-i], outline=(0, 0, 0))
gap = int(round(0.25 * dpi / 2.54))
tmp = np.array(img)
gap_c = [245, 237, 218]
for i in range(outer, outer + gap):
    for x in range(i, w-i):
        tmp[i,x,:] = gap_c
        tmp[h-1-i,x,:] = gap_c
    for y in range(i, h-i):
        tmp[y,i,:] = gap_c
        tmp[y,w-1-i,:] = gap_c
img = Image.fromarray(tmp.astype(np.uint8))
draw = ImageDraw.Draw(img)
inner = int(round(0.3 * dpi / 2.54))
for i in range(outer + gap, outer + gap + inner):
    draw.rectangle([i, i, w-1-i, h-1-i], outline=(0, 0, 0))

border_layers = _get_border_layers_robust(img, (255,255,255))
print('边框层:')
cum = [0]
for i, (c, t) in enumerate(border_layers):
    print('  层%d: RGB(%d,%d,%d) %dpx' % (i, c[0],c[1],c[2], t))
    cum.append(cum[-1]+t)
total_border_depth = sum(t for _, t in border_layers)
print('total_border_depth =', total_border_depth)

# 间隙层 regions
is_gap_layer = []
content_ref = np.array([248, 240, 220], dtype=np.float64)
for c, _ in border_layers:
    d = np.sqrt(np.sum((np.array(c, dtype=float) - content_ref)**2))
    is_gap_layer.append(d < COLOR_DIST_THRESHOLD)
print('is_gap_layer:', is_gap_layer)  # 应该是 [False, True, False, True]

# 手动模拟 color_idx=1 (间隙层) 的 d=20 单像素情形
R = int(round(3.6 * dpi / 2.54))
corner_key = 'bl'
print('R =', R, 'total_border_depth =', total_border_depth)

# 给 color_idx = 1 (gap layer, depth 19~34, is_gap=True) 打补丁验证
# 具体看 d=20
# 在代码中, diagonal_interior 计算基于 cumulative_depths[color_idx], cumulative_depths[color_idx+1]
# color_idx=1: x_left=cum_before_i=19, x_right=cum_after_i=34 (BL 角)
#             y_top = h - cum_after_i = h - 34, y_bottom = h - cum_before_i = h - 19
#             in_left_strip = (global_x >= 19) & (global_x < 34)
#             in_bottom_strip = (global_y >= h-34) & (global_y < h-19)
#             in_extension = in_left_strip | in_bottom_strip
#             diagonal_interior = ~in_extension

print('\n=== 手动追踪 BL 角间隙层 color_idx=1, d=20 ===')
print('BL角 strips: left_x=[19,34), bottom_y=[%d,%d)' % (h-34, h-19))
print('diagonal_interior = 非左边 strip 且 非下边 strip')

# 找一个典型的对角内区像素，depth ≈ 20，不在 left/bottom strip
# BL 圆心 = (R, h-R) = (213, h-213). dist = R - depth = 213-20 = 193
# 对角点 (45°): x = cx - dist*cos45, y = cy + dist*sin45
cx, cy = R, h - R
dist_d = R - 20
px = int(round(cx - dist_d * np.cos(np.radians(45))))
py = int(round(cy + dist_d * np.sin(np.radians(45))))
print('典型对角点: (%d, %d), dist=%.1f, depth=%.1f' % (px, py, dist_d, 20))
print('  x=%d 在 left_strip [19,34)? %s' % (px, 'YES' if 19 <= px < 34 else 'NO'))
print('  y=%d 在 bottom_strip [%d,%d)? %s' % (py, h-34, h-19, 'YES' if h-34 <= py < h-19 else 'NO'))
print('  → diagonal_interior? %s (期望 YES)' % ('YES' if not (19 <= px < 34 or h-34 <= py < h-19) else 'NO'))

# 关键: di_depths (≈ 20.0) vs [19, 34)
print('\n  di_depths = %.1f  vs  layer_range [%d, %d)' % (20.0, cum[1], cum[2]))
print('  in_this_layer? %s (期望 YES)' % ('YES' if cum[1] <= 20.0 < cum[2] else 'NO'))
print('  理论: 该像素应当被填入间隙色 RGB(245,237,218)')

# 现在用实际函数跑，在间隙层处理时打日志
# 创建一个简单的 result
result = Image.new('RGB', (w, h), (255, 255, 255))  # 白底画布
# 用圆角 mask 先 paste 一次（模拟 apply_rounded_corners 的 mask paste）
from core.corner.algorithm import carve_corner_on_mask
mask = Image.new('L', (w, h), 0)
carve_corner_on_mask(mask, 'bl', R)
cutout = Image.new('RGBA', (w, h), (255,255,255,255))
cutout.paste(img, (0, 0), mask)
result.paste(cutout.convert('RGB'), (0, 0))

# 检查 paste 后那个典型点的颜色
res_arr_before = np.array(result)
print('\npaste 后，典型对角点 (%d,%d) 颜色 = RGB(%d,%d,%d)' % (
    px, py, res_arr_before[py, px, 0], res_arr_before[py, px, 1], res_arr_before[py, px, 2]))
print('（这是 mask paste 后的值 —— 如果该点在 mask 内会是 src，否则为画布白 255,255,255）')

# 检查该点是否在 mask 内（即圆角半径内且在正确象限）
print('该点 dist = %.1f vs R=%d，在扇形半径内？ %s' % (dist_d, R, 'YES' if dist_d <= R else 'NO'))
print('BL 象限 x<=cx (%d) ? %s，y>=cy (%d) ? %s' % (cx, 'YES' if px<=cx else 'NO', cy, 'YES' if py>=cy else 'NO'))

# 现在用 validity mask 手动判断
# 先看 validity_mask 来自 apply_rounded_corners 中计算的什么？其实是 all_border_mask
# validity_mask = any depth < total_border_depth 的像素
# 即 x < total_border OR y < total_border（TL 角）等
total_b = total_border_depth
in_border_strip = (px < total_b) or (py >= h - total_b)  # BL 角
print('BL角 validity_mask：x < %d 或 y >= %d → 该点在 validity 内？ %s' % (total_b, h-total_b, 'YES' if in_border_strip else 'NO'))

print('\n=== 如果在 validity 内，会被 _redraw_border_on_corner 处理，否则跳过 ===')
if in_border_strip:
    print('进入重绘流程，理论上在 d=20 (color_idx=1 gap) 时走 gap 填补')
else:
    print('不在 validity mask 中 → 跳过 _redraw_border_on_corner')
    print('该点保留 paste 结果 =', tuple(res_arr_before[py, px]))
    if tuple(res_arr_before[py, px]) == (255, 255, 255):
        print('→ 画布白且不重绘 = 这就是 C 形缺口的真正原因！')
        print('→ 根本上：validity_mask 范围太小，没包含对角内区需要填补的像素')
