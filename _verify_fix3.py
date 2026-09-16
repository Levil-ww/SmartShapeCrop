"""最终验证：精确检查垂直切边 x 范围内 y>yc 是否溢出。"""
import sys
sys.path.insert(0, '.')
import numpy as np

LAYERS = [((20, 18, 16), 8), ((243, 236, 220), 60), ((70, 60, 50), 3)]
T = sum(t for _, t in LAYERS)  # 71
edge = LAYERS[0][1]  # 8

W, H = 3307, 4783
cut_w, cut_h = 118, 590

from core.lshape_border_route import patch_lshape_cut_layers
from core.lshape_border import patch_lshape_cut

def check_overflow_profile(corner):
    """精确溢出检查：翻回后在垂直切边 x 范围内检查 y 超出挖角范围。"""
    canvas = np.full((H, W, 3), 255, dtype=np.uint8)
    
    if corner == 'tl':
        x0, y0, cw, ch = 0, 0, cut_w, cut_h
    elif corner == 'tr':
        x0, y0, cw, ch = W - cut_w, 0, cut_w, cut_h
    elif corner == 'bl':
        x0, y0, cw, ch = 0, H - cut_h, cut_w, cut_h
    else:  # br
        x0, y0, cw, ch = W - cut_w, H - cut_h, cut_w, cut_h
    
    out = patch_lshape_cut_layers(canvas, corner, x0, y0, cw, ch, LAYERS)
    
    # 翻转图坐标 (patch_lshape_cut_layers 内部)
    flipx = corner in ('tl', 'bl')
    flipy = corner in ('bl', 'br')
    nx0 = (W - x0 - cw) if flipx else x0
    ny1 = ((H - y0) if flipy else (y0 + ch))
    xc_flip = nx0  # 翻转图中垂直切边位置
    yc_flip = ny1  # 翻转图中水平切边位置
    
    # 翻回后垂直切边 x 范围（黑描边部分）
    # 翻转图: xx ∈ [xc_flip - edge, xc_flip)
    # 翻回后
    # flipx: x_orig = W - 1 - x_flip
    # no flipx: x_orig = x_flip
    if flipx:
        x_left = W - 1 - (xc_flip)  # x_flip = xc_flip 时 x_orig = W-1-xc_flip
        x_right = W - 1 - (xc_flip - edge + 1)  # dx ∈ [0, edge) → 最右端 x_flip = xc_flip-1
    else:
        x_left = xc_flip - edge
        x_right = xc_flip - 1
    
    # 翻回后 y 范围
    if flipy:
        yc_orig = H - 1 - yc_flip  # flipy: y_orig = H-1 - y_flip
    else:
        yc_orig = yc_flip
    
    overflow = 0
    # 在垂直切边 x 范围内，检查 y 超出挖角的那一侧
    if corner in ('tl', 'tr'):
        # 挖角在顶部，垂直切边应该停在 y = cut_h_px = yc_orig
        y_start = yc_orig + 1
        y_end = min(H, yc_orig + 100)
    else:  # bl, br
        # 挖角在底部，垂直切边应该停在 y = yc_orig（较大 y）
        y_start = max(0, yc_orig - 100)
        y_end = yc_orig - 1
    
    if y_start >= y_end:
        return corner, 0
    
    for x in range(max(0, x_left), min(W, x_right + 1)):
        for y in range(y_start, y_end):
            px = tuple(int(v) for v in out[y, x])
            if px != (255, 255, 255):
                overflow += 1
                if overflow <= 5:
                    print(f"    overflow at ({x},{y}) = {px}")
    
    return corner, overflow

print("=" * 60)
print("精确溢出检查: Profile 路径")
print("=" * 60)
for corner in ['tl', 'tr', 'bl', 'br']:
    corner, ov = check_overflow_profile(corner)
    status = "✅" if ov == 0 else f"⚠️ ({ov}px)"
    print(f"  {status} {corner}")

# V13 路径
print("\n" + "=" * 60)
print("精确溢出检查: V13 路径")
print("=" * 60)

def check_overflow_v13(corner):
    canvas = np.full((H, W, 3), 255, dtype=np.uint8)
    
    if corner == 'tl':
        x0, y0, cw, ch = 0, 0, cut_w, cut_h
    elif corner == 'tr':
        x0, y0, cw, ch = W - cut_w, 0, cut_w, cut_h
    elif corner == 'bl':
        x0, y0, cw, ch = 0, H - cut_h, cut_w, cut_h
    else:
        x0, y0, cw, ch = W - cut_w, H - cut_h, cut_w, cut_h
    
    out = patch_lshape_cut(canvas, corner, x0, y0, cw, ch, edge, T-edge, (243, 236, 220), black=(20, 18, 16))
    
    flipx = corner in ('tl', 'bl')
    flipy = corner in ('bl', 'br')
    nx0 = (W - x0 - cw) if flipx else x0
    ny1 = ((H - y0) if flipy else (y0 + ch))
    xc_flip = nx0
    yc_flip = ny1
    
    if flipx:
        x_left = W - 1 - xc_flip
        x_right = W - 1 - (xc_flip - edge + 1)
    else:
        x_left = xc_flip - edge
        x_right = xc_flip - 1
    
    if flipy:
        yc_orig = H - 1 - yc_flip
    else:
        yc_orig = yc_flip
    
    overflow = 0
    if corner in ('tl', 'tr'):
        y_start = yc_orig + 1
        y_end = min(H, yc_orig + 100)
    else:
        y_start = max(0, yc_orig - 100)
        y_end = yc_orig - 1
    
    if y_start >= y_end:
        return corner, 0
    
    for x in range(max(0, x_left), min(W, x_right + 1)):
        for y in range(y_start, y_end):
            px = tuple(int(v) for v in out[y, x])
            if px != (255, 255, 255):
                overflow += 1
    
    return corner, overflow

for corner in ['tl', 'tr', 'bl', 'br']:
    corner, ov = check_overflow_v13(corner)
    status = "✅" if ov == 0 else f"⚠️ ({ov}px)"
    print(f"  {status} {corner}")

# ============ 回归测试 ============
print("\n" + "=" * 60)
print("回归测试: 运行现有测试套件")
print("=" * 60)
