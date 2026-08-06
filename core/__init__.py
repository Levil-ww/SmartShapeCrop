"""core package

公共 API 总入口，聚合各子包/子模块的对外名称。

子包结构：
  - core.corner:     圆角处理（algorithm 单步扇形切割 / detection 边框层检测 /
                     sector_render 圆弧上的边框重绘）
  - core.parser:     文件名解析 + 模板库匹配
  - core.psd:        PSD 文件加载与导出
  - core.config:     统一配置常量（DPI / 阈值 / 单位换算）
  - core.log_setup:  统一日志配置
  - core.compat:     向后兼容别名（sys.modules 注册旧导入路径）

向后兼容：旧导入路径 core.rounded_corner / core.name_parser /
core.template_matcher / core.psd_loader 通过 core.compat 子包的
sys.modules 别名重定向到新子包，文件已删除但导入仍可用。
"""
# ⚠️ 时序关键：compat 必须在所有业务模块之前导入，确保 sys.modules 别名
# 已注册，后续 `from .rounded_corner import ...` 等相对导入才能解析到别名。
from . import compat  # noqa: F401  触发 sys.modules 别名注册（无副作用）

from .geometry import (
    RectShape, EllipseShape, LShape, BorderLayer,
    BorderText, CropDesign, compute_border_bands,
    compute_inner_corner_radii,
)
from .image_ops import render_design, save_jpg, prepare_material_for_rect
from .psd.loader import (
    PsdLayer, load_psd_layers, load_psd_flattened,
    export_psd_layers_as_jpgs,
)
# 圆角处理子包公共 API（统一从 corner 子包重导出，保证单一来源）
from .corner import (
    CORNER_ANGLES,
    get_corner_square,
    get_corner_pieslice_bbox,
    carve_corner_on_mask,
    detect_nested_rect_layers,
)
# 裁剪服务公共 API
from .image_cropper import CropConfig, crop_image, batch_crop

__all__ = [
    # geometry
    'RectShape', 'EllipseShape', 'LShape', 'BorderLayer',
    'BorderText', 'CropDesign', 'compute_border_bands',
    'compute_inner_corner_radii',
    # image_ops
    'render_design', 'save_jpg', 'prepare_material_for_rect',
    # psd
    'PsdLayer', 'load_psd_layers', 'load_psd_flattened',
    'export_psd_layers_as_jpgs',
    # corner（圆角处理公共 API）
    'CORNER_ANGLES',
    'get_corner_square',
    'get_corner_pieslice_bbox',
    'carve_corner_on_mask',
    'detect_nested_rect_layers',
    # image_cropper（裁剪服务公共 API）
    'CropConfig', 'crop_image', 'batch_crop',
]
