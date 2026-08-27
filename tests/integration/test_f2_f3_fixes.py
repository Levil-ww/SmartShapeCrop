"""
F2 / F3 回归测试

F2：requirements.txt 必须包含 pytesseract（否则干净安装后 OCR 静默失效）。
F3：sketch_parser 在 cv2 全量解码之前先做文件/尺寸校验（解炸弹），
    并设置了 PIL Image.MAX_IMAGE_PIXELS 兜底防御。

无需 Tesseract / 真实草图：F3 用“超大像素上限被拒”即可证明校验在解码前生效。
"""
import os
import sys
import tempfile

sys.path.insert(0, '.')

from PIL import Image


# ---------------------------------------------------------------------------
# F2：依赖声明
# ---------------------------------------------------------------------------

def test_requirements_lists_pytesseract():
    req_path = os.path.join(os.path.dirname(__file__), '..', 'requirements.txt')
    req_path = os.path.abspath(req_path)
    assert os.path.isfile(req_path), f"requirements.txt 不存在: {req_path}"
    content = open(req_path, encoding='utf-8').read().lower()
    assert 'pytesseract' in content, (
        "requirements.txt 缺少 pytesseract，干净安装后 OCR 会静默失效（F2 回归）"
    )


# ---------------------------------------------------------------------------
# F3：校验提前 + 解炸弹
# ---------------------------------------------------------------------------

def test_image_max_pixels_set():
    """模块导入后 PIL 像素上限应已设置（解炸弹二级防御）。"""
    from core.pool_designer import sketch_parser as sp
    # 模块在 PIL 可用时应已设置；PIL 不可用时 Image 为 None（跳过）。
    if sp.Image is not None:
        assert sp.Image.MAX_IMAGE_PIXELS and sp.Image.MAX_IMAGE_PIXELS >= 40_000_000, (
            f"Image.MAX_IMAGE_PIXELS 未设置或过低: {sp.Image.MAX_IMAGE_PIXELS}"
        )


def test_validate_rejects_oversized_image(monkeypatch):
    """validate_sketch_file 在解码前就按像素上限拒绝超大图（解炸弹主闸门）。"""
    from core.pool_designer import sketch_parser as sp
    # 把阈值临时调小，便于构造一张“超限但可廉价生成”的图
    monkeypatch.setattr(sp, '_SKETCH_MAX_PIXELS', 1_000_000)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'big.png')
        # 1500x1500 = 2.25M 像素 > 1M 上限，但生成成本很低
        Image.new('RGB', (1500, 1500), (255, 255, 255)).save(p, 'PNG')
        ok, reason = sp.validate_sketch_file(p)
        assert ok is False, f"超大图应被校验拒绝，但通过了: {reason}"
        assert '像素' in reason or 'MP' in reason, f"拒绝原因应点明像素过多: {reason}"


def test_parse_sketch_validates_before_decode(monkeypatch):
    """parse_sketch 对超大/非法文件应在调用 cv2 解码前返回（F3 校验提前）。

    通过把像素上限临时调小、构造超限图，确认入口在 decode 之前即以
    校验失败返回，而不是先 cv2.imread 全量解码导致 OOM/卡死。
    """
    from core.pool_designer import sketch_parser as sp

    calls = {'decode': 0}

    # 间谍：包裹 _load_image，记录是否被调用
    orig_load = sp._load_image

    def spy_load(path):
        calls['decode'] += 1
        return orig_load(path)

    monkeypatch.setattr(sp, '_load_image', spy_load)
    monkeypatch.setattr(sp, '_SKETCH_MAX_PIXELS', 1_000_000)

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'big.png')
        Image.new('RGB', (1500, 1500), (255, 255, 255)).save(p, 'PNG')
        res = sp.parse_sketch(p)
        assert res.success is False, "超大图应解析失败"
        assert res.message, "失败应带原因文字"
        assert calls['decode'] == 0, (
            "F3 回归：校验应在解码前拦截，但 _load_image 仍被调用了 "
            f"{calls['decode']} 次"
        )


def test_parse_sketch_invalid_extension_rejected_before_decode(monkeypatch):
    """非图片后缀在解码前即以校验失败返回（进一步证明校验顺序前移）。"""
    from core.pool_designer import sketch_parser as sp

    calls = {'decode': 0}
    orig_load = sp._load_image

    def spy_load(path):
        calls['decode'] += 1
        return orig_load(path)

    monkeypatch.setattr(sp, '_load_image', spy_load)

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'note.txt')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('我不是图片')
        res = sp.parse_sketch(p)
        assert res.success is False
        assert calls['decode'] == 0, (
            "非法图片应在解码前被校验拦截，但 _load_image 被调用了"
        )


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-v']))
