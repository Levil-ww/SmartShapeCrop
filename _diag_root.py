"""诊断原始代码的实际问题 + cut_w 大小的影响。"""
import sys; sys.path.insert(0, '.')
import numpy as np

LAYERS = [((20, 18, 16), 8), ((243, 236, 220), 60), ((70, 60, 50), 3)]
offs = [0, 8, 68, 71]
T = offs[-1]; edge = 8
W, H = 3307, 4783

# 先恢复原始 gap 逻辑的 patch 函数做对比
class OriginalPatch:
    @staticmethod
    def gap_fill(b, xc, yc, offs, layers):
        """原始 gap: 全部 dx 用 max 分层"""
        H, W = b.shape[:2]
        T = offs[-1]
        xs = np.arange(max(0, xc - T), xc)
        if xs.size == 0: return
        offs_arr = np.array(offs, dtype=np.int64)
        colors_arr = np.array([c for c, _t in layers], dtype=np.uint8)
        y_start = max(0, yc + 1)
        y_end = min(H, yc + 1 + T)
        dx_arr = xc - xs
        for yy in range(y_start, y_end):
            dy = yy - yc
            d = np.maximum(dx_arr, dy)
            k = np.searchsorted(offs_arr, d, side='right') - 1
            k = np.clip(k, 0, len(layers) - 1)
            b[yy, xs] = colors_arr[k]

from core.lshape_border_route import patch_lshape_cut_layers

print("=" * 70)
print("原始代码 vs 当前修复: 不同 cut_w 下的差异")
print("=" * 70)

for cut_w in [118, 150, 206]:  # 2cm, ~2.7cm, 3.5cm
    cut_h = 590
    corner = 'tl'
    x0, y0, cw, ch = 0, 0, cut_w, cut_h
    
    # 修复后
    canvas = np.full((H, W, 3), 255, dtype=np.uint8)
    fixed = patch_lshape_cut_layers(canvas, corner, x0, y0, cw, ch, LAYERS)
    
    # 原始 (直接用 OriginalPatch 在翻转图坐标上模拟)
    canvas2 = np.full((H, W, 3), 255, dtype=np.uint8)
    flipx = True; flipy = False
    nx0 = (W - x0 - cw) if flipx else x0
    ny0 = (H - y0 - ch) if flipy else y0
    ny1 = ny0 + ch
    xc, yc = nx0, ny1
    
    # 先手动画垂直和水平切边 (模拟原始 _fill_layers_vertical_horizontal)
    b = canvas2.copy()
    for k, (color, _t) in enumerate(LAYERS):
        x_lo, x_hi = max(0, xc - offs[k+1]), min(W, xc - offs[k])
        y_lo = offs[k] if offs[k] < yc else 0
        y_hi = min(yc + 1, H)
        if x_hi > x_lo and y_hi > y_lo:
            b[y_lo:y_hi, x_lo:x_hi] = color
    for k, (color, _t) in enumerate(LAYERS):
        y_lo = max(0, yc + offs[k])
        y_hi = min(H, yc + offs[k+1])
        x_lo, x_hi = max(0, xc), max(0, min(W - offs[k], W))
        if x_hi > x_lo and y_hi > y_lo:
            b[y_lo:y_hi, x_lo:x_hi] = color
    # 原始 gap 填充
    OriginalPatch.gap_fill(b, xc, yc, offs, LAYERS)
    # 翻回
    orig_result = b if not flipx else np.fliplr(b)
    orig_result = orig_result if not flipy else np.flipud(orig_result)
    
    # 找原始图内凹角位置
    orig_cx, orig_cy = cut_w, cut_h
    
    print(f"\n--- cut_w={cut_w}px ({cut_w/442.8:.1f}cm) ---")
    
    # 对比 dx<=edge 区域 (原始图 x∈[orig_cx, orig_cx+edge-1])
    print(f"\n原始图 x∈[{orig_cx}, {orig_cx+edge-1}] (dx<=edge 对应位置):")
    for y in range(orig_cy, min(orig_cy+T+2, H)):
        row_fixed = tuple(int(v) for v in fixed[y, orig_cx])
        row_orig = tuple(int(v) for v in orig_result[y, orig_cx])
        marker = ''
        if row_fixed != row_orig:
            marker = ' ◀ DIFF'
        y_label = f'y={y} (dy={y-orig_cy})'
        fixed_label = {'黑':(20,18,16),'band':(243,236,220),'深':(70,60,50)}.get(None, str(row_fixed))
        orig_label = str(row_orig)
        print(f"  {y_label:>14}: fixed={row_fixed}  orig={row_orig}{marker}")
    
    # 检查不连续
    print(f"\n原始代码在 dx<=edge 区域的颜色跳变 (应该是连续的 band):")
    for y in range(orig_cy, min(orig_cy+T, H)):
        vals = set()
        for x in range(orig_cx, orig_cx+edge):
            vals.add(tuple(int(v) for v in orig_result[y, x]))
        if len(vals) > 1:
            print(f"  y={y} (dy={y-orig_cy}): {vals} ← 不连续!")
