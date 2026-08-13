"""
测试：core.name_parser 文件名解析

覆盖（基于 project_memory 中已修复的 bug 用例）：
  - 尺寸解析：含小数（114.5x49.5cm）、含 CM 大写、各种 × 符号
  - 圆角解析：单角 / 四角 / 两角组合 / 半径值
  - 方向识别：竖版 / 横版
  - generate_filename 往返一致性
  - format_corner_spec 命名格式
"""
import pytest
from core.parser.name_parser import (
    parse_filename,
    parse_size_dims,
    generate_filename,
    format_corner_spec,
    _parse_corners,
    _fmt_num,
)


class TestSizeParsing:
    """尺寸解析（project_memory 中的关键 bug 修复用例）"""

    def test_basic_size(self):
        """基础尺寸 55x41cm"""
        w, h = parse_size_dims("55x41cm")
        assert (w, h) == (55.0, 41.0)

    def test_decimal_size(self):
        """小数尺寸 114.5x49.5cm（曾误识别为 114.0x49.5 的 bug）"""
        w, h = parse_size_dims("114.5x49.5cm")
        assert w == 114.5
        assert h == 49.5

    def test_decimal_large_size(self):
        """225.5x43.2CM 大写单位（曾误识别为 225.0x43.2 的 bug）"""
        w, h = parse_size_dims("225.5x43.2CM")
        assert w == 225.5
        assert h == 43.2

    def test_full_width_x(self):
        """全角 × 符号"""
        w, h = parse_size_dims("55×41cm")
        assert (w, h) == (55.0, 41.0)

    def test_star_separator(self):
        """星号 * 分隔"""
        w, h = parse_size_dims("55*41cm")
        assert (w, h) == (55.0, 41.0)

    def test_2_decimal_precision(self):
        """两位小数精度"""
        w, h = parse_size_dims("49.25x114.50cm")
        assert w == 49.25
        assert h == 114.5


class TestParseFilename:
    """完整文件名解析"""

    def test_vertical_layout(self):
        """竖版尺寸应正确识别长边为高"""
        parsed = parse_filename("双面格-定制-定制尺寸-简织;竖版55x41cm右下角圆角半径2厘米")
        assert parsed.layout == '竖版'
        # 竖版：短边为宽，长边为高
        assert parsed.width_cm == 41.0
        assert parsed.height_cm == 55.0

    def test_horizontal_layout(self):
        """横版：长边为宽，短边为高"""
        parsed = parse_filename("双面格-定制-定制尺寸-简织;55x41cm右下角半径2cm")
        # 无"竖版"关键字 → 横版
        assert parsed.layout == '横版'
        assert parsed.width_cm == 55.0
        assert parsed.height_cm == 41.0

    def test_corner_br_2cm(self):
        """右下角圆角半径2cm"""
        parsed = parse_filename("双面格;55x41cm右下角圆角半径2cm")
        assert parsed.corners is not None
        assert parsed.corners.get('br') == 2.0
        assert parsed.corners.get('tl', 0) == 0

    def test_corner_br_3cm_variant(self):
        """左下角做3cm半径圆弧角（不同表述方式）"""
        parsed = parse_filename("双面格-定制-定制尺寸-戴安娜;49.5x114.5cm左下角做3cm半径圆弧角")
        assert parsed.corners is not None
        assert parsed.corners.get('bl') == 3.0

    def test_all_corners(self):
        """四角半径5cm"""
        parsed = parse_filename("双面格;88x161cm四角半径5cm")
        assert parsed.corners is not None
        for ck in ('tl', 'tr', 'bl', 'br'):
            assert parsed.corners[ck] == 5.0

    def test_large_radius_6cm(self):
        """6cm 大圆角"""
        parsed = parse_filename("双面格;43.2x225.5cm右下角圆角半径6cm")
        assert parsed.corners is not None
        assert parsed.corners.get('br') == 6.0

    def test_no_corners(self):
        """无圆角的文件名"""
        parsed = parse_filename("双面格-定制-简织;55x41cm")
        assert parsed.corners is None

    def test_decimal_in_full_filename(self):
        """完整文件名中含小数尺寸（bug 回归用例）"""
        parsed = parse_filename("双面格-定制-定制尺寸-戴安娜;49.5x114.5cm左下角做3cm半径圆弧角")
        # 无显式方向默认横版：长边(114.5)为宽，短边(49.5)为高
        assert parsed.width_cm == 114.5
        assert parsed.height_cm == 49.5
        assert parsed.layout == '横版'

    def test_material_extraction(self):
        """材质（双面格）应被提取"""
        parsed = parse_filename("双面格-定制-定制尺寸-简织;55x41cm")
        assert parsed.material == '双面格' or '双面格' in (parsed.material or '')


class TestFmtNum:
    """_fmt_num 数字格式化"""

    def test_integer_no_decimal(self):
        assert _fmt_num(161.0) == '161'

    def test_decimal_kept(self):
        assert _fmt_num(2.5) == '2.5'

    def test_zero(self):
        assert _fmt_num(0.0) == '0'


class TestGenerateFilename:
    """generate_filename 往返一致性"""

    def test_roundtrip_vertical(self):
        """解析 → 重新生成 → 应保持关键信息"""
        original = "双面格-定制-定制尺寸-简织;竖版55x41cm右下角半径2cm"
        parsed = parse_filename(original)
        generated = generate_filename(parsed)
        # 重新解析生成的文件名
        reparsed = parse_filename(generated)
        assert reparsed.layout == parsed.layout
        assert reparsed.width_cm == parsed.width_cm
        assert reparsed.height_cm == parsed.height_cm
        assert reparsed.corners == parsed.corners

    def test_roundtrip_horizontal(self):
        original = "双面格;88x161cm四角半径5cm"
        parsed = parse_filename(original)
        generated = generate_filename(parsed)
        reparsed = parse_filename(generated)
        assert reparsed.width_cm == parsed.width_cm
        assert reparsed.height_cm == parsed.height_cm


class TestFourCornerParsing:
    """四角组合的各种表述方式"""

    @pytest.mark.parametrize("desc,expected_r", [
        ("四个角圆角半径2.5cm", 2.5),
        ("四个角圆角半径2.5厘米", 2.5),
        ("4个角圆角半径2.5cm", 2.5),
        ("四个圆角半径2.5cm", 2.5),
        ("4个圆角半径2.5cm", 2.5),
        ("四个角做2.5cm半径圆弧角", 2.5),
        ("4个角做2.5厘米半径圆弧角", 2.5),
        ("四角半径5cm", 5.0),
        ("四角圆角半径2cm", 2.0),
    ])
    def test_four_corner_variants(self, desc, expected_r):
        """四角组合的多种表述应全部解析为四角同半径"""
        corners = _parse_corners(desc)
        assert corners is not None
        for ck in ('tl', 'tr', 'bl', 'br'):
            assert corners[ck] == expected_r

    def test_four_corner_in_filename(self):
        """用户示例：四个角做1cm半径圆弧角"""
        parsed = parse_filename("双面格-定制-定制尺寸-花幔;59.5x89.5CM四个角做1cm半径圆弧角")
        assert parsed.corners is not None
        for ck in ('tl', 'tr', 'bl', 'br'):
            assert parsed.corners[ck] == 1.0


class TestPairCornerParsing:
    """两角组合的各种表述方式"""

    @pytest.mark.parametrize("desc,keys,expected_r", [
        ("左下角和右下角做3cm半径圆弧角", ('bl', 'br'), 3.0),
        ("左下角和右下角各一个圆角半径3cm", ('bl', 'br'), 3.0),
        ("左下角和右下角圆角半径3cm", ('bl', 'br'), 3.0),
        ("左下角右下角是圆角半径3cm", ('bl', 'br'), 3.0),
        ("左下角和右下角做3厘米半径圆弧角", ('bl', 'br'), 3.0),
        ("左上角和右上角圆角半径2cm", ('tl', 'tr'), 2.0),
        ("左上角和左下角圆角半径4cm", ('tl', 'bl'), 4.0),
    ])
    def test_pair_corner_variants(self, desc, keys, expected_r):
        """两角组合的多种表述"""
        corners = _parse_corners(desc)
        assert corners is not None
        for k in keys:
            assert corners[k] == expected_r
        # 其他角应为 0
        all_keys = {'tl', 'tr', 'bl', 'br'} - set(keys)
        for k in all_keys:
            assert corners[k] == 0.0


class TestSingleCornerParsing:
    """单角的各种表述方式"""

    @pytest.mark.parametrize("desc,key,expected_r", [
        ("左下角圆角半径3.1cm", 'bl', 3.1),
        ("右下角圆角半径3.1cm", 'br', 3.1),
        ("左上角圆角半径3.1cm", 'tl', 3.1),
        ("右上角圆角半径3.1cm", 'tr', 3.1),
        ("左下角一个圆角半径3.1厘米", 'bl', 3.1),
        ("左下角做3.1cm半径圆弧角", 'bl', 3.1),
        ("左下角是圆角3.1cm半径", 'bl', 3.1),
        ("左下角圆角半径3.1厘米", 'bl', 3.1),
        ("左下角半径3cm", 'bl', 3.0),
    ])
    def test_single_corner_variants(self, desc, key, expected_r):
        """单角的多种表述"""
        corners = _parse_corners(desc)
        assert corners is not None
        assert corners[key] == expected_r
        # 其他角应为 0
        for k in ('tl', 'tr', 'bl', 'br'):
            if k != key:
                assert corners[k] == 0.0


class TestFormatCornerSpec:
    """format_corner_spec 命名格式"""

    def test_four_corners_same_radius(self):
        """四角同半径 → '4个圆角半径{x}cm'"""
        corners = {'tl': 1.0, 'tr': 1.0, 'bl': 1.0, 'br': 1.0}
        assert format_corner_spec(corners) == "4个圆角半径1cm"

    def test_four_corners_same_radius_decimal(self):
        corners = {'tl': 2.5, 'tr': 2.5, 'bl': 2.5, 'br': 2.5}
        assert format_corner_spec(corners) == "4个圆角半径2.5cm"

    def test_two_corners_same_radius(self):
        """两角同半径 → '{角1}和{角2}圆角半径{x}cm'"""
        corners = {'tl': 0, 'tr': 0, 'bl': 3.0, 'br': 3.0}
        assert format_corner_spec(corners) == "左下角和右下角圆角半径3cm"

    def test_two_corners_top(self):
        corners = {'tl': 2.0, 'tr': 2.0, 'bl': 0, 'br': 0}
        assert format_corner_spec(corners) == "左上角和右上角圆角半径2cm"

    def test_single_corner(self):
        """单角 → '{角}圆角半径{x}cm'"""
        corners = {'tl': 0, 'tr': 0, 'bl': 3.1, 'br': 0}
        assert format_corner_spec(corners) == "左下角圆角半径3.1cm"

    def test_no_corners(self):
        """无圆角 → 空字符串"""
        assert format_corner_spec(None) == ""
        assert format_corner_spec({}) == ""
        assert format_corner_spec({'tl': 0, 'tr': 0, 'bl': 0, 'br': 0}) == ""

    def test_mixed_radii(self):
        """混合半径 → 逐个列出"""
        corners = {'tl': 0, 'tr': 2.0, 'bl': 0, 'br': 3.0}
        assert format_corner_spec(corners) == "右上角圆角半径2cm右下角圆角半径3cm"


class TestFilenamePreviewIntegration:
    """端到端：解析 → 命名格式 → 重新解析一致性"""

    def test_user_example_four_corners(self):
        """用户示例：四个角做1cm半径圆弧角 → 4个圆角半径1cm"""
        original = "双面格-定制-定制尺寸-花幔;59.5x89.5CM四个角做1cm半径圆弧角"
        parsed = parse_filename(original)
        # 圆角设置同步
        assert parsed.corners == {'tl': 1.0, 'tr': 1.0, 'bl': 1.0, 'br': 1.0}
        # 输出文件名预览
        generated = generate_filename(parsed)
        assert "4个圆角半径1cm" in generated
        # 重新解析应保持一致
        reparsed = parse_filename(generated)
        assert reparsed.corners == parsed.corners

    def test_user_example_pair_corners(self):
        """两角组合 → 左下角和右下角圆角半径3cm"""
        original = "双面格-定制;55x89cm左下角和右下角做3cm半径圆弧角"
        parsed = parse_filename(original)
        assert parsed.corners is not None
        assert parsed.corners['bl'] == 3.0
        assert parsed.corners['br'] == 3.0
        generated = generate_filename(parsed)
        assert "左下角和右下角圆角半径3cm" in generated
        reparsed = parse_filename(generated)
        assert reparsed.corners == parsed.corners

    def test_user_example_single_corner(self):
        """单角 → 左下角圆角半径3.1cm"""
        original = "双面格-定制;55x89cm左下角是圆角3.1cm半径"
        parsed = parse_filename(original)
        assert parsed.corners is not None
        assert parsed.corners['bl'] == 3.1
        generated = generate_filename(parsed)
        assert "左下角圆角半径3.1cm" in generated
        reparsed = parse_filename(generated)
        assert reparsed.corners == parsed.corners
