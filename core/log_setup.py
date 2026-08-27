"""
core/log_setup.py
项目统一日志配置。

设计目标：
  - 集中配置根 logger，避免每个模块各自 setup_logging 造成重复 handler
  - 默认级别 INFO（控制台安静、日志文件保留运行轨迹），可通过环境变量
    LOG_LEVEL 动态调整（生产需要安静可设 LOG_LEVEL=WARNING）
  - 输出到控制台 + 滚动日志文件（logs/smartshapecrop.log）
  - 格式统一：[时间] [级别] [模块] 消息

使用方式：
  - 程序入口（main.py / process_image.py）调用 setup_logging() 一次即可
  - 各业务模块只需 `logger = logging.getLogger(__name__)`，无需关心 handler
  - 调试时设置环境变量 LOG_LEVEL=DEBUG 重启程序

注意：重复调用 setup_logging() 会自动跳过（幂等），避免重复 handler。
"""
from __future__ import annotations
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


# 项目根目录（core/ 的上一级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 日志目录
_LOG_DIR = _PROJECT_ROOT / 'logs'
# 日志文件
_LOG_FILE = _LOG_DIR / 'smartshapecrop.log'

# 统一日志格式
_LOG_FORMAT = '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s'
_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# 标记是否已配置过（幂等保护）
_logging_configured = False


def setup_logging(level: str | int | None = None, log_file: str | Path | None = None,
                  console: bool = True, file_mode: str = 'a',
                  max_bytes: int = 5 * 1024 * 1024, backup_count: int = 3) -> None:
    """
    配置项目根 logger。建议在程序入口（main.py / process_image.py）调用一次。

    幂等：重复调用不会重复添加 handler。

    Args:
        level: 日志级别（'DEBUG'/'INFO'/'WARNING'/'ERROR' 或 logging.DEBUG 等整数）。
               None 时按优先级取：环境变量 LOG_LEVEL > 默认 INFO；
               环境变量值非法时回退 WARNING。
        log_file: 日志文件路径。None 时使用默认 logs/smartshapecrop.log。
        console: 是否输出到控制台（stderr）。默认 True。
        file_mode: 文件打开模式，默认 'a'（追加）。
        max_bytes: 单个日志文件最大字节数，默认 5MB。
        backup_count: 保留的旧日志文件数，默认 3 个。
    """
    global _logging_configured
    if _logging_configured:
        return

    # 确定日志级别
    if level is None:
        level = os.environ.get('LOG_LEVEL', 'INFO')
    if isinstance(level, str):
        level = level.upper()
        level = getattr(logging, level, logging.WARNING)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # 控制台 handler
    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # 文件 handler（滚动）
    if log_file is None:
        log_file = _LOG_FILE
    log_file = Path(log_file)
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, mode=file_mode, maxBytes=max_bytes,
            backupCount=backup_count, encoding='utf-8'
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except (PermissionError, OSError) as e:
        # 日志文件不可写入时，仅控制台输出，不阻断程序
        root_logger.warning(f'[log_setup] 无法创建日志文件 {log_file}: {e}，仅控制台输出')

    _logging_configured = True

    # 自己输出一条启动信息（用 root logger，避免模块 logger 名混乱）
    root_logger.info(f'[log_setup] 日志系统已配置：级别={logging.getLevelName(level)}，'
                     f'控制台={console}，文件={log_file}')


def get_log_file_path() -> Path:
    """返回当前日志文件路径"""
    return _LOG_FILE


def is_configured() -> bool:
    """返回日志系统是否已配置"""
    return _logging_configured
