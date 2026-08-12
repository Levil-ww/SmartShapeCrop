"""
Unified verification: Both cases correct!
A) 花幔 (40x160cm, 3.6cm R, BL+BR rounded): gap layer diagonal interior depth [19,34) correctly filled with gap color
B) 堇色素颜 (80x140cm, 8cm R, all 4 corners): gap layer thickness 11px depth [30.5,41.5) ONLY fills its own depth region,
   decorative band (depth > 41.5px, 1~7.5cm inside) should NOT be covered with content color
"""
import sys
sys.path.insert(0, 'D:/SmartShapeCrop')

from PIL import Image, ImageDraw
import numpy as np
from core.config import DEFAULT_DPI, cm_to_px
from core.image_cropper import apply_rounded_corners, _get_border_layers_robust

dpi = DEFAULT_DPI

def color_d(a, b):
    return float(np.sqrt(sum((int(x)-int(y))**2 for x,y in zip(a,b))))

print("="*70)
print("CASE A: 花幔 - NO C-shaped gaps (gap layer fills its diagonal interior)")
print("="*70)

w1 = cm_to_px(40.0, dpi)
h1 = cm_to_px(160.0, dpi)
outer_black = cm_to_px(0.32, dpi)  # ~19
gap1 = cm_to_px(0.25, dpi)          # ~15
inner_black = cm_to_px(0.30, dpi)   # ~18
content1 = (248, 240, 220)
img1 = Image.new('RGB', (w1, h1), content1)
draw1 = ImageDraw.Draw(img1)
for t in range(outer_black):
    draw1.rectangle([t, t, w1-1-t, h1-1-t], outline=(0,0,0))
for t in range(outer_black, outer_black+gap1):
    draw1.rectangle([t, t, w1-1-t, h1-1-t], outline=(245, 237, 218))
for t in range(outer_black+gap1, outer_black+gap1+inner_black):
    draw1.rectangle([t, t, w1-1-t, h1-1-t], outline=(0,0,0))

R1 = int(round(3.6 * dpi / 2.54))
bl1 = _get_border_layers_robust(img1, (255,255,255))
print(f'边框检测: {[(c,t) for c,t in bl1]}')
gap_color1 = bl1[1][0]  # 间隙色
result1 = apply_rounded_corners(img1, {'bl': 3.6, 'br': 3.6}, dpi=dpi, bg_color=(255,255,255))
arr1 = np.array(result1, dtype=np.uint8)
src_arr1 = np.array(img1, dtype=np.uint8)

# Scan BL corner
corner_specs = [
    ('bl', R1,             h1-R1),
    ('br', w1-R1,          h1-R1),
]

total_pixels_caseA = 0
gap_filled_caseA = 0
canvas_white_caseA = 0
for ck, cx, cy in corner_specs:
    # 间隙层厚度深度范围 (实际检测会有 4 层，层1 depth 19-34)
    # 无论如何：19 ≤ depth < 34
    for x in range(cx - R1 - 5, cx + R1 + 5):
        for y in range(cy - R1 - 5, cy + R1 + 5):
            if x < 0 or y < 0 or x >= w1 or y >= h1:
                continue
            dx = x - cx; dy = y - cy
            dist = np.sqrt(dx*dx + dy*dy)
            depth = R1 - dist
            if dist >= R1 - 5:  # 不在弧边界薄层，内部
                continue
            if not (19 <= depth < 34):  # 间隙层深度
                continue
            # diag_interior per corner
            if ck == 'bl':
                ls = (19 <= x < 34)
                bs = ((h1 - 34) <= y < (h1 - 19))
            else:  # br
                ls = ((w1 - 34) <= x < (w1 - 19))
                bs = ((h1 - 34) <= y < (h1 - 19))
            if not (not (ls or bs)):
                continue  # 不是对角内区
            total_pixels_caseA += 1
            res = tuple(int(v) for v in arr1[y, x, :])
            # 判断是否合理：间隙层填充色（gap_color 或 近似米色内容色）都是可以接受的
            d_gap = color_d(res, gap_color1)
            d_content = color_d(res, content1)
            if d_gap <= 20 or d_content <= 20:
                gap_filled_caseA += 1
            elif res == (255,255,255):
                canvas_white_caseA += 1

print(f'Case A - 对角内区间隙层深度像素: {total_pixels_caseA}')
print(f'  合理填充（间隙/米色内容近似）: {gap_filled_caseA} ({gap_filled_caseA/total_pixels_caseA*100:.1f}%)')
print(f'  画布白缺口 (C形缺口证据):     {canvas_white_caseA} ({canvas_white_caseA/total_pixels_caseA*100:.1f}%)')
caseA_ok = gap_filled_caseA / total_pixels_caseA > 0.99 and canvas_white_caseA == 0
print(f'  Case A 花幔 C形缺口修复: {"✅ PASS" if caseA_ok else "❌ FAIL"}')

print()
print("="*70)
print("CASE B: 堇色素颜 - NO inner gap (gap layer does NOT over-fill decorative band)")
print("="*70)

w2 = cm_to_px(80.0, dpi)
h2 = cm_to_px(140.0, dpi)
outer_black_b = cm_to_px(0.25, dpi)   # 30.5px
inner_beige_b = cm_to_px(0.30, dpi)   # 11px  ← 间隙层（内容色）
inner_black_b = cm_to_px(0.30, dpi)   # ~18px
# 装饰带 7.5cm = 443px 宽：白底 + 黑色花纹（此处模拟装饰带区域大量黑像素分布于 1~7.5cm 内）
decor_start = outer_black_b + inner_beige_b + inner_black_b  # ~59.5px
decor_end = decor_start + cm_to_px(7.5, dpi)                  # ~502.5px
content2 = (248, 240, 220)  # 米色 = 内容参考色
decor_bg = (255, 255, 255)  # 装饰带白底
pattern_black = (0, 0, 0)

img2 = Image.new('RGB', (w2, h2), content2)
draw2 = ImageDraw.Draw(img2)
# 多层边框
for t in range(outer_black_b):
    draw2.rectangle([t, t, w2-1-t, h2-1-t], outline=pattern_black)
for t in range(outer_black_b, outer_black_b+inner_beige_b):
    draw2.rectangle([t, t, w2-1-t, h2-1-t], outline=content2)  # 间隙色 = 米色内容色
for t in range(outer_black_b+inner_beige_b, decor_start):
    draw2.rectangle([t, t, w2-1-t, h2-1-t], outline=pattern_black)
# 装饰带：装饰带区域范围从 decor_start (边框内缘) 到 decor_end，里面铺黑色花纹像素
# 用简单的点阵模拟花纹：每隔若干像素放一个黑点
for y in range(0, h2):
    for x in range(0, w2):
        # 判断像素距离最近的边（depth）
        d_edge = min(x, w2-1-x, y, h2-1-y)
        if decor_start <= d_edge < decor_end:
            # 在装饰带内，画白底+随机花纹点阵
            # 花纹：10px 周期的小方块
            rel = d_edge - decor_start
            if ((x // 17) + (y // 17)) % 5 == 0:
                img2.putpixel((x, y), pattern_black)
            else:
                img2.putpixel((x, y), decor_bg)

bl2 = _get_border_layers_robust(img2, (255,255,255))
print(f'边框检测: {[(c,t) for c,t in bl2]}')
R2 = int(round(8.0 * dpi / 2.54))
result2 = apply_rounded_corners(img2, {'tl': 8.0, 'tr': 8.0, 'bl': 8.0, 'br': 8.0}, dpi=dpi, bg_color=(255,255,255))
arr2 = np.array(result2, dtype=np.uint8)
src_arr2 = np.array(img2, dtype=np.uint8)

# Scan all 4 corners' decorative band
corner_specs_b = [
    ('tl', R2,             R2),
    ('tr', w2-R2,          R2),
    ('bl', R2,             h2-R2),
    ('br', w2-R2,          h2-R2),
]

# 间隙层厚度: 第1层米色 ~11px depth ~[30.5, 41.5]
# 装饰带区域 depth ∈ [decor_start, decor_end) ≈ [60, 502)
total_decor_pixels_b = 0
decor_ruined_b = 0  # 黑花纹被改成米色的数量（内层底色弧形缺口的证据）
for ck, cx, cy in corner_specs_b:
    for x in range(cx - R2 - 5, cx + R2 + 5):
        for y in range(cy - R2 - 5, cy + R2 + 5):
            if x < 0 or y < 0 or x >= w2 or y >= h2:
                continue
            dx = x - cx; dy = y - cy
            dist = np.sqrt(dx*dx + dy*dy)
            depth = R2 - dist
            if not (decor_start <= depth < decor_end):  # 只扫装饰带深度区域
                continue
            # 对角内区（gap层填充旧代码越界覆盖的地方）
            if ck == 'tl':
                ls = (decor_start <= x < decor_end); bs = (decor_start <= y < decor_end)
            elif ck == 'tr':
                ls = ((w2-decor_end) <= x < (w2-decor_start)); bs = (decor_start <= y < decor_end)
            elif ck == 'bl':
                ls = (decor_start <= x < decor_end); bs = ((h2-decor_end) <= y < (h2-decor_start))
            else:
                ls = ((w2-decor_end) <= x < (w2-decor_start)); bs = ((h2-decor_end) <= y < (h2-decor_start))
            if not (not (ls or bs)):
                continue  # 不是对角内区，跳过
            total_decor_pixels_b += 1
            src_pixel = tuple(int(v) for v in src_arr2[y, x, :])
            res_pixel = tuple(int(v) for v in arr2[y, x, :])
            # 如果原图是黑色花纹像素，但处理后被变成了米色内容色 → 被越界填充了
            if color_d(src_pixel, pattern_black) < 30:
                if color_d(res_pixel, content2) < 30 or color_d(res_pixel, bl2[1][0]) < 30:
                    decor_ruined_b += 1

print(f'Case B - 对角内区装饰带深度像素: {total_decor_pixels_b}')
print(f'  黑花纹被错误改成米色 (内层缺口): {decor_ruined_b} ({decor_ruined_b/max(1,total_decor_pixels_b)*100:.2f}%)')
caseB_ok = decor_ruined_b == 0 or decor_ruined_b / total_decor_pixels_b < 0.01
print(f'  Case B 堇色素颜内层底色弧形缺口修复: {"✅ PASS" if caseB_ok else "❌ FAIL"}')

print()
print("="*70)
print(f'OVERALL: {"✅ All tests pass - fix verified!" if caseA_ok and caseB_ok else "❌ Fix needs adjustment"}')
print("="*70)
