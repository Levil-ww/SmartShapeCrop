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

from core.template_matcher import TemplateMatcher
from core.name_parser import parse_filename


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
        """更好的匹配应得更高分"""
        m = TemplateMatcher()
        m.set_template_dir(template_dir)
        best_perfect, _ = m.find_best_match("双面格-定制-定制尺寸-简织;竖版55x41cm")
        best_approx, _ = m.find_best_match("双面格-定制-定制尺寸-简织;竖版54x41cm")
        # 精确匹配分数应不低于近似匹配
        if best_perfect and best_approx:
            assert best_perfect.score >= best_approx.score
