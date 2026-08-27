from PIL import Image, ImageDraw
from core.image_cropper import _get_border_layers_robust

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

img=make_b()
layers=_get_border_layers_robust(img,(255,255,255))
print(layers)
