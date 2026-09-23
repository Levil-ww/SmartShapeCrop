from PIL import Image
import sys
sys.path.insert(0, r'F:\SmartShapeCrop')
from core.lshape_border import detect_border_v13

src = Image.open(r'C:\Users\Administrator\Desktop\吸水皮革-定制-裁剪有图-黑色大理石;74x188.5CM裁剪有图.jpg')
result = detect_border_v13(src)
if result:
    edge, band, black_color, band_color = result
    print(f'detect_border_v13 result:')
    print(f'  edge (black stroke width): {edge}px')
    print(f'  band (band width): {band}px')
    print(f'  black_color: {black_color}')
    print(f'  band_color: {band_color}')
else:
    print('detect_border_v13 returned None')
