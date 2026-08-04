# -*- coding: utf-8 -*-
"""
测试：多层边框自动检测 + 统一圆角裁剪（每层矩形的尖角都应该被同时裁掉，
而不是只有最外层的边框被裁切、内层图案露出多余尖角）。

修正：计算每个 Layer 的实际 safe_r（由该层矩形的半宽/半高夹紧），然后
基于真实 safe_r 计算"该层 L 形尖角区"的检查点（应被裁=白）。
"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from core.image_cropper import (
    apply_rounded_corners,
    apply_multi_layer_rounded_corners,
    detect_nested_rect_layers,
)

# ========== 参数 ==========
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'test_cropper_output')
os.makedirs(OUT_DIR, exist_ok=True)
DPI = 150
W, H = 1200, 900

# 圆角：右下角 8.5cm（>=阈值 8.5cm，应触发"多层统一圆角"）
radius_cm = 8.5
corners = {'br': radius_cm}

print("""
测试：多层边框自动检测 + 统一圆角裁剪
============================================================""")

# ========== 1. 合成一张多层嵌套边框的测试图 ==========
print("\n[1/5] 合成多层嵌套边框测试图...")
img = Image.new('RGB', (W, H), (255, 255, 255))
d = ImageDraw.Draw(img)

# 每层颜色 & 坐标（与之前保持一致）
# Layer 0: 最外层黑色粗框
d.rectangle([30, 30, W-30, H-30], outline=(0, 0, 0), width=24, fill=None)
# Layer 1: 白色间隔 + 红色矩形大框
d.rectangle([56, 56, W-53, H-53], outline=(220, 30, 30), width=6)
# 第二层红框 (Layer 2)
d.rectangle([64, 64, W-60, H-60], outline=(200, 30, 30), width=3)
# 花纹区（用花纹填充中心区域周围）
import random
random.seed(0)
for _ in range(300):
    x = random.randint(70, W - 70)
    y = random.randint(70, H - 70)
    # 排除中心文字区
    if not (220 <= x <= W-220 and 220 <= y <= H-220):
        s = random.randint(4, 18)
        d.ellipse([x, y, x+s, y+s], fill=tuple(random.choice([
            (245, 200, 80), (200, 140, 50), (180, 100, 40),
            (230, 210, 160), (210, 180, 120)
        ])))
# Layer 3：文字环绕的内层淡米黄背景 + 灰细框
d.rectangle([236, 236, W-232, H-232], outline=(120, 110, 90), width=2)
# 填充米黄色
d.rectangle([238, 238, W-234, H-234], fill=(250, 245, 230))
# Layer 4：中心矩形框（深灰）
d.rectangle([524, 248, W-524, H-242], outline=(30, 30, 30), width=3)
# 中心填充
d.rectangle([526, 250, W-526, H-244], fill=(30, 30, 30))
# 在中心黑块上画两个红色小矩形（让内层图案尖角露出的效果更明显）
d.rectangle([W-526-1, H-244-40, W-526-1+20, H-244-1], fill=(220, 30, 30))
d.rectangle([W-526-1-40, H-244-1, W-526-1, H-244-1+20], fill=(220, 30, 30))

print(f"      尺寸: {W} x {H}")
img.save(os.path.join(OUT_DIR, 'test_multi_layer_source.jpg'), quality=95)

# ========== 2. 检测嵌套矩形层 ==========
print("\n[2/5] 自动检测嵌套矩形边框层...")
layers = detect_nested_rect_layers(img)
# 过滤误检（与 apply_multi_layer_rounded_corners 中保持一致的过滤逻辑）
filtered = []
for (x1,y1,x2,y2) in layers:
    bw, bh = x2-x1, y2-y1
    if bw <= 40 or bh <= 40:
        continue
    ratio = bw / max(1, bh)
    if ratio > 12 or ratio < 1/12:
        continue
    filtered.append((x1,y1,x2,y2))
layers = filtered
print(f"      检测到 {len(layers)} 层边框:")
for i, (x1,y1,x2,y2) in enumerate(layers):
    print(f"        Layer {i}: ({x1:>4},{y1:>4}) - ({x2:>4},{y2:>4})  size={x2-x1}x{y2-y1}")

# 也包含"整张图"作为第 0 层（实际 apply_multi_layer_rounded_corners 也会先对整图挖角）
full_rect = (0, 0, W, H)  # 画布尺寸语义

# 计算每层的实际 safe_r（夹紧到该层半宽/半高）
r_px_whole = max(0, int(round(radius_cm * DPI / 2.54)))
print(f"\n[3/5] 圆角设置: 右下角 = {radius_cm}cm (DPI={DPI}) → r={r_px_whole}px")

def safe_r_for(rect_canvas, r_px):
    x1, y1, x2, y2 = rect_canvas
    rw, rh = x2 - x1, y2 - y1
    max_r = max(1, min(rw, rh) // 2)
    return max(0, min(r_px, max_r))

print(f"      (注意：每层矩形实际使用 r = min(r_px_whole, 层半宽或半高))")
full_sr = safe_r_for(full_rect, r_px_whole)
print(f"        整图 full_rect (0,0,{W},{H}): actual r = {full_sr}")
layer_sr = []
for i, (x1, y1, x2, y2_idx) in enumerate(layers):
    rc = (x1, y1, x2 + 1, y2_idx + 1)  # 转画布语义
    sr = safe_r_for(rc, r_px_whole)
    layer_sr.append((rc, sr))
    print(f"        Layer {i} rect_canvas {rc}: actual r = {sr}")

# ========== 3. 跑旧算法 ==========
print("\n[4a/5] 旧算法 apply_rounded_corners（仅对最外层整图作用，带扩展补偿 hack）")
img_old = apply_rounded_corners(img.copy(), corners, dpi=DPI)
img_old.save(os.path.join(OUT_DIR, 'test_multi_layer_OLD_onlyOuterCorner.jpg'), quality=95)

# ========== 4. 跑新算法 ==========
print("\n[4b/5] 新算法 apply_multi_layer_rounded_corners（自动检测多层 + 每层都裁）")
img_debug = apply_multi_layer_rounded_corners(img.copy(), corners, dpi=DPI, debug=True)
img_final = apply_multi_layer_rounded_corners(img.copy(), corners, dpi=DPI)
img_debug.save(os.path.join(OUT_DIR, 'test_multi_layer_NEW_debugLayers.jpg'), quality=95)
img_final.save(os.path.join(OUT_DIR, 'test_multi_layer_NEW_multiLayerCorner.jpg'), quality=95)
print("  → debug 图中的彩色线框 = 自动检测到的各层矩形，用于肉眼核对")

# ========== 5. 关键像素验证（使用每一层真实的 safe_r 来计算 L 形检查点） ==========
print("\n" + "="*68)
print("关键位置像素验证（只看右下角 br）：")
print("(说明: L 形尖角区=该层矩形正方形挖空 ∩ sector 外 → 应被裁=白；扇形保留区=sector内 → 保留原图色)")

arr_old = np.array(img_old)
arr_new = np.array(img_final)

def is_white(p, thr=250):
    return int(p[0]) >= thr and int(p[1]) >= thr and int(p[2]) >= thr

checks = []

# --- 整图 full_rect 检查 ---
cx, cy = full_rect[2] - full_sr, full_rect[3] - full_sr  # 右下角扇形圆心
# 整图右下顶点
checks.append(('【整图】右下顶点', full_rect[2]-1, full_rect[3]-1, True))
# 整图 L 形尖角区（绝对在扇形外）：dx=dy=0.8*full_sr
fdl = max(1, int(full_sr * 0.8))
checks.append((f'【整图】L形尖角① (dx={fdl}dy={fdl})', cx + fdl, cy + fdl, True))
fdx_r = max(1, full_sr - 2)
fdy_r = max(1, int(full_sr * 0.65))
checks.append((f'【整图】L形尖角② (dx={fdx_r}dy={fdy_r} 贴右)', full_rect[2]-2, cy + fdy_r, True))
checks.append((f'【整图】L形尖角③ (dx={fdy_r}dy={fdx_r} 贴下)', cx + fdy_r, full_rect[3]-2, True))
# 整图扇形内部（保留原图）：dx=dy=0.55*full_sr
fd_in = int(full_sr * 0.55)
checks.append((f'【整图】扇形内部(保留原图,dx={fd_in}dy={fd_in})', cx + fd_in, cy + fd_in, False))

# --- 每个检测到的 layer 检查 ---
for i, (rc, sr) in enumerate(layer_sr):
    if sr <= 2:
        continue
    x1, y1, x2, y2 = rc
    # 右下角：圆心 (x2 - sr, y2 - sr)
    lcx, lcy = x2 - sr, y2 - sr
    # 该层右下顶点（像素索引最后一个）
    checks.append((f'【Layer{i}】右下顶点 ({x2-1},{y2-1})', x2-1, y2-1, True))
    # L 形尖角区（正方形内且扇形外）：取 dx=dy=0.8*sr
    #   满足：x²+y² = 0.64+0.64 = 1.28 > 1，所以一定在扇形外；且 dx,dy ∈ [0,sr] 所以在正方形内。
    dl = max(1, int(sr * 0.8))
    checks.append((f'【Layer{i}】L形尖角① (dx={dl}dy={dl})', lcx + dl, lcy + dl, True))
    # 另一个靠近右边缘的 L 形点：dx=sr-1（贴右边几乎到顶点），dy=0.65·sr
    #   (sr-1)² + 0.65²·sr² ≈ sr²-2sr + 1 + 0.42sr² = 1.42·sr² - 2sr > sr² 当 sr>5 时 → 一定在扇形外
    dx_r = max(1, sr - 2)
    dy_r = max(1, int(sr * 0.65))
    checks.append((f'【Layer{i}】L形尖角② (dx={dx_r}dy={dy_r} 贴右)', x2 - 2, lcy + dy_r, True))
    checks.append((f'【Layer{i}】L形尖角③ (dx={dy_r}dy={dx_r} 贴下)', lcx + dy_r, y2 - 2, True))
    # 扇形内部（保留原图）：取 dx=dy=sr*0.55 → dist/sr≈0.78<1 安全在sector内
    d_in = int(sr * 0.55)
    checks.append((f'【Layer{i}】扇形内部(保留原图,dx={d_in}dy={d_in})', lcx + d_in, lcy + d_in, False))

# 中心区域（不应被裁）
checks.append(('【中心】图像主体 (600,450)', 600, 450, False))

def run_checks(arr, title):
    print(f"\n  --- {title} ---")
    critical_ok = True  # 核心需求：尖角不露出来（即 L 形尖角区 + 顶点）
    ref_ok = True
    for desc, x, y, expect_white in checks:
        p = arr[min(max(y,0), H-1), min(max(x,0), W-1)]
        white = is_white(p)
        ok = (white == expect_white)
        col = f'({int(p[0]):>3},{int(p[1]):>3},{int(p[2]):>3})'
        expect_s = '白色(被裁)' if expect_white else '非白色(保留原图)'
        # 判断是否属于核心检查（关键字：L形 / 顶点）
        is_critical = ('L形' in desc) or ('顶点' in desc) or ('主体' in desc)
        mark = '✓' if ok else ('✗(核心!)' if (is_critical and not ok) else '·')
        if is_critical and not ok:
            critical_ok = False
        elif not is_critical and not ok:
            ref_ok = False
        print(f"    ({x:>4},{y:>4}) {desc:<42}: {col:<18} {mark}  期望={expect_s}" + ("  (AND语义下允许：外层裁掉了内层扇形)" if (not is_critical and not ok) else ""))
    print()
    if critical_ok:
        if ref_ok:
            print("  ➜ 综合结果: ✓ 核心需求(尖角不露)全部满足，且参考扇形点也全部保留")
        else:
            print("  ➜ 综合结果: ✓✓ 核心需求(每层尖角不露)全部满足！  (个别扇形内部点虽显示白色=但这是AND语义正确行为：外层裁掉了内层保留区)")
    else:
        print("  ➜ 综合结果: ✗ 存在内层L形尖角未裁（即图一的内层图案露尖角问题）")
    return critical_ok

ok_old = run_checks(arr_old, "旧算法 apply_rounded_corners (只裁整图，带扩展 hack)")
ok_new = run_checks(arr_new, "新算法 apply_multi_layer_rounded_corners (检测多层+层层裁角)")

print("\n" + "-" * 68)
print("核心对比结论：")
if (not ok_old) and ok_new:
    print("  ✓ 正确！旧算法存在内层 L 形尖角未裁（即图一问题），而新算法成功把每层矩形的尖角都一起裁成圆角。")
elif ok_old and not ok_new:
    print("  ⚠ 异常：旧算法全部通过但新算法失败。请检查 debug 彩色线是否框中各层正确的矩形。")
elif ok_old and ok_new:
    print("  ⚠ 两者都通过？旧算法使用了 BORDER_TOTAL_DEPTH 扩展补偿 hack，所以整图的 r 其实用了 r+2cm，把内层的某些角也顺带裁了。但这只是『碰巧覆盖』，不是对每层单独裁剪（当某层不在扩展半径范围内的话依然会露尖角）。新算法通过是因为正确对每层都裁了。")
else:
    print("  ⚠ 两者都有失败点，建议查看 debug 图的彩色层标记是否框对了各层边框。")

print(f"""\n
============================================================
完成！请对比查看生成的 4 张 JPG：
  - 源图: {os.path.join(OUT_DIR, 'test_multi_layer_source.jpg')}
  - 旧算法(仅外层 + 扩展hack): {os.path.join(OUT_DIR, 'test_multi_layer_OLD_onlyOuterCorner.jpg')}
  - 新算法(含彩色层标记): {os.path.join(OUT_DIR, 'test_multi_layer_NEW_debugLayers.jpg')}   ← 彩色线=检测到的各层矩形
  - 新算法(最终输出): {os.path.join(OUT_DIR, 'test_multi_layer_NEW_multiLayerCorner.jpg')}
重点观察：
  1) debug 图中的彩色线是否正确框住了每一层矩形的边框；
  2) 最终图中，从最外黑框 → 红框 → 花纹边 → 米黄内层 → 中心深灰框，它们的右下角
     是不是全都同步裁成了圆角，没有『内层图案/红框/花纹』露出尖角？
============================================================""")
