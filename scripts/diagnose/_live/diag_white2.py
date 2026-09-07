import sys; sys.path.insert(0, r'f:\SmartShapeCrop')
import numpy as np
from PIL import Image, ImageDraw
import core.image_cropper_border as icb
from core.image_cropper import _get_border_layers_robust
from core.config import DEFAULT_BG_COLOR
from core.corner.algorithm import CORNER_ANGLES
from core.image_cropper_border import _estimate_outer_background

w,h=1200,800
bg=(235,220,195)
img=Image.new('RGB',(w,h),bg);d=ImageDraw.Draw(img)
d.rectangle([0,0,w-1,h-1],outline=(120,100,80),width=4)
d.rectangle([4,4,w-5,h-5],outline=(140,120,95),width=1)
for x in range(60,w-60,80):
    for y in range(60,h-60,80):
        d.ellipse([x-8,y-8,x+8,y+8],outline=(90,75,55),width=1)

res=icb.apply_border_only_corners(img,{'tl':5.0,'tr':5.0,'bl':5.0,'br':5.0},150,DEFAULT_BG_COLOR)
a=np.array(res)

bl=_get_border_layers_robust(img,DEFAULT_BG_COLOR)
outer_bg=_estimate_outer_background(img)
first_color,first_t=bl[0]
dist_outer=float(np.linalg.norm(np.array(first_color)-np.array(outer_bg)))
threshold=max(30,int(min(w,h)*0.03))
outer_bg_mean=float(np.mean(outer_bg))
if dist_outer<25.0 and first_t>threshold and outer_bg_mean>200:
    bl=bl[1:]
raw_depth=bl[0][1] if bl else 0
dpi=150
corners={'tl':5.0,'tr':5.0,'bl':5.0,'br':5.0}
px_corners={ck:min(int(round(corners[ck]*dpi/2.54)),min(w,h)//2) for ck in corners}
T_plus=max(raw_depth+2,4)

print(f'raw_depth={raw_depth}')
for ck in ['tl','tr','bl','br']:
    r=px_corners[ck]
    cx,cy={'tl':(r,r),'tr':(w-r,r),'bl':(r,h-r),'br':(w-r,h-r)}[ck]
    ang_min,ang_max=CORNER_ANGLES[ck]
    yy,xx=np.mgrid[0:h,0:w].astype(np.float64)
    dist=np.sqrt((xx-cx)**2+(yy-cy)**2)
    angle=np.mod(np.degrees(np.arctan2(yy-cy,xx-cx)),360.0)
    in_ang=(angle>=ang_min)&(angle<=ang_max)
    if ck=='tl': bz=(xx<=T_plus)|(yy<=T_plus)
    elif ck=='tr': bz=(((w-1)-xx)<=T_plus)|(yy<=T_plus)
    elif ck=='bl': bz=(xx<=T_plus)|(((h-1)-yy)<=T_plus)
    else: bz=(((w-1)-xx)<=T_plus)|(((h-1)-yy)<=T_plus)
    
    inner=in_ang&bz&(dist<=r-raw_depth)
    ib=np.mean(a[inner],axis=1) if np.sum(inner)>0 else np.array([])
    iwhite=np.sum(ib>245)
    border=in_ang&bz&(dist>r-raw_depth)&(dist<=r)
    bb=np.mean(a[border],axis=1) if np.sum(border)>0 else np.array([])
    bwhite=np.sum(bb>245)
    outside=in_ang&(~bz)&(dist>r)
    ob=np.mean(a[outside],axis=1) if np.sum(outside)>0 else np.array([])
    odark=np.sum(ob<200)
    
    flag='!' if iwhite>10 or odark>5 else '.'
    print(f'{flag} {ck}: inner_white={iwhite}/{len(ib)}, border_white={bwhite}/{len(bb)}, out_dark={odark}/{len(ob)}')
    if iwhite>10: print(f'  inner [{ib.min():.0f},{ib.max():.0f}]')
    if odark>5: print(f'  out [{ob.min():.0f},{ob.max():.0f}]')
