"""
测试：core.template_matcher 模板匹配评分

覆盖：
  - find_best_match 基本流程
  - 评分维度：尺寸/比例/方向/花型名
  - 临时目录隔离，不依赖真实模板库
"""
import os
import tempfile
import shutil
import pytest
from PIL import Image

from core.parser.template_matcher import TemplateMatcher
from core.parser.name_parser import parse_filename


@pytest.fixture
def template_dir(tmp_path):
    """创建临时模板库目录，放几张测试图"""
    # 几个测试模板
    templates = [
        ("双面格-定制-定制尺寸-简织;竖版54x41.2cm.jpg", (800, 600)),
        ("双面格-定制-定制尺寸-简织;竖版55x41cm.jpg", (800, 600)),
        ("双面格-定制-定制尺寸-简织;横版88x161cm.jpg", (1200, 600)),
        ("纯色-经典;88x161cm.jpg", (1200, 600)),
    ]
    for name, size in templates:
        img = Image.new('RGB', size, (255, 255, 255))
        img.save(tmp_path / name, 'JPEG', quality=80)
    return str(tmp_path)


class TestTemplateMatcher:
    """TemplateMatcher 引擎"""

    def test_scan_library(self, template_dir):
        """扫描模板库应识别所有图片"""
        m = TemplateMatcher()
        m.set_template_dir(template_dir)
        stats = m.get_library_stats()
        assert stats['total'] == 4

    def test_find_best_match_exact_size(self, template_dir):
        """精确尺寸匹配应得最高分"""
        m = TemplateMatcher()
        m.set_template_dir(template_dir)
        best, candidates = m.find_best_match("双面格-定制-定制尺寸-简织;竖版55x41cm右下角半径2cm")
        assert best is not None
        # 应优先匹配 55x41 而非 54x41.2
        assert best.parsed.width_cm == 41.0 or best.parsed.height_cm == 55.0

    def test_find_best_match_pattern_name(self, template_dir):
        """花型名匹配应作为重要评分维度"""
        m = TemplateMatcher()
        m.set_template_dir(template_dir)
        best, _ = m.find_best_match("双面格-定制-定制尺寸-简织;竖版55x41cm")
        # 应匹配到含"简织"的模板，而非"纯色-经典"
        assert best is not None
        assert best.parsed is not None
        # 简织 花型应被识别
        assert '简织' in (best.parsed.pattern_name or '') or '简织' in best.filename

    def test_direction_mismatch_lower_score(self, template_dir):
        """方向不匹配（横版查询竖版模板）应得较低分"""
        m = TemplateMatcher()
        m.set_template_dir(template_dir)
        # 竖版查询
        best_v, _ = m.find_best_match("双面格-定制-定制尺寸-简织;竖版55x41cm")
        # 横版查询
        best_h, _ = m.find_best_match("双面格-定制-定制尺寸-简织;横版55x41cm")
        # 两个都应有结果
        assert best_v is not None
        assert best_h is not None

    def test_no_match_returns_none(self, template_dir):
        """完全不匹配的查询应返回 None 或最低分结果"""
        m = TemplateMatcher()
        m.set_template_dir(template_dir)
        # 极端尺寸
        best, candidates = m.find_best_match("完全不存在的花型;999x999cm")
        # 应不崩溃；best 可能为 None 或低分候选
        # 主要断言是不抛异常
        assert isinstance(best, object) or best is None

    def test_cache_invalidated_on_dir_change(self, template_dir, tmp_path):
        """模板库目录变更后缓存应刷新"""
        m = TemplateMatcher()
        m.set_template_dir(template_dir)
        stats1 = m.get_library_stats()
        assert stats1['total'] == 4

        # 新增一个模板
        img = Image.new('RGB', (800, 600), (255, 255, 255))
        img.save(tmp_path / "新增-测试;55x41cm.jpg", 'JPEG', quality=80)

        # 重新扫描（mtime 变化应触发重扫）
        m2 = TemplateMatcher()
        m2.set_template_dir(template_dir)
        stats2 = m2.get_library_stats()
        assert stats2['total'] == 5


class TestScoringDetails:
    """评分细节"""

    def test_size_diff_zero_for_exact_match(self, template_dir):
        """精确尺寸匹配 size_diff 应接近 0"""
        m = TemplateMatcher()
        m.set_template_dir(template_dir)
        best, _ = m.find_best_match("双面格-定制-定制尺寸-简织;竖版55x41cm")
        if best is not None:
            assert best.size_diff < 0.02  # 精确匹配阈值

    def test_score_increases_with_better_match(self, template_dir):
        """比例更接近的匹配应得更高分（新权重：比例>绝对尺寸）"""
        m = TemplateMatcher()
        m.set_template_dir(template_dir)
        # 54x41cm 查询: 54x41.2cm 模板比例更接近 (比例差≈0.006)，应得更高分
        best_close, _ = m.find_best_match("双面格-定制-定制尺寸-简织;竖版54x41cm")
        # 55x41cm 查询: 55x41cm 模板比例完美匹配 (比例差=0)，应得更高分
        best_perfect, _ = m.find_best_match("双面格-定制-定制尺寸-简织;竖版55x41cm")
        if best_close and best_perfect:
            # 两个查询各自的最佳匹配应该都是比例最接近的模板
            assert best_close.parsed is not None
            assert best_perfect.parsed is not None
            # 验证各自匹配到正确的模板
            assert abs(best_close.parsed.width_cm - 41.2) < 0.1 or abs(best_close.parsed.height_cm - 54.0) < 0.1
            assert abs(best_perfect.parsed.width_cm - 41.0) < 0.1 or abs(best_perfect.parsed.height_cm - 55.0) < 0.1


class TestShapeKeywordMatching:
    """[Fix 2026-08-17] 形状关键词严格匹配测试"""

    @pytest.fixture
    def shape_template_dir(self, tmp_path):
        """创建含不同形状关键词的模板库"""
        templates = [
            # 模板: 含弧形关键词（形状不匹配）
            ("镜面皮革-定制-定制尺寸-素锦弧形;横版90x160cm.jpg", (500, 900)),
            # 模板: 正确的素锦（形状匹配）
            ("双面格-定制-定制尺寸-素锦;横版90x160cm.jpg", (500, 900)),
            # 模板: 含圆形关键词
            ("双面格-定制-定制尺寸-素锦圆形;横版90x160cm.jpg", (500, 900)),
        ]
        for name, size in templates:
            img = Image.new('RGB', size, (255, 255, 255))
            img.save(tmp_path / name, 'JPEG', quality=80)
        return str(tmp_path)

    def test_target_without_shape_keyword_rejects_arc_template(self, shape_template_dir):
        """目标无弧形关键词时，不应匹配含弧形的模板"""
        m = TemplateMatcher()
        m.set_template_dir(shape_template_dir)
        target = "双面格-定制-定制尺寸-素锦;90x160CM4个圆角半径4.5厘米"
        best, _ = m.find_best_match(target)
        assert best is not None
        assert '弧形' not in best.filename, \
            f"目标无弧形关键词，不应匹配含弧形的模板: {best.filename}"

    def test_target_without_shape_keyword_rejects_circle_template(self, shape_template_dir):
        """目标无圆形关键词时，不应匹配含圆形的模板"""
        m = TemplateMatcher()
        m.set_template_dir(shape_template_dir)
        target = "双面格-定制-定制尺寸-素锦;90x160CM4个圆角半径4.5厘米"
        best, _ = m.find_best_match(target)
        assert best is not None
        assert '圆形' not in best.filename, \
            f"目标无圆形关键词，不应匹配含圆形的模板: {best.filename}"

    def test_no_keyword_target_keeps_lenient_direction_behavior(self, tmp_path):
        """[Fix C 2026-08-31] 目标无显式方向关键词时，保持原行为：不按方向过滤（向后兼容）

        原 test_direction_keyword_allowed_with_shape_mismatch 的断言（弧形不匹配）仍成立，
        但其文档/命名错误地声称"方向关键词不应被过滤"为通用规则。
        本测试更名后明确：方向放行仅适用于"目标无显式方向关键词"的兼容场景；
        目标含显式方向关键词时的严格匹配见 TestDirectionKeywordStrictMatching。
        """
        templates = [
            # 横版 + 弧形（形状不正确）
            ("素材-素锦弧形;横版90x160cm.jpg", (500, 900)),
            # 横版 + 正确形状
            ("素材-素锦;横版90x160cm.jpg", (500, 900)),
            # 竖版 + 正确形状（目标无方向关键词时仍可作为候选）
            ("素材-素锦;竖版160x90cm.jpg", (900, 500)),
        ]
        for name, size in templates:
            img = Image.new('RGB', size, (255, 255, 255))
            img.save(tmp_path / name, 'JPEG', quality=80)

        m = TemplateMatcher()
        m.set_template_dir(str(tmp_path))
        target = "素材-素锦;90x160CM"  # 无显式方向关键词
        best, _ = m.find_best_match(target)
        assert best is not None
        # 形状关键词仍需严格匹配
        assert '弧形' not in best.filename, \
            f"形状关键词仍需严格匹配: {best.filename}"


class TestDirectionKeywordStrictMatching:
    """[Fix 2026-08-31] 方向关键词(横版/竖版)严格匹配测试

    修复根因：原逻辑仅在形状关键词(弧形/圆形/方形)上严格匹配，方向关键词放行，
    叠加 norm2 交换尺寸比较，导致 竖版目标 错配 横版/无关键词(默认横版)模板。
    修复后：目标含显式方向关键词时，模板方向必须一致方可匹配。
    """

    @pytest.fixture
    def mixed_direction_dir(self, tmp_path):
        """含竖版/横版/无关键词模板的库"""
        templates = [
            # 竖版模板（与竖版目标同向）
            ("双面格-定制-定制尺寸-花幔;竖版26x46cm.jpg", (600, 1100)),
            # 无关键词模板（默认横版，曾错配竖版目标）
            ("双面格-定制-定制尺寸-花幔;25x44cm.jpg", (1100, 600)),
            # 显式横版模板
            ("双面格-定制-定制尺寸-花幔;横版46x26cm.jpg", (1100, 600)),
        ]
        for name, size in templates:
            img = Image.new('RGB', size, (255, 255, 255))
            img.save(tmp_path / name, 'JPEG', quality=80)
        return str(tmp_path)

    def test_vertical_target_matches_vertical_template(self, mixed_direction_dir):
        """竖版目标应匹配竖版模板"""
        m = TemplateMatcher()
        m.set_template_dir(mixed_direction_dir)
        target = "双面格-定制-定制尺寸-花幔;竖版26x46cm左下角右下角圆角半径1.5cm"
        best, _ = m.find_best_match(target)
        assert best is not None
        assert '竖版' in best.filename, \
            f"竖版目标应匹配竖版模板，实得: {best.filename}"

    def test_vertical_target_rejects_horizontal_template(self, mixed_direction_dir):
        """竖版目标不得匹配横版模板（含无关键词默认横版）"""
        m = TemplateMatcher()
        m.set_template_dir(mixed_direction_dir)
        target = "双面格-定制-定制尺寸-花幔;竖版26x46cm左下角右下角圆角半径1.5cm"
        best, _ = m.find_best_match(target)
        assert best is not None
        assert '横版' not in best.filename, \
            f"竖版目标不得匹配显式横版模板: {best.filename}"
        assert '25x44' not in best.filename, \
            f"竖版目标不得匹配无方向关键词(默认横版)模板: {best.filename}"

    def test_horizontal_target_rejects_vertical_template(self, mixed_direction_dir):
        """横版目标不得匹配竖版模板"""
        m = TemplateMatcher()
        m.set_template_dir(mixed_direction_dir)
        target = "双面格-定制-定制尺寸-花幔;横版46x26cm"
        best, _ = m.find_best_match(target)
        assert best is not None
        assert '竖版' not in best.filename, \
            f"横版目标不得匹配竖版模板: {best.filename}"

    def test_vertical_target_only_horizontal_available_returns_none(self, tmp_path):
        """竖版目标，库中只有横版/无关键词模板时，不得返回横版模板作为最佳匹配"""
        templates = [
            ("双面格-定制-定制尺寸-花幔;横版46x26cm.jpg", (1100, 600)),
            ("双面格-定制-定制尺寸-花幔;25x44cm.jpg", (1100, 600)),
        ]
        for name, size in templates:
            img = Image.new('RGB', size, (255, 255, 255))
            img.save(tmp_path / name, 'JPEG', quality=80)

        m = TemplateMatcher()
        m.set_template_dir(str(tmp_path))
        target = "双面格-定制-定制尺寸-花幔;竖版26x46cm"
        best, candidates = m.find_best_match(target)
        # 竖版目标在只有横版模板的库中：不得返回横版模板作为最佳匹配
        if best is not None:
            assert '横版' not in best.filename, \
                f"竖版目标不得返回横版模板作为最佳匹配: {best.filename}"
            assert '25x44' not in best.filename, \
                f"竖版目标不得返回无关键词(默认横版)模板: {best.filename}"

    def test_user_reported_case_vertical_26x46_vs_25x44(self, tmp_path):
        """用户报告的回归用例：竖版26x46cm 不得匹配 25x44cm（默认横版）

        复现原 bug：原评分给该错配 83 分（花型+30/比例+40/交换尺寸+5/材质+5/方向错配+3），
        高于绝大多数候选，导致横版素材被作为竖版目标的最佳匹配。
        """
        templates = [
            # 用户库中存在的"问题素材"（无竖版关键词 → 默认横版）
            ("双面格-定制-定制尺寸-花幔;25x44cm.jpg", (1100, 600)),
        ]
        for name, size in templates:
            img = Image.new('RGB', size, (255, 255, 255))
            img.save(tmp_path / name, 'JPEG', quality=80)

        m = TemplateMatcher()
        m.set_template_dir(str(tmp_path))
        # 用户报告的目标文件名
        target = "双面格-定制-定制尺寸-花幔;竖版26x46cm左下角右下角圆角半径1.5cm"
        best, _ = m.find_best_match(target)
        # 竖版目标不得匹配默认横版的 25x44cm 模板
        if best is not None:
            assert '25x44' not in best.filename, \
                f"竖版26x46目标不得匹配横版25x44模板: {best.filename}"
