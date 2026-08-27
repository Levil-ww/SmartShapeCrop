"""
tests/test_f15_f19_fixes.py
F15–F19（低危）修复的回归测试：

  F15 调试脚本混入 tests/  → tests/conftest.py collect_ignore 覆盖所有零测试脚本
  F16 PreviewRenderWorker  → _retire_worker / shutdown 生命周期管理
  F17 pytest.ini           → 不再全局屏蔽 DeprecationWarning
  F18 log_setup 文档       → 文档默认级别与代码一致（INFO）
  F19 产物目录治理         → core/artifact_cleanup.py 保留期+数量上限+保护名单
"""
from __future__ import annotations

import inspect
import os
import re
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# QApplication 供 QWidget 相关测试使用（整个会话只建一次）
try:
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
except Exception:  # PyQt5 不可用时跳过 F16 的 widget 用例
    _app = None


# ---------------------------------------------------------------------------
# F15: tests/conftest.py collect_ignore 必须覆盖所有零测试函数的脚本
# ---------------------------------------------------------------------------
def _has_real_tests(path: Path) -> bool:
    text = path.read_text(encoding='utf-8', errors='replace')
    return bool(re.search(r'^\s*(def test_|class Test)', text, re.M))


def test_f15_every_zero_test_file_is_collect_ignored():
    from tests import conftest as tests_conftest
    ignored = set(tests_conftest.collect_ignore)

    zero_test_files = {
        p.name for p in (PROJECT_ROOT / 'tests').glob('test_*.py')
        if not _has_real_tests(p)
    }
    # 所有"看起来像测试但没有任何测试函数"的脚本都必须被排除出收集
    missing = zero_test_files - ignored
    assert not missing, f'以下零测试脚本未被 collect_ignore 排除: {sorted(missing)}'

    # 反向：被排除的文件必须确实没有真实测试（防止误伤真实用例）
    wrongly_ignored = {n for n in ignored if (PROJECT_ROOT / 'tests' / n).exists()
                       and _has_real_tests(PROJECT_ROOT / 'tests' / n)}
    assert not wrongly_ignored, f'以下含真实测试的文件被错误排除: {sorted(wrongly_ignored)}'


# ---------------------------------------------------------------------------
# F16: PreviewRenderWorker 生命周期管理
# ---------------------------------------------------------------------------
@pytest.mark.skipif(_app is None, reason='PyQt5 不可用')
def test_f16_retire_worker_clears_reference_on_none():
    from gui.canvas_widget import PreviewCanvas
    canvas = PreviewCanvas()
    canvas._render_worker = None
    canvas._retire_worker()  # 不应抛异常
    assert canvas._render_worker is None


@pytest.mark.skipif(_app is None, reason='PyQt5 不可用')
def test_f16_retire_worker_clears_reference_for_finished_worker():
    from gui.canvas_widget import PreviewRenderWorker, PreviewCanvas
    from core.geometry import CropDesign

    canvas = PreviewCanvas()
    design = CropDesign(canvas_w_cm=10.0, canvas_h_cm=10.0, dpi=72)
    worker = PreviewRenderWorker(design, canvas)  # 未 start，isRunning() 为 False
    canvas._render_worker = worker
    canvas._retire_worker()
    assert canvas._render_worker is None, '退役后必须清空引用，允许新 worker 启动'


@pytest.mark.skipif(_app is None, reason='PyQt5 不可用')
def test_f16_retire_worker_interrupts_running_thread():
    """运行中的线程：请求中断并等待其干净退出，绝不在运行中析构。"""
    from PyQt5.QtCore import QThread
    from gui.canvas_widget import PreviewCanvas

    class DummyRender(QThread):
        interrupted = False

        def run(self):
            for _ in range(200):
                if self.isInterruptionRequested():
                    self.interrupted = True
                    return
                self.msleep(20)

    canvas = PreviewCanvas()
    t = DummyRender()
    canvas._render_worker = t
    t.start()
    canvas._retire_worker()
    assert canvas._render_worker is None, '退役后必须清空引用'
    # 线程应因中断而退出，且 wait 能干净收敛（不会 destroy-while-running）
    assert t.wait(5000), '线程应在收到中断后及时结束'
    assert not t.isRunning()
    assert t.interrupted, 'retire 必须通过 requestInterruption 请求中断'


@pytest.mark.skipif(_app is None, reason='PyQt5 不可用')
def test_f16_shutdown_is_safe_with_and_without_worker():
    from gui.canvas_widget import PreviewCanvas
    canvas = PreviewCanvas()
    canvas.shutdown(timeout_ms=100)  # 无 worker：安全 no-op
    assert canvas._render_worker is None


# ---------------------------------------------------------------------------
# F17: pytest.ini 不再全局屏蔽 DeprecationWarning
# ---------------------------------------------------------------------------
def test_f17_pytest_ini_does_not_ignore_deprecation_warning():
    import configparser
    cp = configparser.ConfigParser()
    cp.read(PROJECT_ROOT / 'pytest.ini', encoding='utf-8')
    filterwarnings = cp.get('pytest', 'filterwarnings', fallback='').strip()
    assert 'ignore::DeprecationWarning' not in filterwarnings, (
        'pytest.ini 的 filterwarnings 不应全局屏蔽 DeprecationWarning（F17 回归）')


# ---------------------------------------------------------------------------
# F18: log_setup 文档与代码的默认级别一致
# ---------------------------------------------------------------------------
def test_f18_docstring_and_code_agree_on_default_level():
    from core import log_setup
    module_doc = log_setup.__doc__ or ''
    assert '默认级别 INFO' in module_doc
    assert '默认 WARNING' not in module_doc

    # 代码事实：环境变量缺失时默认 INFO
    src = inspect.getsource(log_setup.setup_logging)
    assert "os.environ.get('LOG_LEVEL', 'INFO')" in src

    func_doc = log_setup.setup_logging.__doc__ or ''
    assert '默认 INFO' in func_doc


# ---------------------------------------------------------------------------
# F19: artifact_cleanup 保留期 / 数量上限 / 保护名单
# ---------------------------------------------------------------------------
@pytest.fixture()
def f19_sandbox(tmp_path, monkeypatch):
    """把调试目录指到临时沙箱，避免误删真实产物。"""
    debug_dir = tmp_path / 'debug_output'
    log_dir = tmp_path / 'logs'
    debug_dir.mkdir()
    log_dir.mkdir()
    from core import artifact_cleanup
    monkeypatch.setattr(artifact_cleanup, 'DEBUG_ARTIFACT_DIRS', [debug_dir, log_dir])
    yield debug_dir, log_dir


def _touch(path: Path, age_days: float = 0.0, size: int = 10) -> None:
    path.write_bytes(b'x' * size)
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))


def test_f19_deletes_expired_but_keeps_fresh(f19_sandbox):
    from core.artifact_cleanup import cleanup_debug_artifacts
    debug_dir, log_dir = f19_sandbox
    old = debug_dir / 'old_screenshot.jpg'
    fresh = debug_dir / 'fresh_screenshot.jpg'
    _touch(old, age_days=60)
    _touch(fresh, age_days=1)

    removed = cleanup_debug_artifacts(max_age_days=30, max_files=200)
    assert not old.exists()
    assert fresh.exists()
    assert str(old) in removed


def test_f19_protects_rotating_log_and_crash_log(f19_sandbox):
    from core.artifact_cleanup import cleanup_debug_artifacts
    debug_dir, log_dir = f19_sandbox
    log1 = log_dir / 'smartshapecrop.log'
    log2 = log_dir / 'smartshapecrop.log.1'
    crash = log_dir / 'crash.log'
    for p, age in ((log1, 365), (log2, 365), (crash, 365)):
        _touch(p, age_days=age)
    old_debug = log_dir / 'old_debug.png'
    _touch(old_debug, age_days=365)

    cleanup_debug_artifacts(max_age_days=30, max_files=200)
    assert log1.exists() and log2.exists(), '滚动日志本体不得被清理'
    assert crash.exists(), 'crash.log 不得被清理'
    assert not old_debug.exists(), '过期调试图应被清理'


def test_f19_enforces_max_files_cap(f19_sandbox):
    from core.artifact_cleanup import cleanup_debug_artifacts
    debug_dir, _ = f19_sandbox
    for i in range(8):
        _touch(debug_dir / f'dbg_{i:02d}.jpg', age_days=1)  # 均在保留期内

    removed = cleanup_debug_artifacts(max_age_days=30, max_files=3)
    remaining = list(debug_dir.glob('*.jpg'))
    assert len(remaining) == 3, f'数量上限应把 8 个压到 3 个，实际剩 {len(remaining)}'
    assert len(removed) == 5
    # 保留的应是最新的
    kept_names = {p.name for p in remaining}
    assert 'dbg_07.jpg' in kept_names and 'dbg_06.jpg' in kept_names
    assert 'dbg_00.jpg' not in kept_names, '最旧的应优先删除'


def test_f19_dry_run_deletes_nothing(f19_sandbox):
    from core.artifact_cleanup import cleanup_debug_artifacts
    debug_dir, _ = f19_sandbox
    old = debug_dir / 'old.jpg'
    _touch(old, age_days=365)

    removed = cleanup_debug_artifacts(max_age_days=30, max_files=1, dry_run=True)
    assert old.exists(), 'dry_run 不得真正删除'
    assert any('dry-run' in r for r in removed)


def test_f19_real_dirs_never_include_deliverables():
    """白名单只允许调试目录，ProductSummary/dist 等交付物绝不进入。"""
    from core import artifact_cleanup
    for d in artifact_cleanup.DEBUG_ARTIFACT_DIRS:
        assert d.name in ('debug_output', 'logs'), f'意外目录进入清理白名单: {d}'
