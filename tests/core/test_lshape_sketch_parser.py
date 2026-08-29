"""L 形草图解析器单元测试。

不依赖用户真实草图：用 PIL 合成一张标准 L 形挖角草图（含 A/B/C/D/E/F 标注），
验证几何检测 + OCR 标签归属 + 结构求解全链路。
"""

import os
import sys
import tempfile

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

# 让测试能 import 项目根目录的包
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.pool_designer.lshape_sketch_parser import parse_lshape_sketch, _detect_lshape_geometry


def _load_font(size):
    """加载一个可用字体（优先系统字体，失败则用默认）。"""
    candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for c in candidates:
        if os.path.isfile(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _make_lshape_sketch(out_path, *, corner='tr',
                        A=33.0, B=450.0, C=31.0, D=2.0, E=100.0, F=350.0,
                        s=4.0, stroke=6, margin=60):
    """合成一张 L 形草图 PNG。

    A=左高(总高) B=底宽(总宽) F=顶长段 E=缺口宽 C=右段高 D=缺口高。
    约定：B == F + E, A == C + D。
    """
    assert abs((F + E) - B) < 1e-6, "B 必须等于 F+E"
    assert abs((C + D) - A) < 1e-6, "A 必须等于 C+D"

    W = int((B * s) + 2 * margin)
    H = int((A * s) + 2 * margin)
    img = Image.new('RGB', (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)

    # L 形 6 顶点（图像坐标，y 向下）。先按 corner 计算凹角位置。
    # 基础：origin 在 (margin, margin)，整体 B×A（cm）。
    ox, oy = margin, margin
    wpx, hpx = int(B * s), int(A * s)

    # 凹角所在角：从 corner 决定缺口（notch）长方形的位置
    # notch: 宽 E*px, 高 D*px，位于 corner 指示的那个角
    en = int(E * s)
    dn = int(D * s)
    if corner == 'tr':
        # 缺口在右上：顶点顺序 (左上)(凹上)(凹右)(右下)(左下)
        verts = [
            (ox, oy),
            (ox + wpx - en, oy),
            (ox + wpx - en, oy + dn),
            (ox + wpx, oy + dn),
            (ox + wpx, oy + hpx),
            (ox, oy + hpx),
        ]
    elif corner == 'br':
        verts = [
            (ox, oy),
            (ox + wpx, oy),
            (ox + wpx, oy + hpx - dn),
            (ox + wpx - en, oy + hpx - dn),
            (ox + wpx - en, oy + hpx),
            (ox, oy + hpx),
        ]
    elif corner == 'tl':
        verts = [
            (ox + en, oy),
            (ox + wpx, oy),
            (ox + wpx, oy + hpx),
            (ox, oy + hpx),
            (ox, oy + dn),
            (ox + en, oy + dn),
        ]
    else:  # bl
        verts = [
            (ox, oy),
            (ox + wpx, oy),
            (ox + wpx, oy + hpx),
            (ox + en, oy + hpx),
            (ox + en, oy + hpx - dn),
            (ox, oy + hpx - dn),
        ]

    # 画轮廓（白底黑线）
    d.line(verts + [verts[0]], fill=(0, 0, 0), width=stroke, joint='curve')

    # 标注：每个尺寸放在其对应的边上，且跟随 corner 移动
    # 约定（与解析器几何归属一致）：
    #   A=满高边(与缺口不相邻的垂直边)  B=满宽边(与缺口不相邻的水平边)
    #   F=缺口所在水平边的长段  E=缺口宽  C=缺口所在垂直边的长段  D=缺口深
    font = _load_font(max(18, int(7 * max(1, s / 2))))
    labels = []  # (side, text, x_or_y)
    # A：缺口在右(tr/br) → 满高在左，缺口在左(tl/bl) → 满高在右
    a_side = 'left' if corner in ('tr', 'br') else 'right'
    labels.append((a_side, f"A {A:.0f}", oy + hpx * 0.5))
    # B：缺口在上(tr/tl) → 满宽在下，缺口在下(br/bl) → 满宽在上
    b_side = 'bottom' if corner in ('tr', 'tl') else 'top'
    labels.append((b_side, f"B {B:.0f}", ox + wpx * 0.5))
    # F/E：缺口所在水平边；缺口在右(tr/br) → F 在左段；缺口在左(tl/bl) → F 在右段
    if corner in ('tr', 'br'):
        f_x, e_x = ox + (wpx - en) * 0.5, ox + wpx - en * 0.5
    else:
        f_x, e_x = ox + en + (wpx - en) * 0.5, ox + en * 0.5
    fe_side = 'top' if corner in ('tr', 'tl') else 'bottom'
    labels.append((fe_side, f"F {F:.0f}", f_x))
    labels.append((fe_side, f"E {E:.0f}", e_x))
    # C/D：缺口所在垂直边；缺口在上(tr/tl) → C 在下段；缺口在下(br/bl) → C 在上段
    if corner in ('tr', 'tl'):
        c_y, d_y = oy + (dn + hpx) * 0.5, oy + dn * 0.5
    else:
        c_y, d_y = oy + (hpx - dn) * 0.5, oy + hpx - dn * 0.5
    cd_side = 'right' if corner in ('tr', 'br') else 'left'
    labels.append((cd_side, f"C {C:.0f}", c_y))
    labels.append((cd_side, f"D {D:.0f}", d_y))

    for side, text, pos in labels:
        if side == 'left':
            d.text((ox - margin * 0.65, pos), text, fill=(0, 0, 0), font=font)
        elif side == 'right':
            d.text((ox + wpx + margin * 0.1, pos), text, fill=(0, 0, 0), font=font)
        elif side == 'top':
            d.text((pos, oy - margin * 0.7), text, fill=(0, 0, 0), font=font)
        else:  # bottom
            d.text((pos, oy + hpx + margin * 0.15), text, fill=(0, 0, 0), font=font)

    img.save(out_path, 'PNG')
    return out_path


def test_geometry_tr_detects_lshape():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'l_tr.png')
        _make_lshape_sketch(p, corner='tr')
        import cv2
        from core.pool_designer.sketch_parser_vision import _load_image, _to_gray
        img, _ = _load_image(p)
        gray = _to_gray(img)
        geo = _detect_lshape_geometry(cv2, gray)
        assert geo is not None, "应检测到 L 形几何"
        assert geo['corner'] == 'tr', f"凹角应为 tr，实际 {geo['corner']}"
        # 像素比例：cut_w / outer_w ≈ E / B = 100/450
        r_w = geo['cut_w_px'] / geo['outer_w_px']
        r_h = geo['cut_h_px'] / geo['outer_h_px']
        assert abs(r_w - 100 / 450) < 0.03, f"cut_w 比例偏差过大: {r_w}"
        assert abs(r_h - 2 / 33) < 0.05, f"cut_h 比例偏差过大: {r_h}"


def test_parse_lshape_tr_full():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'l_tr.png')
        _make_lshape_sketch(p, corner='tr', A=33, B=450, C=31, D=2, E=100, F=350)
        res = parse_lshape_sketch(p)
        assert res.success, f"解析应成功: {res.message}"
        assert res.corner == 'tr'
        assert abs(res.outer_w_cm - 450) < 8, f"外宽偏差: {res.outer_w_cm}"
        assert abs(res.outer_h_cm - 33) < 3, f"外高偏差: {res.outer_h_cm}"
        assert abs(res.cut_w_cm - 100) < 6, f"挖宽偏差: {res.cut_w_cm}"
        assert abs(res.cut_h_cm - 2) < 2, f"挖高偏差: {res.cut_h_cm}"
        assert res.self_consistency >= 0.5


@pytest.mark.parametrize("corner", ['tr', 'tl', 'br', 'bl'])
def test_parse_all_corners(corner):
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, f'l_{corner}.png')
        # 用一致的尺寸，仅旋转缺口角
        _make_lshape_sketch(p, corner=corner, A=33, B=450, C=31, D=2, E=100, F=350)
        res = parse_lshape_sketch(p)
        assert res.success, f"[{corner}] 解析应成功: {res.message}"
        assert res.corner == corner, f"[{corner}] 凹角判定错误: {res.corner}"
        assert abs(res.outer_w_cm - 450) < 12
        assert abs(res.outer_h_cm - 33) < 6


def test_not_lshape_returns_failure():
    """一张纯矩形草图应被判定为非 L 形（不误判）。"""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'rect.png')
        _make_lshape_sketch(p, corner='tr')
        # 改画成完整矩形（无缺口）
        img = Image.new('RGB', (900, 120), (255, 255, 255))
        d = ImageDraw.Draw(img)
        d.rectangle([40, 30, 40 + 800, 30 + 60], outline=(0, 0, 0), width=6)
        d.text((420, 10), "B 450", fill=(0, 0, 0))
        img.save(p)
        res = parse_lshape_sketch(p)
        # 矩形没有凹角 → 解析失败（非 L 形）
        assert not res.success


if __name__ == '__main__':
    test_geometry_tr_detects_lshape()
    test_parse_lshape_tr_full()
    test_parse_all_corners('tr')
    test_parse_all_corners('tl')
    test_parse_all_corners('br')
    test_parse_all_corners('bl')
    test_not_lshape_returns_failure()
    print("ALL L-SHAPE TESTS PASSED")
