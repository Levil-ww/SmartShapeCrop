"""
core/name_parser.py
文件名解析器：从文件名提取产品名称、尺寸、圆角要求等信息。

尺寸方向规则：
- 竖版：长边为高，短边为宽（不管数字顺序）
- 横版：长边为宽，短边为高（不管数字顺序）
- 如果命名中出现"横版"，不管在哪个位置都判定为横版
"""
from __future__ import annotations
import re
from dataclasses import dataclass


@dataclass
class ParsedFilename:
    """解析后的文件名信息"""
    product_name: str = ""          # 产品名称，如 "双面格-定制-定制尺寸-简织"
    layout: str = ""                # 布局：'竖版' / '横版'
    width_cm: float = 0.0           # 宽度（厘米）
    height_cm: float = 0.0          # 高度（厘米）
    corners: dict[str, float] | None = None  # 各角圆角半径(cm)，None=无圆角
    raw_filename: str = ""          # 原始文件名


# 中文数字映射
CN_NUM = {'零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def _cn_to_int(s: str) -> int | None:
    """将中文数字转为整数"""
    if s in CN_NUM:
        return CN_NUM[s]
    try:
        return int(s)
    except ValueError:
        return None


def parse_filename(filename: str) -> ParsedFilename:
    """
    解析文件名，提取尺寸和圆角信息。
    
    尺寸方向规则：
    - 竖版：长边为高，短边为宽
    - 横版：长边为宽，短边为高
    - 出现"横版"即判定为横版
    
    Args:
        filename: 文件名（含或不含扩展名）
    
    Returns:
        ParsedFilename 对象
    """
    # 去除扩展名
    name = re.sub(r'\.[^.]+$', '', filename)
    
    result = ParsedFilename(raw_filename=filename)
    
    # 分离产品名称和规格部分 - 支持多种分隔符
    # 规则：找到第一个出现的"竖版"或"横版"或数字x数字作为分界
    # 优先用分号、逗号分隔
    parts = re.split(r'[;,]', name, maxsplit=1)
    
    if len(parts) > 1:
        result.product_name = parts[0].strip()
        spec = parts[1].strip()
    else:
        # 尝试在整个字符串中找布局标记
        spec = name
        # 产品名是除了布局+尺寸+圆角之外的部分
        result.product_name = name
    
    # 检查整个文件名（不仅是spec）来判断布局
    # 规则："横版"优先级最高，出现即判定为横版
    if '横版' in name or '横款' in name:
        result.layout = '横版'
    elif '竖版' in name or '竖款' in name:
        result.layout = '竖版'
    else:
        # 没有明确标记，默认为横版（长边为宽）
        result.layout = '横版'
    
    # 提取尺寸 (数字x数字 cm)
    size_match = re.search(r'(\d+(?:\.\d+)?)\s*[x×X]\s*(\d+(?:\.\d+)?)\s*(?:cm|CM|厘米)?', spec)
    
    if not size_match:
        # 尝试在整个文件名中找
        size_match = re.search(r'(\d+(?:\.\d+)?)\s*[x×X]\s*(\d+(?:\.\d+)?)\s*(?:cm|CM|厘米)?', name)
    
    if size_match:
        a = float(size_match.group(1))
        b = float(size_match.group(2))
        
        # 根据布局确定宽高分配
        if result.layout == '竖版':
            # 竖版：长边为高，短边为宽
            result.width_cm = min(a, b)
            result.height_cm = max(a, b)
        elif result.layout == '横版':
            # 横版：长边为宽，短边为高
            result.width_cm = max(a, b)
            result.height_cm = min(a, b)
        else:
            # 没有明确布局，按横版默认（长边为宽）
            result.width_cm = max(a, b)
            result.height_cm = min(a, b)
    
    # 提取圆角要求
    corners = _parse_corners(name)  # 在整个文件名中查找
    result.corners = corners if corners else None
    
    return result


def _parse_corners(spec: str) -> dict[str, float] | None:
    """
    从字符串中解析圆角要求。
    
    支持的格式：
    - 四角圆角半径2cm / 四个圆角半径2cm / 4个圆角半径2cm
    - 左上角圆角半径2cm / 右下角圆角半径2cm
    - 左下角圆角半径2.5cm / 右上角圆角半径3cm
    """
    result = {}
    
    # 先尝试匹配每个角单独指定的情况
    corner_map = {
        '左上角': 'tl',
        '右上角': 'tr',
        '左下角': 'bl',
        '右下角': 'br',
    }
    
    for cn_name, key in corner_map.items():
        # 匹配：左上角圆角半径2cm / 左上角圆角2cm
        pattern = rf'{cn_name}?圆角(?:半径)?\s*(\d+(?:\.\d+)?)\s*(?:cm|CM|厘米)?'
        # 或者：左上角半径2cm
        pattern2 = rf'{cn_name}(?:圆角)?半径\s*(\d+(?:\.\d+)?)\s*(?:cm|CM|厘米)?'
        
        m = re.search(pattern, spec) or re.search(pattern2, spec)
        if m:
            result[key] = float(m.group(1))
    
    # 检查是否有"四角/四个/4个/2个/两个圆角"的情况
    all_corners_patterns = [
        r'(?:四角|四个)\s*圆角(?:半径)?\s*(\d+(?:\.\d+)?)\s*(?:cm|CM|厘米)?',
        r'(\d+|[一二三四五])\s*个?\s*圆角(?:半径)?\s*(\d+(?:\.\d+)?)\s*(?:cm|CM|厘米)?',
        r'(?:四角|四个)\s*圆角半径\s*(\d+(?:\.\d+)?)\s*(?:cm|CM|厘米)?',
    ]
    
    for pattern in all_corners_patterns:
        m = re.search(pattern, spec)
        if m:
            if len(m.groups()) == 1:
                # 四角圆角半径Xcm
                radius = float(m.group(1))
                result = {'tl': radius, 'tr': radius, 'bl': radius, 'br': radius}
            elif len(m.groups()) == 2:
                # N个圆角半径Xcm
                count_str = m.group(1)
                count = _cn_to_int(count_str)
                radius = float(m.group(2))
                
                if count == 4:
                    result = {'tl': radius, 'tr': radius, 'bl': radius, 'br': radius}
            
            if result:
                return result
    
    # 如果没有"四角"模式，检查是否有单独的角指定
    if result:
        # 如果只有部分角指定，未指定的角设为0（无圆角）
        all_keys = ['tl', 'tr', 'bl', 'br']
        for k in all_keys:
            if k not in result:
                result[k] = 0.0
        return result
    
    return None


def generate_filename(parsed: ParsedFilename, corners_override: dict[str, float] | None = None) -> str:
    """
    根据解析结果和可选的圆角覆盖，生成规范化的文件名。
    
    输出格式: 产品名称;布局尺寸圆角描述.jpg
    示例: 双面格;竖版55x41cm右下角圆角半径2cm.jpg
    
    Args:
        parsed: 解析后的文件名信息
        corners_override: 可选的圆角覆盖（如果为None则使用parsed.corners）
    
    Returns:
        规范化的文件名字符串
    """
    # 构建规格部分（尺寸+圆角）
    spec_parts = []
    
    if parsed.layout:
        spec_parts.append(f"{parsed.layout}{parsed.width_cm}x{parsed.height_cm}cm")
    else:
        spec_parts.append(f"{parsed.width_cm}x{parsed.height_cm}cm")
    
    # 添加圆角描述
    corners = corners_override if corners_override is not None else parsed.corners
    if corners:
        corner_names = {'tl': '左上角', 'tr': '右上角', 'bl': '左下角', 'br': '右下角'}
        
        # 检查圆角情况
        unique_values = set(v for v in corners.values() if v > 0)
        non_zero = {k: v for k, v in corners.items() if v > 0}
        
        if len(unique_values) == 1 and len(non_zero) == 4:
            # 四角相同
            r = list(unique_values)[0]
            spec_parts.append(f"四角圆角半径{r}cm")
        else:
            # 各角不同或部分角
            for k in ('tl', 'tr', 'bl', 'br'):
                if k in non_zero and non_zero[k] > 0:
                    spec_parts.append(f"{corner_names[k]}圆角半径{non_zero[k]}cm")
    
    spec_str = ''.join(spec_parts)  # 尺寸和圆角连在一起
    
    return f"{parsed.product_name};{spec_str}.jpg"


def get_image_info(path: str) -> dict:
    """
    获取图片信息（尺寸、DPI、模式）。
    
    Args:
        path: 图片文件路径
    
    Returns:
        包含图片信息的字典
    """
    from PIL import Image
    
    img = Image.open(path)
    info = {
        'width_px': img.width,
        'height_px': img.height,
        'dpi': img.info.get('dpi', (72, 72)),
        'mode': img.mode,
        'size_cm': None,
    }
    
    # 如果有DPI信息，计算物理尺寸
    dpi = info['dpi']
    if dpi and dpi[0] > 0:
        info['size_cm'] = (
            round(img.width * 2.54 / dpi[0], 2),
            round(img.height * 2.54 / dpi[1], 2) if len(dpi) > 1 else round(img.height * 2.54 / dpi[0], 2)
        )
    
    return info