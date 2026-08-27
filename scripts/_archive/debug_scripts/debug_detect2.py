from PIL import Image, ImageDraw
from core.image_cropper import _get_border_layers_robust, apply_border_only_corners
from core.corner.detection import classify_gap_layers
import numpy as np

def make_solid_border():
    w, h = 1200, 900
    green = (60, 90, 45)
    img = Image.new('RGB', (w, h), green)
    draw = ImageDraw.Draw(img)
    margin = 50
    # two thin solid white border lines
    draw.rectangle([margin, margin, w - margin - 1, h - margin - 1], outline=(255,255,255), width=3)
    draw.rectangle([margin + 18, margin + 18, w - margin - 19, h - margin - 19], outline=(255,255,255), width=3)
    return img

img = make_solid_border()
layers = _get_border_layers_robust(img, (255,255,255))
print('Layers:', layers)
res = apply_border_only_corners(img, {'tl':3.5,'tr':3.5,'bl':3.5,'br':3.5}, dpi=150, bg_color=(255,255,255))
res.save(r'D:\SmartShapeCrop\debug_output\caseC_solid_current.jpg', quality=95)
