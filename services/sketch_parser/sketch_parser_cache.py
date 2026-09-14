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


_ALGO_VERSION = 11  # 2026-08-28: Step6横纵len==3分支不变量守护: inner异常(比边距小/方向锁两侧/ratio>2x)时先用outer-margins推导inner，再走后续流程；方向锁边距禁止比例缩放覆盖(花漾之约ih=7→35.5+margin_top=26.6回归修复)



_SKETCH_CACHE: dict = {}



_SKETCH_CACHE_MAX = 50



_SKETCH_CACHE_LOCK = threading.Lock()



_SKETCH_CONSISTENT_CACHE: dict = {}



_SKETCH_CONSISTENT_CACHE_MAX = 50



_SKETCH_CONSISTENT_CACHE_LOCK = threading.Lock()


def _get_image_content_fingerprint(image_path: str) -> str | None:
    """[H-08] 图像内容指纹：文件大小 + 前 64KB 的 sha256 摘要。

    缓存键仅依赖 mtime 时，文件内容不变但 mtime 变化会失效、mtime 相同但内容
    变化可能误用过期缓存。此处引入内容指纹（前 N KB 哈希，避免大图全量读取开销）
    作为缓存键补充，使缓存键同时反映内容特征。读取失败时返回 None（降级为仅 mtime）。
    """
    try:
        size = os.path.getsize(image_path)
        with open(image_path, 'rb') as f:
            head = f.read(64 * 1024)
        import hashlib
        digest = hashlib.sha256(head).hexdigest()
        return f"{size}:{digest}"
    except Exception:
        logger.debug("[_get_image_content_fingerprint] 忽略异常", exc_info=True)
        return None



def _get_cache_key(image_path: str, target_w: float, target_h: float) -> tuple:
    try:
        mtime = os.path.getmtime(image_path)
    except Exception:
        logger.debug("[_get_cache_key] 忽略异常", exc_info=True)
        mtime = 0
    fingerprint = _get_image_content_fingerprint(image_path)
    return (image_path, mtime, fingerprint, round(target_w, 1), round(target_h, 1), _ALGO_VERSION)



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
    fingerprint = _get_image_content_fingerprint(image_path)
    return (image_path, mtime, fingerprint, _ALGO_VERSION)



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

