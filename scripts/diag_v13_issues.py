"""
诊断问题1：V13 paint 在 retained area 侧 vs Step 3.5 的 black border
诊断问题2：V13 detect_border_v13 对棋盘格/花纹素材的误检
"""
import numpy as np
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.lshape_border import (
    detect_border_v13, patch_lshape_cut,
    detect_pool_material_borders, _v13_segv, _v13_pick,
    apply_lshape_border_completion,
)
from PIL import Image

# =============================================
# 测试1: 绽蔓的模拟 —— 棋盘格边框 + 内部花纹
# V13 可能把内容区误检为 border band
# =============================================
print("=" * 70)
print("测试1: 模拟 绽蔓 (棋盘格 + 花纹) 的 V13 检测行为")
print("=" * 70)

# 构造一个模拟素材：上边缘有棋盘格黑/白交替，然后是大区域内容花纹
H_sim, W_sim = 800, 1200
sim = np.full((H_sim, W_sim, 3), (200, 170, 130), dtype=np.uint8)  # 米色底色

# 上边缘 100px 做棋盘格（20px 方格交替黑白）
for y in range(100):
    for x in range(W_sim):
        block = (x // 20 + y // 20) % 2
        sim[y, x] = (0, 0, 0) if block else (255, 255, 255)

# 再往下 50px 做一些随机花纹（深色小方块）
for y in range(100, 300):
    for x in range(0, W_sim, 40):
        if ((x // 40) + (y // 40)) % 3 == 0:
            sim[y, x:x+15] = (30, 20, 10)

# 做 1D 段分析 —— 顶边中心列
col_center = sim[:min(H_sim//3, 400), W_sim//2]
print(f"\n顶边中心列前400px 1D段分析 (tol=12):")
segs = _v13_segv(col_center, tol=12)
for s in segs[:20]:
    w = s[1] - s[0] + 1
    print(f"  [{s[0]:3d}-{s[1]:3d}] w={w:3d} color=({s[2][0]:3d},{s[2][1]:3d},{s[2][2]:3d}) max={max(s[2])}")

# 用完整 detect_border_v13
sim_img = Image.fromarray(sim)
v13_result = detect_border_v13(sim_img)
print(f"\nV13 detect_border_v13 结果: {v13_result}")

if v13_result:
    edge_px, band_px, band_color = v13_result
    print(f"  edge (黑描边) = {edge_px}px —— 第一个深色段")
    print(f"  band (主带) = {band_px}px —— 宽度={band_px}, max(color)={max(band_color)}")
    if band_px > 100:
        print(f"  ⚠️ WARNING: band={band_px}px > 100px! 这很可能把内容区误检为 border band!")
        print(f"  ⚠️ 这会导致在 cut 区边缘绘制大段内容色条（即用户看到的灰色边框带）")

# 旧路径检测
old_result = detect_pool_material_borders(sim_img, (255, 255, 255))
print(f"\n旧路径 detect_pool_material_borders 结果: {old_result}")

# =============================================
# 测试2: 蝴蝶契约的模拟 —— 纯双层边框素材
# =============================================
print("\n" + "=" * 70)
print("测试2: 模拟 蝴蝶契约 (双层边框 + 内容)")
print("=" * 70)

H2, W2 = 800, 1200
sim2 = np.full((H2, W2, 3), (230, 220, 200), dtype=np.uint8)  # 浅色底

# 10px 黑边 + 30px 棕带 + 内容
sim2[:10, :] = (0, 0, 0)
sim2[10:40, :] = (128, 64, 0)
sim2[-10:, :] = (0, 0, 0)
sim2[-40:-10, :] = (128, 64, 0)
sim2[:, :10] = (0, 0, 0)
sim2[:, 10:40] = (128, 64, 0)
sim2[:, -10:] = (0, 0, 0)
sim2[:, -40:-10] = (128, 64, 0)

sim2_img = Image.fromarray(sim2)
v13_r2 = detect_border_v13(sim2_img)
print(f"\nV13 detect_border_v13 结果: {v13_r2}")
old_r2 = detect_pool_material_borders(sim2_img, (255, 255, 255))
print(f"旧路径结果: {old_r2}")

# =============================================
# 测试3: 检查 V13 paint 区域 vs Step 3.5 black border
# =============================================
print("\n" + "=" * 70)
print("测试3: V13 paint 与 Step 3.5 border_mask 的绘制区域对比")
print("=" * 70)

H_c, W_c = 500, 700
canvas = np.full((H_c, W_c, 3), (200, 180, 100), dtype=np.uint8)

# 模拟素材自带边框
canvas[:10, :] = (0, 0, 0)
canvas[10:40, :] = (128, 64, 0)
canvas[-10:, :] = (0, 0, 0)
canvas[-40:-10, :] = (128, 64, 0)
canvas[:, :10] = (0, 0, 0)
canvas[:, 10:40] = (128, 64, 0)
canvas[:, -10:] = (0, 0, 0)
canvas[:, -40:-10] = (128, 64, 0)

# 参数: inner_rect 在 (50,50), corner='br', cut 450x300
inner_x, inner_y = 50, 50
inner_w, inner_h = 600, 400
cut_w, cut_h = 150, 100
border_width = 10

# 1. 先填 cut 区为白（Step 3）
br_x0 = inner_x + inner_w - cut_w  # 50+600-150 = 500
br_y0 = inner_y + inner_h - cut_h  # 50+400-100 = 350
canvas[br_y0:br_y0+cut_h, br_x0:br_x0+cut_w] = 255
print(f"\nCut 区域: x=[{br_x0},{br_x0+cut_w}], y=[{br_y0},{br_y0+cut_h}] (白色)")

# 保存 Step 3 后的 canvas
canvas_after_step3 = canvas.copy()

# 2. Step 3.5: paint black border (只画 inner_rect 的 10px ring)
# 简化模拟：在 inner_rect 边缘 + cut 边缘画 black
# inner_rect = (50,50,600,400)
# cut 在 br: horizontal cut at y=br_y0=350, vertical cut at x=br_x0=500
step35_canvas = canvas_after_step3.copy()

# 内缩 10px 的 inner_rect
s_x0 = inner_x + border_width
s_y0 = inner_y + border_width
s_x1 = inner_x + inner_w - border_width
s_y1 = inner_y + inner_h - border_width

# 全 inner_rect mask
inner_mask = np.zeros((H_c, W_c), dtype=bool)
inner_mask[inner_y:inner_y+inner_h, inner_x:inner_x+inner_w] = True

# 内缩 inner_rect mask
shrunk_mask = np.zeros((H_c, W_c), dtype=bool)
shrunk_mask[s_y0:s_y1, s_x0:s_x1] = True

# L-shape cut mask
cut_mask = np.zeros((H_c, W_c), dtype=bool)
cut_mask[br_y0:br_y0+cut_h, br_x0:br_x0+cut_w] = True

# inner_mask = full inner minus cut
lshape_mask = inner_mask & ~cut_mask

# shrunk inner minus cut (for border inner)
shrunk_full_mask = np.zeros((H_c, W_c), dtype=bool)
shrunk_full_mask[s_y0:s_y1, s_x0:s_x1] = True
shrunk_cut_mask = np.zeros((H_c, W_c), dtype=bool)
sc_x0 = br_x0 - border_width if br_x0 >= inner_x + border_width else inner_x + border_width
sc_y0 = br_y0 - border_width if br_y0 >= inner_y + border_width else inner_y + border_width
sc_x1 = br_x0 + cut_w + border_width if br_x0 + cut_w <= inner_x + inner_w - border_width else inner_x + inner_w - border_width
sc_y1 = br_y0 + cut_h + border_width if br_y0 + cut_h <= inner_y + inner_h - border_width else inner_y + inner_h - border_width
shrunk_cut_mask[sc_y0:sc_y1, sc_x0:sc_x1] = True

# border_mask = lshape_mask minus (shrunk minus shrunk_cut)
border_mask = lshape_mask & ~(shrunk_full_mask & ~shrunk_cut_mask)

step35_canvas[border_mask] = (0, 0, 0)

print(f"\nStep 3.5 border_mask 统计:")
print(f"  总像素数: {border_mask.sum()}")
print(f"  黑色覆盖范围: y={np.where(border_mask.any(axis=1))[0].min()}..{np.where(border_mask.any(axis=1))[0].max()}, "
      f"x={np.where(border_mask.any(axis=0))[0].min()}..{np.where(border_mask.any(axis=0))[0].max()}")

# 3. V13 patch
sub = canvas_after_step3[inner_y:inner_y+inner_h, inner_x:inner_x+inner_w, :]
patched_v13 = patch_lshape_cut(sub, 'br',
    bx0=inner_w - cut_w,  # 600-150=450 (sub 数组坐标)
    by0=inner_h - cut_h,  # 400-100=300
    cw=cut_w, ch=cut_h,
    edge=10, band=30, color=(128, 64, 0))
v13_canvas = canvas_after_step3.copy()
v13_canvas[inner_y:inner_y+inner_h, inner_x:inner_x+inner_w, :] = patched_v13

# 采样 inside corner 附近
print(f"\nInside corner ({br_x0}, {br_y0}) 附近的绘制对比 (inner 角 = {br_x0},{br_y0}):")
print(f"{'Row':>5} {'Step3':>8} {'Step3.5':>8} {'V13':>8}")
for dy in range(-25, 26, 5):
    y = br_y0 + dy
    for dx in range(-25, 26, 5):
        x = br_x0 + dx
        if 0 <= y < H_c and 0 <= x < W_c:
            c1 = tuple(canvas_after_step3[y, x])
            c2 = tuple(step35_canvas[y, x])
            c3 = tuple(v13_canvas[y, x])
            label = f"y={y} x={x} ({dx:+d},{dy:+d}):"
            print(f"  {label}  Step3={c1}  Step3.5={c2}  V13={c3}")

# 保存对比图
Image.fromarray(step35_canvas).save(
    os.path.join(os.path.dirname(__file__), 'v13_compare_step35.png'))
Image.fromarray(v13_canvas).save(
    os.path.join(os.path.dirname(__file__), 'v13_compare_v13.png'))

print(f"\n保存: v13_compare_step35.png (Step 3.5 only) vs v13_compare_v13.png (V13 only)")
