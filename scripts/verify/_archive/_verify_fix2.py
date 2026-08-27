"""
修正测试图构造，验证修复后的边框检测流程
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
    detect_nested_rect_layers, _scan_edge_boundaries,
)

# 构造正确的测试图
W, H = 4410, 7087
arr = np.full((H, W, 3), 245, dtype=np.uint8)
T_OUTER, GAP, T_INNER = 120, 30, 100

# 正确画边框 (修复之前的 slice bug)
arr[0:T_OUTER, :, :] = (0,0,0)           # top: rows 0-119
arr[H-T_OUTER:H, :, :] = (0,0,0)          # bottom: rows 6967-7086
arr[:, 0:T_OUTER] = (0,0,0)               # left: cols 0-119
arr[:, W-T_OUTER:W] = (0,0,0)             # right: cols 4290-4409

arr[T_OUTER+GAP:T_OUTER+GAP+T_INNER, :, :] = (0,0,0)  # top inner: rows 150-249
arr[H-(T_OUTER+GAP+T_INNER):H-(T_OUTER+GAP), :, :] = (0,0,0)  # bottom inner
arr[:, T_OUTER+GAP:T_OUTER+GAP+T_INNER] = (0,0,0)  # left inner
arr[:, W-(T_OUTER+GAP+T_INNER):W-(T_OUTER+GAP)] = (0,0,0)  # right inner

# 抗锯齿带 (修正位置)
for i in range(3):
    alpha = 1.0 - (i+1)/4.0
    mix = tuple(int(round((1-alpha)*245)) for _ in range(3))
    
    # 外层边框的抗锯齿 (top)
    idx = T_OUTER + i  # 120, 121, 122
    arr[idx, :, :] = mix
    arr[:, idx] = mix
    
    # 外层边框的抗锯齿 (bottom)
    arr[H-1-idx, :, :] = mix
    arr[:, W-1-idx] = mix
    
    # 内层边框的抗锯齿 (top)
    in_idx = T_OUTER + GAP + T_INNER + i  # 250, 251, 252
    arr[in_idx, :, :] = mix
    arr[:, in_idx] = mix
    
    # 内层边框的抗锯齿 (bottom)
    arr[H-1-in_idx, :, :] = mix
    arr[:, W-1-in_idx] = mix

# 花纹 (在中心区域，避开边框)
np.random.seed(123)
margin = T_OUTER + GAP + T_INNER + 100  # 350
for _ in range(80):
    fx = np.random.randint(margin, W - margin)
    fy = np.random.randint(margin, H - margin)
    s = np.random.randint(40, 90)
    arr[fy-s:fy+s, fx-s:fx+s] = np.random.randint(30, 80, size=(2*s, 2*s, 3))

img = Image.fromarray(arr, 'RGB')
bg_color = (245, 235, 215)

# 验证底部中心像素序列
pos = W // 2
depth = 300
seq = []
for dy in range(depth):
    y = H - 1 - dy
    col = tuple(arr[y, pos, :])
    seq.append(col)

print("底部中心像素序列 (前50px):")
for i in range(50):
    print(f"  {i:3d}px: {seq[i]}")

# 检测边框层
print('\n' + '=' * 70)
print("边框检测结果")
print('=' * 70)

# Step 1: _detect_border_layers
layers_raw = _detect_border_layers(img, max_scan_depth_px=300, bg_color=bg_color)
print('\n1. _detect_border_layers:')
for i, (c, t) in enumerate(layers_raw):
    print(f'  层 {i}: color={c}, thickness={t}px')

# Step 2: 内容参考色过滤
content_ref = _estimate_content_reference(img)
print(f'\n2. 内容参考色: {tuple(int(round(v)) for v in content_ref)}')
layers_filtered = _filter_layers_by_content_ref(layers_raw, content_ref)
print('   过滤后:')
for i, (c, t) in enumerate(layers_filtered):
    print(f'  层 {i}: color={c}, thickness={t}px')

# Step 3: 厚度硬上限
layers_final = _enforce_border_thickness_caps(layers_filtered)
print('\n3. 最终 (厚度硬上限后):')
for i, (c, t) in enumerate(layers_final):
    print(f'  层 {i}: color={c}, thickness={t}px')

total_depth = sum(t for _, t in layers_final)
print(f'\n总边框深度: {total_depth}px')
print(f'实际期望结构 (从外到内):')
print(f'  外层黑: ≈{T_OUTER}px, 间隙: ≈{GAP}px, 内层黑: ≈{T_INNER}px')
print(f'  合计: ≈{T_OUTER+GAP+T_INNER}px')

# Step 4: 嵌套矩形
print('\n4. 嵌套矩形检测:')
rects = detect_nested_rect_layers(img, border_layers=layers_final)
for i, r in enumerate(rects):
    print(f'  层 {i}: (x1={r[0]},y1={r[1]},x2={r[2]},y2={r[3]})')

# 验证
print(f'\n验证:')
cumulative = 0
for i, (_, t) in enumerate(layers_final):
    cumulative += t
    if i < len(rects):
        r = rects[i]
        print(f'  层 {i}: 累计深度={cumulative}px, rect.x1={r[0]}px, rect.y1={r[1]}px')
