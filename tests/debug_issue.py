import numpy as np
from PIL import Image, ImageDraw
import sys
sys.path.insert(0, '.')
from core.image_cropper import apply_rounded_corners

img = Image.new('RGB', (200, 150), (200, 200, 200))
d = ImageDraw.Draw(img)
d.rectangle([0, 0, 199, 149], outline=(0, 0, 0), width=4)

try:
    result = apply_rounded_corners(img, {'br': 5.0}, dpi=150, bg_color=(255, 255, 255))
    print("Success!")
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
