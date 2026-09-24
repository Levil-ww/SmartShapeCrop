"""
tests/core/test_config_tesseract_probe_hardened.py
[Fix 2026-09-24 P1-7] ``PathResolver`` 探测 Tesseract 时不再用 ``os.popen``。

缺陷本质
--------
``core/config.py::PathResolver._do_find_tesseract`` 在「系统 PATH」分支里用

    result = os.popen('tesseract --list-langs 2>&1').read()

探测语言包。三个问题：
  1. ``os.popen`` **起 shell**（此处参数是字面量、无注入面，但属不必要的 shell 依赖）；
  2. 返回的管道对象**从不 close**（文件句柄泄漏，每次调用泄漏一个）；
  3. 依赖 shell 的 PATH 二次解析，而调用点刚刚已用 ``shutil.which`` 解析出绝对路径。

修复
----
改为 ``subprocess.run([path_exe, '--list-langs'], stdout=PIPE, stderr=STDOUT, ...)``，
复用已解析的 ``path_exe``，不启用 shell，stderr 合并进 stdout 以保持原语义。
**容错语义不变**：任何异常一律忽略，``tessdata`` 维持 ``None``。

本文件锁住：源码不再出现 ``os.popen``，以及四种探测结果与旧实现一致。
"""
import ast
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from core import config as config_mod
from core.config import PathResolver

CONFIG_PY = PROJECT_ROOT / 'core' / 'config.py'
FAKE_EXE = r'C:\fake\tesseract-ocr\tesseract.exe'


@pytest.fixture()
def path_branch(monkeypatch):
    """把 ``_do_find_tesseract`` 逼进「系统 PATH」分支，并隔离全局状态。

    注意：``core/config.py`` 在函数体内做 ``import shutil``，绑定到的是
    ``sys.modules`` 里的真实模块，因此必须 patch ``shutil.which`` 这个**模块属性**，
    而不是 ``config_mod.shutil``（后者不存在，patch 了也不生效）。
    """
    monkeypatch.setattr(config_mod, 'TESSERACT_SEARCH_PATH_TEMPLATES', [], raising=True)
    monkeypatch.setattr(shutil, 'which', lambda name: FAKE_EXE)
    PathResolver.clear_cache()
    yield
    PathResolver.clear_cache()


def _patch_run(monkeypatch, *, stdout=None, raises=None):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if raises is not None:
            raise raises
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(subprocess, 'run', fake_run)
    return calls


class TestNoPopenInSource:
    # 说明：此处**只能**用 AST 判定。修复点的说明注释里会引述旧写法
    # `os.popen('tesseract --list-langs 2>&1')`，任何原文/正则扫描都会误伤自己。
    def test_ast_has_no_popen_call(self):
        tree = ast.parse(CONFIG_PY.read_text(encoding='utf-8'))
        offenders = [
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'popen'
        ]
        assert offenders == [], f'core/config.py 仍调用 os.popen（行 {offenders}）'

    def test_ast_has_no_popen_reference_at_all(self):
        tree = ast.parse(CONFIG_PY.read_text(encoding='utf-8'))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert 'popen' not in names


class TestProbeBehaviour:
    def test_detects_system_langs(self, path_branch, monkeypatch):
        calls = _patch_run(monkeypatch, stdout='List of available languages:\neng\nchi_sim\n')
        assert PathResolver._do_find_tesseract() == (FAKE_EXE, 'system')
        args, kwargs = calls[0]
        assert args == [FAKE_EXE, '--list-langs'], '应复用已解析的绝对路径，而非裸命令名'
        assert kwargs.get('shell') is not True, '不应启用 shell'
        assert kwargs.get('stderr') is subprocess.STDOUT, 'stderr 应合并进 stdout（保持原 2>&1 语义）'

    def test_eng_only_still_detected(self, path_branch, monkeypatch):
        _patch_run(monkeypatch, stdout='eng\n')
        assert PathResolver._do_find_tesseract() == (FAKE_EXE, 'system')

    def test_unknown_langs_leave_tessdata_none(self, path_branch, monkeypatch):
        _patch_run(monkeypatch, stdout='List of available languages:\nosd\n')
        assert PathResolver._do_find_tesseract() == (FAKE_EXE, None)

    @pytest.mark.parametrize('exc', [
        FileNotFoundError('missing'),
        subprocess.TimeoutExpired(cmd='tesseract', timeout=1),
        OSError('boom'),
    ], ids=['FileNotFoundError', 'TimeoutExpired', 'OSError'])
    def test_probe_failure_is_swallowed(self, path_branch, monkeypatch, exc):
        """原实现的 ``except Exception: pass`` 语义必须保留：异常不外泄。"""
        _patch_run(monkeypatch, raises=exc)
        assert PathResolver._do_find_tesseract() == (FAKE_EXE, None)

    def test_no_tesseract_on_path_returns_none(self, path_branch, monkeypatch):
        monkeypatch.setattr(shutil, 'which', lambda name: None)
        assert PathResolver._do_find_tesseract() == (None, None)
