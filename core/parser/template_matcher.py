"""
core/parser/template_matcher.py
模板库扫描与匹配引擎。

功能：
1. 扫描模板库目录，建立缓存索引
2. 解析模板文件名中的尺寸、花型名、方向
3. 根据目标文件名匹配最佳源图

向后兼容：原 core/template_matcher.py 已改为薄重导出 shim，旧导入路径继续可用。

注意：本模块原使用 `from core.name_parser import`（绝对导入），
现已统一为相对导入 `from .name_parser import`，与项目其他模块风格一致。
"""
from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from .name_parser import (
    ParsedFilename,
    parse_filename,
    get_base_pattern_name,
    normalize_flower_name,
    parse_size_dims,
)


@dataclass
class TemplateEntry:
    """模板库条目"""
    path: str = ""
    filename: str = ""
    parsed: Optional[ParsedFilename] = None
    size_diff: float = float('inf')
    ratio_diff: float = float('inf')
    score: float = 0.0
    name_match: bool = False
    material_match: bool = False
    direction_match: bool = False
    shape_match: bool = False
    is_circular: bool = False
    is_custom: bool = False


class TemplateMatcher:
    """模板库匹配引擎"""

    IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif', '.webp', '.gif'}

    def __init__(self):
        self._template_dir: str = ""
        self._cache: dict[str, TemplateEntry] = {}
        self._dir_mtime: float = 0
        self._on_log: Optional[Callable[[str], None]] = None

    def set_log_callback(self, callback: Callable[[str], None]):
        self._on_log = callback

    def _log(self, msg: str):
        if self._on_log:
            self._on_log(msg)

    def set_template_dir(self, dir_path: str):
        """设置模板库目录"""
        self._template_dir = dir_path
        self._cache = {}
        self._dir_mtime = 0

    def _needs_refresh(self) -> bool:
        if not self._template_dir:
            return False
        try:
            current_mtime = os.path.getmtime(self._template_dir)
            return current_mtime != self._dir_mtime or not self._cache
        except Exception:
            return True

    def scan_library(self, force: bool = False) -> dict[str, TemplateEntry]:
        """
        扫描模板库目录，建立缓存索引。
        
        Returns:
            模板条目字典 {key: TemplateEntry}
        """
        if not self._template_dir:
            self._log("⚠️ 未设置模板库目录")
            return {}

        if not force and not self._needs_refresh():
            self._log(f"📂 使用缓存的模板库（{len(self._cache)} 个文件）")
            return self._cache

        self._log(f"🔍 正在扫描模板库目录: {self._template_dir}")
        cache = {}
        file_count = 0

        def scan_recursive(path: str):
            nonlocal file_count
            try:
                with os.scandir(path) as it:
                    for entry in it:
                        if entry.is_file(follow_symlinks=False):
                            file_count += 1
                            ext = os.path.splitext(entry.name)[1].lower()
                            if ext in self.IMAGE_EXTENSIONS:
                                key = os.path.splitext(entry.name)[0].lower()
                                template_entry = self._create_template_entry(entry.path, entry.name)
                                cache[key] = template_entry
                        elif entry.is_dir(follow_symlinks=False):
                            scan_recursive(entry.path)
            except (PermissionError, OSError):
                pass

        scan_recursive(self._template_dir)

        try:
            self._dir_mtime = os.path.getmtime(self._template_dir)
        except Exception:
            self._dir_mtime = 0

        self._cache = cache
        self._log(f"📂 扫描完成：共 {file_count} 个文件，其中 {len(cache)} 个有效图片")
        return cache

    def _create_template_entry(self, path: str, filename: str) -> TemplateEntry:
        """创建模板条目，解析文件名"""
        entry = TemplateEntry(path=path, filename=filename)

        try:
            parsed = parse_filename(filename)
            entry.parsed = parsed

            # 优先使用 parsed.is_circular（已经综合考虑了 corners 和直径/圆形关键词）
            entry.is_circular = parsed.is_circular

            entry.is_custom = parsed.is_custom

        except Exception:
            entry.parsed = None

        return entry

    def find_best_match(self, target_filename: str) -> tuple[Optional[TemplateEntry], list[TemplateEntry]]:
        """
        根据目标文件名在模板库中查找最佳匹配。
        
        Args:
            target_filename: 目标文件名（如 "双面格-定制-定制尺寸-简织;竖版55x41cm右下角圆角半径2厘米"）
        
        Returns:
            (最佳匹配条目, 所有候选排序列表)
        """
        if not self._template_dir:
            self._log("⚠️ 请先设置模板库目录")
            return None, []

        self.scan_library()

        target_parsed = parse_filename(target_filename)
        self._log(f"🔍 目标解析: 产品={target_parsed.product_name}, 花型={target_parsed.pattern_name}, "
                  f"尺寸={target_parsed.width_cm}x{target_parsed.height_cm}cm, 布局={target_parsed.layout}")

        candidates = []

        for key, entry in self._cache.items():
            if entry.parsed is None:
                continue

            score, details = self._compute_match_score(target_parsed, entry)
            if score > 0:
                entry.score = score
                entry.name_match = details.get('name_match', False)
                entry.material_match = details.get('material_match', False)
                entry.direction_match = details.get('direction_match', False)
                entry.shape_match = details.get('shape_match', False)
                entry.size_diff = details.get('size_diff', float('inf'))
                entry.ratio_diff = details.get('ratio_diff', float('inf'))
                candidates.append(entry)

        candidates.sort(key=lambda e: e.score, reverse=True)

        if candidates:
            best = candidates[0]
            self._log(f"✅ 最佳匹配: {best.filename} (得分: {best.score:.2f})")
            if len(candidates) > 1:
                self._log(f"   其他候选: {len(candidates) - 1} 个")
                for c in candidates[1:4]:
                    self._log(f"   - {c.filename} (得分: {c.score:.2f})")
            return best, candidates
        else:
            self._log("❌ 未找到匹配的模板")
            return None, []

    def _compute_match_score(self, target: ParsedFilename, entry: TemplateEntry) -> tuple[float, dict]:
        """
        计算目标与模板的匹配得分。
        
        评分维度：
        - 花型名匹配 (0-40分)
        - 材质匹配 (0-10分)
        - 尺寸比例接近度 (0-25分)
        - 绝对尺寸差 (0-15分)
        - 方向匹配 (0-10分)
        """
        details = {
            'name_match': False,
            'material_match': False,
            'direction_match': False,
            'shape_match': False,
            'size_diff': float('inf'),
            'ratio_diff': float('inf'),
        }

        parsed = entry.parsed
        if parsed is None:
            return 0.0, details

        score = 0.0

        # ===== 硬约束：形状排斥（圆形 vs 圆角矩形/方形互斥匹配）=====
        # 目标有 corners → 一定是圆角矩形，不能匹配圆形模板
        target_has_corners = bool(target.corners) and any(v > 0 for v in target.corners.values())
        target_is_circle = target.is_circular and not target_has_corners  # 有圆角时按矩形算
        template_is_circle = entry.is_circular

        if target_has_corners and template_is_circle:
            # 目标是圆角矩形，模板是圆形 → 直接排除
            self._log(f"   ❌ 形状排斥：目标是圆角矩形，排除圆形模板 {os.path.basename(entry.path)}")
            return 0.0, details
        if target_is_circle and not template_is_circle:
            # 目标是圆形，模板不是圆形 → 直接排除
            self._log(f"   ❌ 形状排斥：目标是圆形，排除非圆形模板 {os.path.basename(entry.path)}")
            return 0.0, details

        # ===== 硬约束：花型名排斥 =====
        # 仅当目标为结构化命名（含'-'分隔，即material非空）时，花型名才作为硬约束
        # 目标有明确花型名 → 只匹配相同花型名的模板（子串关系视为匹配）
        target_pattern = target.pattern_name.lower() if target.pattern_name else ""
        template_pattern = parsed.pattern_name.lower() if parsed.pattern_name else ""

        # 只有结构化命名（含material）才强制花型名匹配
        if target.material and target_pattern:
            if not template_pattern:
                # 目标有花型名，模板无花型名 → 排除（无法验证花型匹配）
                self._log(f"   ❌ 花型名排斥：目标有[{target_pattern}]，模板无花型名")
                return 0.0, details
            if target_pattern != template_pattern and not (
                target_pattern in template_pattern or template_pattern in target_pattern
            ):
                # 花型名完全不同（无子串关系）→ 直接排除
                self._log(f"   ❌ 花型名排斥：目标[{target_pattern}] 与 模板[{template_pattern}] 完全不同")
                return 0.0, details

        # 1. 花型名匹配 (30分)
        if target_pattern and template_pattern:
            if target_pattern == template_pattern:
                score += 30
                details['name_match'] = True
            elif target_pattern in template_pattern or template_pattern in target_pattern:
                score += 20
                details['name_match'] = True
                self._log(f"   部分花型匹配: {target_pattern} vs {template_pattern}")

        # 2. 形状关键词匹配 (5分)
        target_kw = set(target.shape_keywords or [])
        template_kw = set(parsed.shape_keywords or [])
        if target_kw and template_kw:
            if target_kw.issubset(template_kw) or template_kw.issubset(target_kw):
                score += 5
                details['shape_match'] = True
            elif target_kw & template_kw:
                score += 3
                details['shape_match'] = True

        # 3. 尺寸比例接近度 (40分) — 核心权重
        if (target.width_cm > 0 and target.height_cm > 0 and
                parsed.width_cm > 0 and parsed.height_cm > 0):

            target_ratio = max(target.width_cm, target.height_cm) / min(target.width_cm, target.height_cm)
            template_ratio = max(parsed.width_cm, parsed.height_cm) / min(parsed.width_cm, parsed.height_cm)

            ratio_diff = abs(target_ratio - template_ratio)
            details['ratio_diff'] = ratio_diff

            if ratio_diff < 0.02:
                score += 40
            elif ratio_diff < 0.05:
                score += 38
            elif ratio_diff < 0.1:
                score += 32
            elif ratio_diff < 0.2:
                score += 24
            elif ratio_diff < 0.3:
                score += 16
            elif ratio_diff < 0.5:
                score += 8

        # 4. 绝对尺寸差 (10分)
        if (target.width_cm > 0 and target.height_cm > 0 and
                parsed.width_cm > 0 and parsed.height_cm > 0):

            tw, th = target.width_cm, target.height_cm
            pw, ph = parsed.width_cm, parsed.height_cm

            norm1 = abs(pw - tw) / max(tw, 1) + abs(ph - th) / max(th, 1)
            norm2 = abs(ph - tw) / max(tw, 1) + abs(pw - th) / max(th, 1)
            size_diff = min(norm1, norm2)
            details['size_diff'] = size_diff

            if size_diff < 0.02:
                score += 10
            elif size_diff < 0.05:
                score += 8
            elif size_diff < 0.1:
                score += 5
            elif size_diff < 0.2:
                score += 2

        # 5. 材质匹配 (5分)
        if target.material and parsed.material:
            if target.material.lower() == parsed.material.lower():
                score += 5
                details['material_match'] = True

        # 6. 方向匹配 (10分)
        if target.layout and parsed.layout:
            target_is_vert = target.layout == '竖版'
            template_is_vert = parsed.layout == '竖版'

            if target_is_vert == template_is_vert:
                score += 10
                details['direction_match'] = True
            else:
                # 方向不同但比例相同的话仍有一定匹配度
                if details.get('ratio_diff', float('inf')) < 0.05:
                    score += 3
                    details['direction_match'] = False

        return score, details

    def get_library_stats(self) -> dict:
        """获取模板库统计信息。

        若尚未建立缓存（仅 set_template_dir 后未调用 scan/find），则自动触发一次扫描，
        避免调用方必须显式 scan_library() 才能得到正确 total。
        """
        if self._template_dir and (not self._cache or self._needs_refresh()):
            self.scan_library()
        return {
            'total': len(self._cache),
            'has_pattern': sum(1 for e in self._cache.values()
                              if e.parsed and e.parsed.pattern_name),
            'has_size': sum(1 for e in self._cache.values()
                           if e.parsed and e.parsed.width_cm > 0),
            'has_material': sum(1 for e in self._cache.values()
                               if e.parsed and e.parsed.material),
            'has_custom': sum(1 for e in self._cache.values()
                             if e.is_custom),
        }

    def clear_cache(self):
        """清空缓存"""
        self._cache = {}
        self._dir_mtime = 0

    def get_template_dir(self) -> str:
        return self._template_dir


def scan_template_directory(dir_path: str, on_log: Optional[Callable[[str], None]] = None) -> dict[str, TemplateEntry]:
    """便捷函数：扫描模板目录"""
    matcher = TemplateMatcher()
    matcher.set_log_callback(on_log or (lambda _: None))
    matcher.set_template_dir(dir_path)
    return matcher.scan_library()


def match_template(target_filename: str, template_dir: str,
                   on_log: Optional[Callable[[str], None]] = None) -> tuple[Optional[TemplateEntry], list[TemplateEntry]]:
    """便捷函数：一步完成匹配"""
    matcher = TemplateMatcher()
    matcher.set_log_callback(on_log or (lambda _: None))
    matcher.set_template_dir(template_dir)
    matcher.scan_library()
    return matcher.find_best_match(target_filename)
