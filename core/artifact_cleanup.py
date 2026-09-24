"""
core/artifact_cleanup.py
调试产物目录治理（F19 修复）。

背景（2026-08 分析报告 F19）：
  - logs/ 与 debug_output/ 中的草图诊断截图、调试中间图持续累积
    （当时 logs/ 已 125 个文件、debug_output/ 76 个），无任何清理策略；
  - ProductSummary/、dist/、images/ 等属于"交付物/资源"，本模块绝不触碰。

策略（双保险，均只作用于白名单调试目录）：
  1. 保留期：超过 max_age_days 天的调试产物直接删除；
  2. 数量上限：若清理后仍超过 max_files 个，从最旧的开始删除直至达标。

保护名单（即使在调试目录内也不删除）：
  - smartshapecrop.log*（RotatingFileHandler 自身已做滚动管理）
  - crash.log（崩溃现场，排障需要）

使用方式：
  - 程序内部：main.py 启动时调用 cleanup_debug_artifacts()（异常静默，不阻断启动）；
  - 命令行：python core/artifact_cleanup.py [--dry-run] [--days N] [--max-files N]
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)

# 项目根目录（core/ 的上一级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 只治理这两个"调试产物"目录（递归）；交付物目录（ProductSummary/dist/images）永不进入
DEBUG_ARTIFACT_DIRS = [
    _PROJECT_ROOT / 'debug_output',
    _PROJECT_ROOT / 'logs',
]

# 即使位于调试目录内也不删除的文件名（fnmatch 前缀匹配用）
PROTECTED_PREFIXES = ('smartshapecrop.log', 'crash.log')


def _is_protected(path: Path) -> bool:
    """判断是否属于保护名单（滚动日志与崩溃现场）。"""
    name = path.name
    return any(name.startswith(p) for p in PROTECTED_PREFIXES)


# Windows 重解析点标签（Python < 3.12 无 os.path.isjunction 时的兜底判据）
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
_IO_REPARSE_TAG_SYMLINK = 0xA000000C


def _is_link_node(entry: os.DirEntry) -> bool:
    """判断 scandir 条目是否为「链接节点」（符号链接 / Windows junction）。

    [Fix 2026-09-24 P2-7] junction（目录联接）与符号链接不同：``os.path.islink()``
    对 junction 返回 **False**，因此必须额外用 ``os.path.isjunction()``（Python 3.12+）
    识别；Python < 3.12 时退回按 ``st_reparse_tag`` 判定。

    无法判定时按链接处理（宁可少清理，也不越界删除）。
    """
    try:
        if entry.is_symlink():
            return True
    except OSError:
        return True

    isjunction = getattr(os.path, 'isjunction', None)
    if isjunction is not None:
        try:
            return bool(isjunction(entry.path))
        except OSError:
            return True

    try:
        st = os.lstat(entry.path)
    except OSError:
        return True
    return getattr(st, 'st_reparse_tag', 0) in (
        _IO_REPARSE_TAG_MOUNT_POINT, _IO_REPARSE_TAG_SYMLINK)


def _iter_tree(base: Path) -> tuple[list[Path], list[Path]]:
    """遍历 base 下的真实文件与目录（**不跟随任何链接节点**），返回 (files, dirs)。

    [Fix 2026-09-24 P2-7] 原实现用 ``base.rglob('*')``，会连同 **Windows junction**
    的目标目录一起列出，随后的 ``os.remove()`` 便删掉了调试目录之外的**真实文件**。
    实测（Python 3.13.14 / Windows）：

        场景                rglob('*') 列出            os.remove 后果
        符号链接（文件）     link.txt                   只删链接，目标存活
        符号链接（目录）     不进入（仅列出链接本身）    —
        junction（目录）     junc, junc\\keep.txt        **删掉目标内的真实文件**

    根因：``**`` 只对「符号链接」停止递归，而 junction **不是**符号链接 ——
    ``os.path.islink()`` 返回 False、``os.path.is_dir()`` 返回 True，于是照常进入。
    ``os.walk(followlinks=False)`` 同样不拦 junction。

    故这里自写遍历（**逐层广度优先，与 ``Path.rglob`` 的产出顺序逐条一致** ——
    已由 ``tests/core/test_artifact_cleanup_links.py`` 断言锁定；顺序须保持一致，
    否则下游 ``sort(key=mtime)`` 稳定排序的 tie-break 会漂移），对每个条目先判
    「是否链接节点」，是则**既不递归、也不纳入治理范围**：

    - 递归进去 → 会越界删除目标目录内的真实文件；
    - 纳入 dirs → ``d.rmdir()`` 会删掉用户建立的联接/符号链接本身。

    链接指向的内容属于本目录树之外，调试产物治理不应触碰。
    """
    files: list[Path] = []
    dirs: list[Path] = []
    queue: deque[str] = deque([str(base)])
    while queue:
        cur = queue.popleft()
        try:
            with os.scandir(cur) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            try:
                if _is_link_node(entry):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    dirs.append(Path(entry.path))
                    queue.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    files.append(Path(entry.path))
            except OSError:
                continue
    return files, dirs


def cleanup_debug_artifacts(
    max_age_days: int = 30,
    max_files: int = 200,
    dry_run: bool = False,
) -> list[str]:
    """
    清理调试产物目录，返回被删除（或 dry_run 下将被删除）的文件路径列表。

    Args:
        max_age_days: 保留期（天）。修改时间早于该期限的调试产物被删除。
        max_files: 单个调试目录的数量上限（递归统计），超限删最旧。
        dry_run: True 时只计算不删除，用于预览。
    """
    cutoff = time.time() - max_age_days * 86400
    removed: list[str] = []

    for base in DEBUG_ARTIFACT_DIRS:
        if not base.is_dir():
            continue
        # [Fix 2026-09-24 P2-7] 改用剪枝遍历（原 base.rglob('*') 会跟随 junction
        # 越界删除目标目录内容，详见 _iter_tree 文档）
        tree_files, tree_dirs = _iter_tree(base)
        # 收集所有"可清理"文件（跳过保护名单）
        candidates = [p for p in tree_files if not _is_protected(p)]
        # 空目录顺手清掉（dry_run 除外）
        empties = list(tree_dirs)

        # 第一步：按保留期删除
        expired = [p for p in candidates if p.stat().st_mtime < cutoff]
        # [Fix 2026-09-24 P2-6] set 只构建一次：原写法在推导式内调用 set(expired)，
        # 每次迭代都重建一次，整体 O(len(candidates) × len(expired))；提到循环外后为 O(n)。
        expired_set = set(expired)
        for p in sorted(expired):
            if dry_run:
                removed.append(f'[dry-run] {p}')
            else:
                try:
                    os.remove(p)
                    removed.append(str(p))
                except OSError as e:
                    logger.debug(f"[artifact_cleanup] 删除失败 {p}: {e}")

        # 第二步：数量上限（从最旧开始删）
        remaining = [p for p in candidates if p not in expired_set]
        remaining.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        overflow = remaining[max_files:]
        for p in sorted(overflow, key=lambda p: p.stat().st_mtime):
            if dry_run:
                removed.append(f'[dry-run] {p}')
            else:
                try:
                    os.remove(p)
                    removed.append(str(p))
                except OSError as e:
                    logger.debug(f"[artifact_cleanup] 删除失败 {p}: {e}")

        # 清理空目录（真实模式下）
        if not dry_run:
            for d in sorted(empties, key=lambda p: len(p.parts), reverse=True):
                try:
                    d.rmdir()  # 只删空目录，非空会抛 OSError 并被跳过
                except OSError:
                    pass

    if removed:
        action = '计划删除' if dry_run else '已清理'
        logger.info(
            f"[artifact_cleanup] {action} {len(removed)} 个调试产物"
            f"（保留期 {max_age_days} 天 / 单目录上限 {max_files} 个）"
        )
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description='调试产物清理工具（F19）')
    parser.add_argument('--dry-run', action='store_true', help='只预览不删除')
    parser.add_argument('--days', type=int, default=30, help='保留期（天），默认 30')
    parser.add_argument('--max-files', type=int, default=200,
                        help='单目录调试产物数量上限，默认 200')
    args = parser.parse_args()
    removed = cleanup_debug_artifacts(
        max_age_days=args.days, max_files=args.max_files, dry_run=args.dry_run)
    if not removed:
        print('无需清理：调试产物均在保留期内且未超上限。')
    else:
        tag = '将删除' if args.dry_run else '已删除'
        print(f'{tag} {len(removed)} 个文件：')
        for p in removed[:50]:
            print(f'  {p}')
        if len(removed) > 50:
            print(f'  ...（其余 {len(removed) - 50} 个略）')


if __name__ == '__main__':
    main()
