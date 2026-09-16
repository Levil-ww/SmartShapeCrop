"""验证修复后是否消除溢出。"""
import sys
sys.path.insert(0, '.')
import numpy as np

LAYERS = [((20, 18, 16), 8), ((243, 236, 220), 60), ((70, 60, 50), 3)]
T = sum(t for _, t in LAYERS)  # 71

W, H = 3307, 4783
cut_w_px, cut_h_px = 118, 590

from core.lshape_border_route import patch_lshape_cut_layers

print("=" * 60)
print(f"修复后验证: TL, cut_w={cut_w_px}, cut_h={cut_h_px}, T={T}")
print("=" * 60)

canvas = np.full((H, W, 3), 255, dtype=np.uint8)
out = patch_lshape_cut_layers(canvas, 'tl', 0, 0, cut_w_px, cut_h_px, LAYERS)

x_check_v = cut_w_px
yc = cut_h_px

print(f"\n--- 垂直切边 x={x_check_v} 处 y={yc} 附近 ---")
for y in range(yc - 5, min(yc + 80, H)):
    px = tuple(int(v) for v in out[y, x_check_v])
    is_border = px != (255, 255, 255)
    if is_border:
        print(f"  y={y:5d}: {px}")

print(f"\n--- 溢出检查: x={x_check_v} 处 y > {yc} ---")
overflow_count = 0
for y in range(yc + 1, min(H, yc + 200)):
    px = tuple(int(v) for v in out[y, x_check_v])
    if px != (255, 255, 255):
        overflow_count += 1
if overflow_count == 0:
    print("  ✅ Profile 路径修复成功：无溢出！")
else:
    print(f"  ⚠️ 仍有 {overflow_count} 个溢出像素")

# 同时测试 V13 路径
from core.lshape_border import patch_lshape_cut

print("\n" + "=" * 60)
print("V13 路径验证")
print("=" * 60)

edge, band, color = 8, 60, (243, 236, 220)
canvas2 = np.full((H, W, 3), 255, dtype=np.uint8)
out2 = patch_lshape_cut(canvas2, 'tl', 0, 0, cut_w_px, cut_h_px, edge, band, color, black=(20, 18, 16))

print(f"\n--- 垂直切边 x={x_check_v} 处 y={yc} 附近 ---")
for y in range(yc - 5, min(yc + 80, H)):
    px = tuple(int(v) for v in out2[y, x_check_v])
    is_border = px != (255, 255, 255)
    if is_border:
        print(f"  y={y:5d}: {px}")

print(f"\n--- 溢出检查: x={x_check_v} 处 y > {yc} ---")
overflow_count2 = 0
for y in range(yc + 1, min(H, yc + 200)):
    px = tuple(int(v) for v in out2[y, x_check_v])
    if px != (255, 255, 255):
        overflow_count2 += 1
if overflow_count2 == 0:
    print("  ✅ V13 路径修复成功：无溢出！")
else:
    print(f"  ⚠️ V13 仍有 {overflow_count2} 个溢出像素")

# 检查内凹角区域其他位置是否还正确
print("\n" + "=" * 60)
print("验证内凹角 gap 区域仍然正确绘制")
print("=" * 60)
# 翻回后内凹角在 x ∈ [cut_w, cut_w+T], y ∈ [cut_h, cut_h+T]
# 但内凹角gap是 T×T 减去 edge×edge
# 所以 x ∈ [cut_w, cut_w+T] 的 band 区域应该正确
for x in [cut_w + 50, cut_w + 60, cut_w + 70, cut_w + 80, cut_w + 90, cut_w + 100, cut_w + 118]:
    if x < W:
        px = tuple(int(v) for v in out[yc + 50, x])
        print(f"  x={x:5d} @ y={yc + 50}: {px} border={px != (255, 255, 255)}")
