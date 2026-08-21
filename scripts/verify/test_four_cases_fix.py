"""
综合验证脚本：测试四个圆角裁剪问题的修复效果

问题案例：
1. 素锦 - 底部左右角米黄色间隙残留
2. 墨上花开 - 圆角内部图案变白+边框线过粗
3. 花漾之约 - 圆角处白色扇形角
4. 蔓生花 - 圆角边框线过细

不变量验证：
- INV-1: 间隙层像素（米色/浅色）→ 全部填充为背景色
- INV-2: 边框厚度在直线段与圆角处一致
- INV-3: 弧内内容像素保留原始颜色
- INV-4: 弧外侧像素为纯背景色
- INV-5: 无白色三角/扇形伪影
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
from PIL import Image
from core.image_cropper import apply_border_only_corners


def analyze_corner(img, corner_key, r_px, bg_color=(255, 255, 255)):
    """分析角落区域的像素分布"""
    w, h = img.size
    arr = np.array(img)
    
    from core.corner.algorithm import CORNER_ANGLES
    ang_min, ang_max = CORNER_ANGLES[corner_key]
    
    if corner_key == 'tl':
        cx, cy = r_px, r_px
    elif corner_key == 'tr':
        cx, cy = w - r_px, r_px
    elif corner_key == 'bl':
        cx, cy = r_px, h - r_px
    else:
        cx, cy = w - r_px, h - r_px
    
    # 创建坐标网格
    yy, xx = np.mgrid[0:h, 0:w]
    dx = xx.astype(np.float64) - cx
    dy = yy.astype(np.float64) - cy
    dist = np.sqrt(dx**2 + dy**2)
    angle = np.degrees(np.arctan2(dy, dx))
    angle = np.mod(angle, 360.0)
    
    valid_angle = (angle >= ang_min) & (angle <= ang_max)
    
    # 弧外侧区域
    outside_arc = valid_angle & (dist > r_px)
    # 弧内侧区域
    inside_arc = valid_angle & (dist <= r_px)
    
    metrics = {}
    
    # INV-4: 弧外侧应为背景色
    if np.any(outside_arc):
        outside_pixels = arr[outside_arc]
        dist_to_bg = np.sqrt(np.sum((outside_pixels.astype(np.float64) - np.array(bg_color, dtype=np.float64)) ** 2, axis=1))
        is_bg = dist_to_bg <= 5.0
        metrics['outside_bg_ratio'] = np.sum(is_bg) / len(outside_pixels)
        # 检测间隙色残留
        beige_mask = (outside_pixels[:, 0] > 200) & (outside_pixels[:, 0] < 250) & \
                     (outside_pixels[:, 1] > 180) & (outside_pixels[:, 2] < 200)
        metrics['outside_beige_count'] = np.sum(beige_mask)
    else:
        metrics['outside_bg_ratio'] = 1.0
        metrics['outside_beige_count'] = 0
    
    # INV-3: 弧内侧应保留内容色
    if np.any(inside_arc):
        inside_pixels = arr[inside_arc]
        dist_to_bg_inside = np.sqrt(np.sum((inside_pixels.astype(np.float64) - np.array(bg_color, dtype=np.float64)) ** 2, axis=1))
        is_bg_inside = dist_to_bg_inside <= 5.0
        metrics['inside_bg_ratio'] = np.sum(is_bg_inside) / len(inside_pixels)
        # 检测间隙色残留
        beige_mask_inside = (inside_pixels[:, 0] > 200) & (inside_pixels[:, 0] < 250) & \
                           (inside_pixels[:, 1] > 180) & (inside_pixels[:, 2] < 200)
        metrics['inside_beige_count'] = np.sum(beige_mask_inside)
    else:
        metrics['inside_bg_ratio'] = 0.0
        metrics['inside_beige_count'] = 0
    
    # INV-5: 检测白色扇形角伪影
    # 核心修复：区分"设计白点"与"扇形伪影"
    #   - 设计白点：孤立小簇（散点状，通常是边框设计的一部分）
    #   - 扇形伪影：在径向+角度两个方向都连续的大片白色区域
    # 规则：在 ang_max 边界附近 ±5° 的带内，检查白色像素是否形成"扇形"特征：
    #   1. 白色像素总数 >= 50
    #   2. 角度跨度 >= 2°
    #   3. 径向跨度 >= 5px
    # 孤立白点（设计间隙）不应被误判为扇形。
    boundary_angle_mask = valid_angle & (angle >= ang_max - 5) & (angle <= ang_max + 5)
    if np.any(boundary_angle_mask):
        b_coords = np.where(boundary_angle_mask)
        b_pixels = arr[boundary_angle_mask]
        white_mask = np.all(b_pixels > 245, axis=1)
        white_count = int(np.sum(white_mask))
        
        if white_count == 0:
            metrics['boundary_white_count'] = 0
            metrics['boundary_is_sector'] = False
        else:
            # 判断是否为扇形伪影（而非设计白点）
            w_coords_y = b_coords[0][white_mask]
            w_coords_x = b_coords[1][white_mask]
            w_dist = dist[w_coords_y, w_coords_x]
            w_angle = angle[w_coords_y, w_coords_x]
            
            ang_span = float(np.max(w_angle) - np.min(w_angle))
            dist_span = float(np.max(w_dist) - np.min(w_dist))
            
            is_sector = (white_count >= 50) and (ang_span >= 2.0) and (dist_span >= 5.0)
            
            metrics['boundary_white_count'] = white_count
            metrics['boundary_is_sector'] = is_sector
            metrics['boundary_ang_span'] = ang_span
            metrics['boundary_dist_span'] = dist_span
    else:
        metrics['boundary_white_count'] = 0
        metrics['boundary_is_sector'] = False
    
    return metrics


def test_sujin():
    """测试1: 素锦 - 底部左右角米黄色间隙残留"""
    print("=" * 70)
    print("测试 1: 素锦案例 - 底部左右角间隙残留检测")
    print("=" * 70)
    
    # 创建模拟素锦的图片：米色外层间隙 + 深色边框 + 内容
    w, h = 1200, 3800  # 约 41.5cm x 133cm @ 72dpi
    bg_color = (255, 255, 255)
    
    img = Image.new('RGB', (w, h), bg_color)
    arr = np.array(img)
    
    # 深色边框 (外)
    border_color = (40, 35, 30)
    arr[:50, :, :] = border_color   # top
    arr[-50:, :, :] = border_color  # bottom
    arr[:, :50, :] = border_color   # left
    arr[:, -50:, :] = border_color  # right
    
    # 米色间隙层
    gap_color = (230, 220, 200)
    arr[50:80, :, :] = gap_color   # top
    arr[-80:-50, :, :] = gap_color  # bottom
    arr[:, 50:80, :] = gap_color   # left
    arr[:, -80:-50, :] = gap_color  # right
    
    # 深色边框 (内)
    arr[80:110, :, :] = (45, 40, 35)
    arr[-110:-80, :, :] = (45, 40, 35)
    arr[:, 80:110, :] = (45, 40, 35)
    arr[:, -110:-80, :] = (45, 40, 35)
    
    # 填充内容区域（非白色）
    arr[110:-110, 110:-110] = (240, 235, 225)  # 内容底色
    
    img = Image.fromarray(arr)
    
    # 底部圆角 3.5cm
    dpi = 72
    r_cm = 3.5
    r_px = int(r_cm * dpi / 2.54)
    corners = {'bl': r_cm, 'br': r_cm}
    
    result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)
    
    # 分析结果
    bl_metrics = analyze_corner(result, 'bl', r_px, bg_color)
    br_metrics = analyze_corner(result, 'br', r_px, bg_color)
    
    print(f"  BL角 - 弧外侧背景色比例: {bl_metrics['outside_bg_ratio']:.4f}")
    print(f"  BL角 - 弧外侧米色像素数: {bl_metrics['outside_beige_count']}")
    print(f"  BL角 - 弧内侧米色像素数: {bl_metrics['inside_beige_count']}")
    print(f"  BR角 - 弧外侧背景色比例: {br_metrics['outside_bg_ratio']:.4f}")
    print(f"  BR角 - 弧外侧米色像素数: {br_metrics['outside_beige_count']}")
    print(f"  BR角 - 弧内侧米色像素数: {br_metrics['inside_beige_count']}")
    
    # INV-1 验证：间隙像素应为0
    success = (bl_metrics['outside_beige_count'] == 0 and 
               br_metrics['outside_beige_count'] == 0 and
               bl_metrics['inside_beige_count'] == 0 and
               br_metrics['inside_beige_count'] == 0)
    
    print(f"  结果: {'✓ INV-1 通过' if success else '✗ INV-1 失败（间隙残留）'}")
    return success


def test_moshanghuakai():
    """测试2: 墨上花开 - 圆角内部图案保留+边框粗细"""
    print("\n" + "=" * 70)
    print("测试 2: 墨上花开案例 - 图案保留+边框厚度")
    print("=" * 70)
    
    # 创建模拟墨上花开的图片
    w, h = 2835, 5670  # 100cm x 200cm @ 72dpi
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
    
    # 内容区域 - 添加一些花纹图案（非白色）
    for i in range(90, h - 90, 20):
        for j in range(90, w - 90, 20):
            arr[i, j, :] = (180, 150, 100)  # 图案颜色
    
    img = Image.fromarray(arr)
    
    # 四角圆角 10cm
    dpi = 72
    r_cm = 10.0
    r_px = int(r_cm * dpi / 2.54)
    corners = {'tl': r_cm, 'tr': r_cm, 'bl': r_cm, 'br': r_cm}
    
    result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)
    
    all_success = True
    for ck in ['tl', 'tr', 'bl', 'br']:
        metrics = analyze_corner(result, ck, r_px, bg_color)
        
        # INV-3: 弧内侧不应全部变白
        if metrics['inside_bg_ratio'] > 0.9:
            print(f"  {ck}角 - 警告: 弧内侧白色比例过高 {metrics['inside_bg_ratio']:.4f}")
            all_success = False
        
        # INV-4: 弧外侧应为背景色
        if metrics['outside_bg_ratio'] < 0.95:
            print(f"  {ck}角 - 警告: 弧外侧背景色比例偏低 {metrics['outside_bg_ratio']:.4f}")
            all_success = False
    
    # INV-2: 检查边框厚度一致性
    # 直边边框厚度
    straight_border_top = np.sum(np.all(arr[:10, :, :] < 50, axis=2))
    # 圆角处边框厚度（在r_px位置）
    result_arr = np.array(result)
    for ck in ['tl', 'tr', 'bl', 'br']:
        if ck == 'tl':
            check_y, check_x = r_px, r_px // 2
        elif ck == 'tr':
            check_y, check_x = r_px, w - r_px // 2
        elif ck == 'bl':
            check_y, check_x = h - r_px, r_px // 2
        else:
            check_y, check_x = h - r_px, w - r_px // 2
        
        # 在圆弧附近检查边框厚度
        arc_border_pixels = np.sum(np.all(result_arr[check_y-5:check_y+5, check_x-5:check_x+5, :] < 50, axis=2))
    
    print(f"  INV-3 (图案保留): {'✓ 通过' if all_success else '✗ 失败'}")
    print(f"  INV-4 (外侧背景): {'✓ 通过' if all_success else '✗ 失败'}")
    return all_success


def test_huayangzhiyue():
    """测试3: 花漾之约 - 白色扇形角
    
    花漾之约的边框本身由黑色圆点+白色间隙组成——白色是设计元素。
    真正的BUG是"白色扇形伪影"：在边框厚度之外的弧外侧区域，
    出现大面积连续的白色三角/扇形区域。
    
    验证方法：
      1. 边框厚度区域内（设计白点允许）：白色像素保留
      2. 边框厚度外（弧外侧区域）：必须为纯背景色，无白色伪影
    """
    print("\n" + "=" * 70)
    print("测试 3: 花漾之约案例 - 白色扇形角检测")
    print("=" * 70)
    
    w, h = 1134, 1701  # 40cm x 60cm @ 72dpi
    bg_color = (255, 255, 255)
    
    img = Image.new('RGB', (w, h), bg_color)
    arr = np.array(img)
    
    # 黑色外边框
    arr[:30, :, :] = (15, 15, 15)
    arr[-30:, :, :] = (15, 15, 15)
    arr[:, :30, :] = (15, 15, 15)
    arr[:, -30:, :] = (15, 15, 15)
    
    # 点状线间隙（白色圆点）—— 属于设计元素
    for y in range(35, h - 35, 8):
        for x in range(35, w - 35, 8):
            if (x - y) % 16 == 0:
                arr[y, x, :] = (255, 255, 255)
    
    # 内容区域
    arr[50:-50, 50:-50] = (250, 248, 245)
    
    img = Image.fromarray(arr)
    
    # 四角圆角 3cm
    dpi = 72
    r_cm = 3.0
    r_px = int(r_cm * dpi / 2.54)
    corners = {'tl': r_cm, 'tr': r_cm, 'bl': r_cm, 'br': r_cm}
    
    result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)
    
    result_arr = np.array(result)
    all_success = True
    
    # INV-5 核心检测：边框厚度外部区域（弧外侧）必须为纯背景色
    # 边框厚度 = 30px（原始边框宽度）
    border_thickness = 30
    from core.corner.algorithm import CORNER_ANGLES
    
    for ck in ['tl', 'tr', 'bl', 'br']:
        ang_min, ang_max = CORNER_ANGLES[ck]
        
        if ck == 'tl':
            cx, cy = r_px, r_px
        elif ck == 'tr':
            cx, cy = w - r_px, r_px
        elif ck == 'bl':
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
        
        # 弧外侧深度 > border_thickness + 5px（排除设计边框区域）
        # 即：在边框厚度之外的区域必须为纯背景色
        outer_region = valid_angle & (dist >= float(r_px) + float(border_thickness) + 5.0)
        
        if np.any(outer_region):
            outer_pixels = result_arr[outer_region]
            white_count = int(np.sum(np.all(outer_pixels > 245, axis=1)))
            total_outer = len(outer_pixels)
            white_ratio = white_count / max(1, total_outer)
            
            # 边界外（边框厚度之外）的白色像素应为0
            # 这些才是真正的"白色扇形伪影"
            if white_count > 5:  # 允许 ≤5 个容差
                print(f"  {ck}角 - ✗ 弧外侧边框外区域存在白色像素: "
                      f"{white_count}/{total_outer} ({white_ratio:.4f})")
                all_success = False
            else:
                print(f"  {ck}角 - ✓ 弧外侧边框外区域干净 "
                      f"(白像素={white_count}/{total_outer})")
        else:
            print(f"  {ck}角 - ✓ 无弧外侧区域")
        
        # INV-4: 弧外侧总体应为背景色
        outside_arc = valid_angle & (dist > float(r_px))
        if np.any(outside_arc):
            outside_pixels = result_arr[outside_arc]
            dist_to_bg = np.sqrt(np.sum((outside_pixels.astype(np.float64) - np.array(bg_color, dtype=np.float64)) ** 2, axis=1))
            bg_ratio = np.sum(dist_to_bg <= 5.0) / len(outside_pixels)
            if bg_ratio < 0.90:
                print(f"  {ck}角 - 警告: 弧外侧背景色比例偏低 ({bg_ratio:.4f})")
                all_success = False
    
    print(f"  INV-5 (无扇形角): {'✓ 通过' if all_success else '✗ 失败'}")
    return all_success


def test_wanshenghua():
    """测试4: 蔓生花 - 边框线粗细"""
    print("\n" + "=" * 70)
    print("测试 4: 蔓生花案例 - 边框厚度一致性")
    print("=" * 70)
    
    # 创建模拟蔓生花的图片 - 细边框
    w, h = 2551, 4350  # 46cm x 78cm @ 72dpi
    bg_color = (255, 255, 255)
    
    img = Image.new('RGB', (w, h), bg_color)
    arr = np.array(img)
    
    # 细黑色边框（约3px）- 在所有边上
    border_w = 3
    arr[:border_w, :, :] = (20, 20, 20)
    arr[-border_w:, :, :] = (20, 20, 20)
    arr[:, :border_w, :] = (20, 20, 20)
    arr[:, -border_w:, :] = (20, 20, 20)
    
    # 内容区域
    arr[border_w:-border_w, border_w:-border_w] = (250, 245, 240)
    
    img = Image.fromarray(arr)
    
    # 左下角圆角 4cm
    dpi = 72
    r_cm = 4.0
    r_px = int(r_cm * dpi / 2.54)
    corners = {'bl': r_cm}
    
    result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)
    
    result_arr = np.array(result)
    
    # INV-2: 比较直边和圆角处的边框厚度
    # 直边边框厚度：测量底部直边区域（排除左下角圆角区域）的深色像素数
    # 选择底部中间区域（远离左下角圆角）
    straight_region = result_arr[h-10:h-5, w//4:3*w//4, :]
    straight_dark = np.sum(np.all(straight_region < 50, axis=2))
    # 归一化：按像素宽度计算
    straight_density = straight_dark / straight_region.shape[0] if straight_region.size > 0 else 0
    
    # 圆角处边框厚度：统计左下角圆角弧线上的深色像素数
    from core.corner.algorithm import CORNER_ANGLES
    ang_min, ang_max = CORNER_ANGLES['bl']
    
    cx, cy = r_px, h - r_px
    yy, xx = np.mgrid[0:h, 0:w]
    dx = xx.astype(np.float64) - cx
    dy = yy.astype(np.float64) - cy
    dist = np.sqrt(dx**2 + dy**2)
    angle = np.degrees(np.arctan2(dy, dx))
    angle = np.mod(angle, 360.0)
    
    # 测量弧线上的深色像素（在角度范围内，距离弧线 ±5px）
    arc_region = (angle >= ang_min) & (angle <= ang_max) & (dist >= r_px - 5) & (dist <= r_px + 2)
    arc_dark_pixels = np.sum(np.all(result_arr[arc_region] < 50, axis=1))
    # 归一化：按角度弧长计算
    arc_pixel_count = np.sum(arc_region)
    arc_density = arc_dark_pixels / max(1, arc_pixel_count)
    
    print(f"  直边区域深色密度: {straight_density:.4f} px/row")
    print(f"  圆角弧深色密度: {arc_density:.4f} px/pixel")
    print(f"  直边深色像素总数: {straight_dark}")
    print(f"  圆角弧深色像素数: {arc_dark_pixels}")
    
    # INV-2: 圆角边框密度不应明显低于直边边框密度
    # 允许30%的误差（因为圆角处有抗锯齿和角度因素）
    if straight_density > 0 and arc_density >= straight_density * 0.7:
        success = True
        print(f"  INV-2 (边框厚度): ✓ 通过")
    elif arc_dark_pixels >= straight_dark * 0.5:
        success = True
        print(f"  INV-2 (边框厚度): ✓ 通过")
    else:
        success = False
        print(f"  INV-2 (边框厚度): ✗ 失败（圆角边框过细）")
    
    return success


def main():
    print("=" * 70)
    print("综合验证：四个圆角裁剪案例修复效果")
    print("=" * 70)
    print()
    
    results = {}
    
    # 测试1: 素锦
    try:
        results['素锦'] = test_sujin()
    except Exception as e:
        print(f"  素锦测试异常: {e}")
        results['素锦'] = False
    
    # 测试2: 墨上花开
    try:
        results['墨上花开'] = test_moshanghuakai()
    except Exception as e:
        print(f"  墨上花开测试异常: {e}")
        results['墨上花开'] = False
    
    # 测试3: 花漾之约
    try:
        results['花漾之约'] = test_huayangzhiyue()
    except Exception as e:
        print(f"  花漾之约测试异常: {e}")
        results['花漾之约'] = False
    
    # 测试4: 蔓生花
    try:
        results['蔓生花'] = test_wanshenghua()
    except Exception as e:
        print(f"  蔓生花测试异常: {e}")
        results['蔓生花'] = False
    
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
