import sys; sys.path.insert(0, '.')
import numpy as np
from PIL import Image
from core.lshape_border import detect_border_v13

mat = Image.open('.workbuddy/tmp_samples/colorfix/蔓生花_full.jpg')
print(f'Material size: {mat.size}')
v13 = detect_border_v13(mat)
print(f'V13 result: {v13}')

# Check scale
design_w, design_h = 8268, 4724
inner_rect_w = 2066.72
scale = inner_rect_w / design_w
print(f'Scale (inner_rect / mat_w): {scale:.3f}')

if v13:
    edge_src, band_src, color_src = v13
    edge_canvas = edge_src * scale
    band_canvas = band_src * scale
    print(f'Canvas coords: edge={edge_canvas:.1f}px, band={band_canvas:.1f}px')

    # Create minimal test to see what patch draws
    from core.lshape_border import patch_lshape_cut
    test = np.zeros((1477, 2067, 3), dtype=np.uint8)
    test[:] = (243, 236, 220)  # beige like material
    # Add a thin black border around the edge
    test[:5, :] = (0, 0, 0)
    test[-5:, :] = (0, 0, 0)
    test[:, :5] = (0, 0, 0)
    test[:, -5:] = (0, 0, 0)
    
    # 'bl' cut corner, cut at [0, oh-472, 591, oh]
    result = patch_lshape_cut(test.copy(), 'bl', 0, 1477-472, 591, 472,
                               max(1, int(round(edge_canvas))),
                               max(0, int(round(band_canvas))),
                               color_src)
    
    # Scan result along top edge
    print(f'\nPatch result top edge y=0, x=0..{len(result[0])}:')
    blacks = [x for x in range(2067) if max(result[0, x]) < 50]
    if blacks:
        print(f'  First black at x={blacks[0]}, last at x={blacks[-1]}, total={len(blacks)}')
    else:
        print('  No black!')
    
    # Scan result along left edge
    blacks = [y for y in range(1477) if max(result[y, 0]) < 50]
    print(f'Patch result left edge x=0, black: first={blacks[0] if blacks else "none"}, last={blacks[-1] if blacks else "none"}, total={len(blacks)}')
    
    result_img = Image.fromarray(result)
    result_img.save('_debug_patch.png')
    print(f'\nPatch result saved to _debug_patch.png')
