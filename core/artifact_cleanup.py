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
        # 收集所有"可清理"文件（跳过保护名单）
        candidates = [
            p for p in base.rglob('*')
            if p.is_file() and not _is_protected(p)
        ]
        # 空目录顺手清掉（dry_run 除外）
        empties = [d for d in base.rglob('*') if d.is_dir()]

        # 第一步：按保留期删除
        expired = [p for p in candidates if p.stat().st_mtime < cutoff]
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
        remaining = [p for p in candidates if p not in set(expired)]
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
