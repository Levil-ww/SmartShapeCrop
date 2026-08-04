# -*- coding: utf-8 -*-
"""把 Layer6 单独的 mask 存图，肉眼看 P 点附近到底是红还是白"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from PIL import Image, ImageDraw
from core.image_cropper import _layer_rounded_mask_arr

W, H = 1200, 900
DPI = 150
r_px = max(0, int(round(8.5 * DPI / 2.54)))
print(f"r_px={r_px}")

# Layer6 的 rect_canvas
rc = (522, 246, 678, 660)
print(f"Layer6 rect_canvas={rc}")
rw, rh = rc[2]-rc[0], rc[3]-rc[1]
safe_r = max(0, min(r_px, max(1, min(rw, rh)//2)))
print(f"  rw={rw}, rh={rh}, safe_r={safe_r}")

sq = [rc[2]-safe_r, rc[3]-safe_r, rc[2], rc[3]]
print(f"  sq={sq} (挖空正方形 x∈[{sq[0]},{sq[2]}), y∈[{sq[1]},{sq[2]})")
pb = [rc[2]-2*safe_r, rc[3]-2*safe_r, rc[2], rc[3]]
print(f"  pieslice_bbox={pb}, start=0, end=90")
cx, cy = rc[2]-safe_r, rc[3]-safe_r
print(f"  圆心=({cx},{cy})")

P = (676, 601)
print(f"  P={P}")
dx, dy = P[0]-cx, P[1]-cy
print(f"    dx={dx}, dy={dy}, dist={np.sqrt(dx*dx+dy*dy):.1f} vs r={safe_r} → in_sector? {dx*dx+dy*dy <= safe_r*safe_r and dx>=0 and dy>=0}")
in_sq = sq[0] <= P[0] < sq[2] and sq[1] <= P[1] < sq[3]
print(f"    in_sq={in_sq}")

lm = _layer_rounded_mask_arr(W, H, rc, {'br': r_px})
print(f"  mask(P)=int(lm[{P[1]},{P[0]}]) = {int(lm[P[1], P[0]])}")

# 再用手动步骤画一遍 Layer6 的 mask 与上面的对比
lm2 = Image.new('L', (W, H), 255)
draw2 = ImageDraw.Draw(lm2)
sq_safe = [max(0,sq[0]),max(0,sq[1]),min(W,sq[2]),min(H,sq[3])]
print(f"\n  手动挖矩形 {sq_safe}")
draw2.rectangle(sq_safe, fill=0)
pb_safe = [max(0,pb[0]),max(0,pb[1]),min(W,pb[2]),min(H,pb[3])]
print(f"  手动填 pieslice {pb_safe} start=0, end=90")
draw2.pieslice(pb_safe, start=0, end=90, fill=255)
lm2_arr = np.array(lm2)
print(f"  手动mask(P)=int(lm2_arr[{P[1]},{P[0]}]) = {int(lm2_arr[P[1], P[0]])}")

# 保存：切右下角附近放大看
zoom_x1, zoom_y1 = cx - 5, cy - 5
zoom_x2, zoom_y2 = rc[2] + 5, rc[3] + 5
zoom_x1, zoom_y1 = max(0,zoom_x1), max(0,zoom_y1)
zoom_x2, zoom_y2 = min(W,zoom_x2), min(H,zoom_y2)

def save_zoom(arr, path):
    z = arr[zoom_y1:zoom_y2, zoom_x1:zoom_x2]
    vis = np.zeros((z.shape[0], z.shape[1], 3), dtype=np.uint8)
    vis[z == 0] = [220, 20, 20]  # 红=被挖
    vis[z == 255] = [255, 255, 255]  # 白=保留
    px_p = (P[0]-zoom_x1, P[1]-zoom_y1)
    if 0 <= px_p[0] < vis.shape[1] and 0 <= px_p[1] < vis.shape[0]:
        # 画黄圈标 P 点
        for di in range(-3, 4):
            for dj in range(-3, 4):
                ni, nj = px_p[1]+di, px_p[0]+dj
                if 0 <= ni < vis.shape[0] and 0 <= nj < vis.shape[1]:
                    vis[ni, nj] = [240, 220, 30]  # 黄
    Image.fromarray(vis).save(path)
    print(f"  zoom保存: {path}  (红=被挖 0，白=保留 255，黄=检查点 P)")

save_zoom(lm, r'd:\SmartShapeCrop\test_cropper_output\debug_layer6mask_zoom.png')
save_zoom(lm2_arr, r'd:\SmartShapeCrop\test_cropper_output\debug_layer6mask_manual_zoom.png')

# 对比差异
diff = lm != lm2_arr
if np.any(diff):
    print(f"\nWARN: _layer_rounded_mask_arr 与手动步骤的结果有 {int(np.sum(diff))} 像素差异！")
    ys, xs = np.where(diff)
    for i in range(min(10, len(xs))):
        print(f"  diff[{i}]: ({int(xs[i])},{int(ys[i])}) func={int(lm[int(ys[i]),int(xs[i])])} manual={int(lm2_arr[int(ys[i]),int(xs[i])])}")
else:
    print("\n✓ _layer_rounded_mask_arr 与手动步骤完全一致！")
