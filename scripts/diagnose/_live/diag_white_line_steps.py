"""逐步隔离白色来源"""
import sys; sys.path.insert(0, r'f:\SmartShapeCrop')
import numpy as np
from PIL import Image, ImageDraw
import core.image_cropper_border as icb
from core.image_cropper import _get_border_layers_robust
from core.config import DEFAULT_BG_COLOR
from core.corner.algorithm import CORNER_ANGLES
from core.corner.sector_render import _redraw_border_on_corner
from core.image_cropper_border import (_corner_sector_has_content,
    _redraw_outer_border_on_corners, _post_cleanup_gap_regions)
from core.image_cropper_mask import (_build_multi_layer_corner_mask,
    _build_border_paint_mask)
from core.corner.detection import detect_nested_rect_layers

w, h = 1200, 800
bg = (235, 220, 195)
img = Image.new('RGB', (w, h), bg)
d = ImageDraw.Draw(img)
d.rectangle([0, 0, w-1, h-1], outline=(120, 100, 80), width=4)
d.rectangle([4, 4, w-5, h-5], outline=(140, 120, 95), width=1)
for x in range(60, w-60, 80):
    for y in range(60, h-60, 80):
        d.ellipse([x-8, y-8, x+8, y+8], outline=(90, 75, 55), width=1)
for x in range(100, w-100, 120):
    d.arc([x-20, h//2-30, x+20, h//2+30], 0, 180, fill=(80, 65, 45), width=2)

dpi = 150
corners = {'tl': 5.0, 'tr': 5.0, 'bl': 5.0, 'br': 5.0}
px_corners = {ck: min(int(round(corners[ck]*dpi/2.54)), min(w, h)//2) for ck in corners}

bl = _get_border_layers_robust(img, DEFAULT_BG_COLOR)
raw_depth = sum(t for _, t in bl)
cp_map = {ck: _corner_sector_has_content(img, ck, px_corners[ck], raw_depth) for ck in px_corners}
nested = detect_nested_rect_layers(img, border_layers=bl) or [(0,0,w-1,h-1)]

print(f'raw_depth={raw_depth}, protect_map={cp_map}')
for ck in ['tl','tr','bl','br']:
    print(f'  {ck}: px_r={px_corners[ck]}')

mask = _build_multi_layer_corner_mask(w, h, px_corners, bl,
    nested_rects=nested, protect_content=cp_map,
    bg_color=DEFAULT_BG_COLOR, content_ref_arr=None)
has_protect = any(cp_map.values()) and raw_depth > 0
vm = _build_border_paint_mask(w, h, px_corners, raw_depth) if has_protect else mask

T_plus = max(raw_depth + 2, 4)

def check_white(arr, label):
    for ck in ['tl','tr','bl','br']:
        cx, cy = {'tl':(px_corners[ck],px_corners[ck]),
                  'tr':(w-px_corners[ck],px_corners[ck]),
                  'bl':(px_corners[ck],h-px_corners[ck]),
                  'br':(w-px_corners[ck],h-px_corners[ck])}[ck]
        ang_min, ang_max = CORNER_ANGLES[ck]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
        dist = np.sqrt((xx-cx)**2 + (yy-cy)**2)
        angle = np.mod(np.degrees(np.arctan2(yy-cy, xx-cx)), 360.0)
        in_ang = (angle >= ang_min) & (angle <= ang_max)
        if ck == 'tl': bz = (xx <= T_plus) | (yy <= T_plus)
        elif ck == 'tr': bz = (((w-1)-xx) <= T_plus) | (yy <= T_plus)
        elif ck == 'bl': bz = (xx <= T_plus) | (((h-1)-yy) <= T_plus)
        else: bz = (((w-1)-xx) <= T_plus) | (((h-1)-yy) <= T_plus)
        
        content = in_ang & bz & (dist <= px_corners[ck] - raw_depth - 2)
        cpixels = arr[content]
        cbright = np.mean(cpixels, axis=1) if len(cpixels) > 0 else np.array([])
        cwhite = np.sum(cbright > 245)
        # 也检查整个 in_ang & bz 区域
        all_bz_content = in_ang & bz & (dist <= px_corners[ck])
        bpixels = arr[all_bz_content]
        bbright = np.mean(bpixels, axis=1) if len(bpixels) > 0 else np.array([])
        bwhite = np.sum(bbright > 245)
        btotal = len(bbright)
        print(f'  {label} {ck}: deep_content_white={cwhite}/{len(cbright)}, all_bz_white={bwhite}/{btotal}')

# Step 0: mask.paste 只做 mask
result = Image.new('RGB', (w, h), DEFAULT_BG_COLOR)
result.paste(img, mask=mask)
check_white(np.array(result), 'S0-mask')

# Step 1: + Step A
result1 = Image.new('RGB', (w, h), DEFAULT_BG_COLOR)
result1.paste(img, mask=mask)
for ck in px_corners:
    _redraw_border_on_corner(result1, ck, px_corners[ck], bl, src_img=img,
        validity_mask=vm, only_outermost=False,
        bg_color=DEFAULT_BG_COLOR, paint_inside_arc=False)
check_white(np.array(result1), 'S1-mask+A')

# Step 2: + Step B
result2 = Image.new('RGB', (w, h), DEFAULT_BG_COLOR)
result2.paste(img, mask=mask)
for ck in px_corners:
    _redraw_border_on_corner(result2, ck, px_corners[ck], bl, src_img=img,
        validity_mask=vm, only_outermost=False,
        bg_color=DEFAULT_BG_COLOR, paint_inside_arc=False)
_redraw_outer_border_on_corners(result2, img, px_corners, bl, vm, DEFAULT_BG_COLOR, skip_outside_arc=True)
check_white(np.array(result2), 'S2-mask+A+B')

# Step 3: + Step C (完整)
result3 = Image.new('RGB', (w, h), DEFAULT_BG_COLOR)
result3.paste(img, mask=mask)
for ck in px_corners:
    _redraw_border_on_corner(result3, ck, px_corners[ck], bl, src_img=img,
        validity_mask=vm, only_outermost=False,
        bg_color=DEFAULT_BG_COLOR, paint_inside_arc=False)
_redraw_outer_border_on_corners(result3, img, px_corners, bl, vm, DEFAULT_BG_COLOR, skip_outside_arc=True)
_post_cleanup_gap_regions(result3, img, px_corners, bl, vm, DEFAULT_BG_COLOR)
check_white(np.array(result3), 'S3-mask+A+B+C')
