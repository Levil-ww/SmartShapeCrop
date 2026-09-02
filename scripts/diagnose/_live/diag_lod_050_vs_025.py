# -*- coding: utf-8 -*-
"""LOD 0.50 vs 0.25 像素量与缩放比例的对比分析

用户画布场景：
  Canvas: 140.3 x 59.9 cm @ 150 DPI
    => W_px = round(140.3/2.54*150) = 8285
       H_px = round(59.9/2.54*150)  = 3537
       总像素 ~= 29.3 MP (30 MP 级)

  Inner hole: 53.5 x 33.5 cm @ 150 DPI
    => ~= 3159 x 1978 px (6.25 MP)

素材 DPI 候选档 (业界常见印刷级扫描 / 素材图库)：
  72 / 150 / 200 / 240 / 300 / 600 DPI
"""
import re
from pathlib import Path

CM_PER_INCH = 2.54

def cm_to_px(w_cm, h_cm, dpi):
    return round(w_cm / CM_PER_INCH * dpi), round(h_cm / CM_PER_INCH * dpi)

# ---- 固定场景 ----
CANVAS_W_CM, CANVAS_H_CM, CANVAS_DPI = 140.3, 59.9, 150
INNER_W_CM, INNER_H_CM = 53.5, 33.5

# 方向校正: 内挖 53.5x33.5 是横版 (w>h), 但源素材 35x55cm 是竖版 (h>w)
# 项目中 ROTATE_270 判据, 所以源在使用时会转横版, 即 W=55cm, H=35cm
INNER_SRC_W_CM, INNER_SRC_H_CM = 55.0, 35.0  # 旋转后横版尺寸
OUTER_SRC_W_CM, OUTER_SRC_H_CM = 138.5, 59.0  # 匹配素材"安妮森林 59x138.5cm"

CW, CH = cm_to_px(CANVAS_W_CM, CANVAS_H_CM, CANVAS_DPI)
IW, IH = cm_to_px(INNER_W_CM, INNER_H_CM, CANVAS_DPI)

print("=" * 80)
print(f"画布 @{CANVAS_DPI} DPI: {CW}x{CH} = {CW*CH/1e6:.2f} MP")
print(f"内挖 @{CANVAS_DPI} DPI: {IW}x{IH} = {IW*IH/1e6:.2f} MP")
print("=" * 80)

for lod_name, lod in [("LOD=0.25", 0.25), ("LOD=0.50", 0.50)]:
    LW, LH = round(CW * lod), round(CH * lod)
    LIW, LIH = round(IW * lod), round(IH * lod)
    print(f"\n[{lod_name}] 画布 LOD: {LW}x{LH} = {LW*LH/1e6:.2f} MP   (1/{(CW*CH)/(LW*LH):.1f} 像素)")
    print(f"[{lod_name}] 内挖 LOD: {LIW}x{LIH} = {LIW*LIH/1e6:.2f} MP")

print("\n" + "=" * 80)
print("素材 → 目标 的比例分析 (预览 quality='preview' 下, base_resample = BILINEAR)")
print("  * 判定 > 1.5x 下采样: 强制 LANCZOS (抗 aliasing)")
print("  * 否则: 保留 BILINEAR (快速)")
print("=" * 80)

scales = [("预览 LOD 0.25", 0.25),
          ("预览 LOD 0.50", 0.50),
          ("预览 Full", 1.0),
          ("导出 Full", 1.0)]

for mat_name, (src_w_cm, src_h_cm) in [
    ("Outer 素材安妮森林 59x138.5cm (横版,拉伸→画布)", (OUTER_SRC_W_CM, OUTER_SRC_H_CM)),
    ("Inner 素材安妮森林 35x55cm (旋转270→横版 55x35, stretch→内挖)", (INNER_SRC_W_CM, INNER_SRC_H_CM)),
]:
    print(f"\n--- {mat_name} ---")
    header = f"{'素材 DPI':<10} {'源像素':<18} "
    for scale_name, _ in scales[:3]:
        header += f"{scale_name+' sx/sy':<26}"
    header += f"{'导出 sx/sy':<26}"
    print(header)
    for dpi in [72, 150, 200, 240, 300, 600]:
        sw, sh = cm_to_px(src_w_cm, src_h_cm, dpi)
        row = f"{dpi:<10} {sw}x{sh:<12} "
        for i, (scale_name, scale) in enumerate(scales):
            if i == 0:      # LOD 0.25 目标
                tw, th = round(CW * scale if "Outer" in mat_name else IW * scale), \
                         round(CH * scale if "Outer" in mat_name else IH * scale)
                base = "BILINEAR" if i < 3 else "LANCZOS"
            elif i == 1:    # LOD 0.50
                tw, th = round(CW * scale if "Outer" in mat_name else IW * scale), \
                         round(CH * scale if "Outer" in mat_name else IH * scale)
                base = "BILINEAR"
            elif i == 2:    # Preview FULL
                tw, th = (CW, CH) if "Outer" in mat_name else (IW, IH)
                base = "BILINEAR"
            else:           # Export
                tw, th = (CW, CH) if "Outer" in mat_name else (IW, IH)
                base = "LANCZOS"
            sx, sy = tw / sw, th / sh
            min_s = min(sx, sy)
            override = " → LANCZOS!" if min_s < 1/1.5 else ""
            row += f"{sx:.2f}x / {sy:.2f}x {override:<14}"
        print(row)

# ---- 渲染时长预估 (经验模型) ----
print("\n" + "=" * 80)
print("LOD 代理渲染时长预估模型 (基于: 单次 BILINEAR/LANCZOS 每 MP ~10-15ms,")
print("  render_design 主循环: mask * paste * blend, 经验系数 = ~每 MP 25 ms)")
print("=" * 80)
COEFF_MS_PER_MP = 25  # 保守估计
for lod_name, lod in [("LOD=0.25", 0.25), ("LOD=0.50", 0.50), ("FULL=1.0", 1.0)]:
    mp = (CW * lod) * (CH * lod) / 1e6
    est_ms = mp * COEFF_MS_PER_MP
    flag = " (<500ms GUI 即时阈值 ✅)" if est_ms < 500 else (" (后台 worker ✅)" if lod == 1.0 else " (⚠️ 接近阈值)")
    print(f"  {lod_name:<12}  {mp:>6.2f} MP  →  预估 {est_ms:>5.0f} ms {flag}")
print("  导出 LANCZOS 路径:  29.30 MP  →  预估 3-10 s  (走 QThread 后台 + 进度条)")
