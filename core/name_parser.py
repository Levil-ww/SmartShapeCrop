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
    material: str = ""              # 材质，如 "双面格"
    pattern_name: str = ""          # 花型基础名，如 "简织"
    shape_keywords: list = None     # 形状关键词列表，如 ["竖版"]
    is_custom: bool = False         # 是否为定制类型


# 中文数字映射
CN_NUM = {'零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}

# 形状后缀关键词
SHAPE_SUFFIXES = ['方形', '弧形', '圆形', '横版', '竖版', '裁剪有图', '横', '竖', '方', '弧', '圆']


def _cn_to_int(s: str) -> int | None:
    """将中文数字转为整数"""
    if s in CN_NUM:
        return CN_NUM[s]
    try:
        return int(s)
    except ValueError:
        return None


def get_base_pattern_name(name: str) -> str:
    """提取花型基础名（去除形状修饰后缀）"""
    if not name:
        return ""
    result = str(name).strip()
    changed = True
    while changed:
        changed = False
        for suffix in SHAPE_SUFFIXES:
            if result.endswith(suffix) and len(result) > len(suffix):
                result = result[:-len(suffix)]
                changed = True
                break
    return result.strip()


def normalize_flower_name(name_part: str, size_part: str = "") -> tuple[str, list]:
    """
    标准化花型名，处理等价命名形式。
    返回：(base_pattern_name, shape_keywords)
    """
    if not name_part:
        return "", []

    shape_keywords = []
    found_keywords = []

    for keyword in SHAPE_SUFFIXES:
        if keyword == '圆':
            temp = name_part.replace('圆角', '').replace('圆弧', '')
            if '圆' in temp and '圆' not in found_keywords:
                found_keywords.append('圆')
        elif keyword in name_part and keyword not in found_keywords:
            found_keywords.append(keyword)

    if size_part:
        for keyword in SHAPE_SUFFIXES:
            if keyword == '圆':
                temp = size_part.replace('圆角', '').replace('圆弧', '')
                if '圆' in temp and '圆' not in found_keywords:
                    found_keywords.append('圆')
            elif keyword in size_part and keyword not in found_keywords:
                found_keywords.append(keyword)
        if '直径' in size_part and '圆形' not in found_keywords:
            found_keywords.append('圆形')

    if '圆' in found_keywords and '圆形' not in found_keywords:
        found_keywords.append('圆形')
    if '圆形' in found_keywords and '圆' not in found_keywords:
        found_keywords.append('圆')

    base_name = get_base_pattern_name(name_part)
    return base_name, found_keywords


def parse_size_dims(size_str: str) -> tuple | None:
    """
    解析尺寸字符串为两个浮点数。
    支持：矩形(65x80)、圆形(直径42cm/136cm直径/58cm圆形)
    """
    if not size_str:
        return None
    s = _normalize_str(size_str)

    if '直径' in s:
        diameter_match = re.search(r'直径\s*(\d+(?:[.]\d+)?)', s)
        if diameter_match:
            diameter = float(diameter_match.group(1))
            return (diameter, diameter)
        diameter_match = re.search(r'(\d+(?:[.]\d+)?)\s*(?:cm)?\s*直径', s, flags=re.IGNORECASE)
        if diameter_match:
            diameter = float(diameter_match.group(1))
            return (diameter, diameter)

    circle_match = re.search(r'(\d+(?:[.]\d+)?)\s*(?:cm)?\s*圆形', s, flags=re.IGNORECASE)
    if circle_match:
        diameter = float(circle_match.group(1))
        return (diameter, diameter)
    circle_match = re.search(r'圆形\s*(\d+(?:[.]\d+)?)', s)
    if circle_match:
        diameter = float(circle_match.group(1))
        return (diameter, diameter)

    size_match = re.search(r'(\d+(?:[.]\d+)?)\s*[xX]\s*(\d+(?:[.]\d+)?)', s, flags=re.IGNORECASE)
    if size_match:
        dim1 = float(size_match.group(1))
        dim2 = float(size_match.group(2))
        return (dim1, dim2)

    return None


def _normalize_str(s: str) -> str:
    """全角字符 → 半角字符 + 清除不可见字符，避免正则匹配失败导致精度丢失。"""
    if not s:
        return s
    # 不可见/零宽字符（可能从网页/文档复制而来）
    _INVISIBLE = (
        '\u200b',  # zero-width space
        '\u200c',  # zero-width non-joiner
        '\u200d',  # zero-width joiner
        '\u2060',  # word joiner
        '\ufeff',  # BOM / zero-width no-break space
        '\u00a0',  # non-breaking space → 普通空格
        '\u3000',  # fullwidth space → 普通空格
    )
    for ch in _INVISIBLE:
        s = s.replace(ch, '')

    # 全角 → 半角字符映射
    table = str.maketrans({
        '０': '0', '１': '1', '２': '2', '３': '3', '４': '4',
        '５': '5', '６': '6', '７': '7', '８': '8', '９': '9',
        '．': '.', '。': '.',  # 全角小数点兼容
        '×': 'x', 'Ｘ': 'x', 'ｘ': 'x', '✕': 'x',  # 各种乘号
        '，': ',', '；': ';',
        '　': ' ',
        'ｃ': 'c', 'ｍ': 'm', 'Ｃ': 'c', 'Ｍ': 'm',
    })
    return s.translate(table)


def _extract_size_pair(text: str) -> tuple[float, float] | None:
    """
    多层容错的尺寸提取：
    1. 标准正则（带单位）
    2. 标准正则（不带单位）
    3. 宽松正则（允许任意空白/分隔符）
    4. 兜底：找出所有形如 "数字x数字" 的片段，取第一个
    5. 终极兜底：找出所有数字，取前两个
    """
    if not text:
        return None

    strategies = [
        # S1: 带单位的完整格式
        r'(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)\s*(cm|厘米|公分)',
        # S2: 无单位
        r'(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)',
        # S3: 极宽松 —— 用 \s* 兼容任意空白，用 .*? 尽可能宽松
        r'(\d+(?:\.\d+)?).*?[xX].*?(\d+(?:\.\d+)?)',
    ]

    for i, pat in enumerate(strategies):
        m = re.search(pat, text, flags=re.IGNORECASE | re.DOTALL)
        if m:
            try:
                a = float(m.group(1))
                b = float(m.group(2))
                print(f"[name_parser] size strategy S{i+1} matched: {m.group(1)} x {m.group(2)} -> {a} x {b}")
                return (a, b)
            except ValueError:
                continue

    # S4: 找出所有 "数字x数字" 模式（宽松匹配）
    all_matches = re.findall(r'(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)', text, flags=re.IGNORECASE)
    if all_matches:
        a = float(all_matches[0][0])
        b = float(all_matches[0][1])
        print(f"[name_parser] size strategy S4 matched: {all_matches[0][0]} x {all_matches[0][1]} -> {a} x {b}")
        return (a, b)

    # S5: 终极兜底 — 提取所有数字，取前两个
    all_nums = re.findall(r'\d+(?:\.\d+)?', text)
    if len(all_nums) >= 2:
        a = float(all_nums[0])
        b = float(all_nums[1])
        print(f"[name_parser] size strategy S5 (fallback) matched: {all_nums[0]} x {all_nums[1]} -> {a} x {b}")
        return (a, b)

    print(f"[name_parser] size parsing FAILED for text: {repr(text[:200])}")
    return None


def parse_filename(filename: str) -> ParsedFilename:
    """
    解析文件名，提取尺寸和圆角信息。

    尺寸方向规则：
    - 无显式"横版/竖版"关键词时，默认横版：长边为宽（MAX），短边为高（MIN）
    - 出现"横版/横款"即判定为横版：长边为宽（MAX），短边为高（MIN）
    - 出现"竖版/竖款"即判定为竖版：短边为宽（MIN），长边为高（MAX）
    - 例：45x220 或 220x50 → 宽=长边, 高=短边
         竖版25x60 或 竖版60x25 → 宽=短边, 高=长边

    Args:
        filename: 文件名（含或不含扩展名）

    Returns:
        ParsedFilename 对象
    """
    raw_name = re.sub(r'\.[^.]+$', '', filename)
    # 先做一次全角 → 半角规范化，确保后续正则准确匹配小数部分、x和单位
    name = _normalize_str(raw_name)

    result = ParsedFilename(raw_filename=filename)
    result.shape_keywords = []

    parts = re.split(r'[;,]', name, maxsplit=1)

    if len(parts) > 1:
        result.product_name = parts[0].strip()
        spec = parts[1].strip()
    else:
        spec = name
        result.product_name = name

    # 检测显式方向关键词（在规范化后的完整名称里检测）
    explicit_layout = None
    if '横版' in name or '横款' in name:
        explicit_layout = '横版'
    elif '竖版' in name or '竖款' in name:
        explicit_layout = '竖版'

    # 尺寸识别：使用多层容错的 _extract_size_pair，先在 spec 中查找，找不到再在完整 name 中查找
    dims = _extract_size_pair(spec)
    if dims is None:
        dims = _extract_size_pair(name)

    if dims:
        a, b = dims

        layout = explicit_layout if explicit_layout else '横版'
        if layout == '横版':
            # 横版：长边为宽，短边为高
            result.width_cm = max(a, b)
            result.height_cm = min(a, b)
        else:  # 竖版
            # 竖版：短边为宽，长边为高
            result.width_cm = min(a, b)
            result.height_cm = max(a, b)
        result.layout = layout
        print(f"[name_parser] FINAL: a={a}, b={b}, layout={layout}, width={result.width_cm}, height={result.height_cm}")
    else:
        # 无尺寸信息时默认横版
        result.layout = '横版'
        print(f"[name_parser] FINAL: no dimensions parsed, default layout=横版")
    
    corners = _parse_corners(name)
    result.corners = corners if corners else None
    
    # 提取材质和花型名
    if result.product_name and '-' in result.product_name:
        name_parts = result.product_name.split('-')
        if len(name_parts) >= 2:
            result.material = name_parts[0].strip()
            result.pattern_name = name_parts[-1].strip()
            for part in name_parts:
                if '定制' in part:
                    result.is_custom = True
                    break
        else:
            result.pattern_name = result.product_name
    else:
        result.pattern_name = result.product_name
    
    # 提取形状关键词
    base_name, shape_kw = normalize_flower_name(result.pattern_name or result.product_name, result.layout or "")
    if base_name:
        result.pattern_name = base_name
    result.shape_keywords = shape_kw
    
    return result


def _parse_corners(spec: str) -> dict[str, float] | None:
    """
    从字符串中解析圆角要求。

    支持的格式：
    - 四角圆角半径2cm / 四个圆角半径2cm / 4个圆角半径2cm
    - 左上角圆角半径2cm / 右下角圆角半径2cm
    - 左下角圆角半径2.5厘米 / 右上角圆角半径3cm
    - 右下角圆角半径2厘米
    - 左下角做3cm半径圆弧角 / 右上角做2cm半径弧角  (口语化表述)
    """
    # 先做全角 → 半角规范化，避免小数和单位匹配失败
    s = _normalize_str(spec) if spec else ""
    if not s:
        return None

    result = {}

    # 圆角/圆弧角/弧角 统一为 "角" 关键字
    # 先把 "圆弧角"、"弧角" 统一替换为 "圆角"，简化后续正则
    for alt in ('圆弧角', '弧角', '圆角弧'):
        s = s.replace(alt, '圆角')

    corner_map = {
        '左上角': 'tl',
        '右上角': 'tr',
        '左下角': 'bl',
        '右下角': 'br',
    }

    for cn_name, key in corner_map.items():
        # 多种表述方式（放宽中间的连接词）：
        # 1. 左下角圆角3cm / 左下角圆角半径3cm / 左下角半径3cm
        # 2. 左下角做3cm半径圆角 / 左下角做3cm圆角
        # 3. 左下角3cm圆角 / 左下角3cm半径圆角
        patterns = [
            # 标准：位置 + 圆角 + 半径 + 数字 + 单位
            rf'{cn_name}?圆角(?:半径)?\s*(\d+(?:[.]\d+)?)\s*(cm|厘米|公分)',
            # 位置 + 半径 + 数字 + 单位
            rf'{cn_name}(?:圆角)?半径\s*(\d+(?:[.]\d+)?)\s*(cm|厘米|公分)',
            # 口语：位置 + 做 + 数字 + 单位 + 半径 + 圆角
            rf'{cn_name}.{0,3}做\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)\s*半径?\s*圆角',
            # 口语：位置 + 数字 + 单位 + 半径 + 圆角
            rf'{cn_name}\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)\s*半径?\s*圆角',
            # 口语：位置 + 做 + 数字 + 单位 + 圆角
            rf'{cn_name}.{0,3}做\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)\s*圆角',
            # 无单位回退
            rf'{cn_name}?圆角(?:半径)?\s*(\d+(?:[.]\d+)?)',
            rf'{cn_name}(?:圆角)?半径\s*(\d+(?:[.]\d+)?)',
        ]

        m = None
        for pat in patterns:
            m = re.search(pat, s, flags=re.IGNORECASE)
            if m:
                print(f"[name_parser] corner '{key}' matched pattern: {pat} -> {m.group(1)}")
                break

        if m:
            result[key] = float(m.group(1))

    all_corners_patterns = [
        # 带单位（强制匹配）
        r'(?:四角|四个)\s*圆角(?:半径)?\s*(\d+(?:[.]\d+)?)\s*(cm|厘米|公分)',
        r'(\d+|[一二三四五])\s*个?\s*圆角(?:半径)?\s*(\d+(?:[.]\d+)?)\s*(cm|厘米|公分)',
        r'(?:四角|四个)\s*圆角半径\s*(\d+(?:[.]\d+)?)\s*(cm|厘米|公分)',
        # 无单位回退
        r'(?:四角|四个)\s*圆角(?:半径)?\s*(\d+(?:[.]\d+)?)',
        r'(\d+|[一二三四五])\s*个?\s*圆角(?:半径)?\s*(\d+(?:[.]\d+)?)',
        r'(?:四角|四个)\s*圆角半径\s*(\d+(?:[.]\d+)?)',
    ]

    for pattern in all_corners_patterns:
        m = re.search(pattern, s, flags=re.IGNORECASE)
        if m:
            if len(m.groups()) == 1:
                radius = float(m.group(1))
                result = {'tl': radius, 'tr': radius, 'bl': radius, 'br': radius}
            elif len(m.groups()) == 2:
                count_str = m.group(1)
                count = _cn_to_int(count_str)
                radius = float(m.group(2))

                if count == 4:
                    result = {'tl': radius, 'tr': radius, 'bl': radius, 'br': radius}

            if result:
                print(f"[name_parser] all-corners matched: {result}")
                return result

    if result:
        all_keys = ['tl', 'tr', 'bl', 'br']
        for k in all_keys:
            if k not in result:
                result[k] = 0.0
        print(f"[name_parser] individual corners: {result}")
        return result

    print(f"[name_parser] no corners parsed from: {repr(s[:200])}")
    return None


def _fmt_num(v: float) -> str:
    """格式化数字：整数时去掉小数点，如 161.0 → '161'，2.5 → '2.5'"""
    if v == int(v):
        return str(int(v))
    return f"{v:g}"


def generate_filename(parsed: ParsedFilename, corners_override: dict[str, float] | None = None) -> str:
    """
    根据解析结果和可选的圆角覆盖，生成规范化的文件名。

    输出格式: 产品名称;[竖版]短边x长边cm圆角描述.jpg
    示例: 双面格;88x161cm四角半径5cm.jpg (横版无前缀)
          双面格;竖版41x55cm右下角半径2cm.jpg (竖版加前缀)

    规则:
    - 竖版添加"竖版"前缀，横版不加前缀
    - 尺寸默认短边在前、长边在后
    - 整数值去掉小数点（161.0 → 161）

    Args:
        parsed: 解析后的文件名信息
        corners_override: 可选的圆角覆盖（如果为None则使用parsed.corners）

    Returns:
        规范化的文件名字符串
    """
    w = parsed.width_cm
    h = parsed.height_cm
    short_side = min(w, h)
    long_side = max(w, h)
    size_str = f"{_fmt_num(short_side)}x{_fmt_num(long_side)}cm"
    if parsed.layout == '竖版':
        spec_parts = [f"竖版{size_str}"]
    else:
        spec_parts = [size_str]

    corners = corners_override if corners_override is not None else parsed.corners
    if corners:
        corner_names = {'tl': '左上角', 'tr': '右上角', 'bl': '左下角', 'br': '右下角'}

        unique_values = set(v for v in corners.values() if v > 0)
        non_zero = {k: v for k, v in corners.items() if v > 0}

        if len(unique_values) == 1 and len(non_zero) == 4:
            r = list(unique_values)[0]
            spec_parts.append(f"四个圆角半径{_fmt_num(r)}cm")
        else:
            for k in ('tl', 'tr', 'bl', 'br'):
                if k in non_zero and non_zero[k] > 0:
                    spec_parts.append(f"{corner_names[k]}半径{_fmt_num(non_zero[k])}cm")

    spec_str = ''.join(spec_parts)

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
    
    dpi = info['dpi']
    if dpi and dpi[0] > 0:
        info['size_cm'] = (
            round(img.width * 2.54 / dpi[0], 2),
            round(img.height * 2.54 / dpi[1], 2) if len(dpi) > 1 else round(img.height * 2.54 / dpi[0], 2)
        )
    
    return info