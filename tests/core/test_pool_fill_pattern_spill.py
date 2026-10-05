"""水池填充不得把洞外跨越清理环带的合法黑色花纹涂成素材底色。"""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from core.geometry import CropDesign
from core.image_ops import _get_inner_pixel_mask, render_design


@pytest.mark.parametrize("quality", ["preview", "export"])
@pytest.mark.parametrize("side", ["top", "bottom", "left", "right"])
@pytest.mark.parametrize("pattern", ["crossing", "small_detail"])
def test_fill_preserves_pattern_outside_hole(tmp_path, quality, side, pattern):
    design = CropDesign(
        canvas_w_cm=20, canvas_h_cm=15, dpi=50,
        inner_margin_top_cm=3, inner_margin_bottom_cm=3,
        inner_margin_left_cm=3, inner_margin_right_cm=3,
    )
    size = (design.canvas_w_px, design.canvas_h_px)
    outer = Image.new("RGB", size, (121, 78, 46))
    rect = design.inner_rect_px()
    x, y = round(rect.x), round(rect.y)
    right, bottom = round(rect.right), round(rect.bottom)
    # 黑色花纹从洞边跨出 10px 清理环带，模拟克罗印花的花瓣/线条。
    boxes = {
        "top": (x + 40, y - 25, x + 45, y + 5),
        "bottom": (x + 40, bottom - 5, x + 45, bottom + 25),
        "left": (x - 25, y + 40, x + 5, y + 45),
        "right": (right - 5, y + 40, right + 25, y + 45),
    }
    if pattern == "small_detail":
        boxes = {
            "top": (x + 40, y - 6, x + 45, y - 3),
            "bottom": (x + 40, bottom + 3, x + 45, bottom + 6),
            "left": (x - 6, y + 40, x - 3, y + 45),
            "right": (right + 3, y + 40, right + 6, y + 45),
        }
    ImageDraw.Draw(outer).rectangle(boxes[side], fill=(0, 0, 0))
    outer_path, inner_path = tmp_path / "outer.png", tmp_path / "inner.png"
    outer.save(outer_path)
    Image.new("RGB", size, (121, 78, 46)).save(inner_path)
    design.pool_outer_material_image = str(outer_path)
    design.pool_hole_transparent = True
    blank = np.array(render_design(design, quality=quality))
    design.pool_hole_transparent = False
    design.pool_inner_material_image = str(inner_path)
    filled = np.array(render_design(design, quality=quality))
    outside = ~_get_inner_pixel_mask(design)
    assert np.array_equal(filled[outside], blank[outside])
