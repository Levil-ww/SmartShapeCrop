"""property_panel.py 自动拆分脚本（facade 薄壳模式 + 主类 mixin 化）。

把 gui/property_panel.py (1822 行) 拆为：
  - property_panel_widgets.py  小控件层（ColorButton / _SketchDropLabel + 颜色函数）
  - property_panel_workers.py  Worker 层（PoolRenderWorker / _SketchParseWorker）
  - property_panel_dialogs.py  对话框层（_SketchViewerDialog / _LayersDialog）
  - property_panel_poolbox.py  _PoolBoxMixin（池/草图 UI 槽函数，25 方法）
  - property_panel_generate.py _GenerateMixin（生成/进度回调，4 方法）
  - property_panel_layers.py   _LayersMixin（图层/收集/导出，9 方法）
  - property_panel.py          保留为 facade（PropertyPanel 主类 + 9 核心方法
                                + mixin 继承 + re-export）

纯搬移：不改任何函数逻辑。主类方法零重名，互调均通过 self 在运行时解析，
mixin 化后行为不变。
"""
import ast
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'gui/property_panel.py'
OUT = ROOT / 'gui'

src = SRC.read_text(encoding='utf-8')
tree = ast.parse(src)
lines_all = src.splitlines(keepends=True)

classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
top_funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
assert 'PropertyPanel' in classes, '未找到 PropertyPanel'
methods = {m.name: m for m in classes['PropertyPanel'].body if isinstance(m, ast.FunctionDef)}
assert len(methods) == 47, f'PropertyPanel 方法数异常: {len(methods)}'

# ---- 分组定义 ----
WIDGET_CLASSES = ['ColorButton', '_SketchDropLabel']
WORKER_CLASSES = ['PoolRenderWorker', '_SketchParseWorker']
DIALOG_CLASSES = ['_SketchViewerDialog', '_LayersDialog']
MODULE_FUNCS = ['_color_to_tuple', '_tuple_to_color']  # 归 widgets 层

KEEP_METHODS = [
    'get_output_filename', '__init__', '_build_ui', '_dspin', '_row',
    '_pick_file', '_on_mode_change', '_safe_dir_val2', '_set_pool_status',
]
POOLBOX_METHODS = [
    '_build_pool_box', '_on_pool_hole_mode_change', '_on_pool_target_changed',
    '_pool_sync_output_from_target', '_pool_pick_template_dir',
    '_pool_restore_last_template_dir', '_pool_refresh_template_history_ui',
    '_pool_apply_template_dir_from_history', '_pool_set_template_dir_ui',
    '_pool_clear_template_history', '_refresh_target_history_ui',
    '_pool_apply_target_from_history', '_pool_clear_target_history',
    '_pool_clear_target_history_by_date', '_pool_record_target_name_history',
    '_pool_on_template_history_selected', '_pool_on_template_dir_changed',
    '_pool_pick_target_file', '_pool_pick_sketch', '_pool_load_sketch_from_path',
    '_pool_auto_parse_sketch', '_on_sketch_parsed', '_on_sketch_parse_err',
    '_pool_clear_sketch', '_pool_view_sketch',
]
GENERATE_METHODS = [
    '_pool_run_generate', '_on_pool_progress', '_on_pool_finished_err',
    '_on_pool_finished_ok',
]
LAYERS_METHODS = [
    '_update_layers_label', '_add_layer', '_del_layer', '_edit_layers',
    '_collect', '_apply_quiet', 'apply', '_load_from_design',
    '_export_psd_layers',
]

# ---- 校验：方法归属无遗漏/无重复 ----
all_methods = KEEP_METHODS + POOLBOX_METHODS + GENERATE_METHODS + LAYERS_METHODS
assert len(all_methods) == len(set(all_methods)) == len(methods), \
    f'方法分组异常: 总{len(all_methods)} 唯一{len(set(all_methods))} 实际{len(methods)}'
missing = set(methods) - set(all_methods)
extra = set(all_methods) - set(methods)
assert not missing, f'遗漏未分组方法: {missing}'
assert not extra, f'分组含不存在方法: {extra}'

# ---- seg：提取节点源码（含装饰器），dedent 公共缩进 ----
def seg(node):
    start = node.decorator_list[0].lineno if getattr(node, 'decorator_list', None) else node.lineno
    return textwrap.dedent(''.join(lines_all[start - 1:node.end_lineno]))

def seg_method(mname):
    """提取方法源码（dedent 到 0 缩进），供 mixin 重新缩进。"""
    return seg(methods[mname])

# ---- 子模块统一 HEADER（全量复制原文件 imports） ----
HEADER = '''"""gui/property_panel 子模块 —— {desc}（由 property_panel.py 拆分而来，facade 模式）。

原文件 gui/property_panel.py 保留为 facade（PropertyPanel 主类 + 编排），
本模块只包含 {desc} 相关的实现，逻辑与原文件完全一致。
"""
from __future__ import annotations
import logging
import os
from datetime import date, datetime, timedelta
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QSize
from PyQt5.QtGui import QColor, QPixmap
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel, QDoubleSpinBox,
    QSpinBox, QComboBox, QPushButton, QCheckBox, QFileDialog, QLineEdit,
    QColorDialog, QFrame, QScrollArea, QMessageBox, QProgressDialog,
    QToolButton, QMenu, QAction, QDialog, QApplication,
)
from PyQt5.QtCore import QMimeData  # noqa: E402  (拖拽支持)
from PIL import Image

from core.geometry import CropDesign, BorderLayer, BorderText
from core.parser.name_parser import parse_filename
from core.parser.template_matcher import TemplateMatcher
from core.app_settings import get_app_settings
from core.pool_designer import validate_sketch_file
from core.pool_designer.sketch_parser import _SKETCH_ACCEPT_EXT, get_tesseract_status

logger = logging.getLogger(__name__)
'''

# mixin 统一附加跨模块导入（冗余但安全）
MIXIN_EXTRA_IMPORTS = '''
from .property_panel_widgets import ColorButton, _SketchDropLabel
from .property_panel_workers import PoolRenderWorker, _SketchParseWorker
from .property_panel_dialogs import _LayersDialog, _SketchViewerDialog
'''

# ---- 组装简单子模块（类/函数搬移） ----
def build_simple_module(desc, func_names, class_names):
    body = []
    for f in func_names:
        assert f in top_funcs, f'模块函数不存在: {f}'
        body.append(seg(top_funcs[f]))
    for c in class_names:
        assert c in classes, f'类不存在: {c}'
        body.append(seg(classes[c]))
    return HEADER.replace('{desc}', desc) + '\n\n' + '\n\n\n'.join(body) + '\n'

# ---- 组装 mixin 子模块 ----
def build_mixin(desc, mixin_name, method_names):
    body = []
    for mn in method_names:
        body.append(textwrap.indent(seg_method(mn), '    '))
    return (HEADER.replace('{desc}', desc) + MIXIN_EXTRA_IMPORTS + '\n'
            + f'class {mixin_name}:\n' + '\n\n'.join(body) + '\n')

MODULES = [
    ('property_panel_widgets', '小控件层（颜色按钮 / 草图拖放标签 / 颜色转换函数）',
     MODULE_FUNCS, WIDGET_CLASSES),
    ('property_panel_workers', '后台 Worker 层（水池渲染 / 草图解析线程）',
     [], WORKER_CLASSES),
    ('property_panel_dialogs', '对话框层（草图查看器 / 图层编辑）',
     [], DIALOG_CLASSES),
]

for name, desc, funcs, clss in MODULES:
    text = build_simple_module(desc, funcs, clss)
    (OUT / f'{name}.py').write_text(text, encoding='utf-8')
    print(f'已生成 {name}.py ({len(text.splitlines())} 行)')

for name, desc, mixin_name, method_names in [
    ('property_panel_poolbox', '水池模式 UI 与槽函数（_PoolBoxMixin，25 方法）',
     '_PoolBoxMixin', POOLBOX_METHODS),
    ('property_panel_generate', '一键生成与进度回调（_GenerateMixin，4 方法）',
     '_GenerateMixin', GENERATE_METHODS),
    ('property_panel_layers', '图层编辑与收集（_LayersMixin，9 方法）',
     '_LayersMixin', LAYERS_METHODS),
]:
    text = build_mixin(desc, mixin_name, method_names)
    (OUT / f'{name}.py').write_text(text, encoding='utf-8')
    print(f'已生成 {name}.py ({len(text.splitlines())} 行)')

# ---- 生成 facade（覆盖原文件） ----
facade_parts = []
# 头部：docstring + imports + logger（L1-28）
facade_parts.append(''.join(lines_all[:28]))
facade_parts.append('\n')
# 子模块导入
facade_parts.append(
    'from .property_panel_widgets import (ColorButton, _SketchDropLabel, '
    '_color_to_tuple, _tuple_to_color)\n'
    'from .property_panel_workers import PoolRenderWorker, _SketchParseWorker\n'
    'from .property_panel_dialogs import _LayersDialog, _SketchViewerDialog\n'
    'from .property_panel_poolbox import _PoolBoxMixin\n'
    'from .property_panel_generate import _GenerateMixin\n'
    'from .property_panel_layers import _LayersMixin\n')
facade_parts.append('\n')

# PropertyPanel 类：剔除已搬走的方法（保留注释/信号/核心方法）
skip_lines = set()
for mn in set(methods) - set(KEEP_METHODS):
    m = methods[mn]
    for ln in range(m.lineno, m.end_lineno + 1):
        skip_lines.add(ln)

cls_node = classes['PropertyPanel']
cls_lines = []
for ln in range(cls_node.lineno, cls_node.end_lineno + 1):
    if ln not in skip_lines:
        cls_lines.append(lines_all[ln - 1])
cls_src = ''.join(cls_lines)
cls_src = cls_src.replace(
    'class PropertyPanel(QWidget):',
    'class PropertyPanel(_LayersMixin, _GenerateMixin, _PoolBoxMixin, QWidget):',
    1)
facade_parts.append(cls_src)

facade_text = ''.join(facade_parts)
SRC.write_text(facade_text, encoding='utf-8')
print(f'facade 重写完成: {len(facade_text.splitlines())} 行')
