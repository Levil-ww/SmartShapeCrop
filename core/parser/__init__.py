"""
core/parser/__init__.py
文件名解析与模板匹配子包。
"""
from .name_parser import (
    ParsedFilename,
    CN_NUM,
    SHAPE_SUFFIXES,
    parse_filename,
    parse_size_dims,
    generate_filename,
    get_image_info,
    get_base_pattern_name,
    normalize_flower_name,
    _cn_to_int,
    _normalize_str,
    _extract_size_pair,
    _extract_size_pair_manual,
    _parse_corners,
    _fmt_num,
)
from .template_matcher import (
    TemplateEntry,
    TemplateMatcher,
    scan_template_directory,
    match_template,
)

__all__ = [
    # name_parser
    'ParsedFilename', 'CN_NUM', 'SHAPE_SUFFIXES',
    'parse_filename', 'parse_size_dims', 'generate_filename',
    'get_image_info', 'get_base_pattern_name', 'normalize_flower_name',
    '_cn_to_int', '_normalize_str', '_extract_size_pair',
    '_extract_size_pair_manual', '_parse_corners', '_fmt_num',
    # template_matcher
    'TemplateEntry', 'TemplateMatcher',
    'scan_template_directory', 'match_template',
]
