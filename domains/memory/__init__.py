# -*- coding: utf-8 -*-
"""记忆域（pilot）：只定义「记忆终端」契约，不 import 任何插件模块。

为什么单独抽契约：陪伴（amo）只判定、数据交给「能记」（Astrbot_amoupup_data）。
以前靠 getattr 逐个探方法名；把要用的方法收敛成 Protocol 后，
「数据插件少实现一个方法」这件事在契约测试里立刻可见，而不是运行时静默降级。

契约测试见 tests/test_domain_boundaries.py。
"""
from __future__ import annotations

from typing import Any, Optional, Protocol


class MemoryTerminal(Protocol):
    """记忆终端的对外契约（实现方：数据插件 / 本地回退）。"""

    def recall(self, uid: str, query: str, limit: int = 6, **kwargs: Any) -> list[dict[str, Any]]:
        ...

    def remember(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def add_message(self, *args: Any, **kwargs: Any) -> Any:
        ...


class RecallScope(Protocol):
    """召回作用域：群聊与私聊隔离（群写 group、私聊写 private）。"""

    def scope(self) -> str:
        ...

    def group_id(self) -> Optional[str]:
        ...


#: 数据插件必须提供的方法名（契约漂移测试用；新增能力时同步加）。
TERMINAL_METHODS = ("recall", "remember", "add_message")
