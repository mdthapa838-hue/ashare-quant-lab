# -*- coding: utf-8 -*-
"""
core/logger.py — 日志系统

特性：
  - 同时输出到控制台和文件
  - 按日期切分日志文件
  - 自动清理过期日志
  - 敏感信息脱敏（token/密码）
"""
from __future__ import annotations

import os
import re
import sys
import time
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


_SENSITIVE_PATTERNS = [
    (re.compile(r'(?i)(token["\']?\s*[:=]\s*["\']?)([A-Za-z0-9]{16,})'), r'\1[REDACTED]'),
    (re.compile(r'(?i)(password["\']?\s*[:=]\s*["\']?)([^\s"\',]+)'), r'\1[REDACTED]'),
    (re.compile(r'(?i)(bearer\s+)([A-Za-z0-9._\-]{16,})'), r'\1[REDACTED]'),
    (re.compile(r'(?i)(account_id["\']?\s*[:=]\s*["\']?)(\d{6,})'), r'\1[REDACTED]'),
]


class RedactFilter(logging.Filter):
    """日志脱敏过滤器。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            for pat, rep in _SENSITIVE_PATTERNS:
                msg = pat.sub(rep, msg)
            record.msg = msg
            record.args = ()
        except Exception:
            pass
        return True


def setup_logger(name: str = "qmt_trader", log_dir: str | None = None,
                 level: str = "INFO", console: bool = True,
                 keep_days: int = 60) -> logging.Logger:
    """创建并配置日志器（幂等）。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    redactor = RedactFilter()

    if console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        ch.addFilter(redactor)
        logger.addHandler(ch)

    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            fh = TimedRotatingFileHandler(
                os.path.join(log_dir, f"{name}.log"),
                when="midnight", backupCount=max(1, keep_days), encoding="utf-8")
            fh.setFormatter(fmt)
            fh.addFilter(redactor)
            logger.addHandler(fh)
            _cleanup_old_logs(log_dir, keep_days)
        except Exception as e:
            logger.warning(f"文件日志初始化失败: {e}")

    return logger


def _cleanup_old_logs(log_dir: str, keep_days: int) -> None:
    """清理过期日志文件。"""
    try:
        cutoff = time.time() - keep_days * 86400
        for f in Path(log_dir).glob("*.log*"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception:
                pass
    except Exception:
        pass


def get_logger(name: str = "qmt_trader") -> logging.Logger:
    """获取已配置的日志器。"""
    lg = logging.getLogger(name)
    if not lg.handlers:
        return setup_logger(name)
    return lg
