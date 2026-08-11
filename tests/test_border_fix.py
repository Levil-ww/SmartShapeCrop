"""
测试边框线在圆角裁剪中是否正确保留。
验证修复后的 _detect_border_layers 和 _redraw_border_on_corner 功能。

说明：这是**基于真实源图**的集成测试，需要 psd_demo/ 目录下存在指定 JPG。
如果图片不存在，测试自动跳过（不阻断 CI）。
"""
import os
import sys
import pytest
import numpy as np
from PIL import Image

from core.image_cropper import (
    _detect_border_layers,
    apply_border_only_corners,
    apply_rounded_corners,
    load_source_image,
)

# ============ 参数配置（相对项目根） ============
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_NAME = "双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg"
SRC_PATH = os.path.join(PROJECT_ROOT, "psd_demo", SRC_NAME)
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "logs", "test_border_fix_output")
DPI = 300

TEST_CASES = [
    {
        "name": "small_corner_border_only",
        "target_w_cm": 41.0,
        "target_h_cm": 55.0,
        "corners": {"tl": 0, "tr": 0, "bl": 0, "br": 3.0},
        "expected_mode": "border_only",
    },
]


@pytest.fixture(scope="module")
def source_image():
    if not os.path.exists(SRC_PATH):
        pytest.skip(f"需要源图才能运行此测试（未找到: {SRC_PATH}）")
    return load_source_image(SRC_PATH)


@pytest.fixture(scope="module")
def cropped_image(source_image):
    target_w_px = int(round(41.0 * DPI / 2.54))
    target_h_px = int(round(55.0 * DPI / 2.54))
    return source_image.resize((target_w_px, target_h_px), Image.LANCZOS)


class TestBorderDetection:
    def test_detect_on_original(self, source_image):
        layers = _detect_border_layers(source_image, max_scan_depth_px=300)
        # 简织图必须能检测到至少 1 层边框
        assert len(layers) >= 1, f"原图应检测到边框层，实际 {len(layers)} 层"
        total_thick = sum(t for _, t in layers)
        # 总厚度不得超过硬上限 10 cm (=10 * 300/2.54)
        max_thick = int(10 * DPI / 2.54)
        assert total_thick <= max_thick, f"边框总厚度 {total_thick}px 超上限 {max_thick}px"

    def test_detect_after_resize(self, cropped_image):
        layers = _detect_border_layers(cropped_image, max_scan_depth_px=300)
        assert len(layers) >= 1, f"缩放后仍应检测到边框，实际 {len(layers)} 层"


class TestApplyBorderOnlyCorners:
    @pytest.mark.parametrize("case", TEST_CASES, ids=lambda c: c["name"])
    def test_keeps_border_pixels(self, cropped_image, case):
        result = apply_border_only_corners(
            cropped_image, case["corners"], DPI, (255, 255, 255)
        )
        assert result.size == cropped_image.size, "输出尺寸必须与输入一致"

        w, h = result.size
        arr = np.array(result)
        r_px = int(round(case["corners"]["br"] * DPI / 2.54))
        cx, cy = w - r_px, h - r_px  # 右下角扇形圆心

        # 在右下角 1/4 圆弧上采样（45°→90° = 右下角弧），步长 1°
        dark_pixels = 0
        total_checked = 0
        for deg in range(0, 91, 1):
            theta = np.deg2rad(deg)
            for r_off in range(-2, 3):  # 距圆弧 ±2px
                rx = cx + (r_px + r_off) * np.cos(theta)
                ry = cy + (r_px + r_off) * np.sin(theta)
                x, y = int(round(rx)), int(round(ry))
                if 0 <= x < w and 0 <= y < h:
                    total_checked += 1
                    if sum(arr[y, x, :3]) < 200:
                        dark_pixels += 1

        assert dark_pixels > 0, (
            f"场景 {case['name']} 右下角圆弧 90°×±2px 采样 "
            f"{dark_pixels}/{total_checked} 深色像素，疑似边框丢失"
        )

    @pytest.mark.parametrize("case", TEST_CASES, ids=lambda c: c["name"])
    def test_no_exception_save_result(self, cropped_image, case):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        result = apply_border_only_corners(
            cropped_image, case["corners"], DPI, (255, 255, 255)
        )
        out = os.path.join(OUTPUT_DIR, f"test_{case['name']}.jpg")
        result.save(out, "JPEG", quality=95, optimize=True, dpi=(DPI, DPI))
        assert os.path.exists(out) and os.path.getsize(out) > 1000, "结果文件保存失败"


class TestApplyRoundedCorners:
    def test_br_2cm_keeps_border(self, cropped_image):
        corners = {"tl": 0, "tr": 0, "bl": 0, "br": 2.0}
        result = apply_rounded_corners(cropped_image, corners, DPI, (255, 255, 255))
        w, h = result.size
        r_test = int(round(2.0 * DPI / 2.54))
        cx, cy = w - r_test, h - r_test
        arr = np.array(result)
        dark_in_corner = 0
        total_checked = 0
        # 用极坐标在 BR 圆弧 ±3px 上采样，避免大范围空扫
        for deg in range(0, 91, 1):
            theta = np.deg2rad(deg)
            for r_off in range(-3, 4):
                rx = cx + (r_test + r_off) * np.cos(theta)
                ry = cy + (r_test + r_off) * np.sin(theta)
                x, y = int(round(rx)), int(round(ry))
                if 0 <= x < w and 0 <= y < h:
                    total_checked += 1
                    if sum(arr[y, x, :3]) < 200:
                        dark_in_corner += 1
        assert dark_in_corner > 0, (
            f"apply_rounded_corners BR 2cm: 圆弧附近 {dark_in_corner}/{total_checked} "
            "深色像素，疑似边框丢失"
        )
