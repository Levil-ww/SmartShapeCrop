from PIL import Image, ImageDraw
from core.image_cropper import _get_border_layers_robust, apply_border_only_corners
from core.corner.detection import classify_gap_layers
import numpy as np

def make_qingwumanyeborder():
    w, h = 1200, 900
    green = (60, 90, 45)
    img = Image.new('RGB', (w, h), green)
    draw = ImageDraw.Draw(img)
    margin = 50
    def dotted_rect(x0, y0, x1, y1, color, step=6):
        for x in range(x0, x1, step * 2):
            draw.line([(x, y0), (min(x + step, x1), y0)], fill=color, width=3)
        for x in range(x0, x1, step * 2):
            draw.line([(x, y1), (min(x + step, x1), y1)], fill=color, width=3)
        for y in range(y0, y1, step * 2):
            draw.line([(x0, y), (x0, min(y + step, y1))], fill=color, width=3)
        for y in range(y0, y1, step * 2):
            draw.line([(x1, y), (x1, min(y + step, y1))], fill=color, width=3)
    dotted_rect(margin, margin, w - margin - 1, h - margin - 1, (255, 255, 255))
    dotted_rect(margin + 18, margin + 18, w - margin - 19, h - margin - 19, (255, 255, 255))
    draw.ellipse([margin + 80, margin + 80, margin + 180, margin + 180], fill=(255, 255, 255))
    draw.ellipse([w - margin - 180, h - margin - 180, w - margin - 80, h - margin - 80], fill=(255, 255, 255))
    return img

img = make_qingwumanyeborder()
layers = _get_border_layers_robust(img, (255,255,255))
print('Layers:', layers)
arr = np.array(img, dtype=np.float64)
xs = np.linspace(int(img.width*0.15), int(img.width*0.85), 21, dtype=np.int64).clip(0, img.width-1)
ys = np.linspace(int(img.height*0.15), int(img.height*0.85), 21, dtype=np.int64).clip(0, img.height-1)
gx, gy = np.meshgrid(xs, ys)
content_ref = np.median(arr[gy, gx, :].reshape(-1,3), axis=0)
print('content_ref', content_ref)
print('gap classification', classify_gap_layers(layers, bg_color=(255,255,255), content_ref_arr=content_ref))
