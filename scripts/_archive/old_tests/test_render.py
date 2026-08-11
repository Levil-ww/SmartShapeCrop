"""
test_render.py
非 GUI 自检测试：直接用核心库渲染 4 种示例模板并保存成 JPG，验证框架可用。
不依赖 PyQt5，双击或命令行运行：python test_render.py
"""
import os
import sys

# 核心库（Pillow / numpy 必须；psd-tools / opencv 可选，代码已做降级）
try:
    from PIL import Image
except ImportError:
    print("[ERROR] Pillow 未安装，请先运行：pip install Pillow")
    sys.exit(1)
try:
    import numpy as np
except ImportError:
    print("[ERROR] numpy 未安装，请先运行：pip install numpy")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.geometry import CropDesign, BorderLayer
from core.image_ops import render_design, save_jpg


OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../test_output")
os.makedirs(OUT_DIR, exist_ok=True)


def _base_50x70(mode, **kw) -> CropDesign:
    return CropDesign(
        canvas_w_cm=50, canvas_h_cm=70, dpi=300,
        outer_margin_cm=1.0, mode=mode,
        borders=[
            BorderLayer(offset_cm=0.3, fill_type='solid', color=(0, 0, 0)),
            BorderLayer(offset_cm=0.2, fill_type='solid', color=(255, 255, 255)),
            BorderLayer(offset_cm=0.3, fill_type='solid', color=(0, 0, 0)),
        ],
        outer_bg_color=(0, 0, 0),
        hole_bg_color=(250, 245, 230),
        **kw
    )


def render_rect_nested():
    d = _base_50x70('rect_hole',
                    inner_margin_top_cm=10, inner_margin_bottom_cm=10,
                    inner_margin_left_cm=10, inner_margin_right_cm=10)
    img = render_design(d)
    p = os.path.join(OUT_DIR, "01_rect_nested_50x70cm.jpg")
    save_jpg(img, p, quality=95, dpi=300)
    print(f"[OK] {p}  size={img.width}x{img.height}")
    return p


def render_lshape():
    d = _base_50x70('rect_lshape',
                    canvas_w_cm=80, canvas_h_cm=30,
                    inner_margin_top_cm=3, inner_margin_bottom_cm=3,
                    inner_margin_left_cm=3, inner_margin_right_cm=3,
                    l_corner='br', l_cut_w_cm=20, l_cut_h_cm=15)
    img = render_design(d)
    p = os.path.join(OUT_DIR, "02_lshape_80x30cm.jpg")
    save_jpg(img, p, quality=95, dpi=300)
    print(f"[OK] {p}  size={img.width}x{img.height}")
    return p


def render_ellipse():
    d = _base_50x70('ellipse_hole', ellipse_rx_ratio=0.30, ellipse_ry_ratio=0.28)
    img = render_design(d)
    p = os.path.join(OUT_DIR, "03_ellipse_50x70cm.jpg")
    save_jpg(img, p, quality=95, dpi=300)
    print(f"[OK] {p}  size={img.width}x{img.height}")
    return p


def render_tile_nested():
    # 瓷砖嵌套（先用纯色模拟，用户把自己的 JPG 素材路径填到 hole_bg_image 即可）
    d = CropDesign(
        mode='rect_hole',
        canvas_w_cm=70, canvas_h_cm=60, dpi=300,
        outer_margin_cm=0.8,
        inner_margin_top_cm=12, inner_margin_bottom_cm=12,
        inner_margin_left_cm=25, inner_margin_right_cm=8,
        borders=[
            BorderLayer(offset_cm=0.2, fill_type='solid', color=(240, 230, 210)),
            BorderLayer(offset_cm=0.2, fill_type='solid', color=(0, 0, 0)),
            BorderLayer(offset_cm=0.2, fill_type='solid', color=(255, 255, 255)),
            BorderLayer(offset_cm=0.2, fill_type='solid', color=(0, 0, 0)),
        ],
        outer_bg_color=(0, 0, 0),
        hole_bg_color=(250, 245, 230),
    )
    img = render_design(d)
    p = os.path.join(OUT_DIR, "04_tile_nested_70x60cm.jpg")
    save_jpg(img, p, quality=95, dpi=300)
    print(f"[OK] {p}  size={img.width}x{img.height}")
    return p


def main():
    print("=" * 60)
    print("SmartShapeCrop  核心渲染自检（不启动 GUI）")
    print("=" * 60)
    for i, fn in enumerate([render_rect_nested, render_lshape, render_ellipse, render_tile_nested], 1):
        try:
            fn()
        except Exception as e:
            print(f"[FAIL] 模板 {i}: {e}")
            import traceback; traceback.print_exc()
    print("=" * 60)
    print(f"输出目录：{OUT_DIR}")
    print("若无报错，核心渲染链路正常，可启动 GUI：python main.py")


if __name__ == '__main__':
    main()
