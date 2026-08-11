# -*- coding: utf-8 -*-
"""
三案例端到端合成图诊断
======================
生成符合真实结构的合成图（墨上花开/花野/婉卉），跑完整圆角 pipeline，
验证 5 个正确性不变量（同心性/半径递减/层保护区/扇区无缺口/颜色归属）。

输出：
  1. 每层边框的 R_eff 与检测到的 rect 坐标对比表
  2. 角部遮罩的对角内区像素值逐深度 dump
  3. 渲染图在对角 45° 线上的 RGB 采样（检测 C 形缺口 = 颜色跳变点）
"""
from __future__ import annotations
import sys
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, '.')

from core.config import DEFAULT_DPI
from core.corner.detection import (
    _get_border_layers_robust,
    detect_nested_rect_layers,
)
from core.image_cropper import (
    _build_multi_layer_corner_mask,
    apply_border_only_corners,
)
from core.corner.sector_render import _redraw_border_on_corner
from core.corner.algorithm import CORNER_ANGLES

DPI = 150
BG_CANVAS = (255, 255, 255)

def cm2px(cm: float) -> int:
    return max(1, int(round(cm * DPI / 2.54)))

# ---------------------------------------------------------------------------
# Case 1: 墨上花开 — 75x120cm TL/TR/BL/BR 5cm 圆角
#   结构：Outer 黑 124px → Gap 米色 27px → Inner 黑 103px → Pattern 米色花纹区
# ---------------------------------------------------------------------------
def build_moshanghuakai() -> tuple[Image.Image, dict, tuple]:
    W, H = cm2px(75), cm2px(120)
    T_OUT = 124; GAP = 27; T_IN = 103
    C_BORDER = (0, 0, 0)
    C_GAP = (245, 235, 215)  # 米色间隙
    C_PAT_BG = (245, 245, 245)  # 花纹区底色
    img = Image.new('RGB', (W, H), C_PAT_BG)
    draw = ImageDraw.Draw(img)
    # 外黑框
    draw.rectangle([0, 0, W-1, H-1], outline=C_BORDER, width=T_OUT*2)  # trick: width draws both sides at center offset, need manual
    # 手工精确绘制：外层
    draw.rectangle([0, 0, W-1, H-1], fill=None, outline=None)
    arr = np.array(img)
    # 外层黑 (0..T_OUT-1 全黑)
    arr[:T_OUT, :, :] = C_BORDER
    arr[-T_OUT:, :, :] = C_BORDER
    arr[:, :T_OUT, :] = C_BORDER
    arr[:, -T_OUT:, :] = C_BORDER
    # 间隙米色
    arr[T_OUT:T_OUT+GAP, T_OUT:W-T_OUT, :] = C_GAP
    arr[-(T_OUT+GAP):-T_OUT, T_OUT:W-T_OUT, :] = C_GAP
    arr[T_OUT:H-T_OUT, T_OUT:T_OUT+GAP, :] = C_GAP
    arr[T_OUT:H-T_OUT, -(T_OUT+GAP):-T_OUT, :] = C_GAP
    # 内层黑
    i0 = T_OUT + GAP; i1 = T_OUT + GAP + T_IN
    arr[i0:i1, i0:W-i0, :] = C_BORDER
    arr[-(i1):-(i0), i0:W-i0, :] = C_BORDER
    arr[i0:H-i0, i0:i1, :] = C_BORDER
    arr[i0:H-i0, -(i1):-(i0), :] = C_BORDER
    # 花纹区中心画几朵假花（随机黑线条），避免纯色检测偏差
    rng = np.random.RandomState(42)
    px0 = i1 + 20
    for _ in range(400):
        y = rng.randint(px0, H - px0)
        x = rng.randint(px0, W - px0)
        s = rng.randint(2, 8)
        arr[y:y+s, x:x+s, :] = C_BORDER if rng.random() < 0.5 else (60,60,60)
    img = Image.fromarray(arr, mode='RGB')
    corners_cm = {'tl': 5, 'tr': 5, 'bl': 5, 'br': 5}
    return img, corners_cm, (T_OUT, GAP, T_IN, C_BORDER, C_GAP)

# ---------------------------------------------------------------------------
# Case 2: 花野 — 33x27cm BR 14cm 圆角
#   结构：Outer 黑 40px → White 间隙 15px → Pattern 米色花纹区(带四叶草黑花)
# ---------------------------------------------------------------------------
def build_huaye() -> tuple[Image.Image, dict, tuple]:
    W, H = cm2px(33), cm2px(27)
    T_OUT = 40; GAP = 15
    C_OUTER = (0, 0, 0)
    C_GAP = (255, 255, 255)
    C_PAT_BG = (220, 200, 160)  # 米色花纹底
    C_FLOWER = (30, 30, 30)     # 四叶草深黑
    C_CENTER = (180, 140, 90)   # 花心棕
    arr = np.full((H, W, 3), C_PAT_BG, dtype=np.uint8)
    # 外层黑
    arr[:T_OUT, :, :] = C_OUTER
    arr[-T_OUT:, :, :] = C_OUTER
    arr[:, :T_OUT, :] = C_OUTER
    arr[:, -T_OUT:, :] = C_OUTER
    # 间隙白
    arr[T_OUT:T_OUT+GAP, T_OUT:W-T_OUT, :] = C_GAP
    arr[-(T_OUT+GAP):-T_OUT, T_OUT:W-T_OUT, :] = C_GAP
    arr[T_OUT:H-T_OUT, T_OUT:T_OUT+GAP, :] = C_GAP
    arr[T_OUT:H-T_OUT, -(T_OUT+GAP):-T_OUT, :] = C_GAP
    # 四叶草花纹（规则点阵）
    px0 = T_OUT + GAP + 5
    STEP = 55; DOT = 22
    for cx in range(px0 + STEP//2, W - px0, STEP):
        for cy in range(px0 + STEP//2, H - px0, STEP):
            # 4 petals
            for (dx, dy) in [(-12,0),(12,0),(0,-12),(0,12)]:
                x0 = cx+dx-DOT//2; y0 = cy+dy-DOT//2
                y0s = slice(max(0,y0), min(H, y0+DOT))
                x0s = slice(max(0,x0), min(W, x0+DOT))
                arr[y0s, x0s, :] = C_FLOWER
            # 花心
            r = 6
            arr[cy-r:cy+r+1, cx-r:cx+r+1, :] = C_CENTER
    img = Image.fromarray(arr, mode='RGB')
    corners_cm = {'br': 14}
    return img, corners_cm, (T_OUT, GAP, C_OUTER, C_GAP, C_PAT_BG)

# ---------------------------------------------------------------------------
# Case 3: 婉卉 — 58x147cm BL 4.5cm 圆角
#   结构：Outer 棕 120px → Gap 米色 180px → Inner 黑 35px → 花纹区
#   关键：Inner 黑离外边缘 Dk ≈ 120+180=300px > R=4.5cm=266px → R_eff(inner)=0
# ---------------------------------------------------------------------------
def build_wanhui() -> tuple[Image.Image, dict, tuple]:
    W, H = cm2px(58), cm2px(147)
    T_OUT = 120; GAP = 180; T_IN = 35
    C_OUTER = (120, 80, 50)   # 棕
    C_GAP = (245, 235, 215)   # 米色间隙
    C_INNER = (0, 0, 0)       # 内层黑
    C_PAT_BG = (250, 245, 230)
    arr = np.full((H, W, 3), C_PAT_BG, dtype=np.uint8)
    # 外层棕
    arr[:T_OUT, :, :] = C_OUTER
    arr[-T_OUT:, :, :] = C_OUTER
    arr[:, :T_OUT, :] = C_OUTER
    arr[:, -T_OUT:, :] = C_OUTER
    # 间隙米色
    a0 = T_OUT; a1 = T_OUT + GAP
    arr[a0:a1, a0:W-a0, :] = C_GAP
    arr[-(a1):-(a0), a0:W-a0, :] = C_GAP
    arr[a0:H-a0, a0:a1, :] = C_GAP
    arr[a0:H-a0, -(a1):-(a0), :] = C_GAP
    # 内层黑
    b0 = a1; b1 = a1 + T_IN
    arr[b0:b1, b0:W-b0, :] = C_INNER
    arr[-(b1):-(b0), b0:W-b0, :] = C_INNER
    arr[b0:H-b0, b0:b1, :] = C_INNER
    arr[b0:H-b0, -(b1):-(b0), :] = C_INNER
    # 花纹
    rng = np.random.RandomState(7)
    px0 = b1 + 10
    for _ in range(1500):
        y = rng.randint(px0, H - px0)
        x = rng.randint(px0, W - px0)
        s = rng.randint(1, 5)
        arr[y:y+s, x:x+s, :] = C_INNER
    img = Image.fromarray(arr, mode='RGB')
    corners_cm = {'bl': 4.5}
    return img, corners_cm, (T_OUT, GAP, T_IN, C_OUTER, C_GAP, C_INNER)


# ================================================================
# Diagnostic helpers
# ================================================================
def color_dist(a, b) -> float:
    return float(np.sqrt(sum((x-y)**2 for x,y in zip(a,b))))

def diag_case(name: str, build_fn):
    print(f"\n{'='*70}")
    print(f"  Case: {name}")
    print(f"{'='*70}")
    img, corners_cm, info = build_fn()
    W, H = img.size
    print(f"  尺寸: {W}x{H}px  @{DPI}dpi")
    print(f"  圆角(cm): {corners_cm}")
    corners_px = {k: cm2px(v) for k, v in corners_cm.items()}
    print(f"  圆角(px): {corners_px}")

    bg_color = BG_CANVAS
    # ---------- Step 1: 检测 border_layers ----------
    border_layers = _get_border_layers_robust(img, bg_color=bg_color)
    print(f"\n  [1] border_layers ({len(border_layers)} 层):")
    cum = 0
    for idx, (col, t) in enumerate(border_layers):
        cum += t
        print(f"      L{idx}: color={col}, thick={t}px, cum_depth={cum}")
    tot_depth = cum

    # ---------- Step 2: 检测 nested_rect ----------
    rects = detect_nested_rect_layers(img, border_layers=border_layers)
    print(f"\n  [2] nested_rect_layers ({len(rects)} 层):")
    for idx, r in enumerate(rects):
        print(f"      R{idx}: (x1={r[0]},y1={r[1]},x2={r[2]},y2={r[3]})  w={r[2]-r[0]} h={r[3]-r[1]}")

    # ---------- Step 3: 计算 R_eff(k) per corner per layer ----------
    print(f"\n  [3] 每层的有效圆角半径 R_eff(k) (按检测 rect 计算):")
    for ck, r_px in corners_px.items():
        print(f"    角 {ck} R_total={r_px}px:")
        for kidx, rect_k in enumerate(rects):
            rx1, ry1, rx2, ry2 = rect_k
            if ck == 'tl':   Dk = max(rx1, ry1)
            elif ck == 'tr': Dk = max((W - 1) - rx2, ry1)
            elif ck == 'bl': Dk = max(rx1, (H - 1) - ry2)
            else:            Dk = max((W - 1) - rx2, (H - 1) - ry2)
            R_eff_k = max(0, r_px - int(round(Dk)))
            lm = max(1, min(rx2-rx1, ry2-ry1)//2)
            R_eff_k = min(R_eff_k, lm)
            print(f"      R{kidx}: Dk={Dk}px → R_eff={R_eff_k}px  (Dk>=R_total={Dk>=r_px} → 直角={R_eff_k<=0})")

    # ---------- Step 4: 构建 mask 并分析角部像素 ----------
    mask = _build_multi_layer_corner_mask(
        W, H, corners_px, border_layers, nested_rects=rects
    )
    mask_arr = np.array(mask.convert('L'))
    print(f"\n  [4] 构建 mask: shape={mask_arr.shape}, 0-px(cut)={np.sum(mask_arr==0)}, 255-px(keep)={np.sum(mask_arr==255)}")

    # ---------- Step 5: 对每个角，沿 45° 对角线采样 ----------
    print(f"\n  [5] 沿 45° 对角线采样 (检查层保护区/扇区无缺口不变量):")
    for ck, r_px in corners_px.items():
        # 圆心
        if ck == 'tl':   cx, cy, sx, sy = r_px, r_px, 1, 1
        elif ck == 'tr': cx, cy, sx, sy = W - r_px, r_px, -1, 1
        elif ck == 'bl': cx, cy, sx, sy = r_px, H - r_px, 1, -1
        else:            cx, cy, sx, sy = W - r_px, H - r_px, -1, -1
        print(f"    角 {ck}: 圆心({cx},{cy}), step_dir=({sx},{sy})")
        # 从圆心沿 45° 向外走 (步长 = sqrt(2)/2 近似 1px 对角)
        print(f"      d(px)  | (x,y)   | mask | dist | 应属层 | 期望状态")
        print(f"      " + "-" * 62)
        # 先跑完整 pipeline 渲染一张 result
        result = Image.new('RGB', (W, H), bg_color)
        result.paste(img, mask=mask)
        _redraw_border_on_corner(result, ck, r_px, border_layers, src_img=img, validity_mask=mask)
        res_arr = np.array(result)
        src_arr = np.array(img)

        # 累计厚度，用于"应属层"判定（基于 border_layers 直边累计）
        cums = [0]
        for _, t in border_layers:
            cums.append(cums[-1] + t)
        def layer_of_depth(d: int) -> int:
            for i in range(len(cums)-1):
                if cums[i] <= d < cums[i+1]:
                    return i
            return len(border_layers)  # 花纹区

        for d in range(0, r_px + 40, max(1, r_px // 20)):
            x = cx + sx * d
            y = cy + sy * d
            if not (0 <= x < W and 0 <= y < H):
                continue
            dist = np.sqrt((x - cx)**2 + (y - cy)**2)
            mv = mask_arr[y, x]
            depth = r_px - dist  # 外弧向内深度 = R - dist
            lcol = layer_of_depth(int(depth))
            lcol_name = f"L{lcol}" if lcol < len(border_layers) else "PAT"
            # 期望: dist <= r_px → mask=255; dist > r_px → mask=0
            expect = 255 if dist <= r_px + 0.5 else 0
            ok = "✓" if mv == expect else "✗ MASK_WRONG"
            # 检查颜色: 渲染后的颜色 vs 该深度对应直边颜色
            # 直边参考：沿相同 depth 从 straight extension strip 取色
            if ck == 'tl':
                sx_smp = (depth, min(max(r_px + 10, W//2), W-1))  # top strip
                sy_smp = (min(max(r_px + 10, H//2), H-1), depth)
            elif ck == 'tr':
                sx_smp = (W - 1 - int(depth), min(max(r_px + 10, W//2), W-1))
                sy_smp = (0, depth) if 0 <= depth < H else (0, 0)
            elif ck == 'bl':
                sx_smp = (int(depth), 0) if 0 <= depth < W else (0, 0)
                sy_smp = (min(max(r_px + 10, W//2), W-1), H - 1 - int(depth))
            else:  # br
                sx_smp = (W - 1 - int(depth), H - 1 - min(max(r_px + 10, H//2), H-1))
                sy_smp = (min(max(r_px + 10, W//2), W-1), H - 1 - int(depth))
            # 简化：取 straight color 参考（若在直边带内）
            try:
                cref_side = tuple(src_arr[min(max(sx_smp[1],0),H-1), min(max(sx_smp[0],0),W-1), :])
            except Exception:
                cref_side = (0,0,0)
            cr = tuple(res_arr[y, x, :])
            cdiff = color_dist(cr, cref_side)
            color_ok = "✓" if cdiff < 35 else "✗ COLOR_GAP"
            print(f"      {d:>5}  | ({x:>4},{y:>4}) | {mv:>4} | {dist:>5.1f} | {lcol_name:>5} | expect={expect} {ok} | render_rgb={cr} | ref_straight={cref_side} Δ={cdiff:.0f} {color_ok}")

    # ---------- Step 6: 跑完整 apply_border_only_corners 并 dump 结果图 ----------
    try:
        final = apply_border_only_corners(img, corners_cm, bg_color=bg_color, dpi=DPI)
        outpath = f"_diag_out_{name}.jpg"
        final.save(outpath, quality=92)
        print(f"\n  [6] 渲染图输出: {outpath}")
    except Exception as e:
        print(f"\n  [6] apply_border_only_corners 失败: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()

if __name__ == '__main__':
    diag_case("A1_墨上花开", build_moshanghuakai)
    diag_case("A2_花野", build_huaye)
    diag_case("A3_婉卉", build_wanhui)
    print("\nDone.")
