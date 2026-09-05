"""Debug R2: where exactly does _redraw_border_on_corner paint blue?"""
import numpy as np
from PIL import Image
import sys
sys.path.insert(0, '.')
from core.image_cropper import apply_border_only_corners


def debug_r2():
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

    dpi = 150
    corners = {'tl': 2.0, 'tr': 2.0, 'bl': 2.0, 'br': 2.0}
    r_px = int(2.0 / 2.54 * 150)
    print(f"r_px = {r_px}")

    result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)
    result_arr = np.array(result)

    # Show what's at each test region
    for corner, slices in [
        ('tl', (40, 55, 0, 15)),
        ('tr', (40, 55, 545, 560)),
        ('bl', (745, 760, 0, 15)),
        ('br', (745, 760, 545, 560)),
    ]:
        y1, y2, x1, x2 = slices
        region = result_arr[y1:y2, x1:x2]
        print(f"\n[{corner}] region {y1}:{y2},{x1}:{x2}: shape={region.shape}")
        nonwhite = np.where(~((region[:, :, 0] > 250) & (region[:, :, 1] > 250) & (region[:, :, 2] > 250)))
        if len(nonwhite[0]) > 0:
            print(f"  Non-white pixel count: {len(nonwhite[0])}")
            print(f"  Example non-white pixels (y,x,color):")
            for k in range(min(5, len(nonwhite[0]))):
                py, px = nonwhite[0][k], nonwhite[1][k]
                print(f"    arr[{y1+py},{x1+px}] = {tuple(region[py, px])}")
        else:
            print(f"  All white ✓")

    # Now show what's in the corner band
    print("\n--- Corner band analysis (around R=118) ---")
    cx, cy = 118, 118  # tl
    for dist_offset in range(-15, 16):
        dist_px = r_px + dist_offset
        x = cx - dist_px
        y = cy  # angle=0
        if 0 <= x < w and 0 <= y < h:
            print(f"  dist={dist_px} ({dist_offset:+d}): ({x},{y}) = {tuple(result_arr[y, x])}")
    print()
    # Show the entire row y=118 to see what colors are present
    print("Full row y=118 (sample every 10):")
    for x in range(0, w, 10):
        print(f"  arr[118,{x}] = {tuple(result_arr[118, x])}")


if __name__ == '__main__':
    debug_r2()
