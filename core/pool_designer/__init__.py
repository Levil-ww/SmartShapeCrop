"""水池设计器子包（独立模块，不影响圆角裁剪工具）。

子模块：
    sketch_parser: 尺寸草图解析（多层策略：复杂度评估 + 几何检测 + 数字 OCR）。
    sketch_parser_multihole: 多洞矩形嵌套草图解析（9步法 + 空间位置判定 + 箭头方向）。
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
# [2026-08-29 新增] 多洞解析公开符号（可选导入；对外主入口仍是 parse_sketch 自动分流）
from .sketch_parser_multihole import (
    HoleInfo,
    MultiHoleParseResult,
    try_parse_multi_hole,
)

__all__ = [
    "SketchParseResult",
    "parse_sketch",
    "validate_sketch_file",
    "LSketchParseResult",
    "parse_lshape_sketch",
    # 多洞扩展
    "HoleInfo",
    "MultiHoleParseResult",
    "try_parse_multi_hole",
]