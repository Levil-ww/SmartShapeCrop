from PIL import Image, ImageDraw
from core.image_cropper import _get_border_layers_robust, apply_border_only_corners
import numpy as np

def make_edge_border():
    w, h = 1200, 900
    green = (60, 90, 45)
    img = Image.new('RGB', (w, h), green)
    draw = ImageDraw.Draw(img)
    # white border at the very edge
    draw.rectangle([0, 0, w-1, h-1], outline=(255,255,255), width=3)
    draw.rectangle([18, 18, w-19, h-19], outline=(255,255,255), width=3)
    # content circles near corners
    draw.ellipse([80,80,180,180], fill=(255,255,255))
    draw.ellipse([w-180,h-180,w-80,h-80], fill=(255,255,255))
    return img

img = make_edge_border()
layers = _get_border_layers_robust(img, (255,255,255))
print('Layers:', layers)
res = apply_border_only_corners(img, {'tl':3.5,'tr':3.5,'bl':3.5,'br':3.5}, dpi=150, bg_color=(255,255,255))
res.save(r'D:\SmartShapeCrop\debug_output\caseC_edge_current.jpg', quality=95)
