"""
精确追踪 inner_mask 在 render_design 中的颜色变化
每步打印 inner_mask 边缘附近的 RGB
"""
import sys, os, numpy as np, glob as gl, warnings
warnings.filterwarnings('ignore')
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')
from PIL import Image
from core import image_ops as imops
from core.geometry import CropDesign, compute_inner_corner_radii, build_lshape_mask
from core.image_ops import (load_and_fit, load_image_rgb, _looks_like_tile, 
                             _render_inner_area, compute_border_bands)

mat = None
for c in gl.glob('.workbuddy/tmp_samples/**/*.jpg', recursive=True):
    if '蔓生' in c and 'full' in c.lower(): mat = c; break

def check(arr, mask, label, inner_rect):
    """Check inner_mask border area colors."""
    if not mask.any():
        print(f"  [{label}] mask empty")
        return
    # Border area: 10px band at inner_rect edges that SHOULD be inner_mask
    ir = inner_rect
    hb = 10  # border thickness
    # Right edge of inner_rect
    rx0, rx1 = int(ir.right) - hb, int(ir.right)
    ry0, ry1 = int(ir.y) + hb, int(ir.bottom) - hb  # all along right side
    
    region = arr[ry0:ry1, rx0:rx1]
    # Only where inner_mask is True
    mask_region = mask[ry0:ry1, rx0:rx1]
    if mask_region.any():
        med = np.median(region[mask_region], axis=0).astype(int)
        mean_val = region[mask_region].mean(axis=0).astype(int)
        whites = ((region[mask_region][:,0] > 250) & 
                  (region[mask_region][:,1] > 250) & 
                  (region[mask_region][:,2] > 250)).mean() * 100
    else:
        med = np.array([0,0,0]); whites = 0
    
    # Also check inner_rect bottom edge
    by0, by1 = int(ir.bottom) - hb, int(ir.bottom)
    bx0, bx1 = int(ir.x) + hb, int(ir.right) - hb
    region2 = arr[by0:by1, bx0:bx1]
    mask2 = mask[by0:by1, bx0:bx1]
    if mask2.any():
        med2 = np.median(region2[mask2], axis=0).astype(int)
        whites2 = ((region2[mask2][:,0] > 250) & 
                   (region2[mask2][:,1] > 250) & 
                   (region2[mask2][:,2] > 250)).mean() * 100
    else:
        med2 = np.array([0,0,0]); whites2 = 0
    
    print(f"  [{label}] RIGHT edge inner_mask = RGB({med[0]:3d},{med[1]:3d},{med[2]:3d}) white%={whites:5.1f}%")
    print(f"           BOTTOM edge inner_mask = RGB({med2[0]:3d},{med2[1]:3d},{med2[2]:3d}) white%={whites2:5.1f}%")

d = CropDesign()
d.mode = 'rect_lshape'; d.corner_position = 'br'
d.canvas_w_cm = 138.0; d.canvas_h_cm = 82.0
d.inner_w_cm = 45.0; d.inner_h_cm = 23.0
d.pool_hole_transparent = True
d.pool_outer_material_image = mat
d.dpi = 300

W, H = d.canvas_w_px, d.canvas_h_px
inner_rect = d.inner_rect_px(); outer = d.outer_rect_px()
lshape = d.l_shape_px()
inner_corners = compute_inner_corner_radii(outer, inner_rect, d.corners_px, direct=True)
inner_mask = imops._get_inner_pixel_mask(d)

print(f"inner_mask pixels: {inner_mask.sum()}")
print(f"inner_rect: {inner_rect}")
print(f"inner_rect RIGHT edge x = {inner_rect.right:.0f}")
print(f"inner_rect BOTTOM edge y = {inner_rect.bottom:.0f}")

# Step 1: load canvas
print(f"\n{'='*80}")
print(f"MANUAL REPLAY — checking inner_mask RIGHT/BOTTOM edge colors")
print(f"{'='*80}")

is_tile = _looks_like_tile(d.pool_outer_material_image)
canvas = load_and_fit(d.pool_outer_material_image, W, H, mode='tile' if is_tile else 'cover', quality='export')
canvas_arr = np.array(canvas, dtype=np.uint8)
src_orig = load_image_rgb(d.pool_outer_material_image)

print(f"\n--- AFTER Step 1: canvas loaded ---")
check(canvas_arr, inner_mask, "Step1 canvas", inner_rect)

# Step 1.1 non-L fill
print(f"\n--- AFTER Step 1.1: non-L fill ---")
if d.mode == 'rect_lshape':
    lshape_mask = np.array(build_lshape_mask((W,H), outer, lshape.corner, 0, 0, d.corners_px, fill_value=255), dtype=bool)
    non_lshape_mask = ~lshape_mask
    if non_lshape_mask.any():
        canvas_arr[non_lshape_mask] = np.array(d.outer_bg_color, dtype=np.uint8)
check(canvas_arr, inner_mask, "Step1.1 non-L", inner_rect)

# Step 2: border bands (SKIPPED because is_pool_with_material)
print(f"\n--- Step 2: border bands (SKIPPED — is_pool_with_material=True) ---")
check(canvas_arr, inner_mask, "Step2 (skipped)", inner_rect)

# Step 3: inner_fill + inner_mask fill
print(f"\n--- Step 3: inner_fill computed ---")
inner_fill = _render_inner_area(d, quality='export')
inner_fill_arr = np.array(inner_fill, dtype=np.uint8)
print(f"  inner_fill_arr sample: {inner_fill_arr[H//2, W//2]}")

# Step 3.x: L-shape cut fill (WHITE)
print(f"\n--- AFTER Step 3.x: cut area filled WHITE ---")
lshape_cut_done = False
full_inner_img = build_lshape_mask((W,H), inner_rect, lshape.corner, 0, 0, inner_corners, fill_value=255)
cut_area_mask = np.array(full_inner_img, dtype=bool) & ~inner_mask
if d.mode == 'rect_lshape':
    light_mask = canvas_arr.mean(axis=2) > 128
    if light_mask.any():
        sampled_bg = np.median(canvas_arr[light_mask], axis=0).astype(np.uint8)
    else:
        sampled_bg = np.array([255,255,255], dtype=np.uint8)
    canvas_arr[cut_area_mask] = np.array([255,255,255], dtype=np.uint8)
    lshape_cut_done = True
check(canvas_arr, inner_mask, "Step3.x cut WHITE", inner_rect)

# Step 3.y: inner_mask fill — SKIPPED because lshape_cut_done=True
print(f"\n--- Step 3.y: inner_mask fill (SKIPPED — lshape_cut_done={lshape_cut_done}) ---")
check(canvas_arr, inner_mask, "Step3.y (skipped)", inner_rect)

# Step 3.5: black border rendering
print(f"\n--- AFTER Step 3.5: black border ---")
border_width_px = 10  # approx
shrunk_w = max(0, inner_rect.w - 2 * border_width_px)
shrunk_h = max(0, inner_rect.h - 2 * border_width_px)
has_shrunk = shrunk_w > 0 and shrunk_h > 0
if has_shrunk:
    from core.geometry import RectShape
    shrunk_rect = RectShape(x=inner_rect.x + border_width_px, y=inner_rect.y + border_width_px,
                            w=shrunk_w, h=shrunk_h, corner_r=0.0)
    shrunk_corners = {ck: max(0.0, r - border_width_px) for ck, r in inner_corners.items()}
    shrunk_cut_w = max(0.0, lshape.cut_w - border_width_px)
    shrunk_cut_h = max(0.0, lshape.cut_h - border_width_px)
    mask_b_img = build_lshape_mask((W,H), shrunk_rect, lshape.corner,
                                    shrunk_cut_w, shrunk_cut_h, shrunk_corners, fill_value=255)
    border_mask = inner_mask & ~np.array(mask_b_img, dtype=bool)
else:
    border_mask = inner_mask

print(f"  border_mask pixels: {border_mask.sum()}")
if border_mask.any():
    canvas_arr[border_mask] = (0, 0, 0)  # BLACK_RGB
check(canvas_arr, inner_mask, "Step3.5 black border", inner_rect)

# Step 3.6: border completion
print(f"\n--- AFTER Step 3.6: border completion ---")
try:
    from core.lshape_border import apply_lshape_border_completion
    _scale_x = W / src_orig.size[0]
    _scale_y = H / src_orig.size[1]
    _ok = apply_lshape_border_completion(
        canvas_arr=canvas_arr,
        material_img=Image.fromarray(canvas_arr.copy()),
        src_material_img=src_orig,
        scale_x=_scale_x, scale_y=_scale_y,
        outer_rect=inner_rect,
        cut_corner=lshape.corner,
        cut_w_px=lshape.cut_w,
        cut_h_px=lshape.cut_h,
        dpi=d.dpi,
        bg_color=tuple(int(v) for v in sampled_bg),
    )
    print(f"  completion_ok = {_ok}")
except Exception as e:
    print(f"  Exception: {e}")
check(canvas_arr, inner_mask, "Step3.6 border completion", inner_rect)

print(f"\n{'='*80}")
print(f"COMPARE with ACTUAL render_design output")
print(f"{'='*80}")
actual = imops.render_design(d, quality='export')
actual_arr = np.array(actual)
check(actual_arr, inner_mask, "ACTUAL render_design", inner_rect)
