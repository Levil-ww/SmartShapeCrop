from PIL import Image, ImageDraw
from core.image_cropper import _analyze_corner_sector_content

def make_a():
    w,h=1200,1000
    img=Image.new('RGB',(w,h),(0,0,0))
    d=ImageDraw.Draw(img)
    margin=60
    d.rectangle([margin,margin,w-margin-1,h-margin-1],fill=(255,255,255))
    d.rectangle([margin,margin,w-margin-1,h-margin-1],outline=(0,0,0),width=12)
    d.text((w//2,h//2),'Own Story',fill=(0,0,0),anchor='mm')
    return img

def make_b():
    w,h=1600,1000
    beige=(245,235,220)
    img=Image.new('RGB',(w,h),beige)
    d=ImageDraw.Draw(img)
    margin=40
    d.rectangle([margin,margin,w-margin-1,h-margin-1],outline=(25,22,20),width=10)
    d.rectangle([margin+60,margin+60,w-margin-61,h-margin-61],fill=(255,255,255))
    for x in [w//3,2*w//3]:
        d.line([(x,margin+60),(x,h-margin-61)],fill=(0,0,0),width=6)
    return img

def make_c():
    w,h=1200,900
    green=(60,90,45)
    img=Image.new('RGB',(w,h),green)
    d=ImageDraw.Draw(img)
    margin=50
    def dotted(x0,y0,x1,y1,step=6):
        for x in range(x0,x1,step*2): d.line([(x,y0),(min(x+step,x1),y0)],fill=(255,255,255),width=3)
        for x in range(x0,x1,step*2): d.line([(x,y1),(min(x+step,x1),y1)],fill=(255,255,255),width=3)
        for y in range(y0,y1,step*2): d.line([(x0,y),(x0,min(y+step,y1))],fill=(255,255,255),width=3)
        for y in range(y0,y1,step*2): d.line([(x1,y),(x1,min(y+step,y1))],fill=(255,255,255),width=3)
    dotted(margin,margin,w-margin-1,h-margin-1)
    dotted(margin+18,margin+18,w-margin-19,h-margin-19)
    d.ellipse([margin+80,margin+80,margin+180,margin+180],fill=(255,255,255))
    d.ellipse([w-margin-180,h-margin-180,w-margin-80,h-margin-80],fill=(255,255,255))
    return img

for name,img,r in [('A',make_a(),88),('B',make_b(),295),('C',make_c(),207)]:
    for ck in ['tl','tr','bl','br']:
        print(name,ck,_analyze_corner_sector_content(img,ck,r,(255,255,255)))
