# -*- coding: utf-8 -*-
import sys, traceback
sys.path.insert(0, '.')
from _diag_e2e_three_cases import build_huaye, build_moshanghuakai, build_wanhui
from core.image_cropper import _build_multi_layer_corner_mask
from core.corner.detection import _get_border_layers_robust, detect_nested_rect_layers
from core.corner.sector_render import _redraw_border_on_corner
from core.corner.algorithm import CORNER_ANGLES
from PIL import Image
import numpy as np

dpi = 150
bg = (255,255,255)

def test_one(name, build_fn):
    print('==== Test %s ====' % name)
    img, corners_cm, _ = build_fn()
    W, H = img.size
    corners_px = {}
    r_cap = max(1, min(W, H) // 2)
    for ck, rcm in corners_cm.items():
        r_raw = max(1, int(round(rcm * dpi / 2.54)))
        corners_px[ck] = min(r_raw, r_cap)
    print('  size=%dx%d, corners_px=%s' % (W, H, corners_px))
    border_layers = _get_border_layers_robust(img, bg)
    print('  border_layers: %d层' % len(border_layers))
    for i, (c, t) in enumerate(border_layers):
        print('    L%d: color=%s, thick=%d' % (i, c, t))
    nested_rects = detect_nested_rect_layers(img, border_layers=border_layers)
    print('  nested_rects: %d层' % len(nested_rects))
    mask = _build_multi_layer_corner_mask(W, H, corners_px, border_layers, nested_rects=nested_rects)
    result = Image.new('RGB', (W, H), bg)
    result.paste(img, mask=mask)
    ok = True
    for ck, rpx in corners_px.items():
        try:
            _redraw_border_on_corner(result, ck, rpx, border_layers, src_img=img, validity_mask=mask)
            print('  %s: sector_render OK (r=%d)' % (ck, rpx))
        except Exception as e:
            traceback.print_exc()
            print('  %s: sector_render FAILED: %s' % (ck, e))
            ok = False
    # 检查真正的 MASK_WRONG：在 angle 范围内且 dist > r 的像素应该被裁掉
    mask_arr = np.array(mask)
    wrong_count = 0
    for ck, r in corners_px.items():
        if ck == 'tl': cx, cy = r, r
        elif ck == 'tr': cx, cy = W-r, r
        elif ck == 'bl': cx, cy = r, H-r
        else: cx, cy = W-r, H-r
        ang_min, ang_max = CORNER_ANGLES[ck]
        x1 = max(0, cx - r - 5); y1 = max(0, cy - r - 5)
        x2 = min(W, cx + r + 6); y2 = min(H, cy + r + 6)
        yy, xx = np.mgrid[y1:y2, x1:x2].astype(np.float64)
        dx = xx - float(cx); dy = yy - float(cy)
        dist = np.sqrt(dx*dx + dy*dy)
        ang = np.mod(np.degrees(np.arctan2(dy, dx)), 360.0)
        shifted = np.mod(ang - ang_min, 360.0)
        span = ang_max - ang_min
        in_ang = shifted < span
        # 应该被裁：angle 范围内 & dist > r
        should_cut = in_ang & (dist > float(r))
        local_mask = mask_arr[y1:y2, x1:x2]
        wrong_mask = should_cut & (local_mask > 0)
        cnt = int(np.sum(wrong_mask))
        if cnt > 0:
            wrong_count += cnt
            ys, xs = np.where(wrong_mask)
            for i in range(min(3, len(ys))):
                y = ys[i]+y1; x = xs[i]+x1
                dd = float(dist[ys[i], xs[i]])
                print('  ! %s: (x=%d,y=%d) in_ang=T dist=%.1f > r=%d  mask=%d SHOULD_BE_0' % (
                    ck, x, y, dd, r, local_mask[ys[i], xs[i]]))
    if wrong_count > 0:
        print('  ! %s: %d MASK_WRONG (angle范围内 dist>r 但mask!=0)' % (name, wrong_count))
        ok = False
    else:
        print('  ✓ %s: 0 MASK_WRONG (尖角裁切正确)' % name)
    status = 'PASS' if ok else 'FAIL'
    print('  %s: %s' % (name, status))
    return ok

r1 = test_one('A1_墨上花开', build_moshanghuakai)
r2 = test_one('A2_花野', build_huaye)
r3 = test_one('A3_婉卉', build_wanhui)
print()
print('Overall: %s' % ('ALL PASS' if r1 and r2 and r3 else 'HAS FAILURES'))
sys.exit(0 if (r1 and r2 and r3) else 1)
