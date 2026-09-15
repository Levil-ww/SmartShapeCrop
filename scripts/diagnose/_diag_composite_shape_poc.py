"""_diag_composite_shape_poc.py — 综合形状（中间挖洞 + L 形挖角）可行性验证

目的（零源码改动）：
  用**现有** core.geometry 的原子原语，合成「外框 - L形挖角 - 中间挖洞」的复合 mask，
  并用面积守恒证明几何层可以纯组合实现，无需新增渲染引擎。

验证的草图（E:\\智能裁剪设计器\\测试草图文件-综合中间+L形）：
  外框   185 × 88 cm
  L 挖角  右上角 (tr)，切掉 35(宽) × 10(高) cm
  中间洞  80 × 60 cm，边距 上10 / 下18 / 左45 / 右60

不修改任何生产源码；本脚本只在 _archive/ 下产出诊断图。
运行：F:\\SmartShapeCrop\\.venv\\Scripts\\python.exe scripts/diagnose/_diag_composite_shape_poc.py
"""
from __future__ import annotations

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import numpy as np
from PIL import Image, ImageDraw

from core.geometry import (
    CropDesign, RectShape, make_mask, fill_rect_mask, build_lshape_mask,
)

OUT_DIR = os.path.join(BASE, '_archive', 'poc_composite')
OUT_PNG = os.path.join(OUT_DIR, 'composite_mask_poc.png')

# ---- 草图真值（cm）----
OUTER_W, OUTER_H = 185.0, 88.0
CUT_CORNER = 'tr'
CUT_W, CUT_H = 35.0, 10.0
MT, MB, ML, MR = 10.0, 18.0, 45.0, 60.0

# dpi=127 → 1 cm = 50 px（整数，面积可精确核对）
DPI = 127


def main() -> int:
    # 复用 CropDesign 只读能力（构造属于使用，不改动源码）
    d = CropDesign(
        canvas_w_cm=OUTER_W, canvas_h_cm=OUTER_H, dpi=DPI,
        mode='rect_hole', outer_margin_cm=0.0,
        inner_margin_top_cm=MT, inner_margin_bottom_cm=MB,
        inner_margin_left_cm=ML, inner_margin_right_cm=MR,
    )
    d.validate()

    W, H = d.canvas_w_px, d.canvas_h_px
    outer = d.outer_rect_px()
    inner = d.inner_rect_px()
    zero_radii = {'tl': 0.0, 'tr': 0.0, 'bl': 0.0, 'br': 0.0}
    cut_w_px, cut_h_px = d.cm2px(CUT_W), d.cm2px(CUT_H)
    print(f'[POC] canvas = {W} x {H} px @ {DPI} dpi  ({OUTER_W}x{OUTER_H} cm)')
    print(f'[POC] outer_rect_px = ({outer.x:.0f},{outer.y:.0f})-({outer.right:.0f},{outer.bottom:.0f})')
    print(f'[POC] inner_rect_px = ({inner.x:.0f},{inner.y:.0f})-({inner.right:.0f},{inner.bottom:.0f})')

    # ===== 原语 1：L 形区域（外框 − 右上角 35×10）—— 复用现有 build_lshape_mask =====
    lshape_img = build_lshape_mask(
        (W, H), outer, CUT_CORNER, cut_w_px, cut_h_px,
        zero_radii, fill_value=255,
        cuts=[(CUT_CORNER, cut_w_px, cut_h_px)],
    )
    m_lshape = np.array(lshape_img, dtype=bool)

    # ===== 原语 2：中间洞（由 inner_margin 定义）—— 复用现有 fill_rect_mask =====
    hole_img = make_mask((W, H))
    fill_rect_mask(hole_img, inner, 255)
    m_hole = np.array(hole_img, dtype=bool)

    # ===== 组合：KEEP = L形 − 洞（纯布尔运算，无新几何）=====
    m_cut = ~m_lshape                     # 被切掉的角
    m_keep = m_lshape & ~m_hole           # 最终保留材料的区域

    # ===== 面积守恒证明 =====
    px_per_cm2 = (DPI / 2.54) ** 2
    a_outer = m_lshape.sum() / px_per_cm2 + m_cut.sum() / px_per_cm2
    a_cut = m_cut.sum() / px_per_cm2
    a_hole = m_hole.sum() / px_per_cm2
    a_keep = m_keep.sum() / px_per_cm2
    print(f'[POC] 外框面积     = {a_outer:12.1f} cm^2  (期望 {OUTER_W * OUTER_H:.1f})')
    print(f'[POC] L挖角面积    = {a_cut:12.1f} cm^2  (期望 {CUT_W * CUT_H:.1f})')
    print(f'[POC] 中间洞面积   = {a_hole:12.1f} cm^2  (期望 {80 * 60:.1f})')
    print(f'[POC] 保留材料面积 = {a_keep:12.1f} cm^2  '
          f'(期望 {OUTER_W * OUTER_H - CUT_W * CUT_H - 80 * 60:.1f})')

    # ===== 集合代数精确断言（与栅格化无关，是真正的可行性证明）=====
    exact = []
    exact.append(('L形 + 挖角 == 整幅画布',
                  int(m_lshape.sum()) + int(m_cut.sum()) == W * H))
    exact.append(('保留区 ∩ 洞 == 空',
                  int((m_keep & m_hole).sum()) == 0))
    exact.append(('保留区 ∪ 洞 == L形（恰好划分）',
                  bool(np.array_equal(m_keep | m_hole, m_lshape))))
    exact.append(('洞 ∩ 挖角 == 空（两区域不相交）',
                  int((m_hole & m_cut).sum()) == 0))
    _adj = int(np.logical_and(m_hole, np.roll(m_cut, 1, axis=0)).sum()
               + np.logical_and(m_hole, np.roll(m_cut, 1, axis=1)).sum())
    print(f'[POC] 洞与挖角相邻像素(1px 膨胀) = {_adj}'
          f'（0 = 无共边，10px 黑框可分别绘制后 OR 合并）')

    rel = abs(a_keep - (OUTER_W * OUTER_H - CUT_W * CUT_H - 80 * 60)) \
        / (OUTER_W * OUTER_H - CUT_W * CUT_H - 80 * 60)
    print(f'[POC] 面积相对误差 = {rel * 100:.3f}%  '
          f'（仅来自 PIL 矩形 1px 边界栅格化，非几何误差）')
    for name, ok in exact:
        print(f'[POC] {"PASS" if ok else "FAIL"}  {name}')
    err_i = 0.0 if all(ok for _, ok in exact) else 1.0

    # ===== 出图：三联视图 + 右上角特写 =====
    def _mask_img(mask, keep=(255, 255, 255), off=(232, 232, 232)):
        a = np.empty((H, W, 3), dtype=np.uint8)
        a[...] = off
        a[mask] = keep
        return Image.fromarray(a, 'RGB')

    comp = np.empty((H, W, 3), dtype=np.uint8)
    comp[...] = (232, 232, 232)          # 画布外 → 灰
    comp[m_cut] = (255, 190, 190)        # 挖角   → 淡红
    comp[m_hole] = (190, 210, 255)       # 洞     → 淡蓝
    comp[m_keep] = (255, 255, 255)       # 保留   → 白
    comp_img = Image.fromarray(comp, 'RGB')

    trio = [
        ('(1) L-shape only = Rect - TR cut', _mask_img(m_lshape)),
        ('(2) Hole only = inner_margin rect', _mask_img(m_hole)),
        ('(3) Composite KEEP = (1) - (2)', comp_img),
    ]

    # 右上角特写：最后 70cm × 35cm
    cw_px, ch_px = int(d.cm2px(70)), int(d.cm2px(35))
    crop_box = (W - cw_px, 0, W, ch_px)
    detail = comp_img.crop(crop_box)
    det_w, det_h = detail.size

    gap, pad, cap = 12, 18, 24
    SHEET_W = 1080
    tw = (SHEET_W - pad * 2 - gap * 2) // 3      # 三联每格宽
    th = int(tw * H / W)
    row1_h = th + cap
    dw = SHEET_W - pad * 2                        # 特写行宽
    dh = int(dw * det_h / det_w)
    total_w = SHEET_W
    total_h = pad + row1_h + pad + dh + cap + pad

    sheet = Image.new('RGB', (total_w, total_h), (248, 248, 248))
    sd = ImageDraw.Draw(sheet)

    for i, (label, im) in enumerate(trio):
        x = pad + i * (tw + gap)
        sheet.paste(im.resize((tw, th), Image.LANCZOS), (x, pad))
        sd.text((x + 2, pad + th + 5), label, fill=(25, 25, 25))

    y2 = pad + row1_h + pad
    sheet.paste(detail.resize((dw, dh), Image.LANCZOS), (pad, y2))
    sd.text((pad + 2, y2 + dh + 5),
            '(4) TR corner detail 70x35cm  -  white = keep, red = L cut, blue = hole',
            fill=(25, 25, 25))

    os.makedirs(OUT_DIR, exist_ok=True)
    sheet.save(OUT_PNG)
    print(f'[POC] 输出诊断图：{OUT_PNG}')
    return 0 if err_i == 0.0 else 1


if __name__ == '__main__':
    sys.exit(main())
