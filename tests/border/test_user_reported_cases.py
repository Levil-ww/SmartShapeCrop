"""
针对 2026-08-27 用户报告的 4 个圆角裁剪 bug 的验证测试。

核心不变量:
  INV-A: 内容区 (花纹/图案) 像素在圆角处理后颜色保持不变
  INV-B: 直边边框在非角区域保持完整厚度
  INV-C: 圆角弧区域无额外白色块覆盖应有内容
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
from PIL import Image

from core.image_cropper import apply_border_only_corners


def _make_bordered_image(w, h, bg, edge_layers, content_color=None, content_period=30):
    """构造有边框的测试图像。edge_layers 是 [(edge_width, color), ...]，只有边缘有边框"""
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :, :] = np.array(bg, dtype=np.uint8)
    offset = 0
    for width, color in edge_layers:
        c = np.array(color, dtype=np.uint8)
        arr[offset:offset+width, offset:w-offset, :] = c
        arr[h-offset-width:h-offset, offset:w-offset, :] = c
        arr[offset:h-offset, offset:offset+width, :] = c
        arr[offset:h-offset, w-offset-width:w-offset, :] = c
        offset += width
    if content_color is not None:
        for y in range(offset + 10, h - offset - 10, content_period):
            for x in range(offset + 10, w - offset - 10, content_period):
                arr[y, x, :] = content_color
    return arr


def _content_changed_pct(orig, result, y0, y1, x0, x1):
    o = orig[y0:y1, x0:x1, :].astype(np.float64)
    r = result[y0:y1, x0:x1, :].astype(np.float64)
    diff = np.sqrt(np.sum((o - r) ** 2, axis=2))
    return float(np.mean(diff > 2.0))


def test_maliya_rose_content_preserved():
    """CASE 1: 玛利亚玫瑰 — 内部米色花纹不被修改"""
    w, h = 800, 1200
    bg = (255, 255, 255)
    orig = _make_bordered_image(w, h, bg, [
        (10, (30, 25, 25)),
        (15, (240, 225, 200)),
        (12, (35, 28, 25)),
    ], content_color=(215, 195, 160), content_period=25)
    img = Image.fromarray(orig, 'RGB')
    result = apply_border_only_corners(
        img, {'tl': 3.5, 'tr': 3.5, 'bl': 3.5, 'br': 3.5}, dpi=150, bg_color=bg
    )
    result_arr = np.array(result)
    # 内容区域检查 (距边框内 10px 开始)
    pct = _content_changed_pct(orig, result_arr, 50, h - 50, 50, w - 50)
    assert pct < 0.02, f"CASE1 FAIL: 花纹被修改 {pct:.4f}"
    # 四个角区域内容保留
    r_px = int(3.5 * 150 / 2.54)
    for ck, cy0, cx0 in [
        ('tl', r_px + 15, r_px + 15),
        ('tr', r_px + 15, w - r_px - 15),
    ]:
        p = _content_changed_pct(orig, result_arr, cy0, cy0 + 40, cx0, cx0 + 40)
        assert p < 0.05, f"CASE1 FAIL: {ck} 角花纹被修改 {p:.4f}"
    print("  CASE1 (玛利亚玫瑰): PASS")
    return True


def test_fugu_border_thickness_preserved():
    """CASE 3: 复古花丛 — 直边边框保持完整厚度"""
    w, h = 700, 500
    bg = (255, 255, 255)
    orig = _make_bordered_image(w, h, bg, [
        (8, (20, 20, 20)),
        (14, (245, 240, 230)),
        (8, (30, 30, 30)),
    ])
    img = Image.fromarray(orig, 'RGB')
    result = apply_border_only_corners(
        img, {'tl': 4.0, 'tr': 4.0, 'bl': 4.0, 'br': 4.0}, dpi=150, bg_color=bg
    )
    result_arr = np.array(result)
    r_px = int(4.0 * 150 / 2.54)

    def _first_consecutive_segment(match_arr):
        n = len(match_arr)
        i = 0
        while i < n:
            if match_arr[i]:
                start = i
                while i < n and match_arr[i]:
                    i += 1
                return start, i - start
            i += 1
        return -1, 0

    # 检查顶边 (x 在 TL 和 TR 弧中心之间的直边)
    for x in [300, 350, 400]:
        col = result_arr[:, x, :]
        match = np.all(col == (20, 20, 20), axis=1)
        start, length = _first_consecutive_segment(match)
        assert start == 0, f"CASE3 FAIL: x={x} 外边框不从顶部开始 (start={start})"
        assert 6 <= length <= 10, f"CASE3 FAIL: x={x} 外层黑边框厚度 {length}px, 应为 ~8px"
    print("  CASE3 (复古花丛): PASS")
    return True


def test_wanhui_no_extra_white():
    """CASE 4: 婉卉 — 圆角弧内侧内容保留，无额外白色"""
    w, h = 800, 500
    bg = (255, 255, 255)
    orig = _make_bordered_image(w, h, bg, [
        (6, (20, 20, 20)),
        (10, (240, 240, 240)),
        (8, (25, 25, 25)),
    ], content_color=(245, 240, 235), content_period=1)
    img = Image.fromarray(orig, 'RGB')
    result = apply_border_only_corners(img, {'tl': 4.0}, dpi=150, bg_color=bg)
    result_arr = np.array(result)
    r_px = int(4.0 * 150 / 2.54)
    # 弧内侧 (dist <= r_px) 的内容像素应保留
    from core.corner.algorithm import CORNER_ANGLES
    ang_min, ang_max = CORNER_ANGLES['tl']
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    dx = xx - r_px; dy = yy - r_px
    dist = np.sqrt(dx*dx + dy*dy)
    angle = np.mod(np.degrees(np.arctan2(dy,dx)), 360.0)
    valid = (angle >= ang_min) & (angle <= ang_max)
    inside = valid & (dist <= r_px)
    # 内侧内容像素（245,240,235）应保留
    content_mask = np.all(orig == (245, 240, 235), axis=2) & inside
    if np.any(content_mask):
        kept = np.all(result_arr[content_mask] == (245, 240, 235), axis=1)
        ratio = float(np.mean(kept))
        assert ratio >= 0.95, f"CASE4 FAIL: 内容保留率 {ratio:.4f}"
    print("  CASE4 (婉卉): PASS")
    return True


def test_xianxu_corner_clean():
    """CASE 2: 闲叙青釉 — 圆角弧区域裁切干净"""
    w, h = 900, 600
    bg = (255, 255, 255)
    orig = _make_bordered_image(w, h, bg, [
        (10, (20, 20, 20)),
        (15, (235, 220, 195)),
        (15, (25, 25, 25)),
    ], content_color=(60, 120, 100), content_period=30)
    img = Image.fromarray(orig, 'RGB')
    result = apply_border_only_corners(img, {'tl': 9.0}, dpi=150, bg_color=bg)
    result_arr = np.array(result)
    r_px = int(9.0 * 150 / 2.54)
    # INV: arc 外侧 (dist > r_px) 不应有内容残留
    from core.corner.algorithm import CORNER_ANGLES
    ang_min, ang_max = CORNER_ANGLES['tl']
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    dx = xx - r_px; dy = yy - r_px
    dist = np.sqrt(dx*dx + dy*dy)
    angle = np.mod(np.degrees(np.arctan2(dy,dx)), 360.0)
    valid = (angle >= ang_min) & (angle <= ang_max)
    outside = valid & (dist > r_px)
    # 外侧应为白色（背景色）或黑色（边框色），但不应有绿色内容
    outside_arr = result_arr[outside]
    is_bg_or_border = (np.all(outside_arr == 255, axis=1)) | (np.max(outside_arr, axis=1) < 60)
    not_allowed = ~is_bg_or_border
    if np.any(not_allowed):
        ratio = float(np.mean(not_allowed))
        assert ratio < 0.01, f"CASE2 FAIL: 弧外侧非预期像素 {ratio:.4f}"
    print("  CASE2 (闲叙青釉): PASS")
    return True


def main():
    results = {}
    for name, fn in [
        ('玛利亚玫瑰', test_maliya_rose_content_preserved),
        ('闲叙青釉', test_xianxu_corner_clean),
        ('复古花丛', test_fugu_border_thickness_preserved),
        ('婉卉', test_wanhui_no_extra_white),
    ]:
        try:
            fn()
            results[name] = 'PASS'
        except AssertionError as e:
            results[name] = f'FAIL: {e}'
            print(f'  {name}: FAIL - {e}')
        except Exception as e:
            results[name] = f'ERROR: {e}'
            import traceback
            traceback.print_exc()
            print(f'  {name}: ERROR - {e}')
    print('=' * 40)
    all_ok = all(v == 'PASS' for v in results.values())
    print(f'总体: {"全部通过" if all_ok else "有失败"}')
    for k, v in results.items():
        print(f'  {k}: {v}')
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
