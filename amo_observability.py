# -*- coding: utf-8 -*-
"""吞异常也要留痕：计数 + 限流 warning，/status 可查。

以前大量 `except Exception: pass` 把真问题一起吞了（天气、记忆桥、面板都踩过）。
这里不做「一律不吞」——很多是可选功能的降级——但每次失败都记一笔，
并在日志里限流提醒，避免同一个点刷屏。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import threading
import time

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from astrbot.api import logger
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from astrbot.api import logger
    except Exception:  # pragma: no cover - 纯单测环境
        import logging

        logger = logging.getLogger("amo_observability")

_lock = threading.Lock()
_counts: dict[str, int] = {}
_last_log: dict[str, float] = {}
_last_global = [0.0]

#: 同一个点最少隔多久再 warning 一次（秒）
WARN_INTERVAL = 600
#: 全局 warning 最小间隔（秒）
GLOBAL_INTERVAL = 15


def note(scope, exc=None):
    """记一次「被吞掉的异常」；计数永远加，日志限流。"""
    key = str(scope or "?")
    now = time.time()
    with _lock:
        _counts[key] = _counts.get(key, 0) + 1
        count = _counts[key]
        if now - _last_log.get(key, 0.0) < WARN_INTERVAL:
            return
        if now - _last_global[0] < GLOBAL_INTERVAL:
            return
        _last_log[key] = now
        _last_global[0] = now
    try:
        logger.warning("[amo] %s 失败（累计 %d 次）: %s: %s" % (
            key, count,
            type(exc).__name__ if exc is not None else "-",
            str(exc) if exc is not None else "-",
        ))
    except Exception:
        pass


def snapshot(limit=20):
    """被吞次数最多的前 N 个点（给 /status / 面板用）。"""
    with _lock:
        items = sorted(_counts.items(), key=lambda kv: -kv[1])[: max(1, int(limit))]
    return dict(items)


def reset():
    with _lock:
        _counts.clear()
        _last_log.clear()
        _last_global[0] = 0.0
