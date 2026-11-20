# -*- coding: utf-8 -*-
"""心流 mixin 的宿主成员声明。

``HeartflowTextMixin`` / ``HeartflowEventsMixin`` / ``HeartflowStateMixin`` 都是
mixin：配置和状态由 ``heartflow_engine.HeartflowEngine.__init__`` 初始化，方法分散在
三个 mixin 里互相调用。这里把“宿主提供的成员”集中声明成一个基类，三个 mixin 只在
``TYPE_CHECKING`` 下继承它，运行时的基类仍是 object，行为与改造前完全一致。
"""

from __future__ import annotations

import asyncio

from collections import deque
from typing import Any, Dict, List, Set, Tuple, TYPE_CHECKING

from astrbot.api.event import AstrMessageEvent

try:
    from .amo_group_guard import MemberBook
    from .heartflow_model import ChatState, JudgeResult, RawMessage
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_group_guard import MemberBook
        from heartflow_model import ChatState, JudgeResult, RawMessage


class HeartflowHost:
    """HeartflowEngine / HumanCorePlugin 为心流三个 mixin 提供的成员。"""

    # —— 基础配置（HeartflowEngine.__init__ 写入）——
    context: Any
    config: Any
    bot_name: str
    bot_aliases: List[str]
    bot_ids: Set[str]
    bot_keywords: Tuple[str, ...]
    members: MemberBook
    judge_provider_name: str
    judge_rules: List[Dict[str, Any]]
    judge_include_reasoning: bool
    judge_max_retries: int
    reply_threshold: float
    context_messages_count: int
    judge_context_count: int
    whitelist_enabled: bool
    _whitelist_set: Set[str]
    ignore_bots: bool
    only_when_addressed: bool
    score_min: float
    score_max: float
    weights: Dict[str, float]
    enable_detailed_log: bool

    # —— 动态阈值 ——
    enable_dynamic_threshold: bool
    dynamic_threshold_max: float
    dynamic_threshold_min: float
    dynamic_activity_window: int
    dynamic_activity_weights: List[float]
    group_threshold_configs: Dict[str, Dict[str, float]]

    # —— 群聊状态与原始消息缓冲 ——
    _lock: Any
    chat_states: Dict[str, ChatState]
    _raw_msg_buffer: Dict[str, deque[RawMessage]]
    _raw_msg_buffer_size: int
    _pause_restore_tasks: Dict[str, asyncio.Task]
    _buffer_cleanup_interval: float

    # —— 防抖 / 沉浸模式 ——
    enable_debounce: bool
    debounce_delay: float
    max_debounce_wait: float
    _debounce_states: Dict[str, Dict[str, Any]]
    immersive_mode_duration: int
    judgment_paused_timeout: int

    # —— 引擎（HeartflowEngine）提供的方法 ——
    def _auto_pick_judge_provider(self) -> Any:
        raise NotImplementedError

    # —— 状态 mixin（heartflow_state）提供的方法 ——
    def _log_detail(self, msg: str) -> None:
        raise NotImplementedError

    def _get_chat_state(self, chat_id: str) -> ChatState:
        raise NotImplementedError

    def _cancel_pause_restore_task(self, chat_id: str) -> None:
        raise NotImplementedError

    def _is_judgment_paused(self, chat_id: str) -> bool:
        raise NotImplementedError

    def _set_judgment_paused(self, chat_id: str, paused: bool = True) -> None:
        raise NotImplementedError

    def _get_minutes_since_last_reply(self, chat_id: str) -> int:
        raise NotImplementedError

    def _is_whitelisted(self, unified_msg_origin: str) -> bool:
        raise NotImplementedError

    def _record_raw_message(self, event: AstrMessageEvent, is_bot: bool = False, is_other_bot: bool = False) -> bool:
        raise NotImplementedError

    def _record_bot_reply_manual(self, umo: str, content: str) -> bool:
        raise NotImplementedError

    def _get_raw_buffer(self, umo: str) -> List[RawMessage]:
        raise NotImplementedError

    def _cleanup_old_messages(self, umo: str) -> None:
        raise NotImplementedError

    def _get_activity_description(self, activity: float) -> str:
        raise NotImplementedError

    def _get_group_config_info(self, chat_id: str) -> str:
        raise NotImplementedError

    def _get_dynamic_threshold(self, chat_id: str) -> Tuple[float, float, float]:
        raise NotImplementedError

    def _is_immersive_mode_active(self, chat_id: str) -> bool:
        raise NotImplementedError

    def _get_immersive_mode_remaining(self, chat_id: str) -> int:
        raise NotImplementedError

    def _activate_immersive_mode(self, chat_id: str) -> None:
        raise NotImplementedError

    def _refresh_immersive_mode(self, chat_id: str) -> None:
        raise NotImplementedError

    # —— 事件 mixin（heartflow_events）提供的方法 ——
    def _addresses_bot(self, text: str) -> str:
        raise NotImplementedError

    def _clean_message_content(self, text: str, max_len: int = 200) -> str:
        raise NotImplementedError

    def _normalize_for_dedup(self, text: str) -> str:
        raise NotImplementedError

    @staticmethod
    def _message_meta(msg_obj) -> dict:
        raise NotImplementedError

    # —— 文案 mixin（heartflow_text）提供的方法 ——
    async def judge_with_tiny_model(self, event: AstrMessageEvent) -> JudgeResult:
        raise NotImplementedError

    async def judge_immersive_mode(self, event: AstrMessageEvent) -> JudgeResult:
        raise NotImplementedError
