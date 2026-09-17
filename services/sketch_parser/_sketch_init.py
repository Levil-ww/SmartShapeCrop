"""sketch_parser 包共享初始化 —— PIL 解压炸弹防护（全部子模块唯一设置点）。

[F3 修复] 解炸弹二级防御：为 PIL 设置像素上限，防止恶意/超大图在全量解码时
OOM。主闸门是 parse_sketch 入口的 validate_sketch_file（40MP 头信息校验，
见 _SKETCH_MAX_PIXELS），本模块作为任何 PIL 全量加载路径的兜底网；
上限与 core/image_ops.py 保持一致（2 亿像素 ≈ 14142×14142）。

用 try 包裹：包内各模块主解码走 cv2，PIL 不可用时降级也不影响导入。
子模块统一以 `from ._sketch_init import Image` 引入防护后的 Image。
"""

from __future__ import annotations

import logging

try:  # pragma: no cover - 依赖环境差异
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 200_000_000
except Exception:
    logging.getLogger(__name__).debug("[module] PIL 导入失败，已降级", exc_info=True)
    Image = None  # type: ignore
