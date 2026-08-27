from PIL import Image
import numpy as np
from core.image_cropper import _corner_sector_has_content, _estimate_outer_background
from debug_issue_repro import make_sainaishiguang

img = make_sainaishiguang()
w, h = img.size
r = int(round(5.0*150/2.54))
print("r:", r, "img size:", w, h)
for ck in ['tl','tr','bl','br']:
    result = _corner_sector_has_content(img, ck, r, 10)
    print(ck, result)
