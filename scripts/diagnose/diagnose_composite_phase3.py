"""阶段3 真实草图校准（诊断脚本，不进 CI；正式测试见 tests/sketch_parser/test_composite_ocr_roles.py）。

生成带真实数字标注的综合形状草图（PIL 渲染 + 本机 Tesseract 实跑全链路），
校准口径：10 类角色（外框2 + 洞2 + 边距4 + 挖角2×N）数值误差 ≤ 0.5cm。
输出空间还原：result 的洞/外框含 +1cm 损耗，比较前减回；边距原样。

场景：
  1. to_scale        等比草图 + 标注真值 —— 几何/OCR 双通道都应收敛
  2. not_to_scale    草图不等比（洞画小 5%）+ 标注真值 —— OCR 必须赢过像素比例
  3. two_corners     tr+bl 双挖角 —— 挖角委派与十角色并存
  4. fill_missing    不等比 + 缺洞宽标注 —— ocr_fill 减法回填路径

用法：
    ./.venv/Scripts/python.exe scripts/diagnose/diagnose_composite_phase3.py
"""
from __future__ import annotations

import os
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.sketch_parser.composite_sketch_parser import parse_composite_sketch  # noqa: E402

OUTER = (200, 150, 925, 440)  # px，5px/cm → 185×88cm
TARGET_W_CM, TARGET_H_CM = 185.0, 88.0
TOL_CM = 0.5
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _font(size: int) -> ImageFont.FreeTypeFont:
    for cand in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/calibri.ttf"):
        if os.path.exists(cand):
            return ImageFont.truetype(cand, size)
    return ImageFont.load_default()


def _label(d, font, text, cx, cy):
    bbox = d.textbbox((0, 0), text, font=font)
    d.text((cx - (bbox[2] - bbox[0]) / 2 - bbox[0], cy - (bbox[3] - bbox[1]) / 2 - bbox[1]),
           text, fill="black", font=font)


def _notch(d, corner, cut_w_px, cut_h_px, width=3):
    """在闭合外框环带上开凿角凹口并补画两条内边，保持环带闭合。"""
    ox, oy, ow, oh = OUTER
    if corner == "tr":
        d.rectangle([ox + ow - cut_w_px, oy, ox + ow - 1, oy + cut_h_px - 1], fill="white")
        d.line([(ox + ow - cut_w_px, oy), (ox + ow - cut_w_px, oy + cut_h_px)], fill="black", width=width)
        d.line([(ox + ow - cut_w_px, oy + cut_h_px), (ox + ow - 1, oy + cut_h_px)], fill="black", width=width)
    elif corner == "bl":
        d.rectangle([ox, oy + oh - cut_h_px, ox + cut_w_px - 1, oy + oh - 1], fill="white")
        d.line([(ox, oy + oh - cut_h_px), (ox + cut_w_px, oy + oh - cut_h_px)], fill="black", width=width)
        d.line([(ox + cut_w_px, oy + oh - cut_h_px), (ox + cut_w_px, oy + oh - 1)], fill="black", width=width)
    else:  # pragma: no cover
        raise ValueError(corner)


def make_sketch(path, hole_px, margins_px, labels: dict, cuts_px: dict):
    """labels: {role: text}，role ∈ ml/mr/mt/mb/hw/hh/ow/oh；margins_px = (ml, mt, mr, mb)。"""
    ml_px, mt_px, mr_px, mb_px = margins_px
    ox, oy, ow, oh = OUTER
    hw_px, hh_px = hole_px
    hx = ox + ml_px
    hy = oy + mt_px
    img = Image.new("RGB", (1325, 730), "white")
    d = ImageDraw.Draw(img)
    font = _font(30)
    d.rectangle([ox, oy, ox + ow - 1, oy + oh - 1], outline="black", width=3)
    d.rectangle([hx, hy, hx + hw_px - 1, hy + hh_px - 1], outline="black", width=3)
    for corner, (cw, ch) in cuts_px.items():
        _notch(d, corner, cw, ch)
    hcx, hcy = hx + hw_px / 2, hy + hh_px / 2
    spots = {
        "ml": (ox + ml_px / 2, hcy), "mr": (hx + hw_px + mr_px / 2, hcy),
        "mt": (hcx, oy + mt_px / 2), "mb": (hcx, hy + hh_px + mb_px / 2),
        "hw": (hcx, hy + hh_px * 0.75), "hh": (hx + hw_px * 0.25, hy + hh_px * 0.35),
        "ow": (ox + ow / 2, oy + oh + 45), "oh": (ox - 55, oy + oh / 2),
    }
    for role, text in labels.items():
        _label(d, font, text, *spots[role])
    img.save(path)
    return str(path)


def run_case(name, sketch_path, truth: dict, expect_axis: dict, expect_cuts: int):
    """truth: {outer_w, outer_h, hole_w, hole_h, ml, mt, mr, mb}（源空间真值，cm）。"""
    r = parse_composite_sketch(
        sketch_path, target_outer_w_cm=TARGET_W_CM, target_outer_h_cm=TARGET_H_CM)
    got = {
        "outer_w": r.outer_w_cm - 1.0, "outer_h": r.outer_h_cm - 1.0,
        "hole_w": r.hole_w_cm - 1.0, "hole_h": r.hole_h_cm - 1.0,
        "ml": r.margin_left_cm, "mt": r.margin_top_cm,
        "mr": r.margin_right_cm, "mb": r.margin_bottom_cm,
    }
    print(f"\n=== {name} ===")
    print(f"  success={r.success} self_consistency={r.self_consistency} "
          f"sources(w/h)={r.sources.get('width_axis')}/{r.sources.get('height_axis')} "
          f"cuts={len(r.cuts_cm)}")
    ok = r.success and len(r.cuts_cm) == expect_cuts
    for role, expect in truth.items():
        err = abs(got[role] - expect)
        status = "OK " if err <= TOL_CM else "FAIL"
        if err > TOL_CM:
            ok = False
        print(f"  [{status}] {role:8s} got={got[role]:7.2f} expect={expect:7.2f} err={err:.2f}")
    for axis, expect in expect_axis.items():
        actual = r.sources.get(axis)
        # expect 可以是单值或集合：真实 OCR 对小字号标注可能漏读/误读，
        # 此时按设计走 ocr_fill 减法回填，两者都属正确的轴恢复路径
        passed = actual in expect if isinstance(expect, (set, frozenset, tuple, list)) else actual == expect
        status = "OK " if passed else "FAIL"
        if not passed:
            ok = False
        print(f"  [{status}] {axis}={actual} (expect {expect})")
    return ok


def main():
    tmp = tempfile.mkdtemp(prefix="composite_phase3_")
    all_ok = True

    # 场景1：等比草图，标注=真值（洞 400×300px=80×60，边距 225/50/300/90px=45/10/60/18）
    p1 = make_sketch(
        os.path.join(tmp, "to_scale.png"),
        hole_px=(400, 300), margins_px=(225, 50, 300, 90),
        labels={"ml": "45", "mr": "60", "mt": "10", "mb": "18", "hw": "80", "hh": "60"},
        cuts_px={"tr": (175, 50)})
    all_ok &= run_case(
        "1.to_scale", p1,
        truth={"outer_w": 185, "outer_h": 88, "hole_w": 80, "hole_h": 60,
               "ml": 45, "mt": 10, "mr": 60, "mb": 18},
        expect_axis={"width_axis": "ocr", "height_axis": "ocr"}, expect_cuts=1)

    # 场景2：不等比（洞画成 380×285px），标注真值 → OCR 必须覆盖像素比例
    p2 = make_sketch(
        os.path.join(tmp, "not_to_scale.png"),
        hole_px=(380, 285), margins_px=(235, 60, 310, 95),
        labels={"ml": "45", "mr": "60", "mt": "10", "mb": "18", "hw": "80", "hh": "60"},
        cuts_px={"tr": (175, 50)})
    all_ok &= run_case(
        "2.not_to_scale", p2,
        truth={"outer_w": 185, "outer_h": 88, "hole_w": 80, "hole_h": 60,
               "ml": 45, "mt": 10, "mr": 60, "mb": 18},
        expect_axis={"width_axis": "ocr", "height_axis": "ocr"}, expect_cuts=1)

    # 场景3：tr + bl 双挖角（35×10、30×12）
    p3 = make_sketch(
        os.path.join(tmp, "two_corners.png"),
        hole_px=(400, 300), margins_px=(225, 50, 300, 90),
        labels={"ml": "45", "mr": "60", "mt": "10", "mb": "18", "hw": "80", "hh": "60"},
        cuts_px={"tr": (175, 50), "bl": (150, 60)})
    all_ok &= run_case(
        "3.two_corners", p3,
        truth={"outer_w": 185, "outer_h": 88, "hole_w": 80, "hole_h": 60,
               "ml": 45, "mt": 10, "mr": 60, "mb": 18},
        expect_axis={"width_axis": "ocr", "height_axis": "ocr"}, expect_cuts=2)

    # 场景4：不等比 + 缺洞宽标注 → ocr_fill 减法回填
    p4 = make_sketch(
        os.path.join(tmp, "fill_missing.png"),
        hole_px=(380, 285), margins_px=(235, 60, 310, 95),
        labels={"ml": "45", "mr": "60", "mt": "10", "mb": "18", "hh": "60"},
        cuts_px={"tr": (175, 50)})
    all_ok &= run_case(
        "4.fill_missing", p4,
        truth={"outer_w": 185, "outer_h": 88, "hole_w": 80, "hole_h": 60,
               "ml": 45, "mt": 10, "mr": 60, "mb": 18},
        # 真实 OCR 对 "10" 小标注可能漏读 → 高轴允许 ocr_fill 回填路径
        expect_axis={"width_axis": "ocr_fill", "height_axis": {"ocr", "ocr_fill"}},
        expect_cuts=1)

    print("\n" + ("=" * 40))
    print("校准结果:", "全部通过 ✅" if all_ok else "存在失败项 ❌")
    print(f"草图样本保留在: {tmp}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
