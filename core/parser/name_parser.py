"""
core/parser/name_parser.py
文件名解析器：从文件名提取产品名称、尺寸、圆角要求等信息。

尺寸方向规则：
- 竖版：长边为高，短边为宽（不管数字顺序）
- 横版：长边为宽，短边为高（不管数字顺序）
- 如果命名中出现"横版"，不管在哪个位置都判定为横版

向后兼容：原 core/name_parser.py 已改为薄重导出 shim，旧导入路径继续可用。
"""
from __future__ import annotations
import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


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
    """全角字符 → 半角字符 + 清除所有不可见/异常字符。"""
    if not s:
        return s
        
    # 1. 删除所有不可见/零宽/控制字符
    _INVISIBLE_CHARS = [
        '\u200b',  # zero-width space
        '\u200c',  # zero-width non-joiner
        '\u200d',  # zero-width joiner
        '\u2060',  # word joiner
        '\ufeff',  # BOM / zero-width no-break space
        '\u202a',  # LEFT-TO-RIGHT EMBEDDING
        '\u202b',  # RIGHT-TO-LEFT EMBEDDING
        '\u202c',  # POP DIRECTIONAL FORMATTING
        '\u202d',  # LEFT-TO-RIGHT OVERRIDE
        '\u202e',  # RIGHT-TO-LEFT OVERRIDE
        '\u200e',  # LEFT-TO-RIGHT MARK
        '\u200f',  # RIGHT-TO-LEFT MARK
        '\u2066',  # LEFT-TO-RIGHT ISOLATE
        '\u2067',  # RIGHT-TO-LEFT ISOLATE
        '\u2068',  # FIRST STRONG ISOLATE
        '\u2069',  # POP DIRECTIONAL ISOLATE
        '\u2000',  # EN QUAD
        '\u2001',  # EM QUAD
        '\u2002',  # EN SPACE
        '\u2003',  # EM SPACE
        '\u2004',  # THREE-PER-EM SPACE
        '\u2005',  # FOUR-PER-EM SPACE
        '\u2006',  # SIX-PER-EM SPACE
        '\u2007',  # FIGURE SPACE
        '\u2008',  # PUNCTUATION SPACE
        '\u2009',  # THIN SPACE
        '\u200a',  # HAIR SPACE
        '\u2028',  # LINE SEPARATOR
        '\u2029',  # PARAGRAPH SEPARATOR
    ]
    for ch in _INVISIBLE_CHARS:
        s = s.replace(ch, '')
        
    # 2. 将全角/特殊空格转为普通空格
    _SPACE_CHARS = [
        '\u00a0',  # non-breaking space
        '\u3000',  # ideographic space (全角空格)
        '\u202f',  # NARROW NO-BREAK SPACE
        '\u205f',  # MEDIUM MATHEMATICAL SPACE
    ]
    for ch in _SPACE_CHARS:
        s = s.replace(ch, ' ')
        
    # 3. 全角 → 半角字符映射（注意：字典键不能重复，否则后者覆盖前者）
    table = str.maketrans({
        '０': '0', '１': '1', '２': '2', '３': '3', '４': '4',
        '５': '5', '６': '6', '７': '7', '８': '8', '９': '9',
        '．': '.', '。': '.',  # 全角小数点
        '×': 'x', 'Ｘ': 'x', 'ｘ': 'x', '✕': 'x', '＊': '*',  # 各种乘号
        '，': ',', '；': ';', '：': ':',
        'ｃ': 'c', 'ｍ': 'm', 'Ｃ': 'c', 'Ｍ': 'm',
    })
    s = s.translate(table)
    
    # 4. 将所有连续的空白符压缩为单个空格
    s = re.sub(r'\s+', ' ', s)
    
    return s.strip()


def _extract_size_pair_manual(text: str) -> tuple[float, float] | None:
    """
    字符级手动解析算法（作为正则失败后的终极兜底）。
    逐字符扫描，寻找 "第一个数字" + "乘号/关键字" + "第二个数字" 的模式。
    这种方法完全绕过正则引擎可能的贪婪匹配陷阱，对隐藏字符具有极强的免疫力。
    """
    if not text:
        return None
        
    # 定义乘号字符集
    mul_chars = set('xX*×·⋅')
    
    # 1. 找到文本中所有数字的起止位置
    num_pattern = re.compile(r'\d+(?:\.\d+)?')
    nums_found = []
    for m in num_pattern.finditer(text):
        nums_found.append((m.start(), m.end(), m.group()))
        
    if len(nums_found) < 2:
        return None
        
    # 2. 遍历所有相邻数字对，检查它们之间是否存在乘号
    for i in range(len(nums_found) - 1):
        start_a, end_a, str_a = nums_found[i]
        start_b, end_b, str_b = nums_found[i+1]
        
        # 提取两个数字之间的文本
        between_text = text[end_a:start_b]
        
        # 检查中间文本是否包含乘号字符
        has_mul = any(ch in mul_chars for ch in between_text)
        
        if has_mul:
            try:
                a = float(str_a)
                b = float(str_b)
                logger.debug(f"[name_parser] MANUAL parse matched: '{str_a}' x '{str_b}' -> {a} x {b}")
                return (a, b)
            except ValueError:
                continue
                
    # 3. 如果没有乘号，直接取前两个数字（比如 "49.5 114.5" 或 "49.5, 114.5"）
    if len(nums_found) >= 2:
        _, _, str_a = nums_found[0]
        _, _, str_b = nums_found[1]
        # 检查它们之间是否只有非字母数字和乘号的字符（如空格、逗号）
        between_text = text[nums_found[0][1]:nums_found[1][0]]
        if not re.search(r'[a-zA-Z\u4e00-\u9fff]', between_text):
            try:
                a = float(str_a)
                b = float(str_b)
                logger.debug(f"[name_parser] MANUAL fallback (no mul sign) matched: '{str_a}' x '{str_b}' -> {a} x {b}")
                return (a, b)
            except ValueError:
                pass
                
    return None


def _extract_size_pair(text: str) -> tuple[float, float] | None:
    """
    多层容错的尺寸提取（按可靠性从高到低排序）：
    S1: 带单位的标准正则
    S2: 无单位的标准正则
    S3: 宽松正则
    S4: findall 宽松正则
    S5: 提取所有数字取前两个
    S6: 【新增】字符级手动扫描（绕开所有正则陷阱）
    """
    if not text:
        return None

    strategies = [
        # S1: 带单位的完整格式 —— 强制要求 cm/厘米/公分
        r'(\d+(?:\.\d+)?)\s*[xX*×Ｘｘ✕·⋅]\s*(\d+(?:\.\d+)?)\s*(cm|厘米|公分)',
        # S2: 无单位
        r'(\d+(?:\.\d+)?)\s*[xX*×Ｘｘ✕·⋅]\s*(\d+(?:\.\d+)?)',
        # S3: 极宽松
        r'(\d+(?:\.\d+)?).*?[xX*×Ｘｘ✕·⋅].*?(\d+(?:\.\d+)?)',
    ]

    for i, pat in enumerate(strategies):
        m = re.search(pat, text, flags=re.IGNORECASE | re.DOTALL)
        if m:
            raw_a = m.group(1)
            raw_b = m.group(2)
            try:
                # 双重 round 抑制浮点误差
                a = round(round(float(raw_a), 6), 2)
                b = round(round(float(raw_b), 6), 2)
                logger.debug(f"[name_parser] size strategy S{i+1} matched: '{raw_a}' x '{raw_b}' -> {a} x {b}")
                return (a, b)
            except ValueError:
                logger.debug(f"[name_parser] S{i+1} ValueError for groups: {repr(raw_a)}, {repr(raw_b)}")
                continue

    # S4: 找出所有 "数字x数字" 模式，取第一个
    all_matches = re.findall(r'(\d+(?:\.\d+)?)\s*[xX*×Ｘｘ✕·⋅]\s*(\d+(?:\.\d+)?)', text, flags=re.IGNORECASE)
    if all_matches:
        raw_a, raw_b = all_matches[0]
        a = round(round(float(raw_a), 6), 2)
        b = round(round(float(raw_b), 6), 2)
        logger.debug(f"[name_parser] size strategy S4 matched: '{raw_a}' x '{raw_b}' -> {a} x {b}")
        return (a, b)

    # S5: 兜底 — 提取所有数字，取前两个
    all_nums = re.findall(r'\d+(?:\.\d+)?', text)
    if len(all_nums) >= 2:
        raw_a, raw_b = all_nums[0], all_nums[1]
        a = round(round(float(raw_a), 6), 2)
        b = round(round(float(raw_b), 6), 2)
        logger.debug(f"[name_parser] size strategy S5 (fallback) matched: '{raw_a}' x '{raw_b}' -> {a} x {b}")
        return (a, b)
        
    # S6: 终极兜底 — 字符级手动解析
    manual_result = _extract_size_pair_manual(text)
    if manual_result:
        return manual_result

    logger.warning(f"[name_parser] size parsing FAILED for text: {repr(text[:200])}")
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
    # 仅移除已知的图片扩展名（不能使用 \.[^.]+$ ，否则会把尺寸小数部分误当扩展名删掉）
    raw_name = re.sub(r'\.(jpg|jpeg|png|psd|psb|bmp|tiff|tif|webp|gif)$', '', filename, flags=re.IGNORECASE)
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
        logger.debug(f"[name_parser] FINAL: a={a}, b={b}, layout={layout}, width={result.width_cm}, height={result.height_cm}")
    else:
        # 无尺寸信息时默认横版
        result.layout = '横版'
        logger.warning(f"[name_parser] FINAL: no dimensions parsed, default layout=横版")
    
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


def _extract_radius_from_text(text: str) -> float | None:
    """
    从文本片段中提取圆角半径值。
    用于两角组合中"左下角和右下角"之后的半径描述。

    支持的表述：
    - 做3cm半径圆角 / 做3厘米半径圆弧角
    - 圆角半径3cm / 是圆角半径3cm / 各一个圆角半径3cm
    - 是圆角3cm半径
    - 半径3cm / 3cm半径
    """
    if not text:
        return None

    radius_patterns = [
        # 做 X cm 半径? 圆角  (做3cm半径圆弧角 → 做3cm半径圆角)
        r'做\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)\s*半径?\s*圆角',
        # (各一个)? (是)? 圆角 半径 X cm  (圆角半径3cm / 各一个圆角半径3cm / 是圆角半径3cm)
        r'(?:各\s*一个\s*)?(?:是\s*)?圆角\s*半径\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)',
        # (各一个)? (是)? 圆角 X cm 半径  (是圆角3cm半径)
        r'(?:各\s*一个\s*)?(?:是\s*)?圆角\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)\s*半径',
        # (各一个)? (是)? 圆角 半径? X cm  (圆角3cm)
        r'(?:各\s*一个\s*)?(?:是\s*)?圆角(?:半径)?\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)',
        # 半径 X cm
        r'半径\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)',
        # X cm 半径
        r'(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)\s*半径',
        # 无单位回退
        r'做\s*(\d+(?:[.]\d+)?)\s*半径?\s*圆角',
        r'圆角\s*半径\s*(\d+(?:[.]\d+)?)',
        r'半径\s*(\d+(?:[.]\d+)?)',
    ]

    for pat in radius_patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            logger.debug(f"[name_parser] radius extracted via '{pat}': {m.group(1)}")
            return float(m.group(1))
    return None


def _parse_corners(spec: str) -> dict[str, float] | None:
    """
    从字符串中解析圆角要求。

    支持的格式：
    === 四角组合 ===
    - 四角圆角半径2cm / 四个圆角半径2cm / 4个圆角半径2cm
    - 四个角做2.5cm半径圆弧角 / 4个角做2.5厘米半径圆弧角

    === 两角组合 ===
    - 左下角和右下角做3cm半径圆弧角
    - 左下角和右下角各一个圆角半径3cm
    - 左下角和右下角圆角半径3cm
    - 左下角右下角是圆角半径3cm

    === 单角 ===
    - 左下角圆角半径3.1cm / 左下角半径3.1cm
    - 左下角一个圆角半径3.1厘米
    - 左下角做3.1cm半径圆弧角
    - 左下角是圆角3.1cm半径
    """
    # 先做全角 → 半角规范化，避免小数和单位匹配失败
    s = _normalize_str(spec) if spec else ""
    if not s:
        return None

    # 圆弧角/弧角/圆角弧 统一为 "圆角"，简化后续正则
    for alt in ('圆弧角', '弧角', '圆角弧'):
        s = s.replace(alt, '圆角')

    corner_map = {
        '左上角': 'tl',
        '右上角': 'tr',
        '左下角': 'bl',
        '右下角': 'br',
    }

    # ===== 阶段 0: 四角组合（最高优先级，避免被单角逻辑误匹配）=====
    all_corners_patterns = [
        # 4个/四个 角? 做 X cm 半径? 圆角  (四个角做2.5cm半径圆弧角)
        r'(?:4个|四个|4|四)\s*个?\s*角?\s*做\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)\s*半径?\s*圆角',
        # 4个/四个 角? 圆角 半径? X cm  (四个角圆角半径2.5cm / 4个圆角半径2.5cm)
        r'(?:4个|四个|4|四)\s*个?\s*角?\s*圆角(?:半径)?\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)',
        # 数字 + 个? 圆角 半径? X cm  (4个圆角半径2.5cm)
        r'(\d+|[一二三四五])\s*个?\s*圆角(?:半径)?\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)',
        # 四角 / 四个角 半径 X cm  (四角半径5cm)
        r'(?:四角|四个角)\s*半径\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)',
        # 四角 / 四个 圆角 半径? X cm  (四角圆角半径2cm / 四个圆角半径2cm)
        r'(?:四角|四个)\s*圆角(?:半径)?\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)',
        # 无单位回退
        r'(?:4个|四个|4|四)\s*个?\s*角?\s*做\s*(\d+(?:[.]\d+)?)\s*半径?\s*圆角',
        r'(?:4个|四个|4|四)\s*个?\s*角?\s*圆角(?:半径)?\s*(\d+(?:[.]\d+)?)',
        r'(\d+|[一二三四五])\s*个?\s*圆角(?:半径)?\s*(\d+(?:[.]\d+)?)',
        r'(?:四角|四个角)\s*半径\s*(\d+(?:[.]\d+)?)',
        r'(?:四角|四个)\s*圆角(?:半径)?\s*(\d+(?:[.]\d+)?)',
    ]

    for pattern in all_corners_patterns:
        m = re.search(pattern, s, flags=re.IGNORECASE)
        if m:
            if len(m.groups()) == 1:
                radius = float(m.group(1))
                result = {'tl': radius, 'tr': radius, 'bl': radius, 'br': radius}
                logger.debug(f"[name_parser] all-corners matched: {result}")
                return result
            elif len(m.groups()) == 2:
                count_str = m.group(1)
                count = _cn_to_int(count_str)
                radius = float(m.group(2))
                if count == 4:
                    result = {'tl': radius, 'tr': radius, 'bl': radius, 'br': radius}
                    logger.debug(f"[name_parser] all-corners matched: {result}")
                    return result

    # ===== 阶段 1: 两角组合 (X角和Y角 / X角Y角) =====
    pair_pattern = re.compile(
        r'(左上角|右上角|左下角|右下角)\s*(?:和|与|、)?\s*(左上角|右上角|左下角|右下角)'
    )
    for m in pair_pattern.finditer(s):
        c1_name, c2_name = m.group(1), m.group(2)
        if c1_name == c2_name:
            continue
        c1_key, c2_key = corner_map[c1_name], corner_map[c2_name]

        # 在双角组合之后（优先）或之前的文本中寻找半径
        radius_value = _extract_radius_from_text(s[m.end():])
        if radius_value is None:
            radius_value = _extract_radius_from_text(s[:m.start()])

        if radius_value is not None:
            result = {k: 0.0 for k in ('tl', 'tr', 'bl', 'br')}
            result[c1_key] = radius_value
            result[c2_key] = radius_value
            logger.debug(f"[name_parser] pair corners matched: {c1_name}+{c2_name} -> {result}")
            return result

    # ===== 阶段 2: 单角 =====
    result = {}
    for cn_name, key in corner_map.items():
        patterns = [
            # 位置 + (一个)? + (是)? + 圆角 + 半径? + 数字 + 单位
            rf'{cn_name}(?:一个)?(?:是)?\s*圆角(?:半径)?\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)',
            # 位置 + (圆角)? + 半径 + 数字 + 单位
            rf'{cn_name}(?:圆角)?\s*半径\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)',
            # 口语：位置 + 做 + 数字 + 单位 + 半径? + 圆角
            rf'{cn_name}.{{0,3}}做\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)\s*半径?\s*圆角',
            # 口语：位置 + 数字 + 单位 + 半径? + 圆角
            rf'{cn_name}\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)\s*半径?\s*圆角',
            # 口语：位置 + 是 + 圆角 + 数字 + 单位 + 半径  (左下角是圆角3.1cm半径)
            rf'{cn_name}是\s*圆角\s*(\d+(?:[.]\d+)?)\s*(?:cm|厘米|公分)\s*半径',
            # 无单位回退
            rf'{cn_name}(?:一个)?(?:是)?\s*圆角(?:半径)?\s*(\d+(?:[.]\d+)?)',
            rf'{cn_name}(?:圆角)?\s*半径\s*(\d+(?:[.]\d+)?)',
        ]

        m = None
        for pat in patterns:
            m = re.search(pat, s, flags=re.IGNORECASE)
            if m:
                logger.debug(f"[name_parser] corner '{key}' matched pattern: {pat} -> {m.group(1)}")
                break

        if m:
            result[key] = float(m.group(1))

    if result:
        for k in ('tl', 'tr', 'bl', 'br'):
            if k not in result:
                result[k] = 0.0
        logger.debug(f"[name_parser] individual corners: {result}")
        return result

    logger.warning(f"[name_parser] no corners parsed from: {repr(s[:200])}")
    return None


def _fmt_num(v: float) -> str:
    """格式化数字：整数时去掉小数点，如 161.0 → '161'，2.5 → '2.5'"""
    if v == int(v):
        return str(int(v))
    return f"{v:g}"


def format_corner_spec(corners: dict[str, float]) -> str:
    """
    根据圆角配置生成规范化的圆角描述字符串。

    命名规则：
    - 四角同半径: "4个圆角半径{x}cm"
    - 两角同半径: "{角1}和{角2}圆角半径{x}cm"
    - 单角/其他: 逐个列出 "{角}圆角半径{x}cm"

    Args:
        corners: 角->半径 映射，如 {'tl': 0, 'tr': 0, 'bl': 3.0, 'br': 3.0}

    Returns:
        圆角描述字符串（无圆角时返回空字符串）
    """
    if not corners:
        return ""

    corner_names = {'tl': '左上角', 'tr': '右上角', 'bl': '左下角', 'br': '右下角'}
    non_zero = {k: v for k, v in corners.items() if v > 0}

    if not non_zero:
        return ""

    unique_radii = set(non_zero.values())

    # 四角同半径
    if len(non_zero) == 4 and len(unique_radii) == 1:
        r = list(unique_radii)[0]
        return f"4个圆角半径{_fmt_num(r)}cm"

    # 两角同半径（恰好两个非零角且半径相同）
    if len(non_zero) == 2 and len(unique_radii) == 1:
        r = list(unique_radii)[0]
        keys = [k for k in ('tl', 'tr', 'bl', 'br') if k in non_zero]
        names = [corner_names[k] for k in keys]
        return f"{'和'.join(names)}圆角半径{_fmt_num(r)}cm"

    # 其他情况（单角、三角、混合半径）→ 逐个列出
    parts = []
    for k in ('tl', 'tr', 'bl', 'br'):
        if k in non_zero and non_zero[k] > 0:
            parts.append(f"{corner_names[k]}圆角半径{_fmt_num(non_zero[k])}cm")
    return ''.join(parts)


def generate_filename(parsed: ParsedFilename, corners_override: dict[str, float] | None = None) -> str:
    """
    根据解析结果和可选的圆角覆盖，生成规范化的文件名。

    输出格式: 产品名称;[竖版]短边x长边cm圆角描述.jpg
    示例: 双面格;88x161cm4个圆角半径5cm.jpg (横版无前缀)
          双面格;竖版41x55cm右下角圆角半径2cm.jpg (竖版加前缀)

    规则:
    - 竖版添加"竖版"前缀，横版不加前缀
    - 尺寸默认短边在前、长边在后
    - 整数值去掉小数点（161.0 → 161）
    - 圆角命名使用 format_corner_spec 统一格式

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
    corner_spec = format_corner_spec(corners)
    if corner_spec:
        spec_parts.append(corner_spec)

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
