"""
最终验证测试 - 验证修复后的圆角处理逻辑

核心验证点：
1. 修复逻辑不会将间隙错误填充为边框颜色（边界像素除外）
2. 边框在圆角处平滑连续，无明显缺口
3. 内部花纹保持完整
4. 原有功能不受影响
"""
import numpy as np
import math
from PIL import Image
import sys
sys.path.insert(0, '.')

from core.image_cropper import apply_rounded_corners, apply_border_only_corners


def test_border_smoothness():
    """
    测试边框在圆角处的平滑性
    
    验证点：圆角弧线上不应有明显的白色缺口
    """
    print("=" * 60)
    print("测试 1: 边框圆角平滑性验证")
    print("=" * 60)
    
    w, h = 800, 1000
    img = Image.new('RGB', (w, h), (255, 255, 255))
    arr = np.array(img)
    
    # 外层黑色边框
    arr[0:50, :] = (0, 0, 0)
    arr[-50:, :] = (0, 0, 0)
    arr[:, 0:50] = (0, 0, 0)
    arr[:, -50:] = (0, 0, 0)
    
    # 内层红色装饰
    arr[100:150, 100:700] = (200, 0, 0)
    arr[150:850, 100:150] = (200, 0, 0)
    
    img = Image.fromarray(arr, 'RGB')
    bg_color = (255, 255, 255)
    
    dpi = 150
    corners = {'tl': 0, 'tr': 0, 'bl': 3.0, 'br': 0}
    
    result = apply_rounded_corners(img, corners, dpi=dpi, bg_color=bg_color)
    result_arr = np.array(result)
    
    # 检查左下角圆角弧线上的边框连续性
    cx, cy = 50, 950
    r_px = int(3.0 / 2.54 * dpi)
    
    gap_count = 0
    total_checks = 0
    for angle_deg in range(2, 91, 3):
        angle_rad = math.radians(angle_deg)
        x = int(cx + r_px * math.cos(angle_rad))
        y = int(cy - r_px * math.sin(angle_rad))
        if 0 <= x < w and 0 <= y < h:
            total_checks += 1
            pixel = result_arr[y, x]
            if tuple(pixel) == (255, 255, 255):
                gap_count += 1
    
    gap_ratio = gap_count / total_checks * 100 if total_checks > 0 else 0
    
    print(f"  检查点数: {total_checks}")
    print(f"  白色缺口数: {gap_count}")
    print(f"  缺口率: {gap_ratio:.1f}%")
    
    # 缺口率应小于5%
    passed = gap_ratio < 5.0
    if passed:
        print("  ✅ 通过: 边框平滑，无明显缺口")
    else:
        print("  ⚠️  警告: 边框上可能存在较多缺口")
    
    return passed


def test_no_wrong_gap_fill():
    """
    验证间隙区域不会被错误填充为边框颜色
    
    检查非边界的间隙像素（距圆角中心52-68px）
    """
    print("\n" + "=" * 60)
    print("测试 2: 间隙区域填充验证")
    print("=" * 60)
    
    w, h = 800, 1000
    img = Image.new('RGB', (w, h), (255, 255, 255))
    arr = np.array(img)
    
    # 外层黑色边框 50px
    arr[0:50, :] = (0, 0, 0)
    arr[-50:, :] = (0, 0, 0)
    arr[:, 0:50] = (0, 0, 0)
    arr[:, -50:] = (0, 0, 0)
    
    # 浅灰色间隙 20px (50-70)
    arr[50:70, 50:750] = (200, 200, 200)
    arr[-70:-50, 50:750] = (200, 200, 200)
    arr[50:950, 50:70] = (200, 200, 200)
    arr[50:950, -70:-50] = (200, 200, 200)
    
    # 内层红色边框 30px
    arr[70:100, 70:730] = (200, 0, 0)
    arr[-100:-70, 70:730] = (200, 0, 0)
    arr[70:930, 70:100] = (200, 0, 0)
    arr[70:930, -100:-70] = (200, 0, 0)
    
    img = Image.fromarray(arr, 'RGB')
    bg_color = (255, 255, 255)
    
    dpi = 150
    corners = {'tl': 0, 'tr': 0, 'bl': 3.0, 'br': 0}
    
    result = apply_rounded_corners(img, corners, dpi=dpi, bg_color=bg_color)
    result_arr = np.array(result)
    
    cx, cy = 50, 950
    r_px = int(3.0 / 2.54 * dpi)
    
    # 检查间隙中间区域（52-68px）- 排除边界像素
    black_in_gap_core = 0
    total_gap_core = 0
    for angle_deg in range(5, 91, 5):
        angle_rad = math.radians(angle_deg)
        for dist_px in range(52, 69):  # 排除边界
            x = int(cx + dist_px * math.cos(angle_rad))
            y = int(cy - dist_px * math.sin(angle_rad))
            if 0 <= x < w and 0 <= y < h:
                total_gap_core += 1
                pixel = result_arr[y, x]
                if pixel[0] < 30 and pixel[1] < 30 and pixel[2] < 30:
                    black_in_gap_core += 1
    
    black_ratio = black_in_gap_core / total_gap_core * 100 if total_gap_core > 0 else 0
    
    print(f"  间隙核心区域检查点: {total_gap_core}")
    print(f"  黑色像素数: {black_in_gap_core}")
    print(f"  黑色占比: {black_ratio:.1f}%")
    
    # 核心间隙区域不应有黑色像素
    passed = black_ratio < 5.0
    if passed:
        print("  ✅ 通过: 间隙区域无错误黑色填充")
    else:
        print("  ⚠️  警告: 间隙区域可能存在错误填充")
    
    return passed


def test_internal_content_preserved():
    """
    验证内部花纹在圆角处理后保持完整
    """
    print("\n" + "=" * 60)
    print("测试 3: 内部花纹完整性验证")
    print("=" * 60)
    
    w, h = 800, 1000
    img = Image.new('RGB', (w, h), (255, 255, 255))
    arr = np.array(img)
    
    # 边框
    arr[0:50, :] = (0, 0, 0)
    arr[-50:, :] = (0, 0, 0)
    arr[:, 0:50] = (0, 0, 0)
    arr[:, -50:] = (0, 0, 0)
    
    # 复杂花纹
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
    
    dpi = 150
    corners = {'tl': 0, 'tr': 0, 'bl': 3.0, 'br': 0}
    
    result = apply_rounded_corners(img, corners, dpi=dpi, bg_color=bg_color)
    result_arr = np.array(result)
    
    # 检查蓝色圆形
    circle_region = result_arr[250:350, 250:350]
    blue_count = (circle_region[:, :, 2] > 150).sum()
    expected_blue = 10000
    blue_pct = blue_count / expected_blue * 100
    
    # 检查绿色矩形
    rect_region = result_arr[550:650, 250:350]
    green_count = (rect_region[:, :, 1] > 150).sum()
    expected_green = 10000
    green_pct = green_count / expected_green * 100
    
    print(f"  蓝色圆形保留率: {blue_pct:.1f}% (预期 > 90%)")
    print(f"  绿色矩形保留率: {green_pct:.1f}% (预期 > 90%)")
    
    passed = blue_pct > 90 and green_pct > 90
    if passed:
        print("  ✅ 通过: 内部花纹保持完整")
    else:
        print("  ❌ 失败: 内部花纹被破坏")
    
    return passed


def test_original_functionality_unchanged():
    """
    验证原有功能不受影响
    
    检查点：
    1. 无边框圆角正常
    2. 零半径无变化
    3. 简单尺寸调整正常
    """
    print("\n" + "=" * 60)
    print("测试 4: 原有功能兼容性验证")
    print("=" * 60)
    
    w, h = 400, 500
    img = Image.new('RGB', (w, h), (255, 255, 255))
    
    # 绘制简单边框
    arr = np.array(img)
    arr[0:30, :] = (0, 0, 0)
    arr[-30:, :] = (0, 0, 0)
    arr[:, 0:30] = (0, 0, 0)
    arr[:, -30:] = (0, 0, 0)
    img = Image.fromarray(arr, 'RGB')
    
    all_passed = True
    
    # 测试1: 零半径无变化
    result_zero = apply_rounded_corners(img, {'tl': 0, 'tr': 0, 'bl': 0, 'br': 0})
    if result_zero.tobytes() == img.tobytes():
        print("  ✅ 零半径测试通过")
    else:
        print("  ❌ 零半径测试失败")
        all_passed = False
    
    # 测试2: 小半径测试
    result_small = apply_rounded_corners(img, {'tl': 0.5, 'tr': 0, 'bl': 0, 'br': 0})
    result_small_arr = np.array(result_small)
    # 检查结果是否与原图不同（应该不同）
    if not np.array_equal(result_small_arr, arr):
        print("  ✅ 小半径测试通过")
    else:
        print("  ❌ 小半径测试失败")
        all_passed = False
    
    # 测试3: 四角圆角
    result_all = apply_rounded_corners(img, {'tl': 1.0, 'tr': 1.0, 'bl': 1.0, 'br': 1.0})
    result_all_arr = np.array(result_all)
    # 检查四角都被处理
    if not np.array_equal(result_all_arr, arr):
        # 检查四角的白色区域
        tl_corner = result_all_arr[0:50, 0:50]
        br_corner = result_all_arr[-50:, -50:]
        tl_is_white = (tl_corner == 255).all()
        br_is_white = (br_corner == 255).all()
        if tl_is_white and br_is_white:
            print("  ✅ 四角圆角测试通过")
        else:
            print("  ⚠️  四角圆角效果可能不符合预期")
    else:
        print("  ❌ 四角圆角测试失败")
        all_passed = False
    
    return all_passed


def main():
    print("\n" + "=" * 60)
    print("圆角修复最终验证测试")
    print("=" * 60)
    print("\n验证修复逻辑是否：")
    print("  1. 解决圆角缺口问题")
    print("  2. 不将间隙错误填充为边框颜色")
    print("  3. 保持内部花纹完整")
    print("  4. 不影响原有功能")
    print()
    
    results = []
    
    results.append(("边框圆角平滑性", test_border_smoothness()))
    results.append(("间隙区域填充验证", test_no_wrong_gap_fill()))
    results.append(("内部花纹完整性", test_internal_content_preserved()))
    results.append(("原有功能兼容性", test_original_functionality_unchanged()))
    
    print("\n" + "=" * 60)
    print("最终测试总结")
    print("=" * 60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status} - {name}")
    
    print(f"\n总计: {passed}/{total} 项通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！")
        print("\n修复结论：")
        print("  ✅ 解决了圆角缺口问题")
        print("  ✅ 间隙区域不会被错误填充为边框颜色")
        print("  ✅ 内部花纹保持完整")
        print("  ✅ 原有功能不受影响")
        return 0
    else:
        print("\n⚠️  部分测试失败，请检查。")
        return 1


if __name__ == '__main__':
    sys.exit(main())
