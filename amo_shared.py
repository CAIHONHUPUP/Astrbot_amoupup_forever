# -*- coding: utf-8 -*-
"""陪伴插件的共享底座：导入兜底 / 常量 / 工具函数 / 用量统计。

从 main.py 拆出来，main 与四个 mixin 都从这里取，避免循环 import。
"""
from __future__ import annotations

from typing import TYPE_CHECKING
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

import hashlib
import json
import os
import random
import re
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

# 统一时区：容器/服务器常在 UTC 跑，直接取服务器本地时间会把北京时间 10 点算成凌晨 2 点
# （「大晚上的」就是这么来的）。clock.now() 返回目标时区下的 naive 本地时间，
# 和原来的用法完全兼容。
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .clock import (
        now as _clock_now,
        from_timestamp as _clock_from_ts,
        configure as _clock_configure,
        current_name as _clock_name,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .clock import (
            now as _clock_now,
            from_timestamp as _clock_from_ts,
            configure as _clock_configure,
            current_name as _clock_name,
        )
    except Exception:
        try:
            from clock import (
                now as _clock_now,
                from_timestamp as _clock_from_ts,
                configure as _clock_configure,
                current_name as _clock_name,
            )
        except Exception:
            def _clock_now(*args, **kwargs):
                return datetime.now()

            def _clock_from_ts(ts, *args, **kwargs):
                return datetime.fromtimestamp(float(ts or 0))

            def _clock_configure(*args, **kwargs):
                return ""

            def _clock_name(*args, **kwargs):
                return ""



def _now() -> datetime:
    """目标时区下的「现在」（naive，便于和原代码里 datetime 运算混用）。"""
    return _clock_now()

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    import astrbot.api.message_components as Comp
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        import astrbot.api.message_components as Comp
    except Exception:
        Comp = None

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from astrbot.api.event import MessageChain
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from astrbot.api.event import MessageChain
    except Exception:
        try:
            from astrbot.core.message.message_event_result import MessageChain
        except Exception:
            MessageChain = None

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from astrbot.api.web import request as web_request
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from astrbot.api.web import request as web_request
    except Exception:
        try:
            from quart import request as web_request
        except Exception:
            web_request = None

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from astrbot.core.agent.message import TextPart
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from astrbot.core.agent.message import TextPart
    except Exception:
        TextPart = None

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .heartflow_engine import HeartflowEngine
    from .segment_engine import SegmentEngine
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .heartflow_engine import HeartflowEngine
        from .segment_engine import SegmentEngine
    except Exception:
        try:
            from heartflow_engine import HeartflowEngine
            from segment_engine import SegmentEngine
        except Exception:
            HeartflowEngine = None
            SegmentEngine = None
            try:
                import importlib.util
                _engine_dir = os.path.dirname(os.path.abspath(__file__))
                def _load_engine(module_name, class_name):
                    _spec = importlib.util.spec_from_file_location(
                        "amo_" + module_name,
                        os.path.join(_engine_dir, module_name + ".py"),
                    )
                    _module = importlib.util.module_from_spec(_spec)
                    _spec.loader.exec_module(_module)
                    return getattr(_module, class_name)
                HeartflowEngine = _load_engine("heartflow_engine", "HeartflowEngine")
                SegmentEngine = _load_engine("segment_engine", "SegmentEngine")
            except Exception as _engine_exc:
                logger.error("[amo] 内置心流/分段模块加载失败: %s" % _engine_exc)

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .weather_alert import evaluate as evaluate_weather_alert, parse_daily as parse_weather_daily, next_check_slot, parse_clock, slot_key, is_weather_query, forecast_brief
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .weather_alert import evaluate as evaluate_weather_alert, parse_daily as parse_weather_daily, next_check_slot, parse_clock, slot_key, is_weather_query, forecast_brief
    except Exception:
        try:
            from weather_alert import evaluate as evaluate_weather_alert, parse_daily as parse_weather_daily, next_check_slot, parse_clock, slot_key, is_weather_query, forecast_brief
        except Exception:
            def evaluate_weather_alert(*args, **kwargs):
                return ""
            def parse_weather_daily(*args, **kwargs):
                return []
            def next_check_slot(*args, **kwargs):
                return ""
            def parse_clock(*args, **kwargs):
                return 0
            def slot_key(today, slot):
                return "%s|%s" % (today, slot)
            def is_weather_query(*args, **kwargs):
                return False
            def forecast_brief(*args, **kwargs):
                return ""

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .gift_engine import GiftEngine
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .gift_engine import GiftEngine
    except Exception:
        try:
            from gift_engine import GiftEngine
        except Exception:
            GiftEngine = None

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .proactive_guard import analyze_rows as _pg_analyze_rows, compose_task as _pg_compose_task, near_duplicate as _pg_near_duplicate
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .proactive_guard import analyze_rows as _pg_analyze_rows, compose_task as _pg_compose_task, near_duplicate as _pg_near_duplicate
    except Exception:
        try:
            from proactive_guard import analyze_rows as _pg_analyze_rows, compose_task as _pg_compose_task, near_duplicate as _pg_near_duplicate
        except Exception:
            _pg_analyze_rows = _pg_compose_task = _pg_near_duplicate = None

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .time_recall import looks_like_time_query as _looks_like_time_query, render_time_block as _render_time_block
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .time_recall import looks_like_time_query as _looks_like_time_query, render_time_block as _render_time_block
    except Exception:
        try:
            from time_recall import looks_like_time_query as _looks_like_time_query, render_time_block as _render_time_block
        except Exception:
            def _looks_like_time_query(*args, **kwargs):
                return False

            def _render_time_block(*args, **kwargs):
                return ""

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .greet_route import greeting_targets as _greeting_targets, within_minutes as _within_minutes
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .greet_route import greeting_targets as _greeting_targets, within_minutes as _within_minutes
    except Exception:
        try:
            from greet_route import greeting_targets as _greeting_targets, within_minutes as _within_minutes
        except Exception:
            logger.warning("[amo] greet_route 载入失败，问候平台随机功能不可用")

            def _greeting_targets(candidates, primary="", randomize=True, rng=None):
                items = []
                for umo in list(candidates or []) + [primary]:
                    value = str(umo or "")
                    if value and value not in items:
                        items.append(value)
                return items if randomize else ([primary] if primary else [])

            def _within_minutes(last_ts, now, minutes):
                return False

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .diary_lines import (
        DIARY_WRITTEN_REPLIES as _DIARY_WRITTEN,
        DIARY_SHOW_REPLIES as _DIARY_SHOW,
        pick_hints as _diary_pick_hints,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .diary_lines import (
            DIARY_WRITTEN_REPLIES as _DIARY_WRITTEN,
            DIARY_SHOW_REPLIES as _DIARY_SHOW,
            pick_hints as _diary_pick_hints,
        )
    except Exception:
        try:
            from diary_lines import (
                DIARY_WRITTEN_REPLIES as _DIARY_WRITTEN,
                DIARY_SHOW_REPLIES as _DIARY_SHOW,
                pick_hints as _diary_pick_hints,
            )
        except Exception:
            _DIARY_WRITTEN = ["写了", "写了，不给你看", "写了，秘密"]
            _DIARY_SHOW = ["不行，你不能偷看我的日记哦", "那是我的日记，才不给你看", "你想得美"]

            def _diary_pick_hints(items, count=8, seed=""):
                return list(items)[:count]

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .diary_engine import (
        DEFAULT_DIR as _DIARY_DEFAULT_DIR,
        resolve_dir as _diary_resolve_dir,
        parse_day as _diary_parse_day,
        build_prompt as _diary_build_prompt,
        import_folder as _diary_import_folder,
        write_diary as _diary_write,
        read_diary as _diary_read,
        list_tree as _diary_tree,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .diary_engine import (
            DEFAULT_DIR as _DIARY_DEFAULT_DIR,
            resolve_dir as _diary_resolve_dir,
            parse_day as _diary_parse_day,
            build_prompt as _diary_build_prompt,
            import_folder as _diary_import_folder,
            write_diary as _diary_write,
            read_diary as _diary_read,
            list_tree as _diary_tree,
        )
    except Exception:
        try:
            from diary_engine import (
                DEFAULT_DIR as _DIARY_DEFAULT_DIR,
                resolve_dir as _diary_resolve_dir,
                parse_day as _diary_parse_day,
                build_prompt as _diary_build_prompt,
                import_folder as _diary_import_folder,
                write_diary as _diary_write,
                read_diary as _diary_read,
                list_tree as _diary_tree,
            )
        except Exception:
            _DIARY_DEFAULT_DIR = ""
            _diary_resolve_dir = None
            _diary_parse_day = _diary_write = _diary_read = _diary_tree = _diary_import_folder = None

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .milestone import (
        should_archive as _ms_should_archive,
        idle_archive_due as _ms_idle_due,
        transcript as _ms_transcript,
        build_prompt as _ms_build_prompt,
        build_content as _ms_build_content,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .milestone import (
            should_archive as _ms_should_archive,
            idle_archive_due as _ms_idle_due,
            transcript as _ms_transcript,
            build_prompt as _ms_build_prompt,
            build_content as _ms_build_content,
        )
    except Exception:
        try:
            from milestone import (
                should_archive as _ms_should_archive,
                idle_archive_due as _ms_idle_due,
                transcript as _ms_transcript,
                build_prompt as _ms_build_prompt,
                build_content as _ms_build_content,
            )
        except Exception:
            def _ms_should_archive(*args, **kwargs):
                return False

            def _ms_idle_due(*args, **kwargs):
                return False

            def _ms_transcript(*args, **kwargs):
                return ""

            def _ms_build_prompt(*args, **kwargs):
                return ""

            def _ms_build_content(*args, **kwargs):
                return ""

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .wander import (
        assess as _wander_assess,
        ask_limit as _wander_ask_limit,
        detect_agreement as _wander_agree,
        detect_question as _wander_question,
        detect_follow_invite as _follow_invite,
        detect_follow_refuse as _follow_refuse,
        detect_refusal as _wander_refuse,
        follow_agree_text as _wander_follow_agree_text,
        follow_block as _follow_block,
        offer_block as _wander_offer_block,
        offer_text as _wander_offer_text,
        pick_destination as _wander_pick,
        plan_text as _wander_plan_text,
        solo_agree_text as _wander_solo_agree_text,
        solo_block as _wander_solo_block,
        solo_refuse_text as _wander_solo_refuse_text,
        trip_days as _wander_trip_days,
        want_travel as _wander_want,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .wander import (
            assess as _wander_assess,
            ask_limit as _wander_ask_limit,
            detect_agreement as _wander_agree,
            detect_question as _wander_question,
            detect_follow_invite as _follow_invite,
            detect_follow_refuse as _follow_refuse,
            detect_refusal as _wander_refuse,
            follow_agree_text as _wander_follow_agree_text,
            follow_block as _follow_block,
            offer_block as _wander_offer_block,
            offer_text as _wander_offer_text,
            pick_destination as _wander_pick,
            plan_text as _wander_plan_text,
            solo_agree_text as _wander_solo_agree_text,
            solo_block as _wander_solo_block,
            solo_refuse_text as _wander_solo_refuse_text,
            trip_days as _wander_trip_days,
            want_travel as _wander_want,
        )
    except Exception:
        try:
            from wander import (
                assess as _wander_assess,
                ask_limit as _wander_ask_limit,
                detect_agreement as _wander_agree,
                detect_question as _wander_question,
                detect_follow_invite as _follow_invite,
                detect_follow_refuse as _follow_refuse,
                detect_refusal as _wander_refuse,
                follow_agree_text as _wander_follow_agree_text,
                follow_block as _follow_block,
                offer_block as _wander_offer_block,
                offer_text as _wander_offer_text,
                pick_destination as _wander_pick,
                plan_text as _wander_plan_text,
                solo_agree_text as _wander_solo_agree_text,
                solo_block as _wander_solo_block,
                solo_refuse_text as _wander_solo_refuse_text,
                trip_days as _wander_trip_days,
                want_travel as _wander_want,
            )
        except Exception:
            def _wander_assess(*args, **kwargs):
                return (False, False)

            def _wander_agree(*args, **kwargs):
                return False

            def _wander_question(*args, **kwargs):
                return False

            def _wander_refuse(*args, **kwargs):
                return False

            def _follow_block(*args, **kwargs):
                return ""

            def _follow_invite(*args, **kwargs):
                return False

            def _follow_refuse(*args, **kwargs):
                return False

            def _wander_follow_agree_text(*args, **kwargs):
                return ""

            def _wander_offer_block(*args, **kwargs):
                return ""

            def _wander_offer_text(*args, **kwargs):
                return ""

            def _wander_pick(*args, **kwargs):
                return ("", [])

            def _wander_plan_text(*args, **kwargs):
                return ""

            def _wander_ask_limit(*args, **kwargs):
                return 1

            def _wander_solo_agree_text(*args, **kwargs):
                return ""

            def _wander_solo_block(*args, **kwargs):
                return ""

            def _wander_solo_refuse_text(*args, **kwargs):
                return ""

            def _wander_trip_days(*args, **kwargs):
                return 1

            def _wander_want(*args, **kwargs):
                return False

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .memory_hooks import MemoryHub, render_recent_block
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .memory_hooks import MemoryHub, render_recent_block
    except Exception:
        try:
            from memory_hooks import MemoryHub, render_recent_block
        except Exception:
            MemoryHub = None
            render_recent_block = None

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .attribution import first_person_experience, attribution_leak, leak_tokens
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .attribution import first_person_experience, attribution_leak, leak_tokens
    except Exception:
        try:
            from attribution import first_person_experience, attribution_leak, leak_tokens
        except Exception:
            first_person_experience = attribution_leak = leak_tokens = None

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .brushoff import strip_brushoff
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .brushoff import strip_brushoff
    except Exception:
        try:
            from brushoff import strip_brushoff
        except Exception:
            strip_brushoff = None

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .relation_learn import parse_relations
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .relation_learn import parse_relations
    except Exception:
        try:
            from relation_learn import parse_relations
        except Exception:
            parse_relations = None

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .city_follow import (
        DEFAULT_CITY as _CITY_DEFAULT,
        detect_city as _city_detect,
        compose_block as _city_block,
        travel_block as _city_travel_block,
        travel_events as _city_travel_events,
        travel_action as _city_travel_action,
        travel_to_events as _city_travel_to_events,
        pick_spots as _city_pick_spots,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .city_follow import (
            DEFAULT_CITY as _CITY_DEFAULT,
            detect_city as _city_detect,
            compose_block as _city_block,
            travel_block as _city_travel_block,
            travel_events as _city_travel_events,
            travel_action as _city_travel_action,
            travel_to_events as _city_travel_to_events,
            pick_spots as _city_pick_spots,
        )
    except Exception:
        try:
            from city_follow import (
                DEFAULT_CITY as _CITY_DEFAULT,
                detect_city as _city_detect,
                compose_block as _city_block,
                travel_block as _city_travel_block,
                travel_events as _city_travel_events,
                travel_action as _city_travel_action,
                travel_to_events as _city_travel_to_events,
                pick_spots as _city_pick_spots,
            )
        except Exception:
            _CITY_DEFAULT = "东莞"

            def _city_pick_spots(*args, **kwargs):
                return []

            def _city_travel_to_events(*args, **kwargs):
                return []

            def _city_travel_action(*args, **kwargs):
                return "none"

            def _city_detect(*args, **kwargs):
                return None

            def _city_block(*args, **kwargs):
                return ""

            def _city_travel_block(*args, **kwargs):
                return ""

            def _city_travel_events(*args, **kwargs):
                return []


if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .relation_graph import rank_edges as _rank_edges
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .relation_graph import rank_edges as _rank_edges
    except Exception:
        try:
            from relation_graph import rank_edges as _rank_edges
        except Exception:
            _rank_edges = None

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .relation_sediment import plan_edges as plan_relation_edges
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .relation_sediment import plan_edges as plan_relation_edges
    except Exception:
        try:
            from relation_sediment import plan_edges as plan_relation_edges
        except Exception:
            plan_relation_edges = None

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .schedule_engine import ScheduleEngine
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .schedule_engine import ScheduleEngine
    except Exception:
        try:
            from schedule_engine import ScheduleEngine
        except Exception:
            try:
                import importlib.util as _importlib_util
                _schedule_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schedule_engine.py")
                _schedule_spec = _importlib_util.spec_from_file_location("amo_schedule_engine", _schedule_path)
                _schedule_module = _importlib_util.module_from_spec(_schedule_spec)
                _schedule_spec.loader.exec_module(_schedule_module)
                ScheduleEngine = getattr(_schedule_module, "ScheduleEngine")
            except Exception as _schedule_exc:
                ScheduleEngine = None
                logger.error("[amo] 日程引擎加载失败: %s" % _schedule_exc)


PLUGIN_NAME = "Astrbot_amoupup_forever"
PLUGIN_VERSION = "6.6.8"
DEFAULT_ADMIN = ""
LEGACY_DEFAULT_ADMIN = "3261748099"

RELATIONS = ("仇恨", "抵触", "普通", "熟识", "相恋")
DIALECT_SAMPLES = {
    "贵州话": "晓得、咋个、要得、安逸",
    "东北话": "咋整、唠嗑、得瑟、麻溜",
    "四川话": "啥子、莫得、巴适、搞快些",
    "广东话": "唔该、点解、得闲、搞掂",
    "台湾腔": "酱紫、蛮、超赞、拍谢",
    "天津话": "嘛呢、倍儿、哏儿、得嘞",
    "河南话": "弄啥嘞、中、得劲、木有",
    "上海话": "阿拉、侬、哪能、老灵",
}
CITY_DIALECT_RULES = (
    (("贵阳", "贵州", "遵义", "安顺", "六盘水", "凯里"), "贵州话"),
    (("成都", "四川", "重庆", "绵阳", "德阳", "宜宾"), "四川话"),
    (("广州", "深圳", "东莞", "佛山", "珠海", "中山", "惠州", "江门", "汕头", "广东"), "广东话"),
    (("上海", "苏州", "无锡", "常州", "昆山"), "上海话"),
    (("天津",), "天津话"),
    (("郑州", "河南", "洛阳", "开封"), "河南话"),
    (("哈尔滨", "沈阳", "长春", "大连", "东北", "辽宁", "吉林", "黑龙江"), "东北话"),
    (("台北", "台湾", "高雄", "台中"), "台湾腔"),
)


def dialect_for_city(city: Any) -> str:
    text = str(city or "")
    if not text:
        return ""
    for keys, style in CITY_DIALECT_RULES:
        if any(key in text for key in keys):
            return style
    return ""


DIALECT_FALLBACK_ROWS = {
    "东北话": [
        ("咋整", "怎么办", "咋整啊"), ("唠嗑", "聊天", "来唠嗑"), ("得瑟", "显摆", "别得瑟"),
        ("麻溜", "快点", "麻溜的"), ("贼拉", "非常", "贼拉冷"), ("嘎哈", "干什么", "你嘎哈呢"),
        ("老铁", "哥们", "老铁没毛病"),
    ],
    "四川话": [
        ("啥子", "什么", "你说啥子"), ("莫得", "没有", "莫得问题"), ("巴适", "舒服", "巴适得很"),
        ("搞快些", "快点", "搞快些"), ("瓜娃子", "傻孩子", "你个瓜娃子"), ("雄起", "加油", "雄起"),
        ("撇脱", "干脆", "撇脱点"),
    ],
    "广东话": [
        ("唔该", "谢谢、劳驾", "唔该晒"), ("点解", "为什么", "点解啊"), ("得闲", "有空", "得闲饮茶"),
        ("搞掂", "搞定", "搞掂啦"), ("靓仔", "帅哥", "靓仔"), ("靓女", "美女", "靓女"),
        ("冇", "没有", "冇问题"),
    ],
    "台湾腔": [
        ("酱紫", "这样", "酱紫啊"), ("蛮", "很", "蛮好的"), ("超赞", "非常棒", "超赞"),
        ("拍谢", "不好意思", "拍谢啦"), ("机车", "麻烦、烦人", "很机车"), ("阿嬷", "奶奶", "阿嬷"),
        ("龟毛", "挑剔", "别龟毛"),
    ],
    "天津话": [
        ("嘛呢", "干什么", "嘛呢"), ("倍儿", "非常", "倍儿好"), ("哏儿", "有趣", "真哏儿"),
        ("得嘞", "好的", "得嘞"), ("腻歪", "腻烦、黏人", "别腻歪"), ("幺蛾子", "麻烦", "出幺蛾子"),
        ("耐人", "招人喜欢", "真耐人"),
    ],
    "河南话": [
        ("弄啥嘞", "干什么", "你弄啥嘞"), ("中", "好", "中，就这么办"), ("得劲", "舒服", "真得劲"),
        ("木有", "没有", "木有事"), ("恁", "你", "恁吃饭没"), ("俺", "我", "俺知道了"),
        ("中不中", "行不行", "中不中"),
    ],
    "上海话": [
        ("阿拉", "我、我们", "阿拉晓得"), ("侬", "你", "侬好"), ("哪能", "怎么", "哪能办"),
        ("结棍", "厉害", "侬结棍"), ("老灵", "很好", "老灵额"), ("白相", "玩", "出去白相"),
        ("适意", "舒服", "适意"),
    ],
}
DIALECT_REGIONS = {
    "贵州话": "贵州",
    "东北话": "东北",
    "四川话": "四川",
    "广东话": "广东",
    "台湾腔": "台湾",
    "天津话": "天津",
    "河南话": "河南",
    "上海话": "上海",
}
SCHEDULE_VERSION = "backbone-v6"
NETWORK_RELATIONS = ("家人", "恋人", "朋友", "同事", "同学", "熟人", "陌生人", "仇人")
MOOD_KEYS = ("开心", "平静", "郁闷", "生气", "暴躁", "委屈", "疲惫", "焦虑", "兴奋", "依恋")
#: 真正算「生病」的状态；「疲惫」只是累，不该按病号排日程。
SICK_STATUSES = ("轻微不适", "感冒", "低烧", "发烧", "重感冒", "不舒服", "生病", "拉肚子", "肠胃炎", "咳嗽")
#: 每种病默认持续多久（小时）；生病不是一律 24 小时
SICK_DURATIONS = {
    "轻微不适": 12,
    "感冒": 60,
    "低烧": 36,
    "发烧": 84,
    "重感冒": 84,
    "肠胃炎": 36,
    "咳嗽": 60,
    "拉肚子": 36,
    "不舒服": 24,
    "生病": 48,
}
MOOD_BASE = {
    "开心": 55, "平静": 60, "郁闷": 18, "生气": 8, "暴躁": 6, "委屈": 20,
    "疲惫": 28, "焦虑": 18, "兴奋": 30, "依恋": 45,
}
STYLE_HINTS = {
    "soft": "整体语气温柔、软一点。",
    "cool": "整体语气清淡、克制一点。",
    "lively": "整体语气活泼、有精神：多用语气词（呀/啦/嘛/诶/哼哼）、短句、反问和玩笑，偶尔撒个小娇或吐槽；不要平铺直叙，也不要每句都完整规矩。",
    "mature": "整体语气成熟、稳一点。",
}

POS_WORDS = ("开心", "高兴", "哈哈", "笑死", "好耶", "喜欢", "爱你", "可爱", "棒", "不错",
             "太好了", "谢谢", "嘿嘿", "嘻嘻", "么么", "抱抱", "亲亲", "好吃", "好看")
NEG_WORDS = ("难过", "伤心", "哭", "烦", "累", "讨厌", "滚", "闭嘴", "无语", "失望",
             "委屈", "孤单", "想死", "崩溃", "气死", "不开心")
ANGRY_WORDS = ("滚", "闭嘴", "傻", "废物", "烦死", "气死", "去死", "恶心", "别烦", "有病")
SAD_WORDS = ("难过", "伤心", "想哭", "委屈", "孤单", "没人", "算了", "失望", "哭")
AFFECTION_WORDS = ("喜欢", "爱你", "想你", "抱抱", "亲亲", "贴贴", "宝贝", "老婆", "宝宝", "么么")
INSULT_WORDS = ("傻逼", "智障", "废物", "垃圾", "去死", "恶心", "滚")


# 关系网里的内部节点：bot/self 代表"我自己"，不是真实人物。
# 这种边只存在本地（_push_relation_to_data 不推给记忆终端），比较远端/本地时也要排掉，
# 否则远端永远比本地少一条，每 10 分钟误报一次"关系网不同步"。
INTERNAL_RELATION_NODES = ("bot", "self")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def now_ts() -> float:
    return time.time()


CN_HOLIDAYS = {
    "2025-01-01": "元旦",
    "2025-01-28": "除夕", "2025-01-29": "春节", "2025-01-30": "春节", "2025-01-31": "春节", "2025-02-01": "春节", "2025-02-02": "春节", "2025-02-03": "春节", "2025-02-04": "春节",
    "2025-04-04": "清明",
    "2025-05-01": "劳动节",
    "2025-05-31": "端午",
    "2025-10-01": "国庆", "2025-10-02": "国庆", "2025-10-03": "国庆", "2025-10-04": "国庆", "2025-10-05": "国庆", "2025-10-07": "国庆", "2025-10-08": "国庆",
    "2025-10-06": "国庆中秋",
    "2026-01-01": "元旦",
    "2026-02-16": "除夕", "2026-02-17": "春节", "2026-02-18": "春节", "2026-02-19": "春节", "2026-02-20": "春节", "2026-02-21": "春节", "2026-02-22": "春节", "2026-02-23": "春节",
    "2026-04-05": "清明",
    "2026-05-01": "劳动节",
    "2026-06-19": "端午",
    "2026-09-25": "中秋",
    "2026-10-01": "国庆", "2026-10-02": "国庆", "2026-10-03": "国庆", "2026-10-04": "国庆", "2026-10-05": "国庆", "2026-10-06": "国庆", "2026-10-07": "国庆",
    "2027-01-01": "元旦",
    "2027-02-05": "除夕", "2027-02-06": "春节", "2027-02-07": "春节", "2027-02-08": "春节", "2027-02-09": "春节", "2027-02-10": "春节", "2027-02-11": "春节",
    "2027-04-05": "清明",
    "2027-05-01": "劳动节",
    "2027-06-09": "端午",
    "2027-09-15": "中秋",
    "2027-10-01": "国庆", "2027-10-02": "国庆", "2027-10-03": "国庆", "2027-10-04": "国庆", "2027-10-05": "国庆", "2027-10-06": "国庆", "2027-10-07": "国庆",
}

# 2026 年主要假期的连续日期（用于判断长假/短假）
for _d in range(1, 4):
    CN_HOLIDAYS.setdefault("2026-01-%02d" % _d, "元旦")
for _d in range(4, 7):
    CN_HOLIDAYS.setdefault("2026-04-%02d" % _d, "清明")
for _d in range(1, 6):
    CN_HOLIDAYS.setdefault("2026-05-%02d" % _d, "劳动节")
for _d in range(19, 22):
    CN_HOLIDAYS.setdefault("2026-06-%02d" % _d, "端午")
for _d in range(25, 28):
    CN_HOLIDAYS.setdefault("2026-09-%02d" % _d, "中秋")

SOLAR_TERMS = {
    "2025-01-05": "小寒", "2025-01-20": "大寒", "2025-02-03": "立春", "2025-02-18": "雨水", "2025-03-05": "惊蛰", "2025-03-20": "春分", "2025-04-04": "清明", "2025-04-20": "谷雨", "2025-05-05": "立夏", "2025-05-21": "小满", "2025-06-05": "芒种", "2025-06-21": "夏至", "2025-07-07": "小暑", "2025-07-22": "大暑", "2025-08-07": "立秋", "2025-08-23": "处暑", "2025-09-07": "白露", "2025-09-23": "秋分", "2025-10-08": "寒露", "2025-10-23": "霜降", "2025-11-07": "立冬", "2025-11-22": "小雪", "2025-12-07": "大雪", "2025-12-21": "冬至",
    "2026-01-05": "小寒", "2026-01-20": "大寒", "2026-02-04": "立春", "2026-02-18": "雨水", "2026-03-05": "惊蛰", "2026-03-20": "春分", "2026-04-05": "清明", "2026-04-20": "谷雨", "2026-05-05": "立夏", "2026-05-21": "小满", "2026-06-05": "芒种", "2026-06-21": "夏至", "2026-07-07": "小暑", "2026-07-23": "大暑", "2026-08-07": "立秋", "2026-08-23": "处暑", "2026-09-07": "白露", "2026-09-23": "秋分", "2026-10-08": "寒露", "2026-10-23": "霜降", "2026-11-07": "立冬", "2026-11-22": "小雪", "2026-12-07": "大雪", "2026-12-21": "冬至",
    "2027-01-05": "小寒", "2027-01-20": "大寒", "2027-02-04": "立春", "2027-02-19": "雨水", "2027-03-06": "惊蛰", "2027-03-21": "春分", "2027-04-05": "清明", "2027-04-20": "谷雨", "2027-05-06": "立夏", "2027-05-21": "小满", "2027-06-06": "芒种", "2027-06-21": "夏至", "2027-07-07": "小暑", "2027-07-23": "大暑", "2027-08-08": "立秋", "2027-08-23": "处暑", "2027-09-08": "白露", "2027-09-23": "秋分", "2027-10-08": "寒露", "2027-10-24": "霜降", "2027-11-08": "立冬", "2027-11-22": "小雪", "2027-12-07": "大雪", "2027-12-22": "冬至",
}


def day_meta(day: Any = None) -> dict:
    """返回某天的节日、节气和是否休息日。"""
    try:
        target = date.fromisoformat(str(day)) if day else _now().date()
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.day_meta", _amo_exc)
        target = _now().date()
    key = target.isoformat()
    holiday = CN_HOLIDAYS.get(key, "")
    term = SOLAR_TERMS.get(key, "")
    weekend = target.weekday() >= 5
    rest = bool(weekend or holiday)
    holiday_days = 0
    if holiday:
        cursor = target
        while CN_HOLIDAYS.get(cursor.isoformat()) == holiday:
            holiday_days += 1
            cursor -= timedelta(days=1)
        cursor = target + timedelta(days=1)
        while CN_HOLIDAYS.get(cursor.isoformat()) == holiday:
            holiday_days += 1
            cursor += timedelta(days=1)
    label = "、".join(item for item in (holiday, term) if item)
    if holiday_days >= 2:
        label += (" · " if label else "") + "%d天假期" % holiday_days
    if not label and weekend:
        label = "周末"
    return {
        "date": key,
        "weekday": "一二三四五六日"[target.weekday()],
        "weekend": weekend,
        "holiday": holiday,
        "solar_term": term,
        "holiday_days": holiday_days,
        "rest": rest,
        "label": label,
    }

def lark_quote_kwargs(is_private: bool, message_id: Any, sender_id: Any, chat_id: Any, quote: Any) -> dict:
    """飞书发送参数：决定走「引用回复」还是普通发送。

    AstrBot 的飞书适配器在 `LarkMessageEvent.send()` 里固定传
    `reply_message_id=self.message_obj.message_id`，所以不管私聊群聊、也不管
    `platform_settings.reply_with_quote`，每条消息都会变成飞书里的「回复」，
    私聊看起来就是一直在引用他上一条。

    - 私聊：普通发送（receive_id = 他的 open_id），不引用；
    - 群聊且按概率判定要引用：reply；
    - 群聊但不引用：普通发送（receive_id = 会话 chat_id）；
    - 拿不到 open_id / chat_id 时退回原来的 reply，宁可引用也别发不出去。
    """
    message_id = str(message_id or "")
    sender_id = str(sender_id or "")
    chat_id = str(chat_id or "")
    if is_private and sender_id:
        return {
            "receive_id": sender_id,
            "receive_id_type": "open_id",
            "fallback_chat_id": chat_id or None,
        }
    if quote and message_id:
        return {"reply_message_id": message_id}
    if chat_id:
        return {"receive_id": chat_id, "receive_id_type": "chat_id"}
    if message_id:
        return {"reply_message_id": message_id}
    return {}


def today_str() -> str:
    return _now().date().isoformat()


def other_platform_bot_lines(rows: Any, umo: Any, limit: int = 3, platform: str = "") -> list:
    """从跨平台近聊里挑出「她自己说的、且不是当前会话」的几句话。

    主动消息以前只看当前会话的最近 12 句：QQ 上 08:48 说「等散会了跟你说」，
    之后一直没人说话，12:00 的主动消息就接着那场会往下说；与此同时飞书那边
    早就是「在便利店买饭」了。把她在别的平台刚说的话也放进去，两边才不会
    各讲各的故事（内容冲突时以当前状态为准，见 gen_proactive 的规则）。

    数据插件有的行没有 `umo`，所以再按 `platform` 兜一层，避免把本平台的
    话当成「另一边说过」。
    """
    umo = str(umo or "")
    platform = str(platform or "")
    out = []
    for row in rows or []:
        if str(row.get("role") or "") != "bot":
            continue
        if str(row.get("umo") or "") == umo:
            continue
        row_platform = str(row.get("platform") or "")
        if row_platform and platform and row_platform == platform:
            continue
        text = str(row.get("content") or "").strip().replace("\n", " ")
        if text:
            out.append(text)
    size = max(1, int(limit or 1))
    return out[-size:]


_CATEGORY_MOOD_CACHE = None


def schedule_category_mood() -> dict:
    """日程类别 → 每小时心情增量（复用 schedule_engine.CATEGORY_MOOD）。"""
    global _CATEGORY_MOOD_CACHE
    if _CATEGORY_MOOD_CACHE is not None:
        return _CATEGORY_MOOD_CACHE
    table: dict = {}
    try:
        from .schedule_engine import CATEGORY_MOOD as _category_mood
        table = dict(_category_mood or {})
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.schedule_category_mood", _amo_exc)
        try:
            from schedule_engine import CATEGORY_MOOD as _category_mood_abs
            table = dict(_category_mood_abs or {})
        except Exception as _amo_exc:
            _note_swallowed("amo_shared.schedule_category_mood", _amo_exc)
            table = {}
    _CATEGORY_MOOD_CACHE = dict(table)
    return _CATEGORY_MOOD_CACHE



def weekday_cn() -> str:
    return "一二三四五六日"[_now().date().weekday()]


def clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    try:
        value = float(value)
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.clamp", _amo_exc)
        return float(low)
    return max(float(low), min(float(high), value))


def to_int(value: Any, default: int = 0) -> int:
    # 空值不是异常：记忆行里没有 tier、配置没填，都该安静地用默认值。
    # 以前 None 会走 float(None) 抛 TypeError，被吞异常计数器记一笔，
    # 用户发一次 /amo memories 就在日志里看到一条「to_int 失败」的警告。
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.to_int", _amo_exc)
        return default


def to_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.to_float", _amo_exc)
        return default


def truncate(text: Any, limit: int = 120) -> str:
    text = str(text or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def md5_int(text: Any) -> int:
    return int(hashlib.md5(str(text).encode("utf-8")).hexdigest()[:8], 16)


def uniq(items: Iterable[Any]) -> list:
    out = []
    seen = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def parse_hhmm(value: Any, default: int = 0) -> int:
    try:
        parts = str(value).strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.parse_hhmm", _amo_exc)
        return default


def minutes_now() -> int:
    dt = _now()
    return dt.hour * 60 + dt.minute


def in_minute_range(cur: int, start: int, end: int) -> bool:
    if start == end:
        return True
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end


def quiet_now(spec: Any) -> bool:
    try:
        start, end = str(spec).split("-", 1)
        return in_minute_range(minutes_now(), parse_hhmm(start), parse_hhmm(end))
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.quiet_now", _amo_exc)
        return False


def looks_like_plain_command(text: Any) -> bool:
    text = (text or "").strip()
    return text.startswith("/") or text.startswith("／")


def has_question(text: Any) -> bool:
    if "?" in text or "？" in text:
        return True
    return any(word in text for word in (
        "吗", "呢", "怎么", "为什么", "啥", "什么", "哪", "多少", "几",
        "是不是", "能不能", "可不可以", "如何", "几点", "谁",
    ))


def sentiment(text: Any) -> dict:
    text = text or ""
    pos = sum(1 for word in POS_WORDS if word in text)
    neg = sum(1 for word in NEG_WORDS if word in text)
    angry = sum(1 for word in ANGRY_WORDS if word in text)
    sad = sum(1 for word in SAD_WORDS if word in text)
    aff = sum(1 for word in AFFECTION_WORDS if word in text)
    total = pos + neg
    score = 0.0 if total == 0 else (pos - neg) / float(total)
    return {
        "pos": pos, "neg": neg, "angry": angry, "sad": sad,
        "aff": aff, "insult": any(w in text for w in INSULT_WORDS),
        "score": score,
    }


_WORD_RE = re.compile(r"[a-zA-Z0-9_]{2,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def tokens(text: Any) -> set:
    text = (text or "").lower()
    out = set()
    for word in _WORD_RE.findall(text):
        out.add(word)
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            out.add(run)
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
        for i in range(len(run) - 2):
            out.add(run[i:i + 3])
    return out


def overlap(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(max(1, min(len(a), len(b))))


def json_extract_list(text: Any) -> list:
    """给“要一个数组”的场景用：json_extract 会先匹配 "{"，
    遇到 `[{...},{...}]` 只返回第一个对象，导致 extract_facts 永远拿到 dict → 返回 []。
    """
    if not text:
        return []
    raw = str(text).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    start, end = raw.find("["), raw.rfind("]")
    if start >= 0 and end > start:
        chunk = re.sub(r",\s*([}\]])", r"\1", raw[start:end + 1])
        try:
            data = json.loads(chunk)
            if isinstance(data, list):
                return data
        except Exception as _amo_exc:
            _note_swallowed("amo_shared.json_extract_list", _amo_exc)
    data = json_extract(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def json_extract(text: Any) -> Any:
    if not text:
        return None
    raw = str(text).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        if start < 0:
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(raw)):
            char = raw[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    chunk = raw[start:index + 1]
                    chunk = re.sub(r",\s*([}\]])", r"\1", chunk)
                    try:
                        return json.loads(chunk)
                    except Exception as _amo_exc:
                        _note_swallowed("amo_shared.json_extract", _amo_exc)
                        break
    return None


CHAT_FORMAT_NOISE = re.compile(
    r"@\s*([^\s（(]+)\s*[（(]\s*QQ\s*[:：]?\s*\d+\s*[）)]"
    r"|[（(]\s*QQ\s*[:：]?\s*\d+\s*[）)]"
    r"|\bQQ\s*[:：]?\s*\d{4,}\b"
    r"|@\s*([^\s，。！？!?、；;：:]+)",
    re.I,
)


def strip_chat_format(text: Any) -> str:
    """去掉模型复述的 @昵称(QQ号)、QQ号等后台格式，保留自然称呼。"""
    value = str(text or "")
    value = CHAT_FORMAT_NOISE.sub(lambda m: (m.group(1) or m.group(2) or "").strip(), value)
    value = re.sub(r"[（(]\s*[）)]", "", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    return value.strip()


def clean_reply(text: Any, limit: int = 300) -> str:
    if not text:
        return ""
    text = strip_chat_format(str(text).strip())
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"^(?:Amo|amo|核心|我|助手|assistant|AI|机器人)\s*[:：]\s*", "", text)
    if len(text) >= 2 and text[0] in "\"'“‘" and text[-1] in "\"'”’":
        text = text[1:-1]
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


# ---------------------------------------------------------------------------
# 分段器：书号、引号等成对符号内部绝对不切
# ---------------------------------------------------------------------------

_PAIRS = {
    "《": "》", "〈": "〉", "“": "”", "‘": "’", "「": "」", "『": "』",
    "（": "）", "(": ")", "【": "】", "〔": "〕", "[": "]", "{": "}",
}
_STRONG = "。！？!?…\n"
_WEAK = "，,、；;：:~～"
_TRIM = " \t\r\n。！？!?…，,、；;：:~～"


def _protect_mask(text):
    length = len(text)
    mask = [False] * length

    def mark(start, end):
        for index in range(max(0, start), min(length, end)):
            mask[index] = True

    for match in re.finditer(r"```.*?```", text, re.S):
        mark(match.start(), match.end())
    for match in re.finditer(r"`[^`\n]+`", text):
        mark(match.start(), match.end())
    for match in re.finditer(r"https?://[^\s]+", text):
        mark(match.start(), match.end())

    index = 0
    while index < length:
        char = text[index]
        closer = _PAIRS.get(char)
        if closer:
            depth = 1
            scan = index + 1
            while scan < length:
                if text[scan] == char:
                    depth += 1
                elif text[scan] == closer:
                    depth -= 1
                    if depth == 0:
                        break
                scan += 1
            if scan < length:
                mark(index, scan + 1)
                index = scan + 1
                continue
            mark(index, length)
            break
        index += 1
    return mask


def _cut(text, mask, delimiters, min_keep=0):
    parts = []
    start = 0
    for index, char in enumerate(text):
        if mask[index]:
            continue
        if char in delimiters and (index + 1 - start) >= min_keep:
            parts.append(text[start:index + 1])
            start = index + 1
    parts.append(text[start:])
    return parts


def _protected_ranges(text):
    mask = _protect_mask(text)
    ranges = []
    start = None
    for index, flag in enumerate(mask):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            ranges.append((start, index))
            start = None
    if start is not None:
        ranges.append((start, len(mask)))
    return ranges


def _mask_with_placeholders(text):
    ranges = _protected_ranges(text)
    if not ranges:
        return text, {}
    pieces = []
    placeholders = {}
    last = 0
    for index, (start, end) in enumerate(ranges):
        pieces.append(text[last:start])
        token = "\x00%d\x00" % index
        placeholders[token] = text[start:end]
        pieces.append(token)
        last = end
    pieces.append(text[last:])
    return "".join(pieces), placeholders


def _split_regex(text, pattern, protect_pairs=True):
    if not pattern:
        return None
    masked, placeholders = _mask_with_placeholders(text) if protect_pairs else (text, {})
    try:
        compiled = re.compile(pattern)
    except re.error:
        return None
    pieces = []
    last = 0
    for match in compiled.finditer(masked):
        end = match.end()
        if end <= last:
            continue
        pieces.append(masked[last:end])
        last = end
    pieces.append(masked[last:])
    restored = []
    for piece in pieces:
        value = piece
        for token, original in placeholders.items():
            value = value.replace(token, original)
        value = value.strip()
        if value:
            restored.append(value)
    return restored


def segment_text(text, keep_punc=True, min_len=6, max_len=42, max_segments=4,
                 mode="punct", split_chars="", regex_pattern="", protect_pairs=True):
    """按标点符号或正则分段；成对符号、代码块、URL 内部不会被切开。

    mode="punct" 时按 segment_split_chars + 内置标点分段；
    mode="regex" 时按 segment_regex 分段，失败自动回退到标点模式。
    """
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    min_len = max(1, to_int(min_len, 6))
    max_len = max(min_len + 1, to_int(max_len, 42))
    max_segments = max(1, to_int(max_segments, 4))

    pieces = None
    if str(mode).lower() == "regex" and regex_pattern:
        pieces = _split_regex(text, regex_pattern, protect_pairs)
    if pieces is None:
        extra = "".join(char for char in str(split_chars or "") if char not in "\r\n")
        strong = "".join(dict.fromkeys(_STRONG + extra))
        mask = _protect_mask(text) if protect_pairs else [False] * len(text)
        pieces = []
        for part in _cut(text, mask, strong):
            part = part.strip()
            if not part:
                continue
            if len(part) > max_len:
                sub_mask = _protect_mask(part) if protect_pairs else [False] * len(part)
                pieces.extend(_cut(part, sub_mask, _WEAK, min_keep=max(4, max_len // 2)))
            else:
                pieces.append(part)

    normalized = []
    for part in pieces or []:
        part = str(part).strip()
        if not part:
            continue
        if len(part) > max_len:
            sub_mask = _protect_mask(part) if protect_pairs else [False] * len(part)
            normalized.extend(_cut(part, sub_mask, _WEAK, min_keep=max(4, max_len // 2)))
        else:
            normalized.append(part)
    # 没有可断标点的超长块（长英文/数字串）按长度硬切，保证 max_len 真的生效；
    # 链接、成对符号、代码块内部不切，宁可超长也不打断。
    capped = []
    for part in normalized:
        if len(part) <= max_len or (protect_pairs and _protected_ranges(part)):
            capped.append(part)
            continue
        capped.extend(part[start:start + max_len] for start in range(0, len(part), max_len))
    pieces = capped

    segments = []
    for part in pieces:
        seg = part.strip()
        if not keep_punc:
            seg = seg.strip(_TRIM)
        if seg:
            segments.append(seg)
    if not segments:
        return []

    index = 0
    while len(segments) > 1 and index < len(segments):
        if len(segments[index]) < min_len:
            if index + 1 < len(segments):
                segments[index + 1] = segments[index] + segments[index + 1]
                del segments[index]
                continue
            segments[index - 1] = segments[index - 1] + segments[index]
            del segments[index]
            break
        index += 1

    while len(segments) > max_segments:
        best = 0
        best_len = None
        for cursor in range(len(segments) - 1):
            total = len(segments[cursor]) + len(segments[cursor + 1])
            if best_len is None or total < best_len:
                best_len = total
                best = cursor
        segments[best] = segments[best] + segments[best + 1]
        del segments[best + 1]

    return [seg for seg in segments if seg][:max_segments]


AI_ADMISSION_RE = re.compile(
    r"(?:我是|作为|毕竟|本人|本机)\s*(?:AI|人工智能|机器人|人机|程序|模型)"
    r"|(?:只是|不过是|就是个|只是个)\s*(?:AI|人工智能|机器人|人机|程序|模型)"
    r"|人机(?:也|当然|本来|就是)"
    r"|不是真人|虚拟(?:人|角色|存在)"
    r"|(?:我|咱|我们)(?:其实|本质上|说到底|严格来说)?(?:就是|不过是|只是个)"
    r"\s*(?:AI|人工智能|机器人|人机|程序|语言模型|大模型)",
    re.I,
)


def looks_like_ai_admission(text: Any) -> bool:
    return bool(AI_ADMISSION_RE.search(str(text or "")))


# 她不能报后台：模型名、厂商、插件名、API、提示词这类都不该出现在聊天里
MODEL_LEAK_RE = re.compile(
    r"(?:deepseek|openai|anthropic|claude|gemini|chatgpt|gpt-?[0-9]|qwen|通义|千问|"
    r"glm|chatglm|智谱|moonshot|kimi|doubao|豆包|ernie|文心|hunyuan|混元|llama|mistral|"
    r"grok|sonnet|opus|haiku|dp-?[0-9]|llm|大模型|语言模型|模型名称|api\s*key|"
    r"token|提示词|prompt|system\s*prompt|插件|astrbot)",
    re.I,
)


DEFENSIVE_REPLY_RE = re.compile(
    r"(?:你才|你才是|谁家|哪有|我才不是)[^。！？]{0,12}(?:人机|机器人|AI|人工智能)"
    r"|(?:人机|机器人)(?:才|也|怎么|哪有)",
    re.I,
)


def looks_like_defensive_reply(text: Any) -> bool:
    """是不是在跟人争“我不是人机”——技术讨论里这种话很突兀。"""
    return bool(DEFENSIVE_REPLY_RE.search(str(text or "")))


def looks_like_model_leak(text: Any) -> bool:
    """回复里有没有把模型名/厂商/插件/API 这类后台信息说出来。"""
    raw = str(text or "")
    if not raw:
        return False
    # 只有短回答或带“模型/接/用/是”这类上下文才算泄漏，避免误伤正常聊天
    if not MODEL_LEAK_RE.search(raw):
        return False
    if len(re.sub(r"\s+", "", raw)) > 60:
        return False
    return True


def typing_delay_ms(text: Any, base: int = 700, per_char: int = 400, cap: int = 20000) -> int:
    """按回复字数模拟打字时间，越长的回复停顿越久；标点再补一点。"""
    clean = re.sub(r"\s+", "", str(text or ""))
    chars = len(clean)
    pauses = (
        clean.count("。") + clean.count("！") + clean.count("？")
        + clean.count("!") + clean.count("?") + clean.count("…")
    )
    commas = clean.count("，") + clean.count(",") + clean.count("、")
    value: float = float(base + chars * per_char + pauses * 70 + commas * 35)
    value = min(value, float(cap))
    value = max(200, value * random.uniform(0.85, 1.2))
    return int(value)


# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------


# ---- 用量统计（读 AstrBot 的 data_v4.db provider_stats） ----
USAGE_PEAK_HOURS = ((9, 12), (14, 18))
USAGE_DEFAULT_PRICES = {
    "hit_peak": 0.04, "hit_idle": 0.02,
    "miss_peak": 2.0, "miss_idle": 1.0,
    "out_peak": 8.0, "out_idle": 4.0,
}


def usage_is_peak(ts: float) -> bool:
    """北京时间工作日 9-12、14-18 为高峰；周末全天算空闲（法定节假日未处理）。"""
    try:
        # 不能写死 +8：时区是可配的（clock.configure），服务器跑 UTC 时 +8 只是碰巧对
        dt = _clock_from_ts(ts)
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.usage_is_peak", _amo_exc)
        return False
    if dt.weekday() >= 5:
        return False
    return any(start <= dt.hour < end for start, end in USAGE_PEAK_HOURS)


def _usage_row_ts(start_time, created_at):
    try:
        value = float(start_time or 0)
        if value > 0:
            return value
    except Exception as _amo_exc:
        _note_swallowed("amo_shared._usage_row_ts", _amo_exc)
    try:
        dt = datetime.fromisoformat(str(created_at or "").strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception as _amo_exc:
        _note_swallowed("amo_shared._usage_row_ts", _amo_exc)
        return 0.0


def _empty_bucket():
    return {"calls": 0, "input_miss": 0, "input_cached": 0, "output": 0}


def summarize_token_usage(db_path: Any, start_ts: Any = None, end_ts: Any = None, model_filter: Any = None) -> dict:
    """按 provider_stats 汇总 token。start_ts/end_ts 为 epoch 秒，None 表示不限。"""
    usage = _empty_bucket()
    usage.update({"cache_rate": 0.0, "peak": _empty_bucket(), "idle": _empty_bucket(), "by_model": {}})
    if not db_path or not os.path.exists(db_path):
        usage["error"] = "找不到 AstrBot 数据库"
        return usage
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % str(db_path).replace("\\", "/"), uri=True)
    except Exception as exc:
        _note_swallowed("amo_shared.summarize_token_usage", exc)
        usage["error"] = "打开数据库失败: %s" % exc
        return usage
    try:
        rows = conn.execute(
            "SELECT provider_model, token_input_other, token_input_cached, token_output, "
            "start_time, created_at FROM provider_stats"
        ).fetchall()
    except Exception as exc:
        _note_swallowed("amo_shared.summarize_token_usage", exc)
        usage["error"] = "读取 provider_stats 失败: %s" % exc
        conn.close()
        return usage
    conn.close()

    for model, miss, cached, out, start_time, created_at in rows:
        model = str(model or "")
        if model_filter and model_filter not in model:
            continue
        ts = _usage_row_ts(start_time, created_at)
        if start_ts is not None and ts and ts < float(start_ts):
            continue
        if end_ts is not None and ts and ts >= float(end_ts):
            continue
        miss = int(miss or 0)
        cached = int(cached or 0)
        out = int(out or 0)
        usage["calls"] += 1
        usage["input_miss"] += miss
        usage["input_cached"] += cached
        usage["output"] += out
        bucket = usage["peak"] if usage_is_peak(ts) else usage["idle"]
        bucket["calls"] += 1
        bucket["input_miss"] += miss
        bucket["input_cached"] += cached
        bucket["output"] += out
        item = usage["by_model"].setdefault(model or "unknown", _empty_bucket())
        item["calls"] += 1
        item["input_miss"] += miss
        item["input_cached"] += cached
        item["output"] += out

    total_in = usage["input_miss"] + usage["input_cached"]
    usage["cache_rate"] = (usage["input_cached"] / total_in) if total_in else 0.0
    usage["by_model"] = [
        dict(model=key, **value) for key, value in
        sorted(usage["by_model"].items(), key=lambda kv: -(kv[1]["input_miss"] + kv[1]["input_cached"]))
    ]
    return usage


def usage_cost(usage: dict, prices: Any = None) -> float:
    """按空闲/高峰两档单价（元/百万 token）估算花费。"""
    table = dict(USAGE_DEFAULT_PRICES)
    if isinstance(prices, dict):
        for key, value in prices.items():
            try:
                table[key] = float(value)
            except (TypeError, ValueError):
                continue
    cost = 0.0
    for bucket, prefix in ((usage.get("peak") or {}, "peak"), (usage.get("idle") or {}, "idle")):
        cost += float(bucket.get("input_cached") or 0) * table["hit_" + prefix] / 1e6
        cost += float(bucket.get("input_miss") or 0) * table["miss_" + prefix] / 1e6
        cost += float(bucket.get("output") or 0) * table["out_" + prefix] / 1e6
    return round(cost, 6)


def fetch_provider_balance(api_key: str, base_url: str, kind: str, timeout: float = 8.0) -> dict:
    """尽力查询供应商余额；查不到就返回 {ok: False, error}，不抛异常。

    支持：DeepSeek（/user/balance）、SiliconFlow（/v1/user/info）、
    OpenAI 兼容中转（/v1/dashboard/billing/subscription + usage，one-api/new-api 常见）。
    """
    import urllib.request

    api_key = str(api_key or "").strip()
    base = str(base_url or "").rstrip("/")
    if not api_key or not base:
        return {"ok": False, "error": "缺密钥或地址"}

    def _request(url, headers=None):
        auth = dict(headers or {})
        auth.setdefault("Authorization", "Bearer " + api_key)
        auth.setdefault("Accept", "application/json")
        request = urllib.request.Request(url, headers=auth)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        if kind == "deepseek":
            payload = _request(base + "/user/balance", {"Accept": "application/json"})
            infos = payload.get("balance_infos") or []
            chosen = None
            for item in infos:
                if str(item.get("currency") or "").upper() == "CNY":
                    chosen = item
                    break
            if chosen is None and infos:
                chosen = infos[0]
            if not isinstance(chosen, dict):
                return {"ok": False, "error": "返回里没有余额信息"}

            def money(value):
                try:
                    return round(float(value), 2)
                except (TypeError, ValueError):
                    return None

            return {
                "ok": True,
                "available": bool(payload.get("is_available")),
                "currency": str(chosen.get("currency") or "CNY"),
                "amount": money(chosen.get("total_balance")),
                "granted": money(chosen.get("granted_balance")),
                "topped_up": money(chosen.get("topped_up_balance")),
            }
        if kind == "siliconflow":
            payload = _request(base + "/v1/user/info", {"Accept": "application/json"})
            data = payload.get("data") or {}
            amount = None
            for field in ("balance", "totalBalance"):
                raw = data.get(field)
                if raw is None:
                    continue
                try:
                    amount = round(float(raw), 2)
                    break
                except (TypeError, ValueError):
                    continue
            if amount is None:
                return {"ok": False, "error": "返回里没有余额字段"}
            return {"ok": True, "currency": "CNY", "amount": amount}
        # openai 兼容中转（one-api/new-api 常见）
        subscription = _request(base + "/v1/dashboard/billing/subscription")
        hard_limit = None
        try:
            hard_limit = float(subscription.get("hard_limit_usd"))
        except (TypeError, ValueError):
            hard_limit = None
        if hard_limit is None:
            return {"ok": False, "error": "返回里没有 hard_limit_usd"}
        today = _now()
        month_start = today.replace(day=1).strftime("%Y-%m-%d")
        end_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        used_cents = 0.0
        try:
            usage = _request(
                base + "/v1/dashboard/billing/usage?start_date=" + month_start + "&end_date=" + end_date
            )
            used_cents = float(usage.get("total_usage") or 0)
        except Exception as _amo_exc:
            _note_swallowed("amo_shared.fetch_provider_balance", _amo_exc)
            used_cents = 0.0
        remaining = round(hard_limit - used_cents / 100.0, 2)
        return {
            "ok": True,
            "currency": "USD",
            "amount": max(0.0, remaining),
            "used": round(used_cents / 100.0, 2),
            "limit": round(hard_limit, 2),
        }
    except Exception as exc:
        _note_swallowed("amo_shared.fetch_provider_balance", exc)
        return {"ok": False, "error": str(exc)[:120]}


def fetch_deepseek_balance(api_key: str, base_url: str = "https://api.deepseek.com", timeout: float = 8.0) -> dict:
    """查 DeepSeek 余额；失败返回 {ok: False, error}，不抛异常。

    解析逻辑和 ``fetch_provider_balance(kind="deepseek")`` 完全一致，这里只保留
    一个"没配 key 就直说"的前置判断，避免两处各写一份、改一处漏一处。
    """
    api_key = str(api_key or "").strip()
    if not api_key:
        return {"ok": False, "error": "未配置 API Key"}
    return fetch_provider_balance(
        api_key, base_url or "https://api.deepseek.com", "deepseek", timeout
    )


def merge_uid_alias_data(conn: Any, alias_map: Any) -> dict:
    """把别名 uid 的档案/记忆/消息/关系/称呼并进主 uid（一次性、幂等）。

    返回统计 dict，调用方负责记 kv 标记，避免每次启动重复跑。
    """
    report = {"profiles": 0, "memories": 0, "messages": 0, "proactive_log": 0,
              "relations": 0, "kv": 0}
    if not isinstance(alias_map, dict) or not alias_map:
        return report
    cur = conn.cursor()

    def table_columns(name):
        try:
            return [row[1] for row in cur.execute("PRAGMA table_info(%s)" % name)]
        except Exception as _amo_exc:
            _note_swallowed("amo_shared.merge_uid_alias_data.table_columns", _amo_exc)
            return []

    profile_cols = table_columns("profiles")
    if not profile_cols:
        return report

    def to_num(value, default=0.0):
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return default

    for alias_uid, canon_uid in list(alias_map.items()):
        alias_uid = str(alias_uid or "")
        canon_uid = str(canon_uid or "")
        if not alias_uid or not canon_uid or alias_uid == canon_uid:
            continue
        cur.execute("SELECT * FROM profiles WHERE uid=?", (alias_uid,))
        alias_raw = cur.fetchone()
        cur.execute("SELECT * FROM profiles WHERE uid=?", (canon_uid,))
        canon_raw = cur.fetchone()

        def as_dict(row):
            if row is None:
                return None
            if hasattr(row, "keys"):
                return dict(row)
            return {col[0]: value for col, value in zip(cur.description, row)}

        alias_row = as_dict(alias_raw)
        canon_row = as_dict(canon_raw)
        if alias_row is not None and canon_row is None:
            # 只有别名档案：直接改主键，历史原样保留
            cur.execute("UPDATE profiles SET uid=? WHERE uid=?", (canon_uid, alias_uid))
            report["profiles"] += 1
        elif alias_row is not None and canon_row is not None:
            a = alias_row
            b = canon_row
            merged = dict(b)
            merged["uid"] = canon_uid
            for key in ("name", "birthday", "alias", "notes", "platform"):
                if key not in profile_cols:
                    continue
                if key in ("alias", "notes"):
                    parts = []
                    for value in (b.get(key), a.get(key)):
                        text = str(value or "").strip()
                        if text and text not in parts:
                            parts.append(text)
                    merged[key] = " | ".join(parts)
                else:
                    merged[key] = b.get(key) or a.get(key) or ""
            if "relation" in profile_cols:
                canon_rel = str(b.get("relation") or "普通")
                alias_rel = str(a.get("relation") or "普通")
                merged["relation"] = canon_rel if canon_rel != "普通" else alias_rel
            for key in ("affection", "msg_count", "proactive_count", "is_admin"):
                if key in profile_cols:
                    merged[key] = max(int(to_num(b.get(key))), int(to_num(a.get(key))))
            for key in ("last_seen", "last_proactive", "relation_at"):
                if key in profile_cols:
                    merged[key] = max(to_num(b.get(key)), to_num(a.get(key)))
            if "first_seen" in profile_cols:
                values = [v for v in (to_num(b.get("first_seen")), to_num(a.get("first_seen"))) if v > 0]
                merged["first_seen"] = min(values) if values else None
            if "private_umo" in profile_cols:
                merged["private_umo"] = b.get("private_umo") or a.get("private_umo") or ""
            if "last_group" in profile_cols:
                merged["last_group"] = b.get("last_group") or a.get("last_group") or ""
            if "proactive_day" in profile_cols:
                merged["proactive_day"] = b.get("proactive_day") or a.get("proactive_day") or ""
            cols = [c for c in profile_cols if c in merged]
            cur.execute("DELETE FROM profiles WHERE uid=?", (alias_uid,))
            cur.execute(
                "INSERT OR REPLACE INTO profiles(%s) VALUES(%s)"
                % (",".join(cols), ",".join("?" * len(cols))),
                [merged.get(c) for c in cols],
            )
            report["profiles"] += 1

        # 消息：改归属 + 只对"别名带来的"那几条去重
        try:
            alias_msgs = [tuple(r) for r in cur.execute(
                "SELECT umo, role, content, created_at FROM messages WHERE uid=?", (alias_uid,))]
            cur.execute("UPDATE messages SET uid=? WHERE uid=?", (canon_uid, alias_uid))
            report["messages"] += cur.rowcount or 0
            for umo, role, content, created in alias_msgs:
                cur.execute(
                    "DELETE FROM messages WHERE umo=? AND uid=? AND role=? AND content=? AND created_at=? "
                    "AND rowid NOT IN (SELECT MIN(rowid) FROM messages WHERE umo=? AND uid=? AND role=? AND content=? AND created_at=?)",
                    (umo, canon_uid, role, content, created, umo, canon_uid, role, content, created),
                )
        except Exception as _amo_exc:
            _note_swallowed("amo_shared.merge_uid_alias_data", _amo_exc)

        # 主动记录
        try:
            alias_pro = [tuple(r) for r in cur.execute(
                "SELECT kind, content, created FROM proactive_log WHERE uid=?", (alias_uid,))]
            cur.execute("UPDATE proactive_log SET uid=? WHERE uid=?", (canon_uid, alias_uid))
            report["proactive_log"] += cur.rowcount or 0
            for kind, content, created in alias_pro:
                cur.execute(
                    "DELETE FROM proactive_log WHERE uid=? AND kind=? AND content=? AND created=? "
                    "AND rowid NOT IN (SELECT MIN(rowid) FROM proactive_log WHERE uid=? AND kind=? AND content=? AND created=?)",
                    (canon_uid, kind, content, created, canon_uid, kind, content, created),
                )
        except Exception as _amo_exc:
            _note_swallowed("amo_shared.merge_uid_alias_data", _amo_exc)

        # 关系网
        try:
            cur.execute("UPDATE OR IGNORE relation_network SET a_uid=? WHERE a_uid=?", (canon_uid, alias_uid))
            cur.execute("UPDATE OR IGNORE relation_network SET b_uid=? WHERE b_uid=?", (canon_uid, alias_uid))
            # OR IGNORE 遇到已存在的同一对关系会跳过，残留的别名行要显式删掉
            cur.execute("DELETE FROM relation_network WHERE a_uid=? OR b_uid=?", (alias_uid, alias_uid))
            # 两列各自 UPDATE 会把「a=friend,b=别名」变成「a=friend,b=主uid」，
            # 与另一条「a=主uid,b=friend」方向相反 → 同一条关系留两份。
            # 先按「无序对」去重（换方向时会撞唯一索引，必须先去重再换），再统一方向。
            cur.execute(
                "DELETE FROM relation_network WHERE rowid NOT IN (SELECT MIN(rowid) FROM relation_network "
                "GROUP BY MIN(a_uid, b_uid), MAX(a_uid, b_uid))"
            )
            cur.execute("UPDATE OR IGNORE relation_network SET a_uid=b_uid, b_uid=a_uid WHERE a_uid > b_uid")
            cur.execute("DELETE FROM relation_network WHERE a_uid=b_uid")
            cur.execute(
                "DELETE FROM relation_network WHERE rowid NOT IN "
                "(SELECT MIN(rowid) FROM relation_network GROUP BY a_uid, b_uid)"
            )
            report["relations"] += 1
        except Exception as _amo_exc:
            _note_swallowed("amo_shared.merge_uid_alias_data", _amo_exc)

        # kv：通道记录要合并成一份，其余带别名的键改名
        try:
            old_key = "channels:%s" % alias_uid
            new_key = "channels:%s" % canon_uid
            old_ch = cur.execute("SELECT value FROM kv WHERE key=?", (old_key,)).fetchone()
            new_ch = cur.execute("SELECT value FROM kv WHERE key=?", (new_key,)).fetchone()

            def load(raw):
                try:
                    return json.loads(raw[0] or "{}") if raw else {}
                except Exception as _amo_exc:
                    _note_swallowed("amo_shared.merge_uid_alias_data.load", _amo_exc)
                    return {}

            merged_ch = load(new_ch) if isinstance(load(new_ch), dict) else {}
            alias_ch = load(old_ch) if isinstance(load(old_ch), dict) else {}
            for platform, value in alias_ch.items():
                if not isinstance(value, dict):
                    continue
                current = merged_ch.get(platform)
                if not isinstance(current, dict) or to_num(value.get("ts")) > to_num(current.get("ts")):
                    merged_ch[platform] = value
            if merged_ch:
                cur.execute("UPDATE OR IGNORE kv SET key=? WHERE key=?", (new_key, old_key))
                cur.execute("DELETE FROM kv WHERE key=?", (old_key,))
                cur.execute("INSERT OR REPLACE INTO kv(key, value, updated) VALUES(?,?,?)",
                            (new_key, json.dumps(merged_ch, ensure_ascii=False), time.time()))
                report["kv"] += 1
            else:
                cur.execute("DELETE FROM kv WHERE key=?", (old_key,))
        except Exception as _amo_exc:
            _note_swallowed("amo_shared.merge_uid_alias_data", _amo_exc)
        try:
            # 用 Python 侧的包含判断，避免 LIKE 里 '_' 被当成通配符误伤别的键
            for row in list(cur.execute("SELECT key FROM kv")):
                key = str(row[0] or "")
                if alias_uid not in key:
                    continue
                new = key.replace(alias_uid, canon_uid)
                if new == key:
                    continue
                cur.execute("UPDATE OR IGNORE kv SET key=? WHERE key=?", (new, key))
                cur.execute("DELETE FROM kv WHERE key=?", (key,))
                report["kv"] += 1
        except Exception as _amo_exc:
            _note_swallowed("amo_shared.merge_uid_alias_data", _amo_exc)

    try:
        conn.commit()
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.merge_uid_alias_data", _amo_exc)
    return report





def extract_persona_name(persona: Any) -> str:
    """从 AstrBot Personality 的 name / system prompt 中取角色名。"""
    if not persona:
        return ""
    name = ""
    prompt = ""
    try:
        name = str(persona.get("name") or "").strip()
        prompt = str(persona.get("prompt") or "")
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.extract_persona_name", _amo_exc)
        name = str(getattr(persona, "name", "") or "").strip()
        prompt = str(getattr(persona, "prompt", "") or "")
    generic = {"default", "assistant", "系统默认", "默认", "bot", "ai"}
    if name and name.lower() not in generic and not re.fullmatch(r"[0-9a-fA-F-]{8,}", name):
        return name[:30]
    patterns = (
        r"你的名字(?:是|叫|为)\s*[「“\"']?([\u4e00-\u9fa5A-Za-z0-9_·]{1,16})",
        r"你叫(?:做)?\s*[「“\"']?([\u4e00-\u9fa5A-Za-z0-9_·]{1,16})",
        r"你名叫\s*[「“\"']?([\u4e00-\u9fa5A-Za-z0-9_·]{1,16})",
        r"名为\s*[「“\"']?([\u4e00-\u9fa5A-Za-z0-9_·]{1,16})",
        r"你是\s*[「“\"']?([\u4e00-\u9fa5A-Za-z0-9_·]{1,16})",
    )
    blocked = {"一个", "一名", "一位", "个人", "人", "你", "我", "他", "她", "这个", "那个",
               "ai助手", "助手", "机器人", "人工智能", "ai"}
    for pattern in patterns:
        for match in re.finditer(pattern, prompt):
            candidate = str(match.group(1) or "").strip("「」“”\"' ")
            if (
                candidate and candidate.lower() not in blocked
                and not candidate.startswith(("一个", "一名", "一位", "个", "人"))
            ):
                return candidate[:30]
    return ""

__all__ = [
    "_now", "Comp", "logger", "AstrMessageEvent", "filter", "Context", "Star", "MessageChain", "web_request", "TextPart", "HeartflowEngine", "SegmentEngine", "importlib", "_engine_dir", "_load_engine", "evaluate_weather_alert", "parse_weather_daily", "next_check_slot", "parse_clock", "slot_key", "is_weather_query", "forecast_brief", "GiftEngine", "_pg_analyze_rows", "_pg_compose_task", "_pg_near_duplicate", "_looks_like_time_query", "_render_time_block", "_greeting_targets", "_within_minutes", "_DIARY_WRITTEN", "_DIARY_SHOW", "_diary_pick_hints", "_DIARY_DEFAULT_DIR", "_diary_resolve_dir", "_diary_parse_day", "_diary_build_prompt", "_diary_import_folder", "_diary_write", "_diary_read", "_diary_tree", "_ms_should_archive", "_ms_transcript", "_ms_build_prompt", "_ms_build_content", "_wander_assess", "_wander_ask_limit", "_wander_agree", "_wander_question", "_follow_invite", "_follow_refuse", "_wander_refuse", "_wander_follow_agree_text", "_follow_block", "_wander_offer_block", "_wander_offer_text", "_wander_pick", "_wander_plan_text", "_wander_solo_agree_text", "_wander_solo_block", "_wander_solo_refuse_text", "_wander_trip_days", "_wander_want", "MemoryHub", "render_recent_block", "first_person_experience", "attribution_leak", "leak_tokens", "strip_brushoff", "parse_relations", "_CITY_DEFAULT", "_city_detect", "_city_block", "_city_travel_block", "_city_travel_events", "_city_travel_action", "_city_travel_to_events", "_city_pick_spots", "_rank_edges", "plan_relation_edges", "ScheduleEngine", "_importlib_util", "_schedule_path", "_schedule_spec", "_schedule_module", "PLUGIN_NAME", "PLUGIN_VERSION", "DEFAULT_ADMIN", "LEGACY_DEFAULT_ADMIN", "RELATIONS", "DIALECT_SAMPLES", "CITY_DIALECT_RULES", "dialect_for_city", "DIALECT_FALLBACK_ROWS", "DIALECT_REGIONS", "SCHEDULE_VERSION", "NETWORK_RELATIONS", "MOOD_KEYS", "SICK_STATUSES", "MOOD_BASE", "STYLE_HINTS", "POS_WORDS", "NEG_WORDS", "ANGRY_WORDS", "SAD_WORDS", "AFFECTION_WORDS", "INSULT_WORDS", "INTERNAL_RELATION_NODES", "now_ts", "CN_HOLIDAYS", "SOLAR_TERMS", "day_meta", "lark_quote_kwargs", "today_str", "other_platform_bot_lines", "_CATEGORY_MOOD_CACHE", "schedule_category_mood", "weekday_cn", "clamp", "to_int", "to_float", "truncate", "md5_int", "uniq", "parse_hhmm", "minutes_now", "in_minute_range", "quiet_now", "looks_like_plain_command", "has_question", "sentiment", "_WORD_RE", "_CJK_RE", "tokens", "overlap", "json_extract_list", "json_extract", "CHAT_FORMAT_NOISE", "strip_chat_format", "clean_reply", "_PAIRS", "_STRONG", "_WEAK", "_TRIM", "_protect_mask", "_cut", "_protected_ranges", "_mask_with_placeholders", "_split_regex", "segment_text", "AI_ADMISSION_RE", "looks_like_ai_admission", "MODEL_LEAK_RE", "DEFENSIVE_REPLY_RE", "looks_like_defensive_reply", "looks_like_model_leak", "typing_delay_ms", "USAGE_PEAK_HOURS", "USAGE_DEFAULT_PRICES", "usage_is_peak", "_usage_row_ts", "_empty_bucket", "summarize_token_usage", "usage_cost", "fetch_provider_balance", "fetch_deepseek_balance", "merge_uid_alias_data", "extract_persona_name",
]
