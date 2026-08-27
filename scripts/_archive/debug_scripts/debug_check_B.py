from PIL import Image
import numpy as np
from core.image_cropper import apply_border_only_corners, _get_border_layers_robust, _estimate_outer_background, _corner_sector_has_content

# Load case B source
from debug_issue_repro import make_sainaishiguang
img = make_sainaishiguang()
w, h = img.size

layers = _get_border_layers_robust(img, (255,255,255))
print("raw layers:", layers)
outer_bg = _estimate_outer_background(img)
print("outer_bg:", outer_bg)
# simulate filter
first_color, first_t = layers[0]
color_dist = float(np.linalg.norm(np.array(first_color, dtype=np.float64) - np.array(outer_bg, dtype=np.float64)))
threshold = max(30, int(min(img.size) * 0.03))
print(f"dist={color_dist:.2f}, threshold={threshold}, first_t={first_t}")
if color_dist < 25.0 and first_t > threshold:
    layers = layers[1:]
print("filtered layers:", layers)

r = int(round(5.0*150/2.54))
for ck in ['tl','tr','bl','br']:
    print(ck, _corner_sector_has_content(img, ck, r, sum(t for _,t in layers)))

res = apply_border_only_corners(img, {'tl':5.0,'tr':5.0,'bl':5.0,'br':5.0}, dpi=150, bg_color=(255,255,255))
arr = np.array(res)

# Check top-left corner outside arc
print("TL outside arc samples:")
for y in [10, 20, 30, 40]:
    for x in [10, 20, 30, 40]:
        d = np.sqrt(x*x + y*y)
        if d > r:
            print(f"  ({x},{y}) d={d:.1f} color={arr[y,x]}")

# Check if any beige pixels remain in corner outside arc
beige = np.array([245,235,220])
beige_count = 0
for y in range(0, r+50):
    for x in range(0, r+50):
        d = np.sqrt(x*x + y*y)
        if d > r and y < h and x < w:
            if np.linalg.norm(arr[y,x].astype(np.float64) - beige) < 30:
                beige_count += 1
print("beige pixels outside TL arc:", beige_count)

res.save("debug_output/caseB_check.jpg", quality=95)
print("saved caseB_check.jpg")
