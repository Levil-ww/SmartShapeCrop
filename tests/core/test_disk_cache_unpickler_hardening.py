"""
测试：DiskCache pickle 反序列化加固（P0-4）

背景
----
`services/parser/template_matcher.py` 的 `DiskCache.load` 原先直接调用
`pickle.load`。缓存路径 `~/.smartshapecrop/caches/<名>_<md5>.cache.pickle`
可预测，任意本地进程均可替换该文件；而 pickle 在归还数据**之前**即可执行代码
（`__reduce__` → `os.system` / `subprocess`），构成本地反序列化 RCE 面。

修复
----
改用 `_RestrictedUnpickler`，只放行惰性内置类型。缓存载荷实测为纯内置类型
（dict/list/str/int/float/bool），正常读写不需要实例化任何自定义类。

覆盖
----
  - 恶意 pickle 被拒绝，且**不产生任何副作用**（含对照组证明载荷确实可执行）
  - 合法缓存 save → load 完全保真（证明修复未改变既有功能）
  - 恶意 pickle + 合法 JSON 并存时优雅回退（既有 fallback 路径未被破坏）
  - 白名单边界：放行惰性内置、拒绝可执行对象
"""
import io
import json
import os
import pickle

import pytest

import core  # noqa: F401  先加载 core，遵循既有 compat shim 导入顺序
from services.parser.template_matcher import (
    DiskCache,
    _CACHE_SCHEMA_VERSION,
    _RestrictedUnpickler,
)


class _CommandPayload:
    """__reduce__ 即执行 os.system 的恶意载荷。"""

    def __init__(self, sentinel_path: str):
        self._sentinel = sentinel_path

    def __reduce__(self):
        return (os.system, (f'echo PWNED > "{self._sentinel}"',))


def _valid_payload(template_dir: str) -> dict:
    """一份结构合法（schema/目录均匹配）的缓存载荷。"""
    return {
        "schema_version": _CACHE_SCHEMA_VERSION,
        "template_dir": template_dir,
        "subdir_mtimes": {"sub": 1.5},
        "entries": {},
        "idx_pattern": {"p": ["a.jpg", "b.jpg"]},
        "idx_layout": {"竖版": ["a.jpg"]},
        "idx_circular": {"True": ["c.jpg"], "False": ["d.jpg"]},
        "idx_ratio": {"1:1": ["a.jpg"]},
        "last_scan_at": 123.0,
        "last_full_walk_at": 456.0,
        "dir_mtime": 9.0,
    }


class TestRestrictedUnpickler:
    """受限 Unpickler 的白名单边界。"""

    @pytest.mark.parametrize("module,name", [
        ("os", "system"),
        ("subprocess", "Popen"),
        ("builtins", "eval"),
        ("builtins", "exec"),
        ("builtins", "__import__"),
        ("posixpath", "system"),
    ])
    def test_rejects_executable_global(self, module, name):
        u = _RestrictedUnpickler(io.BytesIO(b""))
        with pytest.raises(pickle.UnpicklingError):
            u.find_class(module, name)

    @pytest.mark.parametrize("module,name", [
        ("builtins", "set"),
        ("builtins", "frozenset"),
        ("collections", "OrderedDict"),
    ])
    def test_allows_inert_builtin_container(self, module, name):
        u = _RestrictedUnpickler(io.BytesIO(b""))
        assert u.find_class(module, name) is not None

    def test_loads_all_builtin_payload(self):
        """纯内置类型载荷（真实缓存的实际形态）必须正常加载。"""
        payload = {"a": [1, 2.5, "x", True, None], "b": {"c": (1, 2)}}
        raw = pickle.dumps(payload, protocol=4)
        assert _RestrictedUnpickler(io.BytesIO(raw)).load() == payload


class TestDiskCacheLoadHardening:
    """DiskCache.load 的加固表现。"""

    def test_malicious_pickle_rejected_without_side_effect(self, tmp_path):
        """恶意 pickle 被拒绝，且不执行其中代码。

        自带对照组（裸 pickle.load）证明该载荷确实具备执行能力，
        避免"断言恒真"的假测试。
        """
        sentinel = str(tmp_path / "pwned.txt")
        base = str(tmp_path / "evil.cache")
        payload = _valid_payload(str(tmp_path))
        payload["evil"] = _CommandPayload(sentinel)
        with open(base + ".pickle", "wb") as f:
            pickle.dump(payload, f, protocol=4)

        # 对照组：裸 pickle.load 会执行（证明载荷可执行、测试非空转）
        with open(base + ".pickle", "rb") as f:
            pickle.load(f)
        assert os.path.exists(sentinel), "对照组未执行 → 该载荷不具备执行能力，本测试无意义"
        os.remove(sentinel)

        # 修复后：受限加载
        assert DiskCache.load(base, str(tmp_path)) is None
        assert not os.path.exists(sentinel), "受限加载仍执行了载荷代码"

    def test_legitimate_cache_round_trip_unchanged(self, tmp_path):
        """合法缓存 save → load 全字段保真（修复不得削减既有功能）。"""
        base = str(tmp_path / "rt.cache")
        src = DiskCache(
            template_dir=str(tmp_path),
            subdir_mtimes={"sub": 1.5},
            last_scan_at=123.0,
            last_full_walk_at=456.0,
            dir_mtime=9.0,
            idx_pattern={"p": ["a.jpg", "b.jpg"]},
            idx_layout={"竖版": ["a.jpg"]},
            idx_ratio={"1:1": ["a.jpg"]},
        )
        src.save(base)
        assert os.path.exists(base + ".pickle"), "pickle 未写出"

        got = DiskCache.load(base, str(tmp_path))
        assert got is not None
        assert got.schema_version == _CACHE_SCHEMA_VERSION
        assert got.template_dir == str(tmp_path)
        assert got.subdir_mtimes == {"sub": 1.5}
        assert got.idx_pattern == {"p": ["a.jpg", "b.jpg"]}
        assert got.idx_layout == {"竖版": ["a.jpg"]}
        assert got.idx_ratio == {"1:1": ["a.jpg"]}
        assert got.last_scan_at == 123.0
        assert got.last_full_walk_at == 456.0
        assert got.dir_mtime == 9.0

    def test_falls_back_to_json_when_pickle_rejected(self, tmp_path):
        """pickle 被拒时沿用既有 JSON fallback（不因加固而丢失可用缓存）。"""
        base = str(tmp_path / "fb.cache")
        with open(base + ".json", "w", encoding="utf-8") as f:
            json.dump(_valid_payload(str(tmp_path)), f, ensure_ascii=False)

        sentinel = str(tmp_path / "pwned2.txt")
        with open(base + ".pickle", "wb") as f:
            pickle.dump({"evil": _CommandPayload(sentinel)}, f, protocol=4)

        got = DiskCache.load(base, str(tmp_path))
        assert got is not None, "未回退到 JSON"
        assert got.idx_pattern == {"p": ["a.jpg", "b.jpg"]}
        assert not os.path.exists(sentinel)

    def test_valid_pickle_not_silently_dropped(self, tmp_path):
        """合法 pickle 优先于 JSON（顺序语义未被改动）。

        pickle 与 JSON 内容不同时，应以 pickle 为准 —— 证明加固没有把 pickle
        分支变成"永远失败"。
        """
        base = str(tmp_path / "prio.cache")
        pkl_payload = _valid_payload(str(tmp_path))
        pkl_payload["idx_pattern"] = {"from": ["pickle.jpg"]}
        with open(base + ".pickle", "wb") as f:
            pickle.dump(pkl_payload, f, protocol=4)

        json_payload = _valid_payload(str(tmp_path))
        json_payload["idx_pattern"] = {"from": ["json.jpg"]}
        with open(base + ".json", "w", encoding="utf-8") as f:
            json.dump(json_payload, f, ensure_ascii=False)

        got = DiskCache.load(base, str(tmp_path))
        assert got is not None
        assert got.idx_pattern == {"from": ["pickle.jpg"]}, "pickle 分支未生效"

    def test_schema_version_mismatch_still_returns_none(self, tmp_path):
        """既有 schema_version 校验未被本次改动影响。"""
        base = str(tmp_path / "old.cache")
        payload = _valid_payload(str(tmp_path))
        payload["schema_version"] = _CACHE_SCHEMA_VERSION - 1
        with open(base + ".pickle", "wb") as f:
            pickle.dump(payload, f, protocol=4)
        assert DiskCache.load(base, str(tmp_path)) is None

    def test_template_dir_mismatch_still_returns_none(self, tmp_path):
        """既有 template_dir 校验未被本次改动影响。"""
        base = str(tmp_path / "dir.cache")
        payload = _valid_payload(str(tmp_path / "other"))
        with open(base + ".pickle", "wb") as f:
            pickle.dump(payload, f, protocol=4)
        assert DiskCache.load(base, str(tmp_path)) is None


class TestRealWorldCacheCompatibility:
    """存量缓存兼容性：真实缓存载荷形态必须仍可加载。"""

    def test_real_cache_file_shapes_load(self, tmp_path):
        """按真实缓存的实际键/类型构造载荷，受限 Unpickler 必须放行。"""
        payload = _valid_payload(str(tmp_path))
        payload["entries"] = {
            "a.jpg": {
                "path": "a.jpg", "filename": "a.jpg",
                "_product_name": "P", "_layout": "竖版",
                "_width_cm": 54.0, "_height_cm": 41.2,
                "_material": "简织", "_pattern_name": "双面格",
                "_pattern_key": "双面格", "_shape_keywords_serialized": "",
                "_file_mtime": 1.0, "_ratio_bucket": "1:1", "_has_corners": False,
                "is_circular": False, "is_custom": False,
            }
        }
        base = str(tmp_path / "real.cache")
        with open(base + ".pickle", "wb") as f:
            pickle.dump(payload, f, protocol=4)

        got = DiskCache.load(base, str(tmp_path))
        assert got is not None
        entry = got.entries["a.jpg"]
        assert entry.filename == "a.jpg"
        assert entry._width_cm == 54.0
        assert entry.is_custom is False
