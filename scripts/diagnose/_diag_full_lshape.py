from PIL import Image
import numpy as np
import sys
sys.path.insert(0, r'F:\SmartShapeCrop')
from core.lshape_border import detect_border_v13, apply_lshape_border_completion
from core.geometry import RectShape

src = Image.open(r'C:\Users\Administrator\Desktop\吸水皮革-定制-裁剪有图-黑色大理石;74x188.5CM裁剪有图.jpg')
arr = np.array(src.convert('RGB'))
H, W = arr.shape[:2]
print(f'Source image: {W}x{H}')

# Test V13 detection
v13 = detect_border_v13(src)
if v13:
    edge, band, black_color, band_color = v13
    print(f'\nV13 detection:')
    print(f'  edge={edge}px, band={band}px')
    print(f'  black_color={black_color}, band_color={band_color}')

# Simulate a simple L-shape cut (top-right corner)
# Create a canvas with the image
canvas = arr.copy()

# Define outer rect (full image)
outer_rect = RectShape(0, 0, W, H)

# Cut 500x500 from top-right
cut_corner = 'tr'
cut_w_px = 500
cut_h_px = 500

print(f'\nApplying L-shape border completion:')
print(f'  corner={cut_corner}, cut={cut_w_px}x{cut_h_px}px')

# Apply border completion
result = apply_lshape_border_completion(
    canvas_arr=canvas,
    material_img=src,
    outer_rect=outer_rect,
    cut_corner=cut_corner,
    cut_w_px=cut_w_px,
    cut_h_px=cut_h_px,
    dpi=150,
    bg_color=(255, 255, 255),
    src_material_img=src,
    scale_x=1.0,
    scale_y=1.0,
)

print(f'\nBorder completion result: {result}')

# Check the border thickness in the output
# Sample the vertical cut edge (should be at x=W-cut_w_px)
cut_x = W - cut_w_px
print(f'\nVertical cut edge at x={cut_x}:')
# Sample a column just inside the cut edge
sample_col = canvas[100:200, cut_x-20:cut_x]
print(f'  Sample region shape: {sample_col.shape}')
# Find where the black border ends
for i in range(20):
    pixel = canvas[150, cut_x-1-i]
    print(f'  [{cut_x-1-i}] RGB={tuple(pixel)}')
