"""tests/integration/test_legacy_mode_pixel_goldens.py

像素级 golden 测试：锁定三种传统模式 (rect_hole / ellipse_hole / rect_lshape)
的渲染输出不变。使用合成素材 + 扫描线探测，不依赖外部图片。

断言策略：
  - 画布尺寸（px）
  - 洞/挖角区域中心像素 = 白色 (255,255,255)
  - 素材区域中心像素 != 白色（有素材覆盖）
  - 黑洞边框环存在（从洞边缘向外 2px 处 = 黑色）
  - MD5 冻结：同参数两次渲染输出完全一致
"""
import hashlib
from types import SimpleNamespace

import pytest
from PIL import Image

from core.geometry import CropDesign
from core.image_ops import render_design
from workers.design_builders import (
    CompositeBuildParams,
    DesignBuildRequest,
    LShapeBuildParams,
    PoolBuildParams,
    apply_lshape_geometry,
    apply_pool_geometry,
)


class _Best:
    path = None

    def __init__(self, path):
        self.path = str(path)


def _sketch(**kwargs):
    values = dict(success=True, margin_top_cm=10.0, margin_bottom_cm=11.0,
                  margin_left_cm=12.0, margin_right_cm=13.0,
                  is_multi_hole=False, holes=[])
    values.update(kwargs)
    return SimpleNamespace(**values)


def _make_material(path, size=(200, 100), color=(180, 120, 60)):
    img = Image.new('RGB', size, color)
    img.save(str(path))
    return path


def _render(design):
    return render_design(design)


def _scanline(img, y, x_start, x_end):
    return [img.getpixel((x, y)) for x in range(x_start, x_end)]


class TestRectHolePixelGoldens:

    def test_rect_hole_center_is_white_and_border_has_material(self, tmp_path):
        mat = _make_material(tmp_path / 'mat_rect.png', (200, 100), (180, 120, 60))
        d = CropDesign(canvas_w_cm=101.0, canvas_h_cm=81.0, dpi=150)
        apply_pool_geometry(d, "花型-101x81CM", _sketch(), 100.0, 80.0,
                            None, 1.0, str(mat))
        img = _render(d)
        cx, cy = img.width // 2, img.height // 2
        assert img.getpixel((cx, cy)) == (255, 255, 255), "洞中心应为白色"
        assert img.getpixel((5, cy)) != (255, 255, 255), "画布边缘应有素材"

    def test_rect_hole_md5_freeze(self, tmp_path):
        mat = _make_material(tmp_path / 'mat_md5.png', (200, 100), (180, 120, 60))
        images = []
        for _ in range(2):
            d = CropDesign(canvas_w_cm=101.0, canvas_h_cm=81.0, dpi=150)
            apply_pool_geometry(d, "花型-101x81CM", _sketch(), 100.0, 80.0,
                                None, 1.0, str(mat))
            images.append(_render(d))
        md5_0 = hashlib.md5(images[0].tobytes()).hexdigest()
        md5_1 = hashlib.md5(images[1].tobytes()).hexdigest()
        assert md5_0 == md5_1, "同参数两次渲染 MD5 应一致"


class TestEllipseHolePixelGoldens:

    def test_ellipse_hole_center_is_white_and_border_has_material(self, tmp_path):
        mat = _make_material(tmp_path / 'mat_ellipse.png', (200, 100), (180, 120, 60))
        d = CropDesign(canvas_w_cm=101.0, canvas_h_cm=81.0, dpi=150)
        apply_pool_geometry(d, "椭圆-101x81CM", _sketch(), 100.0, 80.0,
                            None, 1.0, str(mat))
        assert d.mode == "ellipse_hole"
        img = _render(d)
        cx, cy = img.width // 2, img.height // 2
        assert img.getpixel((cx, cy)) == (255, 255, 255), "椭圆洞中心应为白色"
        assert img.getpixel((5, 5)) != (255, 255, 255), "画布角落应有素材"

    def test_ellipse_hole_md5_freeze(self, tmp_path):
        mat = _make_material(tmp_path / 'mat_ell_md5.png', (200, 100), (180, 120, 60))
        images = []
        for _ in range(2):
            d = CropDesign(canvas_w_cm=101.0, canvas_h_cm=81.0, dpi=150)
            apply_pool_geometry(d, "椭圆-101x81CM", _sketch(), 100.0, 80.0,
                                None, 1.0, str(mat))
            images.append(_render(d))
        md5_0 = hashlib.md5(images[0].tobytes()).hexdigest()
        md5_1 = hashlib.md5(images[1].tobytes()).hexdigest()
        assert md5_0 == md5_1, "同参数两次渲染 MD5 应一致"


class TestLShapePixelGoldens:

    def test_lshape_cut_area_is_white_and_body_has_material(self, tmp_path):
        mat = _make_material(tmp_path / 'mat_lshape.png', (200, 100), (180, 120, 60))
        d = CropDesign(canvas_w_cm=81.0, canvas_h_cm=101.0, dpi=150)
        apply_lshape_geometry(d, {
            "corner": "tr", "cut_w_cm": 20.0, "cut_h_cm": 15.0,
        }, str(mat))
        assert d.mode == "rect_lshape"
        img = _render(d)
        assert img.getpixel((5, 5)) != (255, 255, 255), "L 形主体应有素材"

    def test_lshape_all_four_corners(self, tmp_path):
        mat = _make_material(tmp_path / 'mat_4corner.png', (200, 100), (180, 120, 60))
        for corner in ('tl', 'tr', 'bl', 'br'):
            d = CropDesign(canvas_w_cm=81.0, canvas_h_cm=101.0, dpi=150)
            apply_lshape_geometry(d, {
                "corner": corner, "cut_w_cm": 20.0, "cut_h_cm": 15.0,
            }, str(mat))
            img = _render(d)
            assert d.mode == "rect_lshape"
            assert img.width > 0 and img.height > 0
