"""
深度诊断：针对三个案例的真实特征模拟，定位代码缺陷

重点检查：
1. ring_lower_bound 在多层边框+抗锯齿下是否产生正确保护
2. detect_nested_rect_layers 在真实花纹干扰下是否能正确定位内层矩形
3. _redraw_border_on_corner 的 structure_allow + force_paint 逻辑
   是否在"大半径厚边框 + 花纹/间隙交替"场景下产生漏绘
4. 圆角在"嵌套矩形+R_eff_k"逻辑下是否形成同心圆弧递减
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
from PIL import Image, ImageDraw
from core.image_cropper import (
    _build_multi_layer_corner_mask,
    _get_border_layers_robust,
    apply_border_only_corners,
)
from core.corner.detection import detect_nested_rect_layers

DPI = 150
CM_TO_PX = DPI / 2.54

# ===== 案例 C1: 墨上花开 =====
# 75x120cm -> W=4410, H=7087, R=5cm -> 295px
# 合成特征：外层黑(30px) + 米色间隙(15px) + 内层黑(25px) + 花纹
# 注意：花纹填充，并且可能有多层嵌套
print("=" * 70)
print("案例 C1: 墨上花开 75x120cm, 四角5cm (295px)")
print("=" * 70)

# 为加速计算，缩放到 1/5 分辨率
SCALE = 0.2
W, H = 4410, 7087
w, h = int(W * SCALE), int(H * SCALE)
R_PX_FULL = int(round(5.0 * CM_TO_PX))
r = max(20, int(round(R_PX_FULL * SCALE)))  # ~59px
print(f'  原图 {W}x{H}, 缩放后 {w}x{h}, 圆角 r={r}px')

# 构造真实特征图
arr = np.full((h, w, 3), 245, dtype=np.uint8)  # 米色背景
T_OUTER = int(round(30 * SCALE))   # 外层黑边
GAP = int(round(15 * SCALE))      # 米色间隙
T_INNER = int(round(25 * SCALE))  # 内层黑边

# 加抗锯齿过渡（1px渐变带）
def draw_border(arr, t_out, gap, t_in):
    w_, h_ = arr.shape[1], arr.shape[0]
    col_black = (0, 0, 0)
    col_beige = (245, 235, 215)
    # 外层黑
    for s, e, col in [(0, t_out, col_black),
                      (t_out + gap, t_out + gap + t_in, col_black)]:
        arr[s:e, :, :] = col
        arr[-e:-s, :, :] = col
        arr[:, s:e] = col
        arr[:, -e:-s] = col
    # 1px 抗锯齿带：外层黑到米色
    for i in range(min(3, gap)):
        alpha = 1.0 - (i + 1) / 4.0
        mix_b = tuple(int(round((1 - alpha) * 245)) for _ in range(3))
        row_idx = t_out + i
        arr[row_idx, :, :] = mix_b
        arr[-(row_idx + 1), :, :] = mix_b
        arr[:, row_idx] = mix_b
        arr[:, -(row_idx + 1)] = mix_b
        # 内边缘
        in_idx = t_out + gap - 1 - i
        arr[in_idx, :, :] = mix_b
        arr[-(in_idx + 1), :, :] = mix_b
        arr[:, in_idx] = mix_b
        arr[:, -(in_idx + 1)] = mix_b

    # 内层黑到花纹的抗锯齿
    in_edge = t_out + gap + t_in
    for i in range(min(3, 8)):
        alpha = 1.0 - (i + 1) / 4.0
        mix_b = tuple(int(round((1 - alpha) * 200)) for _ in range(3))
        row_idx = in_edge + i
        if row_idx < h - in_edge:
            arr[row_idx, :, :] = mix_b
            arr[-(row_idx + 1), :, :] = mix_b
            arr[:, row_idx] = mix_b
            arr[:, -(row_idx + 1)] = mix_b

# 填充花纹（用深色不规则斑模拟墨线花卉）
def add_floral_pattern(arr, margin):
    h_, w_ = arr.shape[:2]
    np.random.seed(123)
    INNER_X1, INNER_Y1 = margin, margin
    INNER_X2, INNER_Y2 = w_ - margin, h_ - margin
    # 用灰度墨线花纹
    for _ in range(80):
        fx = np.random.randint(INNER_X1 + 10, INNER_X2 - 10)
        fy = np.random.randint(INNER_Y1 + 10, INNER_Y2 - 10)
        # 深色花朵
        size = np.random.randint(4, 9)
        arr[fy-size:fy+size, fx-size:fx+size] = np.random.randint(
            30, 80, size=(2*size, 2*size, 3))

draw_border(arr, T_OUTER, GAP, T_INNER)
margin = T_OUTER + GAP + T_INNER
add_floral_pattern(arr, margin)

img = Image.fromarray(arr, 'RGB')
border_layers = _get_border_layers_robust(img, (245, 235, 215))
print(f'  检测到 border_layers:')
for i, (c, t) in enumerate(border_layers):
    print(f'    层 {i}: color={c}, thickness={t}px')

try:
    nested_rects = detect_nested_rect_layers(img)
    print(f'  detect_nested_rect_layers 找到 {len(nested_rects)} 层:')
    for i, r in enumerate(nested_rects):
        print(f'    层 {i}: (x1={r[0]},y1={r[1]},x2={r[2]},y2={r[3]})')
except Exception as e:
    nested_rects = []
    print(f'  detect_nested_rect_layers 失败: {e}')

corners_px = {'tl': r, 'tr': r, 'bl': r, 'br': r}
mask = _build_multi_layer_corner_mask(w, h, corners_px, border_layers, nested_rects=nested_rects)

# 质量检查：四角边框扇区
TOTAL_DEPTH = sum(t for _, t in border_layers)
print(f'  总边框深度 TOTAL_DEPTH={TOTAL_DEPTH}px')
print(f'  ring_lower_bound 预期: max(TOTAL_DEPTH, 1.5*TOTAL_DEPTH+20, 0.20*r)')
exp_expanded = int(round(TOTAL_DEPTH * 1.5 + 20))
exp_ratio = int(round(r * 0.20))
exp_ring = max(TOTAL_DEPTH, exp_expanded, exp_ratio)
exp_ring = min(r - 1, max(0, exp_ring))
print(f'    expanded={exp_expanded}, ratio={exp_ratio}, ring_lower_bound={exp_ring}')

mask_arr = np.array(mask)
OK, BAD = 0, 0
for ck, (cx_map, cy_map) in [('tl', (r, r)), ('tr', (w-r, r)),
                               ('bl', (r, h-r)), ('br', (w-r, h-r))]:
    for dy in range(-r, r + 1, max(1, r // 15)):
        for dx in range(-r, r + 1, max(1, r // 15)):
            x, y = cx_map + dx, cy_map + dy
            if not (0 <= x < w and 0 <= y < h): continue
            d = np.sqrt(dx*dx + dy*dy)
            if d > r: continue
            depth = r - d
            if depth > TOTAL_DEPTH: continue  # 花纹区不检查
            if mask_arr[y, x] == 255: OK += 1
            else: BAD += 1
    print(f'  角 {ck}: 采样后 OK={OK}, BAD={BAD}')

# 案例 C2: 花野
print()
print("=" * 70)
print("案例 C2: 花野 竖版33x27cm, 右下14cm (827px)")
print("=" * 70)

W_F, H_F = int(33 * CM_TO_PX), int(27 * CM_TO_PX)
SCALE_F = 0.2
w2, h2 = int(W_F * SCALE_F), int(H_F * SCALE_F)
R_FULL = int(round(14 * CM_TO_PX))
r2 = max(30, int(round(R_FULL * SCALE_F)))
print(f'  原图 {W_F}x{H_F}, 缩放后 {w2}x{h2}, 圆角 r={r2}px')

# 特征：外黑(20px) + 白(18px) + 花纹(米色底+深棕四叶草)
arr2 = np.full((h2, w2, 3), 235, dtype=np.uint8)
T_BLACK2 = int(round(20 * SCALE_F))
T_WHITE2 = int(round(18 * SCALE_F))

def draw_border_pattern(arr_, t_b, t_w):
    h_, w_ = arr_.shape[:2]
    # 黑
    for s, e, col in [(0, t_b, (0, 0, 0)), (t_b, t_b + t_w, (255, 255, 255))]:
        arr_[s:e, :, :] = col
        arr_[-e:-s, :, :] = col
        arr_[:, s:e] = col
        arr_[:, -e:-s] = col
    # 花纹：深米色底 + 深棕花朵/四叶草
    PAT_START = t_b + t_w
    np.random.seed(42)
    for _ in range(40):
        fx = np.random.randint(PAT_START + 8, w_ - PAT_START - 8)
        fy = np.random.randint(PAT_START + 8, h_ - PAT_START - 8)
        s = np.random.randint(5, 9)
        arr_[fy-s:fy+s, fx-s:fx+s] = (55, 45, 35)  # 深棕黑花
        if s > 6:
            arr_[fy-s//2:fy+s//2, fx-s//2:fx+s//2] = (150, 105, 65)  # 棕心

draw_border_pattern(arr2, T_BLACK2, T_WHITE2)

img2 = Image.fromarray(arr2, 'RGB')
border_layers2 = _get_border_layers_robust(img2, (235, 230, 220))
print(f'  检测到 border_layers:')
for i, (c, t) in enumerate(border_layers2):
    print(f'    层 {i}: color={c}, thickness={t}px')

try:
    nested_rects2 = detect_nested_rect_layers(img2)
    print(f'  detect_nested_rect_layers 找到 {len(nested_rects2)} 层')
    for i, r in enumerate(nested_rects2[:5]):
        print(f'    层 {i}: (x1={r[0]},y1={r[1]},x2={r[2]},y2={r[3]})')
except Exception as e:
    nested_rects2 = []
    print(f'  detect_nested_rect_layers 失败: {e}')

corners_px2 = {'br': r2}
mask2 = _build_multi_layer_corner_mask(w2, h2, corners_px2, border_layers2, nested_rects=nested_rects2)
mask2_arr = np.array(mask2)

# 检查 br 角的同心性：dist 应为 [0, r] 的完整圆扇区
R_PAT = r2 - T_BLACK2 - T_WHITE2
print(f'  br 角: 外黑 R_eff={r2}, 白框 R_eff={r2-T_BLACK2}, 花纹 R_eff={R_PAT}')
TOTAL_DEPTH2 = sum(t for _, t in border_layers2)
OK2, BAD2 = 0, 0
# 检查 br 角花纹区是否被正确保留（同心效果）
for dy in range(-r2, 1, max(1, r2 // 12)):
    for dx in range(-r2, 1, max(1, r2 // 12)):
        x, y = w2 - r2 + dx, h2 - r2 + dy
        if not (0 <= x < w2 and 0 <= y < h2): continue
        d = np.sqrt(dx*dx + dy*dy)
        if d > r2: continue
        depth = r2 - d
        if depth > TOTAL_DEPTH2: continue  # 花纹区
        if mask2_arr[y, x] == 255: OK2 += 1
        else: BAD2 += 1
print(f'  br 边框扇区: OK={OK2}, BAD={BAD2}')

# 额外检查：外黑/白框区域是否被误切
print(f'  br 角边框详细检查（逐层）:')
for depth_range_name, (d_min, d_max) in [('外黑 (0~T_BLACK2)', (0, T_BLACK2)),
                                        ('白框 (T_BLACK2~T_BLACK2+T_WHITE2)', (T_BLACK2, T_BLACK2+T_WHITE2))]:
    sub_ok, sub_bad = 0, 0
    for dy in range(-r2, 1, max(1, r2 // 20)):
        for dx in range(-r2, 1, max(1, r2 // 20)):
            x, y = w2 - r2 + dx, h2 - r2 + dy
            if not (0 <= x < w2 and 0 <= y < h2): continue
            d = np.sqrt(dx*dx + dy*dy)
            if d > r2: continue
            depth = r2 - d
            if depth < d_min or depth >= d_max: continue
            if mask2_arr[y, x] == 255: sub_ok += 1
            else: sub_bad += 1
    print(f'    {depth_range_name}: OK={sub_ok}, BAD={sub_bad}')

# 案例 C3: 婉卉
print()
print("=" * 70)
print("案例 C3: 婉卉 58x147cm, 左下4.5cm (266px)")
print("=" * 70)

W3_F, H3_F = int(58 * CM_TO_PX), int(147 * CM_TO_PX)
SCALE3 = 0.2
w3, h3 = int(W3_F * SCALE3), int(H3_F * SCALE3)
R3_FULL = int(round(4.5 * CM_TO_PX))
r3 = max(25, int(round(R3_FULL * SCALE3)))
print(f'  原图 {W3_F}x{H3_F}, 缩放后 {w3}x{h3}, 圆角 r={r3}px')

# 特征：咖色厚外框(120px) + 米色间隙(10px) + 内层黑边(5px) + 大花纹
BROWN = (120, 80, 50)
arr3 = np.full((h3, w3, 3), 245, dtype=np.uint8)
T_BROWN = max(20, int(round(120 * SCALE3)))
GAP3 = max(2, int(round(10 * SCALE3)))
T_BLACK3 = max(2, int(round(5 * SCALE3)))

# 咖色边框
arr3[:T_BROWN, :, :] = BROWN
arr3[-T_BROWN:, :, :] = BROWN
arr3[:, :T_BROWN] = BROWN
arr3[:, -T_BROWN] = BROWN

# 内层黑边
INNER_START = T_BROWN + GAP3
INNER_END = INNER_START + T_BLACK3
arr3[INNER_START:INNER_END, :, :] = 0
arr3[-INNER_END:-INNER_START, :, :] = 0
arr3[:, INNER_START:INNER_END] = 0
arr3[:, -INNER_END:-INNER_START] = 0

# 花纹
np.random.seed(99)
for _ in range(30):
    fx = np.random.randint(INNER_END + 10, w3 - INNER_END - 10)
    fy = np.random.randint(INNER_END + 10, h3 - INNER_END - 10)
    s = np.random.randint(6, 12)
    arr3[fy-s:fy+s, fx-s:fx+s] = np.random.randint(80, 150, size=(2*s, 2*s, 3))

img3 = Image.fromarray(arr3, 'RGB')
border_layers3 = _get_border_layers_robust(img3, (245, 235, 215))
print(f'  检测到 border_layers:')
for i, (c, t) in enumerate(border_layers3):
    print(f'    层 {i}: color={c}, thickness={t}px')

try:
    nested_rects3 = detect_nested_rect_layers(img3)
    print(f'  detect_nested_rect_layers 找到 {len(nested_rects3)} 层:')
    for i, r in enumerate(nested_rects3):
        print(f'    层 {i}: (x1={r[0]},y1={r[1]},x2={r[2]},y2={r[3]})')
        if i >= 8: break
except Exception as e:
    nested_rects3 = []
    print(f'  detect_nested_rect_layers 失败: {e}')

corners_px3 = {'bl': r3}
mask3 = _build_multi_layer_corner_mask(w3, h3, corners_px3, border_layers3, nested_rects=nested_rects3)
mask3_arr = np.array(mask3)

# 关键检查：内层黑边距边缘 INNER_START，若 INNER_START > r3 → R_eff=0 → 完全直角
# 实际：INNER_START = T_BROWN + GAP3 ≈ 24+2 = 26px > r3 (25px)?
# 如果 r3=25, INNER_START=26, 则 R_eff=max(0, 25-26)=0
print(f'  内层黑边外边缘距图边 INNER_START={INNER_START}px, r3={r3}px')
print(f'  若 INNER_START >= r3 → 内层黑边应完全直角 (R_eff=0)')
R_eff_inner = max(0, r3 - INNER_START)
print(f'  R_eff_inner = max(0, {r3} - {INNER_START}) = {R_eff_inner}')

# 检查内层黑边左下区域是否被误裁
CUT_INNER, TOTAL_INNER = 0, 0
# bl 角: cx=r3, cy=h3-r3
cx3, cy3 = r3, h3 - r3
blk_x1, blk_x2 = INNER_START, INNER_END
blk_y1, blk_y2 = h3 - INNER_END, h3 - INNER_START
for dy in range(-r3, r3 + 1):
    for dx in range(-r3, r3 + 1):
        x, y = cx3 + dx, cy3 + dy
        if not (0 <= x < w3 and 0 <= y < h3): continue
        in_black = (blk_x1 <= x < blk_x2 and y >= blk_y1) or \
                   (blk_y1 <= y < blk_y2 and x < blk_x2)
        if in_black:
            TOTAL_INNER += 1
            if mask3_arr[y, x] == 0:
                CUT_INNER += 1
print(f'  内层黑边 L 形: TOTAL={TOTAL_INNER}, 被裁 0 的 CUT={CUT_INNER}')
print(f'  正确率: {(TOTAL_INNER - CUT_INNER) / max(1, TOTAL_INNER) * 100:.1f}%')

print()
print("=" * 70)
print("深度诊断完成")
print("=" * 70)
