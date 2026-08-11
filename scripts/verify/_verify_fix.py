"""
验证修复后的 _detect_border_layers 函数
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
print("验证修复后的边框检测流程")
print("=" * 70)

# 1. _detect_border_layers (含抗锯齿合并 + 背景过滤)
print('\n1. _detect_border_layers (抗锯齿带合并 + 背景过滤):')
layers_raw = _detect_border_layers(img, max_scan_depth_px=300, bg_color=bg_color)
for i, (c, t) in enumerate(layers_raw):
    print(f'  层 {i}: color={c}, thickness={t}px')

# 2. 内容参考色过滤
content_ref = _estimate_content_reference(img)
print(f'\n2. 内容参考色过滤 (content_ref={tuple(int(round(v)) for v in content_ref)}):')
layers_filtered = _filter_layers_by_content_ref(layers_raw, content_ref)
for i, (c, t) in enumerate(layers_filtered):
    d = float(np.sqrt(sum((a - b) ** 2 for a, b in zip(c, content_ref))))
    print(f'  层 {i}: color={c}, thickness={t}px, dist_to_content={d:.1f}')

# 3. 厚度硬上限
print('\n3. 厚度硬上限:')
layers_final = _enforce_border_thickness_caps(layers_filtered)
for i, (c, t) in enumerate(layers_final):
    print(f'  层 {i}: color={c}, thickness={t}px')

total_depth = sum(t for _, t in layers_final)
print(f'\n总边框深度: {total_depth}px')
print(f'实际期望: 外层黑({T_OUTER}px) + 米色间隙({GAP}px) + 内层黑({T_INNER}px) = {T_OUTER+GAP+T_INNER}px')

# 4. 嵌套矩形检测
print('\n4. 嵌套矩形检测 (带 border_layers 厚度推断):')
rects = detect_nested_rect_layers(img, border_layers=layers_final)
for i, r in enumerate(rects):
    print(f'  层 {i}: (x1={r[0]},y1={r[1]},x2={r[2]},y2={r[3]})')

# 验证：每层矩形应对应每层边框的深度
print(f'\n验证嵌套矩形坐标:')
cumulative = 0
for i, (_, t) in enumerate(layers_final):
    cumulative += t
    if i < len(rects):
        r = rects[i]
        expected_x1 = cumulative
        actual_x1 = r[0]
        print(f'  层 {i}: 累计深度={cumulative}px, rect.x1={actual_x1}px, 差值={actual_x1 - expected_x1}px')

# 5. 简单场景：只有一个边框层
print('\n' + '=' * 70)
print("测试简单场景（只有单层黑边框）")
print('=' * 70)
arr_simple = np.full((200, 200, 3), 250, dtype=np.uint8)
arr_simple[0:30, :, :] = (0, 0, 0)
arr_simple[170:200, :, :] = (0, 0, 0)
arr_simple[:, 0:30] = (0, 0, 0)
arr_simple[:, 170:200] = (0, 0, 0)
img_simple = Image.fromarray(arr_simple, 'RGB')

layers_simple = _detect_border_layers(img_simple, max_scan_depth_px=50, bg_color=(250, 250, 250))
print(f'  单层边框检测结果:')
for i, (c, t) in enumerate(layers_simple):
    print(f'    层 {i}: color={c}, thickness={t}px')

# 6. 多层嵌套场景 (间距较小)
print('\n' + '=' * 70)
print("测试多层嵌套场景（三层嵌套）")
print('=' * 70)
arr_multi = np.full((500, 500, 3), 240, dtype=np.uint8)
arr_multi[0:40, :, :] = (0, 0, 0)
arr_multi[460:500, :, :] = (0, 0, 0)
arr_multi[:, 0:40] = (0, 0, 0)
arr_multi[:, 460:500] = (0, 0, 0)
arr_multi[60:100, :, :] = (50, 50, 50)
arr_multi[400:440, :, :] = (50, 50, 50)
arr_multi[:, 60:100] = (50, 50, 50)
arr_multi[:, 400:440] = (50, 50, 50)
img_multi = Image.fromarray(arr_multi, 'RGB')

layers_multi = _detect_border_layers(img_multi, max_scan_depth_px=200, bg_color=(240, 240, 240))
print(f'  多层嵌套检测结果:')
for i, (c, t) in enumerate(layers_multi):
    print(f'    层 {i}: color={c}, thickness={t}px')

rects_multi = detect_nested_rect_layers(img_multi, border_layers=layers_multi)
print(f'  嵌套矩形:')
for i, r in enumerate(rects_multi):
    print(f'    层 {i}: (x1={r[0]},y1={r[1]},x2={r[2]},y2={r[3]})')
