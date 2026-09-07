"""诊断白色竖线问题"""
import sys; sys.path.insert(0, r'f:\SmartShapeCrop')
import numpy as np
from PIL import Image, ImageDraw
import core.image_cropper_border as icb
from core.image_cropper import _get_border_layers_robust
from core.config import DEFAULT_BG_COLOR
from core.corner.algorithm import CORNER_ANGLES
from core.image_cropper_border import _corner_sector_has_content

# 庄园秘境风格: 米色背景 + 极细边框 + 花纹
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
r = min(int(round(5.0*dpi/2.54)), min(w, h)//2)

bl = _get_border_layers_robust(img, DEFAULT_BG_COLOR)
raw_depth = sum(t for _, t in bl)
protect_map = {ck: _corner_sector_has_content(img, ck, r, raw_depth) for ck in ['tl', 'tr', 'bl', 'br']}
print(f'raw_depth={raw_depth}, r={r}, bl={bl}')
print(f'protect_map={protect_map}')

res = icb.apply_border_only_corners(img, corners, dpi, DEFAULT_BG_COLOR)
a = np.array(res)
T_plus = max(raw_depth + 2, 4)

for ck in ['tl', 'tr', 'bl', 'br']:
    cx, cy = {'tl':(r,r), 'tr':(w-r,r), 'bl':(r,h-r), 'br':(w-r,h-r)}[ck]
    ang_min, ang_max = CORNER_ANGLES[ck]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    dist = np.sqrt((xx-cx)**2 + (yy-cy)**2)
    angle = np.mod(np.degrees(np.arctan2(yy-cy, xx-cx)), 360.0)
    in_ang = (angle >= ang_min) & (angle <= ang_max)
    
    if ck == 'tl': bz = (xx <= T_plus) | (yy <= T_plus)
    elif ck == 'tr': bz = (((w-1)-xx) <= T_plus) | (yy <= T_plus)
    elif ck == 'bl': bz = (xx <= T_plus) | (((h-1)-yy) <= T_plus)
    else: bz = (((w-1)-xx) <= T_plus) | (((h-1)-yy) <= T_plus)
    
    # 内容区 (border_zone 内, dist <= r - raw_depth): 应该是米色
    content = in_ang & bz & (dist <= r - raw_depth - 2)
    cpixels = a[content]
    cbright = np.mean(cpixels, axis=1) if len(cpixels) > 0 else np.array([])
    cwhite = np.sum(cbright > 245)
    
    # 直边边框区 (border_zone 内, dist 在 [r-raw_depth, r]): 应该是深色边框色
    border = in_ang & bz & (dist >= r - raw_depth) & (dist <= r)
    bpixels = a[border]
    bbright = np.mean(bpixels, axis=1) if len(bpixels) > 0 else np.array([])
    bwhite = np.sum(bbright > 245)
    
    # 0°/90° 接缝检查: 直边 (border_zone 内) 的 dist ≈ r 处
    seam = in_ang & bz & (dist >= r - 1) & (dist <= r + 1)
    spixels = a[seam]
    sbright = np.mean(spixels, axis=1) if len(spixels) > 0 else np.array([])
    swhite = np.sum(sbright > 245)
    
    print(f'{ck}: content_white={cwhite}/{len(cbright)}, border_white={bwhite}/{len(bbright)}, seam_white={swhite}/{len(sbright)}')
    if cwhite > 10:
        print(f'  ⚠️ content bright range [{cbright.min():.0f},{cbright.max():.0f}]')
    if bwhite > 5:
        print(f'  ⚠️ border bright range [{bbright.min():.0f},{bbright.max():.0f}]')
    if swhite > 5:
        print(f'  ⚠️ seam bright range [{sbright.min():.0f},{sbright.max():.0f}]')
