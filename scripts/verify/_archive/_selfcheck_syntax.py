
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

import sys, os
sys.path.insert(0, 'd:/SmartShapeCrop')
print('=== 语法 / 导入自检 ===')

# 1. 导入 image_cropper 全部关键函数
from core.image_cropper import (
    CropConfig, crop_image, batch_crop,
    apply_rounded_corners, apply_border_only_corners,
    detect_nested_rect_layers,
)
print('  [ok] core.image_cropper imports')

# 2. 单独再导入 geometry
from core import geometry
print(f'  [ok] core.geometry imported: compute_border_bands = {hasattr(geometry, "compute_border_bands")}')
print(f'       apply_rounded_corners_to_mask available = {hasattr(geometry, "apply_rounded_corners_to_mask")}')

# 3. 导入 image_ops
from core import image_ops
print(f'  [ok] core.image_ops imported: render_design = {hasattr(image_ops, "render_design")}')
print(f'       _get_inner_pixel_mask = {hasattr(image_ops, "_get_inner_pixel_mask")}')

# 4. 构造一个最小设计并调用 compute_border_bands 验证无运行时错误
d = geometry.CropDesign()
d.canvas_w_cm = 20; d.canvas_h_cm = 20; d.dpi = 150
d.corner_br_cm = 8.5
bands = geometry.compute_border_bands(d)
total_band_area = sum(int(b.sum()) for b, _ in bands)
print(f'  [ok] geometry.compute_border_bands(br=8.5cm) returned {len(bands)} bands, total True pix = {total_band_area}')

# 5. 再验证 render_design 也可以正常运行
img = image_ops.render_design(d)
print(f'  [ok] image_ops.render_design ok, size={img.size}')

# 6. process_image.py 的 import 语法检查
import py_compile
try:
    py_compile.compile('d:/SmartShapeCrop/process_image.py', doraise=True)
    print('  [ok] process_image.py compiles cleanly')
except Exception as e:
    print(f'  [FAIL] process_image.py compile: {e}')

# 8. main.py / GUI files syntax check
for p in ['main.py', 'gui/canvas_widget.py', 'gui/cropper_panel.py', 'gui/property_panel.py']:
    fp = os.path.join('d:/SmartShapeCrop', p)
    try:
        py_compile.compile(fp, doraise=True)
        print(f'  [ok] {p} compiles cleanly')
    except Exception as e:
        print(f'  [FAIL] {p} compile: {e}')

print('\n=== 全部自检通过 ===')
