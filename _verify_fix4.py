"""精确验证：手动推导翻回坐标，不依赖patch_lshape_cut_layers的内部逻辑。"""
import sys
sys.path.insert(0, '.')
import numpy as np

LAYERS = [((20, 18, 16), 8), ((243, 236, 220), 60), ((70, 60, 50), 3)]
T = sum(t for _, t in LAYERS)  # 71
edge = LAYERS[0][1]  # 8
band = LAYERS[1][1]  # 60

W, H = 3307, 4783
cut_w, cut_h = 118, 590

from core.lshape_border_route import patch_lshape_cut_layers
from core.lshape_border import patch_lshape_cut

def verify_corner(corner, path):
    """手动推导翻回后的垂直切边x范围，精确检查溢出。"""
    if corner == 'tl':
        x0, y0, cw, ch = 0, 0, cut_w, cut_h
    elif corner == 'tr':
        x0, y0, cw, ch = W - cut_w, 0, cut_w, cut_h
    elif corner == 'bl':
        x0, y0, cw, ch = 0, H - cut_h, cut_w, cut_h
    else:
        x0, y0, cw, ch = W - cut_w, H - cut_h, cut_w, cut_h
    
    canvas = np.full((H, W, 3), 255, dtype=np.uint8)
    if path == 'profile':
        out = patch_lshape_cut_layers(canvas, corner, x0, y0, cw, ch, LAYERS)
    else:
        out = patch_lshape_cut(canvas, corner, x0, y0, cw, ch, edge, band, (243, 236, 220), black=(20, 18, 16))
    
    # === 手动推导翻转后的坐标 ===
    flipx = corner in ('tl', 'bl')
    flipy = corner in ('bl', 'br')
    nx0 = (W - x0 - cw) if flipx else x0
    ny1 = (H - (y0 + ch)) if flipy else (y0 + ch)
    
    # 翻转图
    xc = nx0  # 垂直切边（缺口左边界）
    yc = ny1  # 水平切边（缺口下边界）
    
    # 翻转图中：
    # - 垂直切边k=0: xx ∈ [xc-edge, xc), yy ∈ [0, yc+1)    (缺口左边界，保留区左侧)
    # - 水平切边k=0: xx ∈ [xc, W), yy ∈ [yc, yc+edge)      (缺口下边界，保留区下方)
    # - gap区域:     xx ∈ [xc-T, xc-edge), yy ∈ [yc+1, yc+T)  (修复后)
    
    # 翻回变换
    # flipx: x_orig = W - 1 - x_flip
    # no flipx: x_orig = x_flip
    # flipy: y_orig = H - 1 - y_flip
    # no flipy: y_orig = y_flip
    
    def x_flip_to_orig(xx):
        return (W - 1 - xx) if flipx else xx
    
    def y_flip_to_orig(yy):
        return (H - 1 - yy) if flipy else yy
    
    # 翻回后垂直切边k=0的范围
    xx_vmin, xx_vmax = xc - edge, xc - 1
    x_orig_vmin = x_flip_to_orig(xx_vmax)  # flipx会反转
    x_orig_vmax = x_flip_to_orig(xx_vmin)
    
    # 翻回后垂直切边的y范围
    yy_vmin, yy_vmax = 0, yc
    y_orig_vmin = y_flip_to_orig(yy_vmin)
    y_orig_vmax = y_flip_to_orig(yy_vmax)
    
    print(f"\n  {corner} ({path}):")
    print(f"    flipx={flipx}, flipy={flipy}")
    print(f"    翻转图: xc={xc}, yc={yc}")
    print(f"    翻回后垂直切边k=0: x=[{x_orig_vmin},{x_orig_vmax}], y=[{y_orig_vmin},{y_orig_vmax}]")
    
    # 检查溢出：在翻回后垂直切边x范围内，y超出垂直切边范围
    overflow = 0
    overflow_ys = []
    for x in range(x_orig_vmin, x_orig_vmax + 1):
        for y in range(H):
            # y 在垂直切边范围内 → 跳过（正常绘制）
            if min(y_orig_vmin, y_orig_vmax) <= y <= max(y_orig_vmin, y_orig_vmax):
                continue
            # y 超出很多（> y_orig_vmax + 100 或 < y_orig_vmin - 100）→ 跳过（太远）
            if corner in ('tl', 'tr'):
                if y <= y_orig_vmax or y > y_orig_vmax + 100:
                    continue
            else:
                if y >= y_orig_vmin or y < y_orig_vmin - 100:
                    continue
            px = tuple(int(v) for v in out[y, x])
            if px != (255, 255, 255):
                overflow += 1
                overflow_ys.append((y, px))
    
    if overflow == 0:
        print(f"    ✅ 无溢出")
    else:
        print(f"    ⚠️ {overflow} 像素溢出")
        for y, px in overflow_ys[:5]:
            print(f"      y={y}: {px}")

print("=" * 60)
print("精确验证（手动推导翻回坐标）")
print("=" * 60)

for path in ['profile', 'v13']:
    print(f"\n--- {path.upper()} 路径 ---")
    for corner in ['tl', 'tr', 'bl', 'br']:
        verify_corner(corner, path)
