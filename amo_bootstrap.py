# -*- coding: utf-8 -*-
"""插件装配表：把 main.py 原来动态装配的 mixin / 引擎 / 日志器改成显式导入。

以前 main.py 用 globals()[_n] = load(amo_shared, ...) 这种写法：pyflakes 看不到这些名字，
只能在质量门里开白名单放行，名字拼错还得等运行时才炸。现在每个名字都是普通 import。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

try:  # 包模式（AstrBot 正常加载 / 单测按包导入）
    from .amo_api import ApiMixin
    from .amo_commands import CommandMixin
    from .amo_config import ConfigMixin
    from .amo_kb import KbMixin
    from .amo_llm import LlmMixin
    from .amo_memory import MemoryMixin
    from .amo_message import MessageMixin
    from .amo_proactive import ProactiveMixin
    from .amo_runtime import RuntimeMixin
    from .amo_schedule import ScheduleMixin
    from .amo_social import SocialMixin
    from .amo_travel import TravelMixin
    from .amo_usage import UsageMixin
    from .amo_feature_api import FeatureApiMixin
    from .amo_handshake import HandshakeMixin
    from .amo_init import InitMixin
    from .amo_lifecycle import LifecycleMixin
    from .clock import configure as _clock_configure, current_name as _clock_name
    from .amo_shared import Context, GiftEngine, SegmentEngine, Star, _now, clamp, has_question, logger, looks_like_plain_command, now_ts, overlap, quiet_now, sentiment, to_float, to_int, today_str, tokens, truncate, web_request
    from .amo_db import AmoDB
    from .amo_human_state import HumanState
    from .amo_hunger import HungerEngine
    from .amo_llm_service import LLMService
    from .draw_engine import DrawEngine
    from .photo_engine import PhotoEngine
    from .wardrobe import Wardrobe
    from .amo_events import EventBus, ScheduleChanged
    from .pose_library import PoseLibrary
    from .housing import Housing
    from .micro_events import MicroEventEngine
    from .amo_reply_gate import ReplyGate
    from .amo_routes import WebRouteMixin, WRITE_ROUTES, align_handler_module_paths as _align_handler_module_paths
    from .amo_observability import note as _note_swallowed
except ImportError:  # 按文件直接加载（顶层 sys.path 模式）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_api import ApiMixin
        from amo_commands import CommandMixin
        from amo_config import ConfigMixin
        from amo_kb import KbMixin
        from amo_llm import LlmMixin
        from amo_memory import MemoryMixin
        from amo_message import MessageMixin
        from amo_proactive import ProactiveMixin
        from amo_runtime import RuntimeMixin
        from amo_schedule import ScheduleMixin
        from amo_social import SocialMixin
        from amo_travel import TravelMixin
        from amo_usage import UsageMixin
        from amo_feature_api import FeatureApiMixin
        from amo_handshake import HandshakeMixin
        from amo_init import InitMixin
        from amo_lifecycle import LifecycleMixin
        from clock import configure as _clock_configure, current_name as _clock_name
        from amo_shared import Context, GiftEngine, SegmentEngine, Star, _now, clamp, has_question, logger, looks_like_plain_command, now_ts, overlap, quiet_now, sentiment, to_float, to_int, today_str, tokens, truncate, web_request
        from amo_db import AmoDB
        from amo_human_state import HumanState
        from amo_hunger import HungerEngine
        from amo_llm_service import LLMService
        from draw_engine import DrawEngine
        from photo_engine import PhotoEngine
        from wardrobe import Wardrobe
        from amo_events import EventBus, ScheduleChanged
        from pose_library import PoseLibrary
        from housing import Housing
        from micro_events import MicroEventEngine
        from amo_reply_gate import ReplyGate
        from amo_routes import WebRouteMixin, WRITE_ROUTES, align_handler_module_paths as _align_handler_module_paths
        from amo_observability import note as _note_swallowed


# 这些名字是 main.py 的装配入口，也是外部 import 的稳定出口
__all__ = [
    "AmoDB",
    "ApiMixin",
    "CommandMixin",
    "ConfigMixin",
    "Context",
    "DrawEngine",
    "EventBus",
    "FeatureApiMixin",
    "GiftEngine",
    "HandshakeMixin",
    "InitMixin",
    "LifecycleMixin",
    "Housing",
    "HumanState",
    "HungerEngine",
    "KbMixin",
    "LLMService",
    "LlmMixin",
    "MemoryMixin",
    "MessageMixin",
    "MicroEventEngine",
    "PhotoEngine",
    "PoseLibrary",
    "ProactiveMixin",
    "ReplyGate",
    "RuntimeMixin",
    "ScheduleChanged",
    "ScheduleMixin",
    "SegmentEngine",
    "SocialMixin",
    "Star",
    "TravelMixin",
    "UsageMixin",
    "WRITE_ROUTES",
    "Wardrobe",
    "WebRouteMixin",
    "_align_handler_module_paths",
    "_clock_configure",
    "_clock_name",
    "_note_swallowed",
    "_now",
    "clamp",
    "has_question",
    "logger",
    "looks_like_plain_command",
    "now_ts",
    "overlap",
    "quiet_now",
    "sentiment",
    "to_float",
    "to_int",
    "today_str",
    "tokens",
    "truncate",
    "web_request",
]


# 心流已移除：保留名字给旧调用方做 None 判断
HeartflowEngine = None
