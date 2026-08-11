# -*- coding: utf-8 -*-
"""
一次性脚本：给 scripts/diagnose 和 scripts/verify 下的 .py 文件：
1) 在 docstring / coding / shebang 之后插入 PROJECT_ROOT auto-inject 代码段
2) 将所有 D:\SmartShapeCrop\... 绝对路径替换为基于 _D (PROJECT_ROOT) 的 os.path.join 调用
不会重复处理已注入的文件。
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

ROOT = Path(r"d:\SmartShapeCrop")
TARGETS = [ROOT / "scripts" / "diagnose", ROOT / "scripts" / "verify"]

INJECT_MARK = "PROJECT_ROOT auto-inject"

PRELUDE = '''
# ============================================================
# PROJECT_ROOT auto-inject (added by test-dir cleanup 2026-08-11)
# 脚本从 scripts/ 子目录运行时仍能正确定位 core/, psd_demo/, Test/output 等
import sys as _sys
import os as _os
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))
_D = str(_PROJECT_ROOT)
# ============================================================
'''

DRIVE_RE = re.compile(r'(?i)[rR]?"[Dd]:\\SmartShapeCrop\\([^"]*)"')


def replace_drive_paths(text: str) -> str:
    def _sub(m: re.Match) -> str:
        sub = m.group(1).replace("\\", "/")
        parts = [p for p in sub.split("/") if p]
        if not parts:
            return "_D"
        parts_repr = ", ".join(repr(p) for p in parts)
        return f"_os.path.join(_D, {parts_repr})"
    return DRIVE_RE.sub(_sub, text)


def find_insert_pos(text: str) -> int:
    """找到应该插入 preamble 的字节位置：跳过 shebang/coding/docstring/空行。"""
    i = 0
    n = len(text)

    # 逐行过：shebang, coding, 空行
    while i < n:
        j = text.find("\n", i)
        line = text[i:j] if j != -1 else text[i:]
        stripped = line.lstrip()
        if stripped.startswith("#!") or (stripped.startswith("#") and "coding" in stripped.lower()) or stripped == "":
            i = j + 1 if j != -1 else n
            continue
        break

    # 处理三引号 docstring（' 或 "）
    for quote in ('"""', "'''"):
        if text[i:].startswith(quote):
            end = text.find(quote, i + len(quote))
            if end != -1:
                i = end + len(quote)
                # docstring 之后如果紧跟着换行跳过
                while i < n and text[i] in "\r\n":
                    i += 1
            break
    return i


def process_file(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    if INJECT_MARK in raw:
        return "skip-already-inject"

    # 移除原文件中显式的 sys.path.insert(0, r"D:\SmartShapeCrop")
    raw = re.sub(
        r'(?m)^\s*sys\.path\.insert\(0,\s*[rR]?"[Dd]:\\SmartShapeCrop\\?"\s*\)\s*$',
        "# (removed by cleanup: see PROJECT_ROOT auto-inject above)",
        raw,
    )

    # 替换路径
    raw = replace_drive_paths(raw)

    # 注入 preamble
    pos = find_insert_pos(raw)
    new_text = raw[:pos] + PRELUDE + ("\n" if pos < len(raw) and not raw[pos:].startswith("\n") else "") + raw[pos:]

    path.write_text(new_text, encoding="utf-8")
    return "ok"


def main() -> int:
    files = []
    for d in TARGETS:
        files.extend(sorted(d.glob("*.py")))
    ok = skip = 0
    for f in files:
        res = process_file(f)
        if res == "ok":
            ok += 1
            print(f"  OK    {f.relative_to(ROOT)}")
        else:
            skip += 1
            print(f"  SKIP  {f.relative_to(ROOT)}  ({res})")
    print(f"\nProcessed: {ok} modified, {skip} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
