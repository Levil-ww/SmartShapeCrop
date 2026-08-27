"""
测试：core.rounded_corner 统一圆角 mask 算法

覆盖：
  - CORNER_ANGLES 角度映射正确性
  - get_corner_square / get_corner_pieslice_bbox 坐标计算
  - carve_corner_on_mask 单步扇形切割算法
  - 边界情况：r=0、r 过大、单角、四角
"""
import numpy as np
from PIL import Image
import pytest

from core.rounded_corner import (
    CORNER_ANGLES,
    carve_corner_on_mask,
    get_corner_square,
    get_corner_pieslice_bbox,
)


class TestCornerAngles:
    """角度常量映射（PIL 屏幕坐标系 y 向下）"""

    def test_tl_angle(self):
        """左上角：180°(左) → 270°(上)"""
        assert CORNER_ANGLES['tl'] == (180, 270)

    def test_tr_angle(self):
        """右上角：270°(上) → 360°(右)"""
        assert CORNER_ANGLES['tr'] == (270, 360)

    def test_bl_angle(self):
        """左下角：90°(下) → 180°(左)"""
        assert CORNER_ANGLES['bl'] == (90, 180)

    def test_br_angle(self):
        """右下角：0°(右) → 90°(下)"""
        assert CORNER_ANGLES['br'] == (0, 90)

    def test_all_four_corners_present(self):
        assert set(CORNER_ANGLES.keys()) == {'tl', 'tr', 'bl', 'br'}


class TestCornerGeometry:
    """角落几何坐标计算"""

    def test_square_br(self):
        """右下角正方形：应以 (w-r, h-r) 为左上角"""
        sq = get_corner_square((0, 0, 100, 80), 'br', 10)
        assert sq == (90, 70, 100, 80)

    def test_square_tl(self):
        """左上角正方形：应以 (0, 0) 为左上角"""
        sq = get_corner_square((0, 0, 100, 80), 'tl', 10)
        assert sq == (0, 0, 10, 10)

    def test_square_with_offset(self):
        """带偏移的矩形：正方形应相对矩形角点"""
        sq = get_corner_square((20, 30, 100, 80), 'br', 10)
        # rect.x=20, w=100 → 右边 x=120；rect.y=30, h=80 → 下边 y=110
        assert sq == (110, 100, 120, 110)

    def test_pieslice_bbox_br(self):
        """右下角 pieslice bbox：圆心在 (w-r, h-r)，bbox = [w-2r, h-2r, w, h]"""
        bbox = get_corner_pieslice_bbox((0, 0, 100, 80), 'br', 10)
        assert bbox == (80, 60, 100, 80)

    def test_pieslice_bbox_tl(self):
        """左上角 pieslice bbox：圆心在 (r, r)，bbox = [0, 0, 2r, 2r]"""
        bbox = get_corner_pieslice_bbox((0, 0, 100, 80), 'tl', 10)
        assert bbox == (0, 0, 20, 20)


class TestCarveCornerOnMask:
    """carve_corner_on_mask 核心算法"""

    def test_zero_radius_no_change(self):
        """r=0 时 mask 应保持全 255（不裁剪）"""
        mask = Image.new('L', (100, 80), 255)
        carve_corner_on_mask(mask, (0, 0, 100, 80), {'br': 0})
        arr = np.array(mask)
        assert arr.min() == 255  # 全部保留，无裁剪

    def test_negative_radius_no_change(self):
        """负半径应被跳过"""
        mask = Image.new('L', (100, 80), 255)
        carve_corner_on_mask(mask, (0, 0, 100, 80), {'br': -5})
        arr = np.array(mask)
        assert arr.min() == 255

    def test_br_corner_carves_square(self):
        """右下角应裁掉 r×r 正方形（除 1/4 圆外）"""
        W, H, r = 100, 80, 10
        mask = Image.new('L', (W, H), 255)
        carve_corner_on_mask(mask, (0, 0, W, H), {'br': r})
        arr = np.array(mask)

        # 右下角顶点 (99, 79) 在正方形外角，应被裁掉（=0）
        assert arr[79, 99] == 0
        # 正方形内部远离圆弧的点 (95, 75) 也应被裁掉
        # 距圆心 (90,70) 的距离 = sqrt(25+25) ≈ 7.07 < 10，在圆内保留=255
        # 改测距圆心更远的点：(99, 70) 距圆心 (90,70) = 9 < 10，保留
        # (99, 79) 距圆心 = sqrt(81+81) ≈ 12.7 > 10，裁掉 ✓
        assert arr[79, 99] == 0

    def test_br_corner_preserves_quarter_circle(self):
        """右下角应保留 1/4 圆区域（圆心 (W-r, H-r) 内）"""
        W, H, r = 100, 80, 10
        mask = Image.new('L', (W, H), 255)
        carve_corner_on_mask(mask, (0, 0, W, H), {'br': r})
        arr = np.array(mask)

        # 圆心 (90, 70) 应保留
        assert arr[70, 90] == 255
        # 圆内靠近圆心的点 (85, 65) 距圆心 ≈ 7.07 < 10，应保留
        assert arr[65, 85] == 255

    def test_center_unchanged(self):
        """图片中心区域不应被圆角影响"""
        W, H, r = 100, 80, 10
        mask = Image.new('L', (W, H), 255)
        carve_corner_on_mask(mask, (0, 0, W, H), {'br': r})
        arr = np.array(mask)
        assert arr[40, 50] == 255  # 中心点

    def test_all_four_corners(self):
        """四角同时裁剪：四角顶点都应被裁掉"""
        W, H, r = 100, 80, 10
        mask = Image.new('L', (W, H), 255)
        carve_corner_on_mask(mask, (0, 0, W, H),
                             {'tl': r, 'tr': r, 'bl': r, 'br': r})
        arr = np.array(mask)
        assert arr[0, 0] == 0      # tl
        assert arr[0, W-1] == 0    # tr
        assert arr[H-1, 0] == 0    # bl
        assert arr[H-1, W-1] == 0  # br

    def test_oversized_radius_clamped(self):
        """半径超过矩形一半时应被 clamp，不应崩溃"""
        W, H = 100, 80
        mask = Image.new('L', (W, H), 255)
        # r=200 远超 W/2=50 和 H/2=40，应被 clamp 到 min(W,H)//2=40
        carve_corner_on_mask(mask, (0, 0, W, H), {'br': 200})
        # 不应抛异常，mask 仍合法
        arr = np.array(mask)
        assert arr.shape == (H, W)

    def test_offset_rect(self):
        """带偏移的矩形：圆角应作用于矩形角点而非画布角点"""
        W, H = 200, 150
        mask = Image.new('L', (W, H), 255)
        # 矩形 (20, 30, 100, 80) 的右下角 = (120, 110)
        carve_corner_on_mask(mask, (20, 30, 100, 80), {'br': 10},
                             canvas_size=(W, H))
        arr = np.array(mask)
        # 矩形右下角顶点 (120, 110) 应被裁掉
        # 注意 PIL 坐标：x=120, y=110 → arr[110, 120]
        # 顶点距圆心 (110, 100) 的距离 = sqrt(100+100) ≈ 14.1 > 10，应裁掉
        assert arr[110, 120] == 0
        # 画布原点 (0, 0) 不在矩形角附近，应保留
        assert arr[0, 0] == 255


class TestMaskConsistency:
    """验证三处调用方使用同一算法"""

    def test_image_cropper_uses_carve(self):
        """image_cropper.apply_rounded_corners 应通过 carve_corner_on_mask 生效"""
        from core.image_cropper import apply_rounded_corners
        from PIL import Image as PILImage
        # 全黑图 + 右下角圆角：右下角应变成背景色 (255,255,255)
        img = PILImage.new('RGB', (100, 80), (0, 0, 0))
        result = apply_rounded_corners(img, {'br': 10}, dpi=150,
                                       bg_color=(255, 255, 255))
        arr = np.array(result)
        # 右下角顶点应被背景色填充
        assert tuple(arr[79, 99]) == (255, 255, 255)

    def test_geometry_uses_carve(self):
        """geometry.apply_rounded_corners_to_mask 应与 carve_corner_on_mask 一致"""
        from core.geometry import apply_rounded_corners_to_mask, RectShape
        W, H, r = 100, 80, 10

        # 用 geometry 接口生成
        mask_geo = Image.new('L', (W, H), 255)
        rect = RectShape(0, 0, W, H)
        apply_rounded_corners_to_mask(mask_geo, rect, {'br': r})
        arr_geo = np.array(mask_geo)

        # 用统一接口生成
        mask_uni = Image.new('L', (W, H), 255)
        carve_corner_on_mask(mask_uni, (0, 0, W, H), {'br': r})
        arr_uni = np.array(mask_uni)

        # 两者应完全一致
        assert np.array_equal(arr_geo, arr_uni)


class TestCornerContentProtection:
    """[Fix 2026-08-17] 圆角裁剪内容区保护测试"""

    def test_protect_content_when_radius_le_2x_border_depth(self):
        """当圆角半径 <= 2×边框厚度时，内部图案应保持直角完整"""
        from core.image_cropper import apply_border_only_corners, _get_border_layers_robust

        # 创建测试图像：多层边框 + 内部花纹
        w, h = 1000, 1200
        img = Image.new('RGB', (w, h), (255, 255, 255))
        arr = np.array(img)

        # 外层黑色边框 80px
        arr[0:80, :] = (0, 0, 0)
        arr[-80:, :] = (0, 0, 0)
        arr[:, 0:80] = (0, 0, 0)
        arr[:, -80:] = (0, 0, 0)

        # 装饰边框 30px
        arr[80:110, :] = (200, 50, 50)
        arr[-110:-80, :] = (200, 50, 50)
        arr[:, 80:110] = (200, 50, 50)
        arr[:, -110:-80] = (200, 50, 50)

        # 内部花纹矩形（蓝色，应保持完整）
        arr[200:300, 200:300] = (0, 100, 200)

        img = Image.fromarray(arr, 'RGB')
        bg_color = (255, 255, 255)
        border_layers = _get_border_layers_robust(img, bg_color)
        raw_depth = sum(t for _, t in border_layers) if border_layers else 0

        # r=3cm at 150dpi = 177px, raw_depth should be ~111px
        # r <= 2*raw_depth: 177 <= 222 → True, protection should trigger
        dpi = 150
        corners = {'tl': 0, 'tr': 0, 'bl': 3.0, 'br': 0}
        result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)

        result_arr = np.array(result)

        # 验证内部花纹完整（所有像素蓝色）
        inner_region = result_arr[200:300, 200:300]
        blue_pixels = (inner_region[:, :, 2] > 150).sum()
        total_pixels = 100 * 100
        assert blue_pixels == total_pixels, \
            f"内部花纹不完整: {blue_pixels}/{total_pixels} 像素为蓝色"

    def test_border_corner_correctly_carved_in_protection_mode(self):
        """保护模式下，边框区域的角应被正确裁切"""
        from core.image_cropper import apply_border_only_corners, _get_border_layers_robust

        w, h = 1000, 1200
        img = Image.new('RGB', (w, h), (255, 255, 255))
        arr = np.array(img)

        # 边框
        arr[0:80, :] = (0, 0, 0)
        arr[-80:, :] = (0, 0, 0)
        arr[:, 0:80] = (0, 0, 0)
        arr[:, -80:] = (0, 0, 0)

        # 内部花纹
        arr[300:400, 300:400] = (0, 200, 100)

        img = Image.fromarray(arr, 'RGB')
        bg_color = (255, 255, 255)

        dpi = 150
        corners = {'tl': 0, 'tr': 0, 'bl': 3.0, 'br': 0}
        result = apply_border_only_corners(img, corners, dpi=dpi, bg_color=bg_color)

        result_arr = np.array(result)

        # 左下角顶点应为背景色（被裁切）
        corner_pixel = result_arr[h - 1, 0]
        assert tuple(corner_pixel) == (255, 255, 255), \
            f"左下角顶点应为背景色，实际为: {tuple(corner_pixel)}"

        # 检查圆角区域（左下角）
        corner_region = result_arr[h - 200:h, 0:200]
        bg_in_corner = np.all(corner_region == np.array(bg_color), axis=2).sum()
        # 应有相当数量的背景像素（圆角裁切区域）
        assert bg_in_corner > 100, \
            f"左下角裁切区域背景像素过少: {bg_in_corner}"
