"""
最小化复现：直接测试 V13 flip + fill 逻辑
"""
import numpy as np
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.lshape_border import patch_lshape_cut, _fill_vertical_horizontal
from core.geometry import RectShape

# 构造一个简单画布 (700x400), 底部-right角 挖角 (corner='br', cw=150, ch=100)
H, W = 400, 700
arr = np.full((H, W, 3), (200, 180, 100), dtype=np.uint8)  # 米色底

# 模拟素材边框：给画布右边和下边画一条 10px 黑带 + 30px 棕色带
arr[370:400, :, :] = (128, 64, 0)  # 底部 30px 棕色带
arr[390:400, :, :] = (0, 0, 0)     # 底部 10px 黑带
arr[:, 670:700, :] = (128, 64, 0)  # 右侧 30px 棕色带
arr[:, 690:700, :] = (0, 0, 0)     # 右侧 10px 黑带

# 挖角区域填白 (corner='br')
arr[300:400, 550:700, :] = 255  # white

print("=== 原始画布（挖角后）===")
# 打印几个关键位置
print(f"  子图尺寸: {H}x{W}")
print(f"  挖角: corner=br, cw=150, ch=100")
print(f"  挖角矩形: x=550..700, y=300..400")
print(f"  挖角左上点(inside corner): (550, 300)")

# 颜色采样
print(f"\n=== 颜色采样（挖角边缘附近）===")
inside_x, inside_y = 550, 300  # inside corner
for dy in range(-15, 16):
    y = inside_y + dy
    row_colors = arr[y, inside_x-5:inside_x+10, :]
    cols_str = "".join([f"{r}" if r < 50 else "." for r,_,_ in row_colors])
    print(f"  y={y:3d} (dy={dy:+2d}): {cols_str}")

# 测试 patch_lshape_cut
print("\n=== 调用 patch_lshape_cut ===")
patched = patch_lshape_cut(arr, 'br', 550, 300, 150, 100, 10, 30, (128, 64, 0))
print("  patch_lshape_cut 完成")

# 保存结果
from PIL import Image
out_dir = os.path.dirname(__file__)
Image.fromarray(arr).save(os.path.join(out_dir, 'v13_test_before.png'))
Image.fromarray(patched).save(os.path.join(out_dir, 'v13_test_after.png'))
print(f"\n  保存: v13_test_before.png, v13_test_after.png")

# 采样 patched 版本
print(f"\n=== patch后颜色采样（挖角边缘附近）===")
for dy in range(-20, 21):
    y = inside_y + dy
    row_colors = patched[y, inside_x-10:inside_x+15, :]
    cols_str = "".join([f"{r}" if r < 50 else "x" if r > 200 else "o" for r,_,_ in row_colors])
    print(f"  y={y:3d} (dy={dy:+2d}): {cols_str}  white={int(patched[y, inside_x, 0]) if inside_x < 700 else 'OOB'}")

# 对比差异
diff = (arr != patched).any(axis=2)
print(f"\n=== patch 修改区域统计 ===")
ys, xs = np.where(diff)
if len(ys) > 0:
    print(f"  修改像素数: {len(ys)}")
    print(f"  y 范围: {ys.min()} ~ {ys.max()}")
    print(f"  x 范围: {xs.min()} ~ {xs.max()}")
    # 分组打印修改区域
    print("\n  修改区域详情:")
    for y in range(ys.min(), ys.max()+1):
        row_diff = xs[ys == y]
        if len(row_diff) > 0:
            rng = f"{row_diff.min()}-{row_diff.max()}"
            sample = patched[y, row_diff[0], :]
            print(f"    y={y}: x={rng} px, 新颜色=({sample[0]},{sample[1]},{sample[2]})")
else:
    print("  无修改！")
