"""尺寸草图解析器 —— 基础层（文本规范化 / 文件校验 / 公共常量）（由 sketch_parser.py 拆分而来，facade 模式）。

原文件 core/pool_designer/sketch_parser.py 为编排层 facade，
本模块只包含 基础层（文本规范化 / 文件校验 / 公共常量） 相关的实现，逻辑与原文件完全一致。
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


_FW_HW_TRANSLATION = str.maketrans({
    '０': '0', '１': '1', '２': '2', '３': '3', '４': '4',
    '５': '5', '６': '6', '７': '7', '８': '8', '９': '9',
    '．': '.', '，': '.', '、': '.', '。': '.',
    '　': ' ',   # 全角空格→半角
})



_SKETCH_ACCEPT_EXT = {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp'}



_SKETCH_MAX_FILE_MB = 50



_SKETCH_MAX_PIXELS = 40_000_000



_PARSE_TIMEOUT_SEC = 20



def _normalize_ocr_text(text: str) -> str:
    """OCR 识别文本规范化：全角→半角，去除多余空白。"""
    if not text:
        return ''
    return text.translate(_FW_HW_TRANSLATION).strip()



def validate_sketch_file(path: str) -> tuple:
    if not path or not os.path.isfile(path):
        return False, "文件不存在"
    ext = os.path.splitext(path)[1].lower()
    if ext not in _SKETCH_ACCEPT_EXT:
        return False, f"不支持的图片格式：{ext}"
    size = os.path.getsize(path)
    if size == 0:
        return False, "文件为空"
    if size > _SKETCH_MAX_FILE_MB * 1024 * 1024:
        return False, f"文件过大（{size/1024/1024:.1f}MB > {_SKETCH_MAX_FILE_MB}MB）"
    try:
        from PIL import Image as PILImage
        with PILImage.open(path) as im:
            w, h = im.size
    except Exception as e:
        return False, f"图片无法读取（可能已损坏）：{e}"
    if w <= 0 or h <= 0:
        return False, "图片尺寸无效"
    if w * h > _SKETCH_MAX_PIXELS:
        return False, (f"图片像素过多（{w}×{h}≈{w*h/1e6:.1f}MP），OCR会卡，请缩小")
    return True, ""

