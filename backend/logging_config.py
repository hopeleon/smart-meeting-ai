"""
日志配置模块 — 对齐 InsightEye run_demo.py 的 TeeStream 日志系统

将所有 print() 输出同时写入文件和控制台，
文件保存在 backend/logs/ 目录，格式为 meeting_YYYYMMDD_HHMMSS.log，
每行自动添加时间戳前缀。
"""

import logging
import sys
import time
import threading
from pathlib import Path
from typing import Optional

_log_file: Optional[object] = None
_log_seq = 0
_log_lock = threading.Lock()


def _get_log_dir() -> Path:
    """获取日志目录（backend/logs/）"""
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    return log_dir


def _generate_log_path() -> Path:
    """生成带时间戳的唯一日志文件路径"""
    log_dir = _get_log_dir()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return log_dir / f"meeting_{timestamp}.log"


class _TeeStream:
    """
    同时写到原始流和日志文件，每行自动加时间戳。
    对齐 InsightEye run_demo.py 的 _TeeStream 实现。
    """

    def __init__(self, original, log_file):
        self._orig = original
        self._file = log_file
        self._buf = ""
        self._log_seq = 0

    def write(self, data: str) -> None:
        self._orig.write(data)
        self._orig.flush()

        global _log_seq
        with _log_lock:
            self._buf += data
            while "\n" in self._buf:
                _log_seq += 1
                self._log_seq = _log_seq
                line, self._buf = self._buf.split("\n", 1)
                ts = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
                self._file.write(f"[{ts}] [#{self._log_seq:05d}] {line}\n")
                self._file.flush()

    def flush(self) -> None:
        self._orig.flush()
        self._file.flush()

    def fileno(self) -> int:
        return self._orig.fileno()

    def isatty(self) -> bool:
        return False


def setup_file_logging() -> Path:
    """
    初始化文件日志系统，将 stdout/stderr 重定向到 TeeStream。
    返回日志文件路径。
    """
    global _log_file

    log_path = _generate_log_path()
    _log_file = open(log_path, "w", encoding="utf-8")
    _log_file.write(
        f"=== Smart Meeting AI log started at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n"
    )
    _log_file.write(f"Log file: {log_path}\n")
    _log_file.write("=" * 60 + "\n")

    sys.stdout = _TeeStream(sys.__stdout__, _log_file)
    sys.stderr = _TeeStream(sys.__stderr__, _log_file)

    return log_path


def setup_logging() -> None:
    """配置 Python logging 模块（对齐 InsightEye 格式）"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def get_log_dir() -> Path:
    """返回日志目录路径（供外部引用）"""
    return _get_log_dir()


def flush_logs() -> None:
    """刷新所有日志缓冲区"""
    sys.stdout.flush()
    sys.stderr.flush()
