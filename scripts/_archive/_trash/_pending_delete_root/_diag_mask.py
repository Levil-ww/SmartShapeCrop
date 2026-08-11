"""Diagnostic script to check the mask merge logic in apply_border_only_corners"""
import numpy as np
from PIL import Image, ImageDraw

w, h = 500, 700
border_w_px = 100
r_px = 50

# 1. Create full rounded corner mask
full_mask = Image.new('L', (w, h), 255)
draw = ImageDraw.Draw(full_mask)
# Carve square (br corner)
sq = (w - r_px, h - r_px, w, h)
draw.rectangle(sq, fill=0)
# Fill back 1/4 circle
bbox = (w - 2*r_px, h - 2*r_px, w, h)
draw.pieslice(bbox, start=0, end=90, fill=255)

# 2. Create inner rectangular mask
inner_mask = Image.new('L', (w, h), 0)
inner_draw = ImageDraw.Draw(inner_mask)
inner_rect = [border_w_px, border_w_px, w - border_w_px, h - border_w_px]
inner_draw.rectangle(inner_rect, fill=255)

# 3. CURRENT merge logic
zero_img = Image.new('L', (w, h), 0)
border_region_mask = Image.composite(zero_img, full_mask, inner_mask)
final_mask_current = Image.composite(inner_mask, border_region_mask, inner_mask)

# CORRECT merge logic
final_mask_correct = Image.composite(full_mask, inner_mask, inner_mask)

arr_current = np.array(final_mask_current)
arr_correct = np.array(final_mask_correct)

print("=" * 60)
print("MASK MERGE DIAGNOSTIC")
print("=" * 60)

# Border region (near br corner)
print("\n--- Border region (near br corner) ---")
print(f"  CURRENT mask: min={arr_current[h-5:h, w-5:w].min()}, max={arr_current[h-5:h, w-5:w].max()}")
print(f"  CORRECT mask: min={arr_correct[h-5:h, w-5:w].min()}, max={arr_correct[h-5:h, w-5:w].max()}")

# Straight border region
print("\n--- Straight border region (bottom edge, away from corner) ---")
y_straight = h - border_w_px // 2
print(f"  CURRENT mask: min={arr_current[y_straight:y_straight+5, 50:55].min()}, max={arr_current[y_straight:y_straight+5, 50:55].max()}")
print(f"  CORRECT mask: min={arr_correct[y_straight:y_straight+5, 50:55].min()}, max={arr_correct[y_straight:y_straight+5, 50:55].max()}")

# Inner region
print("\n--- Inner region (center of image) ---")
print(f"  CURRENT mask: min={arr_current[h//2-5:h//2+5, w//2-5:w//2+5].min()}, max={arr_current[h//2-5:h//2+5, w//2-5:w//2+5].max()}")
print(f"  CORRECT mask: min={arr_correct[h//2-5:h//2+5, w//2-5:w//2+5].min()}, max={arr_correct[h//2-5:h//2+5, w//2-5:w//2+5].max()}")

# Overall difference
diff = np.abs(arr_current.astype(int) - arr_correct.astype(int))
print("\n--- Overall difference ---")
print(f"  Non-zero pixels: {np.count_nonzero(diff)} / {diff.size}")
print(f"  Percentage different: {100 * np.count_nonzero(diff) / diff.size:.2f}%")

# Save visualizations
print("\n--- Saving visualizations ---")
final_mask_current.save('_diag_current_mask.png')
final_mask_correct.save('_diag_correct_mask.png')
print("  Saved _diag_current_mask.png (white=keep, black=cut)")
print("  Saved _diag_correct_mask.png (white=keep, black=cut)")

# Also check the border redraw sector mask
print("\n--- Border redraw sector mask check ---")
import math
from core.corner.sector_render import _build_border_sector_mask

w2, h2 = 500, 700
cx, cy = w2 - 50, h2 - 50  # br corner center
R = 50
d_outer = 0.0
d_inner = 20.0

sector = _build_border_sector_mask(w2, h2, 'br', cx, cy, R, d_outer, d_inner)
sector_arr = sector.astype(np.uint8) * 255
sector_img = Image.fromarray(sector_arr, mode='L')
sector_img.save('_diag_sector_mask.png')

# Check where the sector mask is non-zero
ys, xs = np.where(sector)
if len(ys) > 0:
    print(f"  Sector mask: {len(ys)} non-zero pixels")
    print(f"  Y range: {ys.min()} to {ys.max()}")
    print(f"  X range: {xs.min()} to {xs.max()}")
    print(f"  Center: ({cx}, {cy})")
    print(f"  Distance range from center: {np.sqrt((xs-cx)**2 + (ys-cy)**2).min():.1f} to {np.sqrt((xs-cx)**2 + (ys-cy)**2).max():.1f}")
    print(f"  Expected distance range: {R-d_inner} to {R-d_outer}")
else:
    print("  Sector mask: EMPTY (all zeros!)")

print("\nDone.")
