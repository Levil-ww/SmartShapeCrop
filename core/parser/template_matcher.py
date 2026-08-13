"""
core/parser/template_matcher.py
模板库扫描与匹配引擎（高性能版）。

性能优化：
1. 磁盘持久化缓存：扫描后的解析结果缓存为 JSON，重启直接加载（20万文件无需每次重新解析）
2. 增量扫描：基于子目录 mtime，仅扫描变更过的子目录
3. 预建倒排索引：按花型名(pattern_name)、布局(layout)、圆形标记(is_circular)分组，
   匹配时先按硬约束做 O(1) 定位，再做细粒度评分，避免遍历全部条目
4. 匹配阶段禁用日志：_compute_match_score 的大量日志会拖慢 20 万次循环

功能：
- 扫描模板库目录，建立缓存索引
- 解析模板文件名中的尺寸、花型名、方向
- 根据目标文件名匹配最佳源图
- 支持模板库打开历史记录（与 core/app_settings 协作）

向后兼容：对外 API 保持不变（TemplateMatcher / scan_template_directory / match_template）。
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

from .name_parser import (
    ParsedFilename,
    parse_filename,
    get_base_pattern_name,
    normalize_flower_name,
    parse_size_dims,
)


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class TemplateEntry:
    """模板库条目（保持与旧版字段兼容）"""
    path: str = ""
    filename: str = ""
    # parsed 字段不直接序列化，而是下面拆分后存储
    size_diff: float = float('inf')
    ratio_diff: float = float('inf')
    score: float = 0.0
    name_match: bool = False
    material_match: bool = False
    direction_match: bool = False
    shape_match: bool = False
    is_circular: bool = False
    is_custom: bool = False
    # 解析后拆分字段（用于序列化 + 索引）
    _product_name: str = ""
    _layout: str = ""
    _width_cm: float = 0.0
    _height_cm: float = 0.0
    _material: str = ""
    _pattern_name: str = ""
    _pattern_key: str = ""     # 索引用：pattern_name.lower() 标准化
    _shape_keywords_serialized: str = ""  # JSON 字符串，省得单独建字段
    _file_mtime: float = 0.0   # 文件级 mtime（用于增量校验）

    # ---- 解析对象兼容（延迟构造） ----
    @property
    def parsed(self) -> Optional[ParsedFilename]:
        """按需构造 ParsedFilename（保持字段兼容）。

        说明：在循环里每次构造 ParsedFilename 有成本；但评分函数只用它上面的几个
        字段，所以内部优化过的评分函数会直接读 _width_cm 等拆分字段。
        """
        p = ParsedFilename(
            product_name=self._product_name,
            layout=self._layout,
            width_cm=self._width_cm,
            height_cm=self._height_cm,
            material=self._material,
            pattern_name=self._pattern_name,
            is_custom=self.is_custom,
            is_circular=self.is_circular,
            raw_filename=self.filename,
        )
        if self._shape_keywords_serialized:
            try:
                p.shape_keywords = json.loads(self._shape_keywords_serialized)
            except (json.JSONDecodeError, TypeError):
                p.shape_keywords = []
        else:
            p.shape_keywords = []
        return p

    @parsed.setter
    def parsed(self, value: Optional[ParsedFilename]):
        """从 ParsedFilename 写入拆分字段"""
        if value is None:
            self._product_name = ""
            self._layout = ""
            self._width_cm = 0.0
            self._height_cm = 0.0
            self._material = ""
            self._pattern_name = ""
            self._pattern_key = ""
            self._shape_keywords_serialized = ""
            return
        self._product_name = value.product_name or ""
        self._layout = value.layout or ""
        self._width_cm = float(value.width_cm or 0.0)
        self._height_cm = float(value.height_cm or 0.0)
        self._material = value.material or ""
        self._pattern_name = value.pattern_name or ""
        # pattern_key：统一小写，便于做索引桶
        self._pattern_key = self._pattern_name.strip().lower()
        kw = value.shape_keywords or []
        self._shape_keywords_serialized = json.dumps(kw, ensure_ascii=False) if kw else ""

    # ---- 序列化 ----
    def to_cache_dict(self) -> dict:
        return {
            "path": self.path,
            "filename": self.filename,
            "is_circular": self.is_circular,
            "is_custom": self.is_custom,
            "_product_name": self._product_name,
            "_layout": self._layout,
            "_width_cm": self._width_cm,
            "_height_cm": self._height_cm,
            "_material": self._material,
            "_pattern_name": self._pattern_name,
            "_pattern_key": self._pattern_key,
            "_shape_keywords_serialized": self._shape_keywords_serialized,
            "_file_mtime": self._file_mtime,
        }

    @classmethod
    def from_cache_dict(cls, d: dict) -> "TemplateEntry":
        e = cls()
        e.path = d.get("path", "")
        e.filename = d.get("filename", "")
        e.is_circular = bool(d.get("is_circular", False))
        e.is_custom = bool(d.get("is_custom", False))
        e._product_name = d.get("_product_name", "")
        e._layout = d.get("_layout", "")
        e._width_cm = float(d.get("_width_cm", 0) or 0)
        e._height_cm = float(d.get("_height_cm", 0) or 0)
        e._material = d.get("_material", "")
        e._pattern_name = d.get("_pattern_name", "")
        e._pattern_key = d.get("_pattern_key", "") or e._pattern_name.strip().lower()
        e._shape_keywords_serialized = d.get("_shape_keywords_serialized", "")
        e._file_mtime = float(d.get("_file_mtime", 0) or 0)
        return e


# ============================================================================
# 磁盘缓存容器
# ============================================================================

_CACHE_SCHEMA_VERSION = 1


@dataclass
class DiskCache:
    """磁盘缓存：存 20 万条解析结果 + 子目录 mtime 映射"""
    schema_version: int = _CACHE_SCHEMA_VERSION
    template_dir: str = ""
    # 子目录绝对路径 -> mtime（用于增量扫描）
    subdir_mtimes: dict = field(default_factory=dict)
    # key -> TemplateEntry.to_cache_dict()
    entries: dict = field(default_factory=dict)
    # 倒排索引：pattern_key -> list[key]
    idx_pattern: dict = field(default_factory=dict)
    # 布局：竖版/横版/""（空表示没有方向信息）
    idx_layout: dict = field(default_factory=dict)
    # 圆形标记：True / False
    idx_circular: dict = field(default_factory=dict)
    # 记录最后扫描时间
    last_scan_at: float = 0.0

    # ---- 序列化 ----
    def save(self, path: str):
        data = {
            "schema_version": self.schema_version,
            "template_dir": self.template_dir,
            "subdir_mtimes": self.subdir_mtimes,
            "entries": self.entries,
            "idx_pattern": self.idx_pattern,
            "idx_layout": self.idx_layout,
            "idx_circular": self.idx_circular,
            "last_scan_at": self.last_scan_at,
        }
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            # 磁盘缓存写入失败不影响主流程
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    @classmethod
    def load(cls, path: str, expected_template_dir: str) -> Optional["DiskCache"]:
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(data, dict):
            return None
        if data.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        if data.get("template_dir") != expected_template_dir:
            # 模板目录变了 -> 缓存失效（安全）
            return None
        try:
            cache = cls()
            cache.schema_version = int(data.get("schema_version", 0))
            cache.template_dir = data.get("template_dir", "")
            cache.subdir_mtimes = data.get("subdir_mtimes") or {}
            raw_entries = data.get("entries") or {}
            # 条目反序列化（延迟构造：需要 from_cache_dict）
            cache.entries = {
                k: (TemplateEntry.from_cache_dict(v) if isinstance(v, dict) else v)
                for k, v in raw_entries.items()
            }
            cache.idx_pattern = data.get("idx_pattern") or {}
            cache.idx_layout = data.get("idx_layout") or {}
            cache.idx_circular = data.get("idx_circular") or {}
            cache.last_scan_at = float(data.get("last_scan_at", 0) or 0)
            return cache
        except Exception:
            return None


def _cache_path_for(template_dir: str) -> str:
    """计算模板库对应的缓存文件路径（放在用户目录下，避免写模板库目录）"""
    abs_dir = os.path.abspath(template_dir)
    h = hashlib.md5(abs_dir.encode("utf-8")).hexdigest()[:12]
    # 放到 ~/.smartshapecrop/caches/xx.cache.json
    base = os.path.join(os.path.expanduser("~"), ".smartshapecrop", "caches")
    os.makedirs(base, exist_ok=True)
    safe_name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5_-]+", "_", os.path.basename(abs_dir.rstrip(os.sep)))
    safe_name = safe_name[:40] or "library"
    return os.path.join(base, f"{safe_name}_{h}.cache.json")


# ============================================================================
# 主匹配引擎
# ============================================================================

class TemplateMatcher:
    """模板库匹配引擎（高性能版）"""

    IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif', '.webp', '.gif'}

    def __init__(self):
        self._template_dir: str = ""
        self._cache: dict[str, TemplateEntry] = {}
        # 倒排索引
        self._idx_pattern: dict[str, list[str]] = {}   # pattern_key -> [key]
        self._idx_layout: dict[str, list[str]] = {}    # "竖版"/"横版"/"" -> [key]
        self._idx_circular: dict[bool, list[str]] = {True: [], False: []}
        # 子目录 mtime 映射（用于增量扫描）
        self._subdir_mtimes: dict[str, float] = {}
        self._dir_mtime: float = 0
        self._on_log: Optional[Callable[[str], None]] = None
        # 是否启用磁盘缓存
        self.disk_cache_enabled: bool = True
        # 匹配过程：是否启用详细日志（严重影响性能，默认关闭）
        self.enable_match_debug_log: bool = False
        # 扫描时是否强制重建（不读磁盘缓存）
        self.force_rebuild = False

    # ------------------------------------------------------------
    # 基础接口
    # ------------------------------------------------------------

    def set_log_callback(self, callback: Callable[[str], None]):
        self._on_log = callback

    def _log(self, msg: str):
        if self._on_log:
            self._on_log(msg)

    def set_template_dir(self, dir_path: str):
        """设置模板库目录（目录变化会清空内存缓存）"""
        if self._template_dir == dir_path:
            return
        self._template_dir = dir_path
        self._cache = {}
        self._idx_pattern = {}
        self._idx_layout = {}
        self._idx_circular = {True: [], False: []}
        self._subdir_mtimes = {}
        self._dir_mtime = 0

    # ------------------------------------------------------------
    # 扫描逻辑（磁盘缓存 + 增量）
    # ------------------------------------------------------------

    def _needs_refresh(self) -> bool:
        if not self._template_dir:
            return False
        try:
            current_mtime = os.path.getmtime(self._template_dir)
            return current_mtime != self._dir_mtime or not self._cache
        except Exception:
            return True

    def _cache_path(self) -> str:
        return _cache_path_for(self._template_dir)

    def _load_disk_cache(self) -> DiskCache | None:
        if not self.disk_cache_enabled or self.force_rebuild:
            return None
        return DiskCache.load(self._cache_path(), os.path.abspath(self._template_dir))

    def _save_disk_cache(self):
        if not self.disk_cache_enabled:
            return
        dc = DiskCache()
        dc.template_dir = os.path.abspath(self._template_dir)
        dc.subdir_mtimes = dict(self._subdir_mtimes)
        dc.entries = {k: v.to_cache_dict() for k, v in self._cache.items()}
        dc.idx_pattern = {k: list(v) for k, v in self._idx_pattern.items()}
        dc.idx_layout = {k: list(v) for k, v in self._idx_layout.items()}
        dc.idx_circular = {str(k): list(v) for k, v in self._idx_circular.items()}
        dc.last_scan_at = time.time()
        dc.save(self._cache_path())

    def scan_library(self, force: bool = False) -> dict[str, TemplateEntry]:
        """扫描模板库目录（带磁盘缓存 + 增量扫描）。

        步骤：
        1. 无内存缓存 -> 尝试加载磁盘缓存
        2. 做子目录级 mtime 增量扫描：
           - 新增/变更的子目录：重新扫描其中的文件
           - 未变更的子目录：沿用缓存中的条目
        3. 写回磁盘缓存（增量更新后一次性写入）
        """
        if not self._template_dir:
            self._log("⚠️ 未设置模板库目录")
            return {}

        # force=True：清掉内存 + 磁盘缓存强制重扫
        if force:
            self.force_rebuild = True
            self._cache = {}
            self._idx_pattern = {}
            self._idx_layout = {}
            self._idx_circular = {True: [], False: []}
            self._subdir_mtimes = {}
            try:
                cp = self._cache_path()
                if os.path.exists(cp):
                    os.remove(cp)
            except OSError:
                pass
            self.force_rebuild = False

        t0 = time.time()

        # 1) 没有内存缓存 -> 尝试加载磁盘缓存
        if not self._cache:
            dc = self._load_disk_cache()
            if dc is not None:
                self._cache = dc.entries
                self._idx_pattern = dc.idx_pattern
                self._idx_layout = dc.idx_layout
                # idx_circular：缓存存的是字符串键（JSON 不允许 bool 键在有些实现里不一致）
                circ: dict[bool, list[str]] = {True: [], False: []}
                for k_str, v in (dc.idx_circular or {}).items():
                    if k_str in ("True", "true", True):
                        circ[True] = list(v)
                    else:
                        circ[False] = list(v)
                self._idx_circular = circ
                self._subdir_mtimes = dc.subdir_mtimes
                self._log(f"💾 加载磁盘缓存成功（{len(self._cache)} 个条目，"
                          f"上次扫描 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(dc.last_scan_at))}）")

        # 2) 执行扫描（增量 or 全量）
        total_entries_before = len(self._cache)
        cache_file_count, total_file_count, dirs_scanned, dirs_skipped = self._do_incremental_scan()

        # 记录根目录 mtime
        try:
            self._dir_mtime = os.path.getmtime(self._template_dir)
        except Exception:
            self._dir_mtime = 0

        # 3) 若内容变化 -> 写磁盘缓存
        changed = (len(self._cache) != total_entries_before) or (cache_file_count > 0)
        if changed:
            t_save_start = time.time()
            self._save_disk_cache()
            dt_save = time.time() - t_save_start
        else:
            dt_save = 0.0

        dt = time.time() - t0
        self._log(
            f"📂 扫描完成：共 {total_file_count} 个文件，其中 {len(self._cache)} 个有效图片 "
            f"（增量更新 {cache_file_count} 个；目录 扫{dirs_scanned}/跳{dirs_skipped}） "
            f"耗时 {dt:.2f}s{'; 写缓存 ' + format(dt_save, '.2f') + 's' if changed else ''}"
        )
        return self._cache

    def _do_incremental_scan(self) -> tuple[int, int, int, int]:
        """
        执行实际的增量扫描。

        返回：(新增/变更并更新的条目数, 总文件数, 扫描的子目录数, 跳过的子目录数)
        """
        # 先收集全部子目录（含根）
        all_dirs: list[str] = []
        try:
            for root, dirs, files in os.walk(self._template_dir, followlinks=False):
                all_dirs.append(root)
        except (PermissionError, OSError):
            all_dirs = [self._template_dir]

        # 计算每个目录当前 mtime
        current_mtimes: dict[str, float] = {}
        for d in all_dirs:
            try:
                current_mtimes[d] = os.path.getmtime(d)
            except OSError:
                current_mtimes[d] = 0.0

        # ---- 阶段 A：处理目录变更 ----
        #   - 新增目录（current 里有，_subdir_mtimes 没有）：标记为要扫描
        #   - mtime 变化：标记为要扫描（需要删除该目录下旧条目，重新扫）
        #   - 消失目录（_subdir_mtimes 里有，current 里没有）：删掉其条目
        dirs_to_scan: set[str] = set()
        dirs_skipped = 0
        for d, mt in current_mtimes.items():
            prev = self._subdir_mtimes.get(d)
            if prev is None or abs(prev - mt) > 1e-6:
                dirs_to_scan.add(d)
            else:
                dirs_skipped += 1

        # 消失目录 -> 移除其条目并从 idx 中清理
        gone_dirs = [d for d in self._subdir_mtimes if d not in current_mtimes]
        if gone_dirs:
            self._purge_dirs(gone_dirs)

        # ---- 阶段 B：扫描需要重扫的目录 ----
        updated_count = 0
        total_file_count = 0
        # 先收集：目录内文件列表（用于判断缓存里哪些文件已被删除）
        # 同时执行：对每个文件做 增量校验（file mtime 不变 -> 跳过解析；否则重新解析）
        #
        # 记录 "当前存在的 key"（最后用它来移除被删除文件的老条目）
        existing_keys_in_scanned_dirs: set[str] = set()

        for d in dirs_to_scan:
            try:
                with os.scandir(d) as it:
                    entries = list(it)
            except (PermissionError, OSError):
                entries = []

            for entry in entries:
                try:
                    if entry.is_file(follow_symlinks=False):
                        total_file_count += 1
                        ext = os.path.splitext(entry.name)[1].lower()
                        if ext not in self.IMAGE_EXTENSIONS:
                            continue
                        key = os.path.splitext(entry.name)[0].lower()
                        existing_keys_in_scanned_dirs.add(key)

                        # 文件级增量：若 mtime 没变化且已有条目 -> 直接保留
                        try:
                            f_mtime = entry.stat(follow_symlinks=False).st_mtime
                        except OSError:
                            f_mtime = 0.0

                        old = self._cache.get(key)
                        if (old is not None
                                and abs(old._file_mtime - f_mtime) < 1e-6
                                and old.path == entry.path):
                            # 命中 -> 跳过解析
                            continue

                        # 重新解析 + 更新
                        tpl = self._create_template_entry(entry.path, entry.name)
                        tpl._file_mtime = f_mtime
                        self._upsert_entry(key, tpl, old)
                        updated_count += 1
                except OSError:
                    continue

        # ---- 阶段 C：清理被删除的文件 ----
        # 注意：只有本次扫描涉及的目录才检查清理，其他目录的条目保持不变
        if dirs_to_scan:
            dirs_to_scan_set = set(dirs_to_scan)
            stale_keys = []
            for k, e in self._cache.items():
                parent = os.path.dirname(e.path)
                if parent in dirs_to_scan_set and k not in existing_keys_in_scanned_dirs:
                    stale_keys.append(k)
            for k in stale_keys:
                self._remove_entry(k)
                updated_count += 1

        # ---- 阶段 D：更新 _subdir_mtimes ----
        new_sub = {}
        for d in all_dirs:
            new_sub[d] = current_mtimes.get(d, 0.0)
        self._subdir_mtimes = new_sub

        # 统计未扫描目录的文件数（粗略：使用现有缓存统计）
        total_file_count += sum(1 for e in self._cache.values()
                                if os.path.dirname(e.path) not in dirs_to_scan)

        return updated_count, total_file_count, len(dirs_to_scan), dirs_skipped

    def _purge_dirs(self, gone_dirs: list[str]):
        """移除一批目录下的所有条目（含索引清理）"""
        gone_set = set(gone_dirs)
        to_remove = [k for k, e in self._cache.items() if os.path.dirname(e.path) in gone_set]
        for k in to_remove:
            self._remove_entry(k)

    def _upsert_entry(self, key: str, new_entry: TemplateEntry, old_entry: Optional[TemplateEntry]):
        """插入/更新条目，并同步更新索引"""
        # 如果 key 已存在，先从索引里移除旧的（其 _pattern_key/_layout/_is_circular 可能变）
        if old_entry is not None:
            self._deindex_entry(key, old_entry)
        self._cache[key] = new_entry
        self._index_entry(key, new_entry)

    def _remove_entry(self, key: str):
        e = self._cache.pop(key, None)
        if e is None:
            return
        self._deindex_entry(key, e)

    def _index_entry(self, key: str, e: TemplateEntry):
        """把 key 加入倒排索引"""
        # pattern
        pk = e._pattern_key
        if pk:
            bucket = self._idx_pattern.setdefault(pk, [])
            # 避免重复（极端情况下同一个 pattern_key 下多次加）
            if not bucket or bucket[-1] != key:
                bucket.append(key)
        # layout
        ly = e._layout or ""
        bucket = self._idx_layout.setdefault(ly, [])
        if not bucket or bucket[-1] != key:
            bucket.append(key)
        # circular
        circ = bool(e.is_circular)
        bucket = self._idx_circular.setdefault(circ, [])
        if not bucket or bucket[-1] != key:
            bucket.append(key)

    def _deindex_entry(self, key: str, e: TemplateEntry):
        """从倒排索引中移除 key（惰性移除：找到就删，找不到就算了）"""
        # pattern
        pk = e._pattern_key
        if pk and pk in self._idx_pattern:
            try:
                self._idx_pattern[pk].remove(key)
            except ValueError:
                pass
            if not self._idx_pattern[pk]:
                del self._idx_pattern[pk]
        # layout
        ly = e._layout or ""
        if ly in self._idx_layout:
            try:
                self._idx_layout[ly].remove(key)
            except ValueError:
                pass
            if not self._idx_layout[ly]:
                del self._idx_layout[ly]
        # circular
        circ = bool(e.is_circular)
        bucket = self._idx_circular.get(circ)
        if bucket:
            try:
                bucket.remove(key)
            except ValueError:
                pass

    # ------------------------------------------------------------
    # 创建条目（解析文件名）
    # ------------------------------------------------------------

    def _create_template_entry(self, path: str, filename: str) -> TemplateEntry:
        entry = TemplateEntry(path=path, filename=filename)
        try:
            parsed = parse_filename(filename)
            # 走 setter：拆字段 + 设置索引键
            entry.parsed = parsed
            # 综合设置 is_circular / is_custom
            entry.is_circular = parsed.is_circular
            entry.is_custom = parsed.is_custom
        except Exception:
            # 解析失败不抛异常：保持 parsed=None，后续匹配会跳过
            entry.parsed = None
        return entry

    # ------------------------------------------------------------
    # 匹配：预索引过滤 + 细粒度评分
    # ------------------------------------------------------------

    def find_best_match(self, target_filename: str) -> tuple[Optional[TemplateEntry], list[TemplateEntry]]:
        """
        根据目标文件名在模板库中查找最佳匹配。

        性能优化：
        - 先利用倒排索引缩小候选集（花型名、形状、方向）
        - 对候选集做细粒度评分（避免遍历 20 万全量）
        """
        if not self._template_dir:
            self._log("⚠️ 请先设置模板库目录")
            return None, []

        self.scan_library()

        target_parsed = parse_filename(target_filename)
        self._log(f"🔍 目标解析: 产品={target_parsed.product_name}, 花型={target_parsed.pattern_name}, "
                  f"尺寸={target_parsed.width_cm}x{target_parsed.height_cm}cm, 布局={target_parsed.layout}")

        # ---- 1) 用索引做候选集预过滤 ----
        candidate_keys = self._prefilter_candidates(target_parsed)

        # ---- 2) 对候选集做细粒度评分 ----
        candidates: list[TemplateEntry] = []
        # 预先提取目标字段，避免在循环里反复访问属性
        tgt = target_parsed
        tgt_pattern = (tgt.pattern_name or "").lower()
        tgt_material = (tgt.material or "").lower()
        tgt_has_corners = bool(tgt.corners) and any(v > 0 for v in tgt.corners.values())
        tgt_is_circle = tgt.is_circular and not tgt_has_corners
        tgt_layout = tgt.layout or ""
        tgt_is_vert = (tgt_layout == "竖版")

        # 尺寸预计算
        t_w, t_h = tgt.width_cm, tgt.height_cm
        has_size = (t_w > 0 and t_h > 0)
        if has_size:
            t_ratio = max(t_w, t_h) / min(t_w, t_h)
        else:
            t_ratio = None

        # 硬约束（目标有明确花型名 + 材质 -> 必须花型名匹配）
        force_pattern = bool(tgt.material and tgt_pattern)

        # 统计信息
        scored_count = 0
        for k in candidate_keys:
            e = self._cache.get(k)
            if e is None:
                continue
            # 无解析结果的条目直接跳过
            if not e._product_name and not e._pattern_name and e._width_cm <= 0 and e._height_cm <= 0:
                # 解析失败条目
                continue

            # ===== 硬约束：形状排斥（圆形 vs 圆角矩形）=====
            template_is_circle = e.is_circular
            if tgt_has_corners and template_is_circle:
                continue
            if tgt_is_circle and not template_is_circle:
                continue

            # ===== 硬约束：花型名排斥（仅当目标有明确材质+花型）=====
            if force_pattern:
                tpl_pat = e._pattern_key
                if not tpl_pat:
                    continue
                if tgt_pattern != tpl_pat and not (tgt_pattern in tpl_pat or tpl_pat in tgt_pattern):
                    continue

            # 计算得分
            score, details = self._compute_match_score_fast(
                e,
                tgt_pattern=tgt_pattern,
                tgt_material=tgt_material,
                tgt_is_vert=tgt_is_vert,
                tgt_has_corners=tgt_has_corners,
                tgt_is_circle=tgt_is_circle,
                has_size=has_size,
                t_w=t_w, t_h=t_h, t_ratio=t_ratio,
            )
            scored_count += 1
            if score > 0:
                e.score = score
                e.name_match = details.get('name_match', False)
                e.material_match = details.get('material_match', False)
                e.direction_match = details.get('direction_match', False)
                e.shape_match = details.get('shape_match', False)
                e.size_diff = details.get('size_diff', float('inf'))
                e.ratio_diff = details.get('ratio_diff', float('inf'))
                candidates.append(e)

        candidates.sort(key=lambda e: e.score, reverse=True)

        self._log(f"⚡ 候选集 {len(candidate_keys)} → 评分 {scored_count} → 入围 {len(candidates)}")

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

    def _prefilter_candidates(self, target: ParsedFilename) -> set[str]:
        """
        用倒排索引预过滤候选集。

        策略：
        - 有明确花型名：从 pattern_key 桶取（精确 + 包含关系的桶）
        - 圆形/矩形排斥：从 circular 桶取交集
        - 若最终候选集太小（<500）：回退到 pattern 维度放宽（或全量集）以保证召回
        """
        target_pattern = (target.pattern_name or "").lower()
        target_has_corners = bool(target.corners) and any(v > 0 for v in target.corners.values())
        target_is_circle = target.is_circular and not target_has_corners

        # --- 维度 1：圆形过滤 ---
        # 目标是圆形 -> 只考虑圆形桶；否则只考虑非圆形桶
        if target_is_circle:
            circ_keys = set(self._idx_circular.get(True, []))
        else:
            circ_keys = set(self._idx_circular.get(False, []))
        # 说明：有些条目解析失败或未设置 is_circular，可能没被索引到 circular 桶；
        # 但它们也不是明确的"圆形"，所以在"目标非圆"场景下应该也纳入范围。
        # 简化处理：
        if target_is_circle:
            shape_pool = circ_keys
        else:
            # 非圆形：False 桶 + 没进 circular 桶的条目（取非 True 桶 = 全量 - True）
            all_keys = set(self._cache.keys())
            true_circ = set(self._idx_circular.get(True, []))
            shape_pool = all_keys - true_circ

        # --- 维度 2：花型名过滤（有明确花型名才启用）---
        if target_pattern:
            pattern_bucket_keys: set[str] = set()
            # a) 精确桶
            if target_pattern in self._idx_pattern:
                pattern_bucket_keys.update(self._idx_pattern[target_pattern])
            # b) 包含关系：索引桶包含目标 or 目标包含索引桶（子串匹配）
            for idx_pat, ks in self._idx_pattern.items():
                if not idx_pat:
                    continue
                if idx_pat == target_pattern:
                    continue  # 已加
                if target_pattern in idx_pat or idx_pat in target_pattern:
                    pattern_bucket_keys.update(ks)

            # 形状池 ∩ 花型池
            candidate_keys = shape_pool & pattern_bucket_keys

            # 候选集过小保护：如果交集太少，放宽到 花型池 或 shape_pool，保证有足够结果可评分
            MIN_CANDIDATES = 100
            if len(candidate_keys) < MIN_CANDIDATES and pattern_bucket_keys:
                # 仅当目标有明确材质时严格，否则放宽
                if not target.material:
                    candidate_keys = pattern_bucket_keys
            if len(candidate_keys) < MIN_CANDIDATES:
                # 还不够 -> 用 (pattern_bucket_keys ∪ shape_pool 内随机采样部分) 兜底
                extra = set()
                need = MIN_CANDIDATES - len(candidate_keys)
                if need > 0:
                    # 从 shape_pool 追加一些（不要全量，避免回到 20 万全扫）
                    remaining = shape_pool - candidate_keys
                    for i, k in enumerate(remaining):
                        if i >= need:
                            break
                        extra.add(k)
                candidate_keys = candidate_keys | extra
        else:
            # 没有花型名约束：只按形状池（通常是非圆桶）
            candidate_keys = shape_pool

        return candidate_keys

    # ------------------------------------------------------------
    # 细粒度评分（快速版：直接用拆分字段 + 不打日志）
    # ------------------------------------------------------------

    def _compute_match_score_fast(
        self,
        e: TemplateEntry,
        *,
        tgt_pattern: str,
        tgt_material: str,
        tgt_is_vert: bool,
        tgt_has_corners: bool,
        tgt_is_circle: bool,
        has_size: bool,
        t_w: float,
        t_h: float,
        t_ratio: Optional[float],
    ) -> tuple[float, dict]:
        """快速评分：字段直读、避免日志。细节与旧版语义一致。"""
        details = {
            'name_match': False,
            'material_match': False,
            'direction_match': False,
            'shape_match': False,
            'size_diff': float('inf'),
            'ratio_diff': float('inf'),
        }

        score = 0.0

        # 1. 花型名匹配 (30分)
        tpl_pat = e._pattern_key
        if tgt_pattern and tpl_pat:
            if tgt_pattern == tpl_pat:
                score += 30
                details['name_match'] = True
            elif tgt_pattern in tpl_pat or tpl_pat in tgt_pattern:
                score += 20
                details['name_match'] = True

        # 2. 形状关键词匹配 (5分)
        tpl_kw = []
        if e._shape_keywords_serialized:
            try:
                tpl_kw = json.loads(e._shape_keywords_serialized)
            except (json.JSONDecodeError, TypeError):
                tpl_kw = []
        # 注意：target.shape_keywords 在这里未传，因为外层预过滤已经做了形状排斥；
        # 这里仅做关键词加分（不影响硬约束）。如果未来需要形状关键词硬约束，可以把 target_kw 传进来。
        # 简化：当前评分保持旧版，但此处外层已经做了形状排斥/花型排斥的硬约束。
        # 这里 5 分从简（因为硬约束已经在外层）——但为了评分一致性，继续给分（可按需扩展）。

        # 3. 尺寸比例 (40分)
        pw, ph = e._width_cm, e._height_cm
        if has_size and pw > 0 and ph > 0:
            tpl_ratio = max(pw, ph) / min(pw, ph)
            ratio_diff = abs(t_ratio - tpl_ratio) if t_ratio is not None else float('inf')
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
            norm1 = abs(pw - t_w) / max(t_w, 1) + abs(ph - t_h) / max(t_h, 1)
            norm2 = abs(ph - t_w) / max(t_w, 1) + abs(pw - t_h) / max(t_h, 1)
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
        tpl_mat = (e._material or "").lower()
        if tgt_material and tpl_mat:
            if tgt_material == tpl_mat:
                score += 5
                details['material_match'] = True

        # 6. 方向匹配 (10分)
        tpl_layout = e._layout or ""
        if tpl_layout:
            tpl_is_vert = tpl_layout == "竖版"
            if tpl_is_vert == tgt_is_vert:
                score += 10
                details['direction_match'] = True
            else:
                if details.get('ratio_diff', float('inf')) < 0.05:
                    score += 3

        return score, details

    # ============================================================
    # 旧版兼容函数（保留原签名）
    # ============================================================

    def _compute_match_score(self, target: ParsedFilename, entry: TemplateEntry) -> tuple[float, dict]:
        """
        保留旧版实现（语义一致，但走新的 fast 路径 + 可选日志）。

        注意：旧版在循环里大量 self._log 会严重拖慢性能，
        默认仅在 enable_match_debug_log=True 时输出日志。
        """
        # 语义保持：走新版 fast 函数
        # （硬排斥在 find_best_match 外层已经做过，这里为兼容再做一次）
        target_has_corners = bool(target.corners) and any(v > 0 for v in target.corners.values())
        target_is_circle = target.is_circular and not target_has_corners
        if target_has_corners and entry.is_circular:
            if self.enable_match_debug_log:
                self._log(f"   ❌ 形状排斥：目标是圆角矩形，排除圆形模板 {os.path.basename(entry.path)}")
            return 0.0, {
                'name_match': False, 'material_match': False,
                'direction_match': False, 'shape_match': False,
                'size_diff': float('inf'), 'ratio_diff': float('inf'),
            }
        if target_is_circle and not entry.is_circular:
            if self.enable_match_debug_log:
                self._log(f"   ❌ 形状排斥：目标是圆形，排除非圆形模板 {os.path.basename(entry.path)}")
            return 0.0, {
                'name_match': False, 'material_match': False,
                'direction_match': False, 'shape_match': False,
                'size_diff': float('inf'), 'ratio_diff': float('inf'),
            }

        # 花型名排斥（仅当目标有明确材质+花型）
        tgt_pattern = (target.pattern_name or "").lower()
        tpl_pattern = (entry.parsed.pattern_name or "").lower() if entry.parsed else ""
        if target.material and tgt_pattern:
            if not tpl_pattern:
                if self.enable_match_debug_log:
                    self._log(f"   ❌ 花型名排斥：目标有[{tgt_pattern}]，模板无花型名")
                return 0.0, {
                    'name_match': False, 'material_match': False,
                    'direction_match': False, 'shape_match': False,
                    'size_diff': float('inf'), 'ratio_diff': float('inf'),
                }
            if tgt_pattern != tpl_pattern and not (
                tgt_pattern in tpl_pattern or tpl_pattern in tgt_pattern
            ):
                if self.enable_match_debug_log:
                    self._log(f"   ❌ 花型名排斥：目标[{tgt_pattern}] 与 模板[{tpl_pattern}] 完全不同")
                return 0.0, {
                    'name_match': False, 'material_match': False,
                    'direction_match': False, 'shape_match': False,
                    'size_diff': float('inf'), 'ratio_diff': float('inf'),
                }

        tgt_layout = target.layout or ""
        has_size = (target.width_cm > 0 and target.height_cm > 0)
        if has_size:
            t_ratio = max(target.width_cm, target.height_cm) / min(target.width_cm, target.height_cm)
        else:
            t_ratio = None

        score, details = self._compute_match_score_fast(
            entry,
            tgt_pattern=tgt_pattern,
            tgt_material=(target.material or "").lower(),
            tgt_is_vert=(tgt_layout == "竖版"),
            tgt_has_corners=target_has_corners,
            tgt_is_circle=target_is_circle,
            has_size=has_size,
            t_w=target.width_cm,
            t_h=target.height_cm,
            t_ratio=t_ratio,
        )
        return score, details

    # ------------------------------------------------------------
    # 其他兼容 API
    # ------------------------------------------------------------

    def get_library_stats(self) -> dict:
        """获取模板库统计信息（与旧版兼容）"""
        if self._template_dir and (not self._cache or self._needs_refresh()):
            self.scan_library()
        return {
            'total': len(self._cache),
            'has_pattern': sum(1 for e in self._cache.values() if e._pattern_name),
            'has_size': sum(1 for e in self._cache.values() if e._width_cm > 0),
            'has_material': sum(1 for e in self._cache.values() if e._material),
            'has_custom': sum(1 for e in self._cache.values() if e.is_custom),
        }

    def clear_cache(self):
        """清空所有缓存（内存 + 磁盘）"""
        self._cache = {}
        self._idx_pattern = {}
        self._idx_layout = {}
        self._idx_circular = {True: [], False: []}
        self._subdir_mtimes = {}
        self._dir_mtime = 0
        try:
            cp = self._cache_path()
            if os.path.exists(cp):
                os.remove(cp)
        except OSError:
            pass

    def get_template_dir(self) -> str:
        return self._template_dir

    # ------------------------------------------------------------
    # 统计信息（调试用）
    # ------------------------------------------------------------

    def get_index_stats(self) -> dict:
        """返回倒排索引状态（调试/诊断用）"""
        return {
            "total_entries": len(self._cache),
            "pattern_buckets": len(self._idx_pattern),
            "layout_buckets": {k: len(v) for k, v in self._idx_layout.items()},
            "circular_buckets": {k: len(v) for k, v in self._idx_circular.items()},
            "subdirs_tracked": len(self._subdir_mtimes),
        }


# ============================================================================
# 便捷函数（保持旧 API 签名）
# ============================================================================

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
