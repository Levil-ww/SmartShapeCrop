"""gui package

公共 API：
  - PreviewCanvas:  水池设计器的预览画布
  - PropertyPanel:   水池设计器的属性面板
  - CropperPanel:    圆角裁剪工具面板（上传 → 文件名解析 → 模板匹配 → 预览 → 导出）
  - LShapePanel:     L 形挖角设计面板（独立承载 L 形挖角 UI 与识别逻辑）
"""
from .canvas_widget import PreviewCanvas
from .property_panel import PropertyPanel
from .cropper_panel import CropperPanel
from .lshape_panel import LShapePanel

__all__ = ['PreviewCanvas', 'PropertyPanel', 'CropperPanel', 'LShapePanel']
