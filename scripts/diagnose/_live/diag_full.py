"""原始代码全场景诊断：蔓生花/素锦/南瓜无忧/纯白"""
import sys; sys.path.insert(0, r'f:\SmartShapeCrop')
import numpy as np
from PIL import Image, ImageDraw
import core.image_cropper_border as icb
from core.image_cropper import _get_border_layers_robust
from core.config import DEFAULT_BG_COLOR
from core.corner.algorithm import CORNER_ANGLES
from core.image_cropper_border import _corner_sector_has_content

def check_case(name, w, h, build_img_fn, corners_dict, dpi=150):
    img = build_img_fn(w, h)
    bl = _get_border_layers_robust(img, DEFAULT_BG_COLOR)
    raw_depth = sum(t for _, t in bl)
    r = min(int(round(max(corners_dict.values())*dpi/2.54)), max(1, min(w, h)//2))
    
    corners_px = {}
    for ck, rc in corners_dict.items():
        if rc > 0: corners_px[ck] = min(int(round(rc*dpi/2.54)), r)
    
    protect_map = {}
    for ck in corners_px:
        protect_map[ck] = _corner_sector_has_content(img, ck, corners_px[ck], raw_depth)
    
    res = icb.apply_border_only_corners(img, corners_dict, dpi, DEFAULT_BG_COLOR)
    a = np.array(res)
    T_plus = raw_depth + 2
    
    print(f'\n===== {name} =====')
    print(f'  bl={bl}, raw_depth={raw_depth}, r_max={r}')
    print(f'  protect_map={protect_map}')
    
    for ck, px_r in corners_px.items():
        cx, cy = {'tl':(px_r,px_r),'tr':(w-px_r,px_r),'bl':(px_r,h-px_r),'br':(w-px_r,h-px_r)}[ck]
        ang_min, ang_max = CORNER_ANGLES[ck]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
        dist = np.sqrt((xx-cx)**2 + (yy-cy)**2)
        angle = np.mod(np.degrees(np.arctan2(yy-cy, xx-cx)), 360.0)
        in_ang = (angle >= ang_min) & (angle <= ang_max)
        if ck=='tl': bz=(xx<=T_plus)|(yy<=T_plus)
        elif ck=='tr': bz=(((w-1)-xx)<=T_plus)|(yy<=T_plus)
        elif ck=='bl': bz=(xx<=T_plus)|(((h-1)-yy)<=T_plus)
        else: bz=(((w-1)-xx)<=T_plus)|(((h-1)-yy)<=T_plus)
        
        # NON-BZ: 弧形外侧 (dist > r, 非直边区) - 应该全白
        nbz = in_ang & (dist > px_r) & (~bz)
        nbz_pixels = a[nbz]
        nbz_dark = np.sum(np.mean(nbz_pixels, axis=1) < 150) if len(nbz_pixels) > 0 else 0
        nbz_bright = np.mean(nbz_pixels, axis=1) if len(nbz_pixels) > 0 else np.array([0])
        
        # IN-BZ: 直边区外侧 - 应该保留深色边框
        ibz = in_ang & (dist > px_r) & bz
        ibz_pixels = a[ibz]
        ibz_dark = np.sum(np.mean(ibz_pixels, axis=1) < 150) if len(ibz_pixels) > 0 else 0
        
        # 边框弧 [r-R, r] 应该深色
        arc = in_ang & (dist > px_r - raw_depth) & (dist <= px_r)
        arc_pixels = a[arc]
        arc_dark = np.sum(np.mean(arc_pixels, axis=1) < 150) if len(arc_pixels) > 0 else 0
        arc_total = len(arc_pixels)
        
        # 内容区 [0, r-R-5] 应该完整（无白色）
        content = in_ang & (dist <= px_r - raw_depth - 5)
        content_pixels = a[content]
        content_white = np.sum(np.mean(content_pixels, axis=1) > 240) if len(content_pixels) > 0 else 0
        content_total = len(content_pixels)
        
        # 综合评估
        has_dark_arc = nbz_dark > 0
        has_white_gap = content_white > 50
        
        if has_dark_arc: mark = '❌ DARK_ARC'
        elif has_white_gap: mark = '❌ WHITE_GAP'
        else: mark = '✅'
        
        print(f'  {mark} {ck}: NON-BZ_dark={nbz_dark} | border_arc={arc_dark}/{arc_total} | content_white={content_white}/{content_total}')
        if has_dark_arc:
            print(f'    ⚠️ nbz bright range: [{nbz_bright.min():.0f}, {nbz_bright.max():.0f}, mean={nbz_bright.mean():.0f}]')

# 蔓生花
def build_msh(w,h):
    img=Image.new('RGB',(w,h),(245,235,210));d=ImageDraw.Draw(img)
    d.rectangle([0,0,w-1,h-1],outline=(100,80,55),width=15)
    for x in range(20,w-20,25):
        d.ellipse([x-4,20,x+4,28],fill=(70,55,35))
        d.ellipse([x-4,h-28,x+4,h-20],fill=(70,55,35))
    for y in range(20,h-20,25):
        d.ellipse([20,y-4,28,y+4],fill=(70,55,35))
        d.ellipse([w-28,y-4,w-20,y+4],fill=(70,55,35))
    for x in range(50,w-50,70):
        for y in range(50,h-50,70):
            d.arc([x-15,y-15,x+15,y+15],0,180,fill=(170,150,110),width=2)
    return img
check_case('蔓生花(protect=True)', 800, 300, build_msh, {'tl':0,'tr':0,'bl':3.0,'br':3.0})

# 素锦
def build_sj(w,h):
    img=Image.new('RGB',(w,h),(250,245,230));d=ImageDraw.Draw(img)
    d.rectangle([0,0,w-1,h-1],outline=(130,115,95),width=20)
    for y in range(40,h-40,35):
        for x in range(40,w-40,50):
            d.line([(x,y),(x+8,y+12)],fill=(160,140,110),width=2)
            d.line([(x+25,y-5),(x+35,y+8)],fill=(150,130,100),width=2)
    return img
check_case('素锦(protect=False)', 800, 480, build_sj, {'tl':5.0,'tr':5.0,'bl':5.0,'br':5.0})

# 南瓜无忧
def build_ng(w,h):
    img=Image.new('RGB',(w,h),(70,55,40));d=ImageDraw.Draw(img)
    np.random.seed(42)
    arr=np.array(img,dtype=np.int16)
    noise=np.random.randint(-12,13,arr.shape)
    arr=np.clip(arr+noise,0,255).astype(np.uint8)
    img=Image.fromarray(arr);d=ImageDraw.Draw(img)
    d.rectangle([0,0,w-1,h-1],outline=(40,30,20),width=10)
    for i,px in enumerate([150,350,550,700]):
        cx,cy=px,h//2
        c=[(180,130,70),(140,100,50),(160,115,60),(120,85,40)][i]
        d.ellipse([cx-50,cy-60,cx+50,cy+60],fill=c)
        for rx in range(-40,41,20):
            d.ellipse([cx+rx-8,cy-50,cx+rx+8,cy+50],fill=(100,70,35))
    return img
check_case('南瓜无忧', 800, 300, build_ng, {'tl':0,'tr':0,'bl':4.0,'br':4.0})

# 纯白边框
def build_pure(w,h):
    img=Image.new('RGB',(w,h),(255,255,255));d=ImageDraw.Draw(img)
    d.rectangle([0,0,w-1,h-1],outline=(50,50,50),width=10)
    return img
check_case('纯白边框', 800, 300, build_pure, {'tl':3.0,'tr':3.0,'bl':3.0,'br':3.0})
