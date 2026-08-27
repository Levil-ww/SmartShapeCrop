import sys
sys.path.insert(0, '.')
from PIL import Image, ImageDraw
import numpy as np
from core.config import DEFAULT_DPI
from core.image_cropper import _get_border_layers_robust, apply_rounded_corners

dpi = DEFAULT_DPI
w = int(round(80 * dpi / 2.54))
h = int(round(140 * dpi / 2.54))
r_8cm = int(round(8 * dpi / 2.54))

img = Image.new('RGB', (w, h), (250, 245, 230))
draw = ImageDraw.Draw(img)

# 层1: 最外层黑 0.4cm
black_1 = int(round(0.4 * dpi / 2.54))
for i in range(black_1):
    draw.rectangle([i, i, w-1-i, h-1-i], outline=(0, 0, 0))

# 层2: 米色间隙 0.3cm
gap_1 = int(round(0.3 * dpi / 2.54))
img_arr_tmp = np.array(img)
for i in range(black_1, black_1 + gap_1):
    for x in range(i, w-i):
        img_arr_tmp[i, x, :] = [250, 245, 230]
        img_arr_tmp[h-1-i, x, :] = [250, 245, 230]
    for y in range(i, h-i):
        img_arr_tmp[y, i, :] = [250, 245, 230]
        img_arr_tmp[y, w-1-i, :] = [250, 245, 230]
img = Image.fromarray(img_arr_tmp)
draw = ImageDraw.Draw(img)

# 层3: 黑色细线 0.2cm
black_2 = int(round(0.2 * dpi / 2.54))
for i in range(black_1 + gap_1, black_1 + gap_1 + black_2):
    draw.rectangle([i, i, w-1-i, h-1-i], outline=(20, 20, 20))

# 装饰带
decor_start = black_1 + gap_1 + black_2
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

for d in range(decor_start + 10, decor_end, 25):
    for x in range(decor_end, w-decor_end, 50):
        draw.ellipse([x, d-4, x+8, d+4], fill=(0,0,0))
        draw.ellipse([x, h-1-d-4, x+8, h-1-d+4], fill=(0,0,0))
    for y in range(decor_end, h-decor_end, 50):
        draw.ellipse([d-4, y, d+4, y+8], fill=(0,0,0))
        draw.ellipse([w-1-d-4, y, w-1-d+4, y+8], fill=(0,0,0))

# 内层黑框
inner_frame = decor_end + int(round(0.3 * dpi / 2.54))
inner_thick = int(round(0.3 * dpi / 2.54))
for i in range(inner_thick):
    draw.rectangle([inner_frame+i, inner_frame+i, w-inner_frame-1-i, h-inner_frame-1-i], outline=(0,0,0))

# 检测边框
border_layers = _get_border_layers_robust(img, (255,255,255))
print('=== 边框检测结果 ===')
total = 0
for i, (color, thick) in enumerate(border_layers):
    thick_cm = thick*2.54/dpi
    total += thick
    print('层%d: RGB(%3d,%3d,%3d) %4dpx (%.2fcm)' % (i, color[0], color[1], color[2], thick, thick_cm))
print('总厚度: %dpx (%.2fcm)' % (total, total*2.54/dpi))

content_ref = np.median(np.array(img)[int(h*0.5):int(h*0.6), int(w*0.5):int(w*0.6), :].reshape(-1,3), axis=0)
print('\n内容参考色: RGB(%.0f,%.0f,%.0f)' % (content_ref[0], content_ref[1], content_ref[2]))
print('间隙层检测:')
for i, (color, thick) in enumerate(border_layers):
    dist = np.sqrt(np.sum((np.array(color, dtype=float) - content_ref.astype(float))**2))
    is_gap = dist < 30.0
    print('  层%d: 与内容色距离=%.1f, 是间隙层? %s' % (i, dist, is_gap))

# 处理圆角
corners = {'tl': r_8cm, 'tr': r_8cm, 'bl': r_8cm, 'br': r_8cm}
result = apply_rounded_corners(img, corners, dpi, (255,255,255))

result_arr = np.array(result)
img_arr = np.array(img)
cx, cy = w - r_8cm, r_8cm

print('\n=== TR角 45°对角线 详细像素变化分析 ===')
print('重点检查：黑色花纹像素是否被替换成了间隙色（米色）？')
hdr = '%6s  %12s  %6s  %12s  %12s  %8s  %s' % ('dist','pos','depth','src','res','changed','note')
print(hdr)

problem_count = 0
for factor in np.arange(0.05, 0.95, 0.02):
    px = int(round(cx - r_8cm * factor * np.cos(np.radians(45))))
    py = int(round(cy - r_8cm * factor * np.sin(np.radians(45))))
    dist = np.sqrt((px-cx)**2 + (py-cy)**2)
    depth = r_8cm - dist
    
    src = img_arr[py, px]
    res = result_arr[py, px]
    changed = not np.array_equal(src, res)
    
    src_black = np.mean(src) < 80
    res_beige = np.sqrt(np.sum((res.astype(float) - content_ref.astype(float))**2)) < 25
    
    note = ''
    if src_black and res_beige and changed:
        note = '*** 问题: 黑花纹 -> 米色 ***'
        problem_count += 1
    elif src_black and not np.array_equal(src, res):
        note = '黑纹变了: RGB(%d,%d,%d)' % (res[0], res[1], res[2])
    elif not changed and src_black:
        note = '黑纹保持'
    elif not changed:
        note = '未变'
    else:
        note = 'RGB(%d,%d,%d)->(%d,%d,%d)' % (src[0],src[1],src[2],res[0],res[1],res[2])
    
    line = '%6.1f  (%4d,%4d)  %6.1f  (%3d,%3d,%3d)  (%3d,%3d,%3d)  %8s  %s' % (
        dist, px, py, depth,
        src[0], src[1], src[2],
        res[0], res[1], res[2],
        str(changed), note)
    print(line)

print('\n发现 %d 个问题像素（黑色花纹被替换成米色）' % problem_count)
if problem_count > 0:
    print('根因：间隙层对角内区兜底填充覆盖了装饰带的花纹像素！')
