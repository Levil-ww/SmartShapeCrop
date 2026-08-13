"""
对比旧逻辑 vs 修复后逻辑的厚边框圆角效果差异
直接模拟 _build_multi_layer_corner_mask 中的 ring_lower_bound 计算
以及 sector_render 中的 effective_border_depth 计算
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PIL import Image
import numpy as np

# 模拟用户场景：厚边框 + 小圆角
# ===== 用户实际场景参数 =====
DPI_REAL = 150
CORNER_R_CM = 2.0
BORDER_THICK_CM = 2.0  # 外层棕色边框约 2cm 厚

r_px = int(CORNER_R_CM * DPI_REAL / 2.54)  # 2cm @150DPI = 118px
border_px = int(BORDER_THICK_CM * DPI_REAL / 2.54)  # 118px
raw_depth = border_px + 2  # 加上白隙/黑线
print(f"=== 用户实际场景 (真实DPI={DPI_REAL}) ===")
print(f"圆角半径 R: {r_px}px ({CORNER_R_CM}cm)")
print(f"检测到的边框累计厚度 raw_depth: {raw_depth}px (~{raw_depth*2.54/DPI_REAL:.2f}cm)")
print()

# === 旧逻辑 (修复前) ===
old_ring = max(0, min(raw_depth + 4, int(r_px * 0.5)))
old_ratio = old_ring * 100.0 / raw_depth
print(f"[修复前] ring_lower_bound = min(raw_depth+4, R*0.5)")
print(f"       = min({raw_depth+4}, {int(r_px*0.5)}) = {old_ring}px")
print(f"       只保护了 {old_ratio:.1f}% 的边框厚度！")
print(f"       内层 {raw_depth - old_ring}px ({(raw_depth-old_ring)*2.54/DPI_REAL:.2f}cm) 的边框")
print(f"       → validity_mask 不允许重绘，花纹残留 → 视觉上弧线明显比直边细")

print()
old_eff_depth = min(177, int(0.7 * r_px), raw_depth)
print(f"[修复前] effective_border_depth = min(177, 0.7*R, total)")
print(f"         = min(177, {int(0.7*r_px)}, {raw_depth}) = {old_eff_depth}px")
print(f"         超过 {old_eff_depth}px 的层被强制为 gap（多层边框时出问题）")

print("\n" + "=" * 60)

# === 新逻辑 (修复后) ===
new_ring = max(0, min(raw_depth + 8, r_px - 2))
new_ratio = new_ring * 100.0 / raw_depth
print(f"\n[修复后] ring_lower_bound = min(raw_depth+8, R-2)")
print(f"       = min({raw_depth+8}, {r_px-2}) = {new_ring}px")
print(f"       保护了 {new_ratio:.1f}% 的边框厚度！")
uncovered = max(0, raw_depth - new_ring)
print(f"       未覆盖的最内层: {uncovered}px ({uncovered*2.54/DPI_REAL:.2f}cm)")
print(f"       → 几乎整条边框都在 validity_mask 重绘范围内，弧线粗细与直边一致")

print()
new_eff_depth = min(177, int(1.0 * r_px), raw_depth)
print(f"[修复后] effective_border_depth = min(177, 1.0*R, total)")
print(f"         = min(177, {int(1.0*r_px)}, {raw_depth}) = {new_eff_depth}px")
print(f"         真实检测到的边框层都在有效深度内，不被误判为 forced_gap")


# === 极端场景：更厚边框 (3cm) + 小圆角 (2cm) ===
THICK_BORDER_CM = 3.0
thick_border_px = int(THICK_BORDER_CM * DPI_REAL / 2.54)
raw_depth2 = thick_border_px
print(f"\n\n=== 极端场景: 3cm厚边框 + 2cm圆角 ===")
print(f"圆角半径 R: {r_px}px, 边框累计厚 raw_depth={raw_depth2}px")

old_ring2 = max(0, min(raw_depth2 + 4, int(r_px * 0.5)))
print(f"\n[修复前] ring_lower_bound = {old_ring2}px")
print(f"       {old_ring2*100.0/raw_depth2:.1f}% 的边框被保护，"
      f"内层 {raw_depth2-old_ring2}px ({(raw_depth2-old_ring2)*2.54/DPI_REAL:.2f}cm) 丢失！")

new_ring2 = max(0, min(raw_depth2 + 8, r_px - 2))
print(f"[修复后] ring_lower_bound = {new_ring2}px")
print(f"       {new_ring2*100.0/raw_depth2:.1f}% 的边框被保护（被 R-2 限制，但已覆盖圆角半径内的全部边框）")
print(f"       注：R={r_px}px，边框厚 {raw_depth2}px。超过 R 的部分在几何上不可能在弧线上")
print(f"       因为弧线 = depth ∈ [0, R]，所以 ring=R-2 完全覆盖了圆角弧线的所有边框像素")
