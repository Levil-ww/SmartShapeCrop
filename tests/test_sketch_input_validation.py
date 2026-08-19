"""
测试：core.pool_designer.sketch_parser.validate_sketch_file 草图输入校验

覆盖送入 OCR 管线前的输入门禁：存在性 / 扩展名 / 文件大小 / 像素数 / 头部可读性。
历史问题：用户可拖入任意大图/损坏图，导致 OCR 长时间卡死或内存爆炸。
用 PIL 现造小图 + monkeypatch 阈值，覆盖各拒绝分支与通过分支，无需提交二进制夹具。
"""
import os

import pytest
from PIL import Image

import core.pool_designer.sketch_parser as sp
from core.pool_designer.sketch_parser import validate_sketch_file


def _make_png(path: str, w: int = 2, h: int = 2):
    """生成一个纯色小 PNG（用于通过/像素分支测试）。"""
    Image.new("RGB", (w, h), "white").save(path)


# ---------------------------------------------------------------------------
# 通过分支
# ---------------------------------------------------------------------------

class TestValidSketch:
    def test_valid_small_png(self, tmp_path):
        """合规小图通过校验。"""
        p = tmp_path / "ok.png"
        _make_png(str(p))
        ok, reason = validate_sketch_file(str(p))
        assert ok is True
        assert reason == ""

    def test_accepts_all_whitelisted_ext(self, tmp_path):
        """白名单内各扩展名均不被格式拒绝（内容合法时）。"""
        for ext in (".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"):
            p = tmp_path / f"img{ext}"
            # 用 PNG 内容保存为其他扩展名——PIL 仍可读头部
            Image.new("RGB", (2, 2), "white").save(str(p))
            ok, _ = validate_sketch_file(str(p))
            assert ok is True, f"{ext} 应被接受"


# ---------------------------------------------------------------------------
# 拒绝分支
# ---------------------------------------------------------------------------

class TestRejectedSketch:
    def test_nonexistent_path(self):
        ok, reason = validate_sketch_file("/no/such/file.png")
        assert ok is False
        assert "不存在" in reason

    def test_unsupported_format(self, tmp_path):
        p = tmp_path / "notes.txt"
        p.write_text("hello", encoding="utf-8")
        ok, reason = validate_sketch_file(str(p))
        assert ok is False
        assert "格式" in reason

    def test_empty_file(self, tmp_path):
        """零字节文件在大小检查阶段即被拒。"""
        p = tmp_path / "empty.png"
        p.write_bytes(b"")
        ok, reason = validate_sketch_file(str(p))
        assert ok is False
        assert "空" in reason

    def test_corrupt_image(self, tmp_path):
        """非图片内容伪装成 .png：PIL 头部读取失败被拒。"""
        p = tmp_path / "fake.png"
        p.write_bytes(b"not an image at all, just text")
        ok, reason = validate_sketch_file(str(p))
        assert ok is False
        assert "无法读取" in reason or "损坏" in reason

    def test_oversize_file_rejected(self, tmp_path, monkeypatch):
        """文件超过大小上限被拒（monkeypatch 降低阈值）。"""
        p = tmp_path / "big.png"
        _make_png(str(p))
        monkeypatch.setattr(sp, "_SKETCH_MAX_FILE_MB", 0)  # 上限 0 字节
        ok, reason = validate_sketch_file(str(p))
        assert ok is False
        assert "过大" in reason

    def test_too_many_pixels_rejected(self, tmp_path, monkeypatch):
        """像素数超上限被拒（monkeypatch 降低阈值）。"""
        p = tmp_path / "huge.png"
        _make_png(str(p), w=2, h=2)  # 4 像素
        monkeypatch.setattr(sp, "_SKETCH_MAX_PIXELS", 3)  # 上限 3 像素
        ok, reason = validate_sketch_file(str(p))
        assert ok is False
        assert "像素" in reason

    def test_thresholds_restored(self, tmp_path, monkeypatch):
        """monkeypatch 不影响后续用例：阈值已还原，合规图仍通过。"""
        p = tmp_path / "ok2.png"
        _make_png(str(p))
        ok, _ = validate_sketch_file(str(p))
        assert ok is True
