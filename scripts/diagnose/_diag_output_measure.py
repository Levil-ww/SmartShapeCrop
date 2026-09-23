"""Measure the actual border thickness in the user's output image."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
from PIL import Image

out_path = r'C:\Users\Administrator\Desktop\智能裁剪设计器\4-L形挖角-单个边角测试输出\10-吸水皮革-定制-裁剪有图-黑色大理石;74x188.5CM裁剪有图.jpg'
img = Image.open(out_path)
arr = np.array(img.convert('RGB'))
H, W = arr.shape[:2]
print(f"Output image: {W}x{H}")

# Find the white cut area (top-right corner)
# Scan from top-right to find where white starts
print("\n=== Finding cut area ===")
# Scan top row from right to left
for x in range(W-1, max(0, W-3000), -1):
    r, g, b = arr[10, x]
    brightness = (int(r) + int(g) + int(b)) / 3
    if brightness > 200:
        print(f"  White cut starts at x={x} on top row (y=10)")
        cut_right = x
        break
else:
    cut_right = W

# Scan right column from top to bottom
for y in range(0, min(H, 2000)):
    r, g, b = arr[y, W-10]
    brightness = (int(r) + int(g) + int(b)) / 3
    if brightness > 200:
        print(f"  White cut starts at y={y} on right column (x={W-10})")
        cut_top = y
        break
else:
    cut_top = 0

# Find the cut corner (bottom-left of white area)
# Scan down from cut_top at x=cut_right-100 to find bottom of white
for y in range(cut_top, min(H, cut_top + 2000)):
    r, g, b = arr[y, cut_right - 100]
    brightness = (int(r) + int(g) + int(b)) / 3
    if brightness < 100:
        print(f"  White cut ends at y={y} (height={y - cut_top})")
        cut_bottom = y
        break
else:
    cut_bottom = H

# Scan left from cut_right at y=cut_top+100 to find left of white
for x in range(cut_right, max(0, cut_right - 5000), -1):
    r, g, b = arr[cut_top + 100, x]
    brightness = (int(r) + int(g) + int(b)) / 3
    if brightness < 100:
        print(f"  White cut ends at x={x} (width={cut_right - x})")
        cut_left = x
        break
else:
    cut_left = 0

print(f"\nCut area: ({cut_left}, {cut_top}) to ({cut_right}, {cut_bottom})")
print(f"Cut size: {cut_right - cut_left} x {cut_bottom - cut_top}")

# Now measure the black border along the vertical cut edge (x = cut_left)
# Scan horizontally from cut_left outward (to the left) at mid-height of cut
mid_y = (cut_top + cut_bottom) // 2
print(f"\n=== Measuring border at vertical cut edge (y={mid_y}) ===")
border_thickness_left = 0
for x in range(cut_left - 1, max(0, cut_left - 200), -1):
    r, g, b = arr[mid_y, x]
    brightness = (int(r) + int(g) + int(b)) / 3
    if brightness > 50:
        print(f"  Border ends at x={x} (thickness={cut_left - 1 - x}px)")
        border_thickness_left = cut_left - 1 - x
        break
    if x == cut_left - 1:
        border_thickness_left += 1

# Also scan from cut_left inward (to the right, into the white area)
print(f"\n=== Scanning into cut area from vertical edge ===")
for x in range(cut_left, min(W, cut_left + 50)):
    r, g, b = arr[mid_y, x]
    brightness = (int(r) + int(g) + int(b)) / 3
    if x < cut_left + 20 or x % 5 == 0:
        print(f"  x={x}: RGB=({r:3d},{g:3d},{b:3d}) brightness={brightness:.0f}")

# Measure the black border along the horizontal cut edge (y = cut_bottom)
# Scan vertically from cut_bottom downward at mid-width of cut
mid_x = (cut_left + cut_right) // 2
print(f"\n=== Measuring border at horizontal cut edge (x={mid_x}) ===")
for y in range(cut_bottom, min(H, cut_bottom + 200)):
    r, g, b = arr[y, mid_x]
    brightness = (int(r) + int(g) + int(b)) / 3
    if brightness > 50:
        print(f"  Border ends at y={y} (thickness={y - cut_bottom}px)")
        break

# Also check the original material's edge (top of image) for comparison
print(f"\n=== Original material top edge profile (x={W//4}) ===")
for y in range(0, 60):
    r, g, b = arr[y, W//4]
    brightness = (int(r) + int(g) + int(b)) / 3
    if y < 20 or y % 5 == 0:
        print(f"  y={y:3d}: RGB=({r:3d},{g:3d},{b:3d}) brightness={brightness:.0f}")
