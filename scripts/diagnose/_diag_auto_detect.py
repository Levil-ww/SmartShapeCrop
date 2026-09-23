from PIL import Image
import sys
sys.path.insert(0, r'F:\SmartShapeCrop')
from core.lshape_border import _detect_lshape_border_auto

src = Image.open(r'C:\Users\Administrator\Desktop\吸水皮革-定制-裁剪有图-黑色大理石;74x188.5CM裁剪有图.jpg')

print('Running _detect_lshape_border_auto...')
profile_layers, v13, v13_computed, v13_preferred = _detect_lshape_border_auto(src)

print(f'\nResults:')
print(f'  profile_layers: {profile_layers}')
print(f'  v13: {v13}')
print(f'  v13_computed: {v13_computed}')
print(f'  v13_preferred: {v13_preferred}')

if v13:
    edge, band, black_color, band_color = v13
    print(f'\nV13 details:')
    print(f'  edge={edge}px')
    print(f'  band={band}px')
    print(f'  black_color={black_color}')
    print(f'  band_color={band_color}')

if profile_layers:
    print(f'\nProfile layers:')
    for i, (color, thickness) in enumerate(profile_layers):
        print(f'  Layer {i}: color={color}, thickness={thickness}px')
