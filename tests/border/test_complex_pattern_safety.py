"""
测试脚本：验证复杂花纹图案的圆角处理安全性

目的：
1. 验证修改后的代码是否会错误地将间隙填充为边框颜色
2. 验证复杂花纹图案的圆角处理是否正确
3. 验证多层边框的圆角处理是否正确
"""

import numpy as np
import math
from PIL import Image, ImageDraw
import sys
sys.path.insert(0, '.')

from core.image_cropper import apply_rounded_corners, apply_border_only_corners, _get_border_layers_robust

def test_complex_pattern_with_gaps():
    """
    测试包含间隙的复杂图案的圆角处理
    
    场景：
    - 外层黑色边框 (50px)
    - 白色间隙 (20px) 
    - 内层红色装饰边框 (30px)
    - 内部花纹（蓝色圆形和矩形）
    """
    print("=" * 60)
    print("测试 1: 包含间隙的复杂图案圆角处理")
    print("=" * 60)
    
    w, h = 800, 1000
    img = Image.new('RGB', (w, h), (255, 255, 255))
    arr = np.array(img)
    
    # 外层黑色边框 50px
    arr[0:50, :] = (0, 0, 0)
    arr[-50:, :] = (0, 0, 0)
    arr[:, 0:50] = (0, 0, 0)
    arr[:, -50:] = (0, 0, 0)
    
    # 浅灰色间隙 20px (50-70) - 使用与背景色不同的颜色
    arr[50:70, 50:750] = (200, 200, 200)  # 灰色间隙
    arr[-70:-50, 50:750] = (200, 200, 200)
    arr[50:950, 50:70] = (200, 200, 200)
    arr[50:950, -70:-50] = (200, 200, 200)
    
    # 内层红色装饰边框 30px (70-100)
    arr[70:100, 70:730] = (200, 0, 0)
    arr[-100:-70, 70:730] = (200, 0, 0)
    arr[70:930, 70:100] = (200, 0, 0)
    arr[70:930, -100:-70] = (200, 0, 0)
    
    # 内部花纹
    # 蓝色圆形
    for y in range(200, 400):
        for x in range(200, 400):
            if (x - 300)**2 + (y - 300)**2 <= 100**2:
                arr[y, x] = (0, 100, 200)
    
    # 绿色矩形
    arr[500:700, 200:400] = (0, 200, 100)
    
    # 黄色细节
    arr[350:450, 500:600] = (255, 200, 0)
    
    img = Image.fromarray(arr, 'RGB')
    bg_color = (255, 255, 255)
    
    # 调试: 检查边框层检测结果
    from core.image_cropper import _get_border_layers_robust
    border_layers = _get_border_layers_robust(img, bg_color)
    print(f"检测到的边框层数: {len(border_layers)}")
    for i, item in enumerate(border_layers):
        print(f"  层 {i}: {item}")
    
    # 执行圆角处理 (左下角 3cm)
    dpi = 150
    corners = {'tl': 0, 'tr': 0, 'bl': 3.0, 'br': 0}
    
    print(f"图像尺寸: {w}x{h}")
    print(f"圆角设置: {corners}")
    
    result = apply_rounded_corners(img, corners, dpi=dpi, bg_color=bg_color)
    result_arr = np.array(result)
    
    # 检查 1: 间隙区域是否保持为灰色（不是被填充为黑色）
    # 间隙区域：外边框(0-50)和内边框(70-100)之间
    # 检查左下角间隙区域
    gap_region_bottom_left = result_arr[950:1000, 50:70]  # 左下角间隙
    # 检查间隙是否被错误填充为黑色（边框颜色）
    is_not_black = (gap_region_bottom_left[:, :, 0] > 50).any() or \
                   (gap_region_bottom_left[:, :, 1] > 50).any()
    # 或者检查间隙是否保持为灰色
    is_gray = (gap_region_bottom_left[:, :, 0] < 220).all() and \
              (gap_region_bottom_left[:, :, 1] < 220).all()
    gap_is_correct = is_gray
    print(f"\n[检查 1] 左下角间隙区域是否保持为灰色: {gap_is_correct}")
    if not gap_is_correct:
        print("  警告: 间隙区域被错误填充！")
        # 检查被填充的颜色
        non_gray_mask = (gap_region_bottom_left[:, :, 0] > 220) | \
                        (gap_region_bottom_left[:, :, 1] > 220) | \
                        (gap_region_bottom_left[:, :, 2] > 220)
        if non_gray_mask.any():
            non_gray_pixels = gap_region_bottom_left[non_gray_mask]
            print(f"  非灰色像素数量: {len(non_gray_pixels)}")
            print(f"  非灰色像素示例颜色: {non_gray_pixels[0] if len(non_gray_pixels) > 0 else 'N/A'}")
    
    # 检查 2: 内部花纹是否完整
    # 检查蓝色圆形
    circle_region = result_arr[250:350, 250:350]
    blue_count = (circle_region[:, :, 2] > 150).sum()
    print(f"\n[检查 2] 内部花纹完整性:")
    print(f"  蓝色圆形像素数 (预期10000): {blue_count}")
    
    # 检查 3: 边框线是否平滑（检查左下角圆角弧线）
    # 在圆角弧线上检查是否有明显的缺口（即背景色的缺口）
    # 检查 1/4 圆区域内的边框连续性
    cx, cy = 50, 950  # 左下角圆角的圆心
    r_px = int(3.0 / 2.54 * 150)  # 3cm 转换为像素
    
    border_gaps_found = False
    gap_count = 0
    for angle_deg in range(0, 91, 5):
        angle_rad = math.radians(angle_deg)
        # 从圆心向外检查边框
        for dist_px in range(r_px - 40, r_px + 41):
            x = int(cx + dist_px * math.cos(angle_rad))
            y = int(cy - dist_px * math.sin(angle_rad))
            if 0 <= x < w and 0 <= y < h:
                pixel = result_arr[y, x]
                # 如果在边框深度范围内发现背景色，可能是缺口
                dist_from_edge = abs(dist_px - r_px)
                if dist_from_edge <= 5 and tuple(pixel) == (255, 255, 255):
                    if not border_gaps_found:
                        print(f"\n[检查 3] 警告: 在角度 {angle_deg}° 方向的边框区域发现白色像素")
                        border_gaps_found = True
                    gap_count += 1
    
    if not border_gaps_found:
        print(f"\n[检查 3] 边框连续性检查: 未发现明显缺口")
    else:
        print(f"[检查 3] 警告: 共发现 {gap_count} 个可能的缺口位置")
    
    assert (gap_is_correct and blue_count > 5000 and not border_gaps_found), (
        f"复杂花纹安全检查未通过: gap_is_correct={gap_is_correct}, "
        f"blue_count={blue_count}(需>5000), border_gaps_found={border_gaps_found}"
    )


def test_multilayer_border_with_mixed_colors():
    """
    测试多层边框（不同颜色）的圆角处理
    
    场景：
    - 外层蓝色边框 (40px)
    - 白色间隙 (15px)
    - 内层绿色边框 (25px)
    - 内部复杂花纹
    """
    print("\n" + "=" * 60)
    print("测试 2: 多层混合色边框的圆角处理")
    print("=" * 60)
    
    w, h = 600, 800
    img = Image.new('RGB', (w, h), (255, 255, 255))
    arr = np.array(img)
    
    # 外层蓝色边框 40px
    arr[0:40, :] = (0, 50, 200)
    arr[-40:, :] = (0, 50, 200)
    arr[:, 0:40] = (0, 50, 200)
    arr[:, -40:] = (0, 50, 200)
    
    # 白色间隙 15px (40-55)
    # 保持白色
    
    # 内层绿色边框 25px (55-80)
    arr[55:80, 55:545] = (0, 150, 80)
    arr[-80:-55, 55:545] = (0, 150, 80)
    arr[55:725, 55:80] = (0, 150, 80)
    arr[55:725, -80:-55] = (0, 150, 80)
    
    # 内部花纹 - 密集的小图案
    for y in range(150, 650, 20):
        for x in range(150, 550, 20):
            # 交替颜色的小方块
            if ((x // 20) + (y // 20)) % 2 == 0:
                arr[y:y+10, x:x+10] = (255, 100, 100)  # 红色
            else:
                arr[y:y+10, x:x+10] = (100, 100, 255)  # 蓝色
    
    img = Image.fromarray(arr, 'RGB')
    bg_color = (255, 255, 255)
    
    # 执行圆角处理 (四个角均为 2cm)
    dpi = 150
    corners = {'tl': 2.0, 'tr': 2.0, 'bl': 2.0, 'br': 2.0}
    
    print(f"图像尺寸: {w}x{h}")
    print(f"圆角设置: {corners}")
    
    result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)
    result_arr = np.array(result)
    
    # 检查 1: 间隙是否保持为白色
    gap_regions_ok = True
    for corner, slices in [
        ('tl', (40, 55, 0, 15)),
        ('tr', (40, 55, 545, 560)),
        ('bl', (745, 760, 0, 15)),
        ('br', (745, 760, 545, 560)),
    ]:
        y1, y2, x1, x2 = slices
        region = result_arr[y1:y2, x1:x2]
        all_white = (region[:, :, 0] > 250).all() and \
                   (region[:, :, 1] > 250).all() and \
                   (region[:, :, 2] > 250).all()
        print(f"[检查 1] 角 {corner} 间隙是否保持白色: {all_white}")
        if not all_white:
            gap_regions_ok = False
    
    # 检查 2: 内部花纹是否完整 (检查中央区域)
    center_region = result_arr[300:500, 200:400]
    red_count = (center_region[:, :, 0] > 200).sum()
    blue_count = (center_region[:, :, 2] > 200).sum()
    print(f"\n[检查 2] 内部花纹完整性:")
    print(f"  红色像素数: {red_count}")
    print(f"  蓝色像素数: {blue_count}")
    
    # 检查 3: 圆角弧线是否正确（不应有明显缺口）
    r_px = int(2.0 / 2.54 * 150)  # 2cm 转换为像素
    
    corners_with_issues = []
    for corner, (cx_offset, cy_offset) in [
        ('tl', (r_px, r_px)),
        ('tr', (w - r_px - 1, r_px)),
        ('bl', (r_px, h - r_px - 1)),
        ('br', (w - r_px - 1, h - r_px - 1)),
    ]:
        cx, cy = cx_offset, cy_offset
        gap_count = 0
        for angle_deg in range(0, 91, 10):
            angle_rad = math.radians(angle_deg)
            for dist_offset in range(-35, 36):
                dist_px = r_px + dist_offset
                if corner == 'tl':
                    x = int(cx - dist_px * math.cos(angle_rad))
                    y = int(cy - dist_px * math.sin(angle_rad))
                elif corner == 'tr':
                    x = int(cx + dist_px * math.cos(angle_rad))
                    y = int(cy - dist_px * math.sin(angle_rad))
                elif corner == 'bl':
                    x = int(cx - dist_px * math.cos(angle_rad))
                    y = int(cy + dist_px * math.sin(angle_rad))
                else:  # br
                    x = int(cx + dist_px * math.cos(angle_rad))
                    y = int(cy + dist_px * math.sin(angle_rad))
                
                if 0 <= x < w and 0 <= y < h:
                    pixel = result_arr[y, x]
                    if abs(dist_offset) <= 5 and tuple(pixel) == (255, 255, 255):
                        gap_count += 1
        
        if gap_count > 5:
            corners_with_issues.append((corner, gap_count))
    
    if corners_with_issues:
        print(f"\n[检查 3] 警告: 以下角落可能存在缺口:")
        for corner, count in corners_with_issues:
            print(f"  - {corner}: {count} 个异常白色像素")
    else:
        print(f"\n[检查 3] 所有角落边框连续性检查: 通过")
    
    assert (gap_regions_ok and len(corners_with_issues) == 0), (
        f"多层混合色边框检查未通过: gap_regions_ok={gap_regions_ok}, "
        f"存在问题的角落={corners_with_issues}"
    )


def test_extreme_colors_and_anti_aliasing():
    """
    测试极端颜色（非常接近的颜色）和抗锯齿效果
    
    场景：
    - 浅灰色边框 (RGB: 200,200,200)
    - 近白色间隙 (RGB: 240,240,240)
    - 内容色 (RGB: 100,100,100)
    """
    print("\n" + "=" * 60)
    print("测试 3: 极端颜色和抗锯齿效果")
    print("=" * 60)
    
    w, h = 500, 500
    img = Image.new('RGB', (w, h), (255, 255, 255))
    arr = np.array(img)
    
    # 浅灰色边框 30px
    arr[0:30, :] = (200, 200, 200)
    arr[-30:, :] = (200, 200, 200)
    arr[:, 0:30] = (200, 200, 200)
    arr[:, -30:] = (200, 200, 200)
    
    # 近白色间隙 10px
    # 保持背景色 (255, 255, 255)
    
    # 内部深色内容
    arr[60:440, 60:440] = (100, 100, 100)
    
    # 添加一些复杂花纹 - 渐变效果
    for y in range(100, 400):
        for x in range(100, 400):
            # 渐变花纹
            intensity = int(100 + 50 * math.sin(x / 20.0) * math.cos(y / 20.0))
            arr[y, x] = (intensity, intensity, intensity + 10)
    
    # 模拟抗锯齿边缘 - 在边框和内容交界处添加混合色
    for y in range(30, 35):
        for x in range(30, w - 30):
            arr[y, x] = (220, 220, 220)  # 边框和间隙的混合色
    for y in range(45, 50):
        for x in range(45, w - 45):
            arr[y, x] = (180, 180, 180)  # 间隙和内容的混合色
    
    for y in range(30, h - 30):
        for x in range(30, 35):
            arr[y, x] = (220, 220, 220)
    for y in range(45, h - 45):
        for x in range(45, 50):
            arr[y, x] = (180, 180, 180)
    
    img = Image.fromarray(arr, 'RGB')
    bg_color = (255, 255, 255)
    
    # 执行圆角处理 (右下角 2.5cm)
    dpi = 150
    corners = {'tl': 0, 'tr': 0, 'bl': 0, 'br': 2.5}
    
    print(f"图像尺寸: {w}x{h}")
    print(f"圆角设置: {corners}")
    print(f"边框颜色: RGB(200, 200, 200)")
    print(f"间隙颜色: RGB(255, 255, 255)")
    print(f"内容颜色: RGB(100, 100, 100)")
    
    result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)
    result_arr = np.array(result)
    
    # 检查 1: 间隙是否保持为白色
    # 检查右下角附近的间隙区域
    gap_region = result_arr[440:460, 440:460]  # 右下角间隙
    all_white = (gap_region[:, :, 0] > 245).all() and \
                (gap_region[:, :, 1] > 245).all() and \
                (gap_region[:, :, 2] > 245).all()
    print(f"\n[检查 1] 右下角间隙是否保持白色: {all_white}")
    
    # 检查 2: 内容是否保持完整
    content_region = result_arr[150:350, 150:350]
    dark_pixels = (content_region[:, :, 0] < 150).sum()
    total_pixels = 200 * 200
    print(f"\n[检查 2] 内容完整性:")
    print(f"  深色像素数量 (预期接近 {total_pixels}): {dark_pixels}")
    
    # 检查 3: 圆角弧线的颜色准确性
    # 在圆角弧线上，边框应该是浅灰色 (200,200,200)
    cx, cy = 469, 469  # 右下角圆角圆心
    r_px = int(2.5 / 2.54 * 150)  # 2.5cm 转换为像素
    
    border_color_ok = True
    color_deviation_count = 0
    for angle_deg in range(0, 91, 5):
        angle_rad = math.radians(angle_deg)
        for dist_offset in range(-28, 29):
            dist_px = r_px + dist_offset
            x = int(cx - dist_px * math.cos(angle_rad))
            y = int(cy - dist_px * math.sin(angle_rad))
            
            if 0 <= x < w and 0 <= y < h:
                pixel = result_arr[y, x]
                # 预期边框颜色在 (200,200,200) 附近
                # 允许一些偏差 (抗锯齿效果)
                if abs(dist_offset) <= 3:
                    deviation = abs(int(pixel[0]) - 200) + abs(int(pixel[1]) - 200) + abs(int(pixel[2]) - 200)
                    if deviation > 100:  # 允许一些偏差
                        color_deviation_count += 1
    
    if color_deviation_count > 10:
        border_color_ok = False
        print(f"\n[检查 3] 警告: 边框颜色偏差过大，共 {color_deviation_count} 个异常像素")
    else:
        print(f"\n[检查 3] 边框颜色准确性检查: 通过")
    
    assert (all_white and border_color_ok), (
        f"极端颜色与抗锯齿检查未通过: all_white={all_white}, "
        f"border_color_ok={border_color_ok}"
    )


if __name__ == '__main__':
    results = []
    
    # 运行测试 1
    try:
        result1 = test_complex_pattern_with_gaps()
        print(f"\n✅ 测试 1 结果: {'通过' if result1 else '失败'}")
        results.append(result1)
    except Exception as e:
        print(f"\n❌ 测试 1 异常: {e}")
        import traceback
        traceback.print_exc()
        results.append(False)
    
    # 运行测试 2
    try:
        result2 = test_multilayer_border_with_mixed_colors()
        print(f"\n✅ 测试 2 结果: {'通过' if result2 else '失败'}")
        results.append(result2)
    except Exception as e:
        print(f"\n❌ 测试 2 异常: {e}")
        import traceback
        traceback.print_exc()
        results.append(False)
    
    # 运行测试 3
    try:
        result3 = test_extreme_colors_and_anti_aliasing()
        print(f"\n✅ 测试 3 结果: {'通过' if result3 else '失败'}")
        results.append(result3)
    except Exception as e:
        print(f"\n❌ 测试 3 异常: {e}")
        import traceback
        traceback.print_exc()
        results.append(False)
    
    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    print(f"总测试数: {len(results)}")
    print(f"通过数: {sum(results)}")
    print(f"失败数: {len(results) - sum(results)}")
    
    if all(results):
        print("\n🎉 所有测试通过！修改是安全的。")
        sys.exit(0)
    else:
        print("\n⚠️  部分测试失败，请检查修改的影响。")
        sys.exit(1)
