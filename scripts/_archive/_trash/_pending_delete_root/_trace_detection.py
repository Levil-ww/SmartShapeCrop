"""
深度追踪 _detect_border_layers 的内部流程
"""
import numpy as np
from PIL import Image
from core.corner.detection import (
    _detect_border_layers, _scan_edge_boundaries,
    _estimate_content_reference, _filter_layers_by_content_ref,
    BORDER_COLOR_DISTANCE_THRESHOLD,
    BORDER_MIN_LAYER_THICKNESS_PX,
    BORDER_BG_SIMILARITY_THRESHOLD,
    BORDER_SCAN_MAX_DEPTH_PX,
)

# 构造测试图 (合理分辨率)
W, H = 4410, 7087
arr = np.full((H, W, 3), 245, dtype=np.uint8)
T_OUTER, GAP, T_INNER = 120, 30, 100

# 画边框
for s, e, col in [(0, T_OUTER, (0,0,0)),
                  (T_OUTER+GAP, T_OUTER+GAP+T_INNER, (0,0,0))]:
    arr[s:e, :, :] = col
    arr[-e:-s, :, :] = col
    arr[:, s:e] = col
    arr[:, -e:-s] = col

# 抗锯齿带 (仅最外边)
for i in range(3):
    alpha = 1.0 - (i+1)/4.0
    mix = tuple(int(round((1-alpha)*245)) for _ in range(3))
    idx = T_OUTER + i
    arr[idx, :, :] = mix
    arr[-(idx+1), :, :] = mix
    arr[:, idx] = mix
    arr[:, -(idx+1)] = mix
    # 内层抗锯齿
    in_idx = T_OUTER + GAP + T_INNER + i
    if in_idx < H/2:
        arr[in_idx, :, :] = mix
        arr[-(in_idx+1), :, :] = mix
        arr[:, in_idx] = mix
        arr[:, -(in_idx+1)] = mix

# 花纹 (在中心区域)
np.random.seed(123)
margin = T_OUTER + GAP + T_INNER
for _ in range(80):
    fx = np.random.randint(margin + 100, W - margin - 100)
    fy = np.random.randint(margin + 100, H - margin - 100)
    s = np.random.randint(40, 90)
    arr[fy-s:fy+s, fx-s:fx+s] = np.random.randint(30, 80, size=(2*s, 2*s, 3))

img = Image.fromarray(arr, 'RGB')
bg_color = (245, 235, 215)

print("=" * 70)
print("追踪 _detect_border_layers 的扫描过程")
print("=" * 70)

COLOR_DIFF_THRESHOLD = BORDER_COLOR_DISTANCE_THRESHOLD
MIN_LAYER_THICKNESS = BORDER_MIN_LAYER_THICKNESS_PX
BG_THRESHOLD = BORDER_BG_SIMILARITY_THRESHOLD
max_scan_depth_px = BORDER_SCAN_MAX_DEPTH_PX

# 手动复现 _detect_border_layers 的扫描逻辑
print(f'\\n参数:')
print(f'  COLOR_DIFF_THRESHOLD = {COLOR_DIFF_THRESHOLD}')
print(f'  MIN_LAYER_THICKNESS = {MIN_LAYER_THICKNESS}')
print(f'  BG_THRESHOLD = {BG_THRESHOLD}')
print(f'  max_scan_depth_px = {max_scan_depth_px}')

# 取底部中点的颜色序列
depth = min(max_scan_depth_px, H // 4)
y_indices = np.arange(H - 1, H - 1 - depth, -1)
pos = W // 2
seq = arr[y_indices, pos, :].astype(np.float64)

print(f'\\n底部中点 ({pos}) 向内 {depth}px 的颜色序列:')
print(f'  位置 | 像素值 (RGB)')
for i in range(min(30, len(seq))):
    col = tuple(int(round(v)) for v in seq[i])
    print(f'  {i:3d}px | {col}')

# 平滑
window_size = 3
pad = window_size // 2
padded = np.pad(seq, ((pad, pad), (0, 0)), mode='edge')
smoothed = np.zeros_like(seq)
for j in range(3):
    smoothed[:, j] = np.convolve(padded[:, j], np.ones(window_size) / window_size, mode='valid')

print(f'\\n平滑后 (前30px):')
for i in range(min(30, len(smoothed))):
    col = tuple(int(round(v)) for v in smoothed[i])
    print(f'  {i:3d}px | {col}')

# 颜色变化检测
diffs = np.sqrt(np.sum(np.diff(smoothed, axis=0) ** 2, axis=1))
print(f'\\n颜色变化 (diff > {COLOR_DIFF_THRESHOLD}):')
for i in range(min(30, len(diffs))):
    marker = ' <-- 变化!' if diffs[i] > COLOR_DIFF_THRESHOLD else ''
    print(f'  位置 {i} -> {i+1}: diff={diffs[i]:.1f}{marker}')

# 构建层
change_mask = diffs > COLOR_DIFF_THRESHOLD
change_indices = np.where(change_mask)[0]
starts = np.concatenate(([0], change_indices + 1, [len(smoothed)]))
print(f'\\n层构建 (阈值 {MIN_LAYER_THICKNESS}px):')
layers_raw = []
for k in range(len(starts) - 1):
    s, e = int(starts[k]), int(starts[k + 1])
    thickness = e - s
    avg_color = np.mean(smoothed[s:e], axis=0)
    color_tuple = tuple(int(round(v)) for v in avg_color)
    col_str = f'  层 {k}: s={s}, e={e}, thickness={thickness}px, color={color_tuple}'
    if thickness >= MIN_LAYER_THICKNESS:
        print(col_str + ' ✓ (纳入)')
        layers_raw.append((color_tuple, thickness))
    else:
        print(col_str + ' ✗ (太薄, 被忽略)')

# 相邻同色合并
print(f'\\n相邻同色合并 (阈值 {COLOR_DIFF_THRESHOLD}):')
if len(layers_raw) >= 2:
    merged = []
    cur_col, cur_t = layers_raw[0]
    for col, t in layers_raw[1:]:
        d = float(np.sqrt(sum((a - b) ** 2 for a, b in zip(col, cur_col))))
        if d <= COLOR_DIFF_THRESHOLD:
            total_t = cur_t + t
            cur_col = tuple(int(round((cur_col[j] * cur_t + col[j] * t) / total_t)) for j in range(3))
            cur_t = total_t
            print(f'  合并 {cur_col}({cur_t}) + {col}({t}) -> d={d:.1f}')
        else:
            merged.append((cur_col, cur_t))
            cur_col, cur_t = col, t
            print(f'  保留 {cur_col}({cur_t})')
    merged.append((cur_col, cur_t))
    layers_raw = merged
    print(f'  合并后:')
    for i, (c, t) in enumerate(layers_raw):
        print(f'    层 {i}: {c}, thickness={t}px')

# 背景色过滤
print(f'\\n背景色过滤 (bg_color={bg_color}, threshold={BG_THRESHOLD}):')
filtered = []
for col, t in layers_raw:
    d = float(np.sqrt(sum((a - b) ** 2 for a, b in zip(col, bg_color))))
    if d <= BG_THRESHOLD:
        print(f'  {col} -> d={d:.1f} <= {BG_THRESHOLD} ✗ 过滤')
    else:
        print(f'  {col} -> d={d:.1f} > {BG_THRESHOLD} ✓ 保留')
        filtered.append((col, t))

# 内容参考色过滤
content_ref = _estimate_content_reference(img)
print(f'\\n内容参考色过滤 (content_ref={tuple(int(round(v)) for v in content_ref)}, threshold=35):')
filtered2 = _filter_layers_by_content_ref(filtered, content_ref)
for col, t in filtered:
    d = float(np.sqrt(sum((a - b) ** 2 for a, b in zip(col, content_ref))))
    if d <= 35.0:
        print(f'  {col} -> d={d:.1f} <= 35 ✗ 过滤')
    else:
        print(f'  {col} -> d={d:.1f} > 35 ✓ 保留')

print(f'\\n最终结果:')
for i, (c, t) in enumerate(filtered2):
    print(f'  层 {i}: {c}, thickness={t}px')

# 对比
print(f'\\n\\n实际期望: 外层黑({T_OUTER}px) + 米色间隙({GAP}px) + 内层黑({T_INNER}px)')
print(f'总期望深度: {T_OUTER + GAP + T_INNER}px')
