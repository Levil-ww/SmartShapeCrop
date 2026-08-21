"""
诊断花漾之约白色扇形角问题：追踪边界角度像素的处理流程
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
from PIL import Image
from core.image_cropper import apply_border_only_corners
from core.corner.algorithm import CORNER_ANGLES

def diagnose_huayang():
    """详细诊断花漾之约圆角处理"""
    print("=" * 70)
    print("诊断花漾之约白色扇形角根因")
    print("=" * 70)
    
    # 创建模拟花漾之约的图片
    w, h = 1134, 1701  # 40cm x 60cm @ 72dpi
    bg_color = (255, 255, 255)
    
    img = Image.new('RGB', (w, h), bg_color)
    arr = np.array(img)
    
    # 黑色外边框 (30px)
    arr[:30, :, :] = (15, 15, 15)
    arr[-30:, :, :] = (15, 15, 15)
    arr[:, :30, :] = (15, 15, 15)
    arr[:, -30:, :] = (15, 15, 15)
    
    # 白色点状间隙 (8px间隔)
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
    
    print(f"  r_px = {r_px}")
    
    # 分析原始图像的白色点分布
    print("\n--- 原始图像分析 ---")
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
        
        # 边界角度带 (ang_max ± 2度)
        boundary_band = valid_angle & (angle >= ang_max - 2) & (angle <= ang_max + 2)
        
        # 原始图像中的白色像素
        orig_white = np.all(arr > 245, axis=2)
        orig_white_in_boundary = orig_white & boundary_band
        
        # 分析边界带内的白色像素位置分布
        if np.any(orig_white_in_boundary):
            bw_coords = np.where(orig_white_in_boundary)
            bw_dists = dist[bw_coords]
            bw_depths = r_px - bw_dists
            
            # 分类：外部/内部弧
            outside_arc = bw_dists > r_px
            inside_arc = bw_dists <= r_px
            
            # 按深度分类
            in_border = (bw_depths >= 0) & (bw_depths < 30)  # 在边框厚度内
            in_gap_region = (bw_depths >= 30) & (bw_depths < 50)  # 间隙区域
            in_content = bw_depths >= 50  # 内容区域
            
            print(f"\n  {ck}角 (ang_min={ang_min}, ang_max={ang_max}):")
            print(f"    原始边界带白色像素数: {len(bw_coords[0])}")
            print(f"      - 弧外侧(dist>r): {np.sum(outside_arc)}")
            print(f"      - 弧内侧(dist<=r): {np.sum(inside_arc)}")
            print(f"        · 边框区域(depth<30): {np.sum(in_border)}")
            print(f"        · 间隙区域(30<=depth<50): {np.sum(in_gap_region)}")
            print(f"        · 内容区域(depth>=50): {np.sum(in_content)}")
        else:
            print(f"\n  {ck}角: 原始边界带无白色像素")
    
    # 运行圆角裁剪
    print("\n--- 运行圆角裁剪 ---")
    result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)
    result_arr = np.array(result)
    
    print("\n--- 结果图像分析 ---")
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
        
        # 边界角度带 (ang_max ± 2度)
        boundary_band = valid_angle & (angle >= ang_max - 2) & (angle <= ang_max + 2)
        
        # 结果中的白色像素
        result_white = np.all(result_arr > 245, axis=2)
        result_white_in_boundary = result_white & boundary_band
        
        if np.any(result_white_in_boundary):
            rw_coords = np.where(result_white_in_boundary)
            rw_dists = dist[rw_coords]
            rw_depths = r_px - rw_dists
            
            # 分类
            outside_arc = rw_dists > r_px
            inside_arc = rw_dists <= r_px
            
            in_border = (rw_depths >= 0) & (rw_depths < 30)
            in_gap_region = (rw_depths >= 30) & (rw_depths < 50)
            in_content = rw_depths >= 50
            
            # 检查这些像素是否是背景色
            bg_arr = np.array(bg_color)
            rw_pixels = result_arr[rw_coords[0], rw_coords[1]].astype(np.float64)
            dist_to_bg = np.sqrt(np.sum((rw_pixels - bg_arr.reshape(1, 3)) ** 2, axis=1))
            is_bg_like = dist_to_bg < 10.0
            
            print(f"\n  {ck}角:")
            print(f"    结果边界带白色像素数: {len(rw_coords[0])}")
            print(f"      - 弧外侧: {np.sum(outside_arc)}")
            print(f"      - 弧内侧: {np.sum(inside_arc)}")
            print(f"        · 边框区域: {np.sum(in_border)}")
            print(f"        · 间隙区域: {np.sum(in_gap_region)}")
            print(f"        · 内容区域: {np.sum(in_content)}")
            print(f"      - 接近背景色(dist<10): {np.sum(is_bg_like)}")
            
            # 检查是否在beyond_arc区域
            beyond_region = valid_angle & (dist > r_px)
            white_in_beyond = result_white & beyond_region
            print(f"      - beyond_arc区域白色像素: {np.sum(white_in_beyond)}")
            
            # 检查角度精确位置
            rw_angles = angle[rw_coords]
            print(f"      - 白色像素角度范围: [{rw_angles.min():.1f}, {rw_angles.max():.1f}]")
            print(f"      - ang_max = {ang_max}")
            print(f"      - 距ang_max最近角度: {ang_max - rw_angles.min():.1f}度 (diff={abs(rw_angles.max()-ang_max):.1f})")
            
            # 检查是否在valid_angle范围外
            out_of_valid = rw_angles < ang_min
            print(f"      - 在valid_angle范围外(ang<{ang_min}): {np.sum(out_of_valid)}")
        else:
            print(f"\n  {ck}角: 结果边界带无白色像素 ✓")
    
    # 检查valid_angle范围外的白色像素
    print("\n--- 角度范围外白色像素检查 ---")
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
        
        # 角度范围外的白色像素
        out_of_angle = ~valid_angle
        white_outside = np.all(result_arr > 245, axis=2) & out_of_angle & (dist <= r_px + 5)
        
        # 检查是否在 ang_max 附近(但超出范围)
        near_max = (angle > ang_max) & (angle <= ang_max + 5) & out_of_angle
        white_near_max = np.all(result_arr > 245, axis=2) & near_max
        
        if np.any(white_near_max):
            nm_coords = np.where(white_near_max)
            print(f"\n  {ck}角: ang_max附近角度范围外白色像素 = {len(nm_coords[0])}")
            nm_angles = angle[nm_coords]
            print(f"    角度范围: [{nm_angles.min():.1f}, {nm_angles.max():.1f}]")
            print(f"    距ang_max: ang_max({ang_max}) vs max_angle({nm_angles.max():.1f}), diff={nm_angles.max()-ang_max:.1f}度")
            nm_dists = dist[nm_coords]
            print(f"    距离范围: [{nm_dists.min():.1f}, {nm_dists.max():.1f}]")
            print(f"    距arc边界: [{r_px - nm_dists.min():.1f}, {r_px - nm_dists.max():.1f}]")
        else:
            print(f"\n  {ck}角: ang_max附近角度范围外无白色像素 ✓")
    
    # 保存结果图像以供查看
    result.save('d:/SmartShapeCrop/scripts/verify/huayang_result.png')
    print("\n  结果图像已保存: huayang_result.png")

if __name__ == '__main__':
    diagnose_huayang()
