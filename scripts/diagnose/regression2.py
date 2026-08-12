import sys
sys.path.insert(0, '.')
from PIL import Image, ImageDraw
import numpy as np
from core.config import DEFAULT_DPI
from core.image_cropper import _get_border_layers_robust, apply_rounded_corners

dpi = DEFAULT_DPI

# ==============================
# 回归测试 1: 花幔模式 - 黑-米-黑三层边框
# 真正的 C 形缺口检查：在「边框总厚度区域内」（x<total 且 y<total），
# 对角内区本应被间隙层/有色层补色的像素是否露出了花纹/白色
# ==============================
def make_huaman_style():
    w = int(round(40 * dpi / 2.54))
    h = int(round(160 * dpi / 2.54))
    img = Image.new('RGB', (w, h), (248, 240, 220))
    draw = ImageDraw.Draw(img)

    outer = int(round(0.3 * dpi / 2.54))
    for i in range(outer):
        draw.rectangle([i, i, w-1-i, h-1-i], outline=(0, 0, 0))

    gap = int(round(0.25 * dpi / 2.54))
    img_arr_tmp = np.array(img)
    gap_color = [245, 237, 218]
    for i in range(outer, outer + gap):
        for x in range(i, w-i):
            img_arr_tmp[i, x, :] = gap_color
            img_arr_tmp[h-1-i, x, :] = gap_color
        for y in range(i, h-i):
            img_arr_tmp[y, i, :] = gap_color
            img_arr_tmp[y, w-1-i, :] = gap_color
    img = Image.fromarray(img_arr_tmp.astype(np.uint8))
    draw = ImageDraw.Draw(img)

    inner = int(round(0.3 * dpi / 2.54))
    for i in range(outer + gap, outer + gap + inner):
        draw.rectangle([i, i, w-1-i, h-1-i], outline=(0, 0, 0))

    # 内容区装饰（离边框有一定距离，避免与边框混淆）
    inner_start = outer + gap + inner + int(round(2.0 * dpi / 2.54))
    draw_obj = ImageDraw.Draw(img)
    for d in range(inner_start, w//2, 40):
        for x in range(inner_start, w - inner_start, 50):
            draw_obj.ellipse([x, d-3, x+6, d+3], fill=(180, 100, 80))
            draw_obj.ellipse([x, h-1-d-3, x+6, h-1-d+3], fill=(180, 100, 80))
    return img, w, h, outer, gap, inner

# 构造花幔测试图
img, w, h, t_outer, t_gap, t_inner = make_huaman_style()
total_b = t_outer + t_gap + t_inner
R = int(round(3.6 * dpi / 2.54))
corners = {'bl': R, 'br': R}

border_layers = _get_border_layers_robust(img, (255,255,255))
print('=' * 60)
print('回归测试 1：花幔模式（BL/BR 3.6cm 圆角）')
print('边框结构：黑 %dpx - 米 %dpx - 黑 %dpx = 总 %dpx (%.2fcm)' %
      (t_outer, t_gap, t_inner, total_b, total_b*2.54/dpi))
print('检测到 %d 层边框:' % len(border_layers))
for i, (c, t) in enumerate(border_layers):
    print('  层%d: RGB(%d,%d,%d) %dpx (%.2fcm)' % (i, c[0],c[1],c[2], t, t*2.54/dpi))
print('圆角半径 R = %dpx (%.2fcm)' % (R, R*2.54/dpi))
print()

result = apply_rounded_corners(img, corners, dpi, (255,255,255))
img_arr = np.array(img)
res_arr = np.array(result)

# ================= 真正的边框连续性检查 =================
# 在角附近，采样 x<total_b 或 y<total_b 的像素（即真正在边框条带里）
# 沿着圆角内侧采样：这些像素在圆角裁剪后的边框里，应该保持边框色/间隙色的连续性
def check_border_continuity(img_arr, res_arr, cx, cy, R_corner, total_border, corner_name):
    print('\n--- %s 角边框连续性检查 (R=%d, total_border=%d) ---' % (corner_name, R_corner, total_border))
    print('扫描方式：在扇形区内满足 x<total_border OR y<total_border 的像素（真·边框条带内）')
    h, w = img_arr.shape[:2]
    checked = 0
    problems = 0

    # 扇形内用细密网格采样
    ys = range(max(0, cy - R_corner - 2), min(h, cy + R_corner + 2))
    xs = range(max(0, cx - R_corner - 2), min(w, cx + R_corner + 2))
    for y in ys:
        for x in xs:
            dist = np.sqrt((x-cx)**2 + (y-cy)**2)
            if dist > float(R_corner):
                continue
            # 只查位于两个直边边框条带的像素
            if not (x < total_border or y < total_border):
                continue

            src = img_arr[y, x].astype(np.float64)
            res = res_arr[y, x].astype(np.float64)

            # 判断该像素应该保持边框组颜色：离 黑 或 米间隙 颜色都太远 → 异常
            d_black = np.sqrt(np.sum((res - [0,0,0])**2))
            d_beige = np.sqrt(np.sum((res - [245, 237, 218])**2))
            d_content = np.sqrt(np.sum((res - [248,240,220])**2))
            d_pat = np.sqrt(np.sum((res - [180,100,80])**2))
            d_white = np.sqrt(np.sum((res - [255,255,255])**2))

            min_ok = min(d_black, d_beige)  # 这两个颜色是可接受的（边框/间隙）
            min_bad = min(d_content, d_pat, d_white)  # 这些是异常颜色（内容/花纹/画布白）

            checked += 1
            if min_ok > 40 and min_bad < 45:
                problems += 1
                if problems <= 8:
                    depth = R_corner - dist
                    # 判断像素在哪个条带
                    in_x = 'x-strip' if x < total_border else '       '
                    in_y = 'y-strip' if y < total_border else '       '
                    print('  FAIL @(%3d,%3d) d=%5.1fpx %s %s | '
                          'src=(%3d,%3d,%3d) res=(%3d,%3d,%3d) | '
                          'ok_dist=%.1f bad_dist=%.1f' % (
                        x, y, depth, in_x, in_y,
                        int(src[0]), int(src[1]), int(src[2]),
                        int(res[0]), int(res[1]), int(res[2]),
                        min_ok, min_bad))

    print('  %s 角：检查 %d 像素，边框色异常 %d 个' % (corner_name, checked, problems))
    return problems

p_bl = check_border_continuity(img_arr, res_arr, R, h-R, R, total_b, 'BL')
p_br = check_border_continuity(img_arr, res_arr, w-R, h-R, R, total_b, 'BR')
print('\n花幔模式总计边框色异常：%d 个（C 形缺口）' % (p_bl + p_br))

# ==============================
# 回归测试 2：堇色素颜模式修复验证
# ==============================
print('\n' + '=' * 60)
print('回归测试 2：堇色素颜（四角 8cm 圆角）- 装饰带不应被米色覆盖')
img2_arr = np.array(img)  # reuse huaman first, then remake jinsu
from PIL import Image as IM
import numpy as np

# 重造堇色素颜
w2 = int(round(80 * dpi / 2.54))
h2 = int(round(140 * dpi / 2.54))
jinsu = IM.new('RGB', (w2, h2), (250, 245, 230))
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
jinsu = IM.fromarray(tmp.astype(np.uint8))
draw2 = ImageDraw.Draw(jinsu)
black_2 = int(round(0.2 * dpi / 2.54))
for i in range(black_1+gap_1, black_1+gap_1+black_2):
    draw2.rectangle([i, i, w2-1-i, h2-1-i], outline=(20,20,20))
decor_s = black_1 + gap_1 + black_2
decor_e = int(round(7.5 * dpi / 2.54))
for side in range(4):
    if side == 0: draw2.rectangle([decor_s, 0, w2-decor_s-1, decor_e], fill=(255,255,255))
    elif side == 1: draw2.rectangle([decor_s, h2-decor_e-1, w2-decor_s-1, h2-1], fill=(255,255,255))
    elif side == 2: draw2.rectangle([0, decor_s, decor_e, h2-decor_s-1], fill=(255,255,255))
    else: draw2.rectangle([w2-decor_e-1, decor_s, w2-1, h2-decor_s-1], fill=(255,255,255))
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
print('\n装饰带区域（depth %d~%dpx = %.1f~%.1fcm）检查：' %
      (decor_s, decor_e, decor_s*2.54/dpi, decor_e*2.54/dpi))
print('FAIL = 黑色花纹被替换成米色（内容色）→ 内层弧形缺口 bug')

centers = {'TL':(R2,R2), 'TR':(w2-R2,R2), 'BL':(R2,h2-R2), 'BR':(w2-R2,h2-R2)}
total_jinsu_problems = 0
for name, (cx, cy) in centers.items():
    p_in_corner = 0
    # 沿着扇形区内，装饰带深度范围的花纹像素扫描
    for ang_deg in np.arange(15, 76, 5):
        a = np.radians(ang_deg)
        for f in np.arange(0.05, 0.95, 0.02):
            px = int(round(cx - R2 * f * np.cos(a)))
            py = int(round(cy - R2 * f * np.sin(a)))
            if not (0 <= py < h2 and 0 <= px < w2):
                continue
            dist_v = np.sqrt((px-cx)**2 + (py-cy)**2)
            if dist_v > R2:
                continue
            depth = R2 - dist_v
            # 只在装饰带深度
            if not (decor_s <= depth <= decor_e):
                continue
            s = j_arr[py, px]
            r = rj_arr[py, px]
            # 黑花纹 → 米色
            src_black = np.mean(s) < 80
            res_beige = np.sqrt(np.sum((r.astype(float) - content_ref)**2)) < 25
            if src_black and res_beige and not np.array_equal(s, r):
                p_in_corner += 1
                total_jinsu_problems += 1
                if total_jinsu_problems <= 5:
                    print('  %s FAIL: ang=%.0f d=%.1fcm | src=黑(%d,%d,%d) → res=米色(%d,%d,%d)' %
                          (name, ang_deg, depth*2.54/dpi, s[0],s[1],s[2], r[0],r[1],r[2]))
    print('  %s 角装饰带: %d 个弧形缺口像素' % (name, p_in_corner))
print('堇色素颜四角：%d 个弧形缺口像素' % total_jinsu_problems)

# ==============================
# 汇总
# ==============================
print('\n' + '=' * 60)
huaman_ok = (p_bl + p_br == 0)
jinsu_ok = (total_jinsu_problems == 0)

if huaman_ok and jinsu_ok:
    print('✅ 全部测试通过：')
    print('   花幔模式（边框 C 形缺口）：0 个问题')
    print('   堇色素颜（内层底色弧形缺口）：0 个问题')
else:
    print('❌ 测试失败：')
    if not huaman_ok:
        print('   ❌ 花幔仍有 %d 个边框色异常像素' % (p_bl + p_br))
    if not jinsu_ok:
        print('   ❌ 堇色素颜仍有 %d 个底色弧形缺口' % total_jinsu_problems)
print('=' * 60)
