# -*- coding: utf-8 -*-
"""共享底座·依赖兜底：可选组件导入 + 历史兼容名。从 amo_shared.py 拆出。"""
from __future__ import annotations

from typing import TYPE_CHECKING
import os
import time
from datetime import datetime
from astrbot.api import logger

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed


try:  # Phase 11：时间/数字纯函数唯一实现在 amo_numbers（旧名继续从这里可用）
    from .amo_numbers import (
        clamp,
        md5_int,
        parse_hhmm,
        to_float,
        to_int,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_numbers import (
            clamp,
            md5_int,
            parse_hhmm,
            to_float,
            to_int,
        )


# 统一时区：容器/服务器常在 UTC 跑，直接取服务器本地时间会把北京时间 10 点算成凌晨 2 点
# （「大晚上的」就是这么来的）。clock.now() 返回目标时区下的 naive 本地时间，
# 和原来的用法完全兼容。
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .clock import (
        now as _clock_now,
        now_ts as _clock_now_ts,
        from_timestamp as _clock_from_ts,
        configure as _clock_configure,
        current_name as _clock_name,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .clock import (
            now as _clock_now,
            now_ts as _clock_now_ts,
            from_timestamp as _clock_from_ts,
            configure as _clock_configure,
            current_name as _clock_name,
        )
    except Exception:
        try:
            from clock import (
                now as _clock_now,
                now_ts as _clock_now_ts,
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

            def _clock_now_ts():
                return time.time()


if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    import astrbot.api.message_components as Comp
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        import astrbot.api.message_components as Comp
    except Exception:
        Comp = None


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
        append_note as _diary_append_note,
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
            append_note as _diary_append_note,
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
                append_note as _diary_append_note,
                list_tree as _diary_tree,
            )
        except Exception:
            _DIARY_DEFAULT_DIR = ""
            _diary_resolve_dir = None
            _diary_parse_day = _diary_write = _diary_read = _diary_tree = _diary_import_folder = None
            _diary_append_note = None


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


__all__ = [
    "Comp",
    "GiftEngine",
    "HeartflowEngine",
    "MemoryHub",
    "MessageChain",
    "ScheduleEngine",
    "SegmentEngine",
    "TextPart",
    "_CITY_DEFAULT",
    "_DIARY_DEFAULT_DIR",
    "_DIARY_SHOW",
    "_DIARY_WRITTEN",
    "_city_block",
    "_city_detect",
    "_city_pick_spots",
    "_city_travel_action",
    "_city_travel_block",
    "_city_travel_events",
    "_city_travel_to_events",
    "_clock_configure",
    "_clock_from_ts",
    "_clock_name",
    "_clock_now",
    "_clock_now_ts",
    "_diary_append_note",
    "_diary_build_prompt",
    "_diary_import_folder",
    "_diary_parse_day",
    "_diary_pick_hints",
    "_diary_read",
    "_diary_resolve_dir",
    "_diary_tree",
    "_diary_write",
    "_follow_block",
    "_follow_invite",
    "_follow_refuse",
    "_greeting_targets",
    "_importlib_util",
    "_looks_like_time_query",
    "_ms_build_content",
    "_ms_build_prompt",
    "_ms_idle_due",
    "_ms_should_archive",
    "_ms_transcript",
    "_note_swallowed",
    "_pg_analyze_rows",
    "_pg_compose_task",
    "_pg_near_duplicate",
    "_rank_edges",
    "_render_time_block",
    "_wander_agree",
    "_wander_ask_limit",
    "_wander_assess",
    "_wander_follow_agree_text",
    "_wander_offer_block",
    "_wander_offer_text",
    "_wander_pick",
    "_wander_plan_text",
    "_wander_question",
    "_wander_refuse",
    "_wander_solo_agree_text",
    "_wander_solo_block",
    "_wander_solo_refuse_text",
    "_wander_trip_days",
    "_wander_want",
    "_within_minutes",
    "attribution_leak",
    "clamp",
    "evaluate_weather_alert",
    "first_person_experience",
    "forecast_brief",
    "importlib",
    "is_weather_query",
    "leak_tokens",
    "md5_int",
    "next_check_slot",
    "parse_clock",
    "parse_hhmm",
    "parse_relations",
    "parse_weather_daily",
    "plan_relation_edges",
    "render_recent_block",
    "slot_key",
    "strip_brushoff",
    "to_float",
    "to_int",
    "web_request",
]
