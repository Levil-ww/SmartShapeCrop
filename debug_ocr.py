import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(message)s')
from core.pool_designer.sketch_parser import _multi_scale_ocr_scan, _merge_split_decimals, _find_all_rectangles, _select_best_nested_pair, _divide_8_zones
import cv2
import os

for fname, label in [('_test_6sketch_1.png', '图1'), ('_test_6sketch_6.png', '图6')]:
    print('='*60)
    print('诊断 %s (%s)' % (label, fname))
    img_path = os.path.abspath('scripts/diagnose/' + fname)
    color = cv2.imread(img_path)
    gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
    
    # 检测内外框
    all_rects = _find_all_rectangles(cv2, gray, color)
    outer, inner = _select_best_nested_pair(all_rects)
    ox, oy, ow, oh = outer[:4]
    ix, iy, iw, ih = inner[:4]
    print('外框: (%d,%d,%d,%d)  内框: (%d,%d,%d,%d)' % (ox, oy, ow, oh, ix, iy, iw, ih))
    
    # OCR
    import pytesseract
    ocr_raw = _multi_scale_ocr_scan(cv2, pytesseract, gray)
    ocr_merged = _merge_split_decimals(ocr_raw)
    
    print('\nOCR 原始候选（去重前）:')
    for v, c, b in ocr_raw:
        print('  val=%-8.1f conf=%d bbox=(%d,%d,%d,%d)' % (v, c, b[0], b[1], b[2], b[3]))
    
    print('\nOCR 合并后候选:')
    for v, c, b in sorted(ocr_merged, key=lambda r: r[1], reverse=True):
        print('  val=%-8.1f conf=%d bbox=(%d,%d,%d,%d) cx=%.0f cy=%.0f' % (v, c, b[0], b[1], b[2], b[3], b[0]+b[2]/2, b[1]+b[3]/2))
    
    # 区域映射
    zone_of = _divide_8_zones(outer, inner, gray.shape[1], gray.shape[0])
    print('\n区域分配结果:')
    buckets = {}
    for v, c, b in ocr_merged:
        cx = b[0] + b[2]/2
        cy = b[1] + b[3]/2
        zone = zone_of(cx, cy)
        if zone:
            if zone not in buckets:
                buckets[zone] = []
            buckets[zone].append((v, c, b))
            print('  val=%-8.1f → %s (cx=%.0f cy=%.0f)' % (v, zone, cx, cy))
    print('  (未分配的值跳过)')
    
    print('\n各区域候选汇总:')
    for z, cands in sorted(buckets.items()):
        vals = [v for v, _, _ in sorted(cands, key=lambda r: r[1], reverse=True)]
        print('  %s: %s' % (z, vals))
    print()