"""
追踪修复后的检测逻辑，定位内层黑框丢失的原因
"""
import numpy as np
from PIL import Image
from core.corner.detection import (
    _detect_border_layers, _enforce_border_thickness_caps,
    _estimate_content_reference, _filter_layers_by_content_ref,
)

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

pos = W // 2
max_depth = 300
y_indices = np.arange(H - 1, H - 1 - max_depth, -1)
seq_orig = arr[y_indices, pos, :].astype(np.float64)

print("原始像素序列 (底部中心, 前260px):")
for i in range(min(260, len(seq_orig))):
    col = tuple(int(round(v)) for v in seq_orig[i])
    marker = ' ← 突变' if i > 0 and np.sqrt(np.sum((seq_orig[i] - seq_orig[i-1]) ** 2)) > 15 else ''
    print(f'  {i:3d}px: {col}{marker}')

# 平滑
window_size = 3
pad = window_size // 2
padded = np.pad(seq_orig, ((pad, pad), (0, 0)), mode='edge')
smoothed = np.zeros_like(seq_orig)
for j in range(3):
    smoothed[:, j] = np.convolve(padded[:, j], np.ones(window_size) / window_size, mode='valid')

print("\n平滑后序列 (前260px):")
for i in range(min(260, len(smoothed))):
    col = tuple(int(round(v)) for v in smoothed[i])
    print(f'  {i:3d}px: {col}')

# 计算颜色变化
COLOR_DIFF_THRESHOLD = 15
MIN_LAYER_THICKNESS = 2

diffs = np.sqrt(np.sum(np.diff(smoothed, axis=0) ** 2, axis=1))
change_mask = diffs > COLOR_DIFF_THRESHOLD
change_indices = np.where(change_mask)[0]

print(f'\n颜色变化位置 (diff > {COLOR_DIFF_THRESHOLD}):')
for ci in change_indices:
    col_b = tuple(int(round(v)) for v in smoothed[ci])
    col_a = tuple(int(round(v)) for v in smoothed[ci+1])
    print(f'  位置 {ci}: {col_b} -> {col_a}, diff={diffs[ci]:.1f}')

# 构建原始层
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
    marker = ' ← 薄层' if t < MIN_LAYER_THICKNESS else ''
    print(f'  层 {i}: thickness={t}px, color={c}{marker}')

# 应用修复: 抗锯齿带合并
merged_layers = []
for col, t in raw_layers:
    if t < MIN_LAYER_THICKNESS and merged_layers:
        prev_col, prev_t = merged_layers[-1]
        merged_layers[-1] = (prev_col, prev_t + t)
    else:
        merged_layers.append((col, t))

if merged_layers and merged_layers[0][1] < MIN_LAYER_THICKNESS and len(merged_layers) > 1:
    first_col, first_t = merged_layers[0]
    second_col, second_t = merged_layers[1]
    merged_layers[0] = (second_col, first_t + second_t)
    merged_layers.pop(1)

layers = [(c, t) for c, t in merged_layers if t >= MIN_LAYER_THICKNESS]

print(f'\n修复后层 (抗锯齿合并+薄过滤):')
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
bg_color = (245, 235, 215)
BG_THRESHOLD = 30
if bg_color is not None and layers:
    filtered: list[tuple[tuple[int, int, int], int]] = []
    for col, t in layers:
        d = float(np.sqrt(sum((a - b) ** 2 for a, b in zip(col, bg_color))))
        if d <= BG_THRESHOLD:
            print(f'  背景过滤: {col} -> d={d:.1f} <= {BG_THRESHOLD} ✗')
            continue
        filtered.append((col, t))
    layers = filtered

print(f'\n背景过滤后:')
for i, (c, t) in enumerate(layers):
    print(f'  层 {i}: thickness={t}px, color={c}')

# 最终: 期望 3 层
print(f'\n期望 3 层结构: 外层黑(≈{T_OUTER}px) + 间隙(≈{GAP}px) + 内层黑(≈{T_INNER}px)')
print(f'实际 {len(layers)} 层:')
for i, (c, t) in enumerate(layers):
    print(f'  层 {i}: thickness={t}px, color={c}')
