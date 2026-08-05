"""Analyze source image border structure"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
import numpy as np

# Load source image
img_path = r'D:\SmartShapeCrop\psd_demo\双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg'
img = Image.open(img_path)
arr = np.array(img)

w, h = img.size
mid_x = w // 2
max_depth = min(200, h // 4)

print(f'Image size: {w} x {h}')
print(f'Scanning bottom edge at x={mid_x}')

# Sample colors from bottom edge inward (upward)
colors = []
for dy in range(max_depth):
    y = h - 1 - dy
    color = tuple(arr[y, mid_x, :])
    colors.append((dy, color))

# Print first 50 samples
print('\nFirst 50 pixel colors from bottom edge:')
for dy, color in colors[:50]:
    print(f'  dy={dy:3d} (y={h-1-dy:4d}): RGB{color}')

# Detect transitions
print('\nDetected color transitions:')
prev_color = colors[0][1]
layer_start = 0
for i in range(1, len(colors)):
    if colors[i][1] != prev_color:
        thickness = i - layer_start
        print(f'  Layer: color={prev_color}, thickness={thickness}px, start_at_dy={layer_start}')
        prev_color = colors[i][1]
        layer_start = i
print(f'  Layer: color={prev_color}, thickness={len(colors)-layer_start}px, start_at_dy={layer_start}')

# Also scan left edge
mid_y = h // 2
max_depth_l = min(200, w // 4)
print(f'\nScanning left edge at y={mid_y}')
colors_l = []
for dx in range(max_depth_l):
    x = dx
    color = tuple(arr[mid_y, x, :])
    colors_l.append((dx, color))

print('\nFirst 50 pixel colors from left edge:')
for dx, color in colors_l[:50]:
    print(f'  dx={dx:3d} (x={dx:4d}): RGB{color}')

print('\nDetected color transitions (left edge):')
prev_color = colors_l[0][1]
layer_start = 0
for i in range(1, len(colors_l)):
    if colors_l[i][1] != prev_color:
        thickness = i - layer_start
        print(f'  Layer: color={prev_color}, thickness={thickness}px, start_at_dx={layer_start}')
        prev_color = colors_l[i][1]
        layer_start = i
print(f'  Layer: color={prev_color}, thickness={len(colors_l)-layer_start}px, start_at_dx={layer_start}')
