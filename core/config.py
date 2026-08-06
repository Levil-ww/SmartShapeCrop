"""
core/config.py
项目统一配置管理：业务常量、默认值、阈值集中于此。

设计原则：
  - 业务常量集中定义，单一数据来源
  - image_cropper.py / geometry.py / cropper_panel.py / process_image.py 共用此模块
  - 历史 image_cropper.py 中的同名常量保留为别名（向后兼容旧测试脚本导入）
  - 与圆角几何相关的常量请勿散落到其他文件

所有数值单位：长度=厘米(cm)，角度=度(°)，分辨率=DPI(px/inch)
"""
from __future__ import annotations


# ============================================================================
# 圆角模式阈值
# ============================================================================

# 圆角半径 >= 此值时采用"整体圆角"（所有嵌套层都同步裁角），否则采用"仅边框圆角"
BORDER_ONLY_THRESHOLD_CM: float = 8.5

# 边框圆角模式下的边框宽度（仅此深度范围内应用圆角，内部保持直角）
DEFAULT_BORDER_WIDTH_CM: float = 1.5

# 边框总深度（覆盖所有边框层的深度）。
# 历史用途：当 radius >= 阈值时，老代码实际裁剪半径 = radius + 此值，
# 以确保所有边框层都被裁掉。新版 apply_multi_layer_rounded_corners
# 已用 AND 逻辑覆盖所有层，不再依赖此补偿，但保留常量以兼容旧测试脚本。
BORDER_TOTAL_DEPTH_CM: float = 2.0


# ============================================================================
# 默认值
# ============================================================================

# 默认 DPI（与 CropDesign / CropConfig / UI 默认值一致）
DEFAULT_DPI: int = 150

# 默认背景色（圆角处、留白处的填充色）
DEFAULT_BG_COLOR: tuple[int, int, int] = (255, 255, 255)

# 默认裁剪模式（高质量 LANCZOS 缩放，不裁剪不留白）
DEFAULT_CROP_MODE: str = 'simple_resize'

# light_cover 模式最大裁剪比例（15%）
DEFAULT_MAX_CROP_RATIO: float = 0.15


# ============================================================================
# 印刷切割损耗
# ============================================================================

# 切割损耗（厘米）：UI 自动给目标尺寸 +1cm 作为源图扫描尺寸
# （原散落于 cropper_panel.py 的 `+1.0` 魔法数字）
CUT_LOSS_CM: float = 1.0


# ============================================================================
# 图像缩放
# ============================================================================

# LANCZOS 重采样算法（高质量缩放，最小损失）
RESAMPLE_ALGORITHM: int = None  # 延迟初始化，避免顶层 import PIL


def get_resample_algorithm():
    """返回 PIL.Image.LANCZOS 常量（延迟 import）"""
    from PIL import Image
    return Image.LANCZOS


# ============================================================================
# 边框检测
# ============================================================================

# 颜色距离阈值：欧氏距离 > 此值视为不同颜色（处理抗锯齿/渐变）
# 用于 _detect_border_layers 的边框颜色匹配
BORDER_COLOR_DISTANCE_THRESHOLD: int = 15

# 边框扫描最大深度（像素）
BORDER_SCAN_MAX_DEPTH_PX: int = 300


# ============================================================================
# 单位换算
# ============================================================================

# 厘米 → 像素 换算因子（基于 DPI）
CM_PER_INCH: float = 2.54


def cm_to_px(cm: float, dpi: int = DEFAULT_DPI) -> int:
    """厘米转像素（向下取整，最小值 1）"""
    return max(1, int(round(cm * dpi / CM_PER_INCH)))


def px_to_cm(px: int, dpi: int = DEFAULT_DPI) -> float:
    """像素转厘米"""
    return px * CM_PER_INCH / dpi
