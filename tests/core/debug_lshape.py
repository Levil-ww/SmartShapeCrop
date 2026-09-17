"""Debug script to analyze the L-shape detection issue."""

import os
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import cv2
from services.sketch_parser.lshape_sketch_parser import (
    parse_lshape_sketch,
    _detect_lshape_geometry,
    _extract_largest_contour,
)
from services.sketch_parser.sketch_parser_vision import _load_image, _to_gray


def _load_font(size):
    candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/msyh.ttc",
    ]
    for c in candidates:
        if os.path.isfile(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _make_tl_notch_sketch(out_path):
    """Create a clear TL notch sketch."""
    s = 3.0
    margin = 60

    B = 161.0
    A = 72.0
    E = 80.0
    D = 20.0

    W = int(B * s) + 2 * margin
    H = int(A * s) + 2 * margin
    img = Image.new('RGB', (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)

    ox, oy = margin, margin
    wpx, hpx = int(B * s), int(A * s)
    en, dn = int(E * s), int(D * s)

    # TL notch vertices
    verts = [
        (ox + en, oy),
        (ox + wpx, oy),
        (ox + wpx, oy + hpx),
        (ox, oy + hpx),
        (ox, oy + dn),
        (ox + en, oy + dn),
    ]

    d.line(verts + [verts[0]], fill=(0, 0, 0), width=6, joint='curve')

    font = _load_font(24)
    d.text((ox + wpx // 2 - 25, oy + hpx + 10), "161", fill=(0, 0, 0), font=font)
    d.text((ox + wpx + 10, oy + hpx // 2 - 10), "72", fill=(0, 0, 0), font=font)
    d.text((ox + en + (wpx - en) // 2 - 15, oy - 30), "81", fill=(0, 0, 0), font=font)
    d.text((ox - 40, oy + dn + (hpx - dn) // 2 - 10), "52", fill=(0, 0, 0), font=font)
    d.text((ox - 40, oy + hpx - 30), "20", fill=(0, 0, 0), font=font)
    d.text((ox + en // 2 - 15, oy + dn + 10), "80", fill=(0, 0, 0), font=font)

    img.save(out_path, 'PNG')
    return out_path


def debug_geometry(image_path):
    """Debug the geometry detection."""
    print(f"\n=== Debugging: {image_path} ===")

    cv2_mod = cv2
    img, err = _load_image(image_path)
    if err:
        print(f"Load error: {err}")
        return
    gray = _to_gray(img)

    print(f"Image size: {img.shape}")

    # Extract contour
    best = _extract_largest_contour(cv2_mod, gray)
    if best is None:
        print("No contour found")
        return

    area, cnt = best
    print(f"Contour area: {area}")

    cnt_pts = cnt.reshape(-1, 2)
    minx, miny = cnt_pts[:, 0].min(), cnt_pts[:, 1].min()
    maxx, maxy = cnt_pts[:, 0].max(), cnt_pts[:, 1].max()
    print(f"Bbox: ({minx}, {miny}) to ({maxx}, {maxy})")
    print(f"Outer: {maxx - minx} x {maxy - miny} px")

    # Detect geometry
    geo = _detect_lshape_geometry(cv2_mod, gray)
    if geo is None:
        print("Geometry detection failed")
        return

    print(f"\nDetected corner: {geo['corner']}")
    print(f"Cut: {geo['cut_w_px']:.1f} x {geo['cut_h_px']:.1f} px")
    print(f"Concave point: {geo['concave']}")
    print(f"Outer: {geo['outer_w_px']:.1f} x {geo['outer_h_px']:.1f} px")

    if 'all_corners' in geo:
        print(f"\nAll corners detected: {len(geo['all_corners'])}")
        for c in geo['all_corners']:
            print(f"  {c['corner']}: cut={c['cut_w_px']:.1f}x{c['cut_h_px']:.1f}, score={c['score']:.1f}")


def main():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'tl_notch.png')
        _make_tl_notch_sketch(p)

        debug_geometry(p)

        print("\n=== Full parse result ===")
        res = parse_lshape_sketch(p)
        print(f"Success: {res.success}")
        print(f"Corner: {res.corner}")
        print(f"Outer: {res.outer_w_cm} x {res.outer_h_cm} cm")
        print(f"Cut: {res.cut_w_cm} x {res.cut_h_cm} cm")
        print(f"Message: {res.message}")


if __name__ == '__main__':
    main()
