"""
Detailed analysis of white pixel positions.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from PIL import Image


def main():
    print("详细分析白色像素位置...")
    
    dpi = 35 * 2.54
    
    # Analyze test_luyi.png
    img = Image.open('test_luyi.png')
    arr = np.array(img)
    h, w = arr.shape[:2]
    
    # 1cm radius
    r_cm = 1.0
    r_px = int(round(r_cm * dpi / 2.54))
    
    print(f"图像尺寸: {w}x{h}")
    print(f"r_px = {r_px}")
    
    for corner_name, (cx, cy) in [
        ('tl', (r_px, r_px)),
        ('tr', (w - r_px, r_px)),
        ('bl', (r_px, h - r_px)),
        ('br', (w - r_px, h - r_px)),
    ]:
        # Get 2R x 2R region around center
        y1 = max(0, cy - r_px * 2)
        y2 = min(h, cy + r_px * 2)
        x1 = max(0, cx - r_px * 2)
        x2 = min(w, cx + r_px * 2)
        
        region = arr[y1:y2, x1:x2]
        white_mask = np.all(region > 240, axis=2)
        
        # Calculate distances
        yy, xx = np.mgrid[y1:y2, x1:x2]
        dx = xx.astype(np.float64) - cx
        dy = yy.astype(np.float64) - cy
        dist = np.sqrt(dx * dx + dy * dy)
        
        inside_arc = dist <= r_px
        outside_arc = dist > r_px
        
        white_inside = np.sum(white_mask & inside_arc)
        white_outside = np.sum(white_mask & outside_arc)
        
        print(f"\n{corner_name} 角 (圆心={cx},{cy}):")
        print(f"  白色像素总数: {white_inside + white_outside}")
        print(f"  弧内白色像素: {white_inside}")
        print(f"  弧外白色像素: {white_outside}")
        
        if white_inside > 0:
            inside_dist = dist[white_mask & inside_arc]
            print(f"  弧内白色像素距离范围: [{inside_dist.min():.1f}, {inside_dist.max():.1f}]")
        
        if white_outside > 0:
            outside_dist = dist[white_mask & outside_arc]
            print(f"  弧外白色像素距离范围: [{outside_dist.min():.1f}, {outside_dist.max():.1f}]")
        
        # Check if white pixels form a square
        white_coords = np.where(white_mask)
        if len(white_coords[0]) > 0:
            y_range = white_coords[0].max() - white_coords[0].min() + 1
            x_range = white_coords[1].max() - white_coords[1].min() + 1
            print(f"  白色像素 y 范围: {y_range}, x 范围: {x_range}")
            print(f"  形状比: {min(y_range, x_range) / max(y_range, x_range):.2f}")


if __name__ == '__main__':
    main()
