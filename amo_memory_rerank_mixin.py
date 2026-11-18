# -*- coding: utf-8 -*-
"""amo_memory_rerank_mixin.py —— 每轮一次的召回重排（挂在 RecallContextMixin 前面）。

单独拆一个 mixin，是为了不改 amo_memory_context 的主体结构：
_rerank_memories 只在候选多于注入上限时调用一次小模型，失败/超时/没 provider
一律按原顺序返回。
"""
from __future__ import annotations

from typing import Any

try:  # 包模式
    from . import amo_memory_rerank
except ImportError:  # 直接按文件加载时
    import amo_memory_rerank

try:  # 包模式
    from .amo_shared import logger
except ImportError:  # 直接按文件加载时
    from amo_shared import logger


class MemoryRerankMixin:
    async def _rerank_memories(self: Any, memories: Any, text: str, limit: int, event: Any = None) -> Any:
        """每轮一次的记忆重排：候选不多就不调模型，失败/超时原样返回。"""
        cap = max(1, int(limit or 6))
        rows = list(memories or [])
        if len(rows) <= cap:
            return rows[:cap]
        if not str(text or "").strip():  # 空查询（记忆回声）没有“这句话”可比，跳过
            return rows[:cap]
        if not self.cfg_bool("memory_rerank_enabled", True):
            return rows[:cap]
        provider = await self._rerank_provider(event)
        if provider is None:
            return rows[:cap]

        async def _call(prompt: str) -> str:
            resp = await provider.text_chat(
                prompt=prompt, system_prompt="只输出 JSON，不要解释。"
            )
            return str(getattr(resp, "completion_text", "") or "")

        try:
            picked = await amo_memory_rerank.rerank(
                rows, text, cap, _call,
                max_candidates=self.cfg_int("memory_rerank_candidates", 10),
                timeout=self.cfg_float("memory_rerank_timeout", 6.0),
            )
            return picked[:cap]
        except Exception as exc:
            logger.debug("[amo] 记忆重排失败: %s" % exc)
            return rows[:cap]

    async def _rerank_provider(self: Any, event: Any = None) -> Any:
        umo = ""
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
        except Exception as exc:
            logger.debug("[amo] 取重排会话失败: %s" % exc)
        try:
            if hasattr(self.context, "get_using_provider_async"):
                return await self.context.get_using_provider_async(umo or None)
            return self.context.get_using_provider(umo or None)
        except Exception as exc:
            logger.debug("[amo] 取重排 provider 失败: %s" % exc)
            return None
