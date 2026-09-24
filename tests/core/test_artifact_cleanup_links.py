"""P2-7 回归测试：``artifact_cleanup`` 不得跟随链接节点越界清理。

背景（产品审查报告 P2-7）：原实现用 ``Path.rglob('*')`` 收集调试产物。
Python 3.13 的 ``**`` 只对「符号链接」停止递归，而 Windows **junction**（目录联接）
不是符号链接 —— ``os.path.islink()`` 对它返回 False、``Path.is_dir()`` 返回 True，
于是 ``**`` 照常进入其目标目录，随后的 ``os.remove()`` 会删掉调试目录**之外**的
真实文件（实测于 Python 3.13.14 / Windows 11）。

本模块验证四件事：

1. 链接节点（符号链接 / junction）既不递归、也不被纳入清理范围；
2. 链接目标内的文件在任何模式下都不会被删除；
3. 链接节点自身（用户建立的联接）不会被 ``rmdir`` 破坏；
4. 普通目录树上的收集结果与 ``Path.rglob('*')`` **逐条一致**（零行为回归）。
"""
from __future__ import annotations

import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from core import artifact_cleanup as ac

_ON_WINDOWS = os.name == 'nt'


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _make_junction(link: Path, target: Path) -> bool:
    """用 ``mklink /J`` 创建目录联接（junction 不需要管理员权限）。"""
    if not _ON_WINDOWS:
        return False
    r = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                       capture_output=True)
    return r.returncode == 0


@contextmanager
def _junction(link: Path, target: Path):
    """创建 junction，退出时只删除联接本身（不触碰目标）。"""
    if not _make_junction(link, target):
        pytest.skip('无法创建 junction（非 Windows 或文件系统不支持）')
    try:
        yield link
    finally:
        if os.path.lexists(link):
            try:
                os.rmdir(link)  # 仅断开联接
            except OSError:
                pass


@contextmanager
def _symlink_dir(link: Path, target: Path):
    """创建目录符号链接，退出时只删除链接本身。"""
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # 无权限 / 平台不支持
        pytest.skip(f'无法创建目录符号链接：{exc}')
    try:
        yield link
    finally:
        if os.path.lexists(link):
            try:
                os.rmdir(link)
            except OSError:
                pass


def _age(path: Path, days: int = 400) -> None:
    """把文件 mtime 改到 N 天前，使其落入「保留期外」。"""
    old = time.time() - days * 86400
    os.utime(path, (old, old))


# ---------------------------------------------------------------------------
# 1. 等价性：普通目录树上与 Path.rglob('*') 逐条一致
# ---------------------------------------------------------------------------

def test_iter_tree_matches_rglob_on_plain_tree(tmp_path):
    """无链接时，_iter_tree 的收集结果与顺序必须与 Path.rglob('*') 完全相同。

    顺序很重要：下游 ``remaining.sort(key=mtime)`` 是稳定排序，mtime 相同的
    文件依赖输入顺序决定谁先被删；顺序漂移即为行为变更。
    """
    base = tmp_path / 'base'
    (base / 'd1' / 'd3').mkdir(parents=True)
    (base / 'd2').mkdir()
    (base / 'empty_dir').mkdir()
    (base / 'f1.txt').write_text('1', encoding='utf-8')
    (base / 'd1' / 'f2.txt').write_text('2', encoding='utf-8')
    (base / 'd1' / 'd3' / 'f3.txt').write_text('3', encoding='utf-8')
    (base / 'd2' / 'f4.txt').write_text('4', encoding='utf-8')

    rglob_all = list(base.rglob('*'))
    files, dirs = ac._iter_tree(base)

    assert files == [p for p in rglob_all if p.is_file()]
    assert dirs == [p for p in rglob_all if p.is_dir()]


def test_is_link_node_false_for_plain_entries(tmp_path):
    """普通文件 / 目录绝不能被误判为链接节点（防止过度剪枝）。"""
    base = tmp_path / 'base'
    (base / 'd').mkdir(parents=True)
    (base / 'f.txt').write_text('x', encoding='utf-8')

    with os.scandir(base) as it:
        verdict = {e.name: ac._is_link_node(e) for e in it}

    assert verdict == {'f.txt': False, 'd': False}


# ---------------------------------------------------------------------------
# 2. junction：不递归、不删除目标内容
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _ON_WINDOWS, reason='junction 仅 Windows 支持')
def test_iter_tree_does_not_enter_junction(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'keep.txt').write_text('data', encoding='utf-8')

    base = tmp_path / 'base'
    base.mkdir()
    (base / 'real.txt').write_text('r', encoding='utf-8')

    with _junction(base / 'junc', outside):
        files, dirs = ac._iter_tree(base)

    assert files == [base / 'real.txt'], 'junction 目标内的文件被列入清理候选'
    assert dirs == [], 'junction 联接本身被当作普通目录列入'


@pytest.mark.skipif(not _ON_WINDOWS, reason='junction 仅 Windows 支持')
def test_cleanup_never_deletes_through_junction(tmp_path, monkeypatch):
    """端到端：过期的真实文件照常清理，但 junction 目标内的文件分毫不动。"""
    outside = tmp_path / 'outside'
    outside.mkdir()
    victim = outside / 'keep.txt'
    victim.write_text('data', encoding='utf-8')
    _age(victim)

    base = tmp_path / 'base'
    base.mkdir()
    stale = base / 'stale.txt'
    stale.write_text('s', encoding='utf-8')
    _age(stale)

    monkeypatch.setattr(ac, 'DEBUG_ARTIFACT_DIRS', [base])

    with _junction(base / 'junc', outside):
        removed = ac.cleanup_debug_artifacts(max_age_days=30, max_files=200)

    # 功能未被削弱：base 内的过期产物仍被正常清理
    assert not stale.exists()
    assert str(stale) in removed
    # 且绝不越界
    assert victim.exists(), 'junction 目标内的真实文件被误删'
    assert str(victim) not in removed


@pytest.mark.skipif(not _ON_WINDOWS, reason='junction 仅 Windows 支持')
def test_cleanup_keeps_junction_node_itself(tmp_path, monkeypatch):
    """清理工具不得把用户建立的目录联接本身删掉。"""
    outside = tmp_path / 'outside'
    outside.mkdir()
    base = tmp_path / 'base'
    base.mkdir()

    monkeypatch.setattr(ac, 'DEBUG_ARTIFACT_DIRS', [base])

    with _junction(base / 'junc', outside) as link:
        ac.cleanup_debug_artifacts(max_age_days=30, max_files=200)

        assert os.path.lexists(link), 'junction 联接被清理工具删除'
        if hasattr(os.path, 'isjunction'):
            assert os.path.isjunction(link), 'junction 已不再指向原目标'


@pytest.mark.skipif(not _ON_WINDOWS, reason='junction 仅 Windows 支持')
def test_dry_run_also_ignores_junction(tmp_path, monkeypatch):
    """dry-run 预览同样不应把 junction 目标内的文件算作「将删除」。"""
    outside = tmp_path / 'outside'
    outside.mkdir()
    victim = outside / 'keep.txt'
    victim.write_text('data', encoding='utf-8')
    _age(victim)

    base = tmp_path / 'base'
    base.mkdir()

    monkeypatch.setattr(ac, 'DEBUG_ARTIFACT_DIRS', [base])

    with _junction(base / 'junc', outside):
        removed = ac.cleanup_debug_artifacts(
            max_age_days=30, max_files=200, dry_run=True)

    assert removed == []
    assert victim.exists()


# ---------------------------------------------------------------------------
# 3. 符号链接：节点同样被跳过，目标存活
# ---------------------------------------------------------------------------

def test_symlink_dir_node_is_skipped_and_target_alive(tmp_path):
    """目录符号链接：不递归、不纳入清理，目标文件存活。

    （Python 3.9+ 的 ``**`` 本就不跟随符号链接，此项为回归护栏，
    顺带验证我们不把链接节点当作普通目录交给 ``rmdir``。）
    """
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'keep.txt').write_text('data', encoding='utf-8')

    base = tmp_path / 'base'
    base.mkdir()

    with _symlink_dir(base / 'link', outside):
        files, dirs = ac._iter_tree(base)

        assert files == []
        assert dirs == []
        assert (outside / 'keep.txt').exists()


def test_symlink_file_node_is_skipped(tmp_path):
    """文件符号链接同样不再被列入候选（避免删除用户建立的链接）。"""
    outside = tmp_path / 'outside'
    outside.mkdir()
    target = outside / 'real.txt'
    target.write_text('data', encoding='utf-8')

    base = tmp_path / 'base'
    base.mkdir()
    link = base / 'link.txt'
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'无法创建文件符号链接：{exc}')

    try:
        files, dirs = ac._iter_tree(base)
        assert files == []
        assert dirs == []
    finally:
        if os.path.lexists(link):
            os.remove(link)

    assert target.exists()
