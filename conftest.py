"""
pytest 全局 conftest

- 将项目根目录加入 sys.path，使 `from core.xxx import ...` 可在 tests/ 下被识别
- 配置公共 fixture
- 防御性屏蔽非测试目录下的 test_*.py 命名文件
  （pytest.ini 的 collect_ignore_glob 在部分版本下不识别 glob 模式，
   故这里用 pathlib 的 glob 兜底，确保 scripts/_archive 等目录下
   的历史 test_*.py 永远不会被收集）
"""
import sys
from pathlib import Path

# 项目根目录（conftest.py 所在目录）
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


import pytest

# glob 兜底：屏蔽 scripts/、_archive/ 下所有 test_*.py 文件
# 防止未来 testpaths 被误删时这些文件被收集
_IGNORE_PATTERNS = ("scripts/**/_test_*.py", "scripts/**/test_*.py",
                    "_archive/**/test_*.py", "packaging/**/test_*.py",
                    "ProductSummary/**/test_*.py")

# pytest 7.0+ 支持 collect_ignore_glob（在 conftest.py 里设置后全局生效）
try:
    collect_ignore_glob = list(_IGNORE_PATTERNS)
except Exception:
    pass


@pytest.fixture(autouse=True)
def _silence_logs():
    """测试时把日志级别提到 CRITICAL，避免污染测试输出。
    如需观察日志，运行 pytest 时加 -s --log-cli-level=DEBUG。"""
    import logging
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)
