from PIL import Image, ImageDraw
from core.image_cropper import _get_border_layers_robust

def make_a():
    w,h=1200,1000
    img=Image.new('RGB',(w,h),(0,0,0))
    d=ImageDraw.Draw(img)
    margin=60
    d.rectangle([margin,margin,w-margin-1,h-margin-1],fill=(255,255,255))
    d.rectangle([margin,margin,w-margin-1,h-margin-1],outline=(0,0,0),width=12)
    return img

img=make_a()
layers=_get_border_layers_robust(img,(255,255,255))
print(layers)
