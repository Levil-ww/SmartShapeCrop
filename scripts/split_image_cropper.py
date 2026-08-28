"""image_cropper.py 自动拆分脚本（facade 薄壳模式）。

把 core/image_cropper.py (1757 行) 拆为：
  - image_cropper_mask.py   掩码/圆角内容分析层（7 个函数，~890 行）
  - image_cropper_border.py 边框层（2 个函数，含嵌套 _edge_sample，~380 行）
  - image_cropper.py        保留为 facade（编排 + re-export）

纯搬移：不改任何函数逻辑，仅移动源码 + 生成 import。
注意：
  - _edge_sample 是 _redraw_outer_border_on_corners 的嵌套函数，随父函数搬移；
  - _estimate_outer_background 被 mask 组引用，下移到 mask 组避免环；
  - _DEFAULT_BORDER_WIDTH_CM 被 apply_border_only_corners 默认参数引用，border 组自含。
"""
import ast
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'core/image_cropper.py'
OUT = ROOT / 'core'

src = SRC.read_text(encoding='utf-8')
tree = ast.parse(src)

# ---- 模块级函数与常量 ----
top_funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
top_classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}

module_consts = {}
for n in tree.body:
    if isinstance(n, (ast.Assign, ast.AnnAssign)):
        targets = n.targets if isinstance(n, ast.Assign) else [n.target]
        for t in targets:
            if isinstance(t, ast.Name):
                module_consts[t.id] = n

# ---- 分组定义 ----
GROUPS = {
    'image_cropper_mask': [
        '_build_multi_layer_corner_mask', '_analyze_corner_sector_content',
        '_estimate_outer_background', '_corner_sector_has_content',
        '_build_border_paint_mask', '_post_cleanup_gap_regions',
        '_clear_inner_arc_to_bg',
    ],
    'image_cropper_border': [
        '_redraw_outer_border_on_corners', 'apply_border_only_corners',
    ],
}

FACADE_FUNCS = [
    'load_source_image', 'apply_rounded_corners', '_filter_gap_layers',
    '_smart_crop', '_light_cover', 'crop_image', 'batch_crop',
    'get_corner_name', 'get_default_corners', 'get_mode_description',
]

CONST_OWNER = {
    '_DEFAULT_BORDER_WIDTH_CM': 'image_cropper_border',
}

# ---- 校验：函数归属无遗漏/无重复 ----
all_funcs = [f for fs in GROUPS.values() for f in fs] + FACADE_FUNCS
assert len(all_funcs) == len(set(all_funcs)), '分组有重复'
missing = set(top_funcs) - set(all_funcs)
extra = set(all_funcs) - set(top_funcs)
assert not missing, f'遗漏未分组函数: {missing}'
assert not extra, f'分组含不存在函数: {extra}'

# ---- 每个函数引用的名字（排除局部名） ----
BUILTINS = set(dir(__builtins__)) if isinstance(__builtins__, dict) else set(dir(__builtins__))
# 子模块 HEADER 全量复制原文件 import，此处无需细分 import 依赖
IMPORT_NAMES = set()

def local_names(node):
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.arg):
            names.add(sub.arg)
        elif isinstance(sub, ast.FunctionDef):
            names.add(sub.name)
        elif isinstance(sub, (ast.Assign, ast.AnnAssign)):
            ts = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            for t in ts:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(sub, (ast.For, ast.comprehension)):
            for n in ast.walk(sub.target):
                if isinstance(n, ast.Name):
                    names.add(n.id)
        elif isinstance(sub, (ast.With, ast.ExceptHandler)):
            if isinstance(sub, ast.With):
                for item in sub.items:
                    if item.optional_vars:
                        for n in ast.walk(item.optional_vars):
                            if isinstance(n, ast.Name):
                                names.add(n.id)
            else:
                if sub.name:
                    names.add(sub.name)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            for alias in sub.names:
                nm = (alias.asname or alias.name).split('.')[0]
                names.add(nm)
        elif isinstance(sub, ast.Global):
            names.update(sub.names)
    return names

def refs_of(func_node):
    locals_ = local_names(func_node)
    refs = set()
    for sub in ast.walk(func_node):
        if isinstance(sub, ast.Name) and sub.id not in locals_:
            refs.add(sub.id)
    return refs

def seg(node):
    """提取节点源码（含装饰器），按行切分，dedent 到模块级缩进。"""
    start = node.decorator_list[0].lineno if getattr(node, 'decorator_list', None) else node.lineno
    lines = src.splitlines(keepends=True)
    return textwrap.dedent(''.join(lines[start - 1:node.end_lineno]))

# ---- 生成每个子模块 ----
HEADER = '''"""图片裁剪服务 —— {desc}（由 image_cropper.py 拆分而来，facade 模式）。

原文件 core/image_cropper.py 为编排层 facade，
本模块只包含 {desc} 相关的实现，逻辑与原文件完全一致。
"""

from __future__ import annotations
import os
import logging
from dataclasses import dataclass
import numpy as np
from PIL import Image, ImageDraw

from .image_ops import load_image_rgb, fit_image_to_rect
from .psd.loader import is_psd_file, load_psd_flattened
from .corner.algorithm import (
    CORNER_ANGLES,
    carve_corner_on_mask,
    get_corner_square,
    get_corner_pieslice_bbox,
)
# 从 corner 子包导入检测与重绘函数（与 image_cropper.py 原头部一致）
from .corner.detection import (
    _BORDER_SCAN_STEP,
    _BORDER_COLOR_DIFF_THRESHOLD,
    _BORDER_MIN_GAP_PX,
    _BORDER_MAX_LAYERS,
    _EDGE_IGNORE_PX,
    _detect_border_layers,
    _get_border_layers_robust,
    _scan_edge_boundaries,
    detect_nested_rect_layers,
    classify_gap_layers,
    get_solid_border_colors,
    GAP_MAX_THICKNESS_GLOBAL,
    GAP_NEIGHBOR_MIN_DIST_GLOBAL,
    GAP_BG_DIST_GLOBAL,
    GAP_CONTENT_DIST_GLOBAL,
    SENTINEL_OUTER_DARK_MAX_RGB,
)
from .corner.sector_render import (
    _build_border_sector_mask,
    _sample_border_color,
    _redraw_border_on_corner,
)
from .config import (
    DEFAULT_BORDER_WIDTH_CM,
    BORDER_TOTAL_DEPTH_CM,
    DEFAULT_DPI,
    DEFAULT_BG_COLOR,
    DEFAULT_CROP_MODE,
    DEFAULT_MAX_CROP_RATIO,
)

logger = logging.getLogger(__name__)
'''

GROUP_ORDER = ['image_cropper_mask', 'image_cropper_border']
GROUP_IDX = {g: i for i, g in enumerate(GROUP_ORDER)}

def group_of_func(name):
    for g, fs in GROUPS.items():
        if name in fs:
            return g
    return '_facade'

# 跨组依赖（仅函数/常量，import 全量在 HEADER）
cross_deps = {}
for gname, fs in GROUPS.items():
    need_funcs = set()
    for f in fs:
        for r in refs_of(top_funcs[f]):
            if r in top_funcs and group_of_func(r) != gname:
                need_funcs.add(r)
            elif r in module_consts and CONST_OWNER.get(r, gname) != gname:
                need_funcs.add(r)
    cross_deps[gname] = need_funcs
    # 校验：被依赖的组必须在更底层（无环）
    for r in need_funcs:
        src_g = group_of_func(r)
        dep_g = src_g if r in top_funcs else CONST_OWNER.get(r)
        if dep_g is None:
            continue
        if dep_g == '_facade':
            print(f'[环警告] {gname} 引用 facade 层函数 {r}（需下移归属）')
        elif GROUP_IDX[dep_g] >= GROUP_IDX[gname]:
            print(f'[环警告] {gname} 引用 {dep_g}.{r}（同层或更高层）')

for g in GROUP_ORDER:
    print(f'[{g}] 跨组引用: {sorted(cross_deps[g])}')

# ---- 组装子模块文件 ----
def build_module(gname, desc):
    body = []
    # 常量（归属本组的）
    for cname, owner in CONST_OWNER.items():
        if owner == gname and cname in module_consts:
            body.append(seg(module_consts[cname]))
    # 函数
    for f in GROUPS[gname]:
        body.append(seg(top_funcs[f]))
    text = HEADER.replace('{desc}', desc) + '\n\n' + '\n\n\n'.join(body) + '\n'
    # 追加跨组 import（放在 header 与常量之间）
    func_imports = sorted(cross_deps[gname])
    if func_imports:
        import_lines = '\n'.join(f'from .{group_of_func(x) if x in top_funcs else CONST_OWNER[x]} import {x}'
                                 for x in func_imports)
        text = text.replace('logger = logging.getLogger(__name__)\n',
                            'logger = logging.getLogger(__name__)\n\n' + import_lines + '\n')
    return text

MODULE_DESC = {
    'image_cropper_mask': '掩码构建 / 圆角扇形内容分析层',
    'image_cropper_border': '边框重绘 / 边框角处理层',
}

for g in GROUP_ORDER:
    out = OUT / f'{g}.py'
    out.write_text(build_module(g, MODULE_DESC[g]), encoding='utf-8')
    print(f'已生成 {out.name} ({len(build_module(g, MODULE_DESC[g]).splitlines())} 行)')

# ---- 生成 facade（覆盖原文件） ----
facade_parts = []
lines_all = src.splitlines(keepends=True)
# 头部：docstring + imports + logger + CropConfig + _DEFAULT_BORDER_WIDTH_CM（原样保留至 L95）
facade_parts.append(''.join(lines_all[:95]))
facade_parts.append('\n')
# 子模块导入
for g in GROUP_ORDER:
    exports = GROUPS[g] + [c for c, o in CONST_OWNER.items() if o == g]
    facade_parts.append(f'from .{g} import ({", ".join(sorted(exports))})\n')
facade_parts.append('\n')
# 编排函数（按原文件出现顺序）
for f in FACADE_FUNCS:
    facade_parts.append(seg(top_funcs[f]) + '\n\n')

facade_text = ''.join(facade_parts)
SRC.write_text(facade_text, encoding='utf-8')
print(f'facade 重写完成: {len(facade_text.splitlines())} 行')
