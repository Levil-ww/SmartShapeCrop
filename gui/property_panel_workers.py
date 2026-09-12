"""兼容 shim：实际模块已迁移至 workers.property_panel_workers。

旧导入路径 `from gui.property_panel_workers import X` 继续可用，
实际指向 workers.property_panel_workers 模块。
"""
from workers.property_panel_workers import *  # noqa: F401, F403
