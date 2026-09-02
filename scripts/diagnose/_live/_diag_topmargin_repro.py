"""复现：边距从 5→15cm 修改后，render_design 是否会生成正确结果。

不依赖网络素材，用生成的条纹图模拟外框素材，方便精确观察 mask 边界。
对比 d1(mt=5) 和 d2(mt=15) 渲染图的差异。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.geometry import CropDesign
from core.image_ops import render_design, _get_inner_pixel_mask
from PIL import Image, ImageDraw
import numpy as np

# 用条纹图模拟外框素材（方便观察 mask 边界）
# 画横向彩色条纹，每条 1cm 宽
def make_stripe_pattern(px_per_cm, w_cm, h_cm):
    w = int(round(w_cm * px_per_cm))
    h = int(round(h_cm * px_per_cm))
    img = Image.new('RGB', (w, h), (128, 128, 128))
    draw = ImageDraw.Draw(img)
    colors = [(200, 50, 50), (50, 200, 50), (50, 50, 200), (200, 200, 50), (200, 50, 200)]
    stripe_h = int(round(1.0 * px_per_cm))  # 1cm 一条
    for i in range(h // stripe_h + 1):
        y0 = i * stripe_h
        y1 = min(y0 + stripe_h, h)
        c = colors[i % len(colors)]
        draw.rectangle([0, y0, w, y1], fill=c)
    return img

def make_design(mt, cached_img, material_path, px_per_cm):
    d = CropDesign()
    d.canvas_w_cm = 176.0
    d.canvas_h_cm = 59.0
    d.dpi = 150
    d.mode = 'rect_hole'
    d.outer_margin_cm = 0.0
    d.inner_margin_top_cm = mt
    d.inner_margin_bottom_cm = 9.0
    d.inner_margin_left_cm = 39.0
    d.inner_margin_right_cm = 68.0
    d.pool_hole_transparent = True
    d._cached_outer_image = cached_img.copy()
    d.pool_outer_material_image = material_path
    d.pool_material_design_w_cm = 58.0
    d.pool_material_design_h_cm = 121.0
    return d

# 画布 px/cm = dpi / 2.54 ≈ 150/2.54 ≈ 59.06
px_per_cm = 150 / 2.54
stripe = make_stripe_pattern(px_per_cm, 58.0, 121.0)
stripe.save('_stripe_pattern.png')

# 给 material_path 一个假路径（PoolRenderWorker 里检查 isfile，但 render_design 用 cached_img）
fake_path = 'f:/fake_material_stripe.png'

d1 = make_design(5.0, stripe, fake_path, px_per_cm)
d2 = make_design(15.0, stripe, fake_path, px_per_cm)

img1 = render_design(d1, quality='preview')
img2 = render_design(d2, quality='preview')
img1.save('_diag_top5.png')
img2.save('_diag_top15.png')

arr1 = np.array(img1)
arr2 = np.array(img2)

cm2px = d1.cm2px(1.0)
y5_px = int(round(5 * cm2px))
y15_px = int(round(15 * cm2px))

print(f'cm2px={cm2px:.2f}')
print(f'img1 size = {img1.size}')
print(f'img2 size = {img2.size}')
print(f'y=5cm pixel row = {y5_px}')
print(f'y=15cm pixel row = {y15_px}')

mid_x = arr1.shape[1] // 2

print('\n=== 图1 (top=5cm) inner_mask 边界 ===')
mask1 = _get_inner_pixel_mask(d1)
print(f'  mask top y = {d1.inner_rect_px().y} px')
print(f'  mask bottom y = {d1.inner_rect_px().y + d1.inner_rect_px().h} px')
print(f'  mask lx={d1.inner_rect_px().x} rx={d1.inner_rect_px().x + d1.inner_rect_px().w}')

print('\n=== 图1 垂直像素剖面：跨越旧 inner_rect 边缘 (y=5cm) ===')
for y_off in [-3, -2, -1, 0, 1, 2, 3, 10, 50]:
    y = y5_px + y_off
    px = arr1[y, mid_x]
    is_white = (int(px[0]) > 250 and int(px[1]) > 250 and int(px[2]) > 250)
    print(f'  y={y:5d} (Δ{y_off:+2d}): RGB=({px[0]:3d},{px[1]:3d},{px[2]:3d})  white={is_white}  mask={mask1[y, mid_x]}')

print('\n=== 图2 (top=15cm) inner_mask 边界 ===')
mask2 = _get_inner_pixel_mask(d2)
print(f'  mask top y = {d2.inner_rect_px().y} px')
print(f'  mask bottom y = {d2.inner_rect_px().y + d2.inner_rect_px().h} px')

print('\n=== 图2 垂直像素剖面：跨越新 inner_rect 边缘 (y=15cm) ===')
for y_off in [-50, -10, -3, -2, -1, 0, 1, 2, 3, 10]:
    y = y15_px + y_off
    px = arr2[y, mid_x]
    is_white = (int(px[0]) > 250 and int(px[1]) > 250 and int(px[2]) > 250)
    print(f'  y={y:5d} (Δ{y_off:+2d}): RGB=({px[0]:3d},{px[1]:3d},{px[2]:3d})  white={is_white}  mask={mask2[y, mid_x]}')

# 关键验证：y=5~15cm 区域在图2中应该全部是外框花纹（彩色条纹，非白色）
print('\n=== 关键验证：图2 y=5cm~15cm 区域（应该全是外框花纹=非白色）===')
all_non_white = True
for y in range(y5_px, y15_px, 5):
    px = arr2[y, mid_x]
    is_white = (int(px[0]) > 250 and int(px[1]) > 250 and int(px[2]) > 250)
    if is_white:
        all_non_white = False
        print(f'  ❌ y={y} pixel={px} IS WHITE (should be stripe pattern)')
if all_non_white:
    print('  ✅ 全部正确：y=5cm~15cm 区域全是非白色的外框花纹')

# 再看 inner_mask 边界附近是否有白色泄漏
print('\n=== 边界检查：mask 外 1px 是否泄漏白色？===')
print(f'  图1 mask top-1 ({d1.inner_rect_px().y-1}px): pixel={arr1[d1.inner_rect_px().y-1, mid_x]}')
print(f'  图1 mask top   ({d1.inner_rect_px().y}px): pixel={arr1[d1.inner_rect_px().y, mid_x]}')
print(f'  图2 mask top-1 ({d2.inner_rect_px().y-1}px): pixel={arr2[d2.inner_rect_px().y-1, mid_x]}')
print(f'  图2 mask top   ({d2.inner_rect_px().y}px): pixel={arr2[d2.inner_rect_px().y, mid_x]}')

# 全图差异统计
diff = np.abs(arr1.astype(int) - arr2.astype(int)).max(axis=2)
print(f'\nMax pixel diff (all canvas): {diff.max()}')
print(f'Mean pixel diff (all canvas): {diff.mean():.2f}')
y5y15_diff = diff[y5_px:y15_px, :]
print(f'Max diff y=5~15cm: {y5y15_diff.max()}')
print(f'Mean diff y=5~15cm: {y5y15_diff.mean():.2f}')
