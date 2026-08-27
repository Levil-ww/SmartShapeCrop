from PIL import Image, ImageDraw
from core.image_cropper import (
    apply_border_only_corners,
    _get_border_layers_robust,
    _estimate_outer_background,
    _corner_sector_has_content,
)

w, h = 1200, 1000
img = Image.new('RGB', (w, h), (0, 0, 0))
draw = ImageDraw.Draw(img)
margin = 60
draw.rectangle([margin, margin, w - margin - 1, h - margin - 1], fill=(255, 255, 255))
border = 12
draw.rectangle([margin, margin, w - margin - 1, h - margin - 1], outline=(0, 0, 0), width=border)

layers = _get_border_layers_robust(img, (255, 255, 255))
print("raw layers:", layers)
print("outer_bg:", _estimate_outer_background(img))

# Simulate filter logic
if layers:
    import numpy as np
    outer_bg = _estimate_outer_background(img)
    first_color, first_t = layers[0]
    color_dist = float(np.linalg.norm(np.array(first_color, dtype=np.float64) - np.array(outer_bg, dtype=np.float64)))
    threshold = max(30, int(min(img.size) * 0.03))
    print(f"dist={color_dist:.2f}, threshold={threshold}, first_t={first_t}")
    if color_dist < 25.0 and first_t > threshold:
        print("would filter first layer")
    else:
        print("would NOT filter")

r = int(round(1.5*150/2.54))
print("corner has content bl:", _corner_sector_has_content(img, 'bl', r, 73))
print("corner has content br:", _corner_sector_has_content(img, 'br', r, 73))

res = apply_border_only_corners(img, {'bl':1.5, 'br':1.5}, dpi=150, bg_color=(255,255,255))
res.save("debug_output/caseA_filter_test.jpg", quality=95)
print("saved")
