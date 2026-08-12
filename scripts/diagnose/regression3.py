import sys
sys.path.insert(0, '.')
from PIL import Image, ImageDraw
import numpy as np
from core.config import DEFAULT_DPI
from core.image_cropper import _get_border_layers_robust, apply_rounded_corners

dpi = DEFAULT_DPI

def make_huaman_style():
    w = int(round(40 * dpi / 2.54))
    h = int(round(160 * dpi / 2.54))
    img = Image.new('RGB', (w, h), (248, 240, 220))
    draw = ImageDraw.Draw(img)
    outer = int(round(0.3 * dpi / 2.54))
    for i in range(outer):
        draw.rectangle([i, i, w-1-i, h-1-i], outline=(0, 0, 0))
    gap = int(round(0.25 * dpi / 2.54))
    tmp = np.array(img)
    for i in range(outer, outer + gap):
        for x in range(i, w-i):
            tmp[i,x,:] = [245, 237, 218]
            tmp[h-1-i,x,:] = [245, 237, 218]
        for y in range(i, h-i):
            tmp[y,i,:] = [245, 237, 218]
            tmp[y,w-1-i,:] = [245, 237, 218]
    img = Image.fromarray(tmp.astype(np.uint8))
    draw = ImageDraw.Draw(img)
    inner = int(round(0.3 * dpi / 2.54))
    for i in range(outer + gap, outer + gap + inner):
        draw.rectangle([i, i, w-1-i, h-1-i], outline=(0, 0, 0))
    return img, w, h, outer, gap, inner

# 花幔模式
img, w, h, t_outer, t_gap, t_inner = make_huaman_style()
total_b = t_outer + t_gap + t_inner
R = int(round(3.6 * dpi / 2.54))
corners = {'bl': R, 'br': R}
print('=' * 60)
print('花幔模式：BL/BR 3.6cm 圆角，边框总厚 %dpx (%.2fcm)' % (total_b, total_b*2.54/dpi))
border_layers = _get_border_layers_robust(img, (255,255,255))
print('检测到 %d 层边框' % len(border_layers))
result = apply_rounded_corners(img, corners, dpi, (255,255,255))
img_arr = np.array(img)
res_arr = np.array(result)

def check_continuity(img_arr, res_arr, cx, cy, R_corner, total_border, corner_type, corner_name):
    """corner_type: tl/tr/bl/br 决定扇形象限 & 哪两条边的条带"""
    h_im, w_im = img_arr.shape[:2]
    checked = 0
    problems = 0
    print('\n--- %s 角 ---' % corner_name)
    ys = range(max(0, cy - R_corner - 2), min(h_im, cy + R_corner + 2))
    xs = range(max(0, cx - R_corner - 2), min(w_im, cx + R_corner + 2))
    fails_to_print = 0
    for y in ys:
        for x in xs:
            # 1. 象限过滤
            if corner_type == 'tl' and not (x <= cx and y <= cy): continue
            if corner_type == 'tr' and not (x >= cx and y <= cy): continue
            if corner_type == 'bl' and not (x <= cx and y >= cy): continue
            if corner_type == 'br' and not (x >= cx and y >= cy): continue
            # 2. 扇形半径过滤
            dist = np.sqrt((x-cx)**2 + (y-cy)**2)
            if dist > float(R_corner):
                continue
            # 3. 在边框条带内
            if corner_type == 'tl':
                in_strip = (x < total_border) or (y < total_border)
            elif corner_type == 'tr':
                in_strip = (x >= w_im - total_border) or (y < total_border)
            elif corner_type == 'bl':
                in_strip = (x < total_border) or (y >= h_im - total_border)
            else:  # br
                in_strip = (x >= w_im - total_border) or (y >= h_im - total_border)
            if not in_strip:
                continue

            src = img_arr[y, x].astype(np.float64)
            res = res_arr[y, x].astype(np.float64)
            d_black = np.sqrt(np.sum((res - [0,0,0])**2))
            d_beige = np.sqrt(np.sum((res - [245, 237, 218])**2))
            d_content = np.sqrt(np.sum((res - [248,240,220])**2))
            d_pat = np.sqrt(np.sum((res - [180,100,80])**2))
            d_white = np.sqrt(np.sum((res - [255,255,255])**2))
            min_ok = min(d_black, d_beige)
            min_bad = min(d_content, d_pat, d_white)
            checked += 1
            if min_ok > 40 and min_bad < 45:
                problems += 1
                if fails_to_print < 5:
                    fails_to_print += 1
                    depth = R_corner - dist
                    in_x = 'x-strip' if (x < total_border if corner_type in ('tl','bl') else x >= w_im-total_border) else '       '
                    in_y = 'y-strip' if (y < total_border if corner_type in ('tl','tr') else y >= h_im-total_border) else '       '
                    print('  FAIL @(%4d,%4d) d=%5.1fpx %s %s | src(%3d,%3d,%3d) res(%3d,%3d,%3d) | ok_min=%.1f bad_min=%.1f' % (
                        x, y, depth, in_x, in_y,
                        int(src[0]),int(src[1]),int(src[2]),
                        int(res[0]),int(res[1]),int(res[2]),
                        min_ok, min_bad))
    print('  检查 %d 像素，异常 %d 个' % (checked, problems))
    return problems

p_bl = check_continuity(img_arr, res_arr, R, h-R, R, total_b, 'bl', 'BL')
p_br = check_continuity(img_arr, res_arr, w-R, h-R, R, total_b, 'br', 'BR')
print('\n花幔模式边框色异常总计：%d 个' % (p_bl + p_br))

# ========================
# 堇色素颜模式
# ========================
print('\n' + '=' * 60)
print('堇色素颜：四角 8cm 圆角 - 装饰带花纹底色弧形缺口检查')
w2 = int(round(80 * dpi / 2.54))
h2 = int(round(140 * dpi / 2.54))
jinsu = Image.new('RGB', (w2, h2), (250, 245, 230))
draw2 = ImageDraw.Draw(jinsu)
black_1 = int(round(0.4 * dpi / 2.54))
for i in range(black_1):
    draw2.rectangle([i, i, w2-1-i, h2-1-i], outline=(0, 0, 0))
gap_1 = int(round(0.3 * dpi / 2.54))
tmp = np.array(jinsu)
for i in range(black_1, black_1 + gap_1):
    for x in range(i, w2-i):
        tmp[i,x,:] = [250,245,230]
        tmp[h2-1-i,x,:] = [250,245,230]
    for y in range(i, h2-i):
        tmp[y,i,:] = [250,245,230]
        tmp[y,w2-1-i,:] = [250,245,230]
jinsu = Image.fromarray(tmp.astype(np.uint8))
draw2 = ImageDraw.Draw(jinsu)
black_2 = int(round(0.2 * dpi / 2.54))
for i in range(black_1+gap_1, black_1+gap_1+black_2):
    draw2.rectangle([i, i, w2-1-i, h2-1-i], outline=(20,20,20))
decor_s = black_1 + gap_1 + black_2
decor_e = int(round(7.5 * dpi / 2.54))
for d in range(decor_s + 10, decor_e, 25):
    for x in range(decor_e, w2-decor_e, 50):
        draw2.ellipse([x, d-4, x+8, d+4], fill=(0,0,0))
        draw2.ellipse([x, h2-1-d-4, x+8, h2-1-d+4], fill=(0,0,0))
    for y in range(decor_e, h2-decor_e, 50):
        draw2.ellipse([d-4, y, d+4, y+8], fill=(0,0,0))
        draw2.ellipse([w2-1-d-4, y, w2-1-d+4, y+8], fill=(0,0,0))

R2 = int(round(8 * dpi / 2.54))
result_j = apply_rounded_corners(jinsu, {'tl':R2,'tr':R2,'bl':R2,'br':R2}, dpi, (255,255,255))
j_arr = np.array(jinsu)
rj_arr = np.array(result_j)
content_ref = np.array([250, 245, 230], dtype=np.float64)
white_ref = np.array([255, 255, 255], dtype=np.float64)

centers = {'TL':('tl', R2, R2), 'TR':('tr', w2-R2, R2), 'BL':('bl', R2, h2-R2), 'BR':('br', w2-R2, h2-R2)}
total_problems = 0
for name, (ctype, cx, cy) in centers.items():
    p_corner = 0
    for ang_deg in np.arange(15, 76, 5):
        a = np.radians(ang_deg)
        for f in np.arange(0.05, 0.95, 0.02):
            # 正确象限：TR=(cx-Rf*cos, cy-Rf*sin), BL=(cx-Rf*cos, cy+Rf*sin)...
            # 按角类型计算
            if ctype == 'tl':
                px = cx - int(round(R2 * f * np.cos(a)))
                py = cy - int(round(R2 * f * np.sin(a)))
            elif ctype == 'tr':
                px = cx + int(round(R2 * f * np.cos(a))) if False else cx - int(round(R2 * f * np.cos(a)))
                px = cx - int(round(R2 * f * np.cos(a)))  # cx 向右减 cos(a)
                py = cy - int(round(R2 * f * np.sin(a)))  # cy 向上减 sin(a)
                pass
            elif ctype == 'bl':
                px = cx - int(round(R2 * f * np.cos(a)))
                py = cy + int(round(R2 * f * np.sin(a)))
            else:  # br
                px = cx + int(round(R2 * f * np.cos(a))) if False else cx - int(round(R2 * f * np.cos(a)))
                pass
            # 直接用简单方法：按象限坐标判断
            if ctype == 'tl':
                px = int(round(cx - R2 * f * np.cos(a)))
                py = int(round(cy - R2 * f * np.sin(a)))
            elif ctype == 'tr':
                px = int(round(cx + R2 * f * np.cos(a)))  # x 增大=向右
                py = int(round(cy - R2 * f * np.sin(a)))
            elif ctype == 'bl':
                px = int(round(cx - R2 * f * np.cos(a)))
                py = int(round(cy + R2 * f * np.sin(a)))
            else:
                px = int(round(cx + R2 * f * np.cos(a)))
                py = int(round(cy + R2 * f * np.sin(a)))

            if not (0 <= py < h2 and 0 <= px < w2): continue
            dist_v = np.sqrt((px-cx)**2 + (py-cy)**2)
            if dist_v > R2: continue
            depth = R2 - dist_v
            if not (decor_s <= depth <= decor_e): continue
            s = j_arr[py, px]
            r = rj_arr[py, px]
            src_black = np.mean(s) < 80
            res_beige = np.sqrt(np.sum((r.astype(float) - content_ref)**2)) < 25
            if src_black and res_beige and not np.array_equal(s, r):
                p_corner += 1
                total_problems += 1
                if total_problems <= 5:
                    print('  %s FAIL(花纹→底色): ang%.0f d=%.1fcm src(%d,%d,%d) res(%d,%d,%d)' %
                          (name, ang_deg, depth*2.54/dpi, s[0],s[1],s[2], r[0],r[1],r[2]))
    print('  %s 角装饰带：%d 个弧形缺口像素（花纹被底色覆盖）' % (name, p_corner))
print('堇色素颜总计：%d 个问题' % total_problems)

# ========================
# 汇总
# ========================
print('\n' + '=' * 60)
if (p_bl+p_br+total_problems) == 0:
    print('✅ 全部通过：')
else:
    print('❌ 仍有问题：')
print('  花幔 C 形缺口 = %d' % (p_bl + p_br))
print('  堇色素颜底色弧形缺口 = %d' % total_problems)
print('=' * 60)
