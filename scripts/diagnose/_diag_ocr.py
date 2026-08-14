"""诊断脚本：在草图图像上运行完整 OCR 管道，输出每一步中间值。
用法: python _diag_ocr.py <草图图像路径> [目标宽cm] [目标高cm]
"""
import sys
import os
import logging

logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.pool_designer.sketch_parser import (
    _safe_import_cv2, _safe_import_tesseract,
    _load_image, _to_gray, _assess_complexity,
    _find_two_nested_rectangles, _find_and_read_numbers,
    _assign_ocr_values_to_fields, _ocr_full_image,
    _geometry_fallback_values, parse_sketch
)

def main():
    if len(sys.argv) < 2:
        print("用法: python _diag_ocr.py <草图图像路径> [目标宽cm] [目标高cm]")
        print("示例: python _diag_ocr.py sketch.png 133.0 60.5")
        sys.exit(1)

    image_path = sys.argv[1]
    target_w = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    target_h = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0

    print(f"{'='*60}")
    print(f"诊断图像: {image_path}")
    print(f"目标尺寸: {target_w} x {target_h} cm")
    print(f"{'='*60}")

    cv2 = _safe_import_cv2()
    if cv2 is None:
        print("ERROR: OpenCV 未安装")
        return

    img, err = _load_image(image_path)
    if err:
        print(f"ERROR: {err}")
        return

    gray = _to_gray(img)
    h, w = gray.shape[:2]
    print(f"\n[Step 1] 图像加载: {w}x{h} px")

    # L1 复杂度评估
    is_complex, reason = _assess_complexity(gray)
    print(f"[Step 2] 复杂度评估: is_complex={is_complex}, reason={reason}")

    # L2 几何检测
    top2 = _find_two_nested_rectangles(cv2, gray, img)
    if len(top2) < 2:
        print(f"[Step 3] 几何检测失败: 只找到 {len(top2)} 个矩形")
        return

    (ox, oy, ow, oh, os_score), (ix, iy, iw, ih, ins_score) = top2
    print(f"[Step 3] 几何检测:")
    print(f"  外框: px=({ox},{ow}) size={ow}x{oh} score={os_score:.3f}")
    print(f"  内框: px=({ix},{iy}) size={iw}x{ih} score={ins_score:.3f}")
    print(f"  像素比: {ow/max(1,oh):.3f} (宽/高)")

    # L3 OCR
    tesseract = _safe_import_tesseract()
    if tesseract is None:
        print("[Step 4] Tesseract 不可用，跳过 OCR")
    else:
        print(f"[Step 4] Tesseract 加载成功")

    # 4a: 全图 OCR
    if tesseract:
        print(f"\n--- 4a: 全图 OCR (_ocr_full_image) ---")
        hits = _ocr_full_image(cv2, gray, tesseract)
        print(f"  OCR 识别到 {len(hits)} 个数字:")
        for val, xc, yc, conf in hits:
            print(f"    {val:8.2f}  @ ({xc:7.1f}, {yc:7.1f})  conf={conf:.3f}")

        # 4b: 空间映射
        print(f"\n--- 4b: 空间映射 (_assign_ocr_values_to_fields) ---")
        mapped = _assign_ocr_values_to_fields(hits, (ox, oy, ow, oh), (ix, iy, iw, ih), h, w)
        for key, (val, conf) in mapped.items():
            print(f"  {key:15s}: {val:8.2f}  (conf={conf})")

    # 4c: _find_and_read_numbers (完整流程，含策略 A+B)
    if tesseract:
        print(f"\n--- 4c: 完整数字检测 (_find_and_read_numbers) ---")
        numbers = _find_and_read_numbers(cv2, gray, (ox, oy, ow, oh), (ix, iy, iw, ih), tesseract)
        for key, (val, conf) in numbers.items():
            print(f"  {key:15s}: {val:8.2f}  (conf={conf})")

    # 4d: 几何回退值
    print(f"\n--- 4d: 几何回退值 ---")
    geo = _geometry_fallback_values((ox, oy, ow, oh), (ix, iy, iw, ih), target_w, target_h)
    for key, (val, conf) in geo.items():
        print(f"  {key:15s}: {val:8.2f}  (conf={conf})")

    # 5: 完整 parse_sketch 流程
    print(f"\n{'='*60}")
    print(f"[Step 5] 完整 parse_sketch 流程")
    print(f"{'='*60}")
    result = parse_sketch(image_path, target_outer_w_cm=target_w, target_outer_h_cm=target_h)
    print(f"\n  result.success = {result.success}")
    print(f"  result.method  = {result.method}")
    print(f"  result.message = {result.message}")
    print(f"  result.debug keys = {list(result.debug.keys())}")

    if result.debug:
        print(f"\n  --- Debug 详情 ---")
        for k, v in result.debug.items():
            print(f"    {k}: {v}")

    print(f"\n  --- 最终识别结果 ---")
    print(f"  外框: {result.outer_w_cm:.2f} x {result.outer_h_cm:.2f} cm")
    print(f"  内挖: {result.inner_w_cm:.2f} x {result.inner_h_cm:.2f} cm")
    print(f"  边距: 上{result.margin_top_cm:.2f}/下{result.margin_bottom_cm:.2f}/左{result.margin_left_cm:.2f}/右{result.margin_right_cm:.2f} cm")

    # 几何自洽性验证
    if result.outer_w_cm > 0 and result.outer_h_cm > 0:
        print(f"\n  --- 几何自洽性验证 ---")
        dh = abs(result.outer_w_cm - result.inner_w_cm - (result.margin_left_cm + result.margin_right_cm))
        dv = abs(result.outer_h_cm - result.inner_h_cm - (result.margin_top_cm + result.margin_bottom_cm))
        print(f"  水平: |{result.outer_w_cm:.2f} - {result.inner_w_cm:.2f} - ({result.margin_left_cm:.2f}+{result.margin_right_cm:.2f})| = {dh:.2f}")
        print(f"  垂直: |{result.outer_h_cm:.2f} - {result.inner_h_cm:.2f} - ({result.margin_top_cm:.2f}+{result.margin_bottom_cm:.2f})| = {dv:.2f}")

if __name__ == '__main__':
    main()
