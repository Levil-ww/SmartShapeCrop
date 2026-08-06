"""
core/corner/__init__.py
圆角处理子包：统一圆角 mask 算法 + 边框层检测 + 圆弧上的边框重绘。

子模块：
  - algorithm: 单步扇形切割算法（CORNER_ANGLES / get_corner_square /
               get_corner_pieslice_bbox / carve_corner_on_mask）。
               所有圆角处理必须经过本模块，确保 image_cropper.py、
               geometry.py、process_image.py 三处圆角逻辑完全一致。
  - detection: 边框层自动检测（_detect_border_layers / detect_nested_rect_layers
               / _scan_edge_boundaries / _get_border_layers_robust）。
               两类检测使用不同阈值是有意为之，分别处理不同场景。
  - sector_render: 圆角弧线上的多层边框重绘（_redraw_border_on_corner 等），
                   采用同心圆弧设计，与 carve_corner_on_mask 的 pieslice
                   角度完全一致。

向后兼容：原 core/rounded_corner.py 已改为薄重导出 shim，旧导入路径继续可用。
image_cropper.py 中的相关函数也已改为从此子包导入。
"""
from .algorithm import (
    CORNER_ANGLES,
    get_corner_square,
    get_corner_pieslice_bbox,
    carve_corner_on_mask,
)
from .detection import (
    _BORDER_SCAN_STEP,
    _BORDER_COLOR_DIFF_THRESHOLD,
    _BORDER_MIN_GAP_PX,
    _BORDER_MAX_LAYERS,
    _EDGE_IGNORE_PX,
    _detect_border_layers,
    _get_border_layers_robust,
    _scan_edge_boundaries,
    detect_nested_rect_layers,
)
from .sector_render import (
    _angle_bottom,
    _angle_side,
    _build_border_sector_mask,
    _sample_border_color,
    _redraw_border_on_corner,
)

__all__ = [
    # algorithm
    'CORNER_ANGLES',
    'get_corner_square',
    'get_corner_pieslice_bbox',
    'carve_corner_on_mask',
    # detection
    '_BORDER_SCAN_STEP',
    '_BORDER_COLOR_DIFF_THRESHOLD',
    '_BORDER_MIN_GAP_PX',
    '_BORDER_MAX_LAYERS',
    '_EDGE_IGNORE_PX',
    '_detect_border_layers',
    '_get_border_layers_robust',
    '_scan_edge_boundaries',
    'detect_nested_rect_layers',
    # sector_render
    '_angle_bottom',
    '_angle_side',
    '_build_border_sector_mask',
    '_sample_border_color',
    '_redraw_border_on_corner',
]
