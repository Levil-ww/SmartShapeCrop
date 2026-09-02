import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ===== 用户场景（来自右侧面板参数）=====
canvas_w_cm = 140.3
canvas_h_cm = 59.9
dpi = 150
inner_w_cm = 53.5
inner_h_cm = 33.5

cm2px = lambda cm: int(round(cm * dpi / 2.54))
canvas_w_px = cm2px(canvas_w_cm)
canvas_h_px = cm2px(canvas_h_cm)
inner_w_px = cm2px(inner_w_cm)
inner_h_px = cm2px(inner_h_cm)
print('=== 全分辨率目标 ===')
print(f'  Canvas: {canvas_w_cm}x{canvas_h_cm}cm @ {dpi} DPI = {canvas_w_px}x{canvas_h_px} px')
print(f'  Inner : {inner_w_cm}x{inner_h_cm}cm = {inner_w_px}x{inner_h_px} px')

LOD = 0.25
lod_cw = max(1, int(canvas_w_px * LOD))
lod_ch = max(1, int(canvas_h_px * LOD))
lod_iw = max(1, int(inner_w_px * LOD))
lod_ih = max(1, int(inner_h_px * LOD))
print(f'\n=== LOD (scale={LOD}) 目标 ===')
print(f'  Canvas LOD: {lod_cw}x{lod_ch} px  (1/{canvas_w_px/lod_cw:.1f}x, 1/{canvas_h_px/lod_ch:.1f}x)')
print(f'  Inner  LOD: {lod_iw}x{lod_ih} px')

def cm2px_mat(cm, mat_dpi):
    return int(round(cm * mat_dpi / 2.54))

for mat_dpi in [150, 200, 300]:
    print(f'\n--- 源素材按设计尺寸 @ {mat_dpi} DPI 估算 ---')
    outer_src_w = cm2px_mat(138.5, mat_dpi)
    outer_src_h = cm2px_mat(59, mat_dpi)
    inner_src_w_raw = cm2px_mat(35, mat_dpi)
    inner_src_h_raw = cm2px_mat(55, mat_dpi)
    inner_src_w = inner_src_h_raw
    inner_src_h = inner_src_w_raw
    print(f'  Outer src: {outer_src_w}x{outer_src_h} px (design 138.5x59cm, landscape)')
    print(f'  Inner src (after ROTATE_270): {inner_src_w}x{inner_src_h} px (35x55 → rotate → 55x35)')

    def pct(src, tgt, label):
        sw, sh = src
        tw, th = tgt
        sx = tw / sw
        sy = th / sh
        if sx > 1.05 or sy > 1.05:
            kind = f'UPSCALE ↑  max={max(sx,sy):.2f}x'
        elif abs(sx-1)<0.05 and abs(sy-1)<0.05:
            kind = '1:1'
        else:
            kind = f'DOWNSCALE ↓ W={1/sx:.1f}x / H={1/sy:.1f}x'
        return f'  {label}: sx={sx:.3f}x, sy={sy:.3f}x  → {kind}'

    print('  [FULL 全分辨率 quality=preview/export]')
    print(pct((outer_src_w, outer_src_h), (canvas_w_px, canvas_h_px), 'Outer→Canvas'))
    print(pct((inner_src_w, inner_src_h), (inner_w_px, inner_h_px), 'Inner→Hole '))
    print('  [LOD scale=0.25  quality=preview → BILINEAR]')
    print(pct((outer_src_w, outer_src_h), (lod_cw, lod_ch), 'Outer→LOD'))
    print(pct((inner_src_w, inner_src_h), (lod_iw, lod_ih), 'Inner→LOD'))
    sx_lod_inner = lod_iw / inner_src_w
    sy_lod_inner = lod_ih / inner_src_h
    if min(sx_lod_inner, sy_lod_inner) < 0.5:
        print(f'      ⚠️  CRITICAL: BILINEAR downsample <0.5x (>2x down) → ALIASING! Need LANCZOS/BOX')
print('\n[结论] Inner LOD 下采样倍率远超 BILINEAR 抗锯齿能力（>2x下采样时BILINEAR等于几乎不抗锯齿）。')
print('     花纹精细线条被压缩进极少像素 → aliasing/色块感 → 用户说"失真"。')
