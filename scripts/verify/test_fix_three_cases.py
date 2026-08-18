"""
Test script for fixing the three corner crop issues.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
import numpy as np
from core.image_cropper import apply_border_only_corners


def analyze_corner_region(img, corner_key, r_px, size_cm):
    """Analyze the corner region of an image."""
    w, h = img.size
    arr = np.array(img)
    
    # Determine center based on corner (same as _build_multi_layer_corner_mask)
    if corner_key == 'tl':
        cx, cy = r_px, r_px
    elif corner_key == 'tr':
        cx, cy = w - r_px, r_px
    elif corner_key == 'bl':
        cx, cy = r_px, h - r_px
    else:  # br
        cx, cy = w - r_px, h - r_px
    
    # Create coordinate grids
    yy, xx = np.mgrid[0:h, 0:w]
    dx = xx.astype(np.float64) - cx
    dy = yy.astype(np.float64) - cy
    dist = np.sqrt(dx**2 + dy**2)
    
    # Calculate angle (same as _build_multi_layer_corner_mask)
    angle = np.degrees(np.arctan2(dy, dx))
    angle = np.mod(angle, 360.0)
    
    # Get angle range from CORNER_ANGLES
    from core.corner.algorithm import CORNER_ANGLES
    ang_min, ang_max = CORNER_ANGLES[corner_key]
    
    # Create valid angle mask
    valid_angle = (angle >= ang_min) & (angle < ang_max)
    
    # Region outside the arc (dist > r)
    outside_arc = valid_angle & (dist > r_px)
    
    # Region inside the arc (dist <= r)
    inside_arc = valid_angle & (dist <= r_px)
    
    # Count pixels
    outside_count = np.sum(outside_arc)
    inside_count = np.sum(inside_arc)
    
    # Analyze outside region
    if outside_count > 0:
        outside_pixels = arr[outside_arc]
        outside_mean = outside_pixels.mean(axis=0)
        # Check if outside is mostly white (should be white after fix)
        white_mask = np.all(outside_pixels > 240, axis=1)
        white_ratio = np.sum(white_mask) / outside_count
    else:
        outside_mean = [0, 0, 0]
        white_ratio = 0
    
    # Analyze inside region
    if inside_count > 0:
        inside_pixels = arr[inside_arc]
        inside_mean = inside_pixels.mean(axis=0)
        # Check for white pixels inside (should not have white inside)
        white_mask_inside = np.all(inside_pixels > 240, axis=1)
        white_ratio_inside = np.sum(white_mask_inside) / inside_count
    else:
        inside_mean = [0, 0, 0]
        white_ratio_inside = 0
    
    return {
        'size_cm': size_cm,
        'corner': corner_key,
        'r_px': r_px,
        'outside_count': outside_count,
        'inside_count': inside_count,
        'outside_mean': outside_mean,
        'inside_mean': inside_mean,
        'white_ratio_outside': white_ratio,
        'white_ratio_inside': white_ratio_inside,
    }


def test_case_1():
    """Test case 1: 青芜漫野 20.3x87CM 左下角 2.5cm半径"""
    print("=" * 60)
    print("测试用例 1: 青芜漫野 20.3x87CM 左下角 2.5cm半径")
    print("=" * 60)
    
    # Create test image with simulated design
    w, h = 714, 3045  # 20.3cm x 87cm at 35px/cm
    img = Image.new('RGB', (w, h), (245, 240, 230))  # Light beige background
    
    # Add border layers
    arr = np.array(img)
    # Outer dark border
    arr[:8, :, :] = 30
    arr[-8:, :, :] = 30
    arr[:, :8, :] = 30
    arr[:, -8:, :] = 30
    # Beige gap
    arr[8:16, :, :] = [245, 240, 230]
    arr[-16:-8, :, :] = [245, 240, 230]
    arr[:, 8:16, :] = [245, 240, 230]
    arr[:, -16:-8, :] = [245, 240, 230]
    # Inner border
    arr[16:25, :, :] = 60
    arr[-25:-16, :, :] = 60
    arr[:, 16:25, :] = 60
    arr[:, -25:-16, :] = 60
    # Add some content
    arr[30:-30, 30:-30] = [245, 240, 230]
    
    img = Image.fromarray(arr)
    
    # Apply smart shape crop (dpi = 35px/cm * 2.54 = 88.9, use dpi=35*2.54)
    dpi = 35 * 2.54  # ~88.9
    corners = {'bl': 2.5}  # 2.5cm radius
    result = apply_border_only_corners(img, corners, dpi=dpi)
    
    # Analyze result
    r_px = 2.5 * dpi / 2.54  # = 35 * 2.5 = 87.5
    metrics = analyze_corner_region(result, 'bl', r_px, '20.3x87CM')
    
    print(f"  裁切区域像素数: {metrics['outside_count']}")
    print(f"  保留区域像素数: {metrics['inside_count']}")
    print(f"  裁切区域白色比例: {metrics['white_ratio_outside']:.4f}")
    print(f"  保留区域白色比例: {metrics['white_ratio_inside']:.4f}")
    print(f"  裁切区域平均色: {metrics['outside_mean'].astype(int)}")
    print(f"  保留区域平均色: {metrics['inside_mean'].astype(int)}")
    
    success = metrics['white_ratio_outside'] > 0.95 and metrics['white_ratio_inside'] < 0.05
    print(f"  结果: {'✓ 通过' if success else '✗ 失败'}")
    return success


def test_case_2():
    """Test case 2: 蔓生花 55x84CM 左下角 9.5cm半径"""
    print("\n" + "=" * 60)
    print("测试用例 2: 蔓生花 55x84CM 左下角 9.5cm半径")
    print("=" * 60)
    
    # Create test image with simulated design
    w, h = 1925, 2940  # 55cm x 84cm at 35px/cm
    img = Image.new('RGB', (w, h), (245, 240, 230))  # Light beige background
    
    # Add border layers
    arr = np.array(img)
    # Outer dark border
    arr[:8, :, :] = 30
    arr[-8:, :, :] = 30
    arr[:, :8, :] = 30
    arr[:, -8:, :] = 30
    # Beige gap (same as background)
    arr[8:16, :, :] = [245, 240, 230]
    arr[-16:-8, :, :] = [245, 240, 230]
    arr[:, 8:16, :] = [245, 240, 230]
    arr[:, -16:-8, :] = [245, 240, 230]
    # Inner border
    arr[16:25, :, :] = 60
    arr[-25:-16, :, :] = 60
    arr[:, 16:25, :] = 60
    arr[:, -25:-16, :] = 60
    # Add decorative content (different from background)
    arr[30:-30, 30:-30] = [255, 200, 150]  # Orange-tinted content
    
    img = Image.fromarray(arr)
    
    # Apply smart shape crop
    dpi = 35 * 2.54
    corners = {'bl': 9.5}  # 9.5cm radius
    result = apply_border_only_corners(img, corners, dpi=dpi)
    
    # Analyze result
    r_px = 9.5 * dpi / 2.54  # = 332.5
    metrics = analyze_corner_region(result, 'bl', r_px, '55x84CM')
    
    print(f"  裁切区域像素数: {metrics['outside_count']}")
    print(f"  保留区域像素数: {metrics['inside_count']}")
    print(f"  裁切区域白色比例: {metrics['white_ratio_outside']:.4f}")
    print(f"  保留区域白色比例: {metrics['white_ratio_inside']:.4f}")
    print(f"  裁切区域平均色: {metrics['outside_mean'].astype(int)}")
    print(f"  保留区域平均色: {metrics['inside_mean'].astype(int)}")
    
    # Inside region should preserve original content (not become white)
    success = metrics['white_ratio_inside'] < 0.10
    print(f"  结果: {'✓ 通过' if success else '✗ 失败'}")
    return success


def test_case_3():
    """Test case 3: 路易花坊 40x50CM 4个圆角 1cm半径"""
    print("\n" + "=" * 60)
    print("测试用例 3: 路易花坊 40x50CM 4个圆角 1cm半径")
    print("=" * 60)
    
    # Create test image with simulated design
    w, h = 1400, 1750  # 40cm x 50cm at 35px/cm
    img = Image.new('RGB', (w, h), (245, 240, 230))  # Light beige background
    
    # Add border layers
    arr = np.array(img)
    # Outer dark border
    arr[:8, :, :] = 30
    arr[-8:, :, :] = 30
    arr[:, :8, :] = 30
    arr[:, -8:, :] = 30
    # Beige gap (same as background)
    arr[8:16, :, :] = [245, 240, 230]
    arr[-16:-8, :, :] = [245, 240, 230]
    arr[:, 8:16, :] = [245, 240, 230]
    arr[:, -16:-8, :] = [245, 240, 230]
    # Inner border
    arr[16:25, :, :] = 60
    arr[-25:-16, :, :] = 60
    arr[:, 16:25, :] = 60
    arr[:, -25:-16, :] = 60
    # Add decorative content
    arr[30:-30, 30:-30] = [220, 230, 240]  # Light blue content
    
    img = Image.fromarray(arr)
    
    # Apply smart shape crop
    dpi = 35 * 2.54
    r_cm = 1.0  # 1cm radius
    corners = {'tl': r_cm, 'tr': r_cm, 'bl': r_cm, 'br': r_cm}
    result = apply_border_only_corners(img, corners, dpi=dpi)
    
    # Analyze all four corners
    r_px = 1.0 * dpi / 2.54  # = 35
    all_success = True
    for corner_key in ['tl', 'tr', 'bl', 'br']:
        metrics = analyze_corner_region(result, corner_key, r_px, '40x50CM')
        print(f"\n  角 {corner_key}:")
        print(f"    裁切区域像素数: {metrics['outside_count']}")
        print(f"    裁切区域白色比例: {metrics['white_ratio_outside']:.4f}")
        print(f"    保留区域白色比例: {metrics['white_ratio_inside']:.4f}")
        
        # Check for white squares inside (should not exist)
        if metrics['white_ratio_inside'] > 0.05:
            print(f"    ⚠ 警告: 内部出现白色区域!")
            all_success = False
        
        # Check consistency
        if corner_key == 'tl':
            base_outside = metrics['white_ratio_outside']
            base_inside = metrics['white_ratio_inside']
        else:
            if abs(metrics['white_ratio_outside'] - base_outside) > 0.05:
                print(f"    ⚠ 警告: 与左上角处理不一致!")
                all_success = False
    
    print(f"\n  结果: {'✓ 通过' if all_success else '✗ 失败'}")
    return all_success


if __name__ == '__main__':
    results = []
    results.append(("青芜漫野", test_case_1()))
    results.append(("蔓生花", test_case_2()))
    results.append(("路易花坊", test_case_3()))
    
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    for name, success in results:
        print(f"  {name}: {'✓ 通过' if success else '✗ 失败'}")
    
    all_pass = all(s for _, s in results)
    print(f"\n总体结果: {'✓ 全部通过' if all_pass else '✗ 部分失败'}")
