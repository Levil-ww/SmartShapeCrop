"""兼容 shim：实际模块已迁移至 services.parser。
旧导入路径通过 core/compat 的 sys.modules 别名重定向，
此文件仅作为目录占位和安全网。
"""
from services.parser import *  # noqa: F401, F403
