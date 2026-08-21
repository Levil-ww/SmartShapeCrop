"""
core/app_settings.py
应用级持久化设置（基于 QSettings）。

功能：
1. 模板库目录历史记录（最近打开的目录列表）
2. 默认模板库目录
3. 匹配引擎参数（未来可扩展）

跨会话保存：下次打开程序时自动恢复上次选择的模板库。
"""
from __future__ import annotations
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Qt 是可选依赖：无 GUI 的脚本环境下退化为 JSON 文件存储
try:
    from PyQt5.QtCore import QSettings
    _HAS_QT = True
except ImportError:
    _HAS_QT = False


# ---------------------------------------------------------------------------
# 历史记录条目
# ---------------------------------------------------------------------------

@dataclass
class TemplateDirHistory:
    """模板库历史记录条目"""
    path: str                          # 目录绝对路径
    last_used_at: float = 0.0          # 上次使用时间戳（epoch秒）
    total_files: int = 0               # 该库中的图片总数（上次扫描结果）
    # 以下字段仅展示用
    display_name: str = ""             # 显示名称（默认取 basename）

    @classmethod
    def from_path(cls, path: str) -> "TemplateDirHistory":
        p = os.path.abspath(path)
        return cls(
            path=p,
            last_used_at=time.time(),
            display_name=os.path.basename(p.rstrip(os.sep)) or p,
        )


@dataclass
class TargetNameHistory:
    """目标文件名历史记录条目（按日分组，保留 3 天）

    存储结构：dict[date_str (YYYY-MM-DD), list[record_dict]]
    每条记录：{"name": str, "timestamp": float, "source": str}
    source 取值：'cropper'（圆角裁剪工具）/ 'pool'（水池设计器）
    """
    name: str
    timestamp: float = 0.0
    source: str = ""

    @classmethod
    def create(cls, name: str, source: str) -> "TargetNameHistory":
        return cls(name=name, timestamp=time.time(), source=source)

    def to_dict(self) -> dict:
        return {"name": self.name, "timestamp": self.timestamp, "source": self.source}


# ---------------------------------------------------------------------------
# 主设置类
# ---------------------------------------------------------------------------

class AppSettings:
    """应用持久化设置。

    优先使用 QSettings（注册表/Ini），无 Qt 时使用 JSON 文件（与 main.py 同目录）。
    """

    # 键名常量
    KEY_TEMPLATE_DIR = "template/default_dir"
    KEY_TEMPLATE_HISTORY = "template/history"
    KEY_HISTORY_MAX = "template/history_max"
    KEY_MATCHER_CACHE_ENABLED = "matcher/cache_enabled"
    KEY_TARGET_NAME_HISTORY = "target_name/history"

    DEFAULT_HISTORY_MAX = 5
    # 目标文件名历史：保留 3 天（含今天），每天最多 50 条，同日同名去重置顶
    TARGET_NAME_HISTORY_DAYS = 3
    TARGET_NAME_HISTORY_PER_DAY = 50

    def __init__(self, organization: str = "SmartShapeCrop", app_name: str = "SmartShapeCrop"):
        self._history_max = self.DEFAULT_HISTORY_MAX
        if _HAS_QT:
            self._qs = QSettings(organization, app_name)
            self._fallback_path: Optional[str] = None
        else:
            self._qs = None
            # 退回到 JSON 文件（放在项目根目录或用户目录）
            self._fallback_path = self._find_fallback_path()
        self._history: list[TemplateDirHistory] = []
        self._load_history()

    # ------------------------------------------------------------
    # 内部：存储后端
    # ------------------------------------------------------------

    @staticmethod
    def _find_fallback_path() -> str:
        """确定 JSON 后备文件路径"""
        # 优先放在项目根目录下的 .app_settings.json
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidate = os.path.join(project_root, ".app_settings.json")
        try:
            if os.access(project_root, os.W_OK):
                return candidate
        except Exception as e:
            logger.warning(f"项目目录写入权限检测失败 root={project_root}: {e}")
        # 否则放到用户目录
        home = os.path.expanduser("~")
        return os.path.join(home, ".smartshapecrop_settings.json")

    def _read(self, key: str, default=None):
        if self._qs is not None:
            v = self._qs.value(key, default)
            # QSettings 返回 str/List 等，保持与 JSON 模式一致
            return v
        # JSON 后备
        data = self._read_fallback_json()
        return data.get(key, default)

    def _write(self, key: str, value):
        if self._qs is not None:
            self._qs.setValue(key, value)
            self._qs.sync()
            return
        # JSON 后备
        data = self._read_fallback_json()
        data[key] = value
        self._write_fallback_json(data)

    def _read_fallback_json(self) -> dict:
        if not self._fallback_path or not os.path.exists(self._fallback_path):
            return {}
        try:
            with open(self._fallback_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_fallback_json(self, data: dict):
        if not self._fallback_path:
            return
        try:
            tmp = self._fallback_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._fallback_path)
        except OSError:
            pass

    # ------------------------------------------------------------
    # 历史记录
    # ------------------------------------------------------------

    def _load_history(self):
        raw = self._read(self.KEY_TEMPLATE_HISTORY, None)
        items: list[TemplateDirHistory] = []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                raw = None
        if isinstance(raw, list):
            for d in raw:
                if not isinstance(d, dict):
                    continue
                p = d.get("path", "")
                if not p:
                    continue
                items.append(TemplateDirHistory(
                    path=p,
                    last_used_at=float(d.get("last_used_at", 0) or 0),
                    total_files=int(d.get("total_files", 0) or 0),
                    display_name=d.get("display_name") or os.path.basename(p.rstrip(os.sep)) or p,
                ))
        # 历史上限：单一来源 = DEFAULT_HISTORY_MAX（不受旧配置/遗留值干扰）
        self._history_max = self.DEFAULT_HISTORY_MAX
        # 读入后立即裁剪一次，确保即使原来的持久化里存了多于上限的条目也被修剪
        if len(items) > self._history_max:
            items = items[:self._history_max]
        self._history = items

    def _save_history(self):
        # 序列化
        serializable = [
            {
                "path": h.path,
                "last_used_at": h.last_used_at,
                "total_files": h.total_files,
                "display_name": h.display_name,
            }
            for h in self._history
        ]
        if self._qs is not None:
            # QSettings 兼容：用 JSON 字符串存 list[dict]
            self._write(self.KEY_TEMPLATE_HISTORY, json.dumps(serializable, ensure_ascii=False))
        else:
            self._write(self.KEY_TEMPLATE_HISTORY, serializable)

    # ------------------------------------------------------------
    # 公共 API：模板库默认目录
    # ------------------------------------------------------------

    def get_default_template_dir(self) -> str:
        """获取上次使用的模板库目录（默认目录）"""
        v = self._read(self.KEY_TEMPLATE_DIR, "")
        return v if isinstance(v, str) else ""

    def set_default_template_dir(self, path: str):
        """设置默认模板库目录（一般在用户打开目录时保存）"""
        if not path:
            return
        self._write(self.KEY_TEMPLATE_DIR, os.path.abspath(path))

    # ------------------------------------------------------------
    # 公共 API：模板库历史记录
    # ------------------------------------------------------------

    def get_template_history(self) -> list[TemplateDirHistory]:
        """获取模板库历史记录（按 last_used_at 降序，最多 history_max 条）"""
        return list(self._history)

    def add_template_history(self, path: str, total_files: int = 0) -> TemplateDirHistory:
        """添加或更新一条模板库历史记录（移到最前并保存）"""
        if not path:
            # 不要记录空值
            dummy = TemplateDirHistory.from_path("")
            return dummy
        apath = os.path.abspath(path)
        now = time.time()

        # 查找已有
        existing = None
        for i, h in enumerate(self._history):
            if os.path.normcase(h.path) == os.path.normcase(apath):
                existing = i
                break

        if existing is not None:
            item = self._history.pop(existing)
            item.last_used_at = now
            if total_files:
                item.total_files = total_files
            if not item.display_name:
                item.display_name = os.path.basename(apath.rstrip(os.sep)) or apath
        else:
            item = TemplateDirHistory.from_path(apath)
            item.total_files = total_files

        # 最前面
        self._history.insert(0, item)

        # 只保留最多 history_max 条；同时过滤掉不存在的路径（可选：保留以展示）
        trimmed = []
        seen = set()
        for h in self._history:
            key = os.path.normcase(h.path)
            if key in seen:
                continue
            seen.add(key)
            trimmed.append(h)
            if len(trimmed) >= self._history_max:
                break
        self._history = trimmed

        self._save_history()
        # 同时作为默认目录保存
        self.set_default_template_dir(apath)
        return item

    def remove_template_history(self, path: str) -> bool:
        """从历史记录中移除指定目录。返回是否实际删除。"""
        if not path:
            return False
        apath = os.path.normcase(os.path.abspath(path))
        new_list = [h for h in self._history if os.path.normcase(h.path) != apath]
        changed = len(new_list) != len(self._history)
        if changed:
            self._history = new_list
            self._save_history()
        return changed

    def clear_template_history(self):
        self._history = []
        self._save_history()

    # ------------------------------------------------------------
    # 公共 API：目标文件名历史记录（按日分组，保留 3 天）
    # 按 source（'cropper' / 'pool'）物理隔离存储，互不干扰
    # ------------------------------------------------------------

    # 支持的数据源常量（用于存储键命名与 UI 显示）
    TARGET_SRC_CROPPER = "cropper"
    TARGET_SRC_POOL = "pool"
    TARGET_SRC_LABEL = {
        TARGET_SRC_CROPPER: "圆角裁剪工具",
        TARGET_SRC_POOL: "水池设计器",
    }

    def _target_name_key(self, source: str) -> str:
        """按 source 生成独立的存储键，实现物理隔离"""
        src = (source or "").strip()
        if src not in (self.TARGET_SRC_CROPPER, self.TARGET_SRC_POOL):
            src = self.TARGET_SRC_CROPPER
        return f"{self.KEY_TARGET_NAME_HISTORY}/{src}"

    def _load_target_name_history(self, source: str) -> dict:
        """读取指定 source 的目标文件名历史：{date_str: [{"name","timestamp"}, ...]}"""
        key = self._target_name_key(source)
        raw = self._read(key, None)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                raw = {}
        if not isinstance(raw, dict):
            return {}
        result: dict = {}
        for d, items in raw.items():
            if not isinstance(items, list):
                continue
            cleaned = []
            for r in items:
                if not isinstance(r, dict):
                    continue
                name = r.get("name", "")
                if not isinstance(name, str) or not name:
                    continue
                cleaned.append({
                    "name": name,
                    "timestamp": float(r.get("timestamp", 0) or 0),
                })
            if cleaned:
                result[d] = cleaned
        return result

    def _save_target_name_history(self, source: str, data: dict):
        key = self._target_name_key(source)
        if self._qs is not None:
            self._write(key, json.dumps(data, ensure_ascii=False))
        else:
            self._write(key, data)

    @staticmethod
    def _today_iso() -> str:
        return date.today().isoformat()

    def add_target_name_history(self, name: str, source: str = TARGET_SRC_CROPPER) -> TargetNameHistory:
        """添加一条目标文件名历史记录。

        - 按 source 独立存储，物理隔离
        - 同日同名记录去重（已存在则移到最前，更新 timestamp）
        - 保留近 N 天（含今天），过期自动清理
        - 每天最多 TARGET_NAME_HISTORY_PER_DAY 条，超出截断尾部
        """
        name = (name or "").strip()
        if not name:
            return TargetNameHistory.create("", source)
        today_str = self._today_iso()
        now = time.time()
        all_records = self._load_target_name_history(source)

        today_list = [r for r in all_records.get(today_str, []) if r.get("name") != name]
        today_list.insert(0, {"name": name, "timestamp": now})
        if len(today_list) > self.TARGET_NAME_HISTORY_PER_DAY:
            today_list = today_list[:self.TARGET_NAME_HISTORY_PER_DAY]
        all_records[today_str] = today_list

        # 清理过期记录（保留含今天在内的最近 N 天）
        cutoff = date.today() - timedelta(days=self.TARGET_NAME_HISTORY_DAYS - 1)
        cutoff_str = cutoff.isoformat()
        for d in list(all_records.keys()):
            if d < cutoff_str:
                del all_records[d]

        self._save_target_name_history(source, all_records)
        return TargetNameHistory(name=name, timestamp=now, source=source)

    def get_target_name_history(self, source: str, days: int = 0) -> dict:
        """获取指定 source 的目标文件名历史（按日分组）。

        参数 source 必传：'cropper' 或 'pool'。
        参数 days 默认 0 表示使用 TARGET_NAME_HISTORY_DAYS（3 天，含今天）。
        返回：{date_str: [{"name","timestamp"}, ...]}
        日期键为 ISO 格式 (YYYY-MM-DD)，按日期降序，每日列表按时间降序。
        """
        n = days if days > 0 else self.TARGET_NAME_HISTORY_DAYS
        cutoff_str = (date.today() - timedelta(days=n - 1)).isoformat()
        all_records = self._load_target_name_history(source)
        filtered = {d: r for d, r in all_records.items() if d >= cutoff_str}
        return {d: filtered[d] for d in sorted(filtered.keys(), reverse=True)}

    def clear_target_name_history(self, source: str):
        """清空指定 source 的目标文件名历史"""
        self._save_target_name_history(source, {})

    def clear_target_name_history_by_date(self, source: str, date_str: str):
        """清空指定 source 指定日期的目标文件名历史"""
        all_records = self._load_target_name_history(source)
        if date_str in all_records:
            del all_records[date_str]
            self._save_target_name_history(source, all_records)


# ---------------------------------------------------------------------------
# 便捷：全局单例
# ---------------------------------------------------------------------------

_app_settings_singleton: Optional[AppSettings] = None


def get_app_settings() -> AppSettings:
    """获取全局应用设置单例"""
    global _app_settings_singleton
    if _app_settings_singleton is None:
        _app_settings_singleton = AppSettings()
    return _app_settings_singleton
