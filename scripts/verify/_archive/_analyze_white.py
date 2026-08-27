"""
Analyze white pixel distribution in test images.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
import numpy as np


def analyze_white_distribution(filename):
    """Analyze white pixel distribution in detail."""
    print(f"\n{'='*60}")
    print(f"分析图像: {filename}")
    print('='*60)
    
    img = Image.open(filename)
    arr = np.array(img)
    w, h = img.size
    
    # Find white pixels
    white_mask = np.all(arr > 240, axis=2)
    white_coords = np.where(white_mask)
    n_white = len(white_coords[0])
    
    print(f"  白色像素总数: {n_white}")
    print(f"  白色像素比例: {n_white / (w * h):.6f}")
    
    if n_white > 0:
        # Find bounding box of white pixels
        min_y, max_y = white_coords[0].min(), white_coords[0].max()
        min_x, max_x = white_coords[1].min(), white_coords[1].max()
        print(f"  白色像素边界: y=[{min_y}, {max_y}], x=[{min_x}, {max_x}]")
        print(f"  白色像素区域大小: {max_y - min_y + 1}x{max_x - min_x + 1}")
    
    # Analyze corners in detail
    for corner_name, region in [
        ('tl', (0, 100, 0, 100)),
        ('tr', (0, 100, w-100, w)),
        ('bl', (h-100, h, 0, 100)),
        ('br', (h-100, h, w-100, w)),
    ]:
        y1, y2, x1, x2 = region
        corner = arr[y1:y2, x1:x2]
        white_in_corner = np.all(corner > 240, axis=2)
        n_white_corner = np.sum(white_in_corner)
        total_corner = corner.shape[0] * corner.shape[1]
        print(f"  {corner_name} 角: {n_white_corner}/{total_corner} 白色像素 ({100*n_white_corner/total_corner:.2f}%)")
        
        if n_white_corner > 0:
            # Find white pixel positions within corner
            local_coords = np.where(white_in_corner)
            print(f"    白色像素数量: {len(local_coords[0])}")
            
            # Check if white pixels form a square
            if len(local_coords[0]) > 10:
                # Get unique y and x values
                unique_y = np.unique(local_coords[0])
                unique_x = np.unique(local_coords[1])
                print(f"    唯一 y 值数量: {len(unique_y)}, 唯一 x 值数量: {len(unique_x)}")
                
                # Check if it's roughly square
                y_range = unique_y.max() - unique_y.min() + 1
                x_range = unique_x.max() - unique_x.min() + 1
                print(f"    y 范围: {y_range}, x 范围: {x_range}")
                if y_range > 0 and x_range > 0:
                    ratio = min(y_range, x_range) / max(y_range, x_range)
                    print(f"    形状比: {ratio:.2f} {'(接近正方形)' if ratio > 0.7 else '(不是正方形)'}")
    
    return


def main():
    for filename in ['test_luyi.png']:
        if os.path.exists(filename):
            analyze_white_distribution(filename)


if __name__ == '__main__':
    main()
