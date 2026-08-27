"""
圆角缺口根因诊断脚本
分析花幔和中古大花案例中圆角处理的详细问题
"""
import numpy as np
from PIL import Image
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import px_per_cm_at_dpi
from core.corner.detection import detect_border_layers_robust, detect_nested_rects
from core.corner.sector_render import (
    CORNER_ANGLES, _redraw_border_on_corner
)
from core.geometry import carve_corner_on_mask


def diagnose_corner_gap(
    img_path: str,
    corners: dict,
    output_dir: str = "debug_output"
):
    """诊断圆角缺口问题"""
    print("=" * 80)
    print(f"诊断圆角缺口: {os.path.basename(img_path)}")
    print("=" * 80)

    img = Image.open(img_path).convert('RGB')
    w, h = img.size
    dpi = 150
    bg_color = (255, 255, 255)

    print(f"图片尺寸: {w} x {h} px")
    print(f"DPI: {dpi}")
    print(f"圆角设置: {corners}")
    print("")

    # 检测边框层
    border_layers = detect_border_layers_robust(img, bg_color)
    print(f"[边框检测] 检测到 {len(border_layers)} 层边框:")
    for i, (color, thickness) in enumerate(border_layers):
        color_name = "黑色" if color[0] < 50 and color[1] < 50 and color[2] < 50 else \
                     "棕色" if color[0] > 100 and color[1] < 150 else \
                     "米色/间隙" if color[0] > 200 and abs(color[0] - color[1]) < 15 else \
                     f"其他({color})"
        print(f"  第{i+1}层: 厚度={thickness}px ({thickness/px_per_cm_at_dpi(dpi):.2f}cm), 颜色={color} [{color_name}]")
    total_depth = sum(t for _, t in border_layers)
    print(f"  边框总厚度: {total_depth}px ({total_depth/px_per_cm_at_dpi(dpi):.2f}cm)")
    print("")

    # 转换圆角为像素
    corners_px = {}
    for ck, radius_cm in corners.items():
        if radius_cm > 0:
            corners_px[ck] = int(round(radius_cm * dpi / 2.54))

    print("[圆角像素映射]")
    for ck, rp in corners_px.items():
        print(f"  {ck}: {rp}px ({rp/px_per_cm_at_dpi(dpi):.2f}cm)")
    print("")

    # 对每个有圆角的角进行详细分析
    for corner_key, r_px in corners_px.items():
        print("=" * 60)
        print(f"分析 {corner_key} 角 (半径={r_px}px)")
        print("=" * 60)

        # 计算圆心
        if corner_key == 'tl':
            cx, cy = r_px, r_px
        elif corner_key == 'tr':
            cx, cy = w - r_px, r_px
        elif corner_key == 'bl':
            cx, cy = r_px, h - r_px
        else:  # br
            cx, cy = w - r_px, h - r_px

        ang_min, ang_max = CORNER_ANGLES[corner_key]
        print(f"圆心: ({cx}, {cy})")
        print(f"角度范围: {ang_min}° 至 {ang_max}°")
        print("")

        # 检查 mask 创建
        mask = Image.new('L', (w, h), 0)
        carve_corner_on_mask(mask, corner_key, r_px)
        mask_arr = np.array(mask)

        # 检查弧线上的像素
        arr = np.array(img)
        yy, xx = np.mgrid[cy-r_px:cy+r_px+1, cx-r_px:cx+r_px+1].astype(np.float64)
        dx = xx - cx
        dy = yy - cy
        dist = np.sqrt(dx*dx + dy*dy)
        angle = np.degrees(np.arctan2(dy, dx))
        angle = np.mod(angle, 360.0)

        # 检查弧线上的像素（5°间隔）
        print("--- 弧线上的像素分析 ---")
        for ang in np.arange(ang_min - 5, ang_max + 5, 5):
            if ang_max == 360 and ang > ang_max:
                break
            if ang_max != 360 and ang > ang_max + 5:
                break

            rad = np.radians(ang)
            px = int(round(cx + r_px * np.cos(rad)))
            py = int(round(cy + r_px * np.sin(rad)))

            if 0 <= px < w and 0 <= py < h:
                mask_val = mask_arr[py, px]
                pixel_color = tuple(arr[py, px])

                # 确定像素位置
                actual_dist = np.sqrt((px - cx)**2 + (py - cy)**2)
                is_arc = abs(actual_dist - r_px) < 1.5
                is_inside = actual_dist < r_px - 1
                is_outside = actual_dist > r_px + 1.5

                status = ""
                if is_arc:
                    status = "弧线上"
                elif is_inside:
                    status = "弧线内"
                elif is_outside:
                    status = "弧线外"

                print(f"  {ang:6.1f}° ({px:4d}, {py:4d}) dist={actual_dist:.1f} mask={mask_val:3d} RGB={pixel_color} {status}")

        print("")

        # 检查弧内侧的边框层绘制
        print("--- 弧内侧边框层分析 ---")
        for offset in [0, -5, -10, -15, -20]:
            test_r = r_px + offset
            if test_r < 0:
                continue

            for ang in [ang_min, (ang_min + ang_max) / 2, ang_max]:
                rad = np.radians(ang)
                px = int(round(cx + test_r * np.cos(rad)))
                py = int(round(cy + test_r * np.sin(rad)))

                if 0 <= px < w and 0 <= py < h:
                    actual_dist = np.sqrt((px - cx)**2 + (py - cy)**2)
                    depth = r_px - actual_dist

                    # 确定边框层
                    if depth < border_layers[0][1]:
                        layer_info = f"第1层({border_layers[0][0]})"
                    elif depth < border_layers[0][1] + border_layers[1][1]:
                        layer_info = f"第2层({border_layers[1][0]})"
                    elif depth < total_depth:
                        layer_info = f"第3层({border_layers[2][0]})"
                    else:
                        layer_info = "内容区"

                    pixel_color = tuple(arr[py, px])
                    print(f"  r{offset:+4d}px {ang:6.1f}° ({px:4d}, {py:4d}) depth={depth:5.1f}px {layer_info} RGB={pixel_color}")

        print("")

    # 尝试完整的圆角处理流程
    print("=" * 60)
    print("完整圆角处理测试")
    print("=" * 60)

    # 创建测试结果
    result = img.copy()

    # 创建 mask
    full_mask = Image.new('L', (w, h), 0)
    for ck, rp in corners_px.items():
        carve_corner_on_mask(full_mask, ck, rp)

    # 应用 mask
    bg_img = Image.new('RGB', (w, h), bg_color)
    bg_img.paste(img, mask=full_mask)

    # 重绘边框
    for ck, rp in corners_px.items():
        _redraw_border_on_corner(
            bg_img, ck, rp, border_layers,
            src_img=img, validity_mask=full_mask
        )

    # 保存结果
    os.makedirs(output_dir, exist_ok=True)
    result_path = os.path.join(output_dir, f"diag_{os.path.basename(img_path)}")
    bg_img.save(result_path, quality=95)
    print(f"[结果] 已保存: {result_path}")

    # 对比分析
    print("")
    print("--- 对比分析（原图 vs 处理后）---")
    result_arr = np.array(bg_img)
    for corner_key, r_px in corners_px.items():
        if corner_key == 'tl':
            cx, cy = r_px, r_px
        elif corner_key == 'tr':
            cx, cy = w - r_px, r_px
        elif corner_key == 'bl':
            cx, cy = r_px, h - r_px
        else:
            cx, cy = w - r_px, h - r_px

        ang_min, ang_max = CORNER_ANGLES[corner_key]

        print(f"\n{corner_key} 角对比:")
        for ang in np.arange(ang_min, ang_max + 1, 15):
            rad = np.radians(ang)
            px = int(round(cx + r_px * np.cos(rad)))
            py = int(round(cy + r_px * np.sin(rad)))

            if 0 <= px < w and 0 <= py < h:
                orig_color = tuple(arr[py, px])
                new_color = tuple(result_arr[py, px])

                if orig_color != new_color:
                    print(f"  {ang:6.0f}° ({px:4d}, {py:4d}): 原图={orig_color} 处理后={new_color} 【已改变】")
                else:
                    print(f"  {ang:6.0f}° ({px:4d}, {py:4d}): {orig_color} 【未改变】")


if __name__ == '__main__':
    # 诊断花幔案例
    huaman_path = r"D:\SmartShapeCrop\logs\output\gap_verify_src.jpg"
    if os.path.exists(huaman_path):
        diagnose_corner_gap(huaman_path, {'bl': 3.6, 'br': 3.6})
    else:
        print(f"[警告] 花幔图片不存在: {huaman_path}")
        print("请提供花幔图片的正确路径")

    print("\n" + "=" * 80)
    print("诊断完成")
    print("=" * 80)
