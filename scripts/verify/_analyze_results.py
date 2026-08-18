"""
Detailed analysis of test results.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
import numpy as np


def analyze_image(filename):
    """Analyze an image in detail."""
    print(f"\n分析图像: {filename}")
    img = Image.open(filename)
    arr = np.array(img)
    w, h = img.size
    
    print(f"  尺寸: {w}x{h}")
    print(f"  平均色: {arr.mean(axis=(0, 1)).astype(int)}")
    print(f"  最小色: {arr.min(axis=(0, 1))}")
    print(f"  最大色: {arr.max(axis=(0, 1))}")
    
    # Check for white regions
    white_mask = np.all(arr > 240, axis=2)
    white_ratio = np.sum(white_mask) / (w * h)
    print(f"  白色像素比例: {white_ratio:.4f}")
    
    # Check corners
    for corner_name, (cx, cy) in [('tl', (0, 0)), ('tr', (w, 0)), ('bl', (0, h)), ('br', (w, h))]:
        # Get corner region (50x50 pixels from corner)
        size = min(50, min(w, h) // 4)
        if corner_name == 'tl':
            region = arr[:size, :size]
        elif corner_name == 'tr':
            region = arr[:size, w-size:]
        elif corner_name == 'bl':
            region = arr[h-size:, :size]
        else:
            region = arr[h-size:, w-size:]
        
        white_in_corner = np.all(region > 240, axis=2)
        corner_white_ratio = np.sum(white_in_corner) / (size * size)
        print(f"  {corner_name} 角白色比例: {corner_white_ratio:.4f}")
    
    return


def main():
    # Analyze test images
    for filename in ['test_qingwu.png', 'test_mansheng.png', 'test_luyi.png']:
        if os.path.exists(filename):
            analyze_image(filename)
        else:
            print(f"\n图像不存在: {filename}")


if __name__ == '__main__':
    main()
