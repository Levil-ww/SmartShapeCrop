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
from .composite_sketch_parser import (
    CompositeSketchParseResult,
    parse_composite_sketch,
)


def parse_shape_sketch(image_path: str, *, mode: str = 'rect_hole',
                       target_outer_w_cm: float = 0.0,
                       target_outer_h_cm: float = 0.0, **kwargs):
    """按形状模式分派草图解析；旧模式保持原有入口和行为。"""
    if mode == 'rect_lshape_hole':
        return parse_composite_sketch(
            image_path,
            target_outer_w_cm=target_outer_w_cm,
            target_outer_h_cm=target_outer_h_cm,
            **kwargs,
        )
    if mode == 'rect_lshape':
        return parse_lshape_sketch(
            image_path,
            target_outer_w_cm=target_outer_w_cm,
            target_outer_h_cm=target_outer_h_cm,
            **kwargs,
        )
    return parse_sketch(image_path, **kwargs)
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
    "CompositeSketchParseResult",
    "parse_composite_sketch",
    "parse_shape_sketch",
    # 多洞扩展
    "HoleInfo",
    "MultiHoleParseResult",
    "try_parse_multi_hole",
]
