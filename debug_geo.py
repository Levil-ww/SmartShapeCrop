"""Quick debug: trace geometry-driven parse for the problematic sketch.

Run via: python debug_geo.py <sketch_path>
"""
import sys
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
logger = logging.getLogger('debug')

sys.path.insert(0, os.path.dirname(__file__))

import cv2
import numpy as np

# 尝试加载 tesseract
_tesseract = None
try:
    import pytesseract
    tess_paths = [
        r'E:\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    ]
    for tp in tess_paths:
        if os.path.exists(tp):
            pytesseract.pytesseract.tesseract_cmd = tp
            os.environ['TESSDATA_PREFIX'] = os.path.join(os.path.dirname(tp), 'tessdata')
            _tesseract = pytesseract
            logger.info("Tesseract 已配置: %s", tp)
            break
except Exception as e:
    logger.warning("Tesseract 加载失败: %s", e)

from core.pool_designer.sketch_parser import (
    _find_all_rectangles,
    _select_best_nested_pair,
    _compute_gaps,
    _find_direction_labels_in_gaps,
    _scan_gap_for_value,
    _scan_inner_dimensions,
    _build_binary_masks,
    _multi_scale_ocr_scan,
)


def main(img_path):
    img = cv2.imread(img_path)
    if img is None:
        logger.error("无法读取: %s", img_path)
        return
    color_img = img
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    target_w = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
    target_h = float(sys.argv[3]) if len(sys.argv) > 3 else 58.0

    logger.info("=== 输入: %s (图像尺寸 %dx%d) 目标 %.1fx%.1fcm ===",
                img_path, gray.shape[1], gray.shape[0], target_w, target_h)

    # Step 1: 矩形候选
    all_rects = _find_all_rectangles(cv2, gray, color_img)
    logger.info("--- 矩形候选 (按面积降序) ---")
    for i, r in enumerate(all_rects[:15]):
        x, y, w, h, sc, ar = r
        logger.info("  [%d] xywh=(%d,%d,%d,%d) area=%d aspect=%.2f score=%.2f",
                    i, x, y, w, h, ar, min(w, h)/max(w, h), sc)

    # Step 2: 选择最佳对
    outer, inner = _select_best_nested_pair(all_rects, target_w, target_h,
                                            cv2=cv2, gray_img=gray, tesseract=_tesseract)
    if outer is None or inner is None:
        logger.error("未能选出外框+内框对")
        return

    logger.info("--- 选中 ---")
    logger.info("  外框: xywh=(%d,%d,%d,%d) 面积=%d", *outer[:4], outer[5])
    logger.info("  内框: xywh=(%d,%d,%d,%d) 面积=%d", *inner[:4], inner[5])

    ow, oh = outer[2], outer[3]
    iw, ih = inner[2], inner[3]

    cm_px_x = target_w / ow if target_w > 0 and ow > 0 else 1.0
    cm_px_y = target_h / oh if target_h > 0 and oh > 0 else 1.0
    logger.info("--- 像素密度: x=%.4f cm/px, y=%.4f cm/px ---", cm_px_x, cm_px_y)

    # 像素级边距
    ox, oy, ow, oh = outer[:4]
    ix, iy, iw, ih = inner[:4]
    g_top_px = iy - oy
    g_bot_px = (oy + oh) - (iy + ih)
    g_left_px = ix - ox
    g_right_px = (ox + ow) - (ix + iw)
    logger.info("--- 像素级边距 (raw) ---")
    logger.info("  top=%dpx (=%.2fcm) bot=%dpx (=%.2fcm) left=%dpx (=%.2fcm) right=%dpx (=%.2fcm)",
                g_top_px, g_top_px * cm_px_y,
                g_bot_px, g_bot_px * cm_px_y,
                g_left_px, g_left_px * cm_px_x,
                g_right_px, g_right_px * cm_px_x)

    # Step 3: gaps
    gaps = _compute_gaps(ox, oy, ow, oh, ix, iy, iw, ih)
    logger.info("--- Gaps ---")
    for d, (x1, y1, x2, y2) in gaps.items():
        logger.info("  %s: (%d,%d)-(%d,%d) 尺寸=%dx%d", d, x1, y1, x2, y2, x2-x1, y2-y1)

    # Step 4: direction labels (no tesseract)
    dir_labels = _find_direction_labels_in_gaps(cv2, gray, None, gaps, outer, inner, color_img)
    logger.info("--- Direction labels ---")
    for d, v in dir_labels.items():
        logger.info("  %s: pos=%s conf=%.3f char=%s", d, v[0], v[1], v[2])

    # Step 5: OCR scan each gap
    logger.info("--- OCR 扫描各间隙 ---")
    if _tesseract is not None:
        for d, (x1, y1, x2, y2) in gaps.items():
            pad_x = max(5, int((x2 - x1) * 0.2))
            pad_y = max(5, int((y2 - y1) * 0.2))
            sx1 = max(0, x1 - pad_x)
            sy1 = max(0, y1 - pad_y)
            sx2 = min(gray.shape[1], x2 + pad_x)
            sy2 = min(gray.shape[0], y2 + pad_y)
            scan = gray[sy1:sy2, sx1:sx2]
            results = _multi_scale_ocr_scan(cv2, _tesseract, scan)
            logger.info("  %s 间隙 OCR 结果 (共%da): %s", d, len(results),
                        [(round(r[0], 2), r[1]) for r in results[:8]])

    # Visualize: save annotated image
    vis = color_img.copy()
    cv2.rectangle(vis, (ox, oy), (ox + ow, oy + oh), (0, 255, 0), 3)
    cv2.rectangle(vis, (ix, iy), (ix + iw, iy + ih), (0, 0, 255), 3)
    for d, (x1, y1, x2, y2) in gaps.items():
        color = {'top': (255, 0, 0), 'bottom': (255, 0, 0),
                 'left': (0, 255, 255), 'right': (0, 255, 255)}[d]
        cv2.rectangle(vis, (x1, y1), (x2, y2), color, 2)
    for d, info in dir_labels.items():
        (lx, ly), _, ch = info
        cv2.putText(vis, str(ch), (int(lx) - 8, int(ly) + 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)

    out_path = os.path.splitext(img_path)[0] + '_debug.png'
    cv2.imwrite(out_path, vis)
    logger.info("=== 可视化输出: %s ===", out_path)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法: python debug_geo.py <sketch_path> [target_w target_h]")
        sys.exit(1)
    main(sys.argv[1])
