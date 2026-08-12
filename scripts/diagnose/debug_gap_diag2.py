import sys
sys.path.insert(0, '.')
import numpy as np
from core.config import DEFAULT_DPI

dpi = DEFAULT_DPI
w = int(round(80 * dpi / 2.54))
h = int(round(140 * dpi / 2.54))
r_8cm = int(round(8 * dpi / 2.54))

gap_thick = int(round(0.3 * dpi / 2.54))
decor_start = int(round(1.0 * dpi / 2.54))   # 装饰带起点（约1cm深度）
decor_end = int(round(7.5 * dpi / 2.54))     # 装饰带终点（约7.5cm深度）

print('=== 间隙层对角内区覆盖范围验证 ===')
print('间隙层厚度: %dpx (%.2fcm)' % (gap_thick, gap_thick*2.54/dpi))
print('圆角半径 R: %dpx (%.2fcm)' % (r_8cm, r_8cm*2.54/dpi))
print('装饰带深度范围: %d~%dpx (%.2f~%.2fcm)' % 
      (decor_start, decor_end, decor_start*2.54/dpi, decor_end*2.54/dpi))
print()

# TR角：圆心 (cx, cy) = (w-R, R)
R_total = r_8cm
cx = w - R_total
cy = R_total

# 生成整个扇形的坐标（只考虑圆角内部）
y_idx, x_idx = np.indices((h, w))
dx = x_idx.astype(np.float64) - float(cx)
dy = y_idx.astype(np.float64) - float(cy)
dist = np.sqrt(dx*dx + dy*dy)
in_sector = dist <= float(R_total)

# 间隙层的 in_extension（TR角）
cumulative_depths = [0, gap_thick]
x_left = w - cumulative_depths[1]   # w - 18
x_right = w - cumulative_depths[0]  # w - 0
y_top = cumulative_depths[0]        # 0
y_bottom = cumulative_depths[1]     # 18

in_right_strip = (x_idx >= x_left) & (x_idx < x_right)
in_top_strip = (y_idx >= y_top) & (y_idx < y_bottom)
in_extension = in_right_strip | in_top_strip
diagonal_interior = in_sector & (~in_extension)

# 装饰带深度区间：depth = R - dist ∈ [decor_start, decor_end]
pixel_depth = float(R_total) - dist
in_decor_depth = (pixel_depth >= decor_start) & (pixel_depth <= decor_end)
in_decor_and_di = diagonal_interior & in_decor_depth

print('TR角扇形总像素数: %d' % np.sum(in_sector))
print('间隙层 diagonal_interior 像素数: %d (占扇形 %.1f%%)' % 
      (np.sum(diagonal_interior), 100.0*np.sum(diagonal_interior)/max(1,np.sum(in_sector))))
print('装饰带深度区间 + diagonal_interior 像素数: %d' % np.sum(in_decor_and_di))
print()

if np.sum(in_decor_and_di) > 0:
    print('✅ 假设验证成立！')
    print('   间隙层 diagonal_interior 覆盖了装饰带区域的 %d 个像素' % np.sum(in_decor_and_di))
    print('   → 这些装饰带白底黑花纹的像素会被强制填成米色（内容色）')
    print('   → 形成了用户看到的"内层底色弧形缺口"')
else:
    print('假设不成立，需要继续分析')

# 详细分析 45° 对角线上的覆盖
print()
print('=== 45°对角线上的 diagonal_interior 覆盖情况 ===')
print('%8s  %8s  %10s  %12s  %s' % ('depth(px)', 'depth(cm)', 'in_sector', 'in_DI', '区域'))
for factor in np.arange(0.02, 0.98, 0.04):
    px = int(round(cx - r_8cm * factor * np.cos(np.radians(45))))
    py = int(round(cy - r_8cm * factor * np.sin(np.radians(45))))
    dist_val = np.sqrt((px-cx)**2 + (py-cy)**2)
    depth_val = r_8cm - dist_val
    d_cm = depth_val * 2.54 / dpi
    
    if 0 <= py < h and 0 <= px < w:
        in_sec = bool(in_sector[py, px])
        in_di = bool(diagonal_interior[py, px])
        
        if depth_val <= gap_thick:
            zone = '间隙层'
        elif decor_start <= depth_val <= decor_end:
            zone = '装饰带 ⚠️'
        elif depth_val > total_border_depth if 'total_border_depth' in dir() else depth_val > gap_thick:
            zone = '内容区'
        else:
            zone = '其他'
        
        print('%8.1f  %8.2f  %10s  %12s  %s' % 
              (depth_val, d_cm, str(in_sec), str(in_di), zone))
