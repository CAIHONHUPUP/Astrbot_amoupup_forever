# -*- coding: utf-8 -*-
"""记忆入口（facade）：只适配「能记」(Astrbot_amoupup_data)。

    amo_memory_data.py     DataSyncMixin：能记桥 / 同步 / 画像 / 列表 / 读写
    amo_memory_bridge.py   BridgeMixin：MemoryHub 与写入入口
    amo_memory_context.py  RecallContextMixin：按时间回看、跨平台、_recall

旧记忆源（牢记 / 天使之魂 / livingmemory / companion）的适配已移除。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_memory_bridge import BridgeMixin
    from .amo_memory_context import RecallContextMixin
    from .amo_memory_data import DataSyncMixin
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_memory_bridge import BridgeMixin
        from .amo_memory_context import RecallContextMixin
        from .amo_memory_data import DataSyncMixin
    except ImportError:  # 直接按文件加载时
        from amo_memory_bridge import BridgeMixin
        from amo_memory_context import RecallContextMixin
        from amo_memory_data import DataSyncMixin


class MemoryMixin(DataSyncMixin, BridgeMixin, RecallContextMixin):
    """组合见上：实际方法在三个子模块里。"""

    pass
