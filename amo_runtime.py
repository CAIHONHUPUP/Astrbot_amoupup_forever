# -*- coding: utf-8 -*-
"""RuntimeMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""

from __future__ import annotations
from typing import TYPE_CHECKING

try:  # 取时入口仍挂在门面上（tests/fixtures.py 的历史 patch 点）
    from .amo_shared import _now, now_ts, today_str
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared import _now, now_ts, today_str


try:  # 门面转发：上下文 mixin
    from .amo_runtime_context import (
    RuntimeContextMixin,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_runtime_context import (
        RuntimeContextMixin,
        )

try:  # 门面转发：回复 mixin
    from .amo_runtime_reply import (
    RuntimeReplyMixin,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_runtime_reply import (
        RuntimeReplyMixin,
        )

try:  # 门面转发：心跳 mixin
    from .amo_runtime_tick import (
    RuntimeTickMixin,
        warm_up,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_runtime_tick import (
        RuntimeTickMixin,
            warm_up,
        )

class RuntimeMixin(RuntimeContextMixin, RuntimeReplyMixin, RuntimeTickMixin):
    _last_relation_decay: float
    _last_relation_sediment: float
    _last_purge: float
    _last_auto_learn: float
    _last_pending_check: float
    _last_schedule_save: str
    _proactive_last: float

