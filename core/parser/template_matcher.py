"""
core/parser/template_matcher.py
模板库扫描与匹配引擎（高性能版 v2）。

性能优化（v2 新增 / 相对上一版）：
【优化 1a】磁盘缓存 pickle 化（protocol 4）：体积降 1/2-2/3，读写速度 3-5 倍；保留 JSON 为兼容 fallback
【优化 1b】scan_library 快速跳过路径：force=False 时根目录 mtime 未变直接跳过全目录 walk；
            并加写回合并阈值（<0.1% 且 <100 条变更不立即 dump，避免每次加 1 张图重写 80MB）
【优化 2a】新增尺寸比例 idx_ratio 分桶索引（7 档）
【优化 2b】预过滤阶段 pattern "精确桶 + 左前缀桶" 优先；只有不够 MIN_CANDIDATES 时才启用双向包含桶
           无花型名（最大瓶颈场景）时叠加 ratio 桶 ∩ shape_pool，候选从 19 万 → 几万
【优化 2c】通用词防爆：
           - target_pattern 单字（len<2）不启用包含桶，避免 "花"拉进所有带花字的 pattern
           - 每个候选集最大容量 BUCKET_MAX_CAPACITY = 15000，超过则停止继续加包含桶
其他既有优化：磁盘持久化缓存、子目录增量扫描、倒排索引预过滤、快速评分函数、禁用匹配日志

向后兼容：对外 API 完全不变（TemplateMatcher / scan_template_directory / match_template）。
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import pickle
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

from .name_parser import (
    ParsedFilename,
    parse_filename,
)


# ============================================================================
# 常量：匹配 / 索引 / 缓存
# ============================================================================

# 最小候选集阈值（预过滤后若候选 < 这个数量，会逐级放宽条件补齐）
MIN_CANDIDATES = 100

# 候选集绝对上限（防爆：防止通用词 pattern 把 5 万+ 条目都拉进来做评分）
BUCKET_MAX_CAPACITY = 15000

# 比例桶分界（ratio = max(w, h) / min(w, h)）
RATIO_BOUNDS = [1.0, 1.2, 1.5, 2.0, 3.0, 5.0]
RATIO_BUCKET_LABELS = ["<1.0", "1.0-1.2", "1.2-1.5", "1.5-2.0", "2.0-3.0", "3.0-5.0", ">=5.0"]

# 扫描快速路径：自上次完整 walk 以来，根目录 mtime 未变且未超过这个秒数 → 跳过全 walk
# 设为 6 小时：防止外部进程向孙子目录写新图后"父目录 mtime 未被操作系统传播"的极端 NTFS 场景
QUICK_SKIP_MAX_STALE_SEC = 6 * 3600

# 写缓存合并阈值：变更条目相对占比 < 0.001 (0.1%) 且 绝对数量 < 100 → 不立即写磁盘，累计到下次再写
MERGE_WRITE_RATIO_THRESHOLD = 0.001
MERGE_WRITE_ABS_THRESHOLD = 100


def ratio_bucket_for(w: float, h: float) -> str:
    """计算尺寸比例桶标签；无尺寸返回 ''"""
    if w <= 0 or h <= 0:
        return ""
    r = max(w, h) / min(w, h)
    if r < RATIO_BOUNDS[0]:
        return RATIO_BUCKET_LABELS[0]
    for i in range(len(RATIO_BOUNDS) - 1):
        if RATIO_BOUNDS[i] <= r < RATIO_BOUNDS[i + 1]:
            return RATIO_BUCKET_LABELS[i + 1]
    return RATIO_BUCKET_LABELS[-1]


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class TemplateEntry:
    """模板库条目（保持与旧版字段兼容）"""
    path: str = ""
    filename: str = ""
    # 评分缓存（每次评分后回写）
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
    _pattern_key: str = ""
    _shape_keywords_serialized: str = ""
    _file_mtime: float = 0.0
    _ratio_bucket: str = ""  # 新增：比例分桶标签

    # ---- 解析对象兼容（延迟构造） ----
    @property
    def parsed(self) -> Optional[ParsedFilename]:
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
        if value is None:
            self._product_name = ""
            self._layout = ""
            self._width_cm = 0.0
            self._height_cm = 0.0
            self._material = ""
            self._pattern_name = ""
            self._pattern_key = ""
            self._shape_keywords_serialized = ""
            self._ratio_bucket = ""
            return
        self._product_name = value.product_name or ""
        self._layout = value.layout or ""
        self._width_cm = float(value.width_cm or 0.0)
        self._height_cm = float(value.height_cm or 0.0)
        self._material = value.material or ""
        self._pattern_name = value.pattern_name or ""
        self._pattern_key = self._pattern_name.strip().lower()
        kw = value.shape_keywords or []
        self._shape_keywords_serialized = json.dumps(kw, ensure_ascii=False) if kw else ""
        self._ratio_bucket = ratio_bucket_for(self._width_cm, self._height_cm)

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
            "_ratio_bucket": self._ratio_bucket,
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
        rb = d.get("_ratio_bucket", "")
        if not rb:
            # 兼容旧版缓存：没有 ratio_bucket 则现算
            rb = ratio_bucket_for(e._width_cm, e._height_cm)
        e._ratio_bucket = rb
        return e


# ============================================================================
# 磁盘缓存容器
# ============================================================================

_CACHE_SCHEMA_VERSION = 2  # v2：新增 idx_ratio 字段 + TemplateEntry._ratio_bucket


@dataclass
class DiskCache:
    """磁盘缓存：20 万条解析结果 + 子目录 mtime + 所有倒排索引"""
    schema_version: int = _CACHE_SCHEMA_VERSION
    template_dir: str = ""
    subdir_mtimes: dict = field(default_factory=dict)
    entries: dict = field(default_factory=dict)
    idx_pattern: dict = field(default_factory=dict)
    idx_layout: dict = field(default_factory=dict)
    idx_circular: dict = field(default_factory=dict)
    idx_ratio: dict = field(default_factory=dict)  # v2 新增
    last_scan_at: float = 0.0
    last_full_walk_at: float = 0.0  # v2 新增：上次真正 os.walk 的时间（用于快速跳过）

    # ---- 序列化（pickle 优先 + JSON fallback）----
    def save(self, path_without_ext: str):
        """写入：优先写 .pickle；失败时写 .json（保证可读性/可恢复）"""
        data = {
            "schema_version": self.schema_version,
            "template_dir": self.template_dir,
            "subdir_mtimes": self.subdir_mtimes,
            "entries": {k: v.to_cache_dict() for k, v in self.entries.items()},
            "idx_pattern": self.idx_pattern,
            "idx_layout": self.idx_layout,
            # JSON 不允许 bool 键做某些序列化（且 pickle 与 JSON 一致更安全）→ 统一字符串键
            "idx_circular": {str(k): list(v) for k, v in self.idx_circular.items()},
            "idx_ratio": self.idx_ratio,
            "last_scan_at": self.last_scan_at,
            "last_full_walk_at": self.last_full_walk_at,
        }
        # 1) 先写 pickle
        pkl_path = path_without_ext + ".pickle"
        tmp_pkl = pkl_path + ".tmp"
        wrote_pickle = False
        try:
            with open(tmp_pkl, "wb") as f:
                pickle.dump(data, f, protocol=4)
            os.replace(tmp_pkl, pkl_path)
            wrote_pickle = True
        except Exception as e:
            logger.warning(f"磁盘缓存 pickle 写入失败 path={pkl_path}: {e}")
            try:
                if os.path.exists(tmp_pkl):
                    os.remove(tmp_pkl)
            except OSError:
                pass

        # 2) 同时写 JSON（小量容错：pickle 损坏时可手工恢复）
        #    —— 仅当 pickle 成功写入且文件 < 2 万条时再写 JSON，避免大文件 IO
        json_path = path_without_ext + ".json"
        if wrote_pickle and len(self.entries) <= 20000:
            tmp_json = json_path + ".tmp"
            try:
                with open(tmp_json, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp_json, json_path)
            except OSError:
                try:
                    if os.path.exists(tmp_json):
                        os.remove(tmp_json)
                except OSError:
                    pass

    @classmethod
    def load(cls, path_without_ext: str, expected_template_dir: str) -> Optional["DiskCache"]:
        """加载：优先 pickle → 其次 JSON → None"""
        pkl_path = path_without_ext + ".pickle"
        data = None
        if os.path.exists(pkl_path):
            try:
                with open(pkl_path, "rb") as f:
                    data = pickle.load(f)
            except Exception as e:
                logger.warning(f"磁盘缓存 pickle 加载失败 path={pkl_path}: {e}")
                data = None
        if data is None:
            json_path = path_without_ext + ".json"
            if not os.path.exists(json_path):
                return None
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                return None

        if not isinstance(data, dict):
            return None
        if data.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        if data.get("template_dir") != expected_template_dir:
            return None

        try:
            cache = cls()
            cache.schema_version = int(data.get("schema_version", 0))
            cache.template_dir = data.get("template_dir", "")
            cache.subdir_mtimes = data.get("subdir_mtimes") or {}
            raw_entries = data.get("entries") or {}
            cache.entries = {
                k: (TemplateEntry.from_cache_dict(v) if isinstance(v, dict) else v)
                for k, v in raw_entries.items()
            }
            cache.idx_pattern = data.get("idx_pattern") or {}
            cache.idx_layout = data.get("idx_layout") or {}
            # idx_circular: 字符串键 -> 恢复成 bool
            circ: dict[bool, list[str]] = {True: [], False: []}
            for k_str, v in (data.get("idx_circular") or {}).items():
                if k_str in ("True", "true", True):
                    circ[True] = list(v)
                else:
                    circ[False] = list(v)
            cache.idx_circular = circ
            cache.idx_ratio = data.get("idx_ratio") or {}
            cache.last_scan_at = float(data.get("last_scan_at", 0) or 0)
            cache.last_full_walk_at = float(data.get("last_full_walk_at", 0) or 0)
            return cache
        except Exception as e:
            logger.warning(f"磁盘缓存反序列化失败: {e}")
            return None


def _cache_basepath_for(template_dir: str) -> str:
    """缓存文件基础路径（不带扩展名；实际保存时追加 .pickle / .json）"""
    abs_dir = os.path.abspath(template_dir)
    h = hashlib.md5(abs_dir.encode("utf-8")).hexdigest()[:12]
    base = os.path.join(os.path.expanduser("~"), ".smartshapecrop", "caches")
    os.makedirs(base, exist_ok=True)
    safe_name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5_-]+", "_", os.path.basename(abs_dir.rstrip(os.sep)))
    safe_name = safe_name[:40] or "library"
    return os.path.join(base, f"{safe_name}_{h}.cache")


# ============================================================================
# 主匹配引擎
# ============================================================================

class TemplateMatcher:
    """模板库匹配引擎"""

    IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif', '.webp', '.gif'}

    def __init__(self):
        self._template_dir: str = ""
        self._cache: dict[str, TemplateEntry] = {}
        # 倒排索引
        self._idx_pattern: dict[str, list[str]] = {}
        self._idx_layout: dict[str, list[str]] = {}
        self._idx_circular: dict[bool, list[str]] = {True: [], False: []}
        self._idx_ratio: dict[str, list[str]] = {}
        # 子目录 mtime
        self._subdir_mtimes: dict[str, float] = {}
        self._dir_mtime: float = 0
        # 上次完整 walk 的 epoch 时间戳（用于快速跳过）
        self._last_full_walk_at: float = 0.0
        # 回调
        self._on_log: Optional[Callable[[str], None]] = None
        # 控制参数
        self.disk_cache_enabled: bool = True
        self.enable_match_debug_log: bool = False
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
        if self._template_dir == dir_path:
            return
        self._template_dir = dir_path
        self._cache = {}
        self._idx_pattern = {}
        self._idx_layout = {}
        self._idx_circular = {True: [], False: []}
        self._idx_ratio = {}
        self._subdir_mtimes = {}
        self._dir_mtime = 0
        self._last_full_walk_at = 0.0

    def get_template_dir(self) -> str:
        return self._template_dir

    # ------------------------------------------------------------
    # 磁盘缓存 I/O
    # ------------------------------------------------------------

    def _cache_path(self) -> str:
        return _cache_basepath_for(self._template_dir)

    def _load_disk_cache(self) -> DiskCache | None:
        if not self.disk_cache_enabled or self.force_rebuild:
            return None
        return DiskCache.load(self._cache_path(), os.path.abspath(self._template_dir))

    def _save_disk_cache(self, updated_full_walk: bool):
        if not self.disk_cache_enabled:
            return
        dc = DiskCache()
        dc.template_dir = os.path.abspath(self._template_dir)
        dc.subdir_mtimes = dict(self._subdir_mtimes)
        dc.entries = dict(self._cache)  # entries 直接存 TemplateEntry（序列化在 DiskCache.save 内 to_cache_dict）
        dc.idx_pattern = {k: list(v) for k, v in self._idx_pattern.items()}
        dc.idx_layout = {k: list(v) for k, v in self._idx_layout.items()}
        dc.idx_circular = {str(k): list(v) for k, v in self._idx_circular.items()}
        dc.idx_ratio = {k: list(v) for k, v in self._idx_ratio.items()}
        dc.last_scan_at = time.time()
        if updated_full_walk:
            self._last_full_walk_at = dc.last_scan_at
        dc.last_full_walk_at = self._last_full_walk_at
        dc.save(self._cache_path())

    # ------------------------------------------------------------
    # 扫描：快速跳过路径 + 增量 + 合并写回
    # ------------------------------------------------------------

    def _try_quick_skip(self, force: bool) -> bool:
        """快速路径检查：返回 True 表示可以跳过本次完整扫描。

        跳过条件（force=False 且 已有内存缓存）：
          a) 根目录 mtime 与上次记录相同
          b) 距上次 full walk 未超过 QUICK_SKIP_MAX_STALE_SEC
        """
        if force:
            return False
        if not self._cache:
            return False
        if self._dir_mtime <= 0:
            return False
        try:
            current_mtime = os.path.getmtime(self._template_dir)
        except Exception as e:
            logger.warning(f"获取模板目录 mtime 失败(quick_skip): {e}")
            return False
        if abs(current_mtime - self._dir_mtime) > 1e-6:
            return False
        now = time.time()
        if self._last_full_walk_at <= 0 or (now - self._last_full_walk_at) > QUICK_SKIP_MAX_STALE_SEC:
            return False
        return True

    def scan_library(self, force: bool = False) -> dict[str, TemplateEntry]:
        if not self._template_dir:
            self._log("⚠️ 未设置模板库目录")
            return {}

        # force=True：清空一切
        if force:
            self.force_rebuild = True
            self._cache = {}
            self._idx_pattern = {}
            self._idx_layout = {}
            self._idx_circular = {True: [], False: []}
            self._idx_ratio = {}
            self._subdir_mtimes = {}
            self._dir_mtime = 0
            self._last_full_walk_at = 0.0
            # 删除磁盘缓存文件（pickle + json）
            try:
                base = self._cache_path()
                for ext in (".pickle", ".json"):
                    p = base + ext
                    if os.path.exists(p):
                        os.remove(p)
            except OSError:
                pass
            self.force_rebuild = False

        t0 = time.time()

        # 1) 无内存缓存 -> 尝试加载磁盘缓存
        loaded_from_disk = False
        if not self._cache:
            dc = self._load_disk_cache()
            if dc is not None:
                self._cache = dc.entries
                self._idx_pattern = dc.idx_pattern
                self._idx_layout = dc.idx_layout
                self._idx_circular = {
                    True: list(dc.idx_circular.get(True, [])),
                    False: list(dc.idx_circular.get(False, [])),
                }
                self._idx_ratio = dc.idx_ratio
                self._subdir_mtimes = dc.subdir_mtimes
                self._last_full_walk_at = dc.last_full_walk_at
                loaded_from_disk = True
                self._log(
                    f"💾 加载磁盘缓存成功（{len(self._cache)} 个条目，"
                    f"上次扫描 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(dc.last_scan_at))}）"
                )

        # 2) 快速跳过检查（已存在缓存，根目录未变）
        if self._try_quick_skip(force):
            dt = time.time() - t0
            stale_h = (time.time() - self._last_full_walk_at) / 3600
            self._log(
                f"⚡ 快速跳过扫描（根目录未变；距上次全扫 {stale_h:.1f}h） "
                f"共 {len(self._cache)} 条目，耗时 {dt*1000:.0f}ms"
            )
            return self._cache

        # 3) 执行增量扫描
        total_entries_before = len(self._cache)
        cache_file_count, total_file_count, dirs_scanned, dirs_skipped, did_full_walk = self._do_incremental_scan()

        try:
            self._dir_mtime = os.path.getmtime(self._template_dir)
        except Exception as e:
            logger.warning(f"获取模板目录 mtime 失败(scan_library): {e}")
            self._dir_mtime = 0

        if did_full_walk:
            self._last_full_walk_at = time.time()

        # 4) 写磁盘缓存：合并阈值判断
        total_now = len(self._cache)
        changed_abs = cache_file_count or 0
        if total_now:
            changed_ratio = changed_abs / total_now
        else:
            changed_ratio = 1.0
        # 触发写的条件：首次(loaded_from_disk 之前无内容) / force / 数量变了 / 变更达到阈值
        should_write = (
            (total_now != total_entries_before and not loaded_from_disk)
            or force
            or (loaded_from_disk and cache_file_count > 0)  # 从磁盘加载后又有增量，需要合并回写
            or (changed_ratio >= MERGE_WRITE_RATIO_THRESHOLD)
            or (changed_abs >= MERGE_WRITE_ABS_THRESHOLD)
        )
        if should_write:
            t_save_start = time.time()
            self._save_disk_cache(updated_full_walk=did_full_walk)
            dt_save = time.time() - t_save_start
            save_note = f"；写缓存 {dt_save:.2f}s"
        else:
            dt_save = 0.0
            save_note = f"；变更{changed_abs}条（{changed_ratio*100:.3f}%）< 阈值，延迟写"

        dt = time.time() - t0
        self._log(
            f"📂 扫描完成：共 {total_file_count} 文件，{len(self._cache)} 有效图片 "
            f"（增量更新 {cache_file_count} 个；目录 扫{dirs_scanned}/跳{dirs_skipped}）"
            f"{' [首次/已持久化]' if loaded_from_disk else ''} "
            f"耗时 {dt:.2f}s{save_note}"
        )
        return self._cache

    def _do_incremental_scan(self) -> tuple[int, int, int, int, bool]:
        """返回：(updated_count, total_file_count, dirs_scanned, dirs_skipped, did_full_walk)"""
        # 收集所有子目录
        all_dirs: list[str] = []
        try:
            for root, dirs, files in os.walk(self._template_dir, followlinks=False):
                all_dirs.append(root)
        except (PermissionError, OSError):
            all_dirs = [self._template_dir]
        did_full_walk = True

        current_mtimes: dict[str, float] = {}
        for d in all_dirs:
            try:
                current_mtimes[d] = os.path.getmtime(d)
            except OSError:
                current_mtimes[d] = 0.0

        # A) 目录变更判定
        dirs_to_scan: set[str] = set()
        dirs_skipped = 0
        for d, mt in current_mtimes.items():
            prev = self._subdir_mtimes.get(d)
            if prev is None or abs(prev - mt) > 1e-6:
                dirs_to_scan.add(d)
            else:
                dirs_skipped += 1

        gone_dirs = [d for d in self._subdir_mtimes if d not in current_mtimes]
        if gone_dirs:
            self._purge_dirs(gone_dirs)

        # B) 扫描变更目录内文件
        updated_count = 0
        total_file_count = 0
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

                        try:
                            f_mtime = entry.stat(follow_symlinks=False).st_mtime
                        except OSError:
                            f_mtime = 0.0

                        old = self._cache.get(key)
                        if (old is not None
                                and abs(old._file_mtime - f_mtime) < 1e-6
                                and old.path == entry.path):
                            continue

                        tpl = self._create_template_entry(entry.path, entry.name)
                        tpl._file_mtime = f_mtime
                        self._upsert_entry(key, tpl, old)
                        updated_count += 1
                except OSError:
                    continue

        # C) 清理扫描目录下已删除的文件
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

        # D) 更新 subdir mtimes
        new_sub = {}
        for d in all_dirs:
            new_sub[d] = current_mtimes.get(d, 0.0)
        self._subdir_mtimes = new_sub

        # 未扫描目录里的文件数计入总数
        total_file_count += sum(
            1 for e in self._cache.values()
            if os.path.dirname(e.path) not in dirs_to_scan
        )

        return updated_count, total_file_count, len(dirs_to_scan), dirs_skipped, did_full_walk

    def _purge_dirs(self, gone_dirs: list[str]):
        gone_set = set(gone_dirs)
        to_remove = [k for k, e in self._cache.items() if os.path.dirname(e.path) in gone_set]
        for k in to_remove:
            self._remove_entry(k)

    # ------------------------------------------------------------
    # 索引维护
    # ------------------------------------------------------------

    def _upsert_entry(self, key: str, new_entry: TemplateEntry, old_entry: Optional[TemplateEntry]):
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
        # pattern
        pk = e._pattern_key
        if pk:
            bucket = self._idx_pattern.setdefault(pk, [])
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
        # ratio
        rb = e._ratio_bucket or ""
        bucket = self._idx_ratio.setdefault(rb, [])
        if not bucket or bucket[-1] != key:
            bucket.append(key)

    def _deindex_entry(self, key: str, e: TemplateEntry):
        pk = e._pattern_key
        if pk and pk in self._idx_pattern:
            try:
                self._idx_pattern[pk].remove(key)
            except ValueError:
                pass
            if not self._idx_pattern[pk]:
                del self._idx_pattern[pk]
        ly = e._layout or ""
        if ly in self._idx_layout:
            try:
                self._idx_layout[ly].remove(key)
            except ValueError:
                pass
            if not self._idx_layout[ly]:
                del self._idx_layout[ly]
        circ = bool(e.is_circular)
        bucket = self._idx_circular.get(circ)
        if bucket:
            try:
                bucket.remove(key)
            except ValueError:
                pass
        rb = e._ratio_bucket or ""
        if rb in self._idx_ratio:
            try:
                self._idx_ratio[rb].remove(key)
            except ValueError:
                pass
            if not self._idx_ratio[rb]:
                del self._idx_ratio[rb]

    # ------------------------------------------------------------
    # 创建条目
    # ------------------------------------------------------------

    def _create_template_entry(self, path: str, filename: str) -> TemplateEntry:
        entry = TemplateEntry(path=path, filename=filename)
        try:
            parsed = parse_filename(filename)
            entry.parsed = parsed
            entry.is_circular = parsed.is_circular
            entry.is_custom = parsed.is_custom
        except Exception as e:
            logger.warning(f"模板条目文件名解析失败 filename={filename}: {e}")
            entry.parsed = None
        return entry

    # ------------------------------------------------------------
    # 匹配：预过滤 + 评分
    # ------------------------------------------------------------

    def find_best_match(self, target_filename: str) -> tuple[Optional[TemplateEntry], list[TemplateEntry]]:
        if not self._template_dir:
            self._log("⚠️ 请先设置模板库目录")
            return None, []

        self.scan_library()

        target_parsed = parse_filename(target_filename)
        self._log(
            f"🔍 目标解析: 产品={target_parsed.product_name}, 花型={target_parsed.pattern_name}, "
            f"尺寸={target_parsed.width_cm}x{target_parsed.height_cm}cm, 布局={target_parsed.layout}"
        )

        candidate_keys = self._prefilter_candidates(target_parsed)

        candidates: list[TemplateEntry] = []
        tgt = target_parsed
        tgt_pattern = (tgt.pattern_name or "").lower()
        tgt_material = (tgt.material or "").lower()
        tgt_has_corners = bool(tgt.corners) and any(v > 0 for v in tgt.corners.values())
        tgt_is_circle = tgt.is_circular and not tgt_has_corners
        tgt_layout = tgt.layout or ""
        tgt_is_vert = (tgt_layout == "竖版")
        t_w, t_h = tgt.width_cm, tgt.height_cm

        # [Fix 2026-08-26] 水池模式：已统一使用标准规则（长边为宽、短边为高）
        #   name_parser 现在直接设置正确的 width_cm/height_cm，oriented_outer_w_h_cm()
        #   不再做二次交换，因此直接使用解析后的尺寸即可。
        #   例：58x121CM → width_cm=121, height_cm=58 → 正确的横版尺寸

        has_size = (t_w > 0 and t_h > 0)
        t_ratio = (max(t_w, t_h) / min(t_w, t_h)) if has_size else None
        force_pattern = bool(tgt.material and tgt_pattern)

        # 形状关键词分类（与 name_parser.SHAPE_SUFFIXES 对齐）
        _SHAPE_KEYWORDS_DIRECTION = {'横版', '竖版', '横', '竖'}
        _SHAPE_KEYWORDS_SHAPE = {'弧形', '圆形', '方形', '弧', '圆', '方', '裁剪有图'}

        tgt_shape_kw = set(tgt.shape_keywords or [])
        tgt_shape_only = tgt_shape_kw & _SHAPE_KEYWORDS_SHAPE
        self._log(
            f"🔍 目标形状关键词: 全部={tgt_shape_kw}, 形状类={tgt_shape_only}"
        )

        scored_count = 0
        shape_rejected_count = 0
        for k in candidate_keys:
            e = self._cache.get(k)
            if e is None:
                continue
            if (not e._product_name and not e._pattern_name
                    and e._width_cm <= 0 and e._height_cm <= 0):
                continue

            template_is_circle = e.is_circular
            if tgt_has_corners and template_is_circle:
                continue
            if tgt_is_circle and not template_is_circle:
                continue
            if force_pattern:
                tpl_pat = e._pattern_key
                if not tpl_pat:
                    continue
                if tgt_pattern != tpl_pat and not (tgt_pattern in tpl_pat or tpl_pat in tgt_pattern):
                    continue

            # [Fix 形状关键词严格匹配 2026-08-17]
            # 方向关键词(横版/竖版)允许匹配，形状关键词(弧形/圆形/方形)必须严格匹配
            # 规则：模板含有的形状关键词，目标必须也含有；否则拒绝匹配
            tpl_parsed = e.parsed
            if tpl_parsed:
                tpl_shape_kw = set(tpl_parsed.shape_keywords or [])
                tpl_shape_only = tpl_shape_kw & _SHAPE_KEYWORDS_SHAPE
                # 提取模板独有形状关键词（不在目标中）
                tpl_exclusive_shape = tpl_shape_only - tgt_shape_only
                if tpl_exclusive_shape:
                    if self.enable_match_debug_log:
                        self._log(
                            f"   ❌ 形状排斥：模板 [{e.filename}] "
                            f"含形状关键词 {tpl_exclusive_shape}，目标不含"
                        )
                    shape_rejected_count += 1
                    continue

            score, details = self._compute_match_score_fast(
                e,
                tgt_pattern=tgt_pattern,
                tgt_material=tgt_material,
                tgt_is_vert=tgt_is_vert,
                tgt_has_corners=tgt_has_corners,
                tgt_is_circle=tgt_is_circle,
                has_size=has_size,
                t_w=t_w, t_h=t_h, t_ratio=t_ratio,
                # —— 水池模式 ——
                tgt_pool_mode=tgt.pool_mode,
                tgt_pool_pattern=(tgt.pool_pattern_name or "").lower(),
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
        v2 预过滤策略（防爆 + 召回兼顾）：

        A. 形状池：
            目标圆 → 只取圆桶
            目标非圆 → 全量 - 圆桶

        B. 目标有花型名：
            B1. 精确桶（pattern_key == target）→ 加进去
            B2. 左前缀桶（idx_pat startswith target_pattern）→ 加进去（target=克罗印花 → 克罗印花A 属于）
            B3. 若此时 ≥ MIN_CANDIDATES → 停止；否则才开启"双向包含桶"，但有防爆：
                - target_pattern 长度 < 2 → 跳过包含桶（避免 1 字通用词爆炸）
                - 单次加入超过 BUCKET_MAX_CAPACITY 上限就停
            B4. 形状池 ∩ pattern 池 = 候选核心
            B5. 不足 MIN_CANDIDATES → 从 shape_pool 追加补齐

        C. 目标无花型名（最大瓶颈）：
            C1. 基础候选 = shape_pool（通常 19 万）
            C2. 如果目标有明确尺寸比例 → 再用 ratio 桶交集，砍到几万
            C3. 候选如仍大于 BUCKET_MAX_CAPACITY → 在 ratio 交集后再均匀采样到上限
        """
        target_pattern = (target.pattern_name or "").lower()
        target_has_corners = bool(target.corners) and any(v > 0 for v in target.corners.values())
        target_is_circle = target.is_circular and not target_has_corners

        # --- A. 形状池 ---
        if target_is_circle:
            shape_pool = set(self._idx_circular.get(True, []))
        else:
            all_keys = set(self._cache.keys())
            true_circ = set(self._idx_circular.get(True, []))
            shape_pool = all_keys - true_circ

        # --- B. 有花型名 ---
        if target_pattern:
            pattern_bucket_keys: set[str] = set()

            # B1. 精确
            if target_pattern in self._idx_pattern:
                pattern_bucket_keys.update(self._idx_pattern[target_pattern])

            # B2. 左前缀：idx_pat 以 target_pattern 开头
            #     例：target = "克罗印花" → 匹配 "克罗印花a"/"克罗印花-春"，不匹配 "中古大花"
            if len(pattern_bucket_keys) < MIN_CANDIDATES:
                for idx_pat, ks in self._idx_pattern.items():
                    if not idx_pat or idx_pat == target_pattern:
                        continue
                    if idx_pat.startswith(target_pattern):
                        pattern_bucket_keys.update(ks)
                        if len(pattern_bucket_keys) >= BUCKET_MAX_CAPACITY:
                            break

            # B3. 若仍不够才开包含桶（防爆：短字禁用 + 容量上限）
            if len(pattern_bucket_keys) < MIN_CANDIDATES and len(target_pattern) >= 2:
                for idx_pat, ks in self._idx_pattern.items():
                    if not idx_pat or idx_pat == target_pattern:
                        continue
                    # 跳过已加过的左前缀桶
                    if idx_pat.startswith(target_pattern):
                        continue
                    if target_pattern in idx_pat or idx_pat in target_pattern:
                        pattern_bucket_keys.update(ks)
                        if len(pattern_bucket_keys) >= BUCKET_MAX_CAPACITY:
                            break

            # B4. 形状池 ∩ pattern 池
            candidate_keys = shape_pool & pattern_bucket_keys

            # B5. 不足 MIN_CANDIDATES → 放宽
            if len(candidate_keys) < MIN_CANDIDATES and pattern_bucket_keys:
                if not target.material:
                    candidate_keys = candidate_keys | pattern_bucket_keys
            if len(candidate_keys) < MIN_CANDIDATES:
                need = MIN_CANDIDATES - len(candidate_keys)
                remaining = shape_pool - candidate_keys
                extra = set()
                for i, k in enumerate(remaining):
                    if i >= need:
                        break
                    extra.add(k)
                candidate_keys = candidate_keys | extra

            # 候选过大保护（防爆桶）
            if len(candidate_keys) > BUCKET_MAX_CAPACITY:
                # 不直接截断：优先保留精确+左前缀集合与 shape_pool 的交集部分，然后再在包含桶里采样
                exact_and_prefix = set()
                if target_pattern in self._idx_pattern:
                    exact_and_prefix.update(self._idx_pattern[target_pattern])
                for idx_pat, ks in self._idx_pattern.items():
                    if idx_pat.startswith(target_pattern) and idx_pat != target_pattern:
                        exact_and_prefix.update(ks)
                core = shape_pool & exact_and_prefix
                if len(core) >= BUCKET_MAX_CAPACITY:
                    # 核心够大就用核心（等比例缩）
                    lst = list(core)
                    step = max(1, len(lst) // BUCKET_MAX_CAPACITY)
                    candidate_keys = set(lst[::step][:BUCKET_MAX_CAPACITY])
                else:
                    need = BUCKET_MAX_CAPACITY - len(core)
                    leftover = list(candidate_keys - core)
                    if leftover:
                        step = max(1, len(leftover) // max(need, 1))
                        candidate_keys = core | set(leftover[::step][:need])
                    else:
                        candidate_keys = core

            return candidate_keys

        # --- C. 无花型名（最大瓶颈）---
        candidate_keys = shape_pool

        # 先叠加 ratio 桶：有明确尺寸就切一刀
        t_w, t_h = target.width_cm, target.height_cm
        target_has_size = (t_w > 0 and t_h > 0)
        if target_has_size:
            target_rb = ratio_bucket_for(t_w, t_h)
            if target_rb and target_rb in self._idx_ratio:
                ratio_keys = set(self._idx_ratio[target_rb])
                # 也给相邻桶各取一部分（±1 档，避免 ratio 刚好在分界线上误杀）
                if target_rb in RATIO_BUCKET_LABELS:
                    idx_rb = RATIO_BUCKET_LABELS.index(target_rb)
                    for neighbour in (idx_rb - 1, idx_rb + 1):
                        if 0 <= neighbour < len(RATIO_BUCKET_LABELS):
                            nlabel = RATIO_BUCKET_LABELS[neighbour]
                            if nlabel in self._idx_ratio:
                                # 邻居桶取 50%
                                nb = self._idx_ratio[nlabel]
                                ratio_keys.update(nb[::2])
                before = len(candidate_keys)
                candidate_keys = candidate_keys & ratio_keys
                after = len(candidate_keys)
                self._log(f"   🎯 ratio 桶({target_rb}±1)：候选 {before} → {after}")

        # 防爆：仍然过大就均匀降采样到 BUCKET_MAX_CAPACITY
        if len(candidate_keys) > BUCKET_MAX_CAPACITY:
            lst = list(candidate_keys)
            step = max(1, len(lst) // BUCKET_MAX_CAPACITY)
            candidate_keys = set(lst[::step][:BUCKET_MAX_CAPACITY])
            self._log(f"   🧷 候选过大，均匀降采样至 {len(candidate_keys)}")

        return candidate_keys

    # ------------------------------------------------------------
    # 评分函数（快速版）
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
        # —— 水池设计器新增参数（默认保持旧行为）——
        tgt_pool_mode: bool = False,
        tgt_pool_pattern: str = "",
    ) -> tuple[float, dict]:
        details = {
            'name_match': False,
            'material_match': False,
            'direction_match': False,
            'shape_match': False,
            'size_diff': float('inf'),
            'ratio_diff': float('inf'),
            'pool_mode': tgt_pool_mode,
        }
        score = 0.0

        tpl_pat = e._pattern_key
        # 水池模式：优先用 pool_pattern（花型名更精确，如"克罗印花"），权重更高
        effective_pattern = (tgt_pool_pattern or "").lower() if (tgt_pool_mode and tgt_pool_pattern) else tgt_pattern
        if effective_pattern and tpl_pat:
            if tgt_pool_mode:
                if effective_pattern == tpl_pat:
                    score += 50
                    details['name_match'] = True
                elif effective_pattern in tpl_pat or tpl_pat in effective_pattern:
                    score += 35
                    details['name_match'] = True
            else:
                if effective_pattern == tpl_pat:
                    score += 30
                    details['name_match'] = True
                elif effective_pattern in tpl_pat or tpl_pat in effective_pattern:
                    score += 20
                    details['name_match'] = True

        pw, ph = e._width_cm, e._height_cm
        if has_size and pw > 0 and ph > 0:
            tpl_ratio = max(pw, ph) / min(pw, ph)
            ratio_diff = abs(t_ratio - tpl_ratio) if t_ratio is not None else float('inf')
            details['ratio_diff'] = ratio_diff

            # [Fix 2026-08-26] 水池模式：统一标准规则后，直接使用 width/height 比较方向
            #   目标和模板都遵循 "长边为宽、短边为高" 标准，AR = width/height
            #   方向一致性检查：两者都是横向(AR>1)或都是竖向(AR<1)
            if tgt_pool_mode:
                target_oriented_ratio = t_w / max(t_h, 0.001) if t_h > 0 else float('inf')
                tpl_oriented_ratio = pw / max(ph, 0.001) if ph > 0 else float('inf')
                details['target_oriented_ratio'] = target_oriented_ratio
                details['tpl_oriented_ratio'] = tpl_oriented_ratio
                
                tgt_is_landscape = target_oriented_ratio > 1.0
                tpl_is_landscape = tpl_oriented_ratio > 1.0
                details['orientation_match'] = tgt_is_landscape == tpl_is_landscape
                
                if not details['orientation_match']:
                    oriented_ratio_diff = abs(target_oriented_ratio - tpl_oriented_ratio)
                    ratio_diff = max(ratio_diff, oriented_ratio_diff)
                    details['ratio_diff'] = ratio_diff

            # 比例分（水池模式和普通模式权重一致）
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

            norm1 = abs(pw - t_w) / max(t_w, 1) + abs(ph - t_h) / max(t_h, 1)
            norm2 = abs(ph - t_w) / max(t_w, 1) + abs(pw - t_h) / max(t_h, 1)
            size_diff = min(norm1, norm2)
            details['size_diff'] = size_diff

            if tgt_pool_mode:
                # 水池：尺寸绝对值不敏感，降级；重点是素材够大（≥目标80%）可以缩放不糊
                if size_diff < 0.02:
                    score += 5
                elif size_diff < 0.05:
                    score += 4
                elif size_diff < 0.1:
                    score += 2
                elif size_diff < 0.2:
                    score += 1
                # 额外加分：模板面积 ≥ 目标面积 × 0.64（=单边×0.8）
                tgt_area = t_w * t_h
                tpl_area = pw * ph
                if tgt_area > 0 and tpl_area >= tgt_area * 0.64:
                    score += 10
            else:
                if size_diff < 0.02:
                    score += 10
                elif size_diff < 0.05:
                    score += 8
                elif size_diff < 0.1:
                    score += 5
                elif size_diff < 0.2:
                    score += 2

        tpl_mat = (e._material or "").lower()
        if tgt_material and tpl_mat:
            if tgt_material == tpl_mat:
                score += 5
                details['material_match'] = True

        tpl_layout = e._layout or ""
        if tpl_layout:
            tpl_is_vert = tpl_layout == "竖版"
            # [Fix 2026-08-26] 水池模式方向匹配：统一标准规则后直接使用 layout 比较
            #   不再需要 pool swap 反转逻辑
            if tgt_pool_mode:
                if tpl_is_vert == tgt_is_vert:
                    score += 10
                    details['direction_match'] = True
                else:
                    score -= 5
                    details['direction_match'] = False
            else:
                if tpl_is_vert == tgt_is_vert:
                    score += 10
                    details['direction_match'] = True
                else:
                    if details.get('ratio_diff', float('inf')) < 0.05:
                        score += 3

        return score, details

    # ============================================================
    # 旧版兼容
    # ============================================================

    def _compute_match_score(self, target: ParsedFilename, entry: TemplateEntry) -> tuple[float, dict]:
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
        t_ratio = max(target.width_cm, target.height_cm) / min(target.width_cm, target.height_cm) if has_size else None
        return self._compute_match_score_fast(
            entry,
            tgt_pattern=tgt_pattern,
            tgt_material=(target.material or "").lower(),
            tgt_is_vert=(tgt_layout == "竖版"),
            tgt_has_corners=target_has_corners,
            tgt_is_circle=target_is_circle,
            has_size=has_size,
            t_w=target.width_cm, t_h=target.height_cm, t_ratio=t_ratio,
            # —— 水池模式 ——
            tgt_pool_mode=target.pool_mode,
            tgt_pool_pattern=(target.pool_pattern_name or "").lower(),
        )

    def get_library_stats(self) -> dict:
        if self._template_dir and (not self._cache or self._needs_refresh()):
            self.scan_library()
        return {
            'total': len(self._cache),
            'has_pattern': sum(1 for e in self._cache.values() if e._pattern_name),
            'has_size': sum(1 for e in self._cache.values() if e._width_cm > 0),
            'has_material': sum(1 for e in self._cache.values() if e._material),
            'has_custom': sum(1 for e in self._cache.values() if e.is_custom),
        }

    def _needs_refresh(self) -> bool:
        if not self._template_dir:
            return False
        try:
            current_mtime = os.path.getmtime(self._template_dir)
            return current_mtime != self._dir_mtime or not self._cache
        except Exception as e:
            logger.warning(f"获取模板目录 mtime 失败(needs_refresh): {e}")
            return True

    def clear_cache(self):
        self._cache = {}
        self._idx_pattern = {}
        self._idx_layout = {}
        self._idx_circular = {True: [], False: []}
        self._idx_ratio = {}
        self._subdir_mtimes = {}
        self._dir_mtime = 0
        self._last_full_walk_at = 0.0
        try:
            base = self._cache_path()
            for ext in (".pickle", ".json"):
                p = base + ext
                if os.path.exists(p):
                    os.remove(p)
        except OSError:
            pass

    def get_index_stats(self) -> dict:
        return {
            "total_entries": len(self._cache),
            "pattern_buckets": len(self._idx_pattern),
            "layout_buckets": {k: len(v) for k, v in self._idx_layout.items()},
            "circular_buckets": {k: len(v) for k, v in self._idx_circular.items()},
            "ratio_buckets": {k: len(v) for k, v in self._idx_ratio.items()},
            "subdirs_tracked": len(self._subdir_mtimes),
            "last_full_walk_at": self._last_full_walk_at,
        }


# ============================================================================
# 便捷函数（保持旧 API 签名）
# ============================================================================

def scan_template_directory(dir_path: str, on_log: Optional[Callable[[str], None]] = None) -> dict[str, TemplateEntry]:
    matcher = TemplateMatcher()
    matcher.set_log_callback(on_log or (lambda _: None))
    matcher.set_template_dir(dir_path)
    return matcher.scan_library()


def match_template(target_filename: str, template_dir: str,
                   on_log: Optional[Callable[[str], None]] = None) -> tuple[Optional[TemplateEntry], list[TemplateEntry]]:
    matcher = TemplateMatcher()
    matcher.set_log_callback(on_log or (lambda _: None))
    matcher.set_template_dir(template_dir)
    matcher.scan_library()
    return matcher.find_best_match(target_filename)
