"""
pytest 全局 conftest

- 将项目根目录加入 sys.path，使 `from core.xxx import ...` 可在 tests/ 下被识别
- 配置公共 fixture
"""
import sys
from pathlib import Path

# 项目根目录（conftest.py 所在目录）
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


import pytest


@pytest.fixture(autouse=True)
def _silence_logs():
    """测试时把日志级别提到 CRITICAL，避免污染测试输出。
    如需观察日志，运行 pytest 时加 -s --log-cli-level=DEBUG。"""
    import logging
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)
