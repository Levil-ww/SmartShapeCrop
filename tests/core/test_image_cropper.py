"""
测试：core.image_cropper 核心裁剪功能

覆盖：
  - apply_rounded_corners / apply_border_only_corners
  - CropConfig 默认值
  - 圆角与背景色交互
"""
import numpy as np
from PIL import Image, ImageDraw
import pytest

from core.image_cropper import (
    CropConfig,
    crop_image,
    apply_rounded_corners,
    apply_border_only_corners,
)
from core import config


class TestCropConfig:
    """CropConfig 默认值"""

    def test_default_dpi(self):
        c = CropConfig()
        assert c.dpi == config.DEFAULT_DPI == 150

    def test_default_mode(self):
        c = CropConfig()
        assert c.mode == 'simple_resize'

    def test_default_bg_color(self):
        c = CropConfig()
        assert c.bg_color == (255, 255, 255)

    def test_custom_values(self):
        c = CropConfig(
            src_path="/tmp/test.jpg",
            target_w_cm=41.0,
            target_h_cm=55.0,
            corners={'br': 2.0},
            dpi=300,
        )
        assert c.src_path == "/tmp/test.jpg"
        assert c.corners == {'br': 2.0}


class TestApplyRoundedCorners:
    """apply_rounded_corners 整体圆角"""

    def _make_test_image(self, w=200, h=150):
        """创建带黑边框的测试图"""
        img = Image.new('RGB', (w, h), (200, 200, 200))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, w-1, h-1], outline=(0, 0, 0), width=4)
        return img

    def test_br_corner_filled_with_bg(self):
        """右下角圆角处应填充为背景色"""
        img = self._make_test_image(w=200, h=150)
        result = apply_rounded_corners(img, {'br': 5.0}, dpi=150,
                                       bg_color=(255, 255, 255))
        arr = np.array(result)
        # 右下角顶点 (x=199, y=149) 应被背景色填充
        assert tuple(arr[149, 199]) == (255, 255, 255)

    def test_center_preserved(self):
        """图片中心区域不应被圆角影响"""
        img = self._make_test_image()
        original_center = np.array(img)[75, 100]
        result = apply_rounded_corners(img, {'br': 5.0}, dpi=150)
        result_center = np.array(result)[75, 100]
        assert tuple(original_center) == tuple(result_center)

    def test_no_corners_no_change(self):
        """无圆角时图片不应改变"""
        img = self._make_test_image()
        result = apply_rounded_corners(img, {}, dpi=150)
        assert np.array_equal(np.array(img), np.array(result))


class TestApplyBorderOnlyCorners:
    """apply_border_only_corners 仅边框圆角"""

    def test_inner_area_unchanged(self):
        """仅边框圆角：内部应保持直角（不被裁剪）"""
        img = Image.new('RGB', (200, 150), (255, 255, 255))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, 199, 149], outline=(0, 0, 0), width=4)
        # 中心点 (100, 75) 设为红色
        img.putpixel((100, 75), (255, 0, 0))

        result = apply_border_only_corners(img, {'br': 2.0}, dpi=150)
        arr = np.array(result)
        # 内部红点应保留
        assert tuple(arr[75, 100]) == (255, 0, 0)

    def test_corner_filled_with_bg(self):
        """圆角处应填充背景色"""
        img = Image.new('RGB', (200, 150), (0, 0, 0))
        result = apply_border_only_corners(img, {'br': 3.0}, dpi=150,
                                           bg_color=(255, 255, 255))
        arr = np.array(result)
        # 右下角顶点应为背景色
        assert tuple(arr[149, 199]) == (255, 255, 255)


class TestCropImageIntegration:
    """crop_image 集成测试"""

    def test_simple_resize_mode(self, tmp_path):
        """simple_resize 模式：等比缩放，不裁剪不留白"""
        # 创建 200x150 的源图
        src = Image.new('RGB', (200, 150), (100, 150, 200))
        src.save(tmp_path / "src.jpg", 'JPEG', quality=95)

        cfg = CropConfig(
            src_path=str(tmp_path / "src.jpg"),
            target_w_cm=41.0,
            target_h_cm=55.0,
            corners={'br': 2.0},
            mode='simple_resize',
            dpi=150,
        )
        result = crop_image(cfg)
        # 应返回 PIL Image
        assert result is not None
        assert result.size[0] > 0 and result.size[1] > 0

    def test_no_corners(self, tmp_path):
        """无圆角的简单缩放"""
        src = Image.new('RGB', (200, 150), (100, 150, 200))
        src.save(tmp_path / "src.jpg", 'JPEG', quality=95)

        cfg = CropConfig(
            src_path=str(tmp_path / "src.jpg"),
            target_w_cm=41.0,
            target_h_cm=55.0,
            corners=None,
            mode='simple_resize',
            dpi=150,
        )
        result = crop_image(cfg)
        assert result is not None


class TestCropConfigValidate:
    """CropConfig.validate() 参数校验"""

    def _valid(self, **kw):
        base = dict(src_path="/tmp/x.jpg", target_w_cm=41.0, target_h_cm=55.0,
                    corners={'br': 2.0}, mode='simple_resize', dpi=150)
        base.update(kw)
        return CropConfig(**base)

    # —— 正常值不抛异常 ——
    def test_valid_config_passes(self):
        self._valid().validate()

    def test_none_corners_passes(self):
        self._valid(corners=None).validate()

    def test_empty_corners_passes(self):
        self._valid(corners={}).validate()

    def test_zero_corner_passes(self):
        self._valid(corners={'br': 0.0}).validate()

    # —— 宽高 ——
    def test_zero_w_raises(self):
        with pytest.raises(ValueError, match='target_w_cm'):
            self._valid(target_w_cm=0).validate()

    def test_negative_w_raises(self):
        with pytest.raises(ValueError, match='target_w_cm'):
            self._valid(target_w_cm=-1.0).validate()

    def test_zero_h_raises(self):
        with pytest.raises(ValueError, match='target_h_cm'):
            self._valid(target_h_cm=0).validate()

    def test_negative_h_raises(self):
        with pytest.raises(ValueError, match='target_h_cm'):
            self._valid(target_h_cm=-5.0).validate()

    # —— DPI ——
    def test_zero_dpi_raises(self):
        with pytest.raises(ValueError, match='dpi'):
            self._valid(dpi=0).validate()

    def test_negative_dpi_raises(self):
        with pytest.raises(ValueError, match='dpi'):
            self._valid(dpi=-150).validate()

    # —— mode ——
    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match='mode'):
            self._valid(mode='bogus').validate()

    def test_all_valid_modes_pass(self):
        for m in ('simple_resize', 'cover', 'contain', 'light_cover', 'auto'):
            self._valid(mode=m).validate()

    # —— max_crop_ratio ——
    def test_negative_ratio_raises(self):
        with pytest.raises(ValueError, match='max_crop_ratio'):
            self._valid(max_crop_ratio=-0.1).validate()

    def test_ratio_over_1_raises(self):
        with pytest.raises(ValueError, match='max_crop_ratio'):
            self._valid(max_crop_ratio=1.5).validate()

    def test_ratio_boundary_zero_passes(self):
        self._valid(max_crop_ratio=0.0).validate()

    def test_ratio_boundary_one_passes(self):
        self._valid(max_crop_ratio=1.0).validate()

    # —— corners ——
    def test_invalid_corner_key_raises(self):
        with pytest.raises(ValueError, match='corners 键'):
            self._valid(corners={'xx': 2.0}).validate()

    def test_negative_corner_raises(self):
        with pytest.raises(ValueError, match="corners\\['br'\\]"):
            self._valid(corners={'br': -1.0}).validate()

    def test_corner_exceeds_half_size_raises(self):
        # min(41, 55)/2 = 20.5; 25 > 20.5
        with pytest.raises(ValueError, match='超过目标尺寸的一半'):
            self._valid(corners={'br': 25.0}).validate()

    def test_corner_at_half_passes(self):
        self._valid(corners={'br': 20.0}).validate()

    # —— bg_color ——
    def test_bad_bg_color_raises(self):
        with pytest.raises(ValueError, match='bg_color'):
            self._valid(bg_color=(255, 255)).validate()

    def test_non_tuple_bg_color_raises(self):
        with pytest.raises(ValueError, match='bg_color'):
            self._valid(bg_color='white').validate()
