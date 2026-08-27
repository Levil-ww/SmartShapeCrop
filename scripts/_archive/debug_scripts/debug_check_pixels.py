from PIL import Image
import numpy as np

img = Image.open("debug_output/caseA_filter_test.jpg")
arr = np.array(img)
w, h = img.size
print("image size:", w, h)
# Check bottom-left corner outer region
for y in [h-5, h-10, h-20, h-30]:
    for x in [5, 10, 20, 30]:
        print(f"pixel ({x},{y}):", arr[y, x])

# Check a few pixels along the arc region
print("arc region samples:")
for y in range(h-60, h-1, 5):
    for x in range(0, 60, 5):
        if x*x + (h-1-y)*(h-1-y) > 89*89:
            pass
print("done")

# Check white content area
print("content (300,300):", arr[300,300])

# Check if black region in bottom-left arc exists
black_count = 0
white_count = 0
for y in range(h-100, h):
    for x in range(0, 100):
        d = np.sqrt((x)**2 + (h-1-y)**2)
        if d > 89:
            if np.all(arr[y,x] < 30):
                black_count += 1
            elif np.all(arr[y,x] > 240):
                white_count += 1
print(f"outside arc: black={black_count}, white={white_count}")
