"""
测试：core.image_ops 的 P2 级技术债修复

覆盖（仅技术债验证，不改功能语义）：
  - [P2-15] save_jpg 显式 4:4:4 色度采样（subsampling=0），避免 JPEG 默认 4:2:0
            对细线/深色素材引入色度模糊与偏色（行为级验证：解析 SOF 采样因子）；
  - [P2-14] _looks_like_tile 负向后缀正则：'hua'/'zhuan' 不再误命中
            "huang"/"huan"/"zhuang" 等拼音前缀词；tile/pattern/花砖 语义不变。
"""
import os
import struct

import pytest
from PIL import Image

from core.image_ops import _looks_like_tile, save_jpg


def _jpeg_sampling_factors(path: str):
    """解析 JPEG SOF 标记，返回各分量采样因子列表 [(h, v), ...]；找不到返回 None"""
    with open(path, "rb") as f:
        data = f.read()
    i = 2  # 跳过 SOI
    while i + 9 < len(data):
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker == 0xD8 or 0xD0 <= marker <= 0xD9:
            i += 2
            continue
        (seg_len,) = struct.unpack(">H", data[i + 2:i + 4])
        # SOF0-SOF15（非 DHT/DAC/RST/SOI/EOI/TEM 类）
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            ncomp = data[i + 2 + 7]  # precision(1)+height(2)+width(2) 之后是 Nf
            factors = []
            off = i + 2 + 8
            for _ in range(ncomp):
                hv = data[off + 1]
                factors.append((hv >> 4, hv & 0x0F))
                off += 3
            return factors
        i += 2 + seg_len
    return None


class TestSaveJpgSubsampling:
    """[P2-15] 保存 JPG 时显式 4:4:4（subsampling=0）"""

    def test_save_jpg_writes_444_sampling(self, tmp_path):
        img = Image.new("RGB", (64, 64), (200, 30, 30))
        out = os.path.join(str(tmp_path), "out_444.jpg")
        save_jpg(img, out, quality=95)
        assert os.path.isfile(out)
        factors = _jpeg_sampling_factors(out)
        assert factors is not None, "JPEG 中未找到 SOF 标记"
        assert all(h == 1 and v == 1 for h, v in factors), (
            f"期望 4:4:4（所有分量 1x1 采样），实际 {factors}"
        )

    def test_save_jpg_keeps_quality_and_dpi(self, tmp_path):
        img = Image.new("RGB", (32, 32), (10, 10, 10))
        out = os.path.join(str(tmp_path), "out_dpi.jpg")
        save_jpg(img, out, quality=90, dpi=300)
        with Image.open(out) as reopened:
            assert reopened.info.get("dpi") == (300, 300)

    def test_save_jpg_output_reopenable(self, tmp_path):
        """回归：保存文件可重开且尺寸正确"""
        img = Image.new("RGB", (48, 32), (5, 120, 200))
        out = os.path.join(str(tmp_path), "out_reopen.jpg")
        save_jpg(img, out)
        with Image.open(out) as reopened:
            assert reopened.size == (48, 32)


class TestLookLikeTileNegativeRegex:
    """[P2-14] 'hua'/'zhuan' 负向后缀正则，排除拼音前缀词误命中"""

    @pytest.mark.parametrize("name", [
        "material_tile.jpg", "marble_pattern.png", "花砖素材.jpg",
        "hua-01.png", "zhuan-01.png",
    ])
    def test_positive_hits(self, name):
        # P2-14 只约束 ASCII 'hua'/'zhuan' 子串语义；纯中文名不含 ASCII 子串，
        # 由 tile/pattern/花砖 分支覆盖，不在此处断言。
        assert _looks_like_tile(name) is True, name

    @pytest.mark.parametrize("name", [
        "huangshan_photo.jpg",   # huang 含 'hua' → 必须排除
        "huan_yue_photo.jpg",    # huan 含 'hua' → 必须排除
        "zhuangyuanmei.jpg",     # zhuang 含 'zhuan' → 必须排除
        "zhu_photo.jpg",         # 裸 'zhu' 不含 'zhuan' → 不命中
        "plain_photo.jpg",
    ])
    def test_negative_hits(self, name):
        assert _looks_like_tile(name) is False, name

    def test_tile_semantics_unchanged(self):
        """tile/pattern/花砖 子串语义不变（纯中文名也命中）"""
        assert _looks_like_tile("花砖背景.jpg") is True    # 花砖 分支
        assert _looks_like_tile("tile-01.jpg") is True     # tile 分支
        # 无 ASCII 子串且非"花砖"的纯中文名 → 不命中（原语义同样不命中）
        assert _looks_like_tile("瓷砖背景.jpg") is False
        assert _looks_like_tile("砖纹背景.jpg") is False
        assert _looks_like_tile("大理石花纹.jpg") is False