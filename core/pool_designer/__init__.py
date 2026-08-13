"""水池设计器子包（独立模块，不影响圆角裁剪工具）。

子模块：
    sketch_parser: 尺寸草图解析（两嵌套矩形的几何检测 + 可选 OCR）。
"""

from .sketch_parser import (
    SketchParseResult,
    parse_sketch_geometry,
    parse_sketch,
)

__all__ = [
    "SketchParseResult",
    "parse_sketch_geometry",
    "parse_sketch",
]
