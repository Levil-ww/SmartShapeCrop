from PIL import Image
import numpy as np

# Analyze the output image
output = Image.open(r'C:\Users\Administrator\Desktop\智能裁剪设计器\4-L形挖角-单个边角测试输出\10-吸水皮革-定制-裁剪有图-黑色大理石;74x188.5CM裁剪有图.jpg')
out_arr = np.array(output.convert('RGB'))
H, W = out_arr.shape[:2]
print(f'Output image: {W}x{H}')

# The L-shape cut should be in the top-right corner
# Let's find the black border by scanning from the right edge inward
print('\nScanning top edge from right to left (y=50):')
black_start = None
for x in range(W-1, 0, -1):
    pixel = out_arr[50, x]
    if pixel[0] < 50 and pixel[1] < 50 and pixel[2] < 50:
        if black_start is None:
            black_start = x
    else:
        if black_start is not None:
            black_end = x + 1
            print(f'Black border: x={black_end} to x={black_start} (width={black_start-black_end+1}px)')
            break

# Also check the vertical border
print('\nScanning right side from top to bottom (x=W-100):')
black_start_v = None
for y in range(0, H):
    pixel = out_arr[y, W-100]
    if pixel[0] < 50 and pixel[1] < 50 and pixel[2] < 50:
        if black_start_v is None:
            black_start_v = y
    else:
        if black_start_v is not None:
            black_end_v = y - 1
            print(f'Black border: y={black_start_v} to y={black_end_v} (height={black_end_v-black_start_v+1}px)')
            break

# Let's also look at the actual cut corner area
# Based on the image, the cut seems to be around x=10000-11191, y=0-500
print('\nDetailed scan of cut corner area (y=200, x=10000 to 11191):')
in_border = False
border_start = None
for x in range(10000, 11191):
    pixel = out_arr[200, x]
    is_black = pixel[0] < 50 and pixel[1] < 50 and pixel[2] < 50
    if is_black and not in_border:
        border_start = x
        in_border = True
    elif not is_black and in_border:
        print(f'Black border at y=200: x={border_start} to x={x-1} (width={x-border_start}px)')
        in_border = False
        break
