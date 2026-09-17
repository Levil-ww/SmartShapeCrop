"""Test case for the L-shape sketch from the screenshot.

The sketch shows:
- Bottom: 161
- Right: 71
- Top: 81
- Left: 51
- Bottom-left vertical: 20
- Middle horizontal: 80

This is an L-shape with notch at top-left.
Expected:
- Outer frame: 161 x 72 cm
- Corner: tl (top-left)
- Cut: 80 x 20 cm
"""

import os
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.sketch_parser.lshape_sketch_parser import parse_lshape_sketch


def _load_font(size):
    """Load a usable font."""
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


def _make_screenshot_lshape(out_path):
    """Create an L-shape sketch matching the screenshot."""
    s = 4.0
    margin = 80

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

    verts = [
        (ox + en, oy),
        (ox + wpx, oy),
        (ox + wpx, oy + hpx),
        (ox, oy + hpx),
        (ox, oy + dn),
        (ox + en, oy + dn),
    ]

    d.line(verts + [verts[0]], fill=(0, 0, 0), width=8, joint='curve')

    font = _load_font(28)
    d.text((ox + wpx // 2 - 30, oy + hpx + 15), "161", fill=(255, 0, 0), font=font)
    d.text((ox + wpx + 15, oy + hpx // 2 - 15), "71", fill=(255, 0, 0), font=font)
    d.text((ox + en + (wpx - en) // 2 - 20, oy - 35), "81", fill=(255, 0, 0), font=font)
    d.text((ox - 50, oy + dn + (hpx - dn) // 2 - 15), "51", fill=(255, 0, 0), font=font)
    d.text((ox - 50, oy + hpx - 40), "20", fill=(255, 0, 0), font=font)
    d.text((ox + en // 2 - 20, oy + dn + 15), "80", fill=(255, 0, 0), font=font)

    img.save(out_path, 'PNG')
    return out_path


def test_screenshot_lshape():
    """Test the L-shape from the screenshot."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'screenshot_lshape.png')
        _make_screenshot_lshape(p)

        print(f"Testing: {p}")
        res = parse_lshape_sketch(p)

        print(f"\nResult:")
        print(f"  Success: {res.success}")
        print(f"  Message: {res.message}")
        print(f"  Corner: {res.corner}")
        print(f"  Outer: {res.outer_w_cm} x {res.outer_h_cm} cm")
        print(f"  Cut: {res.cut_w_cm} x {res.cut_h_cm} cm")

        print(f"\nExpected:")
        print(f"  Corner: tl")
        print(f"  Outer: 161 x 72 cm")
        print(f"  Cut: 80 x 20 cm")

        print(f"\nDebug info:")
        if res.debug:
            for k, v in res.debug.items():
                print(f"  {k}: {v}")


if __name__ == '__main__':
    test_screenshot_lshape()
