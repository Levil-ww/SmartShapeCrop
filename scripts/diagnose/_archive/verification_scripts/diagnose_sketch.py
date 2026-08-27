"""诊断草图解析逻辑 - 追踪OCR值在哪里被破坏。

生成一个与用户截图中一致的合成草图，然后逐步追踪 parse_sketch 的每一步。
"""
import sys
import os
import logging

logging.basicConfig(level=logging.DEBUG, format='%(name)s: %(message)s')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
from PIL import Image, ImageDraw, ImageFont

def create_test_sketch(output_path):
    """创建与用户截图一致的合成草图。
    
    目标尺寸: 60.5 x 133 cm (竖边x横边)
    外框: 60.5 x 133
    内框: 44.5 x 76 (水平方向内框宽76, 竖直方向内框高44.5)
    边距: 上6, 下10, 左14.6, 右42.4
    
    在草图中标注:
    - 60.5 在左侧 (竖边=总高)
    - 133 在底部 (横边=总宽)
    - 44.5 在内框中 (内框高)
    - 76 在内框中 (内框宽)
    - 14.6 左侧边距
    - 42.4 右侧边距
    - 6 上边距
    - 10 下边距
    """
    # 使用像素尺寸: 1 cm = 8 px (60.5*8=484, 133*8=1064)
    scale = 8
    W = int(133 * scale)  # 1064 px
    H = int(60.5 * scale)  # 484 px
    
    # 边距转换为像素
    ml = int(14.6 * scale)  # 116 px
    mr = int(42.4 * scale)  # 339 px
    mt = int(6 * scale)     # 48 px
    mb = int(10 * scale)    # 80 px
    
    # 内框尺寸
    iw = int(76 * scale)    # 608 px
    ih = int(44.5 * scale)  # 356 px
    
    # 创建白色背景
    img = Image.new('RGB', (W + 100, H + 100), 'white')
    draw = ImageDraw.Draw(img)
    
    ox, oy = 50, 50  # 外框左上角
    # 画外框 (红色)
    draw.rectangle([ox, oy, ox + W, oy + H], outline='red', width=3)
    
    # 画内框 (蓝色)
    ix = ox + ml
    iy = oy + mt
    draw.rectangle([ix, iy, ix + iw, iy + ih], outline='blue', width=3)
    # 添加尺寸标注 (使用黑色文字)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except:
        font = ImageFont.load_default()
    
    # 标注总高 60.5 (在左侧, 垂直排列)
    draw.text((ox - 45, oy + H//2 - 10), '60.5', fill='black', font=font)
    
    # 标注总宽 133 (在底部)
    draw.text((ox + W//2 - 15, oy + H + 10), '133', fill='black', font=font)
    
    # 标注内框宽 76 (内框中上方)
    draw.text((ix + iw//2 - 10, iy + 10), '76', fill='black', font=font)
    
    # 标注内框高 44.5 (内框中下方)
    draw.text((ix + iw//2 - 15, iy + ih//2), '44.5', fill='black', font=font)
    
    # 标注左边距 14.6
    draw.text((ox + ml//2 - 15, iy + ih//2 - 10), '14.6', fill='black', font=font)
    
    # 标注右边距 42.4
    draw.text((ix + iw + mr//2 - 15, iy + ih//2 - 10), '42.4', fill='black', font=font)
    
    # 标注上边距 6
    draw.text((ox + W//2 - 5, oy + mt//2 - 10), '6', fill='black', font=font)
    
    # 标注下边距 10
    draw.text((ox + W//2 - 8, iy + ih + mb//2 - 10), '10', fill='black', font=font)
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path)
    print(f"测试草图已保存: {output_path}")
    print(f"图像尺寸: {W+100} x {H+100} px")
    print(f"外框像素: ({ox},{oy}) - ({ox+W},{oy+H}), {W}x{H} px")
    print(f"内框像素: ({ix},{iy}) - ({ix+iw},{iy+ih}), {iw}x{ih} px")
    return output_path

def trace_parsing(image_path, target_w, target_h):
    """逐步追踪解析过程"""
    from core.pool_designer.sketch_parser import (
        _safe_import_cv2, _load_image, _to_gray,
        _assess_complexity, _find_two_nested_rectangles,
        _safe_import_tesseract, _find_and_read_numbers,
        _geometry_fallback_values, _score_assignment_consistency,
        _value_based_assignment,
        _assign_ocr_values_to_fields, _ocr_full_image,
        _enumerate_assignments, _enumerate_inner_margin_assignments,
        _validate_and_fix_margins,
    )
    
    cv2 = _safe_import_cv2()
    img, err = _load_image(image_path)
    if err:
        print(f"加载失败: {err}")
        return
    
    gray = _to_gray(img)
    h, w = gray.shape[:2]
    print(f"\n=== L1: 复杂度评估 ===")
    is_complex, reason = _assess_complexity(gray)
    print(f"is_complex={is_complex}, reason={reason}")
    if is_complex:
        print("草图过于复杂，跳过")
        return
    
    print(f"\n=== L2: 几何检测 ===")
    top2 = _find_two_nested_rectangles(cv2, gray, img)
    if len(top2) < 2:
        print(f"只检测到 {len(top2)} 个矩形")
        return
    
    (ox, oy, ow, oh, os_score), (ix, iy, iw, ih, ins_score) = top2
    print(f"外框: ({ox},{oy},{ow},{oh}), score={os_score:.3f}")
    print(f"内框: ({ix},{iy},{iw},{ih}), score={ins_score:.3f}")
    print(f"像素外框宽高比: {ow/oh if oh>0 else 'N/A':.2f}")
    
    print(f"\n=== L3: OCR 识别 ===")
    tesseract = _safe_import_tesseract()
    if tesseract:
        print("Tesseract 已加载")
        
        # Step 1: 全图 OCR
        print("\n--- Step 1: 全图 OCR ---")
        ocr_hits = _ocr_full_image(cv2, gray, tesseract)
        print(f"检测到 {len(ocr_hits)} 个 OCR 值:")
        for val, xc, yc, conf in ocr_hits:
            print(f"  {val:.1f} at ({xc:.0f},{yc:.0f}), conf={conf:.2f}")
        
        # Step 2: 空间分配
        print("\n--- Step 2: 空间位置分配 ---")
        result_spatial = _assign_ocr_values_to_fields(
            ocr_hits, (ox, oy, ow, oh), (ix, iy, iw, ih),
            h, w, target_w_hint=target_w, target_h_hint=target_h
        )
        for key in ['total_w', 'total_h', 'inner_w', 'inner_h', 
                     'margin_top', 'margin_bottom', 'margin_left', 'margin_right']:
            val, conf = result_spatial.get(key, (0, 0))
            print(f"  {key}: {val:.2f} (conf={conf})")
        
        sc_spatial = _score_assignment_consistency(result_spatial)
        print(f"空间分配自洽性: {sc_spatial:.3f}")
        
        # Step 3: 数值大小分配
        print("\n--- Step 3: 数值大小分配 ---")
        result_value = _value_based_assignment(
            ocr_hits, (ox, oy, ow, oh), (ix, iy, iw, ih),
            target_w, target_h
        )
        for key in ['total_w', 'total_h', 'inner_w', 'inner_h', 
                     'margin_top', 'margin_bottom', 'margin_left', 'margin_right']:
            val, conf = result_value.get(key, (0, 0))
            print(f"  {key}: {val:.2f} (conf={conf})")
        
        sc_value = _score_assignment_consistency(result_value)
        print(f"数值分配自洽性: {sc_value:.3f}")
        
        if sc_value > sc_spatial:
            print(f"\n*** 数值分配优于空间分配！采用数值分配结果 ***")
            result_ocr = result_value
        else:
            print(f"\n空间分配更优，采用空间分配结果")
            result_ocr = result_spatial
        
        # 几何回退
        geo_result = _geometry_fallback_values(
            (ox, oy, ow, oh), (ix, iy, iw, ih), target_w, target_h
        )
    else:
        print("Tesseract 未加载，使用几何回退")
        result_ocr = {k: (0, 0) for k in ['total_w', 'total_h', 'inner_w', 'inner_h', 
                                          'margin_top', 'margin_bottom', 'margin_left', 'margin_right']}
        geo_result = _geometry_fallback_values(
            (ox, oy, ow, oh), (ix, iy, iw, ih), target_w, target_h
        )
    
    print(f"\n=== 融合结果 ===")
    fused = {}
    for key in result_ocr:
        ocr_val, ocr_conf = result_ocr[key]
        geo_val, geo_conf = geo_result.get(key, (0, 0))
        if ocr_conf > 0 and ocr_val > 0:
            fused[key] = ocr_val
        elif geo_conf > 0 and geo_val > 0:
            fused[key] = geo_val
        else:
            fused[key] = 0.0
    
    for key, val in fused.items():
        print(f"  {key}: {val:.2f}")
    
    # 几何自洽性检测
    print(f"\n=== 几何自洽性检测 ===")
    outer_w = fused.get('total_w', 0)
    outer_h = fused.get('total_h', 0)
    inner_w = fused.get('inner_w', 0)
    inner_h = fused.get('inner_h', 0)
    mt = fused.get('margin_top', 0)
    mb = fused.get('margin_bottom', 0)
    ml = fused.get('margin_left', 0)
    mr = fused.get('margin_right', 0)
    
    all_positive = all(v > 0 for v in [outer_w, outer_h, inner_w, inner_h, mt, mb, ml, mr])
    print(f"所有字段为正: {all_positive}")
    
    if all_positive:
        h_diff = abs(outer_w - inner_w - (ml + mr))
        v_diff = abs(outer_h - inner_h - (mt + mb))
        h_tol = max(2.0, outer_w * 0.10)
        v_tol = max(2.0, outer_h * 0.10)
        h_ok = h_diff <= h_tol
        v_ok = v_diff <= v_tol
        print(f"水平: |{outer_w} - {inner_w} - ({ml}+{mr})| = {h_diff:.2f} <= {h_tol:.2f}? {'✓' if h_ok else '✗'}")
        print(f"垂直: |{outer_h} - {inner_h} - ({mt}+{mb})| = {v_diff:.2f} <= {v_tol:.2f}? {'✓' if v_ok else '✗'}")
        
        # Case 2: 交换 outer 和 inner 的宽高
        h_diff2 = abs(outer_h - inner_h - (ml + mr))
        v_diff2 = abs(outer_w - inner_w - (mt + mb))
        h_ok2 = h_diff2 <= max(2.0, outer_h * 0.10)
        v_ok2 = v_diff2 <= max(2.0, outer_w * 0.10)
        print(f"\nCase2 (交换outer宽高):")
        print(f"  水平: |{outer_h} - {inner_h} - ({ml}+{mr})| = {h_diff2:.2f}? {'✓' if h_ok2 else '✗'}")
        print(f"  垂直: |{outer_w} - {inner_w} - ({mt}+{mb})| = {v_diff2:.2f}? {'✓' if v_ok2 else '✗'}")
        
        c2_full = h_ok2 and v_ok2
        print(f"  Case2 完整自洽: {c2_full}")
    
    print(f"\n=== 方向矫正 Phase 1 ===")
    ratio_px = ow / oh if oh > 0 else 1
    ratio_val = outer_w / outer_h if outer_h > 0 else 1
    px_is_landscape = ratio_px > 1.25
    px_is_portrait = ratio_px < 1.0/1.25
    val_is_landscape = ratio_val > 1.25
    val_is_portrait = ratio_val < 1.0/1.25
    need_swap = (px_is_portrait and val_is_landscape) or (px_is_landscape and val_is_portrait)
    print(f"像素: {ow}x{oh} (ratio={ratio_px:.2f}, {'横' if px_is_landscape else '竖' if px_is_portrait else '近方'})")
    print(f"数值: {outer_w:.1f}x{outer_h:.1f} (ratio={ratio_val:.2f}, {'横' if val_is_landscape else '竖' if val_is_portrait else '近方'})")
    print(f"需要交换: {need_swap}")
    if need_swap:
        outer_w, outer_h = outer_h, outer_w
        print(f"交换后: {outer_w:.1f}x{outer_h:.1f}")
    
    # 目标尺寸验证
    print(f"\n=== 目标尺寸验证 ===")
    print(f"目标: {target_w} x {target_h}")
    if target_w > 0 and target_h > 0:
        ratio_w = outer_w / target_w
        ratio_h = outer_h / target_h
        print(f"当前/目标 宽比: {ratio_w:.2f}, 高比: {ratio_h:.2f}")
        over20_w = ratio_w > 1.20 or ratio_w < 0.83
        over20_h = ratio_h > 1.20 or ratio_h < 0.83
        print(f"宽偏差>20%: {over20_w}, 高偏差>20%: {over20_h}")
        
        # 检查 OCR 自洽性
        if all_positive:
            c1_h = abs(outer_w - inner_w - (ml + mr)) <= max(2.0, outer_w * 0.10)
            c1_v = abs(outer_h - inner_h - (mt + mb)) <= max(2.0, outer_h * 0.10)
            c2_h = abs(outer_h - inner_h - (ml + mr)) <= max(2.0, outer_h * 0.10)
            c2_v = abs(outer_w - inner_w - (mt + mb)) <= max(2.0, outer_w * 0.10)
            ocr_fully_consistent = (c1_h and c1_v) or (c2_h and c2_v)
        else:
            ocr_fully_consistent = False
        print(f"OCR 自洽: {ocr_fully_consistent}")
        
        if over20_w or over20_h:
            if ocr_fully_consistent:
                print("→ OCR 自洽但尺寸偏差大，仅覆盖外框，不强制重算")
            else:
                print("→ OCR 不自洽，强制重算！")
        else:
            print("→ 偏差在20%内，不强制重算")
    
    print(f"\n=== 预期正确结果 ===")
    print(f"外框: 133.0 x 60.5 cm")
    print(f"内框: 76.0 x 44.5 cm")
    print(f"边距: 上6.0 / 下10.0 / 左14.6 / 右42.4 cm")

if __name__ == '__main__':
    sketch_path = os.path.join(os.path.dirname(__file__), 'test_sketch.png')
    create_test_sketch(sketch_path)
    trace_parsing(sketch_path, target_w=133.0, target_h=60.5)