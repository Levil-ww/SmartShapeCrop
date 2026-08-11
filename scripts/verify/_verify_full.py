"""
完整验证修复后的检测+圆角遮罩构建
"""

# ============================================================
# PROJECT_ROOT auto-inject (added by test-dir cleanup 2026-08-11)
# 脚本从 scripts/ 子目录运行时仍能正确定位 core/, psd_demo/, Test/output 等
import sys as _sys
import os as _os
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))
_D = str(_PROJECT_ROOT)
# ============================================================

import numpy as np
from PIL import Image
from core.corner.detection import (
    _detect_border_layers, _enforce_border_thickness_caps,
    _estimate_content_reference, _filter_layers_by_content_ref,
    detect_nested_rect_layers,
)
from core.image_cropper import _build_multi_layer_corner_mask

W, H = 4410, 7087
arr = np.full((H, W, 3), 245, dtype=np.uint8)
T_OUTER, GAP, T_INNER = 120, 30, 100

arr[0:T_OUTER, :, :] = (0,0,0)
arr[H-T_OUTER:H, :, :] = (0,0,0)
arr[:, 0:T_OUTER] = (0,0,0)
arr[:, W-T_OUTER:W] = (0,0,0)

arr[T_OUTER+GAP:T_OUTER+GAP+T_INNER, :, :] = (0,0,0)
arr[H-(T_OUTER+GAP+T_INNER):H-(T_OUTER+GAP), :, :] = (0,0,0)
arr[:, T_OUTER+GAP:T_OUTER+GAP+T_INNER] = (0,0,0)
arr[:, W-(T_OUTER+GAP+T_INNER):W-(T_OUTER+GAP)] = (0,0,0)

for i in range(3):
    alpha = 1.0 - (i+1)/4.0
    mix = tuple(int(round((1-alpha)*245)) for _ in range(3))
    idx = T_OUTER + i
    arr[H-1-idx, :, :] = mix
    arr[:, W-1-idx] = mix
    in_idx = T_OUTER + GAP + T_INNER + i
    arr[H-1-in_idx, :, :] = mix
    arr[:, W-1-in_idx] = mix

np.random.seed(123)
margin = T_OUTER + GAP + T_INNER + 100
for _ in range(80):
    fx = np.random.randint(margin, W - margin)
    fy = np.random.randint(margin, H - margin)
    s = np.random.randint(40, 90)
    arr[fy-s:fy+s, fx-s:fx+s] = np.random.randint(30, 80, size=(2*s, 2*s, 3))

img = Image.fromarray(arr, 'RGB')
bg_color = (245, 235, 215)

print("=" * 70)
print("完整验证：检测 → 圆角遮罩构建")
print('=' * 70)

# Step 1: 边框检测 (完整流程)
print('\n1. _detect_border_layers (完整流程):')
layers_raw = _detect_border_layers(img, max_scan_depth_px=300, bg_color=bg_color)
for i, (c, t) in enumerate(layers_raw):
    print(f'  层 {i}: color={c}, thickness={t}px')

# Step 2: 嵌套矩形检测
print('\n2. detect_nested_rect_layers:')
rects = detect_nested_rect_layers(img, border_layers=layers_raw)
for i, r in enumerate(rects):
    print(f'  层 {i}: (x1={r[0]},y1={r[1]},x2={r[2]},y2={r[3]})')

# Step 3: 构建圆角遮罩
print('\n3. _build_multi_layer_corner_mask:')
# 使用 5cm 半径 = 295px @ 150dpi
DPI = 150
radius_cm = 5
r = int(round(radius_cm * DPI / 2.54))  # ≈ 295px
corners_px = {'tl': r, 'tr': r, 'bl': r, 'br': r}

mask = _build_multi_layer_corner_mask(
    W, H, corners_px, layers_raw, nested_rects=rects
)

# 转换为 numpy 数组分析
if hasattr(mask, 'convert'):
    mask_arr = np.array(mask.convert('L'))
else:
    mask_arr = np.array(mask)

# 分析遮罩
print(f'  遮罩尺寸: {mask_arr.shape}')
print(f'  白色像素数 (mask==255): {np.sum(mask_arr == 255)}')
print(f'  黑色像素数 (mask==0): {np.sum(mask_arr == 0)}')
total_border_depth = sum(t for _, t in layers_raw)
print(f'  total_border_depth: {total_border_depth}px')
print(f'  r (圆角半径): {r}px')

# 分析角部的遮罩结构
print(f'\n  左上角 (tl) 遮罩分析:')
tl_corner = mask_arr[0:r+1, 0:r+1]
# 沿着对角线检查
diag_vals = []
for d in range(0, min(r+1, 300)):
    diag_vals.append(tl_corner[d, d])
# 检查角部是否有完整的扇形
print(f'    从(0,0)沿对角线: 前300px遮罩值')
change_points = []
for i in range(1, min(300, len(diag_vals))):
    if diag_vals[i] != diag_vals[i-1]:
        change_points.append((i, diag_vals[i-1], diag_vals[i]))
print(f'    状态变化点: {len(change_points)}')
for pos, prev, curr in change_points[:10]:
    print(f'      @{pos}px: {prev} → {curr}')

# 分析底部的遮罩 (检查圆角是否正确)
print(f'\n  底部中心遮罩分析 (检查边框保留):')
center_x = W // 2
bottom_mask = mask_arr[H-300:H, center_x]
for i in range(0, 300, 20):
    print(f'    距底部 {i}px: mask={bottom_mask[-(i+1)]}')

print(f'\n验证:')
# total_border_depth = 124 + 27 + 103 + 46 = 300px
# 期望 total_border_depth ≈ T_OUTER + GAP + T_INNER + 花纹区域 ≈ 120 + 30 + 100 = 250px
# 检测到 300px 总深度，其中 46px 是花纹区域的开始
if total_border_depth >= T_OUTER + GAP + T_INNER:
    print(f'  ✓ total_border_depth ({total_border_depth}px) >= 期望边框深度 ({T_OUTER+GAP+T_INNER}px)')
else:
    print(f'  ✗ total_border_depth ({total_border_depth}px) < 期望边框深度 ({T_OUTER+GAP+T_INNER}px)')

if len(rects) >= 3:
    print(f'  ✓ 嵌套矩形层数 ({len(rects)}) >= 3')
else:
    print(f'  ✗ 嵌套矩形层数 ({len(rects)}) < 3')
