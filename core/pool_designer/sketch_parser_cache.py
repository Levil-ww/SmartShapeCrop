"""尺寸草图解析器 —— 缓存层（结果与自洽解缓存）（由 sketch_parser.py 拆分而来，facade 模式）。

原文件 core/pool_designer/sketch_parser.py 为编排层 facade，
本模块只包含 缓存层（结果与自洽解缓存） 相关的实现，逻辑与原文件完全一致。
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

try:  # pragma: no cover - 依赖环境差异
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 200_000_000
except Exception:
    logging.getLogger(__name__).debug("[module] PIL 导入失败，已降级", exc_info=True)
    Image = None  # type: ignore

logger = logging.getLogger(__name__)


_ALGO_VERSION = 8  # 2026-08-28: Phase3 num_tokens放宽单位后缀+Phase3绑定走_try_bind+Phase4移除短边*0.5硬阈值小数改写(中古雨林74→7.4)



_SKETCH_CACHE: dict = {}



_SKETCH_CACHE_MAX = 50



_SKETCH_CACHE_LOCK = threading.Lock()



_SKETCH_CONSISTENT_CACHE: dict = {}



_SKETCH_CONSISTENT_CACHE_MAX = 50



_SKETCH_CONSISTENT_CACHE_LOCK = threading.Lock()



def _get_cache_key(image_path: str, target_w: float, target_h: float) -> tuple:
    try:
        mtime = os.path.getmtime(image_path)
    except Exception:
        logger.debug("[_get_cache_key] 忽略异常", exc_info=True)
        mtime = 0
    return (image_path, mtime, round(target_w, 1), round(target_h, 1), _ALGO_VERSION)



def _get_cached_result(image_path: str, target_w: float, target_h: float):
    with _SKETCH_CACHE_LOCK:
        key = _get_cache_key(image_path, target_w, target_h)
        cached = _SKETCH_CACHE.get(key)
        if cached is not None:
            logger.info(f"[sketch_parser] 缓存命中：{image_path}")
            import copy
            return copy.deepcopy(cached)
    return None



def _store_cached_result(image_path: str, target_w: float, target_h: float, result):
    with _SKETCH_CACHE_LOCK:
        key = _get_cache_key(image_path, target_w, target_h)
        if len(_SKETCH_CACHE) >= _SKETCH_CACHE_MAX:
            oldest = next(iter(_SKETCH_CACHE))
            _SKETCH_CACHE.pop(oldest, None)
        import copy
        _SKETCH_CACHE[key] = copy.deepcopy(result)



def _get_consistent_cache_key(image_path: str) -> tuple:
    try:
        mtime = os.path.getmtime(image_path)
    except Exception:
        logger.debug("[_get_consistent_cache_key] 忽略异常", exc_info=True)
        mtime = 0
    return (image_path, mtime, _ALGO_VERSION)



def _get_consistent_cached_result(image_path: str):
    with _SKETCH_CONSISTENT_CACHE_LOCK:
        key = _get_consistent_cache_key(image_path)
        cached = _SKETCH_CONSISTENT_CACHE.get(key)
        if cached is not None:
            logger.info(f"[sketch_parser] 自洽解缓存命中：{image_path}")
            import copy
            return copy.deepcopy(cached)
    return None



def _store_consistent_cached_result(image_path: str, result):
    with _SKETCH_CONSISTENT_CACHE_LOCK:
        key = _get_consistent_cache_key(image_path)
        if len(_SKETCH_CONSISTENT_CACHE) >= _SKETCH_CONSISTENT_CACHE_MAX:
            oldest = next(iter(_SKETCH_CONSISTENT_CACHE))
            _SKETCH_CONSISTENT_CACHE.pop(oldest, None)
        import copy
        _SKETCH_CONSISTENT_CACHE[key] = copy.deepcopy(result)

