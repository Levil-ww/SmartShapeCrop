"""Debug R2 check 3: where are the white pixels in the arc band?"""
import numpy as np
from PIL import Image
import math, sys
sys.path.insert(0, '.')
from core.image_cropper import apply_border_only_corners


def debug_r2_check3():
    w, h = 600, 800
    img = Image.new('RGB', (w, h), (255, 255, 255))
    arr = np.array(img)
    arr[0:40, :] = (0, 50, 200)
    arr[-40:, :] = (0, 50, 200)
    arr[:, 0:40] = (0, 50, 200)
    arr[:, -40:] = (0, 50, 200)
    arr[55:80, 55:545] = (0, 150, 80)
    arr[-80:-55, 55:545] = (0, 150, 80)
    arr[55:725, 55:80] = (0, 150, 80)
    arr[55:725, -80:-55] = (0, 150, 80)
    for y in range(150, 650, 20):
        for x in range(150, 550, 20):
            if ((x // 20) + (y // 20)) % 2 == 0:
                arr[y:y+10, x:x+10] = (255, 100, 100)
            else:
                arr[y:y+10, x:x+10] = (100, 100, 255)
    img = Image.fromarray(arr, 'RGB')
    bg_color = (255, 255, 255)
    corners = {'tl': 2.0, 'tr': 2.0, 'bl': 2.0, 'br': 2.0}
    dpi = 150
    result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)
    result_arr = np.array(result)

    # Save source and result for visual check
    Image.fromarray(arr).save('.workbuddy/diag/r2_source.png')
    Image.fromarray(result_arr).save('.workbuddy/diag/r2_result.png')
    print("Saved r2_source.png and r2_result.png")

    # Replicate the check 3 logic
    r_px = int(2.0 / 2.54 * 150)
    print(f"r_px={r_px}")
    for corner, (cx_offset, cy_offset) in [
        ('tl', (r_px, r_px)),
        ('tr', (w - r_px - 1, r_px)),
        ('bl', (r_px, h - r_px - 1)),
        ('br', (w - r_px - 1, h - r_px - 1)),
    ]:
        cx, cy = cx_offset, cy_offset
        gap_count = 0
        white_locs = []
        for angle_deg in range(0, 91, 10):
            angle_rad = math.radians(angle_deg)
            for dist_offset in range(-35, 36):
                dist_px = r_px + dist_offset
                if corner == 'tl':
                    x = int(cx - dist_px * math.cos(angle_rad))
                    y = int(cy - dist_px * math.sin(angle_rad))
                elif corner == 'tr':
                    x = int(cx + dist_px * math.cos(angle_rad))
                    y = int(cy - dist_px * math.sin(angle_rad))
                elif corner == 'bl':
                    x = int(cx - dist_px * math.cos(angle_rad))
                    y = int(cy + dist_px * math.sin(angle_rad))
                else:
                    x = int(cx + dist_px * math.cos(angle_rad))
                    y = int(cy + dist_px * math.sin(angle_rad))
                if 0 <= x < w and 0 <= y < h:
                    pixel = result_arr[y, x]
                    if abs(dist_offset) <= 5 and tuple(pixel) == (255, 255, 255):
                        gap_count += 1
                        white_locs.append((angle_deg, dist_offset, x, y, dist_px))
        print(f"\n[{corner}] center=({cx},{cy}) gap_count={gap_count}")
        if gap_count > 0:
            # Show distinct angles with white pixels
            angles_with_white = sorted(set(l[0] for l in white_locs))
            print(f"  White angles: {angles_with_white}")
            for loc in white_locs[:8]:
                print(f"    angle={loc[0]} dist_offset={loc[1]} pos=({loc[2]},{loc[3]}) dist_px={loc[4]}")


if __name__ == '__main__':
    debug_r2_check3()
