"""
逐步追踪 _get_border_layers_robust 流程
"""
import numpy as np
from PIL import Image
from core.corner.detection import (
    _detect_border_layers, _enforce_border_thickness_caps,
    _estimate_content_reference, _filter_layers_by_content_ref,
    BORDER_COLOR_DISTANCE_THRESHOLD,
    BORDER_MIN_LAYER_THICKNESS_PX,
    BORDER_BG_SIMILARITY_THRESHOLD,
    BORDER_SCAN_MAX_DEPTH_PX,
)

# 正确的测试图
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

img = Image.fromarray(arr, 'RGB')
bg_color = (245, 235, 215)

print("=" * 70)
print("逐步追踪 _get_border_layers_robust")
print('=' * 70)

# Step 0: 内容参考色
print('\nStep 0: 内容参考色估算')
content_ref = _estimate_content_reference(img)
print(f'  content_ref = {tuple(int(round(v)) for v in content_ref)}')

# Step 1: _detect_border_layers
print('\nStep 1: _detect_border_layers(img, bg_color=bg_color)')
print(f'  参数: max_scan_depth_px={BORDER_SCAN_MAX_DEPTH_PX}, bg_color={bg_color}')
print(f'  内部参数: COLOR_DIFF_THRESHOLD={BORDER_COLOR_DISTANCE_THRESHOLD}')
print(f'           MIN_LAYER_THICKNESS={BORDER_MIN_LAYER_THICKNESS_PX}')
print(f'           BG_THRESHOLD={BORDER_BG_SIMILARITY_THRESHOLD}')

layers_step1 = _detect_border_layers(img, bg_color=bg_color)
for i, (c, t) in enumerate(layers_step1):
    d_bg = float(np.sqrt(sum((a - b) ** 2 for a, b in zip(c, bg_color))))
    d_ref = float(np.sqrt(sum((a - b) ** 2 for a, b in zip(c, content_ref))))
    print(f'  层 {i}: color={c}, thickness={t}px, d_to_bg={d_bg:.1f}, d_to_content={d_ref:.1f}')

# Step 2: 内容参考色过滤
print('\nStep 2: _filter_layers_by_content_ref(layers, content_ref)')
layers_step2 = _filter_layers_by_content_ref(layers_step1, content_ref)
print(f'  输入 {len(layers_step1)} 层, 输出 {len(layers_step2)} 层')
for i, (c, t) in enumerate(layers_step2):
    print(f'  层 {i}: color={c}, thickness={t}px')

# Step 3: 厚度硬上限
print('\nStep 3: _enforce_border_thickness_caps(layers)')
layers_step3 = _enforce_border_thickness_caps(layers_step2)
print(f'  输入 {len(layers_step2)} 层, 输出 {len(layers_step3)} 层')
for i, (c, t) in enumerate(layers_step3):
    print(f'  层 {i}: color={c}, thickness={t}px')

# 对比手动追踪的结果
print('\n' + '=' * 70)
print('对比: 手动追踪 vs 实际函数')
print('=' * 70)

# 手动追踪 (和之前 _trace_fix2.py 一样)
COLOR_DIFF_THRESHOLD = 15
MIN_LAYER_THICKNESS = 2
BG_THRESHOLD = 30

pos = W // 2
max_depth = BORDER_SCAN_MAX_DEPTH_PX
y_indices = np.arange(H - 1, H - 1 - max_depth, -1)
seq = arr[y_indices, pos, :].astype(np.float64)

window_size = 3
pad = window_size // 2
padded = np.pad(seq, ((pad, pad), (0, 0)), mode='edge')
smoothed = np.zeros_like(seq)
for j in range(3):
    smoothed[:, j] = np.convolve(padded[:, j], np.ones(window_size) / window_size, mode='valid')

diffs = np.sqrt(np.sum(np.diff(smoothed, axis=0) ** 2, axis=1))
change_mask = diffs > COLOR_DIFF_THRESHOLD
change_indices = np.where(change_mask)[0]

raw_layers = []
starts = np.concatenate(([0], change_indices + 1, [len(smoothed)]))
for k in range(len(starts) - 1):
    s, e = int(starts[k]), int(starts[k + 1])
    thickness = e - s
    avg_color = np.mean(smoothed[s:e], axis=0)
    color_tuple = tuple(int(round(v)) for v in avg_color)
    raw_layers.append((color_tuple, thickness))

# 手动执行抗锯齿合并
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

layers_manual = [(c, t) for c, t in merged_layers if t >= MIN_LAYER_THICKNESS]

# 手动: 相邻同色合并
if len(layers_manual) >= 2:
    merged_m: list[tuple[tuple[int, int, int], int]] = []
    cur_col, cur_t = layers_manual[0]
    for col, t in layers_manual[1:]:
        d = float(np.sqrt(sum((a - b) ** 2 for a, b in zip(col, cur_col))))
        if d <= COLOR_DIFF_THRESHOLD:
            total_t = cur_t + t
            cur_col = tuple(int(round((cur_col[j] * cur_t + col[j] * t) / total_t)) for j in range(3))
            cur_t = total_t
        else:
            merged_m.append((cur_col, cur_t))
            cur_col, cur_t = col, t
    merged_m.append((cur_col, cur_t))
    layers_manual = merged_m

# 手动: 背景过滤
if bg_color is not None and layers_manual:
    filtered_m: list[tuple[tuple[int, int, int], int]] = []
    for col, t in layers_manual:
        d = float(np.sqrt(sum((a - b) ** 2 for a, b in zip(col, bg_color))))
        if d <= BG_THRESHOLD:
            continue
        filtered_m.append((col, t))
    layers_manual = filtered_m

print(f'手动追踪结果 ({len(layers_manual)} 层):')
for i, (c, t) in enumerate(layers_manual):
    print(f'  层 {i}: color={c}, thickness={t}px')

print(f'\n实际函数结果 ({len(layers_step1)} 层):')
for i, (c, t) in enumerate(layers_step1):
    print(f'  层 {i}: color={c}, thickness={t}px')

if len(layers_step1) != len(layers_manual):
    print(f'\n❌ 层数不一致! 手动={len(layers_manual)}, 实际={len(layers_step1)}')
    print('  检查: _detect_border_layers 内部是否有其他过滤步骤')
else:
    print(f'\n✓ 层数一致')
