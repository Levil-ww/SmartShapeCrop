"""
tests/core/test_app_version_single_source.py
[Fix 2026-09-24 P1-1 / P2-3] 版本号单一事实来源（``core.config.APP_VERSION``）。

根因
----
2026-09-24 审查实测：全项目 ``git grep __version__`` 仅命中第三方
``PyInstaller.__version__``，项目自身**没有任何版本常量**，版本只靠提交信息与
散落文档追踪 —— 由此产生「仓库已是 V2.2.3、打包脚本仍叫 packageV2.2.2 并产出
标识为 V2.2.2 的 exe」的错位（P1-1）。

约定
----
发版只改 ``core/config.py`` 的 ``APP_VERSION`` 一行；下列消费方一律引用它：
  - ``packaging/packageV<版本>.py`` → exe 名 / 打包横幅
  - ``core/log_setup.py``           → 启动日志头
  - ``main.py``                     → 「关于」对话框

本文件锁住「单一来源」这件事本身：常量存在且格式正确、派生名一致、
三个消费方都真实引用它、打包脚本内不再硬编码版本字面量。
"""
import ast
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

from core.config import APP_DISPLAY_NAME, APP_VERSION

PACKAGE_SCRIPT = PROJECT_ROOT / 'packaging' / 'packageV2.2.3.py'
SPEC_FILE = PROJECT_ROOT / 'packaging' / 'specs' / f'{APP_DISPLAY_NAME}.spec'


# ---------------------------------------------------------------------------
# 1) 常量本身
# ---------------------------------------------------------------------------

class TestVersionConstant:
    def test_is_semver_like_string(self):
        assert isinstance(APP_VERSION, str)
        assert re.fullmatch(r'\d+\.\d+(\.\d+)?', APP_VERSION), APP_VERSION

    def test_display_name_derived_from_version(self):
        assert APP_DISPLAY_NAME == f'智能裁剪设计器V{APP_VERSION}'

    def test_single_definition_point(self):
        """全项目只应有一个 APP_VERSION 赋值点。

        跳过隐藏目录与归档/产物目录 —— 尤其是 ``.pytest_tmp``：验证脚本会在其下
        复制一份仓库子集（用于补丁往返校验），若不跳过会误判为「多个定义点」。
        """
        skip_parts = {'.venv', '.git', '.pytest_cache', '.pytest_tmp', '.workbuddy',
                      '.dumate', '.trae-html-share-packages', '__pycache__',
                      '_archive', 'build', 'dist', 'ProductSummary'}
        hits = []
        for path in PROJECT_ROOT.rglob('*.py'):
            rel = path.relative_to(PROJECT_ROOT)
            if set(rel.parts) & skip_parts:
                continue
            if any(p.startswith('.') for p in rel.parts[:-1]):
                continue
            text = path.read_text(encoding='utf-8', errors='replace')
            if re.search(r'^\s*APP_VERSION\s*[:=]', text, re.M):
                hits.append(rel.as_posix())
        assert hits == ['core/config.py'], f'APP_VERSION 定义点不唯一：{hits}'


# ---------------------------------------------------------------------------
# 2) 打包脚本
# ---------------------------------------------------------------------------

def _load_package_script():
    spec = importlib.util.spec_from_file_location('_ssc_pkg_probe', PACKAGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPackagingScript:
    def test_new_entry_exists(self):
        assert PACKAGE_SCRIPT.is_file(), '缺少 packaging/packageV2.2.3.py'
        assert SPEC_FILE.is_file(), f'缺少 spec：{SPEC_FILE.name}'

    def test_exe_name_matches_app_version(self):
        module = _load_package_script()
        assert module.APP_VERSION == APP_VERSION
        assert module.APP_NAME == APP_DISPLAY_NAME

    def test_no_hardcoded_version_literal_in_code(self):
        """可执行代码里不得再出现版本字面量（文档串里的历史叙述不算）。"""
        tree = ast.parse(PACKAGE_SCRIPT.read_text(encoding='utf-8'))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                body = getattr(node, 'body', [])
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    docstrings.add(id(body[0].value))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                if re.search(r'\d+\.\d+\.\d+', node.value):
                    offenders.append((node.lineno, node.value[:60]))
        assert offenders == [], f'打包脚本内仍有硬编码版本字面量：{offenders}'

    def test_spec_name_matches_display_name(self):
        assert f"name='{APP_DISPLAY_NAME}'" in SPEC_FILE.read_text(encoding='utf-8')


# ---------------------------------------------------------------------------
# 3) 日志头与 GUI 关于框
# ---------------------------------------------------------------------------

class TestConsumers:
    def test_log_setup_source_references_version(self):
        src = (PROJECT_ROOT / 'core' / 'log_setup.py').read_text(encoding='utf-8')
        assert 'APP_VERSION' in src

    def test_log_header_actually_contains_version(self, tmp_path):
        log_file = tmp_path / 'probe.log'
        code = (
            "from core.log_setup import setup_logging\n"
            f"setup_logging(level='INFO', log_file=r'{log_file}', console=False)\n"
        )
        proc = subprocess.run([sys.executable, '-c', code], cwd=str(PROJECT_ROOT),
                              capture_output=True, text=True, encoding='utf-8',
                              errors='replace')
        assert proc.returncode == 0, proc.stderr
        text = log_file.read_text(encoding='utf-8')
        assert f'SmartShapeCrop v{APP_VERSION}' in text, text[:400]

    def test_main_about_dialog_references_version(self):
        src = (PROJECT_ROOT / 'main.py').read_text(encoding='utf-8')
        tree = ast.parse(src)
        about = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == '_about':
                about = node
        assert about is not None, 'main.py 未找到 _about'
        body = ast.get_source_segment(src, about)
        assert 'APP_VERSION' in body, '「关于」对话框未引用 APP_VERSION'

    def test_main_imports_version_from_config(self):
        src = (PROJECT_ROOT / 'main.py').read_text(encoding='utf-8')
        assert re.search(r'from core\.config import[^\n]*APP_VERSION', src)
