from PIL import Image
import numpy as np

# Analyze the output image
output = Image.open(r'C:\Users\Administrator\Desktop\智能裁剪设计器\4-L形挖角-单个边角测试输出\10-吸水皮革-定制-裁剪有图-黑色大理石;74x188.5CM裁剪有图.jpg')
out_arr = np.array(output.convert('RGB'))
H, W = out_arr.shape[:2]
print(f'Output image: {W}x{H}')

# Find the L-shape cut corner (should be top-right based on the image)
# Look for the white rectangle area
# Sample the top edge to find where the cut starts
print('\nTop edge profile (looking for cut corner):')
for x in range(W-1000, W, 10):
    pixel = out_arr[10, x]
    print(f'  [{x}] RGB={tuple(pixel)}')

# Find the vertical cut edge
# Look for a transition from image content to white (cut area)
print('\nSearching for vertical cut edge around y=100:')
for x in range(W-800, W, 5):
    pixel = out_arr[100, x]
    if pixel[0] > 200 and pixel[1] > 200 and pixel[2] > 200:
        print(f'  [{x}] RGB={tuple(pixel)} <- WHITE (cut area)')
        # Check pixels to the left
        print(f'  Border thickness check:')
        for dx in range(20):
            p = out_arr[100, x-1-dx]
            print(f'    [{x-1-dx}] RGB={tuple(p)}')
        break
