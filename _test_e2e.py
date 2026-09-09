"""端到端验证 L 形挖角修复"""
import sys, os
sys.path.insert(0, '.')
import numpy as np
from PIL import Image
from core.lshape_border import apply_lshape_border_completion
from core.image_ops import load_image_rgb, adapt_pool_material
from core.geometry import RectShape

for name, path in [('蔓生花', r'.workbuddy\tmp_samples\colorfix\蔓生花_full.jpg'),
                   ('中古雨林', r'.workbuddy\tmp_samples\e2e\中古雨林100x140.png')]:
    print(f'\n{"="*60}')
    print(f'完整流程验证: {name}')
    print(f'{"="*60}')

    src = load_image_rgb(path)
    sw, sh = src.size
    print(f'原始素材: {sw}x{sh}')

    # 模拟画布尺寸
    W, H = 7438, 4005
    scale_x = W / sw
    scale_y = H / sh

    canvas = adapt_pool_material(src, W, H, quality='preview')
    canvas_arr = np.array(canvas, dtype=np.uint8)

    # 模拟 L 形参数（左下角 cut_corner=bl）
    inner_rect = RectShape(x=400, y=400, w=6638, h=3205)
    cut_corner = 'bl'
    cut_w = int(W * 0.20)  # 约 1488px = 40cm
    cut_h = int(H * 0.22)  # 约 881px = 15cm

    cut_mask = np.zeros((H, W), dtype=bool)
    cut_x0 = int(inner_rect.x)
    cut_y0 = int(inner_rect.bottom) - cut_h
    cut_mask[cut_y0:cut_y0+cut_h, cut_x0:cut_x0+cut_w] = True
    canvas_arr[cut_mask] = [255, 255, 255]

    result = apply_lshape_border_completion(
        canvas_arr=canvas_arr,
        material_img=canvas,
        src_material_img=src,
        scale_x=scale_x,
        scale_y=scale_y,
        outer_rect=inner_rect,
        cut_corner=cut_corner,
        cut_w_px=float(cut_w),
        cut_h_px=float(cut_h),
        dpi=150,
        bg_color=(255, 255, 255),
    )
    print(f'补边结果: {result}')

    # 采样
    vert_edge_x = cut_x0 + cut_w
    horz_edge_y = cut_y0

    print(f'\n垂直切边 (x={vert_edge_x}) 附近采样:')
    for dy in [50, 200, 400, 600]:
        py = horz_edge_y + dy
        if py < H and vert_edge_x < W:
            left = canvas_arr[py, vert_edge_x - 5]
            right = canvas_arr[py, vert_edge_x + 5]
            print(f'  y={py}: 左={tuple(int(v) for v in left)}, 右={tuple(int(v) for v in right)}')

    print(f'\n水平切边 (y={horz_edge_y}) 附近采样:')
    for dx in [50, 200, 400, 600]:
        px = vert_edge_x - cut_w + dx
        if px < W and horz_edge_y > 0:
            above = canvas_arr[horz_edge_y - 5, px]
            below = canvas_arr[horz_edge_y + 5, px]
            print(f'  x={px}: 上={tuple(int(v) for v in above)}, 下={tuple(int(v) for v in below)}')

    print(f'\n内凹角 (x≈{vert_edge_x}, y≈{horz_edge_y}) 附近:')
    for dd in [3, 10, 25]:
        for dy_s, dx_s in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
            px = vert_edge_x + dx_s * dd
            py = horz_edge_y + dy_s * dd
            if 0 <= px < W and 0 <= py < H:
                c = canvas_arr[py, px]
                print(f'  ({py-horz_edge_y:+d}, {px-vert_edge_x:+d}): {tuple(int(v) for v in c)}')
