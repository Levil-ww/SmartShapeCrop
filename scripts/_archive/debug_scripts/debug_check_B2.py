from PIL import Image
import numpy as np
from core.image_cropper import apply_border_only_corners
from debug_issue_repro import make_sainaishiguang

img = make_sainaishiguang()
w, h = img.size
r = int(round(5.0*150/2.54))
res = apply_border_only_corners(img, {'tl':5.0,'tr':5.0,'bl':5.0,'br':5.0}, dpi=150, bg_color=(255,255,255))
arr = np.array(res)

beige = np.array([245,235,220])
# Count beige pixels outside each corner arc
for name, cx, cy in [('tl', r, r), ('tr', w-r, r), ('bl', r, h-r), ('br', w-r, h-r)]:
    cnt = 0
    for y in range(max(0, cy-r-20), min(h, cy+r+21)):
        for x in range(max(0, cx-r-20), min(w, cx+r+21)):
            dx = x - cx
            dy = y - cy
            d = np.sqrt(dx*dx + dy*dy)
            if d > r:
                if np.linalg.norm(arr[y,x].astype(np.float64) - beige) < 30:
                    cnt += 1
    print(f"{name} beige outside arc:", cnt)

# Sample pixels at corner outside arc
print("TL (10,10):", arr[10,10])
print("TR (w-10,10):", arr[10, w-10])
print("BL (10,h-10):", arr[h-10, 10])
print("BR (w-10,h-10):", arr[h-10, w-10])
