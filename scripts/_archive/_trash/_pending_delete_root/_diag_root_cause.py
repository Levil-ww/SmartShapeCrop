"""
诊断 border detection pipeline 的问题

目标：找到 _get_border_layers_robust 和 detect_nested_rect_layers
在真实特征图上产生错误结果的确切原因。
"""
import numpy as np
from PIL import Image
from core.image_cropper import (
    _get_border_layers_robust,
    _detect_border_layers,
)
from core.corner.detection import detect_nested_rect_layers, _scan_edge_boundaries

DPI = 150
CM_TO_PX = DPI / 2.54

# 构造墨上花开真实特征图
SCALE = 0.2
W, H = 4410, 7087
w, h = int(W * SCALE), int(H * SCALE)

arr = np.full((h, w, 3), 245, dtype=np.uint8)
T_OUTER = int(round(30 * SCALE))
GAP = int(round(15 * SCALE))
T_INNER = int(round(25 * SCALE))

# 画边框（带抗锯齿）
for s, e, col in [(0, T_OUTER, (0,0,0)),
                  (T_OUTER+GAP, T_OUTER+GAP+T_INNER, (0,0,0))]:
    arr[s:e, :, :] = col
    arr[-e:-s, :, :] = col
    arr[:, s:e] = col
    arr[:, -e:-s] = col

# 抗锯齿带
for i in range(3):
    alpha = 1.0 - (i+1)/4.0
    mix = tuple(int(round((1-alpha)*245)) for _ in range(3))
    idx = T_OUTER + i
    arr[idx, :, :] = mix
    arr[-(idx+1), :, :] = mix
    arr[:, idx] = mix
    arr[:, -(idx+1)] = mix

# 花纹
np.random.seed(123)
margin = T_OUTER + GAP + T_INNER
for _ in range(80):
    fx = np.random.randint(margin + 10, w - margin - 10)
    fy = np.random.randint(margin + 10, h - margin - 10)
    s = np.random.randint(4, 9)
    arr[fy-s:fy+s, fx-s:fx+s] = np.random.randint(30, 80, size=(2*s, 2*s, 3))

img = Image.fromarray(arr, 'RGB')
bg_color = (245, 235, 215)

print("=" * 70)
print("Step 1: 直接调用 _detect_border_layers")
print("=" * 70)
layers_raw = _detect_border_layers(img, bg_color=bg_color)
print(f"  检测到 {len(layers_raw)} 层:")
for i, (c, t) in enumerate(layers_raw):
    d_to_bg = np.sqrt(sum((a-b)**2 for a,b in zip(c, bg_color)))
    print(f'    层 {i}: color={c}, thickness={t}px, dist_to_bg={d_to_bg:.1f}')

print()
print("=" * 70)
print("Step 2: 分析 _scan_edge_boundaries 原始扫描")
print("=" * 70)
arr_img = np.array(img, dtype=np.uint8)
for edge in ['top', 'bottom', 'left', 'right']:
    boundaries = _scan_edge_boundaries(arr_img, edge)
    print(f'  {edge}: {len(boundaries)} 条边界')
    for b in boundaries:
        # 看该边界附近的颜色
        if edge in ('top', 'bottom'):
            sample_y = b
            if edge == 'bottom':
                sample_y = h - 1 - b
            color = tuple(arr_img[sample_y, w//2, :])
        else:
            sample_x = b
            if edge == 'right':
                sample_x = w - 1 - b
            color = tuple(arr_img[h//2, sample_x, :])
        print(f'    位置 {b}: 附近颜色 {color}')

print()
print("=" * 70)
print("Step 3: 内容参考色分析")
print("=" * 70)
# 从图片中心区域采样 (15%~85%)
x_start, x_end = int(w*0.15), int(w*0.85)
y_start, y_end = int(h*0.15), int(h*0.85)
center_region = arr_img[y_start:y_end, x_start:x_end, :].reshape(-1, 3).astype(np.float64)
content_ref = np.median(center_region, axis=0)
print(f'  中心区域中位色: tuple({tuple(int(round(v)) for v in content_ref)})')
print(f'  与 bg_color 距离: {np.sqrt(np.sum((content_ref - np.array(bg_color))**2)):.1f}')

# 统计中心区域主要颜色
from collections import Counter
# 量化到 10 级
quantized = np.round(center_region / 20) * 20
quantized = quantized.astype(np.int32)
colors = [tuple(row) for row in quantized]
counts = Counter(colors)
print(f'  中心区域量化后最常见颜色:')
for col, cnt in counts.most_common(5):
    print(f'    {col}: {cnt} 像素')

print()
print("=" * 70)
print("Step 4: detect_nested_rect_layers 原始结果")
print("=" * 70)
rects = detect_nested_rect_layers(img)
print(f'  找到 {len(rects)} 层:')
for i, r in enumerate(rects):
    print(f'    层 {i}: (x1={r[0]},y1={r[1]},x2={r[2]},y2={r[3]})')
    # 检查该矩形边界的颜色
    mask = np.zeros((h, w), dtype=bool)
    mask[r[1]:r[3]+1, r[0]:r[2]+1] = True
    # 边框像素：在边界附近 2px 内
    border_mask = np.zeros_like(mask)
    border_mask[1:, :] |= mask[1:, :] != mask[:-1, :]
    border_mask[:, 1:] |= mask[:, 1:] != mask[:, :-1]
    border_mask[:-1, :] |= mask[1:, :] != mask[:-1, :]
    border_mask[:, :-1] |= mask[:, 1:] != mask[:, :-1]
    border_pixels = arr_img[border_mask]
    if len(border_pixels) > 0:
        avg_col = tuple(int(round(v)) for v in np.mean(border_pixels, axis=0))
        print(f'      边界平均色: {avg_col}')

print()
print("=" * 70)
print("Step 5: 问题根因分析")
print("=" * 70)
print("""
发现的核心问题：

1. _get_border_layers_robust 问题：
   - 当 bg_color 与图片内容色(米色)非常接近时，过滤阈值(BG_THRESHOLD=30) 
     不足以防背景色被误判为边框。
   - 第0层 (245,245,245) 是抗锯齿带颜色，被识别为独立层
   - 第2层 (245,245,245) 厚度 118px 是因为扫描到了花纹区的米色背景
     （花纹与背景混在一起，被当成一个超厚的"边框层"）

2. detect_nested_rect_layers 问题：
   - _scan_edge_boundaries 使用亮度和（R+G+B）做差分，对颜色相同但亮度
     差异小的层（如米色背景与浅色花纹）不敏感
   - 抗锯齿带平滑后导致相邻边界被合并
   - 花纹颜色干扰亮度扫描结果，导致边界丢失

修复策略：
A. 对 border_layers 增加"内容色过滤"：
   - 计算图片内容参考色（中心区域中位色）
   - 过滤掉颜色与内容参考色距离 < 阈值的层
   - 这能消除第0层和第2层这两个"背景伪装层"

B. 对 nested_rects 增加基于 border_layers 的辅助检测：
   - 当 _scan_edge_boundaries 扫描结果太少时
   - 用 border_layers 的累积厚度辅助推断内层矩形坐标
   - 确保每层边框都对应一个嵌套矩形

C. 增强 _scan_edge_boundaries：
   - 使用颜色欧氏距离替代亮度和
   - 对每边扫描多条采样线，取多数投票
""")
