"""水池设计器子包（独立模块，不影响圆角裁剪工具）。

子模块：
    sketch_parser: 尺寸草图解析（多层策略：复杂度评估 + 几何检测 + 数字 OCR）。
    lshape_sketch_parser: L 形挖角草图解析（两矩形减法推断 + 凹角顶点检测 + 标签几何归属）。
"""

from .sketch_parser import (
    SketchParseResult,
    parse_sketch,
    validate_sketch_file,
)
from .lshape_sketch_parser import (
    LSketchParseResult,
    parse_lshape_sketch,
)

__all__ = [
    "SketchParseResult",
    "parse_sketch",
    "validate_sketch_file",
    "LSketchParseResult",
    "parse_lshape_sketch",
]