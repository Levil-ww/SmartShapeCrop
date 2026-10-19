"""模式约束不改变原有单洞/多洞算法，仅限制分流和缓存复用。"""
import importlib

import numpy as np
import pytest

sp = importlib.import_module('services.sketch_parser.sketch_parser')
mh = importlib.import_module('services.sketch_parser.sketch_parser_multihole')


@pytest.fixture
def parser(monkeypatch, tmp_path):
    path = str(tmp_path / 'sketch.png')
    monkeypatch.setattr(sp, 'validate_sketch_file', lambda p: (True, ''))
    monkeypatch.setattr(sp, '_get_cached_result', lambda *a, **kw: None)
    monkeypatch.setattr(sp, '_get_consistent_cached_result', lambda *a, **kw: None)
    monkeypatch.setattr(sp, '_store_cached_result', lambda *a, **kw: None)
    monkeypatch.setattr(sp, '_safe_import_cv2', lambda: object())
    monkeypatch.setattr(sp, '_load_image', lambda p: (np.zeros((8, 8)), None))
    monkeypatch.setattr(sp, '_to_gray', lambda img: img)
    monkeypatch.setattr(sp, '_safe_import_tesseract', lambda: None)
    monkeypatch.setattr(sp, '_enhance_colored_ink', lambda *a, **kw: None)
    monkeypatch.setattr(sp, '_7step_parse', lambda *a, **kw: {
        'success': True, 'outer_w': 100, 'outer_h': 60,
        'inner_w': 80, 'inner_h': 40})
    return path


def test_single_skips_multi_classifier(parser, monkeypatch):
    def unexpected(*a, **kw):
        pytest.fail('单洞模式不应调用多洞识别')
    monkeypatch.setattr(mh, 'try_parse_multi_hole', unexpected)
    result = sp.parse_sketch(parser, hole_mode='single')
    assert result.success and not result.is_multi_hole


@pytest.mark.parametrize('consistent', [False, True])
@pytest.mark.parametrize('mode', ['single', 'multi'])
def test_conflicting_cache_is_not_reused(parser, monkeypatch, mode, consistent):
    old = sp.SketchParseResult(success=True, is_multi_hole=(mode == 'single'))
    cache = '_get_consistent_cached_result' if consistent else '_get_cached_result'
    monkeypatch.setattr(sp, cache, lambda *a, **kw: old)
    monkeypatch.setattr(mh, 'try_parse_multi_hole', lambda *a, **kw: {
        'success': True, 'holes': [object(), object()]})
    result = sp.parse_sketch(parser, hole_mode=mode)
    assert result is not old
    assert result.success
    assert result.is_multi_hole == (mode == 'multi')


@pytest.mark.parametrize('raises', [False, True])
def test_multi_failure_never_falls_back_to_single(parser, monkeypatch, raises):
    def multi(*a, **kw):
        if raises:
            raise RuntimeError('failed')
        return {'success': False, '_fallback_to_single_hole': True,
                'message': '未检测到两个洞'}
    monkeypatch.setattr(mh, 'try_parse_multi_hole', multi)
    result = sp.parse_sketch(parser, hole_mode='multi')
    assert not result.success
    assert result.message


def test_default_auto_keeps_single_fallback(parser, monkeypatch):
    monkeypatch.setattr(mh, 'try_parse_multi_hole', lambda *a, **kw: {
        'success': False, '_fallback_to_single_hole': True})
    result = sp.parse_sketch(parser)
    assert result.success and not result.is_multi_hole


@pytest.mark.parametrize('consistent', [False, True])
def test_cache_separates_auto_single_and_multi(tmp_path, consistent):
    from services.sketch_parser import sketch_parser_cache as cache
    path = str(tmp_path / 'cache.png')
    results = {mode: sp.SketchParseResult(success=True, message=mode)
               for mode in ('auto', 'single', 'multi')}
    for mode, result in results.items():
        if consistent:
            cache._store_consistent_cached_result(path, result, hole_mode=mode)
        else:
            cache._store_cached_result(path, 100, 60, result, hole_mode=mode)
    for mode in results:
        if consistent:
            cached = cache._get_consistent_cached_result(path, hole_mode=mode)
        else:
            cached = cache._get_cached_result(path, 100, 60, hole_mode=mode)
        assert cached.message == mode
        assert cached is not results[mode]
