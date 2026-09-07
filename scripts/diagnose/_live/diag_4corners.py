"""验证 near_edge 硬编码是否会导致四角白色空隙"""
import sys; sys.path.insert(0, r'f:\SmartShapeCrop')
import numpy as np
from PIL import Image, ImageDraw
import core.image_cropper_border as icb
from core.image_cropper import _get_border_layers_robust
from core.config import DEFAULT_BG_COLOR
from core.corner.algorithm import CORNER_ANGLES
from core.image_cropper_border import _corner_sector_has_content

w,h=600,400
img=Image.new('RGB',(w,h),(240,230,200));d=ImageDraw.Draw(img)
d.rectangle([0,0,w-1,h-1],outline=(100,80,55),width=12)
# 内容紧贴四角直边
d.rectangle([5,5,60,60],fill=(200,160,120))              # tl
d.rectangle([w-60,5,w-5,60],fill=(180,140,100))          # tr
d.rectangle([5,h-60,60,h-5],fill=(190,150,110))          # bl
d.rectangle([w-60,h-60,w-5,h-5],fill=(170,130,90))       # br
# 中间花纹
for x in range(80,w-80,50):
    for y in range(80,h-80,50):
        d.ellipse([x-6,y-6,x+6,y+6],fill=(150,120,80))

dpi=150
corners={'tl':4.0,'tr':4.0,'bl':4.0,'br':4.0}
r=min(int(round(4.0*dpi/2.54)),min(w,h)//2)

res=icb.apply_border_only_corners(img,corners,dpi,DEFAULT_BG_COLOR)
a=np.array(res)

bl=_get_border_layers_robust(img,DEFAULT_BG_COLOR)
raw_depth=sum(t for _,t in bl)
print(f'r={r}, raw_depth={raw_depth}, border_layers={bl}')

for ck in ['tl','tr','bl','br']:
    cx,cy={'tl':(r,r),'tr':(w-r,r),'bl':(r,h-r),'br':(w-r,h-r)}[ck]
    ang_min,ang_max=CORNER_ANGLES[ck]
    yy,xx=np.mgrid[0:h,0:w].astype(np.float64)
    dist=np.sqrt((xx-cx)**2+(yy-cy)**2)
    angle=np.mod(np.degrees(np.arctan2(yy-cy,xx-cx)),360.0)
    in_ang=(angle>=ang_min)&(angle<=ang_max)
    
    # border_zone 内的内容区 (dist <= r - raw_depth - 5) - 这里应该保留内容色
    content_area=in_ang&(dist<=r-raw_depth-5)
    ca_pixels=a[content_area]
    ca_bright=np.mean(ca_pixels,axis=1) if len(ca_pixels)>0 else np.array([])
    ca_white=np.sum(ca_bright>240)
    ca_total=len(ca_pixels)
    
    # border_zone 外侧的弧线区 - 应该全白
    arc_outside=in_ang&(dist>r)
    ao_pixels=a[arc_outside]
    ao_bright=np.mean(ao_pixels,axis=1) if len(ao_pixels)>0 else np.array([])
    ao_dark=np.sum(ao_bright<200)
    
    status='✅' if ca_white<=50 and ao_dark<=10 else '❌'
    print(f'{status} {ck}: content_white={ca_white}/{ca_total}, arc_outside_dark={ao_dark}')
