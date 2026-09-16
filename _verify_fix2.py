"""验证修复后是否消除溢出 - 最终版。"""
import sys
sys.path.insert(0, '.')
import numpy as np

LAYERS = [((20, 18, 16), 8), ((243, 236, 220), 60), ((70, 60, 50), 3)]
T = sum(t for _, t in LAYERS)  # 71

W, H = 3307, 4783
cut_w_px, cut_h_px = 118, 590
yc = cut_h_px

from core.lshape_border_route import patch_lshape_cut_layers
from core.lshape_border import patch_lshape_cut

print("=" * 60)
print("Profile 路径验证")
print("=" * 60)
canvas = np.full((H, W, 3), 255, dtype=np.uint8)
out = patch_lshape_cut_layers(canvas, 'tl', 0, 0, cut_w_px, cut_h_px, LAYERS)

x_check = cut_w_px
print(f"\n--- 垂直切边 x={x_check} 处溢出检查 ---")
overflow = 0
for y in range(yc + 1, min(H, yc + 100)):
    px = tuple(int(v) for v in out[y, x_check])
    if px != (255, 255, 255):
        overflow += 1
if overflow == 0:
    print("  ✅ Profile: 无溢出!")
else:
    print(f"  ⚠️ Profile: 仍有 {overflow} 个溢出像素")
    for y in range(yc + 1, yc + 50):
        px = tuple(int(v) for v in out[y, x_check])
        if px != (255, 255, 255):
            print(f"    y={y}: {px}")

print("\n--- 内凹角 gap 区域验证 (x>cut_w+8, y>yc) ---")
# 翻回后 gap 在 x ∈ [cut_w+7, cut_w+70], y ∈ [yc+1, yc+72]
gap_ok = True
for x in range(cut_w_px + 15, cut_w_px + T + 20, 10):
    if x >= W: break
    for y in [yc + 10, yc + 30, yc + 50, yc + 70]:
        if y >= H: break
        px = tuple(int(v) for v in out[y, x])
        if px == (255, 255, 255):
            print(f"  ⚠️ gap 区域未绘制: ({x},{y}) = white")
            gap_ok = False
if gap_ok:
    print("  ✅ Profile gap 区域正确绘制")

print("\n--- 水平切边 x=0 处验证 (不应受影响) ---")
for y in [yc - 5, yc, yc + 1, yc + 5, yc + 50]:
    px = tuple(int(v) for v in out[y, 0])
    print(f"  y={y}: {px} border={px != (255,255,255)}")

# ============ V13 ============
print("\n" + "=" * 60)
print("V13 路径验证")
print("=" * 60)
edge, band, color = 8, 60, (243, 236, 220)
canvas2 = np.full((H, W, 3), 255, dtype=np.uint8)
out2 = patch_lshape_cut(canvas2, 'tl', 0, 0, cut_w_px, cut_h_px, edge, band, color, black=(20, 18, 16))

print(f"\n--- 垂直切边 x={x_check} 处溢出检查 ---")
overflow2 = 0
for y in range(yc + 1, min(H, yc + 100)):
    px = tuple(int(v) for v in out2[y, x_check])
    if px != (255, 255, 255):
        overflow2 += 1
if overflow2 == 0:
    print("  ✅ V13: 无溢出!")
else:
    print(f"  ⚠️ V13: 仍有 {overflow2} 个溢出像素")
    for y in range(yc + 1, yc + 50):
        px = tuple(int(v) for v in out2[y, x_check])
        if px != (255, 255, 255):
            print(f"    y={y}: {px}")

print("\n--- 内凹角 gap 区域验证 ---")
gap_ok2 = True
for x in range(cut_w_px + 15, cut_w_px + T + 20, 10):
    if x >= W: break
    for y in [yc + 10, yc + 30, yc + 50, yc + 70]:
        if y >= H: break
        px = tuple(int(v) for v in out2[y, x])
        if px == (255, 255, 255):
            print(f"  ⚠️ V13 gap 区域未绘制: ({x},{y}) = white")
            gap_ok2 = False
if gap_ok2:
    print("  ✅ V13 gap 区域正确绘制")

# ============ 其他角落验证 ============
print("\n" + "=" * 60)
print("其他角落验证 (tr, bl, br)")
print("=" * 60)
for corner in ['tr', 'bl', 'br']:
    canvas3 = np.full((H, W, 3), 255, dtype=np.uint8)
    if corner == 'tr':
        out3 = patch_lshape_cut_layers(canvas3, corner, W-cut_w_px, 0, cut_w_px, cut_h_px, LAYERS)
        # tr: 翻转图 xc=cut_w_px, 翻回后水平翻转
        # 垂直切边在翻回后 x=W-cut_w_px 附近, 检查 y>cut_h_px 是否溢出
        x_chk = W - cut_w_px
    elif corner == 'bl':
        out3 = patch_lshape_cut_layers(canvas3, corner, 0, H-cut_h_px, cut_w_px, cut_h_px, LAYERS)
        x_chk = cut_w_px
    else:  # br
        out3 = patch_lshape_cut_layers(canvas3, corner, W-cut_w_px, H-cut_h_px, cut_w_px, cut_h_px, LAYERS)
        x_chk = W - cut_w_px
    
    overflow3 = 0
    if corner in ['bl', 'br']:
        # 翻回后需要检查 y < H-cut_h_px
        for y in range(max(0, H - cut_h_px - 100), max(0, H - cut_h_px)):
            px = tuple(int(v) for v in out3[y, x_chk])
            if px != (255, 255, 255):
                overflow3 += 1
    else:
        for y in range(cut_h_px + 1, cut_h_px + 100):
            if y >= H: break
            px = tuple(int(v) for v in out3[y, x_chk])
            if px != (255, 255, 255):
                overflow3 += 1
    
    status = "✅" if overflow3 == 0 else "⚠️"
    print(f"  {status} {corner}: overflow={overflow3}")

# ============ 回归测试: 较大挖角不应受影响 ============
print("\n" + "=" * 60)
print("回归测试: 大挖角 (cut_w=300, cut_h=300)")
print("=" * 60)
canvas4 = np.full((H, W, 3), 255, dtype=np.uint8)
out4 = patch_lshape_cut_layers(canvas4, 'tl', 0, 0, 300, 300, LAYERS)

# 垂直切边 x=300
x_v = 300
print(f"  x={x_v} @ y=299: {tuple(int(v) for v in out4[299, x_v])}")
print(f"  x={x_v} @ y=300: {tuple(int(v) for v in out4[300, x_v])}")
print(f"  x={x_v} @ y=301: {tuple(int(v) for v in out4[301, x_v])}")
print(f"  x={x_v+10} @ y=310: {tuple(int(v) for v in out4[310, x_v+10])}")
print(f"  x={x_v+50} @ y=320: {tuple(int(v) for v in out4[320, x_v+50])}")
