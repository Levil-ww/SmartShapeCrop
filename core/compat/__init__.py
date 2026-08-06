"""
core/compat/ 子包：向后兼容别名集中管理。

通过 sys.modules 注册旧导入路径的别名，使以下旧路径继续可用：
  - core.rounded_corner    → core.corner.algorithm
  - core.name_parser       → core.parser.name_parser
  - core.template_matcher → core.parser.template_matcher
  - core.psd_loader        → core.psd.loader

工作原理：
  Python 导入 `from core.name_parser import X` 或 `from .name_parser import X`
  时，会先查 sys.modules['core.name_parser']。本子包在导入时把该键指向真实的
  core.parser.name_parser 模块对象，使旧路径"重定向"到新子包，文件不存在但
  导入仍可用。这是 six / future / importlib.resources 等标准库常用的兼容手法。

时序要求：
  本子包必须在 core/__init__.py 最开头被导入（`from . import compat`），
  确保后续业务模块（geometry / image_cropper / image_ops 等）中的相对导入
  `from .rounded_corner import ...` 也能解析到别名。

同源保证：
  sys.modules 别名注册的是同一个模块对象，因此
  `from core.rounded_corner import f` is `from core.corner.algorithm import f`
  恒为 True（_refactor_selfcheck.py 的同源校验通过）。

未来迁移：
  所有调用方迁移到新路径后，删除本子包即可，无需改动业务代码。
"""
import sys

# 1. 先导入真实子包，确保模块对象存在于 sys.modules
from ..corner import algorithm as _rounded_corner_mod
from ..parser import name_parser as _name_parser_mod
from ..parser import template_matcher as _template_matcher_mod
from ..psd import loader as _psd_loader_mod

# 2. 注册旧导入路径的别名（旧路径 → 真实子包模块对象）
#    sys.modules[name] = module 后，import name / from name import X 都会走别名
_COMPAT_ALIASES = {
    'core.rounded_corner': _rounded_corner_mod,
    'core.name_parser': _name_parser_mod,
    'core.template_matcher': _template_matcher_mod,
    'core.psd_loader': _psd_loader_mod,
}

for _old_path, _module in _COMPAT_ALIASES.items():
    sys.modules[_old_path] = _module

# 暴露给 `from core.compat import rounded_corner` 等用法（可选便利）
rounded_corner = _rounded_corner_mod
name_parser = _name_parser_mod
template_matcher = _template_matcher_mod
psd_loader = _psd_loader_mod

__all__ = ['rounded_corner', 'name_parser', 'template_matcher', 'psd_loader']
