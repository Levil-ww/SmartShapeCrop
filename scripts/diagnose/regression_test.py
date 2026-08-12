import sys
sys.path.insert(0, '.')
from PIL import Image, ImageDraw
import numpy as np
from core.config import DEFAULT_DPI
from core.image_cropper import apply_rounded_corners

dpi = DEFAULT_DPI

# ==============================
# 回归测试 1: 黑-米-黑三层边框（花幔模式）
# 验证间隙层对角内区填补仍然有效，不会出现外层黑框 C 形缺口
# ==============================
def make_huaman_style():
    w = int(round(40 * dpi / 2.54))
    h = int(round(160 * dpi / 2.54))
    # 米色内容背景
    img = Image.new('RGB', (w, h), (248, 240, 220))
    draw = ImageDraw.Draw(img)

    # 外层黑 0.3cm
    outer = int(round(0.3 * dpi / 2.54))
    for i in range(outer):
        draw.rectangle([i, i, w-1-i, h-1-i], outline=(0, 0, 0))

    # 米色间隙 0.25cm（关键：与内容色极近但有细微差异）
    gap = int(round(0.25 * dpi / 2.54))
    img_arr_tmp = np.array(img)
    content_color = [248, 240, 220]
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

    # 内层黑 0.3cm
    inner = int(round(0.3 * dpi / 2.54))
    for i in range(outer + gap, outer + gap + inner):
        draw.rectangle([i, i, w-1-i, h-1-i], outline=(0, 0, 0))

    # 内容区添加装饰花纹（用于检查"花纹色漏入边框对角内区"= C形缺口）
    decor_start = outer + gap + inner + 2
    draw_obj = ImageDraw.Draw(img)
    for d in range(decor_start, min(w, h)//3, 30):
        for x in range(decor_start + 5, w - decor_start, 40):
            draw_obj.ellipse([x, d-3, x+6, d+3], fill=(180, 100, 80))
            draw_obj.ellipse([x, h-1-d-3, x+6, h-1-d+3], fill=(180, 100, 80))
    return img, w, h

def check_c_shape_gap(img_arr, result_arr, cx, cy, R, name, content_color, gap_color):
    """检查黑框 C 形缺口：对角内区边框深度位置是否出现花纹/内容色（本该是边框色或间隙色）"""
    total_border = int(round(0.85 * dpi / 2.54))  # 约 0.85cm 总边框厚
    content_ref = np.array(content_color, dtype=np.float64)
    gap_ref = np.array(gap_color, dtype=np.float64)
    black_ref = np.array([0, 0, 0], dtype=np.float64)

    angles = [30.0, 45.0, 60.0]
    problems = 0
    checked = 0
    print('\n--- %s 角：外层黑框有无 C 形缺口检查 ---' % name)
    print('  %6s  %6s  %12s  %12s  %s' % ('depth', 'd_cm', 'src', 'result', 'status'))
    for ang_deg in angles:
        ang = np.radians(ang_deg)
        for factor in np.arange(0.5, 0.98, 0.03):
            px = int(round(cx - R * factor * np.cos(ang)))
            py = int(round(cy - R * factor * np.sin(ang)))
            if not (0 <= py < img_arr.shape[0] and 0 <= px < img_arr.shape[1]):
                continue
            dist_v = np.sqrt((px-cx)**2 + (py-cy)**2)
            depth_v = R - dist_v
            d_cm = depth_v * 2.54 / dpi

            if depth_v > total_border:
                continue  # 超出边框深度范围，不查

            src = img_arr[py, px].astype(np.float64)
            res = result_arr[py, px].astype(np.float64)
            checked += 1

            # 判断：在边框深度范围内，结果像素如果是花纹色/离边框色太远
            d_to_black = np.sqrt(np.sum((res - black_ref)**2))
            d_to_gap = np.sqrt(np.sum((res - gap_ref)**2))
            d_to_content = np.sqrt(np.sum((res - content_ref)**2))
            d_to_pattern = min(d_to_content,  # 如果内容色/花纹色更近 than 边框色组 → 问题
                               np.sqrt(np.sum((res - np.array([180,100,80]))**2)))
            min_border_group = min(d_to_black, d_to_gap)

            status = 'ok'
            if min_border_group > 40 and d_to_pattern < 40:
                status = 'FAIL ❌ C形缺口!'
                problems += 1
            elif not np.array_equal(src.astype(int), res.astype(int)):
                status = 'changed'

            print('  %6.1f  %6.2f  (%3d,%3d,%3d)  (%3d,%3d,%3d)  %s' % (
                depth_v, d_cm,
                int(src[0]), int(src[1]), int(src[2]),
                int(res[0]), int(res[1]), int(res[2]), status))

    print('  %s 角：%d 检查点，%d C形缺口问题' % (name, checked, problems))
    return problems

# ====== 测试 1 花幔模式 ======
print('=' * 60)
print('回归测试 1：花幔模式（黑-米-黑三层边框 + 左下右下 3.6cm 圆角）')
print('目标：验证间隙层深度限制不会导致黑框 C 形缺口回归')
print('=' * 60)
img, w, h = make_huaman_style()
R = int(round(3.6 * dpi / 2.54))
content_color = [248, 240, 220]
gap_color = [245, 237, 218]
corners = {'bl': R, 'br': R}
result = apply_rounded_corners(img, corners, dpi, (255,255,255))

img_arr = np.array(img)
res_arr = np.array(result)

p1 = check_c_shape_gap(img_arr, res_arr, R, h - R, R, 'BL', content_color, gap_color)
p2 = check_c_shape_gap(img_arr, res_arr, w - R, h - R, R, 'BR', content_color, gap_color)
print('\n花幔模式总计：%d 个 C 形缺口' % (p1 + p2))

# ====== 测试 2 中古大花模式（9cm 大四圆角） ======
print('\n' + '=' * 60)
print('回归测试 2：中古大花模式（多层边框 + 4角 9cm 圆角，大半径）')
print('目标：大半径下对角内区覆盖逻辑仍然稳定')
print('=' * 60)

w2 = int(round(34.5 * dpi / 2.54))
h2 = int(round(120 * dpi / 2.54))
img2 = Image.new('RGB', (w2, h2), (245, 230, 210))
draw2 = ImageDraw.Draw(img2)

layers = [
    ((10, 10, 10), int(round(0.3 * dpi / 2.54))),  # 深棕
    ((240, 225, 200), int(round(0.2 * dpi / 2.54))),  # 浅米间隙
    ((80, 40, 30), int(round(0.3 * dpi / 2.54))),  # 红棕
]
pos = 0
for color, thick in layers:
    for i in range(thick):
        p = pos + i
        draw2.rectangle([p, p, w2-1-p, h2-1-p], outline=color)
    pos += thick

# 装饰带花纹（深色密集，防止被误当边框）
decor_s = pos + 1
decor_e = int(round(5.0 * dpi / 2.54))
for d in range(decor_s, decor_e, 18):
    for x in range(decor_e, w2-decor_e, 22):
        draw2.rectangle([x, d-2, x+4, d+2], fill=(60, 30, 20))
        draw2.rectangle([x, h2-1-d-2, x+4, h2-1-d+2], fill=(60, 30, 20))
    for y in range(decor_e, h2-decor_e, 22):
        draw2.rectangle([d-2, y, d+2, y+4], fill=(60, 30, 20))
        draw2.rectangle([w2-1-d-2, y, w2-1-d+2, y+4], fill=(60, 30, 20))

R2 = int(round(9 * dpi / 2.54))
corners2 = {'tl': R2, 'tr': R2, 'bl': R2, 'br': R2}
result2 = apply_rounded_corners(img2, corners2, dpi, (255,255,255))

# 扫描：装饰带深度区域不应被边框色覆盖（花纹应保留）
img_arr2 = np.array(img2)
res_arr2 = np.array(result2)
decor_overwrite = 0
total_decor_check = 0
for name, (cx, cy) in [('TL',(R2,R2)), ('TR',(w2-R2,R2)), ('BL',(R2,h2-R2)), ('BR',(w2-R2,h2-R2))]:
    for ang in [25, 45, 65]:
        a = np.radians(ang)
        for f in np.arange(0.2, 0.9, 0.04):
            px = int(round(cx - R2 * f * np.cos(a)))
            py = int(round(cy - R2 * f * np.sin(a)))
            if not (0 <= py < h2 and 0 <= px < w2):
                continue
            dv = R2 - np.sqrt((px-cx)**2 + (py-cy)**2)
            if decor_s <= dv <= decor_e:
                s = img_arr2[py, px].astype(int)
                r = res_arr2[py, px].astype(int)
                total_decor_check += 1
                # 如果是小方块花纹 (60,30,20) 被替换成了米色内容色，就是弧形缺口
                d_s_pat = np.sqrt(np.sum((s - [60,30,20])**2))
                d_r_cont = np.sqrt(np.sum((r - np.array([245,230,210]))**2))
                if d_s_pat < 30 and d_r_cont < 30 and not np.array_equal(s,r):
                    decor_overwrite += 1
                    if decor_overwrite <= 5:
                        print('  %s角 深花纹被填米色: d=%.1f src=(%d,%d,%d) res=(%d,%d,%d)' %
                              (name, dv*2.54/dpi, s[0],s[1],s[2], r[0],r[1],r[2]))

print('\n中古大花：装饰带 %d 个检查点，花纹被底色覆盖 %d 个' % (total_decor_check, decor_overwrite))

# ====== 汇总 ======
print('\n' + '=' * 60)
all_ok = (p1 + p2 == 0) and (decor_overwrite == 0)
if all_ok:
    print('✅ 全部回归测试通过：')
    print('   - 花幔模式 C 形缺口：0 个')
    print('   - 中古大花模式 花纹底色弧形缺口：0 个')
else:
    print('❌ 回归测试发现问题，请检查！')
print('=' * 60)
