"""模拟用户第二张草图，调试 OCR 识别流程"""
import cv2
import numpy as np
import tempfile
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.pool_designer.sketch_parser import (
    _safe_import_tesseract,
    _load_image,
    _to_gray,
    _find_two_nested_rectangles,
    _find_and_read_numbers,
    _find_number_regions,
    _ocr_region,
    parse_sketch,
)

# === Step 1: 生成模拟用户草图的测试图 ===
# 草图标注（从用户草图读取）：
#   总宽 133    (底部，水平方向)
#   总高 60.5   (左边，垂直方向)
#   上 边距 6   (顶部)
#   下边距 10   (底部)
#   左边距 14.6 (左边中间)
#   右边距 42.4 (右边中间)
#   内宽 76     (内框中间，水平方向)
#   内高 44.5   (内框中间，垂直方向)
# 修正后目标: width=133 x height=60.5（name_parser 已正确解析）

img = np.ones((1000, 800, 3), dtype=np.uint8) * 255

# 外框
cv2.rectangle(img, (100, 100), (700, 900), (0, 0, 200), 3)
# 内框
cv2.rectangle(img, (200, 250), (500, 800), (0, 0, 200), 3)

# 添加标注数字和箭头（模拟用户的草图标注）
# 总宽 133 - 底部
cv2.putText(img, '133', (380, 955), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 0, 200), 3)
# 总高 60.5 - 左边
cv2.putText(img, '60.5', (30, 520), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 200), 3)
# 上边距 6 —— 字号再加大，粗 4 像素，提升 Tesseract 识别率
cv2.putText(img, '6', (420, 210), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 200), 4)
# 下边距 10
cv2.putText(img, '10', (420, 850), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 200), 2)
# 左边距 14.6
cv2.putText(img, '14.6', (120, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 200), 2)
# 右边距 42.4
cv2.putText(img, '42.4', (600, 540), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 200), 2)
# 内宽 44.5
cv2.putText(img, '44.5', (300, 510), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 200), 3)
# 内高 76
cv2.putText(img, '76', (340, 680), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 200), 3)

# 保存临时文件
tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False, dir=tempfile.gettempdir())
tmp_path = tmp.name
tmp.close()
cv2.imwrite(tmp_path, img)
print(f'Test image saved: {tmp_path}')
print(f'Image size: {img.shape}')

# === Step 2: 检查 Tesseract ===
print()
print('=' * 60)
print('Step 2: 检查 Tesseract')
tesseract = _safe_import_tesseract()
if tesseract is not None:
    print('✓ Tesseract 加载成功')
    try:
        ver = tesseract.get_tesseract_version()
        print(f'  Version: {ver}')
    except Exception as e:
        print(f'  Version error: {e}')
else:
    print('✗ Tesseract 加载失败')

# === Step 3: 直接测试整张图 OCR ===
if tesseract is not None:
    print()
    print('=' * 60)
    print('Step 3: 直接对整张图做 OCR（先检查能否识别任何数字）')
    from PIL import Image as PILImage
    pil_full = PILImage.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    try:
        config_all = r'--oem 3 --psm 11 -c tessedit_char_whitelist=0123456789.'
        text_all = tesseract.image_to_string(pil_full, config=config_all).strip()
        print(f'  OCR(全图 whitelist): "{text_all}"')
    except Exception as e:
        print(f'  OCR error: {e}')

# === Step 4: 加载图 + 检测矩形 ===
print()
print('=' * 60)
print('Step 4: 加载图 + 矩形检测')
loaded, err = _load_image(tmp_path)
if err:
    print(f'ERROR load image: {err}')
    sys.exit(1)
gray = _to_gray(loaded)

top2 = _find_two_nested_rectangles(cv2, gray, loaded)
print(f'Detected {len(top2)} rectangles')
for i, r in enumerate(top2):
    print(f'  Rect {i}: x={r[0]} y={r[1]} w={r[2]} h={r[3]} score={r[4]:.3f}')

if len(top2) >= 2:
    outer = (top2[0][0], top2[0][1], top2[0][2], top2[0][3])
    inner = (top2[1][0], top2[1][1], top2[1][2], top2[1][3])
    print(f'Outer (px): {outer}')
    print(f'Inner (px): {inner}')

    # === Step 5: 调试 ROI 区域和数字检测 ===
    print()
    print('=' * 60)
    print('Step 5: 调试数字区域检测')

    h_img, w_img = gray.shape[:2]
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner

    # 直接扫描全局找数字区域，看看能找到多少
    print('  Full-image number regions:')
    full_roi = (0, 0, w_img, h_img)
    regions = _find_number_regions(cv2, gray, full_roi, max_regions=30)
    print(f'    Found {len(regions)} regions')
    for i, (rx, ry, rw, rh, ra) in enumerate(regions):
        print(f'    [{i}] x={rx} y={ry} w={rw} h={rh} area={ra}')

    # === Step 6: 逐个区域 OCR ===
    if tesseract is not None:
        print()
        print('=' * 60)
        print('Step 6: 逐个区域 OCR 识别')
        for i, reg in enumerate(regions):
            val = _ocr_region(cv2, gray, reg, tesseract)
            rx, ry, rw, rh, ra = reg
            print(f'  [{i}] region=({rx},{ry},{rw},{rh}) area={ra}  OCR result: {val}')

    # === Step 7: 完整 _find_and_read_numbers ===
    print()
    print('=' * 60)
    print('Step 7: 调用完整 _find_and_read_numbers')
    ocr_result = _find_and_read_numbers(cv2, gray, outer, inner, tesseract)
    for k, (v, c) in ocr_result.items():
        print(f'  {k}: {v} (conf={c})')

# === Step 8: 完整 parse_sketch ===
# 修正后：水池模式 target 应为 width=133, height=60.5（name_parser 已正确解析）
print()
print('=' * 60)
print('Step 8: 完整 parse_sketch')
result = parse_sketch(tmp_path, target_outer_w_cm=133.0, target_outer_h_cm=60.5)
print(f'Success: {result.success}')
print(f'Method: {result.method}')
print(f'Outer: {result.outer_w_cm:.2f} x {result.outer_h_cm:.2f} cm')
print(f'Inner: {result.inner_w_cm:.2f} x {result.inner_h_cm:.2f} cm')
print(f'Margins: T={result.margin_top_cm:.2f} B={result.margin_bottom_cm:.2f} L={result.margin_left_cm:.2f} R={result.margin_right_cm:.2f}')
print(f'Message: {result.message[:200]}')

# 对比期望值（修正后的正确值：width=133, height=60.5）
print()
print('=' * 60)
print('期望对比:')
expected = {
    'outer_w': 133.0,
    'outer_h': 60.5,
    'margin_top': 6,
    'margin_bottom': 10,
    'margin_left': 14.6,
    'margin_right': 42.4,
    'inner_w': 76,
    'inner_h': 44.5,
}
actual = {
    'outer_w': result.outer_w_cm,
    'outer_h': result.outer_h_cm,
    'margin_top': result.margin_top_cm,
    'margin_bottom': result.margin_bottom_cm,
    'margin_left': result.margin_left_cm,
    'margin_right': result.margin_right_cm,
    'inner_w': result.inner_w_cm,
    'inner_h': result.inner_h_cm,
}
for k in expected:
    exp = expected[k]
    act = actual[k]
    diff = abs(act - exp)
    ok = '✓' if diff < 2.0 else '✗'
    print(f'  {k}: expected={exp}, actual={act:.2f}, diff={diff:.2f} {ok}')

os.unlink(tmp_path)
