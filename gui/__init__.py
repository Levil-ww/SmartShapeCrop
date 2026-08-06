"""gui package

公共 API：
  - PreviewCanvas:  水池设计器的预览画布
  - PropertyPanel:   水池设计器的属性面板
  - CropperPanel:    圆角裁剪工具面板（上传 → 文件名解析 → 模板匹配 → 预览 → 导出）
"""
from .canvas_widget import PreviewCanvas
from .property_panel import PropertyPanel
from .cropper_panel import CropperPanel

__all__ = ['PreviewCanvas', 'PropertyPanel', 'CropperPanel']
