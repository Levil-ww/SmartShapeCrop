"""Analyze the actual pixel profile at the edge of the black marble material."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
from PIL import Image

src_path = r'C:\Users\Administrator\Desktop\吸水皮革-定制-裁剪有图-黑色大理石;74x188.5CM裁剪有图.jpg'
img = Image.open(src_path)
arr = np.array(img)
H, W = arr.shape[:2]
print(f"Image: {W}x{H}")

# Scan top edge at multiple columns
print("\n=== Top edge profile (scanning downward at W//4) ===")
col = W // 4
for y in range(0, 80):
    r, g, b = arr[y, col]
    brightness = (int(r) + int(g) + int(b)) / 3
    if y < 30 or y % 5 == 0:
        print(f"  y={y:3d}: RGB=({r:3d},{g:3d},{b:3d}) brightness={brightness:.0f}")

print("\n=== Left edge profile (scanning rightward at H//4) ===")
row = H // 4
for x in range(0, 80):
    r, g, b = arr[row, x]
    brightness = (int(r) + int(g) + int(b)) / 3
    if x < 30 or x % 5 == 0:
        print(f"  x={x:3d}: RGB=({r:3d},{g:3d},{b:3d}) brightness={brightness:.0f}")

# Compute L1 differences between adjacent pixels (top edge)
print("\n=== Top edge L1 diffs (adjacent pixel differences) ===")
col = W // 4
prev = arr[0, col].astype(int)
for y in range(1, 60):
    curr = arr[y, col].astype(int)
    l1 = int(np.sum(np.abs(curr - prev)))
    if y < 30 or y % 5 == 0:
        print(f"  y={y}: L1={l1:3d} {'<-- BREAK' if l1 > 12 else ''}")
    prev = curr

# Compute L1 diffs for left edge
print("\n=== Left edge L1 diffs ===")
row = H // 4
prev = arr[row, 0].astype(int)
for x in range(1, 60):
    curr = arr[row, x].astype(int)
    l1 = int(np.sum(np.abs(curr - prev)))
    if x < 30 or x % 5 == 0:
        print(f"  x={x}: L1={l1:3d} {'<-- BREAK' if l1 > 12 else ''}")
    prev = curr

# Also check what _v13_segv would produce
print("\n=== V13 segment analysis (top edge) ===")
from core.lshape_border import _v13_segv, _v13_pick
top_profile = arr[:400, W // 2]
segs = _v13_segv(top_profile)
print(f"Segments: {segs}")
pick = _v13_pick(segs)
print(f"Pick: {pick}")

print("\n=== V13 segment analysis (left edge) ===")
left_profile = arr[H // 2, :400]
segs_l = _v13_segv(left_profile)
print(f"Segments: {segs_l}")
pick_l = _v13_pick(segs_l)
print(f"Pick: {pick_l}")
