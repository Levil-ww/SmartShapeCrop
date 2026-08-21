"""
验证脚本：测试安妮森林和中古花园案例的修复效果

问题案例：
1. 安妮森林 - 圆角部分黑色边框消失，出现白色/米色弧形缺口
2. 中古花园 - 圆角部分出现有宽度的弧形缺口，应为光滑黑色边框

验证不变量：
- INV-AF-1: 最外层黑色边框（深色）不得被识别为间隙层
- INV-AF-2: 间隙层（白色/米色）被正确清除
- INV-AF-3: 圆角处黑色边框连续、无缺口
- INV-AF-4: 圆角弧外侧为纯背景色
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
from PIL import Image
from core.image_cropper import apply_border_only_corners
from core.corner.algorithm import CORNER_ANGLES


def analyze_corner_detailed(img, corner_key, r_px, bg_color=(255, 255, 255)):
    """详细分析角落区域的边框/间隙分布"""
    w, h = img.size
    arr = np.array(img)
    
    ang_min, ang_max = CORNER_ANGLES[corner_key]
    
    if corner_key == 'tl':
        cx, cy = r_px, r_px
    elif corner_key == 'tr':
        cx, cy = w - r_px, r_px
    elif corner_key == 'bl':
        cx, cy = r_px, h - r_px
    else:
        cx, cy = w - r_px, h - r_px
    
    yy, xx = np.mgrid[0:h, 0:w]
    dx = xx.astype(np.float64) - cx
    dy = yy.astype(np.float64) - cy
    dist = np.sqrt(dx**2 + dy**2)
    angle = np.degrees(np.arctan2(dy, dx))
    angle = np.mod(angle, 360.0)
    
    valid_angle = (angle >= ang_min) & (angle <= ang_max)
    
    metrics = {}
    
    # 1. 检查弧边界（dist ≈ r_px）的黑色边框像素
    # 这是最关键的检查：确保黑色边框存在于圆角弧线
    arc_band = valid_angle & (dist >= float(r_px) - 3.0) & (dist <= float(r_px) + 3.0)
    if np.any(arc_band):
        arc_pixels = arr[arc_band]
        # 黑色/深色像素检测 (max RGB < 100)
        dark_mask = np.max(arc_pixels, axis=1) < 100
        dark_count = int(np.sum(dark_mask))
        total_arc_pixels = len(arc_pixels)
        metrics['arc_dark_count'] = dark_count
        metrics['arc_dark_ratio'] = dark_count / max(1, total_arc_pixels)
        metrics['arc_total_pixels'] = total_arc_pixels
        
        # 如果黑色边框比例低于50%，说明边框消失
        if metrics['arc_dark_ratio'] < 0.5:
            print(f"    ⚠ 警告: 弧边界黑色像素比例仅 {metrics['arc_dark_ratio']:.2%}，边框可能消失！")
    else:
        metrics['arc_dark_count'] = 0
        metrics['arc_dark_ratio'] = 0.0
        metrics['arc_total_pixels'] = 0
    
    # 2. 检查弧外侧区域（dist > r_px + 3）的间隙残留
    outside_zone = valid_angle & (dist > float(r_px) + 3.0)
    if np.any(outside_zone):
        outside_pixels = arr[outside_zone]
        # 米色间隙像素检测 (排除背景色)
        beige_mask = (outside_pixels[:, 0] > 180) & (outside_pixels[:, 1] > 180) & \
                     (outside_pixels[:, 2] > 180) & (outside_pixels[:, 0] < 250)
        beige_count = int(np.sum(beige_mask))
        metrics['outside_beige_count'] = beige_count
        
        # 白色间隙像素检测 (排除纯背景色255)
        white_mask = (outside_pixels[:, 0] > 240) & (outside_pixels[:, 1] > 240) & \
                     (outside_pixels[:, 2] > 240) & (outside_pixels[:, 0] < 252)
        white_count = int(np.sum(white_mask))
        metrics['outside_white_count'] = white_count
        
        total_outside = len(outside_pixels)
        metrics['outside_total'] = total_outside
        
        # 弧外侧应为背景色
        dist_to_bg = np.sqrt(np.sum((outside_pixels.astype(np.float64) - np.array(bg_color, dtype=np.float64)) ** 2, axis=1))
        bg_count = np.sum(dist_to_bg <= 5.0)
        metrics['outside_bg_ratio'] = bg_count / max(1, total_outside)
    else:
        metrics['outside_beige_count'] = 0
        metrics['outside_white_count'] = 0
        metrics['outside_total'] = 0
        metrics['outside_bg_ratio'] = 1.0
    
    # 3. 检查弧内侧区域（dist < r_px - 3）的间隙残留
    inside_zone = valid_angle & (dist < float(r_px) - 3.0)
    if np.any(inside_zone):
        inside_pixels = arr[inside_zone]
        beige_mask_inside = (inside_pixels[:, 0] > 180) & (inside_pixels[:, 1] > 180) & \
                            (inside_pixels[:, 2] > 180) & (inside_pixels[:, 0] < 250)
        metrics['inside_beige_count'] = int(np.sum(beige_mask_inside))
        
        # 白色间隙 (排除背景色255)
        white_mask_inside = (inside_pixels[:, 0] > 240) & (inside_pixels[:, 1] > 240) & \
                            (inside_pixels[:, 2] > 240) & (inside_pixels[:, 0] < 252)
        metrics['inside_white_count'] = int(np.sum(white_mask_inside))
    else:
        metrics['inside_beige_count'] = 0
        metrics['inside_white_count'] = 0
    
    # 4. 检查边框完整性：沿弧线采样点
    # 在弧线上均匀采样8个点，检查每个点是否有黑色像素
    sample_points = np.linspace(ang_min, ang_max, 10, dtype=np.float64)
    complete = True
    for sp in sample_points:
        # 转换为角度偏移
        sp_wrapped = np.mod(sp, 360.0)
        # 找到在该角度附近（±2°）的弧边界像素
        angle_diff = np.abs(angle - sp_wrapped)
        angle_diff = np.minimum(angle_diff, 360.0 - angle_diff)
        near_angle = angle_diff <= 2.0
        near_arc = valid_angle & near_angle & (dist >= float(r_px) - 2.0) & (dist <= float(r_px) + 2.0)
        
        if np.any(near_arc):
            arc_p = arr[near_arc]
            has_dark = np.any(np.max(arc_p, axis=1) < 100)
            if not has_dark:
                complete = False
                break
    
    metrics['border_complete'] = complete
    
    return metrics


def test_annie_forest():
    """测试: 安妮森林 - 黑色边框保留+间隙清除"""
    print("=" * 70)
    print("测试: 安妮森林案例 - 黑色边框保留+间隙清除验证")
    print("=" * 70)
    
    # 创建模拟安妮森林的图片（黑色外边框 + 白色间隙 + 米色间隙 + 内边框）
    w, h = 2145, 3675  # 70cm x 120cm @ 72dpi
    bg_color = (255, 255, 255)
    
    img = Image.new('RGB', (w, h), bg_color)
    arr = np.array(img)
    
    # 深色外边框 (黑色)
    outer_border = (30, 25, 20)
    border_w_outer = 12
    arr[:border_w_outer, :, :] = outer_border
    arr[-border_w_outer:, :, :] = outer_border
    arr[:, :border_w_outer, :] = outer_border
    arr[:, -border_w_outer:, :] = outer_border
    
    # 白色间隙层
    gap_color_white = (250, 250, 250)
    gap_w1 = 5
    arr[border_w_outer:border_w_outer+gap_w1, :, :] = gap_color_white
    arr[-(border_w_outer+gap_w1):-border_w_outer, :, :] = gap_color_white
    arr[:, border_w_outer:border_w_outer+gap_w1, :] = gap_color_white
    arr[:, -(border_w_outer+gap_w1):-border_w_outer] = gap_color_white
    
    # 米色间隙层
    gap_color_beige = (220, 210, 190)
    gap_w2 = 8
    arr[border_w_outer+gap_w1:border_w_outer+gap_w1+gap_w2, :, :] = gap_color_beige
    arr[-(border_w_outer+gap_w1+gap_w2):-(border_w_outer+gap_w1), :, :] = gap_color_beige
    arr[:, border_w_outer+gap_w1:border_w_outer+gap_w1+gap_w2, :] = gap_color_beige
    arr[:, -(border_w_outer+gap_w1+gap_w2):-(border_w_outer+gap_w1)] = gap_color_beige
    
    # 深色内边框
    inner_border = (35, 30, 25)
    inner_w = 10
    inner_start = border_w_outer + gap_w1 + gap_w2
    arr[inner_start:inner_start+inner_w, :, :] = inner_border
    arr[-(inner_start+inner_w):-inner_start, :, :] = inner_border
    arr[:, inner_start:inner_start+inner_w, :] = inner_border
    arr[:, -(inner_start+inner_w):-inner_start] = inner_border
    
    # 内容区域（非白色，带些花纹）
    content_start = inner_start + inner_w
    content_color = (235, 220, 180)
    arr[content_start:-content_start, content_start:-content_start] = content_color
    
    # 添加一些花纹点
    np.random.seed(42)
    for _ in range(500):
        y = np.random.randint(content_start, h - content_start)
        x = np.random.randint(content_start, w - content_start)
        arr[y, x, :] = (60, 50, 40)
    
    img = Image.fromarray(arr)
    
    # 四角圆角 3.5cm
    dpi = 72
    r_cm = 3.5
    r_px = int(r_cm * dpi / 2.54)
    corners = {'tl': r_cm, 'tr': r_cm, 'bl': r_cm, 'br': r_cm}
    
    print(f"  圆角半径: {r_cm}cm = {r_px}px")
    print(f"  外层边框厚度: {border_w_outer}px")
    print(f"  边框层总厚度: {border_w_outer + gap_w1 + gap_w2 + inner_w}px")
    print()
    
    result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)
    
    result_arr = np.array(result)
    all_success = True
    
    print("  --- 角落分析 ---")
    for ck in ['tl', 'tr', 'bl', 'br']:
        metrics = analyze_corner_detailed(result, ck, r_px, bg_color)
        
        print(f"\n  {ck}角:")
        print(f"    弧边界深色像素数: {metrics['arc_dark_count']}/{metrics['arc_total_pixels']} ({metrics['arc_dark_ratio']:.2%})")
        print(f"    弧边界边框完整: {'✓' if metrics['border_complete'] else '✗'}")
        print(f"    弧外侧米色间隙数: {metrics['outside_beige_count']}")
        print(f"    弧外侧白色间隙数: {metrics['outside_white_count']}")
        print(f"    弧外侧背景色比例: {metrics['outside_bg_ratio']:.4f}")
        print(f"    弧内侧米色间隙数: {metrics['inside_beige_count']}")
        
        # INV-AF-1: 黑色边框存在
        if metrics['arc_dark_ratio'] < 0.3:
            print(f"    ✗ INV-AF-1 失败: 黑色边框严重缺失")
            all_success = False
        else:
            print(f"    ✓ INV-AF-1 通过: 黑色边框保留")
        
        # INV-AF-2: 间隙清除
        if metrics['outside_beige_count'] > 100 or metrics['outside_white_count'] > 100:
            print(f"    ✗ INV-AF-2 失败: 间隙残留过多")
            all_success = False
        else:
            print(f"    ✓ INV-AF-2 通过: 间隙基本清除")
        
        # INV-AF-3: 边框完整
        if not metrics['border_complete']:
            print(f"    ✗ INV-AF-3 失败: 边框不完整")
            all_success = False
        else:
            print(f"    ✓ INV-AF-3 通过: 边框连续完整")
        
        # INV-AF-4: 外侧背景
        if metrics['outside_bg_ratio'] < 0.95:
            print(f"    ✗ INV-AF-4 失败: 弧外侧背景色比例过低")
            all_success = False
        else:
            print(f"    ✓ INV-AF-4 通过: 弧外侧为背景色")
    
    print(f"\n  {'总体结果: ✓ 安妮森林修复通过' if all_success else '总体结果: ✗ 安妮森林修复失败'}")
    return all_success


def test_zhongguohuayuan():
    """测试: 中古花园 - 黑色边框保留+间隙清除"""
    print("\n" + "=" * 70)
    print("测试: 中古花园案例 - 黑色边框保留+间隙清除验证")
    print("=" * 70)
    
    # 创建模拟中古花园的图片（黑色外边框 + 白色间隙 + 深色内边框）
    w, h = 1905, 3429  # 65cm x 120cm @ 72dpi
    bg_color = (255, 255, 255)
    
    img = Image.new('RGB', (w, h), bg_color)
    arr = np.array(img)
    
    # 深色外边框 (黑色)
    outer_border = (25, 22, 18)
    border_w_outer = 10
    arr[:border_w_outer, :, :] = outer_border
    arr[-border_w_outer:, :, :] = outer_border
    arr[:, :border_w_outer, :] = outer_border
    arr[:, -border_w_outer:, :] = outer_border
    
    # 白色间隙层
    gap_color_white = (245, 245, 245)
    gap_w1 = 4
    arr[border_w_outer:border_w_outer+gap_w1, :, :] = gap_color_white
    arr[-(border_w_outer+gap_w1):-border_w_outer, :, :] = gap_color_white
    arr[:, border_w_outer:border_w_outer+gap_w1, :] = gap_color_white
    arr[:, -(border_w_outer+gap_w1):-border_w_outer] = gap_color_white
    
    # 米色间隙层
    gap_color_beige = (210, 200, 180)
    gap_w2 = 6
    arr[border_w_outer+gap_w1:border_w_outer+gap_w1+gap_w2, :, :] = gap_color_beige
    arr[-(border_w_outer+gap_w1+gap_w2):-(border_w_outer+gap_w1), :, :] = gap_color_beige
    arr[:, border_w_outer+gap_w1:border_w_outer+gap_w1+gap_w2, :] = gap_color_beige
    arr[:, -(border_w_outer+gap_w1+gap_w2):-(border_w_outer+gap_w1)] = gap_color_beige
    
    # 深色内边框
    inner_border = (30, 25, 22)
    inner_w = 8
    inner_start = border_w_outer + gap_w1 + gap_w2
    arr[inner_start:inner_start+inner_w, :, :] = inner_border
    arr[-(inner_start+inner_w):-inner_start, :, :] = inner_border
    arr[:, inner_start:inner_start+inner_w, :] = inner_border
    arr[:, -(inner_start+inner_w):-inner_start] = inner_border
    
    # 内容区域
    content_start = inner_start + inner_w
    content_color = (240, 235, 225)
    arr[content_start:-content_start, content_start:-content_start] = content_color
    
    # 添加花纹
    np.random.seed(123)
    for _ in range(800):
        y = np.random.randint(content_start, h - content_start)
        x = np.random.randint(content_start, w - content_start)
        arr[y, x, :] = (50, 45, 40)
    
    img = Image.fromarray(arr)
    
    # 四角圆角 2cm
    dpi = 72
    r_cm = 2.0
    r_px = int(r_cm * dpi / 2.54)
    corners = {'tl': r_cm, 'tr': r_cm, 'bl': r_cm, 'br': r_cm}
    
    print(f"  圆角半径: {r_cm}cm = {r_px}px")
    print(f"  外层边框厚度: {border_w_outer}px")
    print(f"  边框层总厚度: {border_w_outer + gap_w1 + gap_w2 + inner_w}px")
    print()
    
    result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)
    
    all_success = True
    
    print("  --- 角落分析 ---")
    for ck in ['tl', 'tr', 'bl', 'br']:
        metrics = analyze_corner_detailed(result, ck, r_px, bg_color)
        
        print(f"\n  {ck}角:")
        print(f"    弧边界深色像素数: {metrics['arc_dark_count']}/{metrics['arc_total_pixels']} ({metrics['arc_dark_ratio']:.2%})")
        print(f"    弧边界边框完整: {'✓' if metrics['border_complete'] else '✗'}")
        print(f"    弧外侧米色间隙数: {metrics['outside_beige_count']}")
        print(f"    弧外侧白色间隙数: {metrics['outside_white_count']}")
        print(f"    弧外侧背景色比例: {metrics['outside_bg_ratio']:.4f}")
        
        # INV-AF-1: 黑色边框存在
        if metrics['arc_dark_ratio'] < 0.3:
            print(f"    ✗ INV-AF-1 失败: 黑色边框严重缺失")
            all_success = False
        else:
            print(f"    ✓ INV-AF-1 通过: 黑色边框保留")
        
        # INV-AF-2: 间隙清除
        if metrics['outside_beige_count'] > 100 or metrics['outside_white_count'] > 100:
            print(f"    ✗ INV-AF-2 失败: 间隙残留过多")
            all_success = False
        else:
            print(f"    ✓ INV-AF-2 通过: 间隙基本清除")
        
        # INV-AF-3: 边框完整
        if not metrics['border_complete']:
            print(f"    ✗ INV-AF-3 失败: 边框不完整")
            all_success = False
        else:
            print(f"    ✓ INV-AF-3 通过: 边框连续完整")
        
        # INV-AF-4: 外侧背景
        if metrics['outside_bg_ratio'] < 0.95:
            print(f"    ✗ INV-AF-4 失败: 弧外侧背景色比例过低")
            all_success = False
        else:
            print(f"    ✓ INV-AF-4 通过: 弧外侧为背景色")
    
    print(f"\n  {'总体结果: ✓ 中古花园修复通过' if all_success else '总体结果: ✗ 中古花园修复失败'}")
    return all_success


def main():
    print("=" * 70)
    print("验证: 安妮森林 & 中古花园 圆角裁剪修复效果")
    print("=" * 70)
    print()
    
    results = {}
    
    try:
        results['安妮森林'] = test_annie_forest()
    except Exception as e:
        import traceback
        print(f"  安妮森林测试异常: {e}")
        traceback.print_exc()
        results['安妮森林'] = False
    
    try:
        results['中古花园'] = test_zhongguohuayuan()
    except Exception as e:
        import traceback
        print(f"  中古花园测试异常: {e}")
        traceback.print_exc()
        results['中古花园'] = False
    
    # 汇总
    print("\n" + "=" * 70)
    print("测试结果汇总")
    print("=" * 70)
    for name, success in results.items():
        status = "✓ 通过" if success else "✗ 失败"
        print(f"  {name}: {status}")
    
    all_pass = all(results.values())
    print(f"\n  总体结果: {'✓ 全部通过' if all_pass else '✗ 存在失败'}")
    
    return 0 if all_pass else 1


if __name__ == '__main__':
    sys.exit(main())
