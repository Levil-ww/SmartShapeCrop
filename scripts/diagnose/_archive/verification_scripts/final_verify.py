import sys
sys.path.insert(0, '.')
from PIL import Image, ImageDraw
import numpy as np
from core.config import DEFAULT_DPI
from core.image_cropper import _get_border_layers_robust, apply_rounded_corners

dpi = DEFAULT_DPI

# 构造花幔模式
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

R = int(round(3.6 * dpi / 2.54))
border_layers = _get_border_layers_robust(img, (255,255,255))
print('花幔模式边框检测:')
cum = [0]
for i, (c, t) in enumerate(border_layers):
    print('  层%d: RGB(%d,%d,%d) %dpx depth[%d~%d]' % (
        i, c[0],c[1],c[2], t, cum[-1], cum[-1]+t))
    cum.append(cum[-1] + t)
result = apply_rounded_corners(img, {'bl':R, 'br':R}, dpi, (255,255,255))
res_arr = np.array(result)
img_arr = np.array(img)

# ============ 精准检查：对角内区 + 间隙层深度范围 ============
# 这才是间隙层对角内区兜底的真正作用点
print('\n========= 精准检查：BL 角间隙层对角内区 ============')
print('检查条件：像素在扇形内 + 对角内区（非 x/y strip）+ depth ∈ 间隙层 [%d, %d)' % (cum[1], cum[2]))
print('期望：这些像素被填成间隙色 RGB(%d,%d,%d) = 连续，无 C 形缺口' % (gap_c[0],gap_c[1],gap_c[2]))

cx, cy = R, h - R
gap_start, gap_end = cum[1], cum[2]
total_border = cum[min(3, len(cum)-1)]

def check_corner(cx, cy, ctype, name):
    h_im, w_im = res_arr.shape[:2]
    checked = 0
    correct = 0
    wrong = 0
    wrong_examples = []
    for y in range(h_im):
        for x in range(w_im):
            if ctype == 'bl':
                if not (x <= cx and y >= cy): continue
            elif ctype == 'br':
                if not (x >= cx and y >= cy): continue
            dist = np.sqrt((x-cx)**2 + (y-cy)**2)
            if dist > float(R):
                continue
            depth = R - dist
            # 间隙层深度区间内的像素
            if not (gap_start <= depth < gap_end):
                continue
            # 对角内区：不在两条直边 strip 内（这才是真正需要兜底填补的区域）
            if ctype == 'bl':
                in_strip = (x < total_border) or (y >= h_im - total_border)
            else:  # br
                in_strip = (x >= w_im - total_border) or (y >= h_im - total_border)
            if in_strip:
                continue  # 直边条带内，不属于对角内区
            # 到这里：对角内区 + 间隙层深度 —— 必须是间隙色！
            checked += 1
            res = res_arr[y, x].astype(np.float64)
            d_gap = np.sqrt(np.sum((res - np.array(gap_c, dtype=float))**2))
            # 也允许间隙色与内容色近似（两者色差本来就小）
            d_content = np.sqrt(np.sum((res - np.array([248,240,220],dtype=float))**2))
            if d_gap < 20 or d_content < 20:
                correct += 1
            else:
                wrong += 1
                if len(wrong_examples) < 3:
                    d_black = np.sqrt(np.sum((res - [0,0,0])**2))
                    d_white = np.sqrt(np.sum((res - [255,255,255])**2))
                    what = '画布白' if d_white < 20 else ('黑' if d_black < 20 else '未知(%d,%d,%d)'%(int(res[0]),int(res[1]),int(res[2])))
                    wrong_examples.append('  (@%d,%d d=%.1f) res=%s' % (x, y, depth, what))
    print('\n%s 角:' % name)
    print('  对角内区+间隙层深度: %d 像素' % checked)
    print('  填补为间隙/内容近似色: %d (%.1f%%) ✓' % (correct, 100*correct/max(1,checked)))
    print('  异常像素（C 形缺口）: %d (%.1f%%) ✗' % (wrong, 100*wrong/max(1,checked)))
    for e in wrong_examples:
        print(e)
    return wrong

w_bl = check_corner(R, h-R, 'bl', 'BL')
w_br = check_corner(w-R, h-R, 'br', 'BR')

print('\n========= 汇总 =========')
if (w_bl + w_br) == 0:
    print('✅ 花幔模式：间隙层对角内区兜底 STILL 有效，C 形缺口无回归！')
else:
    print('❌ 花幔模式：仍有 %d 个 C 形缺口像素（间隙层对角内区未正确填补）' % (w_bl + w_br))

# ========================
# 堇色素颜最终确认
# ========================
print('\n' + '=' * 50)
print('堇色素颜：内层底色弧形缺口 bug 修复确认')
print('=' * 50)
w2 = int(round(80 * dpi / 2.54))
h2 = int(round(140 * dpi / 2.54))
j = Image.new('RGB', (w2, h2), (250, 245, 230))
drj = ImageDraw.Draw(j)
b1 = int(round(0.4 * dpi / 2.54))
for i in range(b1):
    drj.rectangle([i, i, w2-1-i, h2-1-i], outline=(0,0,0))
g1 = int(round(0.3 * dpi / 2.54))
arr = np.array(j)
for i in range(b1, b1+g1):
    for x in range(i, w2-i):
        arr[i,x,:]=[250,245,230]; arr[h2-1-i,x,:]=[250,245,230]
    for y in range(i, h2-i):
        arr[y,i,:]=[250,245,230]; arr[y,w2-1-i,:]=[250,245,230]
j = Image.fromarray(arr.astype(np.uint8))
drj = ImageDraw.Draw(j)
b2 = int(round(0.2 * dpi / 2.54))
for i in range(b1+g1, b1+g1+b2):
    drj.rectangle([i,i,w2-1-i,h2-1-i], outline=(20,20,20))
decor_s = b1+g1+b2
decor_e = int(round(7.5 * dpi / 2.54))
# 装饰花纹 - 使用密集点阵在装饰带
dots = []
for d in range(decor_s+5, decor_e, 12):
    for x in range(decor_e, w2-decor_e, 18):
        dots.append((x, d)); dots.append((x, h2-1-d))
    for y in range(decor_e, h2-decor_e, 18):
        dots.append((d, y)); dots.append((w2-1-d, y))
for x, y in dots:
    drj.rectangle([x-1, y-1, x+1, y+1], fill=(0,0,0))

R2 = int(round(8 * dpi / 2.54))
rj = apply_rounded_corners(j, {'tl':R2,'tr':R2,'bl':R2,'br':R2}, dpi, (255,255,255))
rj_arr = np.array(rj)
j_arr = np.array(j)
cref = np.array([250, 245, 230], dtype=float)

total_p = 0
corners_def = {'TL':('tl', R2, R2), 'TR':('tr', w2-R2, R2), 'BL':('bl', R2, h2-R2), 'BR':('br', w2-R2, h2-R2)}
for n, (ct, cx, cy) in corners_def.items():
    pc = 0
    for ang in np.arange(10, 81, 2):
        a = np.radians(ang)
        for f in np.arange(0.1, 0.91, 0.01):
            if ct == 'tl':
                px = cx - int(round(R2 * f * np.cos(a)))
                py = cy - int(round(R2 * f * np.sin(a)))
            elif ct == 'tr':
                px = cx + int(round(R2 * f * np.cos(a)))
                py = cy - int(round(R2 * f * np.sin(a)))
            elif ct == 'bl':
                px = cx - int(round(R2 * f * np.cos(a)))
                py = cy + int(round(R2 * f * np.sin(a)))
            else:
                px = cx + int(round(R2 * f * np.cos(a)))
                py = cy + int(round(R2 * f * np.sin(a)))
            if not (0 <= py < h2 and 0 <= px < w2):
                continue
            if np.sqrt((px-cx)**2 + (py-cy)**2) > R2:
                continue
            dv = R2 - np.sqrt((px-cx)**2 + (py-cy)**2)
            if not (decor_s <= dv <= decor_e):
                continue
            s = j_arr[py, px]
            r = rj_arr[py, px]
            src_black = np.mean(s.astype(float)) < 60
            res_beige = np.sqrt(np.sum((r.astype(float)-cref)**2)) < 22
            if src_black and res_beige and not np.array_equal(s, r):
                pc += 1
                total_p += 1
    print('  %s 装饰带花纹被底色覆盖: %d 个' % (n, pc))
if total_p == 0:
    print('✅ 堇色素颜：内层底色弧形缺口 BUG 已修复！（0 个问题像素）')
else:
    print('❌ 堇色素颜：仍有 %d 个底色弧形缺口像素' % total_p)
