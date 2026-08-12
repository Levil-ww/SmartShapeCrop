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
# 圆角边框宽度
# ============================================================================

# 边框圆角模式下的边框宽度（仅此深度范围内应用圆角，内部保持直角）
DEFAULT_BORDER_WIDTH_CM: float = 1.5

# 边框总深度（覆盖所有边框层的深度）。
# 历史保留常量，当前圆角统一走 apply_border_only_corners，不再依赖此补偿，
# 仅为兼容旧测试脚本保留。
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

# 圆角切割损耗（厘米）：实际裁剪时圆角半径需比命名值大 0.5cm，
# 以补偿印刷切割损耗，确保成品圆角半径与客户要求一致。
# 例：文件名"1cm半径圆角" → 实际裁剪 1.5cm，输出文件名仍为 1cm。
CORNER_CUT_LOSS_CM: float = 0.5


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
# 说明：以下常量统一从 config.py 引用，detection.py / image_cropper.py /
# sector_render.py 等子模块通过 `from ..config import ...` 取得单一来源。
# 历史 detection.py 中的同名带下划线前缀常量（_BORDER_SCAN_STEP 等）保留为
# 别名，向后兼容旧测试脚本的 `from core.image_cropper import _BORDER_SCAN_STEP`。

# 颜色距离阈值：欧氏距离 > 此值视为不同颜色（处理抗锯齿/渐变）
# 用于 _detect_border_layers 的边框颜色匹配（基于 RGB 欧氏距离）
BORDER_COLOR_DISTANCE_THRESHOLD: int = 15

# 亮度差分阈值：R+G+B 总和的一阶差分 > 此值视为边界
# 用于 _scan_edge_boundaries 的亮度突变检测（基于 R+G+B 总和，故阈值较大）
# 注意：两类算法使用不同阈值是有意为之，分别处理不同的边框识别场景
BORDER_LUMINANCE_DIFF_THRESHOLD: int = 25

# 边框扫描步长（像素）：步长越大越快但越不精确
BORDER_SCAN_STEP_PX: int = 2

# 相邻两个边界之间的最小间距（像素）：小于此值视为同一条边界
BORDER_MIN_GAP_PX: int = 5

# 最多检测层数上限，防止误检过多
BORDER_MAX_LAYERS: int = 10

# 忽略最边缘几个像素（避免最外白边/黑边干扰）
BORDER_EDGE_IGNORE_PX: int = 2

# 最小层厚度（像素）：小于此值的层合并到上一层
BORDER_MIN_LAYER_THICKNESS_PX: int = 2

# 背景色相似度阈值：与 bg_color 的距离 <= 此值视为背景（用于 _get_border_layers_robust 的 fallback）
BORDER_BG_SIMILARITY_THRESHOLD: int = 30

# 边框扫描最大深度（像素）：_detect_border_layers 的 max_scan_depth_px 默认值
BORDER_SCAN_MAX_DEPTH_PX: int = 300

# [Fix E/S5 延伸] 检测结果的厚度硬上限（防止把内容区/花纹误判为超厚边框层）
# 单一层最大厚度（厘米）：超过则判定为"内容区污染"，被截断或丢弃
# [硬约束] 单层边框 ≤ 2cm，防止内容区被误识别为单层边框
BORDER_MAX_SINGLE_LAYER_CM: float = 2.0
# 所有层累计总厚度最大上限（厘米）：超过则丢弃最末层直到符合
# [硬约束] 总边框厚度 ≤ 3cm，防止多层边框误判
BORDER_MAX_TOTAL_CM: float = 3.0


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
