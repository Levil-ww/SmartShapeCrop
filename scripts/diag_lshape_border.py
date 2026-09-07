"""
诊断脚本：L 形挖角边框补全问题
问题1：蝴蝶契约 — L形没有对齐连接的直角口
问题2：绽蔓 — 多了灰色的L形边框带

运行: python scripts/diag_lshape_border.py
"""
import sys, os, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.DEBUG, format='%(levelname)s %(name)s: %(message)s')

import numpy as np
from PIL import Image

from core.lshape_border import (
    detect_border_v13,
    detect_pool_material_borders,
    apply_lshape_border_completion,
    patch_lshape_cut,
    _is_real_border,
    _filter_content_layers,
)
from core.image_ops import load_image_rgb, adapt_pool_material
from core.geometry import RectShape, CropDesign


def find_material(keyword):
    """在常见位置搜索素材文件"""
    import os
    roots = [r'C:\Users\Administrator\Desktop',
             r'C:\Users\Administrator\Documents',
             r'F:\SmartShapeCrop',
             r'C:\Users\Administrator']
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # 限制深度
            depth = dirpath.replace(root, '').count(os.sep)
            if depth > 4:
                dirnames[:] = []
                continue
            for f in filenames:
                if keyword in f.lower() and f.lower().endswith(('.jpg', '.png', '.jpeg', '.jpeg', '.webp')):
                    return os.path.join(dirpath, f)
    return None


def diagnose_material(path, label):
    """对单个素材做完整诊断"""
    print(f"\n{'='*60}")
    print(f"诊断素材: {label}")
    print(f"路径: {path}")
    if not path or not os.path.isfile(path):
        print("  [SKIP] 文件不存在")
        return

    src = load_image_rgb(path)
    sw, sh = src.size
    print(f"  原始尺寸: {sw}x{sh}")

    # ---- V13 检测 ----
    v13 = detect_border_v13(src)
    print(f"  V13 detect_border_v13: {v13}")

    # ---- 旧路径检测 ----
    old_layers = detect_pool_material_borders(src, (255,255,255))
    print(f"  旧路径 detect_pool_material_borders: {old_layers}")

    # ---- 像素颜色剖面（顶边中心列）----
    arr = np.array(src)
    H, W = arr.shape[:2]
    col_center = arr[:min(H//3, 400), W//2]
    print(f"  顶边中心列前400px颜色（采样间隔10px）:")
    for i in range(0, min(400, len(col_center)), 10):
        c = col_center[i]
        print(f"    y={i:3d}: RGB({c[0]:3d},{c[1]:3d},{c[2]:3d})")

    # ---- 模拟画布渲染 ----
    # 假设画布 800x500, inner_rect (50,50,700,400), cut_corner='br', cut_w=150, cut_h=100
    canvas_w, canvas_h = 800, 500
    canvas_arr = np.array(adapt_pool_material(src, canvas_w, canvas_h), dtype=np.uint8)
    scale_x = canvas_w / sw
    scale_y = canvas_h / sh
    print(f"  画布尺寸: {canvas_w}x{canvas_h}, scale=({scale_x:.2f},{scale_y:.2f})")

    # 模拟 inner_rect 和 cut 参数（br 角）
    inner_rect = RectShape(x=50, y=50, w=700, h=400)
    cut_corner = 'br'
    cut_w = 150
    cut_h = 100

    # 先填白 cut 区
    from core.geometry import build_lshape_mask
    lshape_mask = build_lshape_mask(
        (canvas_w, canvas_h), inner_rect, cut_corner, cut_w, cut_h,
        {'tl': 0, 'tr': 0, 'bl': 0, 'br': 0}, fill_value=255)
    full_inner = build_lshape_mask(
        (canvas_w, canvas_h), inner_rect, cut_corner, 0, 0,
        {'tl': 0, 'tr': 0, 'bl': 0, 'br': 0}, fill_value=255)
    cut_area = np.array(full_inner, dtype=bool) & ~np.array(lshape_mask, dtype=bool)
    canvas_arr[cut_area] = 255
    print(f"  cut 区像素数: {cut_area.sum()}")

    # 调用边框补全
    ok = apply_lshape_border_completion(
        canvas_arr=canvas_arr,
        material_img=Image.fromarray(canvas_arr.copy()),
        src_material_img=src,
        outer_rect=inner_rect,
        cut_corner=cut_corner,
        cut_w_px=cut_w,
        cut_h_px=cut_h,
        dpi=150,
        bg_color=(255, 255, 255),
    )
    print(f"  apply_lshape_border_completion 返回: {ok}")

    # 保存结果
    out_path = os.path.join(os.path.dirname(__file__), f'diag_{label}_result.png')
    Image.fromarray(canvas_arr).save(out_path)
    print(f"  诊断结果保存到: {out_path}")

    # 检查 cut 区附近的实际颜色
    cut_inner_y = canvas_h - cut_h - 5  # cut 区顶部往上 5px
    cut_inner_x = canvas_w - cut_w - 5  # cut 区左部往右 5px
    print(f"\n  cut 边缘附近像素采样:")
    # 水平切边附近（br 角: 水平切边 y = inner_rect.bottom - cut_h）
    h_edge_y = int(inner_rect.y + inner_rect.h - cut_h)
    v_edge_x = int(inner_rect.x + inner_rect.w - cut_w)
    print(f"    水平切边 y={h_edge_y}, 垂直切边 x={v_edge_x}")

    for dy in range(-8, 9):
        y = h_edge_y + dy
        if 0 <= y < canvas_h:
            row = canvas_arr[y, v_edge_x-3:v_edge_x+8, :]
            colors = [f"({r},{g},{b})" for r,g,b in row]
            print(f"    y={y:3d}: {' '.join(colors)}")


def main():
    # 搜索两个问题素材
    materials = [
        ('蝴蝶契约', 'butterfly'),
        ('绽蔓', 'zhanman'),
    ]
    for keyword, label in materials:
        path = find_material(keyword)
        print(f"\n搜索 '{keyword}' -> {path}")
        if path:
            diagnose_material(path, label)

    # 也测试一个已知正常的素材
    for kw, lbl in [('克罗印花', 'kailuo'), ('安妮森林', 'anni')]:
        p = find_material(kw)
        if p:
            diagnose_material(p, lbl)


if __name__ == '__main__':
    main()
