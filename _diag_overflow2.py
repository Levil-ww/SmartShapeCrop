"""诊断 L 形挖角边框线溢出问题 - 极端窄挖角场景。"""
import sys
sys.path.insert(0, '.')
import numpy as np
from PIL import Image, ImageDraw

print("=" * 60)
print("场景 1: 极端窄挖角 - cut_w < T")
print("=" * 60)

# Profile 路径 (蔓生花): 黑描边8 + 米色边距60 + 细线3
LAYERS = [((20, 18, 16), 8), ((243, 236, 220), 60), ((70, 60, 50), 3)]
T = sum(t for _, t in LAYERS)  # 71

W, H = 800, 600

# cut_w=50 < T=71, cut_h=200
canvas = np.full((H, W, 3), 255, dtype=np.uint8)
from core.lshape_border_route import patch_lshape_cut_layers
out = patch_lshape_cut_layers(canvas, 'tl', 0, 0, 50, 200, LAYERS)

print(f"\nProfile路径: TL, cut_w=50, cut_h=200, T={T}")
print(f"xc翻转后={W-0-50}={W-50}, yc=200")

# 翻转后 xc=W-50=750, 翻回后 x=W-750=50
# 垂直切边在 x≈50, y ∈ [0, yc+1)=[0, 201)
# 水平切边覆盖 y ∈ [yc, yc+T) = [200, 271), x ∈ [50, W)

print("\n--- 垂直切边 x=50 处 y 范围 ---")
x_chk = 50
for y in range(0, min(H, 300), 20):
    px = tuple(int(v) for v in out[y, x_chk])
    print(f"  y={y:3d}: {px} border={px != (255,255,255)}")

# 关键测试: 垂直切边的 y_hi = yc+1 = 201，但水平切边从 y=yc=200 开始
# 当 cut_w < T 时，水平切边的 y=y0=200 这一行同时被垂直切边（最后一行 y=200）
# 和水平切边覆盖

# 让我们看一下 _fill_layers_vertical_horizontal 里的垂直切边 y_lo 逻辑
# 垂直切边 y_lo = offs[k] if offs[k] < yc else 0
# 垂直切边 y_hi = min(yc + 1, H)
# 水平切边 y_lo = max(0, yc + offs[k])
# 水平切边 y_hi = min(H, yc + offs[k+1])

# offs = [0, 8, 68, 71]
# k=0: 垂直 y_lo=0, y_hi=201 → y=[0, 201), x=[xc-8, xc) = [750-8, 750) = [742, 750)
# k=0: 水平 y_lo=200, y_hi=208 → y=[200, 208), x=[750, W)

# 翻回后: x' = W - x_flip
# 垂直k=0: x = [8, 58), y=[0, 201) → 翻回后 x=[742, 750)变成 x=[50, 58)? 不对...
# flipx: b = np.fliplr(b) → b[i, j] = a[i, W-1-j]
# 所以翻回: x_orig = W - 1 - x_flip
# x_flip=742 → x_orig=57
# x_flip=749 → x_orig=50

# 垂直k=0 x范围 (翻转图): [750-8, 750) = [742, 750)
# 翻回: x ∈ [50, 58)

# 水平k=0 x范围 (翻转图): [xc, W) = [750, 800)
# 翻回: x ∈ [0, 50)

# 垂直切边覆盖 x=[50, 58), y=[0, 201)
# 水平切边覆盖 x=[0, 50), y=[200, 208)

# 所以 x=50 是分界线！垂直切边在 x>=50, 水平切边在 x<50
# 在 (x=50, y=200) 处，应该是垂直黑描边的最后一行

print("\n--- 翻转图 (xc=750, yc=200) 分析 ---")
print("垂直k=0(黑描边): x=[742, 750), y=[0, 201) → 翻回后 x=[50, 58), y=[0, 201)")
print("水平k=0(黑描边): x=[750, 800), y=[200, 208) → 翻回后 x=[0, 50), y=[200, 208)")

# 现在让我检查一下水平切边的 x_lo 和 x_hi 计算
# x_lo = max(0, xc) = 750
# x_hi = max(0, min(W - offs[k], W))

print("\n--- 水平切边各层 x 范围 (翻转图) ---")
offs = [0, 8, 68, 71]
for k, (color, t) in enumerate(LAYERS):
    xc_flip = 750
    x_lo = max(0, xc_flip)
    x_hi = max(0, min(W - offs[k], W))
    y_lo = max(0, 200 + offs[k])
    y_hi = min(H, 200 + offs[k + 1])
    print(f"  k={k}: color={color}, x=[{x_lo}, {x_hi}), y=[{y_lo}, {y_hi})")

# 当 cut_w 很小（如 50），翻转后 W - xc = 50
# 水平切边从 x=750 开始向右延伸，到 W=800，只能覆盖 50px
# 但 offs[k] 可能 > 50

print("\n--- 检查水平切边是否覆盖完整 cut 区 ---")
# 翻回后水平切边 x ∈ [0, W-xc_flip) = [0, 50)
# 但如果 offs[k] > 50, x_hi = W - offs[k] = 800 - 71 = 729 → 翻回后 x_orig = W-1-729 = 70
# 这意味着水平切边的内层会延伸到 cut 区之外？不，翻回后 W - offs[k] 才是正确的...

# 等等，让我重新想。水平切边在翻转图中是从 xc 向右延伸到 W。
# offs[k] 是内凹角的距离偏移，但水平切边的 x_hi = W - offs[k] 不对？

# 看代码: x_lo, x_hi = max(0, xc), max(0, min(W - offs[k], W))
# 当 k=0: x_lo = 750, x_hi = W - 0 = 800 → 覆盖整个 cut 区宽度 ✓
# 当 k=1: x_lo = 750, x_hi = W - 8 = 792 → 从 xc 向右 42px
# 当 k=2: x_lo = 750, x_hi = W - 68 = 732 → 732 < 750 = x_lo → 不会画！

# 翻回后: x_flip=732 → x_orig = 800-1-732 = 67
# x_flip=750 → x_orig = 800-1-750 = 49

# 所以 k=2 层在翻转图中不会画（因为 732 < 750），翻回后也不会画。这是对的。

# 现在让我看看另一个方向 - 垂直切边覆盖 y 范围
# 垂直切边 y_hi = min(yc+1, H) = 201
# 这意味着垂直切边画 y ∈ [0, 201) = y=0~200
# 水平切边 y_lo = yc + offs[k]，y=200+0=200 开始

# 在 y=200 处，垂直切边的最后一行和水平切边的第一行重叠
# 垂直切边 x=[50, 58) 处 y=200 是最后一行黑描边
# 水平切边 x=[0, 50) 处 y=200 是第一行黑描边
# 这应该是正确的 - 刚好在 cut 角点汇合

# 让我看看真正的问题 - 用户说蔓生花，可能不是走 Profile 路径而是走 V13 路径？
# 等等，蔓生花首层是黑描边8px，_THICK_BLACK_MIN=50，8 < 50 → 不让位
# 第二层是米色边距 (243,236,220)，不是深色带 → profile_yields_to_v13 返回 False
# 所以蔓生花确实走 Profile 路径

# 让我看看用户的截图 - 第一张是草图，第二张是实际渲染的花幔/蔓生花素材
# 从第二张图看，垂直切边的黑描边在 TL 角落
# 但它看起来延伸超过了挖角高度...

# 让我用更真实的参数测试：假设 DPI=150, 1cm≈59px
# cut_w=2cm → 118px, cut_h=10cm → 590px

print("\n" + "=" * 60)
print("场景 2: 更真实的 DPI=150 (1cm≈59px)")
print("=" * 60)

# 假设画布对应产品尺寸 56cm × 81cm
W_cm, H_cm = 56.0, 81.0
dpi = 150
W_px = int(W_cm * dpi / 2.54)
H_px = int(H_cm * dpi / 2.54)

cut_w_cm, cut_h_cm = 2.0, 10.0
cut_w_px = int(cut_w_cm * dpi / 2.54)
cut_h_px = int(cut_h_cm * dpi / 2.54)

print(f"画布: {W_cm}cm × {H_cm}cm → {W_px}×{H_px}px @ DPI={dpi}")
print(f"挖角: {cut_w_cm}cm × {cut_h_cm}cm → {cut_w_px}×{cut_h_px}px")
print(f"边框层: {LAYERS}, T={T}")

canvas = np.full((H_px, W_px, 3), 255, dtype=np.uint8)
out = patch_lshape_cut_layers(canvas, 'tl', 0, 0, cut_w_px, cut_h_px, LAYERS)

# TL 角落翻转后 xc = W - cut_w_px
xc_flip = W_px - cut_w_px
yc_flip = cut_h_px
print(f"\n翻转图: xc={xc_flip}, yc={yc_flip}")
print(f"xc={xc_flip}, cut_w={cut_w_px}, cut_h={cut_h_px}")

# 翻回后: x_orig = W-1-x_flip, y_orig = y_flip
# 垂直切边在 x=cut_w_px 处

x_check_v = cut_w_px
print(f"\n--- 垂直切边 x={x_check_v} 处 y 范围检查 ---")
y_ranges_to_check = list(range(0, min(yc_flip + 30, H_px), 20)) + [yc_flip - 3, yc_flip - 2, yc_flip - 1, yc_flip, yc_flip + 1, yc_flip + 2, yc_flip + 3]
for y in sorted(set(y_ranges_to_check)):
    if 0 <= y < H_px and 0 <= x_check_v < W_px:
        px = tuple(int(v) for v in out[y, x_check_v])
        print(f"  y={y:5d}: {px} border={px != (255,255,255)}")

print(f"\n--- 垂直切边在 y={yc_flip} 处 x 范围 ---")
y_check_h = yc_flip
x_ranges_to_check = [x_check_v - 5, x_check_v - 4, x_check_v - 3, x_check_v - 2, x_check_v - 1, x_check_v, x_check_v + 1, x_check_v + 2, x_check_v + 3, x_check_v + 4, x_check_v + 5, x_check_v + 10, x_check_v + 20, x_check_v + 50, x_check_v + 100]
for x in sorted(set(x_ranges_to_check)):
    if 0 <= y_check_h < H_px and 0 <= x < W_px:
        px = tuple(int(v) for v in out[y_check_h, x])
        print(f"  x={x:5d}: {px} border={px != (255,255,255)}")

# 关键: 检查是否有"溢出"
# 垂直切边应该停在 y=yc (翻回后也是这个值，因为 TL flipy=False)
# 水平切边从 y=yc 开始

# 检查 y=yc 以下 x=cut_w_px 处是否还有垂直边框
print(f"\n--- 溢出检查: x={x_check_v} 处 y > {yc_flip} 的溢出 ---")
y = yc_flip + 1
overflow_count = 0
overflow_y_start = None
while y < min(H_px, yc_flip + 100):
    px = tuple(int(v) for v in out[y, x_check_v])
    if px != (255, 255, 255):
        overflow_count += 1
        if overflow_y_start is None:
            overflow_y_start = y
    y += 1
if overflow_count > 0:
    print(f"  ⚠️ 检测到溢出! 从 y={overflow_y_start} 起有 {overflow_count} 个溢出像素")
    # 看溢出是什么颜色
    y2 = overflow_y_start
    px = tuple(int(v) for v in out[y2, x_check_v])
    print(f"  溢出颜色: {px}")
else:
    print("  ✅ 无溢出")

# 但是！真正的问题可能在水平切边——当 cut_w < T 时
print(f"\n--- 水平切边 x=0 处 y 范围 (翻回后 x=0 = 翻转图 x=W-1) ---")
x_check_outer = 0
y_ranges2 = list(range(yc_flip - 10, min(yc_flip + T + 10, H_px)))
for y in y_ranges2:
    if 0 <= y < H_px and 0 <= x_check_outer < W_px:
        px = tuple(int(v) for v in out[y, x_check_outer])
        print(f"  y={y:5d}: {px} border={px != (255,255,255)}")
