"""逐步隔离 - 用正确的参数"""
import sys; sys.path.insert(0, r'f:\SmartShapeCrop')
import numpy as np
from PIL import Image, ImageDraw
import core.image_cropper_border as icb
from core.image_cropper import _get_border_layers_robust
from core.config import DEFAULT_BG_COLOR
from core.corner.algorithm import CORNER_ANGLES
from core.corner.sector_render import _redraw_border_on_corner
from core.image_cropper_border import (_corner_sector_has_content,
    _redraw_outer_border_on_corners, _post_cleanup_gap_regions,
    _estimate_outer_background)
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
corners_cm = {'tl': 5.0, 'tr': 5.0, 'bl': 5.0, 'br': 5.0}
px_corners = {ck: min(int(round(corners_cm[ck]*dpi/2.54)), min(w, h)//2) for ck in corners_cm}

# 模拟 apply_border_only_corners 的边框层过滤逻辑
border_layers = _get_border_layers_robust(img, DEFAULT_BG_COLOR)
outer_bg = _estimate_outer_background(img)
first_color, first_t = border_layers[0]
dist_outer_bg = float(np.linalg.norm(np.array(first_color) - np.array(outer_bg)))
threshold = max(30, int(min(w, h) * 0.03))
outer_bg_mean = float(np.mean(outer_bg))
if dist_outer_bg < 25.0 and first_t > threshold and outer_bg_mean > 200:
    border_layers = border_layers[1:]
print(f'border_layers={border_layers} (filtered)')

outermost_layers = [border_layers[0]] if border_layers else []
raw_depth = outermost_layers[0][1] if outermost_layers else 0
print(f'raw_depth={raw_depth}')

# 强制 protect=True（apply_border_only_corners 的逻辑）
cp_map = {ck: True for ck in px_corners}

nested = detect_nested_rect_layers(img, border_layers=border_layers) or [(0,0,w-1,h-1)]

# content_ref_arr
img_arr = np.array(img, dtype=np.float64)
xs = np.linspace(int(w*0.15), int(w*0.85), 21).clip(0, w-1)
ys = np.linspace(int(h*0.15), int(h*0.85), 21).clip(0, h-1)
gx, gy = np.meshgrid(xs, ys)
content_ref_arr = np.median(img_arr[gy, gx, :].reshape(-1, 3), axis=0)

mask = _build_multi_layer_corner_mask(w, h, px_corners, outermost_layers,
    nested_rects=nested, protect_content=cp_map,
    bg_color=DEFAULT_BG_COLOR, content_ref_arr=content_ref_arr)
vm = _build_border_paint_mask(w, h, px_corners, raw_depth) if raw_depth > 0 else mask

T_plus = max(raw_depth + 2, 4)

def check_white(arr, label):
    print(f'\n=== {label} ===')
    for ck in ['tl','tr','bl','br']:
        r_px = px_corners[ck]
        cx, cy = {'tl':(r_px,r_px), 'tr':(w-r_px,r_px),
                  'bl':(r_px,h-r_px), 'br':(w-r_px,h-r_px)}[ck]
        ang_min, ang_max = CORNER_ANGLES[ck]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
        dist = np.sqrt((xx-cx)**2 + (yy-cy)**2)
        angle = np.mod(np.degrees(np.arctan2(yy-cy, xx-cx)), 360.0)
        in_ang = (angle >= ang_min) & (angle <= ang_max)
        if ck == 'tl': bz = (xx <= T_plus) | (yy <= T_plus)
        elif ck == 'tr': bz = (((w-1)-xx) <= T_plus) | (yy <= T_plus)
        elif ck == 'bl': bz = (xx <= T_plus) | (((h-1)-yy) <= T_plus)
        else: bz = (((w-1)-xx) <= T_plus) | (((h-1)-yy) <= T_plus)
        
        # 白色像素检测 (border_zone 内)
        bz_region = in_ang & bz & (dist <= r_px)
        bpixels = arr[bz_region]
        bbright = np.mean(bpixels, axis=1) if len(bpixels) > 0 else np.array([])
        bwhite = np.sum(bbright > 245)
        btotal = len(bbright)
        
        # 分区间看
        inner = in_ang & bz & (dist <= r_px - raw_depth)
        ib = np.mean(arr[inner], axis=1) if np.sum(inner) > 0 else np.array([])
        iwhite = np.sum(ib > 245)
        
        border = in_ang & bz & (dist > r_px - raw_depth) & (dist <= r_px)
        bb = np.mean(arr[border], axis=1) if np.sum(border) > 0 else np.array([])
        bwhite2 = np.sum(bb > 245)
        
        outside = in_ang & (~bz) & (dist > r_px)
        ob = np.mean(arr[outside], axis=1) if np.sum(outside) > 0 else np.array([])
        odark = np.sum(ob < 200)
        
        flag = '⚠️' if iwhite > 10 or odark > 5 else '✅'
        print(f'  {flag} {ck}: inner_white={iwhite}/{len(ib)}, border_white={bwhite2}/{len(bb)}, out_nonbz_dark={odark}/{len(ob)}')
        if iwhite > 10:
            print(f'    inner bright range [{ib.min():.0f},{ib.max():.0f}]')
        if odark > 5:
            print(f'    out_nonbz bright range [{ob.min():.0f},{ob.max():.0f}]')

# Step 0: mask.paste
result = Image.new('RGB', (w, h), DEFAULT_BG_COLOR)
result.paste(img, mask=mask)
check_white(np.array(result), 'S0-mask')

# Step 1: + Step A (only_outermost=True, paint_inside_arc=False)
result1 = Image.new('RGB', (w, h), DEFAULT_BG_COLOR)
result1.paste(img, mask=mask)
for ck in px_corners:
    _redraw_border_on_corner(result1, ck, px_corners[ck], outermost_layers,
        src_img=img, validity_mask=vm, only_outermost=True,
        bg_color=DEFAULT_BG_COLOR, paint_inside_arc=False)
check_white(np.array(result1), 'S1-mask+A')

# Step 2: + Step B
result2 = Image.new('RGB', (w, h), DEFAULT_BG_COLOR)
result2.paste(img, mask=mask)
for ck in px_corners:
    _redraw_border_on_corner(result2, ck, px_corners[ck], outermost_layers,
        src_img=img, validity_mask=vm, only_outermost=True,
        bg_color=DEFAULT_BG_COLOR, paint_inside_arc=False)
if px_corners and border_layers:
    _redraw_outer_border_on_corners(result2, img, px_corners, outermost_layers, vm,
        DEFAULT_BG_COLOR, skip_outside_arc=True, only_outermost=True)
check_white(np.array(result2), 'S2-mask+A+B')

# Step 3: + Step C
result3 = Image.new('RGB', (w, h), DEFAULT_BG_COLOR)
result3.paste(img, mask=mask)
for ck in px_corners:
    _redraw_border_on_corner(result3, ck, px_corners[ck], outermost_layers,
        src_img=img, validity_mask=vm, only_outermost=True,
        bg_color=DEFAULT_BG_COLOR, paint_inside_arc=False)
if px_corners and border_layers:
    _redraw_outer_border_on_corners(result3, img, px_corners, outermost_layers, vm,
        DEFAULT_BG_COLOR, skip_outside_arc=True, only_outermost=True)
    _post_cleanup_gap_regions(result3, img, px_corners, outermost_layers, vm, DEFAULT_BG_COLOR)
check_white(np.array(result3), 'S3-mask+A+B+C')

# Step 4: 完整 apply_border_only_corners
res_full = icb.apply_border_only_corners(img, corners_cm, dpi, DEFAULT_BG_COLOR)
check_white(np.array(res_full), 'S4-full')
