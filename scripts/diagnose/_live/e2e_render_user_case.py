# -*- coding: utf-8 -*-
"""E2E 仿真用户现场：预览 LOD 0.50 + 导出 Full 质量
- 画布 140.3x59.9 cm @ 150 DPI = 8285x3537 px (29.3 MP)
- Outer 素材 138.5x59.0 cm @ 300 DPI (横版) 合成高频花纹 → 命中 LANCZOS 下采样
- Inner 素材 35x55.0 cm @ 300 DPI (竖版, ROTATE_270 判据) → 旋转后横版 55x35cm
- 调用链: render_design_lod(0.50) / render_design('preview') / render_design('export')
"""
import os, sys, time, tempfile, math, pathlib
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import numpy as np
from PIL import Image

from core.geometry import CropDesign
CM_PER_INCH = 2.54

def make_pattern(w_cm, h_cm, dpi, seed=42, variant='outer'):
    """合成高频花纹 (仿印刷百合线稿: 重复条纹 + 圆斑 + 正弦波纹)。
    优化版：圆斑用局部 ROI 而非全网格广播，整体 O(像素) 线性内存。
    """
    w = round(w_cm / CM_PER_INCH * dpi)
    h = round(h_cm / CM_PER_INCH * dpi)
    print(f"  [合成素材 variant={variant}] {w}x{h} ({w*h/1e6:.2f} MP) @{dpi} DPI")

    rng = np.random.default_rng(seed)

    # 背景: 橄榄绿米色调 (仿安妮森林)
    if variant == 'outer':
        bg = np.array([180, 195, 150], dtype=np.uint8)          # 橄榄绿米
    else:
        bg = np.array([160, 120, 80], dtype=np.uint8)           # 棕色相框底

    arr = np.full((h, w, 3), bg, dtype=np.uint8)

    # 1) 每 3cm 的纵向条纹 → 高频 (>1.5x 下采样 → aliasing 高风险区)
    stripe_period_px = max(4, round(3.0 / CM_PER_INCH * dpi))
    col_idx = np.arange(w, dtype=np.int64)
    stripe = (col_idx % stripe_period_px) < (stripe_period_px // 12)
    if variant == 'outer':
        acc = np.array([60, 80, 30], dtype=np.uint8)
    else:
        acc = np.array([80, 50, 20], dtype=np.uint8)
    arr[:, stripe, :] = acc
    del stripe, col_idx

    # 2) 每 5cm 的水平细线 (百合花瓣线感)
    line_period_px = max(4, round(5.0 / CM_PER_INCH * dpi))
    row_idx = np.arange(h, dtype=np.int64)
    hline = (row_idx % line_period_px) < max(1, line_period_px // 40)
    mix = (np.array([255 - bg[0], 255 - bg[1], 255 - bg[2]], dtype=np.uint16) // 2
           + bg.astype(np.uint16) // 2).astype(np.uint8)
    arr[hline, :, :] = mix
    del hline, row_idx

    # 3) 周期性圆斑 (花纹"花头"): 每 8cm 一个, 半径 ~1.2cm （局部 ROI 加速）
    cell_px = max(8, round(8.0 / CM_PER_INCH * dpi))
    radius_px = max(2, round(1.2 / CM_PER_INCH * dpi))
    accent = (np.array([240, 245, 220], dtype=np.uint8) if variant == 'outer'
              else np.array([210, 180, 140], dtype=np.uint8))
    r2 = radius_px * radius_px
    yy = np.arange(-radius_px, radius_px + 1, dtype=np.int64)
    xx = np.arange(-radius_px, radius_px + 1, dtype=np.int64)
    Ym, Xm = np.meshgrid(yy, xx, indexing='ij')
    circ_mask = (Xm * Xm + Ym * Ym) <= r2  # shape (2R+1, 2R+1)
    for cy in range(cell_px // 2, h, cell_px):
        for cx in range(cell_px // 2, w, cell_px):
            jx = int(rng.integers(-cell_px // 6, cell_px // 6 + 1))
            jy = int(rng.integers(-cell_px // 6, cell_px // 6 + 1))
            xc, yc = cx + jx, cy + jy
            y0 = yc - radius_px
            x0 = xc - radius_px
            y1 = y0 + circ_mask.shape[0]
            x1 = x0 + circ_mask.shape[1]
            # clamp to image
            sy0, sy1 = max(0, y0), min(h, y1)
            sx0, sx1 = max(0, x0), min(w, x1)
            if sy1 <= sy0 or sx1 <= sx0:
                continue
            my0 = sy0 - y0
            mx0 = sx0 - x0
            my1 = my0 + (sy1 - sy0)
            mx1 = mx0 + (sx1 - sx0)
            submask = circ_mask[my0:my1, mx0:mx1]
            arr[sy0:sy1, sx0:sx1][submask] = accent
    del circ_mask, Ym, Xm

    # 4) 正弦波纹 (每 16px 做一小组采样以节省内存, 但仍全分辨)
    stripe_s = max(2, stripe_period_px // 6)
    line_s = max(2, line_period_px // 6)
    # 用分片窗口 (避免 114MP × float32 = 1.3 GB ×2 的瞬时峰值)
    if variant == 'outer':
        hi_freq = np.array([255, 0, 0], dtype=np.uint8)
    else:
        hi_freq = np.array([0, 30, 80], dtype=np.uint8)
    BLOCK_ROWS = 4096
    for b0 in range(0, h, BLOCK_ROWS):
        b1 = min(h, b0 + BLOCK_ROWS)
        ys = np.arange(b0, b1, dtype=np.float32).reshape(-1, 1)
        xs = np.arange(0, w, dtype=np.float32).reshape(1, -1)
        wave = np.sin(xs / stripe_s) * np.cos(ys / line_s)  # shape (b1-b0, w)
        sub = np.abs(wave) > 0.92
        arr[b0:b1, :, :][sub] = hi_freq
        del wave, sub, ys, xs

    return Image.fromarray(arr, mode='RGB')


def build_user_case_design(outer_path: str, inner_path: str,
                           outer_design_wh_cm, inner_design_wh_cm_ORIG):
    """传路径: outer/inner 走各自 os.path.isfile 判据 (image_ops 内部逻辑依赖)。
    inner_design_wh_cm_ORIG = 内挖素材的**原始方向** (文件名, 未旋转交换, 例 35x55 竖版)。
    """
    d = CropDesign(
        canvas_w_cm=140.3, canvas_h_cm=59.9, dpi=150,
        mode='rect_hole',
        outer_margin_cm=0.0,
        inner_margin_top_cm=(59.9 - 33.5) / 2,      # 内挖 53.5x33.5 居中
        inner_margin_bottom_cm=(59.9 - 33.5) / 2,
        inner_margin_left_cm=(140.3 - 53.5) / 2,
        inner_margin_right_cm=(140.3 - 53.5) / 2,
        hole_bg_color=(255, 255, 255),
        outer_bg_color=(255, 255, 255),
        # --- 素材填充花型匹配模式 (用户图1场景) ---
        pool_hole_transparent=False,
        pool_outer_material_image=outer_path,
        pool_inner_material_image=inner_path,
        pool_material_design_w_cm=outer_design_wh_cm[0],
        pool_material_design_h_cm=outer_design_wh_cm[1],
        pool_is_multi_hole=False,
        pool_holes_cm=[],
    )
    # 内挖素材的**原始方向**设计尺寸 (image_ops L1226-1227 用 getattr 读)
    #   例 35x55cm 竖版 → ROTATE_270 → 55x35cm 横版 (匹配内挖 53.5x33.5 横版)
    d.pool_inner_src_design_w_cm = inner_design_wh_cm_ORIG[0]
    d.pool_inner_src_design_h_cm = inner_design_wh_cm_ORIG[1]
    return d


def measure(fn, *args, **kwargs):
    t0 = time.perf_counter()
    r = fn(*args, **kwargs)
    dt = (time.perf_counter() - t0) * 1000
    return r, dt


if __name__ == '__main__':
    from core.image_ops import render_design_lod, render_design, save_jpg

    outdir = pathlib.Path(tempfile.gettempdir()) / 'ssc_e2e_diag'
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"\n输出目录: {outdir}\n")

    # Outer: 59x138.5cm 横版。采用 200 DPI 合成档 (29MP, 类似用户常用的中档高清图)。
    #   对应下采样比例: Full Preview ≈ 0.76x (1/1.32 > 1.5阈值不命中) → 仍需 LOD 0.50 ≈ 0.38x (命中强制 LANCZOS)。
    OUTER_DPI, INNER_DPI = 200, 200
    outer_design_wh = (138.5, 59.0)
    outer = make_pattern(outer_design_wh[0], outer_design_wh[1], dpi=OUTER_DPI, seed=101, variant='outer')
    outer_path = str(outdir / 'src_outer.png')
    outer.save(outer_path)

    # Inner: 35x55cm 竖版 (原始方向, 非旋转后) → 渲染时按 ROTATE_270 判
    inner_design_wh_ORIG = (35.0, 55.0)
    inner = make_pattern(inner_design_wh_ORIG[0], inner_design_wh_ORIG[1], dpi=INNER_DPI, seed=202, variant='inner')
    inner_path = str(outdir / 'src_inner_vertical.png')
    inner.save(inner_path)

    design = build_user_case_design(outer_path, inner_path, outer_design_wh, inner_design_wh_ORIG)

    CW = design.canvas_w_px
    CH = design.canvas_h_px
    print(f"\n画布像素: {CW}x{CH} = {CW*CH/1e6:.2f} MP")
    print(f"内挖像素: {design.inner_rect_px()}")

    # 1) LOD 0.50 (预览代理, 用户第一观感 = GUI 即时显示)
    print("\n─── [A] render_design_lod(scale=0.50) 预览代理 (第一观感) ───")
    img_lod, t_lod = measure(render_design_lod, design, scale=0.50)
    print(f"  尺寸={img_lod.size} ({img_lod.size[0]*img_lod.size[1]/1e6:.2f} MP)  耗时={t_lod:.0f} ms")
    img_lod.save(outdir / 'A_lod_0.50.png')
    # LOD 0.25 做对比
    img_lod25, t_lod25 = measure(render_design_lod, design, scale=0.25)
    print(f"  [对比 LOD 0.25] 尺寸={img_lod25.size} ({img_lod25.size[0]*img_lod25.size[1]/1e6:.2f} MP)  耗时={t_lod25:.0f} ms")
    img_lod25.save(outdir / 'A_lod_0.25.png')

    # 2) Full preview (后台 worker 渲染的预览全分辨率)
    print("\n─── [B] render_design(quality='preview') 全分辨率预览 ───")
    img_pv, t_pv = measure(render_design, design, quality='preview')
    print(f"  尺寸={img_pv.size}  耗时={t_pv:.0f} ms")
    img_pv.save(outdir / 'B_preview_full.png')

    # 3) Export (最终导出 JPG) —— 原同步版阻塞时间就是它 + save
    print("\n─── [C] render_design(quality='export') 导出路径 ───")
    img_exp, t_exp = measure(render_design, design, quality='export')
    print(f"  尺寸={img_exp.size}  仅渲染耗时={t_exp:.0f} ms")
    jpg_path = outdir / 'C_export_q95.jpg'
    t_save0 = time.perf_counter()
    save_jpg(img_exp, str(jpg_path), quality=95, dpi=150)
    t_save = (time.perf_counter() - t_save0) * 1000
    jpg_size_kb = jpg_path.stat().st_size / 1024
    print(f"  JPG 保存: {jpg_size_kb/1024:.1f} MB  耗时={t_save:.0f} ms  合计(UI 原阻塞)={t_exp+t_save:.0f} ms = {(t_exp+t_save)/1000:.1f}s")

    # 验证像素一致性: preview vs export 应"视觉等价" (export 用 LANCZOS, preview 用 BILINEAR 除非阈值)
    a_pv = np.array(img_pv).astype(np.int16)
    a_exp = np.array(img_exp).astype(np.int16)
    diff = np.abs(a_pv - a_exp)
    print(f"\n─── 像素一致性 (preview vs export) ───")
    print(f"  diff 均值={diff.mean():.3f}  max={diff.max()}  P95={np.percentile(diff,95):.2f}")
    print(f"  diff>2 的像素占比={(diff>2).mean()*100:.3f}%")

    # 验证花纹高频"无马赛克": 对 LOD 0.50 vs LOD 0.25 的边缘梯度做对比
    # 更大的 Sobel 能量 = 细节保留更好 (而不是块状)
    def sobel_energy(img):
        g = np.array(img.convert('L')).astype(np.float32)
        gx = np.diff(g, axis=1, append=g[:, -1:])
        gy = np.diff(g, axis=0, append=g[-1:, :])
        return float(np.sqrt(gx*gx + gy*gy).mean())
    e_25 = sobel_energy(img_lod25)
    e_50 = sobel_energy(img_lod)
    # 归一化到相同尺寸的"保真度": 按 (1/scale) 对能量加权, 能量越大细节越丰富
    print(f"\n─── 花纹保真度代理 (Sobel 梯度能量/像素, 越大=细节越多, 非块状) ───")
    print(f"  LOD 0.25 梯度能量均值 = {e_25:.3f}")
    print(f"  LOD 0.50 梯度能量均值 = {e_50:.3f}")
    print(f"  提升倍率 = {e_50/e_25:.2f}x")

    # render_design_lod 语义: 返回 upsample 后原画布尺寸 (便于 GUI 直接贴)
    assert img_lod.size == (CW, CH), \
        f"LOD 0.50 最终输出尺寸应为画布 ({CW},{CH}), 实际 {img_lod.size}"

    # 验证花纹高频"无马赛克": 对 LOD 原图 (未上采样前) 直接比较 Sobel 能量
    # 重新跑 LOD, 这次在 _make_lod_design → render_design 阶段直接抓 native 结果
    from core.image_ops import _make_lod_design as __ml
    lod_small_05 = render_design(__ml(design, round(CW*0.50), round(CH*0.50)),
                                  quality='preview', pixel_scale=0.50)
    lod_small_025 = render_design(__ml(design, round(CW*0.25), round(CH*0.25)),
                                   quality='preview', pixel_scale=0.25)
    e_25 = sobel_energy(lod_small_025)
    e_50 = sobel_energy(lod_small_05)
    print(f"\n─── 花纹保真度代理 (Sobel 梯度能量/像素, 越大=细节越多, 非块状) ───")
    print(f"  LOD 0.25 原生 ({lod_small_025.size[0]}×{lod_small_025.size[1]}) 梯度能量均值 = {e_25:.3f}")
    print(f"  LOD 0.50 原生 ({lod_small_05.size[0]}×{lod_small_05.size[1]}) 梯度能量均值 = {e_50:.3f}")
    print(f"  LOD 0.50 细节提升 = {e_50/e_25:.2f}×  (理论 2× 等能量, 实测越接近越好)")
    # 顺带保存原生 LOD, 便于肉眼直接对比 (不放大, 看实际显示内容的"颗粒度")
    lod_small_05.save(outdir / 'A_lod_0.50_NATIVE.png')
    lod_small_025.save(outdir / 'A_lod_0.25_NATIVE.png')

    print(f"\n✅ 所有产物保存在: {outdir}")
    for p in sorted(outdir.iterdir()):
        sz = p.stat().st_size
        print(f"   - {p.name:<30s}  {sz/1024:>8.1f} KB")
