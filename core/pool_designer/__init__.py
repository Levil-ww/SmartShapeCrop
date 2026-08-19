"""水池设计器子包（独立模块，不影响圆角裁剪工具）。

子模块：
    sketch_parser: 尺寸草图解析（多层策略：复杂度评估 + 几何检测 + 数字 OCR）。
"""

from .sketch_parser import (
    SketchParseResult,
    parse_sketch,
    validate_sketch_file,
)

__all__ = [
    "SketchParseResult",
    "parse_sketch",
    "validate_sketch_file",
]