"""sketch_parser.py 自动拆分脚本（facade 薄壳模式）。

把 core/pool_designer/sketch_parser.py (2692 行) 拆为：
  - sketch_parser_base.py    基础层（规范化/文件校验/公共常量）
  - sketch_parser_cache.py   缓存层
  - sketch_parser_vision.py  图像/OCR/几何层
  - sketch_parser_numbers.py 数字提取层
  - sketch_parser_margins.py 边距/赋值层
  - sketch_parser.py         保留为 facade（编排 + re-export）

纯搬移：不改任何函数逻辑，仅移动源码 + 生成 import。
"""
import ast
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'core/pool_designer/sketch_parser.py'
OUT = ROOT / 'core/pool_designer'

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
    'sketch_parser_base': ['_normalize_ocr_text', 'validate_sketch_file'],
    'sketch_parser_cache': [
        '_get_cache_key', '_get_cached_result', '_store_cached_result',
        '_get_consistent_cache_key', '_get_consistent_cached_result',
        '_store_consistent_cached_result',
    ],
    'sketch_parser_vision': [
        '_safe_import_cv2', 'get_tesseract_status', '_safe_import_tesseract',
        '_load_image', '_to_gray', '_enhance_colored_ink',
        '_build_binary_masks', '_find_all_rectangles', '_select_best_nested_pair',
        '_compute_gaps', '_divide_8_zones', '_make_preprocess_variants',
        '_multi_scale_ocr_scan', '_spatial_map_values',
    ],
    'sketch_parser_numbers': [
        '_merge_split_decimals', '_parse_dir_num_token',
        '_extract_direction_label_numbers',
    ],
    'sketch_parser_margins': [
        '_score_assignment_consistency', '_brute_force_margin_permute',
        '_validate_and_fix_margins', '_validate_geometric_constraints',
        '_build_assignment',
    ],
}

FACADE_FUNCS = [
    '_7step_parse', 'parse_sketch', '_assess_complexity',
    '_find_two_nested_rectangles', '_estimate_inner_from_outer',
    '_detect_direction_labels_by_template', '_detect_direction_labels_by_ocr',
    '_detect_margins_by_geometry_ocr', '_assign_margins_by_spatial_reasoning',
    '_focused_ocr_for_direction_label', '_is_label_position_strict',
    '_find_and_read_numbers', '_scan_gap_for_value',
    '_detect_dir_labels_separate_pass',
]

CONST_OWNER = {
    '_FW_HW_TRANSLATION': 'sketch_parser_base',
    '_SKETCH_ACCEPT_EXT': 'sketch_parser_base',
    '_SKETCH_MAX_FILE_MB': 'sketch_parser_base',
    '_SKETCH_MAX_PIXELS': 'sketch_parser_base',
    '_ALGO_VERSION': 'sketch_parser_cache',
    '_SKETCH_CACHE': 'sketch_parser_cache',
    '_SKETCH_CACHE_MAX': 'sketch_parser_cache',
    '_SKETCH_CACHE_LOCK': 'sketch_parser_cache',
    '_SKETCH_CONSISTENT_CACHE': 'sketch_parser_cache',
    '_SKETCH_CONSISTENT_CACHE_MAX': 'sketch_parser_cache',
    '_SKETCH_CONSISTENT_CACHE_LOCK': 'sketch_parser_cache',
    '_TESSERACT_STATUS': 'sketch_parser_vision',
    '_DIR_CHAR_MAP': 'sketch_parser_numbers',
    '_PARSE_TIMEOUT_SEC': 'sketch_parser_base',
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
IMPORT_NAMES = {'logging', 'os', 're', 'sys', 'threading', 'time',
                'dataclass', 'field', 'Optional', 'np', 'Image', 'PIL'}

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
HEADER = '''"""尺寸草图解析器 —— {desc}（由 sketch_parser.py 拆分而来，facade 模式）。

原文件 core/pool_designer/sketch_parser.py 为编排层 facade，
本模块只包含 {desc} 相关的实现，逻辑与原文件完全一致。
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

try:  # pragma: no cover - 依赖环境差异
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 200_000_000
except Exception:
    logging.getLogger(__name__).debug("[module] PIL 导入失败，已降级", exc_info=True)
    Image = None  # type: ignore

logger = logging.getLogger(__name__)
'''

GROUP_ORDER = ['sketch_parser_base', 'sketch_parser_cache', 'sketch_parser_vision',
               'sketch_parser_numbers', 'sketch_parser_margins']
GROUP_IDX = {g: i for i, g in enumerate(GROUP_ORDER)}

def group_of_func(name):
    for g, fs in GROUPS.items():
        if name in fs:
            return g
    return '_facade'

# 跨组依赖
cross_deps = {}   # group -> set(func 名字) 需要从其他组 import
import_deps = {}  # group -> set(import 名字)
for gname, fs in GROUPS.items():
    need_funcs, need_imports = set(), set()
    for f in fs:
        for r in refs_of(top_funcs[f]):
            if r in top_funcs and group_of_func(r) != gname:
                need_funcs.add(r)
            elif r in module_consts and CONST_OWNER.get(r, gname) != gname:
                need_funcs.add(r)
            elif r in IMPORT_NAMES:
                need_imports.add(r)
            elif r not in BUILTINS and r not in module_consts and r not in top_funcs:
                pass  # 第三方（cv2/pytesseract 等函数内 import）或字符串，忽略
    cross_deps[gname] = need_funcs
    import_deps[gname] = need_imports
    # 校验：被依赖的组必须在更底层（无环）
    for r in need_funcs:
        src_g = group_of_func(r)
        if r in top_funcs:
            dep_g = src_g
        else:
            dep_g = CONST_OWNER.get(r)
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
    'sketch_parser_base': '基础层（文本规范化 / 文件校验 / 公共常量）',
    'sketch_parser_cache': '缓存层（结果与自洽解缓存）',
    'sketch_parser_vision': '图像加载 / OCR 扫描 / 矩形几何检测层',
    'sketch_parser_numbers': '方向标签与数值提取层',
    'sketch_parser_margins': '边距校验与赋值打分层',
}

for g in GROUP_ORDER:
    out = OUT / f'{g}.py'
    out.write_text(build_module(g, MODULE_DESC[g]), encoding='utf-8')
    print(f'已生成 {out.name} ({len(build_module(g, MODULE_DESC[g]).splitlines())} 行)')

# ---- 生成 facade（覆盖原文件） ----
facade_parts = []
lines_all = src.splitlines(keepends=True)
# 头部：docstring + imports + PIL + logger（保留原样至 _FW_HW_TRANSLATION 之前）
facade_parts.append(''.join(lines_all[:module_consts['_FW_HW_TRANSLATION'].lineno - 1]))
# 子模块导入
facade_parts.append('\n')
for g in GROUP_ORDER:
    exports = GROUPS[g] + [c for c, o in CONST_OWNER.items() if o == g]
    facade_parts.append(f'from .{g} import ({", ".join(sorted(exports))})\n')
# SketchParseResult dataclass
facade_parts.append(seg(top_classes['SketchParseResult']) + '\n\n')
# 编排函数
for f in FACADE_FUNCS:
    facade_parts.append(seg(top_funcs[f]) + '\n\n')

facade_text = ''.join(facade_parts)
SRC.write_text(facade_text, encoding='utf-8')
print(f'facade 重写完成: {len(facade_text.splitlines())} 行')
