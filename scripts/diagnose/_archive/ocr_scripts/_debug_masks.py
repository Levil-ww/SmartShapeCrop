"""调试草图1的mask检测"""
import sys, os, numpy as np
sys.path.insert(0, '.')
import cv2

img = cv2.imread('scripts/diagnose/_test_sketch1.png')
print(f"Image: {img.shape}, dtype={img.dtype}")
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# Build masks
kernel_small = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
kernel_mid = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))

# Canny
edges_raw = cv2.Canny(gray, 15, 80)
print(f"Canny raw: {cv2.countNonZero(edges_raw)} non-zero pixels")

# Canny + morph
edges = cv2.Canny(gray, 15, 80)
mask_canny = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel_small, iterations=1)
print(f"Canny morph: {cv2.countNonZero(mask_canny)} non-zero pixels")

# Adaptive
binary_adapt = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY_INV, 25, 10)
mask_adapt = cv2.morphologyEx(binary_adapt, cv2.MORPH_CLOSE, kernel_small, iterations=1)
print(f"Adaptive: {cv2.countNonZero(mask_adapt)} non-zero pixels")

# Red detection
hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
mask_r1 = cv2.inRange(hsv, np.array([0, 20, 30]), np.array([15, 255, 255]))
mask_r2 = cv2.inRange(hsv, np.array([165, 20, 30]), np.array([180, 255, 255]))
mask_red = cv2.bitwise_or(mask_r1, mask_r2)
mask_red = cv2.morphologyEx(mask_red, cv2.MORPH_CLOSE, kernel_mid, iterations=1)
print(f"Red mask: {cv2.countNonZero(mask_red)} non-zero pixels")

# Otsu
_, mask_otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
mask_otsu = cv2.morphologyEx(mask_otsu, cv2.MORPH_CLOSE, kernel_small, iterations=1)
print(f"Otsu: {cv2.countNonZero(mask_otsu)} non-zero pixels")

# Check connected components for each mask
h, w = gray.shape[:2]
full_area = h * w
min_component_area = max(200, int(full_area * 0.002))
print(f"\nMin component area: {min_component_area}")

for name, mask in [('canny_raw', edges_raw), ('canny', mask_canny), 
                    ('adaptive', mask_adapt), ('red', mask_red), ('otsu', mask_otsu)]:
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    large_components = []
    for label_id in range(1, num_labels):
        area = stats[label_id, cv2.CC_STAT_AREA]
        if area >= min_component_area:
            x = stats[label_id, cv2.CC_STAT_LEFT]
            y = stats[label_id, cv2.CC_STAT_TOP]
            ww = stats[label_id, cv2.CC_STAT_WIDTH]
            hh = stats[label_id, cv2.CC_STAT_HEIGHT]
            large_components.append((label_id, area, x, y, ww, hh))
    print(f"\n{name}: {num_labels-1} total components, {len(large_components)} large (>= {min_component_area})")
    for lc in large_components[:5]:
        print(f"  label={lc[0]} area={lc[1]:.0f} rect=({lc[2]},{lc[3]},{lc[4]},{lc[5]})")
