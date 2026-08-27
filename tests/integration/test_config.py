"""
测试：core.config 统一配置管理

覆盖：
  - 业务常量值正确
  - 单位换算函数 cm_to_px / px_to_cm 互逆性
  - DPI 默认值一致性
"""
import pytest
from core import config


class TestConfigConstants:
    """业务常量值正确性"""

    def test_border_width(self):
        assert config.DEFAULT_BORDER_WIDTH_CM == 1.5
        assert config.BORDER_TOTAL_DEPTH_CM == 2.0

    def test_default_dpi_is_150(self):
        """统一默认 DPI=150，杜绝 UI=150 / 脚本=300 的不一致"""
        assert config.DEFAULT_DPI == 150

    def test_cut_loss(self):
        assert config.CUT_LOSS_CM == 1.0

    def test_default_bg_color(self):
        assert config.DEFAULT_BG_COLOR == (255, 255, 255)

    def test_default_crop_mode(self):
        assert config.DEFAULT_CROP_MODE == 'simple_resize'

    def test_color_distance_threshold(self):
        assert config.BORDER_COLOR_DISTANCE_THRESHOLD == 15


class TestUnitConversion:
    """cm ↔ px 单位换算"""

    def test_cm_to_px_basic(self):
        # 2.54cm @ 150DPI = 150px
        assert config.cm_to_px(2.54, dpi=150) == 150

    def test_cm_to_px_min_value_1(self):
        """0 或负值应被 clamp 到 1，避免 r=0 导致 pieslice 退化"""
        assert config.cm_to_px(0) == 1
        assert config.cm_to_px(-1.0) == 1

    def test_cm_to_px_default_dpi(self):
        """未指定 DPI 时使用 DEFAULT_DPI=150"""
        assert config.cm_to_px(2.54) == config.cm_to_px(2.54, dpi=config.DEFAULT_DPI)

    def test_px_to_cm_basic(self):
        # 150px @ 150DPI = 2.54cm
        assert abs(config.px_to_cm(150, dpi=150) - 2.54) < 1e-6

    def test_inverse_conversion(self):
        """cm → px → cm 应基本可逆（受取整误差影响，<1像素）"""
        for cm in [0.5, 1.0, 2.54, 8.5, 41.0, 55.0]:
            px = config.cm_to_px(cm, dpi=150)
            cm_back = config.px_to_cm(px, dpi=150)
            assert abs(cm_back - cm) < 2.54 / 150  # 容差 < 1 像素的 cm 值


class TestConfigConsistency:
    """配置在项目内一致性"""

    def test_image_cropper_uses_config(self):
        """image_cropper.py 中的常量应从 config 派生，不应硬编码"""
        from core import image_cropper
        assert image_cropper.BORDER_TOTAL_DEPTH_CM is config.BORDER_TOTAL_DEPTH_CM

    def test_crop_config_default_dpi(self):
        from core.image_cropper import CropConfig
        assert CropConfig.dpi == config.DEFAULT_DPI

    def test_crop_config_default_mode(self):
        from core.image_cropper import CropConfig
        assert CropConfig.mode == config.DEFAULT_CROP_MODE
