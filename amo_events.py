# -*- coding: utf-8 -*-
"""最小事件总线：给日程/媒体/记忆等模块一个稳定的同步事件缝。

设计约束：
- 只做进程内、同步发布；不引入 asyncio/外部依赖。
- 订阅者异常不能影响发布者（主聊天链路不能被审计/日志订阅者拖垮）。
- 事件类型用 dataclass 固定字段，后续订阅者不靠字符串猜 payload。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from astrbot.api import logger
except Exception:  # pragma: no cover - 纯单测环境
    import logging
    logger = logging.getLogger("amo_events")


@dataclass(frozen=True)
class Event:
    """所有事件的基类。"""
    name: str = "event"


@dataclass(frozen=True)
class ScheduleChanged(Event):
    """日程写入成功后的稳定事件。"""
    name: str = "schedule_changed"
    day: str = ""
    version: int = 0
    source: str = ""
    reason: str = ""
    manual_override: bool = False
    start_hour: int = -1
    changed_hours: tuple = ()
    cancelled: tuple = ()


@dataclass(frozen=True)
class DayRolled(Event):
    """跨天事件；目前只预留给日程/日记/穿搭订阅者。"""
    name: str = "day_rolled"
    day: str = ""


@dataclass(frozen=True)
class MediaSaved(Event):
    """bot 媒体（照片/语音）写入后的稳定事件。"""
    name: str = "media_saved"
    umo: str = ""
    marker: str = ""


class EventBus:
    """线程内同步总线；订阅/取消订阅都幂等。"""

    def __init__(self):
        self._subscribers = {}
        self._published = 0

    def subscribe(self, event_type, handler: Callable[[Any], None]):
        if not callable(handler):
            raise TypeError("handler must be callable")
        handlers = self._subscribers.setdefault(event_type, [])
        if handler not in handlers:
            handlers.append(handler)

        def unsubscribe():
            current = self._subscribers.get(event_type) or []
            if handler in current:
                current.remove(handler)

        return unsubscribe

    def has_subscribers(self, event_type):
        return bool(self._subscribers.get(event_type))

    def unsubscribe(self, event_type, handler):
        handlers = self._subscribers.get(event_type) or []
        if handler in handlers:
            handlers.remove(handler)
            return True
        return False

    def _dispatch(self, event):
        if event is None:
            return 0, []
        self._published += 1
        delivered = 0
        results = []
        for event_type, handlers in list(self._subscribers.items()):
            if not isinstance(event, event_type):
                continue
            for handler in list(handlers):
                try:
                    result = handler(event)
                    delivered += 1
                    if result is not None:
                        results.append(result)
                except Exception as exc:
                    logger.warning(
                        "[EVENT] handler_failed event=%s handler=%s error=%s",
                        getattr(event, "name", type(event).__name__),
                        getattr(handler, "__name__", type(handler).__name__),
                        exc,
                    )
        return delivered, results

    def publish(self, event):
        """同步发布；返回成功送达的订阅者数。"""
        delivered, _ = self._dispatch(event)
        return delivered

    def publish_collect(self, event):
        """同步发布并收集订阅者返回值（用于需要取消列表等副产物的链路）。"""
        _, results = self._dispatch(event)
        return results

    @property
    def published(self):
        return self._published

    def clear(self):
        self._subscribers.clear()
