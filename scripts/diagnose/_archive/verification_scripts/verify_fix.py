import sys
sys.path.insert(0, '.')
from PIL import Image, ImageDraw
import numpy as np
from core.config import DEFAULT_DPI
from core.image_cropper import _get_border_layers_robust, apply_rounded_corners

dpi = DEFAULT_DPI

def make_jinsu_test_image():
    """构造堇色素颜风格：外层黑细框 + 米色间隙 + 黑线 + 7.5cm装饰带 + 内层黑框 + 米色内容"""
    w = int(round(80 * dpi / 2.54))
    h = int(round(140 * dpi / 2.54))
    img = Image.new('RGB', (w, h), (250, 245, 230))
    draw = ImageDraw.Draw(img)

    black_1 = int(round(0.4 * dpi / 2.54))
    for i in range(black_1):
        draw.rectangle([i, i, w-1-i, h-1-i], outline=(0, 0, 0))

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

    black_2 = int(round(0.2 * dpi / 2.54))
    for i in range(black_1 + gap_1, black_1 + gap_1 + black_2):
        draw.rectangle([i, i, w-1-i, h-1-i], outline=(20, 20, 20))

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

    inner_frame = decor_end + int(round(0.3 * dpi / 2.54))
    inner_thick = int(round(0.3 * dpi / 2.54))
    for i in range(inner_thick):
        draw.rectangle([inner_frame+i, inner_frame+i, w-inner_frame-1-i, h-inner_frame-1-i], outline=(0,0,0))

    return img, w, h, decor_start, decor_end

def analyze_corner(img_arr, result_arr, cx, cy, R, content_ref, decor_start, decor_end, name):
    """分析单个圆角的装饰带区域"""
    print('\n========= %s 角分析 =========' % name)
    # 采样多个角度：45° + 22.5° + 67.5°
    angles = [22.5, 45.0, 67.5]
    total_problem = 0
    total_checked = 0

    for ang_deg in angles:
        ang = np.radians(ang_deg)
        problem_in_angle = 0
        checked_in_angle = 0
        print('\n  角度 %.1f°:' % ang_deg)
        print('  %6s  %8s  %12s  %12s  %s' % ('dist','depth(cm)','src','result','status'))

        for factor in np.arange(0.05, 0.95, 0.03):
            px = int(round(cx - R * factor * np.cos(ang)))
            py = int(round(cy - R * factor * np.sin(ang)))
            if not (0 <= py < img_arr.shape[0] and 0 <= px < img_arr.shape[1]):
                continue
            dist_v = np.sqrt((px-cx)**2 + (py-cy)**2)
            depth_v = R - dist_v
            d_cm = depth_v * 2.54 / dpi

            src = img_arr[py, px]
            res = result_arr[py, px]

            in_decor = decor_start <= depth_v <= decor_end
            src_black = np.mean(src) < 80
            src_white = np.mean(src) > 230 and np.std(src) < 20
            res_beige = np.sqrt(np.sum((res.astype(float) - content_ref.astype(float))**2)) < 25

            status = 'ok'
            tag = ''
            if in_decor and src_black and res_beige:
                status = 'FAIL ❌ 黑花纹→米色'
                problem_in_angle += 1
            elif in_decor and src_white and res_beige:
                status = 'WARN ⚠️  白底→米色'
                tag = '（装饰背景变色）'
                problem_in_angle += 1
            elif in_decor and not np.array_equal(src, res):
                status = 'changed'
                tag = '(%d,%d,%d)->(%d,%d,%d)' % (src[0],src[1],src[2],res[0],res[1],res[2])

            if in_decor:
                checked_in_angle += 1
                line = '  %6.1f  %8.2f  (%3d,%3d,%3d)  (%3d,%3d,%3d)  %s %s' % (
                    dist_v, d_cm,
                    src[0], src[1], src[2],
                    res[0], res[1], res[2], status, tag)
                print(line)

        total_problem += problem_in_angle
        total_checked += checked_in_angle
        print('  本角度装饰带: %d 个检查点，%d 个问题' % (checked_in_angle, problem_in_angle))

    print('\n  %s 角总计：%d 装饰带像素检查，%d 个问题' % (name, total_checked, total_problem))
    return total_problem

# ================= 主测试流程 =================
print('=' * 60)
print('堇色素颜案例修复验证：8cm 四个圆角')
print('=' * 60)

img, w, h, decor_start, decor_end = make_jinsu_test_image()
R = int(round(8 * dpi / 2.54))

print('图像尺寸: %dx%d px' % (w, h))
print('圆角半径: %dpx (%.2fcm)' % (R, R*2.54/dpi))
print('装饰带深度范围: %d~%dpx (%.2f~%.2fcm)' % (
    decor_start, decor_end,
    decor_start*2.54/dpi, decor_end*2.54/dpi))

# 边框检测
border_layers = _get_border_layers_robust(img, (255,255,255))
print('\n边框检测:')
total_px = 0
for i, (c, t) in enumerate(border_layers):
    print('  层%d: RGB(%d,%d,%d) %dpx (%.2fcm)' % (i, c[0],c[1],c[2], t, t*2.54/dpi))
    total_px += t
print('  总边框厚度: %dpx (%.2fcm)' % (total_px, total_px*2.54/dpi))

# 内容参考色
content_ref = np.median(np.array(img)[int(h*0.5):int(h*0.6), int(w*0.5):int(w*0.6), :].reshape(-1,3), axis=0)

# 处理圆角
corners = {'tl': R, 'tr': R, 'bl': R, 'br': R}
result = apply_rounded_corners(img, corners, dpi, (255,255,255))

img_arr = np.array(img)
result_arr = np.array(result)

# 四个角的圆心
centers = {
    'TL': (R, R),
    'TR': (w - R, R),
    'BL': (R, h - R),
    'BR': (w - R, h - R),
}

all_problems = 0
for name, (cx, cy) in centers.items():
    all_problems += analyze_corner(img_arr, result_arr, cx, cy, R, content_ref, decor_start, decor_end, name)

print('\n' + '=' * 60)
if all_problems == 0:
    print('✅ 修复验证通过！四个圆角装饰带区域均无底色弧形缺口。')
else:
    print('❌ 仍有 %d 个问题像素，修复不完全！' % all_problems)
print('=' * 60)
