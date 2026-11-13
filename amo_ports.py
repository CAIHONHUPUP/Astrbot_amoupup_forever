# -*- coding: utf-8 -*-
"""外部能力端口：金融/社会/日历等真实接口先定契约，不把具体 API 写进聊天主链。

这些 Protocol 不引入任何外部依赖；后续可以：
- 用本地 mock / 回放实现；
- 用独立插件或本地 worker 实现；
- 用真实行情/社交 API 实现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    currency: str = "CNY"
    ts: float = 0.0
    source: str = ""
    stale: bool = False


@dataclass(frozen=True)
class MarketSession:
    day: str
    is_trading_day: bool
    open_at: str = ""
    close_at: str = ""
    timezone: str = "Asia/Shanghai"
    reason: str = ""


@dataclass(frozen=True)
class SocialItem:
    item_id: str
    author: str
    text: str
    created_at: float = 0.0
    url: str = ""
    source: str = ""


@runtime_checkable
class MarketDataPort(Protocol):
    async def quote(self, symbol: str) -> Optional[Quote]:
        ...

    async def history(self, symbol: str, days: int = 20) -> Sequence[Quote]:
        ...


@runtime_checkable
class MarketCalendarPort(Protocol):
    async def session(self, day: str) -> MarketSession:
        ...


@runtime_checkable
class SocialFeedPort(Protocol):
    async def timeline(self, limit: int = 20) -> Sequence[SocialItem]:
        ...

    async def search(self, query: str, limit: int = 20) -> Sequence[SocialItem]:
        ...


@runtime_checkable
class SocialPublishPort(Protocol):
    """发帖必须带外部审批 token；端口本身不负责“自动发”。"""

    async def publish(self, text: str, approval_token: str = "") -> Any:
        ...
