"""验证 smart-resample 下采样保护是否按用户实际场景正确触发。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image
import numpy as np

# Monkey-patch PIL.Image.resize to capture which resample was used
_orig_resize = Image.Image.resize
_calls = []
def _spy_resize(self, size, resample=None, *a, **kw):
    _calls.append(('src=%dx%d' % self.size, 'tgt=%dx%d' % size, 'resample=%s' % (
        {getattr(Image, k): k for k in ['NEAREST','BILINEAR','BICUBIC','LANCZOS','BOX']}.get(resample, str(resample))
    )))
    return _orig_resize(self, size, resample, *a, **kw)
Image.Image.resize = _spy_resize

from core.image_ops import adapt_pool_material, fit_image_to_rect

# ===== 用户场景参数（同量化分析脚本）=====
# 内挖素材源 @300DPI：35×55cm 设计 → 像素竖版 → rotate_270 后 横版 55×35 对应 6496×4134
mat_w_px, mat_h_px = 6496, 4134  # 已旋转后的横版尺寸（像素）
# LOD inner target: ~789 x 494
lod_iw, lod_ih = 789, 494
# Full inner target: ~3159 x 1978
full_iw, full_ih = 3159, 1978
# Upscale test: tiny source → larger target (upscale should keep BILINEAR preview)
tiny_w, tiny_h = 200, 150
big_w, big_h = 1600, 1200

# Make fake source images (repeating lily pattern = gradient stripes)
def make_src(w, h):
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for i in range(w):
        arr[:, i, :] = (80 + (i % 17) * 10, 110 + (i % 13) * 6, 50 + (i % 23) * 8)
    return Image.fromarray(arr, 'RGB')

cases = [
    # (label, src_w, src_h, tgt_w, tgt_h, quality, expected_trigger_downsample_force_LANCZOS)
    ('[LOD Inner] preview - 8.2x down (CRITICAL case)',     mat_w_px, mat_h_px, lod_iw,  lod_ih,  'preview', True),
    ('[Full Inner] preview - 2.1x down',                     mat_w_px, mat_h_px, full_iw, full_ih, 'preview', True),
    ('[1:1 near] preview - 1.02x upscale',                   mat_w_px, mat_h_px, mat_w_px+50, mat_h_px+30, 'preview', False),
    ('[Upscale] preview - 8x up',                            tiny_w, tiny_h,   big_w,   big_h,   'preview', False),
    ('[Mild down 1.2x] preview - <1.5x threshold',           1000, 800, 820, 660,                            'preview', False),
    ('[Export] any scale - already LANCZOS',                 mat_w_px, mat_h_px, lod_iw, lod_ih,            'export',  'N/A (always LANCZOS)'),
]

print('='*70)
print('adapt_pool_material() STRETCH 下采样保护验证')
print('='*70)
for label, sw, sh, tw, th, q, expect_trigger in cases:
    _calls.clear()
    src = make_src(sw, sh)
    # adapt_pool_material 方向判定：pixel AR vs canvas AR 一致 (都横版) → 不旋转
    result = adapt_pool_material(src, tw, th, canvas_w_cm=100, canvas_h_cm=50, quality=q)
    # Find which resize calls were made (should be just 1 stretch)
    resizes = [c for c in _calls]
    print(f'\n{label}')
    for r in resizes:
        print(f'  → resize {r[0]} → {r[1]}  [{r[2]}]')
    # Verify
    if expect_trigger == 'N/A (always LANCZOS)':
        ok = all('LANCZOS' in r[2] for r in resizes)
        print(f'  ➜ expect=export all LANCZOS. Result: {"✅" if ok else "❌ FAIL"}')
    elif expect_trigger == True:
        ok = any('LANCZOS' in r[2] for r in resizes)
        print(f'  ➜ expect=force LANCZOS (heavy downsample). Result: {"✅" if ok else "❌ FAIL — should have used LANCZOS!"}')
    else:
        # expect BILINEAR for preview, LANCZOS for export
        expected_samp = 'BILINEAR' if q == 'preview' else 'LANCZOS'
        ok = all(expected_samp in r[2] for r in resizes)
        print(f'  ➜ expect={expected_samp} (no heavy downsample). Result: {"✅" if ok else "❌ FAIL"}')

# Also test fit_image_to_rect cover + stretch modes
print('\n' + '='*70)
print('fit_image_to_rect() COVER & STRETCH 下采样保护验证')
print('='*70)
cover_cases = [
    ('cover preview - heavy downsample', mat_w_px, mat_h_px, lod_iw, lod_ih, 'cover', 'preview', True),
    ('cover preview - mild 1.2x down',    1000, 800, 820, 660, 'cover', 'preview', False),
    ('cover export',                       mat_w_px, mat_h_px, full_iw, full_ih, 'cover', 'export', 'LANCZOS'),
    ('stretch preview - heavy downsample', mat_w_px, mat_h_px, lod_iw, lod_ih, 'stretch', 'preview', True),
]
for label, sw, sh, tw, th, mode, q, expect_trigger in cover_cases:
    _calls.clear()
    src = make_src(sw, sh)
    result = fit_image_to_rect(src, tw, th, mode=mode, quality=q)
    resizes = [c for c in _calls]
    print(f'\n{label}')
    for r in resizes:
        print(f'  → resize {r[0]} → {r[1]}  [{r[2]}]')
    if expect_trigger == 'LANCZOS':
        ok = all('LANCZOS' in r[2] for r in resizes)
        print(f'  ➜ expect=LANCZOS. Result: {"✅" if ok else "❌ FAIL"}')
    elif expect_trigger == True:
        ok = any('LANCZOS' in r[2] for r in resizes)
        print(f'  ➜ expect=force LANCZOS (heavy downsample). Result: {"✅" if ok else "❌ FAIL — should have used LANCZOS!"}')
    else:
        expected_samp = 'BILINEAR' if q == 'preview' else 'LANCZOS'
        ok = all(expected_samp in r[2] for r in resizes)
        print(f'  ➜ expect={expected_samp} (no heavy downsample). Result: {"✅" if ok else "❌ FAIL"}')

Image.Image.resize = _orig_resize
print('\n✅ [DONE] 如果所有 CASE 结果都是 ✅ = smart 下采样保护按预期工作')
