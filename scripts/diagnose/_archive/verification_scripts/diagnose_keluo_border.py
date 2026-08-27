# -*- coding: utf-8 -*-
"""
诊断 "吸水皮革-定制-定制尺寸-克罗印花" 圆角边框粗细问题
- 检测边框层厚度
- 分析 2cm 半径圆角弧线的绘制厚度
- 对比直边边框粗细 vs 圆角弧线粗细
"""
import os
import sys
import numpy as np
from PIL import Image

# 添加上级路径以便导入 core 模块
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.image_cropper import (
    load_source_image,
    _get_border_layers_robust,
    apply_border_only_corners,
)
from core.corner.detection import _detect_border_layers


def analyze_border_thickness(img, dpi=150, bg_color=(255, 255, 255)):
    """详细分析边框层检测结果"""
    w, h = img.size
    print("=" * 70)
    print(f"图片尺寸: {w} x {h} px  ({w * 2.54 / dpi:.2f} x {h * 2.54 / dpi:.2f} cm @ {dpi} DPI)")
    print("=" * 70)
    
    # 1. 使用 robust 检测
    layers_robust = _get_border_layers_robust(img, bg_color)
    print(f"\n[Robust 检测] 检测到 {len(layers_robust)} 层边框:")
    total_depth = 0
    for i, (color, thickness_px) in enumerate(layers_robust):
        thickness_cm = thickness_px * 2.54 / dpi
        total_depth += thickness_px
        print(f"  第{i+1}层: 颜色={color}, 厚度={thickness_px}px ({thickness_cm:.3f}cm)")
    print(f"  累计总厚度: {total_depth}px ({total_depth * 2.54 / dpi:.3f}cm)")
    
    # 2. 使用基础检测
    layers_basic = _detect_border_layers(img, bg_color=bg_color)
    print(f"\n[Basic 检测] 检测到 {len(layers_basic)} 层边框:")
    total_depth2 = 0
    for i, (color, thickness_px) in enumerate(layers_basic):
        thickness_cm = thickness_px * 2.54 / dpi
        total_depth2 += thickness_px
        print(f"  第{i+1}层: 颜色={color}, 厚度={thickness_px}px ({thickness_cm:.3f}cm)")
    print(f"  累计总厚度: {total_depth2}px ({total_depth2 * 2.54 / dpi:.3f}cm)")
    
    # 3. 从 4 条边的中点逐像素测量实际边框厚度（手工验证）
    print("\n" + "=" * 70)
    print("[手工验证] 从 4 条边中点向内扫描，逐像素分析颜色变化")
    print("=" * 70)
    arr = np.array(img)
    
    edges = [
        ('底边 (y 中点)', h - 1, w // 2, -1, 0),
        ('顶边 (y 中点)', 0, w // 2, 1, 0),
        ('左边 (x 中点)', h // 2, 0, 0, 1),
        ('右边 (x 中点)', h // 2, w - 1, 0, -1),
    ]
    
    px_per_cm = dpi / 2.54
    for edge_name, y0, x0, dy_step, dx_step in edges:
        print(f"\n--- {edge_name} ---")
        prev_color = None
        segment_start = 0
        segments = []
        max_scan = min(200, w // 4, h // 4)
        
        for d in range(max_scan):
            y = y0 + d * dy_step
            x = x0 + d * dx_step
            if not (0 <= y < h and 0 <= x < w):
                break
            color = tuple(arr[y, x, :].astype(int))
            
            if prev_color is None:
                prev_color = color
                segment_start = 0
                continue
            
            # 计算颜色距离
            dist = np.sqrt(sum((a - b) ** 2 for a, b in zip(color, prev_color)))
            
            if dist > 15:  # 阈值
                segments.append((segment_start, d - 1, prev_color))
                segment_start = d
                prev_color = color
        
        if segment_start < max_scan - 1:
            segments.append((segment_start, max_scan - 1, prev_color))
        
        # 打印前 8 段
        for idx, (s, e, col) in enumerate(segments[:8]):
            thickness = e - s + 1
            cm_val = thickness / px_per_cm
            print(f"  段{idx+1}: 深度 {s}~{e}px, 厚={thickness}px ({cm_val:.3f}cm), 颜色={col}")
    
    return layers_robust


def simulate_corner_rendering(img_path, target_w_cm, target_h_cm, corner_r_cm, 
                              output_path, dpi=150, bg_color=(255, 255, 255)):
    """模拟圆角处理并分析弧线粗细"""
    print("\n" + "=" * 70)
    print(f"[模拟圆角处理] {target_w_cm}x{target_h_cm}cm, 右下角圆角半径={corner_r_cm}cm")
    print("=" * 70)
    
    target_w_px = int(round(target_w_cm * dpi / 2.54))
    target_h_px = int(round(target_h_cm * dpi / 2.54))
    corner_r_px = int(round(corner_r_cm * dpi / 2.54))
    
    print(f"目标尺寸: {target_w_px} x {target_h_px} px")
    print(f"圆角半径: {corner_r_px} px ({corner_r_px * 2.54 / dpi:.3f} cm)")
    
    src = load_source_image(img_path)
    print(f"源图尺寸: {src.size} px")
    
    # 先在源图上检测边框层
    src_layers = _get_border_layers_robust(src, bg_color)
    print(f"\n源图边框检测: {len(src_layers)} 层")
    src_total = 0
    for i, (color, thickness) in enumerate(src_layers):
        src_total += thickness
        cm_v = thickness * 2.54 / dpi
        print(f"  层{i+1}: {color}, {thickness}px ({cm_v:.3f}cm)")
    
    # 缩放
    sw, sh = src.size
    scale_x = target_w_px / sw
    scale_y = target_h_px / sh
    scale = max(scale_x, scale_y)
    print(f"\n缩放比例: scale_x={scale_x:.4f}, scale_y={scale_y:.4f}, 使用 scale={scale:.4f}")
    
    scaled_layers = [
        (color, max(1, int(round(thickness * scale))))
        for color, thickness in src_layers
    ]
    print(f"缩放后边框层:")
    for i, (color, thickness) in enumerate(scaled_layers):
        cm_v = thickness * 2.54 / dpi
        print(f"  层{i+1}: {color}, {thickness}px ({cm_v:.3f}cm)")
    
    # 简单缩放
    cropped = src.resize((target_w_px, target_h_px), Image.LANCZOS)
    
    # 在缩放后的图上再次检测边框层（对比）
    scaled_detected_layers = _get_border_layers_robust(cropped, bg_color)
    print(f"\n缩放后图上重新检测: {len(scaled_detected_layers)} 层")
    for i, (color, thickness) in enumerate(scaled_detected_layers):
        cm_v = thickness * 2.54 / dpi
        print(f"  层{i+1}: {color}, {thickness}px ({cm_v:.3f}cm)")
    
    # 应用圆角（仅边框区域）
    corners = {'tl': 0, 'tr': 0, 'bl': 0, 'br': corner_r_cm}
    result = apply_border_only_corners(
        cropped, corners, dpi, bg_color,
        pre_detected_layers=scaled_layers
    )
    
    result.save(output_path, 'JPEG', quality=95, optimize=True, dpi=(dpi, dpi))
    print(f"\n[完成] 结果已保存到: {output_path}")
    
    # 分析右下角圆角的弧线粗细
    analyze_corner_arc_thickness(result, cropped, 'br', corner_r_px, scaled_layers, dpi)
    return result


def analyze_corner_arc_thickness(result_img, src_img, corner_key, r_px, border_layers, dpi):
    """分析圆角弧线的实际粗细"""
    w, h = result_img.size
    result_arr = np.array(result_img)
    src_arr = np.array(src_img)
    
    px_per_cm = dpi / 2.54
    
    # 圆心坐标
    if corner_key == 'br':
        cx, cy = w - r_px, h - r_px
    else:
        return
    
    print(f"\n" + "=" * 70)
    print(f"[圆角弧线粗细分析] {corner_key.upper()} 角, 半径 R={r_px}px")
    print("=" * 70)
    
    # 方法1: 沿径向扫描，测量从最外层边框颜色开始的连续边框像素数量
    # 扫描几条径向线: 200°, 210°, 220°, 230°, 240°, 250°, 260° (BR 角的角度范围 180-270)
    angles_deg = [200, 215, 225, 235, 250]
    
    total_border_thickness_px = sum(t for _, t in border_layers) if border_layers else 0
    outer_color = border_layers[0][0] if border_layers else (0, 0, 0)
    print(f"最外层边框颜色: {outer_color}")
    print(f"检测累计边框厚度: {total_border_thickness_px}px ({total_border_thickness_px/px_per_cm:.3f}cm)")
    
    if border_layers:
        print(f"最外层边框层厚度: {border_layers[0][1]}px ({border_layers[0][1]/px_per_cm:.3f}cm)")
    
    for angle_deg in angles_deg:
        angle_rad = np.radians(angle_deg)
        dx_dir = np.cos(angle_rad)
        dy_dir = np.sin(angle_rad)
        
        print(f"\n--- 径向扫描 angle={angle_deg}° ---")
        print(f"  (dx={dx_dir:.4f}, dy={dy_dir:.4f})")
        
        # 从 r = r_px + 5（弧外 5px）向内扫到 r = r_px - 30
        pixels_info = []
        for r_off in range(5, -40, -1):
            r = r_px + r_off
            x = int(round(cx + r * dx_dir))
            y = int(round(cy + r * dy_dir))
            
            if 0 <= x < w and 0 <= y < h:
                res_color = tuple(result_arr[y, x, :].astype(int))
                src_color = tuple(src_arr[y, x, :].astype(int))
                
                # 计算与最外层颜色的距离
                dist_outer = np.sqrt(sum((a - b)**2 for a, b in zip(res_color, outer_color)))
                
                # 计算与白底的距离
                dist_white = np.sqrt(sum((a - b)**2 for a, b in zip(res_color, (255, 255, 255))))
                
                label = "边框色" if dist_outer < 30 else ("白底" if dist_white < 30 else "其他")
                pixels_info.append((r_off, x, y, res_color, dist_outer, label))
        
        # 打印每像素信息（从外向内）
        for r_off, x, y, col, dist, label in pixels_info:
            r_actual = r_px + r_off
            depth = r_px - r_actual  # depth = R - r
            # 检查哪个深度层
            layer_idx = -1
            cum = 0
            for li, (_, lt) in enumerate(border_layers):
                cum_prev = cum
                cum += lt
                if cum_prev <= depth < cum:
                    layer_idx = li
                    break
            
            layer_str = f" [层{layer_idx+1}]" if layer_idx >= 0 else ""
            print(f"  r_off={r_off:+3d} (r={r_actual}, depth={depth:2d}) pos=({x:4d},{y:4d}) "
                  f"color={col} dist_to_outer={dist:5.1f} {label}{layer_str}")
        
        # 统计连续的边框色像素（从弧外进入弧内）
        print(f"\n  弧线粗细统计:")
        consecutive_border = 0
        max_consecutive = 0
        in_border = False
        border_start = None
        border_end = None
        
        for i, (r_off, x, y, col, dist, label) in enumerate(pixels_info):
            is_border = (label == "边框色")
            
            if is_border and not in_border:
                in_border = True
                border_start = r_off
                consecutive_border = 1
            elif is_border and in_border:
                consecutive_border += 1
            elif not is_border and in_border:
                if consecutive_border > max_consecutive:
                    max_consecutive = consecutive_border
                    border_end = pixels_info[i-1][0]
                in_border = False
                consecutive_border = 0
        
        if in_border and consecutive_border > max_consecutive:
            max_consecutive = consecutive_border
            border_end = pixels_info[-1][0]
        
        if max_consecutive > 0:
            cm_v = max_consecutive / px_per_cm
            print(f"    → 连续边框像素数: {max_consecutive}px ({cm_v:.3f}cm)")
            if border_start is not None and border_end is not None:
                print(f"       范围: r_off ∈ [{border_end}, {border_start}]")
                print(f"       对应 depth ∈ [{r_px - (r_px + border_start)}, {r_px - (r_px + border_end)}] "
                      f"= [{-border_start}, {-border_end}]")
        else:
            print(f"    → 未检测到连续边框像素！")


def main():
    dpi = 150
    bg_color = (255, 255, 255)
    
    # 参数配置（匹配用户描述）
    # 吸水皮革-定制-定制尺寸-克罗印花;58x149CM，右下角2cm圆角
    target_w_cm = 58.0
    target_h_cm = 149.0
    corner_r_cm = 2.0
    
    # 先找 psd_demo 目录下的图片试试
    test_dir = r"D:\SmartShapeCrop\psd_demo"
    candidates = []
    if os.path.isdir(test_dir):
        candidates = [os.path.join(test_dir, f) for f in os.listdir(test_dir)
                      if f.lower().endswith(('.jpg', '.png', '.jpeg', '.psd'))]
    
    src_path = None
    if candidates:
        # 取第一张可用图片
        src_path = candidates[0]
        print(f"[警告] 未找到 '克罗印花' 源图，使用测试图片替代: {os.path.basename(src_path)}")
    else:
        print("[错误] 未找到任何测试图片！请放置 '吸水皮革-定制-定制尺寸-克罗印花' 源图到 psd_demo 目录。")
        print("       程序将以诊断模式演示边框检测逻辑...")
        # 创建一张模拟图片用于演示
        sim_w = int(round(40 * dpi / 2.54))
        sim_h = int(round(120 * dpi / 2.54))
        from PIL import ImageDraw
        sim_img = Image.new('RGB', (sim_w, sim_h), (255, 250, 240))
        draw = ImageDraw.Draw(sim_img)
        # 画外层棕色边框 (3mm 厚)
        brown_thick = max(3, int(round(0.3 * dpi / 2.54)))
        black_thick = max(2, int(round(0.15 * dpi / 2.54)))
        draw.rectangle([0, 0, sim_w-1, sim_h-1], fill=(139, 90, 43))
        draw.rectangle([brown_thick, brown_thick, sim_w-1-brown_thick, sim_h-1-brown_thick], fill=(255, 255, 255))
        # 画内层黑色细线
        inner_off = brown_thick + int(round(0.4 * dpi / 2.54))
        draw.rectangle([inner_off, inner_off, sim_w-1-inner_off, sim_h-1-inner_off], outline=(0, 0, 0), width=black_thick)
        src_path = os.path.join(os.path.dirname(__file__), '..', '..', 'debug_output', 'simulated_keluo.jpg')
        os.makedirs(os.path.dirname(src_path), exist_ok=True)
        sim_img.save(src_path, 'JPEG', quality=95, dpi=(dpi, dpi))
        print(f"       已创建模拟图: {src_path}")
    
    output_dir = os.path.join(os.path.dirname(src_path), '..', 'logs', 'output')
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, 'diagnose_keluo_result.jpg')
    
    # 1. 加载并分析源图
    src = load_source_image(src_path)
    border_layers = analyze_border_thickness(src, dpi, bg_color)
    
    # 2. 模拟圆角处理
    simulate_corner_rendering(src_path, target_w_cm, target_h_cm, corner_r_cm,
                              output_path, dpi, bg_color)


if __name__ == '__main__':
    main()
