# -*- coding: utf-8 -*-
"""复现单边阶梯 L 形挖角的画布边缘衔接缺陷（用户 2026-09-22 反馈 图1/图2）。

用户场景（GUI 单边阶梯 L 形，corner=tr）：
  - 画布 189.5x75cm @150DPI（外框 188.5x74 + 1cm 损耗）
  - 阶梯输入：第1级步进 15x10cm，第2级 20x10cm
    → CutRect 条带（gui/lshape_panel.get_cut_rects_cm 换算）:
       {tr, offset=(0,0),  w=35, h=10} + {tr, offset=(0,10), w=20, h=10}
  - 素材 60x152cm 竖版（黑描边 + 米色带 + 细线结构），渲染时 ROTATE_270
    后拉伸铺满画布 → 素材自带边框沿画布四边分布

缺陷（修复前）：
  Bug1（图1 红X/红箭头）：沿阶梯切边补画的内层（色带/细线）延伸到画布边缘，
    压过素材自身的外层边框 → 多余线条"连到画布最外层边框"。
  Bug2（图2）：内层色带覆盖画布边缘的黑描边 → 最外层边框线条不完整（缺口）。

期望（图3，修复后）：两个画布边缘衔接点均呈"嵌套直角"——
  描边连描边、色带连色带、细线连细线；内层不越过外层。

验证点（对 preview 与 export 两个渲染都检查）：
  T1 顶边衔接（V1 竖切边 × 画布顶边）描边无缺口（Bug2）
  T2 顶边衔接 色带内无多余深色竖线（Bug1）
  T3 顶边衔接 描边竖臂连续（回归保护：最外层不应被裁剪）
  R1 右边衔接（H2 横切边 × 画布右边）描边无缺口（Bug2）
  R2 右边衔接 色带内无多余深色竖线（Bug1）
  R3 右边衔接 描边横臂连续（回归保护）
  S1 阶梯切边轮廓边框存在（补全确实生效）
  S2 挖角区为纯白（cut 填充正确）

用法：
  python scripts/diagnose/_diag_staircase_border_edges.py
  退出码 0 = 全部通过；1 = 存在缺陷/失败。
"""
import os
import sys
import time
import pathlib
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import numpy as np
from PIL import Image

from core.geometry import CropDesign, CutRect

CM_PER_INCH = 2.54

# ---- 合成素材参数（60x152cm 竖版 @100DPI）----
MAT_W_CM, MAT_H_CM = 60.0, 152.0
MAT_DPI = 100
T_STROKE, T_BAND, T_LINE = 25, 50, 8          # 源图像素
C_BLACK = (25, 25, 25)
C_CREAM = (232, 224, 204)
C_LINE = (45, 45, 45)
C_BG = (118, 128, 105)                        # 内容底色（与米色带差异 > 30，防大理石误判）


def make_material(path: str) -> None:
    """黑描边 + 米色带 + 细线 + 低对比内容的合成素材（竖版）。"""
    w = round(MAT_W_CM / CM_PER_INCH * MAT_DPI)
    h = round(MAT_H_CM / CM_PER_INCH * MAT_DPI)
    print(f"  [合成素材] {w}x{h} px ({MAT_W_CM}x{MAT_H_CM}cm @{MAT_DPI}DPI 竖版)")

    arr = np.full((h, w, 3), C_BG, dtype=np.uint8)
    # 低对比内容花纹（均值保持在 90~180 之间，避免干扰边框层测量）
    stripe_p = 40
    cols = np.arange(w)
    arr[:, (cols % stripe_p) < 6, :] = (140, 150, 125)
    rows = np.arange(h)
    arr[(rows % 55) < 3, :, :] = (100, 110, 90)

    # 四边同构边框：描边 → 色带 → 细线
    arr[:T_STROKE, :, :] = C_BLACK
    arr[-T_STROKE:, :, :] = C_BLACK
    arr[:, :T_STROKE, :] = C_BLACK
    arr[:, -T_STROKE:, :] = C_BLACK
    b0, b1 = T_STROKE, T_STROKE + T_BAND
    arr[b0:b1, :, :] = C_CREAM
    arr[-b1:-b0, :, :] = C_CREAM
    arr[:, b0:b1, :] = C_CREAM
    arr[:, -b1:-b0, :] = C_CREAM
    l0, l1 = b1, b1 + T_LINE
    arr[l0:l1, :, :] = C_LINE
    arr[-l1:-l0, :, :] = C_LINE
    arr[:, l0:l1, :] = C_LINE
    arr[:, -l1:-l0, :] = C_LINE

    Image.fromarray(arr, mode='RGB').save(path)


def build_design(mat_path: str) -> CropDesign:
    """复刻 workers/property_panel_workers._apply_lshape_params 的阶梯设计。"""
    return CropDesign(
        canvas_w_cm=189.5, canvas_h_cm=75.0, dpi=150,
        mode='rect_lshape',
        outer_margin_cm=0.0,
        inner_margin_top_cm=0.0,
        inner_margin_bottom_cm=0.0,
        inner_margin_left_cm=0.0,
        inner_margin_right_cm=0.0,
        l_corner='tr',
        l_cut_w_cm=35.0,        # = strip[0].w
        l_cut_h_cm=20.0,        # = sum(strip.h)
        l_cut_rects=[
            CutRect(anchor='tr', offset_x_cm=0.0, offset_y_cm=0.0, w_cm=35.0, h_cm=10.0),
            CutRect(anchor='tr', offset_x_cm=0.0, offset_y_cm=10.0, w_cm=20.0, h_cm=10.0),
        ],
        pool_hole_transparent=True,
        pool_outer_material_image=mat_path,
        pool_inner_material_image=mat_path,
        outer_bg_image=mat_path,
    )


def measure_border_runs(arr: np.ndarray, x_col: int, y_row: int) -> tuple[int, int, int]:
    """从渲染结果测画布边框层厚度：(t0描边, t1色带, t2细线)。

    顶边：x_col 列从 y=0 向下；右边：y_row 行从 x=W-1 向左。
    """
    col = arr[:800, x_col, :].astype(np.int32).mean(axis=1)
    row = arr[y_row, -800:, :].astype(np.int32).mean(axis=1)[::-1]

    def runs(profile):
        # 允许跳过最多 3 个重采样过渡像素（半暗半亮），否则 t1/t2 会因
        # 恰好落在过渡行上而测得 0。
        def run_len(pred, start):
            i = start
            skip = 0
            while i < len(profile) and skip < 3 and not pred(profile[i]):
                i += 1
                skip += 1
            j = i
            while j < len(profile) and pred(profile[j]):
                j += 1
            return j - i, j
        dark = lambda v: v < 90
        light = lambda v: v > 180
        t0, i0 = run_len(dark, 0)
        t1, i1 = run_len(light, i0)
        t2, i2 = run_len(dark, i1)
        return t0, t1, t2

    top_runs = runs(col)
    right_runs = runs(row)
    return top_runs, right_runs


def region_counts(arr: np.ndarray, x0: int, x1: int, y0: int, y1: int) -> tuple[int, int]:
    """区域内 (暗像素数, 亮像素数)。暗=均值<90，亮=均值>180。"""
    if x1 <= x0 or y1 <= y0:
        return (0, 0)
    region = arr[y0:y1, x0:x1, :].astype(np.int32).mean(axis=2)
    dark = int((region < 90).sum())
    light = int((region > 180).sum())
    return dark, light


def check_render(arr: np.ndarray, W: int, H: int, V1: int, H2: int,
                 t0: int, t1: int, t2: int, label: str) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    total = t0 + t1 + t2

    # ---- 顶边衔接（V1 竖切边 × 画布顶边）----
    # Bug2 的错误色带颜色是 bg_color（合成素材下≈(140,150,125)，非亮非暗），
    # "无亮像素"检测不到，必须断言该区全黑（素材描边）。
    d, l = region_counts(arr, V1 - t0 - t1 + 8, V1 - t0 - 8, 3, max(4, t0 - 3))
    t1_px = max(0, (V1 - t0 - 8) - (V1 - t0 - t1 + 8)) * max(0, max(4, t0 - 3) - 3)
    results.append((f"{label}/T1 顶边衔接描边无缺口",
                    d == t1_px and t1_px > 0,
                    f"描边落点区暗像素={d}/{t1_px}（应全暗；不足即 Bug2 缺口）"))
    d, l = region_counts(arr, V1 - total + 3, V1 - t0 - t1 - 3,
                         t0 + 4, t0 + t1 - 4)
    results.append((f"{label}/T2 顶边衔接色带无多余线",
                    d == 0, f"细线落点区暗像素={d}（应为0；>0 即 Bug1 多余线）"))
    d, l = region_counts(arr, V1 - t0 + 5, V1 - 5, t0 + 4, t0 + t1 + t2 + 10)
    n_px = max(0, V1 - 5 - (V1 - t0 + 5)) * max(0, t0 + t1 + t2 + 10 - (t0 + 4))
    results.append((f"{label}/T3 顶边衔接描边竖臂连续",
                    d == n_px and n_px > 0,
                    f"描边臂暗像素={d}/{n_px}（应全暗；否则最外层被误裁）"))

    # ---- 右边衔接（H2 横切边 × 画布右边）----
    d, l = region_counts(arr, W - t0 + 8, W - 8, H2 + t0 + 8, H2 + t0 + t1 - 8)
    r1_px = max(0, (W - 8) - (W - t0 + 8)) * max(0, (H2 + t0 + t1 - 8) - (H2 + t0 + 8))
    results.append((f"{label}/R1 右边衔接描边无缺口",
                    d == r1_px and r1_px > 0,
                    f"描边落点区暗像素={d}/{r1_px}（应全暗；不足即 Bug2 缺口）"))
    d, l = region_counts(arr, W - total + 3, W - t0 - t1 - 3,
                         H2 + t0 + 4, H2 + t0 + t1 - 4)
    results.append((f"{label}/R2 右边衔接色带无多余线",
                    d == 0, f"细线落点区暗像素={d}（应为0；>0 即 Bug1 多多余线）"))
    d, l = region_counts(arr, W - t0 - t1 + 8, W - t0 - 8, H2 + 4, H2 + t0 - 4)
    n_px = max(0, W - t0 - 8 - (W - t0 - t1 + 8)) * max(0, t0 - 4 - 4)
    results.append((f"{label}/R3 右边衔接描边横臂连续",
                    d == n_px and n_px > 0,
                    f"描边臂暗像素={d}/{n_px}（应全暗；否则最外层被误裁）"))

    # ---- 结构 sanity ----
    d, l = region_counts(arr, V1 - t0 + 5, V1 - 5, 250, 350)
    results.append((f"{label}/S1 阶梯切边轮廓边框存在", d > 0,
                    f"切边中部描边臂暗像素={d}（应>0；=0 说明补全未生效）"))
    d, l = region_counts(arr, V1 + 80, V1 + 200, 80, 200)
    n_px = 120 * 120
    results.append((f"{label}/S2 挖角区纯白", l == n_px,
                    f"挖角区亮像素={l}/{n_px}（应全亮）"))
    return results


def main() -> int:
    outdir = pathlib.Path(tempfile.gettempdir()) / 'ssc_staircase_edge_diag'
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"输出目录: {outdir}\n")

    mat_path = str(outdir / 'material_staircase.png')
    make_material(mat_path)

    design = build_design(mat_path)
    W, H = design.canvas_w_px, design.canvas_h_px
    lshape = design.l_shapes_px()
    ir = design.inner_rect_px()
    _ir_y = int(round(ir.y))
    _ir_r = int(round(ir.right))
    specs = lshape.cut_rect_specs()
    V1 = _ir_r - int(round(specs[0]['cut_w'] + specs[0]['offset_x']))
    H2 = _ir_y + int(round(specs[1]['cut_h'] + specs[1]['offset_y']))
    print(f"\n画布: {W}x{H} px；V1(第1级竖切边) x={V1}；H2(第2级横切边) y={H2}")

    # 边框检测路由（与渲染内部分析同一张源图）
    from core.lshape_border import detect_border_v13
    from core.lshape_border_route import detect_border_profile, profile_yields_to_v13
    mat_img = Image.open(mat_path)
    v13 = detect_border_v13(mat_img)
    prof = detect_border_profile(mat_img)
    print(f"detect_border_v13    = {v13}")
    print(f"detect_border_profile= {prof}")
    if prof:
        print(f"profile_yields_to_v13= {profile_yields_to_v13(prof)}")
    del mat_img

    from core.image_ops import render_design
    all_ok = True
    for quality in ('preview', 'export'):
        t0 = time.perf_counter()
        img = render_design(design, quality=quality)
        dt = (time.perf_counter() - t0) * 1000
        arr = np.asarray(img)
        print(f"\n─── render_design(quality={quality!r}) {arr.shape[1]}x{arr.shape[0]}  {dt:.0f} ms ───")
        img.save(outdir / f"full_{quality}.jpg", quality=88)

        (top_runs, right_runs) = measure_border_runs(arr, W // 2, H // 2)
        print(f"顶边层厚(描边/色带/细线) = {top_runs}；右边层厚 = {right_runs}")
        t0p, t1p, t2p = top_runs
        if min(top_runs) <= 0 or min(right_runs) <= 0:
            print("!! 层厚测量失败（某层为0），检查渲染输出")
            all_ok = False
            continue

        results = check_render(arr, W, H, V1, H2, t0p, t1p, t2p, quality)
        for name, ok, detail in results:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
            all_ok = all_ok and ok

        # 保存衔接区放大图（人工核对）
        top_crop = Image.fromarray(arr[0:400, max(0, V1 - 360):V1 + 60])
        top_crop.save(outdir / f"top_junction_{quality}.png")
        right_crop = Image.fromarray(arr[max(0, H2 - 60):H2 + 420, W - 420:W])
        right_crop.save(outdir / f"right_junction_{quality}.png")
        del img, arr

    print("\n" + ("=" * 60))
    print("结论: " + ("全部通过（无 Bug1/Bug2 缺陷）" if all_ok else "存在缺陷（见上方 FAIL 项）"))
    print("=" * 60)
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
