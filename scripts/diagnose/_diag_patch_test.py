"""Test if patch_lshape_cut fails for the actual cut dimensions from the GUI."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
from PIL import Image
from core.lshape_border import patch_lshape_cut, _apply_v13_path, detect_border_v13
from core.geometry import RectShape

src_path = r'C:\Users\Administrator\Desktop\吸水皮革-定制-裁剪有图-黑色大理石;74x188.5CM裁剪有图.jpg'
img = Image.open(src_path)
arr = np.array(img.convert('RGB'))
H, W = arr.shape[:2]

# Simulate the actual GUI cut dimensions
# From the output image: cut area (9536, 0) to (11190, 709)
# So cut_w_px ≈ 1655, cut_h_px ≈ 709

# Test 1: patch_lshape_cut with exact corner positioning
print("=== Test 1: patch_lshape_cut with corner positioning ===")
canvas = arr.copy()
try:
    result = patch_lshape_cut(
        canvas, 'tr',
        9536, 0, 1655, 709,
        edge=12, band=0, color=(0,0,0), black=(0,0,0),
    )
    print("  SUCCESS")
except ValueError as e:
    print(f"  FAILED: {e}")

# Test 2: patch_lshape_cut with W-1 right edge (off by one)
print("\n=== Test 2: patch_lshape_cut with W-1 right edge ===")
canvas = arr.copy()
try:
    result = patch_lshape_cut(
        canvas, 'tr',
        9536, 0, 1654, 709,
        edge=12, band=0, color=(0,0,0), black=(0,0,0),
    )
    print("  SUCCESS")
except ValueError as e:
    print(f"  FAILED: {e}")

# Test 3: _apply_v13_path with actual GUI parameters
print("\n=== Test 3: _apply_v13_path with GUI-like parameters ===")
canvas = arr.copy()
outer_rect = RectShape(0, 0, W, H)
try:
    ok = _apply_v13_path(
        canvas_arr=canvas,
        material_img=img,
        outer_rect=outer_rect,
        cut_corner='tr',
        cut_w_px=1655.0,
        cut_h_px=709.0,
        src_material_img=img,
        scale_x=1.0,
        scale_y=1.0,
        manual_edge_px=12,
        manual_band_px=0,
        manual_band_color=None,
        manual_edge_color=None,
    )
    print(f"  Result: {ok}")
except Exception as e:
    print(f"  EXCEPTION: {type(e).__name__}: {e}")

# Test 4: What if cut_w_px doesn't reach the canvas edge?
print("\n=== Test 4: cut_w_px that doesn't reach canvas edge ===")
canvas = arr.copy()
try:
    ok = _apply_v13_path(
        canvas_arr=canvas,
        material_img=img,
        outer_rect=outer_rect,
        cut_corner='tr',
        cut_w_px=1000.0,  # Doesn't reach right edge
        cut_h_px=709.0,
        src_material_img=img,
        scale_x=1.0,
        scale_y=1.0,
        manual_edge_px=12,
        manual_band_px=0,
        manual_band_color=None,
        manual_edge_color=None,
    )
    print(f"  Result: {ok}")
except Exception as e:
    print(f"  EXCEPTION: {type(e).__name__}: {e}")

# Test 5: Check what cut_w_px values the GUI would produce
print("\n=== Test 5: Simulating GUI cut_w_px calculation ===")
# From image_ops.py:
# cut_w_px = cut_w + (W - _ir_r)  for 'tr' corner
# If inner_rect = (0, 0, W, H) (full canvas), then:
# cut_w_px = cut_w + (W - W) = cut_w
# If inner_rect has margins, e.g., (100, 100, W-100, H-100):
# cut_w_px = cut_w + (W - (W-100)) = cut_w + 100

# Let's say the L-shape cut is 1500x600 in the inner rect
# And inner rect has 0 margins (full canvas)
_ir_x, _ir_y = 0, 0
_ir_r, _ir_b = W, H
cut_w = 1500.0
cut_h = 600.0
cut_w_px = cut_w + (W - _ir_r)  # = 1500 + 0 = 1500
cut_h_px = cut_h + _ir_y  # = 600 + 0 = 600
print(f"  Full canvas inner rect: cut_w_px={cut_w_px}, cut_h_px={cut_h_px}")

# What if inner rect has small margins?
_ir_x, _ir_y = 50, 50
_ir_r, _ir_b = W - 50, H - 50
cut_w_px = cut_w + (W - _ir_r)  # = 1500 + 50 = 1550
cut_h_px = cut_h + _ir_y  # = 600 + 50 = 650
print(f"  50px margin inner rect: cut_w_px={cut_w_px}, cut_h_px={cut_h_px}")

# Test with these values
canvas = arr.copy()
try:
    ok = _apply_v13_path(
        canvas_arr=canvas,
        material_img=img,
        outer_rect=RectShape(0, 0, W, H),
        cut_corner='tr',
        cut_w_px=cut_w_px,
        cut_h_px=cut_h_px,
        src_material_img=img,
        scale_x=1.0,
        scale_y=1.0,
        manual_edge_px=12,
        manual_band_px=0,
        manual_band_color=None,
        manual_edge_color=None,
    )
    print(f"  _apply_v13_path result: {ok}")
except Exception as e:
    print(f"  EXCEPTION: {type(e).__name__}: {e}")
