"""
诊断脚本：追踪圆角处理流程中的像素状态转换
检查每个阶段的像素变化
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
from PIL import Image
from core.image_cropper import apply_border_only_corners
from core.corner.algorithm import CORNER_ANGLES


def diagnose_moshanghuakai():
    """诊断墨上花开案例的图案保留问题"""
    print("=" * 70)
    print("诊断 墨上花开: 追踪像素状态转换")
    print("=" * 70)

    # 创建与测试相同的模拟图片
    w, h = 2835, 5670
    bg_color = (255, 255, 255)

    img = Image.new('RGB', (w, h), bg_color)
    arr = np.array(img)

    # 外边框 (黑色)
    border_color = (25, 20, 18)
    arr[:45, :, :] = border_color
    arr[-45:, :, :] = border_color
    arr[:, :45, :] = border_color
    arr[:, -45:, :] = border_color

    # 米色间隙
    gap_color = (235, 228, 215)
    arr[45:65, :, :] = gap_color
    arr[-65:-45, :, :] = gap_color
    arr[:, 45:65, :] = gap_color
    arr[:, -65:-45, :] = gap_color

    # 内边框
    arr[65:90, :, :] = (30, 25, 22)
    arr[-90:-65, :, :] = (30, 25, 22)
    arr[:, 65:90, :] = (30, 25, 22)
    arr[:, -90:-65, :] = (30, 25, 22)

    # 内容区域 - 添加花纹图案
    pattern_color = (180, 150, 100)
    for i in range(90, h - 90, 20):
        for j in range(90, w - 90, 20):
            arr[i, j, :] = pattern_color

    img = Image.fromarray(arr)

    dpi = 72
    r_cm = 10.0
    r_px = int(r_cm * dpi / 2.54)
    corners = {'tl': r_cm}  # 只测试一个角

    print(f"  圆角半径: {r_cm}cm = {r_px}px")
    print(f"  图像尺寸: {w}x{h}")

    # 分析原图的内容分布
    print("\n  --- 原图内容分析 (tl角) ---")
    yy, xx = np.mgrid[0:h, 0:w]
    dx = xx.astype(np.float64) - r_px
    dy = yy.astype(np.float64) - r_px
    dist = np.sqrt(dx**2 + dy**2)
    angle = np.degrees(np.arctan2(dy, dx))
    angle = np.mod(angle, 360.0)

    ang_min, ang_max = CORNER_ANGLES['tl']
    valid_angle = (angle >= ang_min) & (angle <= ang_max)

    # 弧内侧区域
    inside_arc = valid_angle & (dist <= r_px)
    # 弧边界区域 (dist ≈ r_px)
    boundary_zone = valid_angle & (dist >= r_px - 2) & (dist <= r_px + 2)

    inside_pixels = arr[inside_arc]
    pattern_mask = np.all(inside_pixels == pattern_color, axis=1)
    white_mask = np.all(inside_pixels > 250, axis=1)
    print(f"  弧内侧总像素数: {len(inside_pixels)}")
    print(f"  图案像素数: {np.sum(pattern_mask)} ({100*np.sum(pattern_mask)/len(inside_pixels):.2f}%)")
    print(f"  白色像素数: {np.sum(white_mask)} ({100*np.sum(white_mask)/len(inside_pixels):.2f}%)")

    # 处理图像
    result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)
    result_arr = np.array(result)

    print("\n  --- 处理后内容分析 (tl角) ---")
    inside_pixels_after = result_arr[inside_arc]
    pattern_mask_after = np.all(inside_pixels_after == pattern_color, axis=1)
    white_mask_after = np.all(inside_pixels_after > 250, axis=1)
    black_mask_after = np.all(inside_pixels_after < 50, axis=1)
    print(f"  弧内侧总像素数: {len(inside_pixels_after)}")
    print(f"  图案像素数: {np.sum(pattern_mask_after)} ({100*np.sum(pattern_mask_after)/len(inside_pixels_after):.2f}%)")
    print(f"  白色像素数: {np.sum(white_mask_after)} ({100*np.sum(white_mask_after)/len(inside_pixels_after):.2f}%)")
    print(f"  黑色像素数: {np.sum(black_mask_after)} ({100*np.sum(black_mask_after)/len(inside_pixels_after):.2f}%)")

    # 检查边界区域
    boundary_before = arr[boundary_zone]
    boundary_after = result_arr[boundary_zone]
    boundary_white_before = np.sum(np.all(boundary_before > 250, axis=1))
    boundary_white_after = np.sum(np.all(boundary_after > 250, axis=1))
    boundary_black_before = np.sum(np.all(boundary_before < 50, axis=1))
    boundary_black_after = np.sum(np.all(boundary_after < 50, axis=1))
    print(f"\n  --- 弧边界区域 (dist≈r_px) ---")
    print(f"  原图 白色像素: {boundary_white_before}, 黑色像素: {boundary_black_before}")
    print(f"  结果 白色像素: {boundary_white_after}, 黑色像素: {boundary_black_after}")

    # 差异分析
    lost_pattern = np.sum(pattern_mask & ~pattern_mask_after)
    gained_pattern = np.sum(~pattern_mask & pattern_mask_after)
    print(f"\n  --- 图案变化 ---")
    print(f"  丢失的图案像素: {lost_pattern}")
    print(f"  新增的图案像素: {gained_pattern}")

    if lost_pattern > 0:
        # 找出丢失的图案像素位置
        lost_indices = np.where(pattern_mask & ~pattern_mask_after)[0]
        if len(lost_indices) > 0:
            print(f"  丢失像素位置 (前5个):")
            coords = np.where(inside_arc)
            for idx in lost_indices[:5]:
                y, x = coords[0][idx], coords[1][idx]
                orig_val = arr[y, x]
                new_val = result_arr[y, x]
                print(f"    ({y},{x}): 原图={orig_val}, 结果={new_val}, dist={dist[y,x]:.1f}")

    return True


def diagnose_huayangzhiyue():
    """诊断花漾之约案例的白色扇形角问题"""
    print("\n" + "=" * 70)
    print("诊断 花漾之约: 白色扇形角根因分析")
    print("=" * 70)

    w, h = 1134, 1701
    bg_color = (255, 255, 255)

    img = Image.new('RGB', (w, h), bg_color)
    arr = np.array(img)

    # 黑色外边框
    arr[:30, :, :] = (15, 15, 15)
    arr[-30:, :, :] = (15, 15, 15)
    arr[:, :30, :] = (15, 15, 15)
    arr[:, -30:, :] = (15, 15, 15)

    # 点状线间隙
    for y in range(35, h - 35, 8):
        for x in range(35, w - 35, 8):
            if (x - y) % 16 == 0:
                arr[y, x, :] = (255, 255, 255)

    # 内容区域
    arr[50:-50, 50:-50] = (250, 248, 245)

    img = Image.fromarray(arr)

    dpi = 72
    r_cm = 3.0
    r_px = int(r_cm * dpi / 2.54)

    result = apply_border_only_corners(img, {'tl': r_cm}, dpi=dpi, bg_color=bg_color)
    result_arr = np.array(result)

    # 分析 tl corner 边界
    ang_min, ang_max = CORNER_ANGLES['tl']
    yy, xx = np.mgrid[0:h, 0:w]
    dx = xx.astype(np.float64) - r_px
    dy = yy.astype(np.float64) - r_px
    dist = np.sqrt(dx**2 + dy**2)
    angle = np.degrees(np.arctan2(dy, dx))
    angle = np.mod(angle, 360.0)

    valid_angle = (angle >= ang_min) & (angle <= ang_max)

    # 检查角度边界附近的白色像素
    for offset in [0, 1, 2, 3, 5, 10]:
        boundary_mask = valid_angle & (angle >= ang_max - offset) & (angle <= ang_max + offset)
        if np.any(boundary_mask):
            boundary_pixels = result_arr[boundary_mask]
            white_count = np.sum(np.all(boundary_pixels > 250, axis=1))
            total_count = len(boundary_pixels)
            print(f"  角度范围 [{ang_max-offset:.0f}°, {ang_max+offset:.0f}°]: 白色={white_count}/{total_count}")

    # 检查弧边界 (dist ≈ r_px)
    for band in [(r_px-1, r_px+1), (r_px-2, r_px+2), (r_px-5, r_px+5)]:
        arc_mask = valid_angle & (dist >= band[0]) & (dist <= band[1])
        if np.any(arc_mask):
            arc_pixels = result_arr[arc_mask]
            white_count = np.sum(np.all(arc_pixels > 250, axis=1))
            black_count = np.sum(np.all(arc_pixels < 50, axis=1))
            total_count = len(arc_pixels)
            print(f"  弧带 dist∈[{band[0]},{band[1]}]: 白色={white_count}, 黑色={black_count}, 总={total_count}")

    return True


def diagnose_wanshenghua():
    """诊断蔓生花案例的边框过细问题"""
    print("\n" + "=" * 70)
    print("诊断 蔓生花: 边框厚度分析")
    print("=" * 70)

    w, h = 2551, 4350
    bg_color = (255, 255, 255)

    img = Image.new('RGB', (w, h), bg_color)
    arr = np.array(img)

    border_w = 3
    arr[:border_w, :, :] = (20, 20, 20)
    arr[-border_w:, :, :] = (20, 20, 20)
    arr[:, :border_w, :] = (20, 20, 20)
    arr[:, -border_w:, :] = (20, 20, 20)
    arr[border_w:-border_w, border_w:-border_w] = (250, 245, 240)

    img = Image.fromarray(img)

    dpi = 72
    r_cm = 4.0
    r_px = int(r_cm * dpi / 2.54)

    result = apply_border_only_corners(img, {'bl': r_cm}, dpi=dpi, bg_color=bg_color)
    result_arr = np.array(result)

    # 比较直边和弧的边框密度
    ang_min, ang_max = CORNER_ANGLES['bl']
    yy, xx = np.mgrid[0:h, 0:w]
    dx = xx.astype(np.float64) - r_px
    dy = yy.astype(np.float64) - (h - r_px)
    dist = np.sqrt(dx**2 + dy**2)
    angle = np.degrees(np.arctan2(dy, dx))
    angle = np.mod(angle, 360.0)

    valid_angle = (angle >= ang_min) & (angle <= ang_max)

    # 直边框密度 (前10行)
    straight_region = result_arr[:10, :, :]
    straight_dark = np.sum(np.all(straight_region < 50, axis=2))
    straight_total = 10 * w
    print(f"  直边框: 深色像素={straight_dark}/{straight_total} ({100*straight_dark/straight_total:.2f}%)")

    # 弧边框密度
    arc_mask = valid_angle & (dist >= r_px - 5) & (dist <= r_px + 2)
    if np.any(arc_mask):
        arc_pixels = result_arr[arc_mask]
        arc_dark = np.sum(np.all(arc_pixels < 50, axis=1))
        arc_total = len(arc_pixels)
        print(f"  弧边框: 深色像素={arc_dark}/{arc_total} ({100*arc_dark/arc_total:.2f}%)")

    # 检查不同深度的弧边框密度
    for depth_range in [(0, 1), (0, 3), (0, 5), (0, 10)]:
        d_min, d_max = depth_range
        band_mask = valid_angle & (dist >= r_px - d_max) & (dist <= r_px - d_min)
        if np.any(band_mask):
            band_pixels = result_arr[band_mask]
            band_dark = np.sum(np.all(band_pixels < 50, axis=1))
            band_total = len(band_pixels)
            print(f"  深度 [{d_min},{d_max}]: 深色={band_dark}/{band_total} ({100*band_dark/band_total:.2f}%)")

    return True


def main():
    print("=" * 70)
    print("圆角处理根因诊断")
    print("=" * 70)

    try:
        diagnose_moshanghuakai()
    except Exception as e:
        print(f"  墨上花开诊断异常: {e}")
        import traceback
        traceback.print_exc()

    try:
        diagnose_huayangzhiyue()
    except Exception as e:
        print(f"  花漾之约诊断异常: {e}")
        import traceback
        traceback.print_exc()

    try:
        diagnose_wanshenghua()
    except Exception as e:
        print(f"  蔓生花诊断异常: {e}")
        import traceback
        traceback.print_exc()

    return 0


if __name__ == '__main__':
    sys.exit(main())
