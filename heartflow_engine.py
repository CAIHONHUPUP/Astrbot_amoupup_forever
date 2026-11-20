"""
心流插件 - 智能群聊主动回复系统 v4.27+
支持动态阈值 / 评分模式 / 沉浸模式 / 防抖 / 群聊独立配置
评分规则支持可视化配置（低分/高分情况分维度填写）
*注：已移除大模型上下文注入，完全依赖 AstrBot 原生对话流*
"""

from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

import json
import random
import re
import time
import datetime
import traceback
import threading
import asyncio
from collections import deque
from typing import Dict, Optional, List, Any, Set, Tuple
from dataclasses import dataclass

from astrbot.api.star import Context
from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger, AstrBotConfig
from astrbot.api.message_components import Plain, At, Reply
from astrbot.api.provider import LLMResponse

# 群聊身份识别：认机器人 / 认人 / 判“这句是不是冲她说的”
try:
    from .amo_group_guard import MemberBook, addressing_verdict, is_bot_sender, parse_ids, parse_keywords
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_group_guard import MemberBook, addressing_verdict, is_bot_sender, parse_ids, parse_keywords

# 统一时区（只影响面板/日志里显示的时间，但别和 clock 配置打架）
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .clock import now as _hf_now, from_timestamp as _hf_ts
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .clock import now as _hf_now, from_timestamp as _hf_ts
    except Exception:
        try:
            from clock import now as _hf_now, from_timestamp as _hf_ts
        except Exception:
            def _hf_now(*args, **kwargs):
                return _amo_clock.now()

            def _hf_ts(ts, *args, **kwargs):
                return datetime.datetime.fromtimestamp(float(ts or 0))


try:
    from .heartflow_model import (
    RE_CODE_BLOCK,
    RE_BRACE,
    RE_ALL_BRACES,
    RE_XML_TAG,
    RE_URL,
    SCORE_MIN,
    SCORE_MAX,
    SCORE_DEFAULT,
    BUILTIN_RULES,
    INTERJECT_ANGLES,
    BASE_JUDGE_PROMPT,
    JudgeResult,
    RawMessage,
    ChatState,
    clamp_score,
    safe_extract_score,
    _normalize_target,
    _normalize_style,
    _normalize_context_type,
    _extract_json,
    _build_rules_section,
    _extract_weights_from_rules,
    _validate_config,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from heartflow_model import (
            RE_CODE_BLOCK,
            RE_BRACE,
            RE_ALL_BRACES,
            RE_XML_TAG,
            RE_URL,
            SCORE_MIN,
            SCORE_MAX,
            SCORE_DEFAULT,
            BUILTIN_RULES,
            INTERJECT_ANGLES,
            BASE_JUDGE_PROMPT,
            JudgeResult,
            RawMessage,
            ChatState,
            clamp_score,
            safe_extract_score,
            _normalize_target,
            _normalize_style,
            _normalize_context_type,
            _extract_json,
            _build_rules_section,
            _extract_weights_from_rules,
            _validate_config,
        )

try:
    from .heartflow_state import HeartflowStateMixin
    from .heartflow_events import HeartflowEventsMixin
    from .heartflow_text import HeartflowTextMixin, _build_immersive_prompt
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from heartflow_state import HeartflowStateMixin
        from heartflow_events import HeartflowEventsMixin
        from heartflow_text import HeartflowTextMixin, _build_immersive_prompt


class HeartflowEngine(HeartflowStateMixin, HeartflowEventsMixin, HeartflowTextMixin):
    """心流插件 - 智能群聊主动回复系统
    
    通过小模型多维度评分，判断是否在群聊中主动回复。
    支持动态阈值、沉浸模式、防抖延迟、群聊独立配置等功能。
    """

    _lock = threading.RLock()

    def __init__(self, context: Context, config: AstrBotConfig):
        """初始化插件
        
        Args:
            context: AstrBot 上下文对象
            config: 插件配置对象
        """
        self.context = context
        self.config = _validate_config(config)
        self.bot_name = ""
        self.bot_aliases: List[str] = []

        # 基础配置
        self.judge_provider_name: str = self.config.get("judge_provider_name", "")
        self.reply_threshold: float = self.config.get("reply_threshold", 0.6)
        self.context_messages_count: int = self.config.get("context_messages_count", 5)
        self.judge_context_count: int = self.config.get("judge_context_count", self.context_messages_count)
        self.whitelist_enabled: bool = self.config.get("whitelist_enabled", False)
        self._whitelist_set: Set[str] = {str(x).strip() for x in self.config.get("chat_whitelist", []) if str(x).strip()}
        self.judge_include_reasoning: bool = self.config.get("judge_include_reasoning", True)
        self.judge_max_retries: int = self.config.get("judge_max_retries", 3)
        self.immersive_mode_duration: int = self.config.get("immersive_mode_duration", 30)
        self.judgment_paused_timeout: int = self.config.get("judgment_paused_timeout", 60)

        # 动态阈值配置
        self.enable_dynamic_threshold: bool = self.config.get("enable_dynamic_threshold", False)
        self.dynamic_threshold_max: float = self.config.get("dynamic_threshold_max", 0.85)
        self.dynamic_threshold_min: float = self.config.get("dynamic_threshold_min", 0.35)
        self.dynamic_activity_window: int = self.config.get("dynamic_activity_window", 300)
        self.dynamic_activity_weights: List[float] = self.config.get("dynamic_activity_weights", [0.4, 0.3, 0.3])

        # 群聊独立阈值配置
        self.group_threshold_configs: Dict[str, Dict[str, float]] = {}
        for item in self.config.get("group_threshold_configs", []):
            chat_id = str(item.get("chat_id", "")).strip()
            if chat_id:
                g_reply = clamp_score(item.get("reply_threshold", self.reply_threshold), 0.0, 1.0, self.reply_threshold, allow_zero=True)
                g_max = clamp_score(item.get("dynamic_threshold_max", self.dynamic_threshold_max), 0.0, 1.0, self.dynamic_threshold_max, allow_zero=True)
                g_min = clamp_score(item.get("dynamic_threshold_min", self.dynamic_threshold_min), 0.0, 1.0, self.dynamic_threshold_min, allow_zero=True)
                if g_min > g_max: g_min = max(0.0, g_max - 0.1)
                self.group_threshold_configs[chat_id] = {
                    "reply_threshold": g_reply,
                    "dynamic_threshold_max": g_max,
                    "dynamic_threshold_min": g_min,
                }

        # 评分配置
        self.score_min, self.score_max, self.score_default = SCORE_MIN, SCORE_MAX, SCORE_DEFAULT
        self.judge_rules: List[Dict[str, Any]] = self.config.get("judge_rules", [])
        self.weights = _extract_weights_from_rules(self.judge_rules)

        # 状态管理
        self.chat_states: Dict[str, ChatState] = {}
        self._raw_msg_buffer: Dict[str, deque[RawMessage]] = {}
        self._raw_msg_buffer_size: int = max(self.context_messages_count, self.judge_context_count) * 4
        self._pause_restore_tasks: Dict[str, asyncio.Task] = {}
        self._buffer_cleanup_interval: float = 3600.0
        self.absolute_threshold = self.score_min + (self.score_max - self.score_min) * self.reply_threshold

        # 防抖配置
        self.enable_debounce: bool = self.config.get("enable_debounce", True)
        self.debounce_delay: float = float(self.config.get("debounce_delay", 2.0))
        self.max_debounce_wait: float = float(self.config.get("max_debounce_wait", 5.0))
        self._debounce_states: Dict[str, Dict[str, Any]] = {}
        self.enable_detailed_log: bool = self.config.get("enable_detailed_log", False)

        # 群聊身份识别
        self.ignore_bots: bool = bool(self.config.get("ignore_bots", True))
        self.only_when_addressed: bool = bool(self.config.get("reply_only_when_addressed", True))
        self.bot_keywords: Tuple[str, ...] = parse_keywords(self.config.get("bot_keywords", ""))
        self.bot_ids: Set[str] = parse_ids(self.config.get("bot_ids", ""))
        self.members = MemberBook()

        logger.debug(
            f"[心流] 初始化完成 | 基础阈值: {self.reply_threshold} | "
            f"独立配置群数: {len(self.group_threshold_configs)} | 防抖: {'开' if self.enable_debounce else '关'}"
        )

    def _list_chat_providers(self):
        """列出所有可用的对话供应商（id/model/provider 对象），忽略停用的。"""
        items = []
        try:
            raw = self.context.get_all_providers() or []
        except Exception as _amo_exc:
            _note_swallowed("heartflow_engine.HeartflowEngine._list_chat_providers", _amo_exc)
            raw = []
        for provider in raw:
            config = getattr(provider, "provider_config", None) or {}
            if config.get("enable") is False:
                continue
            pid = str(config.get("id") or "")
            if not pid:
                continue
            model = ""
            try:
                model = str(provider.get_model() or "")
            except Exception as _amo_exc:
                _note_swallowed("heartflow_engine.HeartflowEngine._list_chat_providers", _amo_exc)
                model = str(config.get("model") or "")
            items.append({"id": pid, "model": model, "provider": provider})
        return items

    def _auto_pick_judge_provider(self):
        """未配置判定模型时自动挑一个：优先便宜小模型，其次避开主对话模型。

        结果按供应商列表签名缓存；在 AstrBot 里增删/改模型后下一次判定会自动重选。
        """
        providers = self._list_chat_providers()
        signature = "|".join(sorted(item["id"] + ":" + item["model"] for item in providers))
        cache = getattr(self, "_judge_auto_cache", None)
        if isinstance(cache, dict) and cache.get("signature") == signature:
            return cache.get("provider")
        if not providers:
            self._judge_auto_cache = {"signature": signature, "provider": None}
            return None
        using_id = ""
        try:
            using = self.context.get_using_provider()
            using_id = str((getattr(using, "provider_config", None) or {}).get("id") or "")
        except Exception as _amo_exc:
            _note_swallowed("heartflow_engine.HeartflowEngine._auto_pick_judge_provider", _amo_exc)
            using_id = ""
        others = [item for item in providers if item["id"] != using_id]
        pool = others or providers
        cheap_hints = (
            "flash", "mini", "lite", "turbo", "small", "fast", "air",
            "8b", "7b", "1.5b", "3b", "nano", "haiku", "spark",
        )

        def cheap_score(item):
            text = (item["id"] + " " + item["model"]).lower()
            return sum(1 for hint in cheap_hints if hint in text)

        pool.sort(key=lambda item: (-cheap_score(item), item["id"]))
        chosen = pool[0]["provider"]
        self._judge_auto_cache = {"signature": signature, "provider": chosen}
        try:
            logger.info(
                "[心流] 判定模型自动选择: %s (%s)%s"
                % (pool[0]["id"], pool[0]["model"] or "?",
                   "；避开主对话模型 " + using_id if using_id and others else "")
            )
        except Exception as _amo_exc:
            _note_swallowed("heartflow_engine.HeartflowEngine._auto_pick_judge_provider", _amo_exc)
        return chosen

    def judge_provider_info(self):
        """给面板展示：判定模型当前用的哪个、是不是自动选的。"""
        configured = str(self.judge_provider_name or "").strip()
        provider = None
        if configured:
            try:
                provider = self.context.get_provider_by_id(configured)
            except Exception as _amo_exc:
                _note_swallowed("heartflow_engine.HeartflowEngine.judge_provider_info", _amo_exc)
                provider = None
            auto = False
        else:
            provider = self._auto_pick_judge_provider()
            auto = True
        info = {"configured": configured, "auto": auto, "id": "", "model": ""}
        if provider is not None:
            config = getattr(provider, "provider_config", None) or {}
            info["id"] = str(config.get("id") or "")
            try:
                info["model"] = str(provider.get_model() or "")
            except Exception as _amo_exc:
                _note_swallowed("heartflow_engine.HeartflowEngine.judge_provider_info", _amo_exc)
                info["model"] = str(config.get("model") or "")
        return info

    def set_identity(self, name: str = "", aliases=None) -> None:
        """更新机器人名字/别名，用于识别群友是否在叫机器人。"""
        values = aliases if isinstance(aliases, (list, tuple, set)) else str(aliases or "").split(",")
        new_name = str(name or "").strip()
        new_aliases = [str(item).strip() for item in values if str(item).strip()]
        if new_name == self.bot_name and new_aliases == self.bot_aliases:
            return
        self.bot_name = new_name
        self.bot_aliases = new_aliases
        if self.bot_name or self.bot_aliases:
            logger.debug("[心流] 身份：名字=%s | 别名=%s" % (self.bot_name or "未设置", "、".join(self.bot_aliases) or "无"))
