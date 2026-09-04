"""V13 集成基线脚本 —— 渲染合成带框素材的 L 形挖角，用于变更前后像素级 diff。

复用 test_lshape_border.py 的 _make_bordered_material 模式：合成"棕外框 + 黑内框 + 米色中心"素材，
模拟克罗印花一类带双层边框的素材，触发 apply_lshape_border_completion 的边框补全路径。

用法：
    python scripts/_v13_baseline_render.py            # 渲染并保存基线 JPG
    python scripts/_v13_baseline_render.py --diff X.jpg  # 与指定 JPG 像素 diff

无 manual override（design.lshape_manual_* 全 None）→ 走原有 detect_pool_material_borders 路径，
向后兼容性即"override=None 时输出与基线完全一致"。
"""
from __future__ import annotations
import argparse
import os
import sys
import tempfile

# 让脚本能从项目根目录导入 core
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import numpy as np
from PIL import Image, ImageDraw

from core.geometry import CropDesign
from core.image_ops import render_design


def _make_two_layer_bordered_material(
    size=(400, 300),
    outer_border=20,
    outer_color=(120, 76, 47),     # 棕色外框（模拟克罗印花主带）
    inner_border=4,
    inner_color=(0, 0, 0),         # 黑色内描边
    fill=(245, 235, 220),          # 米色中心
) -> Image.Image:
    """合成"棕外框 + 黑内描边 + 米色中心"双层边框素材。"""
    img = Image.new('RGB', size, fill)
    d = ImageDraw.Draw(img)
    # 外框：四边绘 outer_border 像素棕色
    d.rectangle([0, 0, size[0] - 1, size[1] - 1],
                outline=outer_color, width=outer_border)
    # 内描边：在外框内缘再绘一条 inner_border 像素黑线
    inner_rect = [outer_border, outer_border,
                  size[0] - 1 - outer_border, size[1] - 1 - outer_border]
    d.rectangle(inner_rect, outline=inner_color, width=inner_border)
    return img


def build_design(material_path: str, manual_edge=None, manual_band=None,
                 manual_band_color=None) -> CropDesign:
    """构建 L 形挖角 CropDesign。
    manual_* 默认 None → 走原有自动检测路径。
    显式传入 → 走 V13 手动覆盖路径。
    """
    design = CropDesign(
        canvas_w_cm=30.0, canvas_h_cm=20.0, dpi=150,
        mode='rect_lshape',
        outer_margin_cm=0.0,
        inner_margin_top_cm=0.0, inner_margin_bottom_cm=0.0,
        inner_margin_left_cm=0.0, inner_margin_right_cm=0.0,
        l_corner='tr', l_cut_w_cm=8.0, l_cut_h_cm=6.0,
        hole_bg_color=(255, 255, 255),
        outer_bg_color=(0, 0, 0),
        pool_hole_transparent=True,
        pool_outer_material_image=material_path,
    )
    design.outer_bg_image = material_path
    design.pool_inner_material_image = material_path
    # 手动覆盖字段（集成后会在 geometry.py 中存在；基线运行时无此属性也没关系，
    # setattr 安全赋值，render_design 不会读不存在的字段）
    if manual_edge is not None:
        setattr(design, 'lshape_manual_edge_px', manual_edge)
    if manual_band is not None:
        setattr(design, 'lshape_manual_band_px', manual_band)
    if manual_band_color is not None:
        setattr(design, 'lshape_manual_band_color', manual_band_color)
    return design


def render_to_array(design: CropDesign) -> np.ndarray:
    """render_design → numpy 数组（uint8, H×W×3）。"""
    img = render_design(design, quality='export')
    return np.array(img)


def save_baseline(out_path: str) -> None:
    """渲染基线（无 manual override）并保存为 PNG（无损，确保 diff 只反映渲染差异）。"""
    # 合成素材到临时文件
    tmp_dir = tempfile.mkdtemp(prefix='v13_baseline_')
    material_path = os.path.join(tmp_dir, '_bordered_material.png')
    mat = _make_two_layer_bordered_material()
    mat.save(material_path)

    design = build_design(material_path)  # manual_* 全 None
    arr = render_to_array(design)
    # 无损 PNG：避免 JPG 压缩伪影污染 pixel-diff（JPG q=95 会让 ~77% 像素产生 ≤26 的误差）
    Image.fromarray(arr).save(os.path.splitext(out_path)[0] + '.png')
    print(f'[baseline] saved: {os.path.splitext(out_path)[0] + ".png"}  shape={arr.shape}')

    # 清理临时素材
    try:
        os.remove(material_path)
        os.rmdir(tmp_dir)
    except OSError:
        pass


def diff_against(baseline_path: str, manual_edge=None, manual_band=None,
                 manual_band_color=None) -> dict:
    """重新渲染（可选手动覆盖）并与基线 JPG diff。返回 diff 统计。"""
    # 重新合成同一素材（确定性：相同 size + 相同 border 参数）
    tmp_dir = tempfile.mkdtemp(prefix='v13_diff_')
    material_path = os.path.join(tmp_dir, '_bordered_material.png')
    mat = _make_two_layer_bordered_material()
    mat.save(material_path)

    design = build_design(material_path, manual_edge, manual_band, manual_band_color)
    arr = render_to_array(design)

    # 若传入路径无 .png 扩展名，自动尝试 .png 版本（兼容旧 JPG 调用）
    if not os.path.exists(baseline_path) and not baseline_path.lower().endswith('.png'):
        png_path = os.path.splitext(baseline_path)[0] + '.png'
        if os.path.exists(png_path):
            baseline_path = png_path
    base = np.array(Image.open(baseline_path).convert('RGB'))
    if base.shape != arr.shape:
        return {'error': f'shape mismatch: base={base.shape} vs new={arr.shape}'}
    diff = np.abs(base.astype(np.int16) - arr.astype(np.int16))
    diff_mask = diff.sum(axis=2) > 0
    return {
        'shape': arr.shape,
        'max_diff': int(diff.max()),
        'mean_diff': float(diff.mean()),
        'num_diff_pixels': int(diff_mask.sum()),
        'total_pixels': int(arr.shape[0] * arr.shape[1]),
        'pct_diff': float(diff_mask.mean() * 100.0),
    }


def main():
    ap = argparse.ArgumentParser(description='V13 集成基线 + diff 工具')
    ap.add_argument('--out', default='scripts/_v13_baseline.png',
                    help='基线 PNG 输出路径（无损）')
    ap.add_argument('--diff', default=None,
                    help='与指定基线 JPG 做 pixel diff（不指定则只生成基线）')
    ap.add_argument('--manual-edge', type=int, default=None)
    ap.add_argument('--manual-band', type=int, default=None)
    ap.add_argument('--manual-band-color', default=None,
                    help='r,g,b 三元组，如 120,76,47')
    args = ap.parse_args()

    if args.diff:
        mbc = None
        if args.manual_band_color:
            mbc = tuple(int(x) for x in args.manual_band_color.split(','))
        r = diff_against(args.diff, args.manual_edge, args.manual_band, mbc)
        print('[diff]', r)
    else:
        save_baseline(args.out)


if __name__ == '__main__':
    main()
