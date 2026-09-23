from PIL import Image
import numpy as np

src = Image.open(r'C:\Users\Administrator\Desktop\吸水皮革-定制-裁剪有图-黑色大理石;74x188.5CM裁剪有图.jpg')
arr = np.array(src.convert('RGB'))
H, W = arr.shape[:2]
print(f'Image size: {W}x{H}')

# Top edge profile (center column)
top_win = min(H // 3, 400)
top_col = arr[:top_win, W // 2]
print(f'\nTop edge profile (first 80 pixels):')
for i in range(80):
    r, g, b = top_col[i]
    print(f'  [{i:3d}] RGB=({r:3d},{g:3d},{b:3d})  max={max(r,g,b):3d}')

# Left edge profile (center row)
left_win = min(W // 3, 400)
left_row = arr[H // 2, :left_win]
print(f'\nLeft edge profile (first 80 pixels):')
for i in range(80):
    r, g, b = left_row[i]
    print(f'  [{i:3d}] RGB=({r:3d},{g:3d},{b:3d})  max={max(r,g,b):3d}')

# Also check what _v13_segv would produce
def _v13_segv(arr, tol=12):
    out = []
    prev = tuple(int(v) for v in arr[0])
    s = 0
    for i in range(1, len(arr)):
        c = tuple(int(v) for v in arr[i])
        if abs(c[0] - prev[0]) + abs(c[1] - prev[1]) + abs(c[2] - prev[2]) > tol:
            med = tuple(int(round(v)) for v in np.median(arr[s:i], axis=0))
            out.append((s, i - 1, med))
            s, prev = i, c
    med = tuple(int(round(v)) for v in np.median(arr[s:], axis=0))
    out.append((s, len(arr) - 1, med))
    return out

print('\n=== Top edge segments (first 10) ===')
top_segs = _v13_segv(top_col)
for i, (s, e, c) in enumerate(top_segs[:10]):
    w = e - s + 1
    print(f'  seg[{i}]: [{s:3d}-{e:3d}] width={w:3d} color={c}')

print('\n=== Left edge segments (first 10) ===')
left_segs = _v13_segv(left_row)
for i, (s, e, c) in enumerate(left_segs[:10]):
    w = e - s + 1
    print(f'  seg[{i}]: [{s:3d}-{e:3d}] width={w:3d} color={c}')
