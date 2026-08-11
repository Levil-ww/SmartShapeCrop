"""
深度追踪修复后的 _detect_border_layers
"""
import numpy as np
from PIL import Image
from core.corner.detection import (
    _detect_border_layers, _estimate_content_reference,
    _filter_layers_by_content_ref, _enforce_border_thickness_caps,
    _scan_edge_boundaries, detect_nested_rect_layers,
)

W, H = 4410, 7087
arr = np.full((H, W, 3), 245, dtype=np.uint8)
T_OUTER, GAP, T_INNER = 120, 30, 100

for s, e, col in [(0, T_OUTER, (0,0,0)),
                  (T_OUTER+GAP, T_OUTER+GAP+T_INNER, (0,0,0))]:
    arr[s:e, :, :] = col
    arr[-e:-s, :, :] = col
    arr[:, s:e] = col
    arr[:, -e:-s] = col

for i in range(3):
    alpha = 1.0 - (i+1)/4.0
    mix = tuple(int(round((1-alpha)*245)) for _ in range(3))
    idx = T_OUTER + i
    arr[idx, :, :] = mix
    arr[-(idx+1), :, :] = mix
    arr[:, idx] = mix
    arr[:, -(idx+1)] = mix
    in_idx = T_OUTER + GAP + T_INNER + i
    if in_idx < H/2:
        arr[in_idx, :, :] = mix
        arr[-(in_idx+1), :, :] = mix
        arr[:, in_idx] = mix
        arr[:, -(in_idx+1)] = mix

np.random.seed(123)
margin = T_OUTER + GAP + T_INNER
for _ in range(80):
    fx = np.random.randint(margin + 100, W - margin - 100)
    fy = np.random.randint(margin + 100, H - margin - 100)
    s = np.random.randint(40, 90)
    arr[fy-s:fy+s, fx-s:fx+s] = np.random.randint(30, 80, size=(2*s, 2*s, 3))

img = Image.fromarray(arr, 'RGB')
bg_color = (245, 235, 215)

# === 手动追踪新的检测逻辑 ===
COLOR_DIFF_THRESHOLD = 15
MIN_LAYER_THICKNESS = 2
BG_THRESHOLD = 30

# 扫描
max_scan_depth_px = 300
pos = W // 2
depth = min(max_scan_depth_px, H // 4)
y_indices = np.arange(H - 1, H - 1 - depth, -1)
seq = arr[y_indices, pos, :].astype(np.float64)

# 平滑
window_size = 3
pad = window_size // 2
padded = np.pad(seq, ((pad, pad), (0, 0)), mode='edge')
smoothed = np.zeros_like(seq)
for j in range(3):
    smoothed[:, j] = np.convolve(padded[:, j], np.ones(window_size) / window_size, mode='valid')

diffs = np.sqrt(np.sum(np.diff(smoothed, axis=0) ** 2, axis=1))
change_mask = diffs > COLOR_DIFF_THRESHOLD
change_indices = np.where(change_mask)[0]

print(f'颜色变化位置 (diff > {COLOR_DIFF_THRESHOLD}): {len(change_indices)} 处')
for ci in change_indices[:20]:
    col_b = tuple(int(round(v)) for v in smoothed[ci])
    col_a = tuple(int(round(v)) for v in smoothed[ci+1])
    print(f'  位置 {ci}: {col_b} -> {col_a}, diff={diffs[ci]:.1f}')

# 层构建（新逻辑：包含所有层 + 抗锯齿合并）
raw_layers = []
starts = np.concatenate(([0], change_indices + 1, [len(smoothed)]))
for k in range(len(starts) - 1):
    s, e = int(starts[k]), int(starts[k + 1])
    thickness = e - s
    avg_color = np.mean(smoothed[s:e], axis=0)
    color_tuple = tuple(int(round(v)) for v in avg_color)
    raw_layers.append((color_tuple, thickness))

print(f'\n原始层 ({len(raw_layers)} 层):')
for i, (c, t) in enumerate(raw_layers):
    marker = ' <-- 薄层(抗锯齿)' if t < MIN_LAYER_THICKNESS else ''
    print(f'  层 {i}: thickness={t}px, color={c}{marker}')

# 抗锯齿合并
merged_layers = []
for col, t in raw_layers:
    if t < MIN_LAYER_THICKNESS and merged_layers:
        prev_col, prev_t = merged_layers[-1]
        total_t = prev_t + t
        new_col = tuple(int(round((prev_col[j] * prev_t + col[j] * t) / total_t)) for j in range(3))
        merged_layers[-1] = (new_col, total_t)
    else:
        merged_layers.append((col, t))

if merged_layers and merged_layers[0][1] < MIN_LAYER_THICKNESS and len(merged_layers) > 1:
    first_col, first_t = merged_layers[0]
    second_col, second_t = merged_layers[1]
    total_t = first_t + second_t
    new_col = tuple(int(round((second_col[j] * second_t + first_col[j] * first_t) / total_t)) for j in range(3))
    merged_layers[0] = (new_col, total_t)
    merged_layers.pop(1)

print(f'\n抗锯齿合并后:')
for i, (c, t) in enumerate(merged_layers):
    marker = ' <-- 保留' if t >= MIN_LAYER_THICKNESS else ' <-- 丢弃(太薄)'
    print(f'  层 {i}: thickness={t}px, color={c}{marker}')

layers = [(c, t) for c, t in merged_layers if t >= MIN_LAYER_THICKNESS]
print(f'\n过滤薄层后:')
for i, (c, t) in enumerate(layers):
    print(f'  层 {i}: thickness={t}px, color={c}')

# 相邻同色合并
if len(layers) >= 2:
    merged: list[tuple[tuple[int, int, int], int]] = []
    cur_col, cur_t = layers[0]
    for col, t in layers[1:]:
        d = float(np.sqrt(sum((a - b) ** 2 for a, b in zip(col, cur_col))))
        if d <= COLOR_DIFF_THRESHOLD:
            total_t = cur_t + t
            cur_col = tuple(int(round((cur_col[j] * cur_t + col[j] * t) / total_t)) for j in range(3))
            cur_t = total_t
        else:
            merged.append((cur_col, cur_t))
            cur_col, cur_t = col, t
    merged.append((cur_col, cur_t))
    layers = merged

print(f'\n相邻同色合并后:')
for i, (c, t) in enumerate(layers):
    print(f'  层 {i}: thickness={t}px, color={c}')

# 背景色过滤
if bg_color is not None and layers:
    filtered: list[tuple[tuple[int, int, int], int]] = []
    for col, t in layers:
        d = float(np.sqrt(sum((a - b) ** 2 for a, b in zip(col, bg_color))))
        if d <= BG_THRESHOLD:
            print(f'  背景过滤: {col} -> d={d:.1f} <= {BG_THRESHOLD} ✗')
            continue
        print(f'  背景保留: {col} -> d={d:.1f} > {BG_THRESHOLD} ✓')
        filtered.append((col, t))
    layers = filtered

print(f'\n背景过滤后:')
for i, (c, t) in enumerate(layers):
    print(f'  层 {i}: thickness={t}px, color={c}')

# 检查期望的结果
print(f'\n期望结构:')
print(f'  外层黑: color≈(0,0,0), thickness≈{T_OUTER}px')
print(f'  背景间隙: color≈(245,245,245), thickness≈{GAP}px')
print(f'  内层黑: color≈(0,0,0), thickness≈{T_INNER}px')
print(f'  花纹(内容区): color≈(30-80,30-80,30-80)')

# 问题总结
if len(layers) < 2:
    print(f'\n❌ 问题：只检测到 {len(layers)} 层，预期 3 层')
    print('   - 外层黑(120px) → 被合并或过滤')
    print('   - 背景间隙(30px) → 被合并或过滤')
    print('   - 内层黑(100px) → 被合并或过滤')
elif len(layers) == 2:
    print(f'\n⚠️  问题：只检测到 2 层，预期 3 层')
    print('   - 可能外层黑+背景间隙被合并了')
    print('   - 或者背景间隙+内层黑被合并了')
else:
    print(f'\n✓ 检测到 {len(layers)} 层')
