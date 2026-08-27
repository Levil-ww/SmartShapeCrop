"""
tests/test_f5_f14_fixes.py
F5–F14（中危）修复的回归测试（约束：不改变功能逻辑，仅行为中性修复）：

  F5  Tesseract 缺失不降级几何估算   → 更正 README/packageV2.0.py 误导文案
  F6  _PARSE_TIMEOUT_SEC 从未使用    → image_to_data(timeout=) 单次硬超时 + 7步法总 deadline
  F7  死代码 _fill_corner_boundary_pixels → 已删除，README 不再声称其运行
  F8  carve_corner_on_mask inverse=True 未测试 → 补充 inverse 路径行为断言
  F9  椭圆模式多层边框退化矩形       → 渲染层 warning + 文档明确（行为不变）
  F10 依赖 opencv-python             → 改为 opencv-python-headless
  F11 陈旧 .spec 硬编码绝对路径      → 已删除（packageV2.0.py 自动生成）
  F12 硬编码绝对路径散落             → process_image.py 相对路径/参数化；config.py 去盘符
  F13 simple_resize 标注“推荐”       → 文案改为“拉伸会变形” + 行为断言
  F14 大量裸 except 静默吞错         → 全部注入 logger.debug（AST 扫描为零）
"""
from __future__ import annotations

import ast
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _read(rel: str) -> str:
    return (PROJECT_ROOT / rel).read_text(encoding='utf-8', errors='replace')


# ---------------------------------------------------------------------------
# F5: 文档不再声称“OCR 降级为几何估算/几何回退”
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('rel', [
    'README.md',
    'packageV2.0.py',
])
def test_f5_no_misleading_geometry_fallback_docs(rel):
    text = _read(rel)
    # 只查"肯定式"误导文案（"不会降级为几何估算"这类否定式澄清是正确文案，不命中）
    for bad in ('降级为几何回退', '自动降级为几何估算', '自动回退到几何比例',
                '回退到几何比例计算', '自动回退几何比例', '缺引擎时自动回退'):
        assert bad not in text, f'{rel} 仍含误导文案: {bad!r}'
    # 应明确“未安装则识别失败”
    assert '几何' in text  # 讨论仍存在（用于澄清），但不再声称可降级


# ---------------------------------------------------------------------------
# F6: 真实超时
# ---------------------------------------------------------------------------
def test_f6_all_ocr_calls_pass_timeout():
    """4 个 image_to_data 调用点都必须带 timeout=_PARSE_TIMEOUT_SEC。"""
    src = _read('core/pool_designer/sketch_parser.py')
    # 统计真正调用点（tesseract.image_to_data( 开头），不含 docstring
    call_sites = [m.start() for m in re.finditer(r'tesseract\.image_to_data\(', src)]
    assert len(call_sites) >= 4, f'OCR 调用点异常: {len(call_sites)}'
    for pos in call_sites:
        window = src[pos:pos + 400]
        assert 'timeout=_PARSE_TIMEOUT_SEC' in window, (
            f'image_to_data 调用缺少 timeout（位置 {pos}）')


def test_f6_ocr_timeout_is_swallowed_and_returns_empty():
    """单次 OCR 超时（TimeoutExpired）被捕获 → 该变体跳过，返回空结果，不抛异常。"""
    from core.pool_designer import sketch_parser as sp

    class FakeTess:
        class Output:
            DICT = 'dict'

        def image_to_data(self, **kw):
            raise __import__('subprocess').TimeoutExpired('tesseract', 1)

    cv2 = sp._safe_import_cv2()
    if cv2 is None:
        pytest.skip('OpenCV 不可用')
    gray = np.full((100, 150), 200, dtype=np.uint8)
    res = sp._multi_scale_ocr_scan(cv2, FakeTess(), gray, fast_mode=True)
    assert res == []  # 超时被吞，绝不抛异常、不挂起


def test_f6_7step_deadline_returns_timeout(monkeypatch):
    """总 deadline 过期 → 7 步法在 OCR 阶段前返回明确“超时”失败。"""
    from core.pool_designer import sketch_parser as sp

    fake_rects = [(30, 30, 270, 170), (60, 60, 240, 140)]
    monkeypatch.setattr(sp, '_find_all_rectangles', lambda *a, **k: list(fake_rects))
    monkeypatch.setattr(sp, '_select_best_nested_pair', lambda r: (r[0], r[1]))
    monkeypatch.setattr(sp, '_compute_gaps', lambda *a: {})
    monkeypatch.setattr(sp, '_divide_8_zones', lambda *a: {})

    res = sp._7step_parse(None, np.zeros((200, 300), np.uint8),
                          np.zeros((200, 300, 3), np.uint8), None,
                          deadline=time.monotonic() - 1)
    assert res['success'] is False
    assert '超时' in res['message']


# ---------------------------------------------------------------------------
# F7: 死代码已删除
# ---------------------------------------------------------------------------
def test_f7_dead_function_removed():
    src = _read('core/corner/algorithm.py')
    assert '_fill_corner_boundary_pixels' not in src, '死代码函数应已删除'
    readme = _read('README.md')
    # README 中允许以“已删除”历史说明的方式提及，但不允许声称其仍在运行
    for m in re.finditer(r'_fill_corner_boundary_pixels', readme):
        line_start = readme.rfind('\n', 0, m.start()) + 1
        line_end = readme.find('\n', m.end())
        line = readme[line_start:line_end if line_end != -1 else len(readme)]
        assert ('已删除' in line or '已移除' in line), \
            f'README 不应声称该函数仍运行: {line.strip()!r}'


# ---------------------------------------------------------------------------
# F8: inverse=True 圆角路径行为
# ---------------------------------------------------------------------------
def test_f8_carve_inverse_true_and_false_behavior():
    from core.corner.algorithm import carve_corner_on_mask

    w = h = 100
    rect = (10, 10, 80, 80)          # (x, y, w, h)
    corners = {'tl': 20, 'tr': 20, 'bl': 20, 'br': 20}
    cx, cy, r = 30, 30, 20           # TL 圆心
    # 45° 方向、距圆心 ≤ r 的点（圆内）
    pt_arc = (16, 16)
    # 正方形内、圆外点（尖角部分）
    pt_corner = (12, 12)

    # inverse=False（默认）：尖角切掉(0)，圆弧保留(255)
    m_false = Image.new('L', (w, h), 255)
    carve_corner_on_mask(m_false, rect, corners, canvas_size=(w, h),
                         fill_value=255, inverse=False)
    a_false = np.array(m_false)
    assert a_false[pt_corner[1], pt_corner[0]] == 0, '非 inverse：尖角应被切掉'
    assert a_false[pt_arc[1], pt_arc[0]] == 255, '非 inverse：圆弧应保留'

    # inverse=True：尖角填回(255)，圆弧挖空(0)
    m_true = Image.new('L', (w, h), 255)
    carve_corner_on_mask(m_true, rect, corners, canvas_size=(w, h),
                         fill_value=255, inverse=True)
    a_true = np.array(m_true)
    assert a_true[pt_corner[1], pt_corner[0]] == 255, 'inverse：尖角部分应填回'
    assert a_true[pt_arc[1], pt_arc[0]] == 0, 'inverse：圆弧应挖空'
    assert a_true[50, 50] == 255, '矩形中心不受影响'


# ---------------------------------------------------------------------------
# F9: 椭圆模式 + 边框 → 警告 + 行为不变
# ---------------------------------------------------------------------------
def test_f9_ellipse_with_borders_warns_but_keeps_behavior(caplog):
    from core.geometry import CropDesign, BorderLayer, compute_border_bands

    d = CropDesign(mode='ellipse_hole', canvas_w_cm=30.0, canvas_h_cm=20.0, dpi=150)
    d.borders.append(BorderLayer(offset_cm=0.5, fill_type='solid', color=(255, 0, 0)))
    with caplog.at_level(logging.WARNING, logger='core.geometry'):
        bands = compute_border_bands(d)
    assert len(bands) >= 1, '行为不变：仍按现有逻辑产出边框带'
    assert any('椭圆' in r.message and '边框' in r.message for r in caplog.records), \
        '应产生“椭圆模式不支持多层边框”的 warning'


# ---------------------------------------------------------------------------
# F10: 依赖清单使用 opencv-python-headless
# ---------------------------------------------------------------------------
def test_f10_requirements_use_headless_opencv():
    text = _read('requirements.txt')
    assert 'opencv-python-headless' in text
    # 不允许同时存在完整版（含注释中的说明除外，不能有裸 "opencv-python>="）
    assert not re.search(r'^opencv-python[^-\n]*>=', text, re.M), \
        '不应再依赖完整版 opencv-python'


# ---------------------------------------------------------------------------
# F11: 陈旧硬编码 .spec 已删除
# ---------------------------------------------------------------------------
def test_f11_stale_spec_removed():
    assert not (PROJECT_ROOT / '智能裁剪设计器V2.0.spec').exists(), \
        '陈旧 spec 应已删除（打包统一走 packageV2.0.py）'
    # 保留的 SmartShapeCrop.spec 必须是相对路径（不硬编码作者盘符）
    legacy = PROJECT_ROOT / 'SmartShapeCrop' / 'SmartShapeCrop.spec'
    if legacy.exists():
        text = legacy.read_text(encoding='utf-8', errors='replace')
        assert 'D:/SmartShapeCrop' not in text and 'D:\\SmartShapeCrop' not in text


# ---------------------------------------------------------------------------
# F12: 硬编码绝对路径清除
# ---------------------------------------------------------------------------
def test_f12_no_hardcoded_author_paths():
    proc = _read('process_image.py')
    assert 'D:\\SmartShapeCrop' not in proc and 'D:/SmartShapeCrop' not in proc, \
        'process_image.py 不应硬编码 D:\\SmartShapeCrop'
    assert 'psd_demo' in proc  # 仍使用相对脚本目录的 psd_demo
    cfg = _read('core/config.py')
    for drive in (r'D:\Tesseract-OCR', r'E:\Tesseract-OCR', r'F:\Tesseract-OCR'):
        assert drive not in cfg, f'config.py 不应硬编码盘符路径 {drive}'


# ---------------------------------------------------------------------------
# F13: simple_resize 文案与拉伸行为
# ---------------------------------------------------------------------------
def test_f13_simple_resize_description_mentions_distortion():
    from core.image_cropper import get_mode_description
    desc = get_mode_description('simple_resize')
    assert '变形' in desc, '描述应明确“拉伸会变形”'
    assert '推荐' not in desc, '不应再标注“推荐”'

    panel = _read('gui/cropper_panel.py')
    assert '简单缩放（推荐）' not in panel, 'UI 下拉不应再标注“推荐”'
    assert '简单缩放（拉伸填满）' in panel


def test_f13_simple_resize_stretches_to_fill():
    """行为锁定：simple_resize 直接拉伸填满目标（源图比例不同时变形）。"""
    from core.image_cropper import crop_image, CropConfig

    with tempfile.TemporaryDirectory() as td:
        src = Image.new('RGB', (200, 100), (255, 0, 0))  # 2:1
        src_path = os.path.join(td, 'src.png')  # PNG 无损，避免 JPEG 压缩把 255 变成 254
        src.save(src_path)
        out = crop_image(CropConfig(
            src_path=src_path, target_w_cm=5.0, target_h_cm=5.0,
            mode='simple_resize', dpi=150, bg_color=(255, 255, 255)))
        assert out.size == (295, 295), f'应拉伸填满 5cm@150dpi=295px，实际 {out.size}'
        arr = np.array(out)
        assert tuple(arr[0, 0]) == (255, 0, 0)


# ---------------------------------------------------------------------------
# F14: 完全静默裸 except 归零
# ---------------------------------------------------------------------------
def _silent_except_count(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body = node.body
        has_log = any(isinstance(s, ast.Expr) and isinstance(s.value, ast.Call)
                      and isinstance(s.value.func, ast.Attribute)
                      and s.value.func.attr in ('debug', 'info', 'warning', 'error',
                                                'exception', 'critical')
                      for s in body)
        has_ret = any(isinstance(s, (ast.Return, ast.Raise)) for s in body)
        uses_e = bool(node.name) and any(
            isinstance(sub, ast.Name) and sub.id == node.name
            for s in body for sub in ast.walk(s))
        if not has_log and not has_ret and not uses_e:
            n += 1
    return n


def test_f14_no_silent_bare_except_in_hot_paths():
    for rel in ('core/pool_designer/sketch_parser.py', 'core/image_ops.py'):
        n = _silent_except_count(PROJECT_ROOT / rel)
        assert n == 0, f'{rel} 仍存在 {n} 处完全静默的裸 except（应记录日志或显式处理）'


def test_f14_exif_except_logs():
    """image_ops.load_image_rgb 的 EXIF except 必须记录日志（不再 pass 静默）。"""
    src = _read('core/image_ops.py')
    # 找到 exif_transpose 的 try/except 块
    m = re.search(r'except Exception as e:.*?logger\.debug\(f?"\[image_ops\] EXIF', src, re.S)
    assert m, 'EXIF except 应使用 as e 并记录 logger.debug'
