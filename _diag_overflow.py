"""诊断 L 形挖角边框线溢出问题。"""
import sys
sys.path.insert(0, '.')
import numpy as np
from PIL import Image, ImageDraw

# === Profile 路径 (蔓生花) ===
from core.lshape_border_route import patch_lshape_cut_layers

# 模拟蔓生花边框: 黑描边8 + 米色边距60 + 细线3
LAYERS = [((20, 18, 16), 8), ((243, 236, 220), 60), ((70, 60, 50), 3)]
T = sum(t for _, t in LAYERS)  # 71

W, H = 800, 600
canvas = np.full((H, W, 3), 255, dtype=np.uint8)

# TL 角落挖角，很窄: cut_w=60px, cut_h=300px
print("=== Profile 路径: TL 角落，cut_w=60px, cut_h=300px, T=71px ===")
out = patch_lshape_cut_layers(canvas, 'tl', 0, 0, 60, 300, LAYERS)

# TL 角落翻转后 flipx=True, flipy=False
# 翻转后: nx0 = W-0-60 = 740, ny0 = 0, ny1 = 300
# xc = 740, yc = 300
# 翻回后 x=740 变成 x=W-740=60

# 翻转后的坐标: xc=740, yc=300
# 垂直切边覆盖 y ∈ [0, yc+1) = [0, 301) → 翻回后也是 y ∈ [0, 301)
# 水平切边覆盖 y ∈ [yc, yc+T) = [300, 371) → 翻回后也是这个范围
# 内凹角覆盖 (xc-T, xc) × (yc+1, yc+1+T) = (669, 740) × (301, 371)

print("--- 垂直切边 (原始 x≈60) y 范围 ---")
x_check = 60
for y_test in range(0, H, 50):
    px = tuple(int(v) for v in out[y_test, x_check])
    is_border = px != (255, 255, 255)
    print(f"  y={y_test:3d}: border={is_border} color={px}")

# 关键: y=300 应该是垂直切边的结束，从 y=301 起应该没有垂直边框了
print()
print("--- y=300 附近仔细检查 (x=60) ---")
for y_test in [295, 296, 297, 298, 299, 300, 301, 302, 303, 304, 305]:
    px = tuple(int(v) for v in out[y_test, x_check])
    print(f"  y={y_test}: {px}")

print()
print("--- 水平切边 y=300 附近仔细检查 (x=60) ---")
for y_test in [295, 296, 297, 298, 299, 300, 301, 302, 303, 304, 305, 310, 320, 350, 400]:
    # 在 x 方向的不同位置检查
    x_check2 = 100  # 远离切边的位置
    px = tuple(int(v) for v in out[y_test, x_check2])
    print(f"  y={y_test:3d} @ x={x_check2}: {px} border={px != (255,255,255)}")

# 问题: 挖角很窄 cut_w=60, 但 T=71 > cut_w
# 水平切边从 x=60 (翻回后的xc=W-740=60) 延伸到 W=800
# 这是对的，水平切边应该覆盖整个 cut 区宽度
# 但垂直切边在 x=60，从 y=0 延伸到 y=300，也是对的

# 真正的问题在哪里？让我看看水平切边的内凹角处理
print()
print("--- 内凹角区域检查 ---")
# 翻回后的内凹角: 在 TL 角落，翻回后位置在 (xc-T, xc) × (yc+1, yc+1+T)
# 翻回后: 水平翻转，所以 x→W-x
# 原翻转图中内凹角 x ∈ [xc-T, xc) = [740-71, 740) = [669, 740)
# 翻回后 x = W - x_flip ∈ (W-740, W-669] = (60, 131]
# 所以翻回后内凹角在 x ∈ [61, 131), y ∈ [301, 371)

# 检查翻回后的内凹角区域
for y_test in [298, 299, 300, 301, 302, 303, 304]:
    for x_test in [58, 59, 60, 61, 62, 63, 64, 65, 70, 80, 90, 100, 120, 130]:
        px = tuple(int(v) for v in out[y_test, x_test])
        if px != (255, 255, 255):
            print(f"  BORDER at ({x_test},{y_test}): {px}")
