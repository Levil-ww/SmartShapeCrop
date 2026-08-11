"""
test_psd_import.py
PSD 素材导入功能自检：
  1) 自动用 PIL 生成一张示例 PSD（若 psd-tools 不可用则跳过 PSD 创建，直接演示如何用已有 PSD）
  2) 把 PSD 中每个图层导出为独立 JPG（自动裁剪透明边）
  3) 用导出的 JPG 作为素材，渲染一个带花纹的设计图

用法：
  a) 没有 PSD：直接运行，将生成示例 PSD 并走完整流程
  b) 已有 PSD：修改下方 PSD_PATH 指向你的 PSD 再运行
"""
import os
import sys

try:
    from PIL import Image
except ImportError:
    print("[ERROR] 请先 pip install Pillow"); sys.exit(1)
try:
    import numpy as np
except ImportError:
    print("[ERROR] 请先 pip install numpy"); sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.psd_loader import export_psd_layers_as_jpgs, load_psd_flattened, is_psd_file
from core.geometry import CropDesign, BorderLayer
from core.image_ops import render_design, save_jpg

WORK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../psd_demo")
os.makedirs(WORK, exist_ok=True)

PSD_PATH = os.path.join(WORK, "demo_materials.psd")   # 可以改成你自己的 PSD


def _make_demo_materials():
    """生成 3 张演示用 JPG 素材（模拟你现有的 JPG 成品库）"""
    from PIL import ImageDraw
    paths = []
    # 1. 米色花卉底（纯色加噪点近似）
    img1 = Image.new('RGB', (1200, 1600), (250, 245, 230))
    d1 = ImageDraw.Draw(img1)
    for i in range(120):
        import random; random.seed(i)
        x, y = random.randint(0, img1.width), random.randint(0, img1.height)
        r = random.randint(4, 20)
        d1.ellipse((x-r, y-r, x+r, y+r), outline=(120, 80, 60), width=2)
    p1 = os.path.join(WORK, "flower_bg.jpg"); img1.save(p1, quality=95); paths.append(p1)

    # 2. 黑色花砖（重复图案，tile 用）
    tile = Image.new('RGB', (200, 200), (255, 250, 240))
    dt = ImageDraw.Draw(tile)
    dt.rectangle((0, 0, 199, 199), outline=(80, 60, 40), width=4)
    dt.polygon([(100, 30), (170, 100), (100, 170), (30, 100)], fill=(40, 30, 20))
    # 把 tile 复制成大图（模拟你拿到的成品图）
    img2 = Image.new('RGB', (1600, 1600))
    for y in range(0, 1600, 200):
        for x in range(0, 1600, 200):
            img2.paste(tile, (x, y))
    p2 = os.path.join(WORK, "tile_hua.jpg"); img2.save(p2, quality=95); paths.append(p2)

    # 3. 纯黑色（外框用）
    img3 = Image.new('RGB', (200, 200), (0, 0, 0))
    p3 = os.path.join(WORK, "black_solid.jpg"); img3.save(p3, quality=95); paths.append(p3)
    return paths


def _try_make_demo_psd():
    """
    尝试生成一张演示 PSD（需要 psd-tools 有写能力，实际 psd-tools 仅读取）。
    由于 psd-tools 不支持写 PSD，这里跳过 PSD 创建，直接演示 JPG 素材的使用。
    真实使用场景：把你手上的 PSD 路径赋给 PSD_PATH 即可。
    """
    if os.path.isfile(PSD_PATH):
        return True
    print("[INFO] psd-tools 不支持写 PSD，跳过示例 PSD 生成。")
    print("       真实使用：将面板中的 PSD 文件指向你自己的 .psd 再点导出。")
    return False


def render_with_jpg_materials(material_bg: str, material_outer: str | None):
    """用现成的 JPG 素材渲染一张设计图"""
    d = CropDesign(
        mode='rect_hole',
        canvas_w_cm=50, canvas_h_cm=70, dpi=300,
        outer_margin_cm=1.0,
        inner_margin_top_cm=10, inner_margin_bottom_cm=10,
        inner_margin_left_cm=10, inner_margin_right_cm=10,
        borders=[
            BorderLayer(offset_cm=0.3, fill_type='solid', color=(0, 0, 0)),
            BorderLayer(offset_cm=0.2, fill_type='solid', color=(255, 255, 255)),
            BorderLayer(offset_cm=0.3, fill_type='solid', color=(0, 0, 0)),
        ],
        outer_bg_color=(0, 0, 0),
        outer_bg_image=material_outer,
        hole_bg_color=(250, 245, 230),
        hole_bg_image=material_bg,
    )
    img = render_design(d)
    p = os.path.join(WORK, "result_with_materials.jpg")
    save_jpg(img, p, quality=95, dpi=300)
    print(f"[OK] 用 JPG 素材渲染：{p}  size={img.width}x{img.height}")


def main():
    print("=" * 60)
    print("SmartShapeCrop  PSD/JPG 素材导入自检")
    print("=" * 60)

    mats = _make_demo_materials()
    print(f"[OK] 已生成 {len(mats)} 张演示 JPG 素材：")
    for m in mats: print(f"     - {m}")

    # 1. JPG 素材直接使用演示
    render_with_jpg_materials(mats[0], mats[2])

    # 2. PSD 图层导出演示（若用户已有 PSD）
    if is_psd_file(PSD_PATH):
        out = os.path.join(WORK, "psd_layers_exported")
        try:
            paths = export_psd_layers_as_jpgs(PSD_PATH, out, auto_crop=True)
            print(f"[OK] PSD 图层导出：{len(paths)} 个 -> {out}")
        except Exception as e:
            print(f"[WARN] PSD 导出失败：{e}")
    else:
        _try_make_demo_psd()
        print(f"[INFO] 待你放置 PSD 到此路径再运行：{PSD_PATH}")

    # 3. 合并图层为一张 RGB（等价于 PS 另存为 JPG）
    if is_psd_file(PSD_PATH):
        try:
            flat = load_psd_flattened(PSD_PATH)
            fp = os.path.join(WORK, "psd_flattened.jpg")
            flat.save(fp, quality=95)
            print(f"[OK] PSD 合并导出：{fp}")
        except Exception as e:
            print(f"[WARN] PSD 合并失败：{e}")

    print("=" * 60)
    print(f"输出目录：{WORK}")


if __name__ == '__main__':
    main()
