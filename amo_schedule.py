# -*- coding: utf-8 -*-
"""ScheduleMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""
from __future__ import annotations
from typing import Any, TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock


if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .schedule_change import (
        TRAVEL_WORDS,
        apply_change as apply_sched_change,
        detect_schedule_change,
        should_switch_to_pajamas,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .schedule_change import (
        TRAVEL_WORDS,
        apply_change as apply_sched_change,
        detect_schedule_change,
        should_switch_to_pajamas,
    )
    except ImportError:  # 直接按文件加载时
        try:
            from schedule_change import (
                TRAVEL_WORDS,
                apply_change as apply_sched_change,
                detect_schedule_change,
                should_switch_to_pajamas,
            )
        except ImportError:
            detect_schedule_change = None  # type: ignore[assignment]
            should_switch_to_pajamas = None  # type: ignore[assignment]
            TRAVEL_WORDS = ()  # type: ignore[assignment]
            apply_sched_change = None  # type: ignore[assignment]

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .diary_engine import ensure_private_ending as _diary_ensure_ending
else:
    try:
        from .diary_engine import ensure_private_ending as _diary_ensure_ending
    except ImportError:  # 直接按文件加载时
        try:
            from diary_engine import ensure_private_ending as _diary_ensure_ending
        except ImportError:
            _diary_ensure_ending = None  # type: ignore[assignment]

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_scope import private_rows as _private_rows
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_scope import private_rows as _private_rows
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_scope import private_rows as _private_rows


import asyncio
import os
import random
import re
import time
from datetime import date, datetime, timedelta
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        PLUGIN_NAME, _DIARY_DEFAULT_DIR, _diary_build_prompt, _diary_read, _diary_resolve_dir, _diary_write, _now, evaluate_weather_alert, forecast_brief, logger, next_check_slot, now_ts, parse_clock, slot_key, city_or_empty, to_float, today_str,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            PLUGIN_NAME, _DIARY_DEFAULT_DIR, _diary_build_prompt, _diary_read, _diary_resolve_dir, _diary_write, _now, evaluate_weather_alert, forecast_brief, logger, next_check_slot, now_ts, parse_clock, slot_key, city_or_empty, to_float, today_str,
        )
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_shared import (
                PLUGIN_NAME, _DIARY_DEFAULT_DIR, _diary_build_prompt, _diary_read, _diary_resolve_dir, _diary_write, _now, evaluate_weather_alert, forecast_brief, logger, next_check_slot, now_ts, parse_clock, slot_key, city_or_empty, to_float, today_str,
            )


if TYPE_CHECKING:  # 只给类型检查看：日期标签单一源（星期/节气/节日都由它生成）
    from .amo_shared_time import CN_HOLIDAYS, SOLAR_TERMS, day_label as _day_label_of
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared_time import CN_HOLIDAYS, SOLAR_TERMS, day_label as _day_label_of
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_shared_time import CN_HOLIDAYS, SOLAR_TERMS, day_label as _day_label_of


try:  # 块级小日记：块身份 / 开关 / 地点集合
    from .amo_state_common import block_diary_enabled, block_id, block_places
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_state_common import block_diary_enabled, block_id, block_places

try:  # 时间轴：块读取的唯一入口（第 1 阶段重构）
    from .amo_timeline import Timeline
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_timeline import Timeline


if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


#: 大日记同一天最多自动重写几次（只在「和事实矛盾」时重写；手动重写不占这个预算）
DIARY_REWRITE_LIMIT = 10


def diary_end_day(now, hh, mm):
    """这一轮该收尾到哪一天；早上型写昨天，旧配置（>=18:00）到点写当天。

    早上型（``hh < 18``，默认 08:00）：第二天任意时间都写昨天——开机/首次 tick 就补，
    不再限制 08:00-08:30 窗口，23:30 的宵夜自然算进昨天。
    旧配置（``hh >= 18``，例如 22:00）：到点后写当天，只是不含 23:30 之后的宵夜。
    """
    if int(hh) >= 18:
        if (now.hour, now.minute) < (int(hh), int(mm)):
            return None
        return now.date()
    return (now - timedelta(days=1)).date()


def diary_in_window(now, hh, mm, window_minutes=30):
    """现在是否还在「日记时间 + 窗口」内（默认次日 08:00-08:30）。

    2026-11-15 修：以前只有上界，00:10、10:00 这种「早于日记时间」也被判成在窗口内；
    schedule 变脏后的补写分支只判 `now >= 22:00`，于是 22:40、23:50 仍会重写当天日记，
    和「错过窗口当天不补写」的约定相反。现在上下界都要满足。
    """
    try:
        start = int(hh) * 60 + int(mm)
        span = max(0, int(window_minutes))
        current = now.hour * 60 + now.minute
        if start + span >= 24 * 60:
            # 跨零点的窗口（例如 23:45 + 30 分钟）
            end = (start + span) % (24 * 60)
            return current >= start or current <= end
        return start <= current <= start + span
    except Exception:  # cleanup: 日记容错
        return False


def diary_manual_target(now, requested):
    """面板手动写日记能写哪一天：只允许昨天及以前，当天和未来交给第二天自动写。

    返回 ``(day, error)``：能写返回 ``(date, "")``，不能写返回 ``(None, 原因)``。
    """
    yesterday = (now - timedelta(days=1)).date()
    if requested is None:
        return yesterday, ""
    if requested > yesterday:
        return None, "今天的日记第二天会自动写（第二天写昨天），手动只能补昨天及以前的"
    return requested, ""


#: 自动补写窗口：只允许「目标日 = 昨天」这一档（T+1）；更早的缺日只记 skipped。
DIARY_BACKFILL_WINDOW_DAYS = 1

#: 正文开头日期标签里可能出现的词（星期/节气/节日）；清洗时只吃这些词，正文不动。
_DIARY_LABEL_EXTRA_WORDS = frozenset(("周末", "节气", "节日", "假期"))
_DIARY_DATE_HEAD_RE = re.compile(r"^\s*(\d{4})年(\d{1,2})月(\d{1,2})日")
_DIARY_LABEL_TOKEN_RE = re.compile(r"[\s，,、·]*([^\s，,、·【】\[\]()（）:：]{1,8})")


def _known_label_words():
    """日期标签里允许出现的星期/节气/节日词表（来自 amo_shared_time 的唯一表）。"""
    words = set(_DIARY_LABEL_EXTRA_WORDS)
    try:
        words.update(str(item) for item in SOLAR_TERMS.values() if item)
        words.update(str(item) for item in CN_HOLIDAYS.values() if item)
    except Exception as _amo_exc:  # cleanup: 日期容错
        _note_swallowed("amo_schedule._known_label_words", _amo_exc)
    return words


def _normalize_diary_date_label(text, day):
    """把正文开头的日期标签统一成 target_day 的唯一标签（星期/节气/节日）。

    模型会自己写「2026年11月21日 星期六 小雪」，把第二天（11-22）的节气写到前一天。
    这里只替换「日期 + 星期 + 已知节气/节日」这一段标签，正文一个字不动；
    没有日期头就原样返回。标签文本只由 amo_shared_time.day_label(target_day) 生成。
    """
    value = str(text or "")
    target = _day_date_of(day)
    if target is None or not value:
        return value
    match = _DIARY_DATE_HEAD_RE.match(value)
    if not match:
        return value
    known = _known_label_words()
    pos = match.end()
    while True:
        token_match = _DIARY_LABEL_TOKEN_RE.match(value, pos)
        if not token_match:
            break
        token = token_match.group(1).strip("。.!！,，、·")
        if (token.startswith("星期") and len(token) <= 4) or token in known:
            pos = token_match.end()
            continue
        break
    body = value[pos:].lstrip(" \t\r\n，,、·。.!！")
    label = str(_day_label_of(target).get("text") or "").strip()
    if not label:
        return value
    return (label + " " + body) if body else label


def _record_diary_skip(plugin, day, now, reason="out_of_window"):
    """补写窗口外的缺日：只留审计标记，不生成正文（幂等，一天只记一次）。"""
    parsed = _day_date_of(day)
    if parsed is None:
        return False
    day_text = parsed.isoformat()
    key = "diary_skipped:" + day_text
    try:
        if plugin.db.kv_get(key, None):
            return False
        plugin.db.kv_set(key, {
            "reason": str(reason or "out_of_window"),
            "day": day_text,
            "skipped_at": now.strftime("%Y-%m-%dT%H:%M"),
            "window_days": int(DIARY_BACKFILL_WINDOW_DAYS),
        })
        logger.info("[DIARY_DEBUG] day=%s daily_skip=1 reason=%s window_days=%d",
                    day_text, reason, DIARY_BACKFILL_WINDOW_DAYS)
        return True
    except Exception as _amo_exc:
        _note_swallowed("amo_schedule._record_diary_skip", _amo_exc)
        return False


def _backfill_blocks(plugin, day, items, tasks, version, cap, slot_has_ended,
                     reason="slot_ended"):
    """扫 Timeline 的块：已结束且没有有效 block_diary 的才补，最多 cap 条。"""
    if cap <= 0 or not items:
        return 0
    check = getattr(plugin.db, "block_diary_is_valid", None)
    rows = Timeline.normalize(items, day)
    generated = 0
    # 从最近结束的块往前补：先补最新过完的那一块，再补更早的。
    for item in reversed(rows):
        if generated >= cap:
            break
        start = str(item.get("start") or "")
        if not slot_has_ended(day, start, item.get("end")):
            continue
        bid = block_id(day, item)
        try:
            if callable(check) and check(day, bid, version):
                continue
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.diary_backfill_blocks.check", _amo_exc)
            continue
        if (day, start) in tasks:
            continue
        plugin._spawn_diary_task(day, item, rows, version, reason=reason)
        generated += 1
    return generated


def _backfill_hours(plugin, day, items, tasks, version, cap, slot_has_ended):
    """[deprecated 回滚路径] 旧整点扫描；只有 block_diary_enabled=false 才会走到。

    第 1 阶段重构后正常路径一律走 Timeline 块；这里保留只为「一键回滚旧表」，
    新代码不得再调用。
    """
    generated = 0
    hourly = plugin.state._hourly_schedule(items)
    check = getattr(plugin.db, "diary_is_valid", None)
    for item in reversed(hourly):
        if generated >= cap:
            break
        start = str(item.get("start") or "")
        if not start or not slot_has_ended(day, start, item.get("end")):
            continue
        if callable(check) and check(day, start, version):
            continue
        if (day, start) in tasks:
            continue
        plugin._spawn_diary_task(day, item, hourly, version, reason="slot_ended")
        generated += 1
    return generated


def _backfill_rollover(plugin, now, tasks, slot_has_ended):
    """0 点后补昨天最后一段：块模式走 Timeline.last_ended，旧模式取 23:00 整点。

    旧整点分支是 [deprecated] 回滚路径，只在 block_diary_enabled=false 时使用。
    """
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    yitems = plugin.state.schedule.get(yesterday) or plugin.db.get_schedule(yesterday) or []
    if not yitems:
        return
    yversion = plugin.db.schedule_version(yesterday)
    if block_diary_enabled(plugin):
        timeline = Timeline(plugin)
        rows = timeline.blocks(yesterday)
        if not rows:
            return
        last = timeline.last_ended(yesterday, 24 * 60) or rows[-1]
        bid = block_id(yesterday, last)
        check = getattr(plugin.db, "block_diary_is_valid", None)
        try:
            if callable(check) and check(yesterday, bid, yversion):
                return
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.diary_rollover.check", _amo_exc)
        if (yesterday, str(last.get("start") or "")) not in tasks:
            plugin._spawn_diary_task(yesterday, last, rows, yversion, reason="day_rollover")
        return
    yhourly = plugin.state._hourly_schedule(yitems)
    check = getattr(plugin.db, "diary_is_valid", None)
    for item in yhourly:
        if str(item.get("start") or "") != "23:00":
            continue
        if callable(check) and check(yesterday, "23:00", yversion):
            break
        if (yesterday, "23:00") not in tasks:
            plugin._spawn_diary_task(
                yesterday, item, yhourly, yversion, reason="day_rollover",
            )
        break


#: 她台词里「我去了/回到 X」→ 当前段改成什么（只在公司内，zone 不变，不补通勤块）
OWN_PLACE_RULES = (
    (r"(回|坐回|走回|到)(了)?(工位|座位|办公桌|办公室)", "在工位上忙工作", "work"),
    (r"(去|到|回|进)(了)?(会议室|开会)", "在会议室开会", "work"),
    (r"(去|到|回|进)(了)?(茶水间|接水|泡茶|泡咖啡)", "在茶水间歇一会儿", "rest"),
    (r"(去|到|回|进)(了)?(食堂|餐厅|打饭)", "在公司食堂吃午饭", "meal"),
)


# ---------------- 日记事实 / 最近三天 digest（P0/P1，2026-11-22） ----------------

#: 事实池里算「这一天有内容」的类型（有这些就不该再暗示「今天非常平淡」）。
_DAY_FACT_TYPES = frozenset(
    ("日程", "活动", "物理场景", "线下约会", "状态", "天气", "做饭", "外卖", "消费", "收入"))


def _pool_has_day_facts(pool):
    """这一天素材里有没有真实内容（日程/身体/天气/消费），而不是纯空闲。"""
    if not isinstance(pool, dict):
        return False
    meta = pool.get("meta") or {}
    if meta.get("places") or meta.get("date_together"):
        return True
    for fact in pool.get("facts") or ():
        if isinstance(fact, dict) and str(fact.get("type") or "") in _DAY_FACT_TYPES:
            return True
    return False


def _pool_facts_text(pool):
    """素材包里的全部事实原文（做饭证据/互动清洗共用同一口径）。"""
    pool = pool if isinstance(pool, dict) else {}
    return " ".join(str((fact or {}).get("detail") or "")
                    for fact in (pool.get("facts") or ()) if isinstance(fact, dict))


def _day_has_overtime(plugin, day_text):
    """当天 18:30 后有没有非居家活动/加班块（与核对器 OVERTIME 同口径）。"""
    try:
        state = getattr(plugin, "state", None)
        rows = list((getattr(state, "schedule", None) or {}).get(day_text) or [])
        if not rows and getattr(plugin, "db", None) is not None:
            rows = list(plugin.db.get_schedule(day_text) or [])
    except Exception:  # cleanup: 日程读不到就按没有加班处理
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "加班" in str(row.get("activity") or ""):
            return True
        start = str(row.get("start") or "")
        place = str(row.get("place") or "")
        if place.startswith("东莞·"):
            place = place[len("东莞·"):]
        category = str(row.get("category") or "")
        if start >= "18:30" and place not in ("家", "出租屋", "卧室") \
                and category not in ("sleep", "life"):
            return True
    return False


def _block_digest_worthy(item):
    """这条日程块值不值得记进当天缓冲：出门/吃饭/约会/身体/社交才记，居家routine 不记。"""
    item = item if isinstance(item, dict) else {}
    zone = str(item.get("zone") or "")
    category = str(item.get("category") or "")
    activity = str(item.get("activity") or "")
    if category in ("meal", "date", "health", "social"):
        return True
    if zone and zone not in ("home", "sleep", "road"):
        return True
    return any(word in activity for word in ("吃", "做饭", "外卖", "约会", "医院", "奶茶", "咖啡"))


def _strip_fabricated_interaction_for_day(text, pool, day_text):
    """大日记清洗「素材里没有却声称今天发生」的互动；兜底句按日期轮换，跨天不复读。"""
    try:
        try:
            from .amo_diary_guard import strip_fabricated_interaction as _strip
        except ImportError:
            from amo_diary_guard import strip_fabricated_interaction as _strip
        pool = pool if isinstance(pool, dict) else {}
        facts_text = " ".join(
            str((fact or {}).get("detail") or "") for fact in (pool.get("facts") or []))
        has_chat = bool((pool.get("meta") or {}).get("has_chat"))
        return _strip(text, facts_text, has_chat, day=str(day_text or ""))
    except Exception as exc:  # cleanup: 兜底失败不能拖垮日记生成
        _note_swallowed("amo_schedule._strip_fabricated_interaction_for_day", exc)
        return text


def _strip_ungrounded_cooking_for_pool(text, pool):
    """素材里没有做饭证据时，去掉日记里的强做饭断言（守卫失败不能拖垮日记）。"""
    try:
        try:
            from .amo_diary_guard import strip_ungrounded_cooking as _strip
        except ImportError:
            from amo_diary_guard import strip_ungrounded_cooking as _strip
        return _strip(text, _pool_facts_text(pool))
    except Exception as exc:  # cleanup: 兜底失败不能拖垮日记生成
        _note_swallowed("amo_schedule._strip_ungrounded_cooking_for_pool", exc)
        return text


def _strip_ungrounded_spending_for_pool(text, pool):
    """素材里没有消费事实时，去掉日记里的花钱断言（守卫失败不能拖垮日记）。"""
    try:
        try:
            from .amo_diary_guard import strip_ungrounded_spending as _strip
        except ImportError:
            from amo_diary_guard import strip_ungrounded_spending as _strip
        return _strip(text, _pool_facts_text(pool))
    except Exception as exc:  # cleanup: 兜底失败不能拖垮日记生成
        _note_swallowed("amo_schedule._strip_ungrounded_spending_for_pool", exc)
        return text


async def _diary_day_fact_lines(plugin, day):
    """一天里值得进「最近三天」digest 的短事实（最多 6 条，覆盖出门/三餐/身体/天气/熬夜）。

    只走 amo_schedule 已有的依赖：日程块用 Timeline（唯一块读入口），身体/天气/出门
    这类事实用 amo_fact_pool 的统一事实池（它本来就由 amo_fact_timeline 生产）。
    """
    day_text = day.isoformat() if hasattr(day, "isoformat") else str(day)[:10]
    try:
        items = [row for row in (Timeline(plugin).blocks(day_text) or [])
                 if isinstance(row, dict)]
    except Exception as exc:
        _note_swallowed("amo_schedule._diary_day_fact_lines.items", exc)
        items = []
    lines = []
    places = []
    for row in items:
        place = str(row.get("place") or "").strip()
        zone = str(row.get("zone") or "").strip()
        short = place.split("·")[-1] if place else ""
        if not short or zone in ("home", "sleep", "road"):
            continue
        if short in ("家", "出租屋", "卧室", "路上", "床上", "被窝"):
            continue
        if short not in places:
            places.append(short)
    if places:
        lines.append("今天去了" + "、".join(places[:3]))
    for row in [row for row in items if str(row.get("category") or "") == "meal"][:2]:
        activity = str(row.get("activity") or "").strip()
        if activity:
            lines.append(activity[:40])
    facts: list = []
    try:
        try:
            from .amo_fact_pool import build_fact_pool
        except ImportError:
            from amo_fact_pool import build_fact_pool
        facts = (await build_fact_pool(plugin, day)).get("facts") or []
    except Exception as exc:
        _note_swallowed("amo_schedule._diary_day_fact_lines.pool", exc)
        facts = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        kind = str(fact.get("type") or "")
        detail = str(fact.get("detail") or "").strip()
        if kind == "状态" and detail.startswith("身体："):
            status = detail[len("身体："):].split("（")[0].strip()
            if status:
                lines.append("今天有点%s" % status)
        elif kind == "天气" and detail.startswith("天气："):
            weather_text = re.sub(r"（[^）]*）$", "", detail[len("天气："):].strip()).strip()
            if weather_text:
                lines.append("今天" + weather_text)
    for row in reversed(items):
        if str(row.get("start") or "") >= "22:00" and str(row.get("category") or "") != "sleep":
            activity = str(row.get("activity") or "").strip()
            if activity:
                lines.append("晚上还在" + activity[:16])
            break
    out = []
    for line in lines:
        line = str(line or "").strip()[:80]
        if line and line not in out:
            out.append(line)
    return out[:6]


def _merge_episodic_fact(plugin, text, day_text, key, ttl_hours=48):
    """没有 note_recent_topic 的宿主/测试替身：用同一口径直接合并当天缓冲。"""
    try:
        from .amo_episodic import merge as _merge
    except ImportError:
        from amo_episodic import merge as _merge
    stamp = now_ts()
    items = plugin.db.kv_get("episodic_memory", []) or []
    items = _merge(items, text, stamp + int(ttl_hours) * 3600, key=key, now=stamp, day=day_text)
    plugin.db.kv_set("episodic_memory", items)


def _block_key_of(plugin, day_text, item):
    """块身份：生产插件走 _block_key，测试替身退回 block_id/start。"""
    getter = getattr(plugin, "_block_key", None)
    if callable(getter):
        try:
            value = str(getter(day_text, item) or "")
            if value:
                return value
        except Exception:  # cleanup: 日记容错
            pass
    try:
        return str(block_id(day_text, item) or "")
    except Exception:  # cleanup: 日记容错
        return str((item or {}).get("start") or "")


def _note_diary_episodic(plugin, day, item, text):
    """块小日记落库后记一条当天缓冲事实（最近三天 digest 的生产写入方，P0）。

    以前 note_recent_topic 只有定义、没有任何生产调用方，episodic_memory 恒空，
    day_digest 恒空，【前几天】注入恒空。这里把「出门/吃饭/约会/身体」这类
    值得追问的块事实按当天记进缓冲；第二天补写大日记时由 _day_digest 折成
    day_digest:<day>，之后三天都能读到。
    """
    try:
        body = str(text or "").strip()
        if not body or not _block_digest_worthy(item):
            return
        day_text = day.isoformat() if hasattr(day, "isoformat") else str(day)[:10]
        key = "block:%s:%s" % (day_text, _block_key_of(plugin, day_text, item))
        writer = getattr(plugin, "note_recent_topic", None)
        if callable(writer):
            plugin.note_recent_topic(body[:80], ttl_hours=48, day=day_text, key=key)
        else:
            _merge_episodic_fact(plugin, body[:80], day_text, key)
    except Exception as exc:
        _note_swallowed("amo_schedule._note_diary_episodic", exc)


#: 「最近三天」里每条 day_digest 的安全上限：3 条 + 前缀/标签/分隔符必须 < 240 字。
#: 3 * 72 + len("【前几天】") + 3 * len("昨天：") + 2 = 232 < 240，留 8 字余量。
DIGEST_DAY_MAX_CHARS = 72


def _day_date_of(day):
    """日期参数（date/datetime/ISO 字符串）→ date；解析不了一律 None。"""
    if isinstance(day, datetime):
        return day.date()
    if isinstance(day, date):
        return day
    try:
        return date.fromisoformat(str(day)[:10])
    except (TypeError, ValueError):  # cleanup: 日期容错
        return None


def _day_text_of(day):
    parsed = _day_date_of(day)
    return parsed.isoformat() if parsed else ""


def _row_timestamp(row):
    """聊天/账本行的时间戳（秒）；取不到返回 0（老数据没有时间字段时不凭空丢）。"""
    if not isinstance(row, dict):
        return 0.0
    for key in ("created_at", "ts", "timestamp", "time"):
        try:
            value = float(row.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _digest_fragments(digest):
    """day_digest → 单条事实片段（「；」分隔，去空）。"""
    return [piece.strip() for piece in str(digest or "").split("；") if piece.strip()]


def _clip_day_digest(digest, limit=DIGEST_DAY_MAX_CHARS):
    """按「；」边界把 day_digest 收到 limit 字内：不切断单条事实，保证三天总量 < 240。"""
    pieces = []
    total = 0
    for piece in _digest_fragments(digest):
        if not pieces:
            pieces.append(piece[:limit])
            total = len(pieces[0])
            continue
        if total + 1 + len(piece) > limit:
            break
        pieces.append(piece)
        total += 1 + len(piece)
    return "；".join(pieces)


def _digest_history(plugin, day):
    """前两天已存的 day_digest（跨天去重口径：同一事实三天里只说一次）。"""
    parsed = _day_date_of(day)
    if parsed is None:
        return []
    history = []
    for offset in (1, 2):
        previous = (parsed - timedelta(days=offset)).isoformat()
        try:
            stored = str(plugin.db.kv_get("day_digest:" + previous, "") or "")
        except Exception:  # cleanup: 日记容错
            stored = ""
        if stored:
            history.append(stored)
    return history


def _dedupe_day_digest(plugin, day, digest):
    """跨天去重 + 上限裁剪：前两天的 digest 里出现过的事实不再重复注入。"""
    digest = str(digest or "")
    history = _digest_history(plugin, day)
    if history:
        known = set()
        for stored in history:
            known.update(_digest_fragments(stored))
        pieces = [piece for piece in _digest_fragments(digest)
                  if piece not in known
                  and not any(piece in stored for stored in history)]
        if pieces:
            digest = "；".join(pieces)
    return _clip_day_digest(digest)


async def _seed_day_digest(plugin, day):
    """大日记定稿后把这一天的事实补进缓冲并折成 day_digest（P0，2026-11-22）。

    生产里主要靠块小日记边写边记（_note_diary_episodic）；但块小日记没跑、
    数据插件没接、或首次启用时，也要保证 day_digest:<day> 不是空的，
    否则后几天的【前几天】一直是空的。这里用当天事实兜一层，写入后立刻折叠。
    """
    try:
        day_text = day.isoformat() if hasattr(day, "isoformat") else str(day)[:10]
    except Exception:  # cleanup: 日记容错
        return
    if not day_text:
        return
    try:
        if str(plugin.db.kv_get("day_digest:" + day_text, "") or "").strip():
            return
    except Exception:  # cleanup: 日记容错
        pass
    try:
        lines = await _diary_day_fact_lines(plugin, day)
    except Exception as exc:
        _note_swallowed("amo_schedule._seed_day_digest.lines", exc)
        lines = []
    for index, line in enumerate(lines):
        key = "day:%s:%d" % (day_text, index)
        try:
            writer = getattr(plugin, "note_recent_topic", None)
            if callable(writer):
                plugin.note_recent_topic(line, ttl_hours=48, day=day_text, key=key)
            else:
                _merge_episodic_fact(plugin, line, day_text, key)
        except Exception as exc:
            _note_swallowed("amo_schedule._seed_day_digest.note", exc)
    folder = getattr(plugin, "_day_digest", None)
    if not callable(folder):
        return
    try:
        digest, keep, had = folder(day_text)
    except Exception as exc:
        _note_swallowed("amo_schedule._seed_day_digest.fold", exc)
        return
    if not digest:
        return
    # 跨天去重 + 上限裁剪：前两天的 digest 已经说过的事实不再重复，三天总量压进 240 字。
    digest = _dedupe_day_digest(plugin, day, digest)
    if not digest:
        return
    try:
        plugin.db.kv_set("day_digest:" + day_text, digest)
        if had:
            plugin.db.kv_set("episodic_memory", keep)
        logger.info("[DIARY_DEBUG] day=%s day_digest=%d chars", day_text, len(digest))
    except Exception as exc:
        _note_swallowed("amo_schedule._seed_day_digest.save", exc)


class ScheduleMixin(_MixinBase):
    def _schedule_change_allowed(self, event) -> bool:
        """改日程是会落库的副作用：只认老公的私聊，群聊/其他人不算。"""
        try:
            if not self.cfg_bool("schedule_change_enabled", True):
                return False
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._schedule_change_allowed", _amo_exc)
        try:
            if not event.is_private_chat():
                return False
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._schedule_change_allowed", _amo_exc)
            return False
        owner_ids = {
            item.strip()
            for item in re.split(r"[,，\s]+", self.cfg_str("schedule_change_owner_ids", "") or "")
            if item.strip()
        }
        if owner_ids:
            uid = ""
            try:
                uid = str(self._canonical_uid(event) or "")
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule._schedule_change_allowed", _amo_exc)
            sender = ""
            try:
                sender = str(event.get_sender_id() or "")
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule._schedule_change_allowed", _amo_exc)
            return uid in owner_ids or sender in owner_ids
        # 没单独配名单时，沿用礼物引擎的老公判定（gift_owner_ids / 恋人关系 / 管理员）
        gift = getattr(self, "gift", None)
        checker = getattr(gift, "is_owner", None) if gift is not None else None
        if callable(checker):
            try:
                return bool(checker(event))
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule._schedule_change_allowed", _amo_exc)
        # 既没配名单、也没礼物引擎时，退回「只认她的主页私聊」
        home_umo = ""
        try:
            getter = getattr(self, "_first_private_umo", None)
            if callable(getter):
                home_umo = str(getter() or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._schedule_change_allowed", _amo_exc)
        if home_umo:
            try:
                return str(getattr(event, "unified_msg_origin", "") or "") == home_umo
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule._schedule_change_allowed", _amo_exc)
                return False
        return True

    async def _judge_schedule_proposal(self, event, change, items):
        """把用户提议交给 LLM：同意才 update_schedule，拒绝就保持原计划。"""
        current: dict[str, Any] = {}
        try:
            current = self.state.current_activity() or {}
        except Exception:  # cleanup: 日记容错
            current: dict[str, Any] = {}
        try:
            cash = int(self.state.p.wallet.balance())
        except Exception:  # cleanup: 日记容错
            cash = 0
        try:
            mood = self.state.mood_words()
        except Exception:  # cleanup: 日记容错
            mood = ""
        proposal = {
            "activity": str(change.get("activity") or ""),
            "place": str(change.get("place") or ""),
            "category": str(change.get("category") or ""),
        }
        llm = getattr(self, "llm", None)
        if llm is None or not hasattr(llm, "chat_raw"):
            return {"agree": False, "reason": "没法判断，先按原计划", "fallback": True}
        system = (
            "你是角色的日程决策器。只输出 JSON，不要解释。\n"
            "工具：update_schedule(reason) 同意改；keep_schedule(reason) 拒绝并维持原计划。\n"
            "输出 {\"tool\":\"update_schedule\",\"reason\":\"...\"} 或 "
            "{\"tool\":\"keep_schedule\",\"reason\":\"...\"}。"
        )
        prompt = (
            "你此刻：%s（%s），心情：%s，余额：%.2f 元。\n"
            "他提议把日程改成：%s（%s，%s）。\n"
            "结合你的疲惫、心情、余额和想不想去，自主决定同意还是拒绝。"
        ) % (current.get("activity") or "休息", current.get("place") or "",
             mood, cash / 100.0, proposal["activity"], proposal["place"],
             proposal["category"])
        try:
            raw = await llm.chat_raw(
                str(getattr(event, "unified_msg_origin", "") or ""), system, prompt)
            import json as _json
            import re as _re
            match = _re.search(r"\{.*\}", str(raw or ""), _re.S)
            data = _json.loads(match.group(0)) if match else {}
            tool = str((data or {}).get("tool") or (data or {}).get("action") or "")
            return {"agree": tool == "update_schedule",
                    "reason": str((data or {}).get("reason") or "")[:120],
                    "fallback": False}
        except Exception as exc:
            logger.warning("[amo] 日程提议决策失败，保持原计划: %s" % exc)
            return {"agree": False, "reason": "没想好，先不改", "fallback": True}

    async def _apply_user_schedule_change(self, event, user_text: str) -> None:
        """用户明确安排了行程（来接我 / 一起睡 / 出去吃），把那一段日程改掉。"""
        if detect_schedule_change is None or apply_sched_change is None:
            logger.debug("[SCHED_CHANGE] skip reason=helper_missing")
            return
        try:
            # 先做便宜的关键词识别，命中再查老公（is_owner 会读一次画像库，
            # 放前面等于每条私聊都多一次 DB 查询）。
            change = detect_schedule_change(user_text)
            travel = any(word in str(user_text or "") for word in TRAVEL_WORDS)
            if not change:
                if travel:
                    # 直接出发去旅游/出差：把之前可能设置的「回家换睡衣」清掉
                    self.db.kv_set("home_early:" + today_str(), False)
                return
            if not self._schedule_change_allowed(event):
                logger.debug("[SCHED_CHANGE] skip reason=not_allowed text=%s",
                             str(user_text or "")[:40])
                return
            key = today_str()
            # 当天第一句话还没生成日程时，先把骨架补出来，否则「我回家了」会被直接忽略
            try:
                await self.state.ensure_schedule(str(getattr(event, "unified_msg_origin", "") or ""))
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule._apply_user_schedule_change", _amo_exc)
            # schedule 挂在 HumanState 上（self.state.schedule），插件本身没有这个属性
            items = self.state.schedule.get(key) or self.db.get_schedule(key) or []
            if not items:
                logger.debug("[SCHED_CHANGE] skip reason=no_schedule text=%s",
                             str(user_text or "")[:40])
                return
            new_items = apply_sched_change(items, change, _now())
            if new_items == items:
                logger.debug("[SCHED_CHANGE] skip reason=no_change change=%s",
                             str(change.get("activity") or "")[:40])
                return
            decision = await self._judge_schedule_proposal(event, change, items)
            try:
                self.db.kv_set("schedule_proposal", {
                    "day": key, "at": _now().timestamp(), "text": str(user_text or "")[:120],
                    "proposal": change, "agree": bool(decision.get("agree")),
                    "reason": str(decision.get("reason") or ""),
                })
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule.schedule_proposal.save", _amo_exc)
            if not decision.get("agree"):
                logger.info("[amo] 拒绝用户改日程提议: %s（%s）",
                            change.get("activity"), decision.get("reason"))
                return
            reason_text = str(change.get("activity") or "")
            apply = getattr(self.state, "apply_schedule", None)
            if callable(apply):
                def _mutate(rows):
                    return apply_sched_change(rows, change, _now())
                apply(key, _mutate, source="chat", reason=reason_text)
            else:
                self.state.commit_schedule_change(
                    key, new_items, old_items=items,
                    source="chat", reason=reason_text,
                )
            try:
                # 日程已经切了，旧的 current_action 不能继续压住新地点
                self.db.kv_set("current_action", {})
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule.schedule_change.clear_action", _amo_exc)
            # 生病/请假长期呆家才换睡衣；旅游/出差直接出发不换
            if str(change.get("category") or "") == "health":
                text_raw = str(user_text)
                if "肠胃炎" in text_raw or "拉肚子" in text_raw:
                    status = "肠胃炎"
                elif "发烧" in text_raw:
                    status = "发烧"
                elif "感冒" in text_raw:
                    status = "感冒"
                elif "咳嗽" in text_raw:
                    status = "咳嗽"
                else:
                    status = "生病"
                current = self.state.health_info()
                if current.get("sick") and status == "生病":
                    status = str(current.get("status") or "生病")
                treatment = ""
                if any(word in text_raw for word in ("打吊水", "输液", "挂水")):
                    treatment = "打吊水"
                elif any(word in text_raw for word in ("看医生", "看病", "去医院", "挂号")):
                    treatment = "看医生"
                try:
                    if current.get("sick") and status == str(current.get("status") or ""):
                        # 已经病着，老公只是让她去看医生/不去：别把生病起点重置
                        self.state.set_treatment(treatment)
                    else:
                        self.state.set_health(
                            status, note="按老公的话生病请假", treatment=treatment)
                except Exception as _amo_exc:
                    _note_swallowed("amo_schedule._apply_user_schedule_change", _amo_exc)
            if should_switch_to_pajamas is not None and should_switch_to_pajamas(change, user_text):
                self.db.kv_set("home_early:" + key, True)
            elif travel:
                self.db.kv_set("home_early:" + key, False)
            logger.info("[amo] 按用户的话改了日程: %s" % change.get("activity"))
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._apply_user_schedule_change", _amo_exc)

    def _maybe_own_offwork_sync(self, text):
        """她自己说「我下班了」时，把当前这段从上班改成下班回家。

        只在 17:00 之后、当前还是 work 时才改；已经在家/在睡/在吃饭都不动。
        """
        raw = str(text or "")
        if not re.search(r"(我|刚|刚刚|已经)?(下班|收工)(了|啦|咯|喽)", raw):
            return
        if re.search(r"(还没|没|不|未|别)(下班|收工)", raw):
            return
        # 她说的是别人（他/她/你）下班了，不算她自己下班
        if re.search(r"(他|她|你|别人)(刚|刚刚|已经)?(下班|收工)", raw):
            return
        if _now().hour < 17:
            return
        day = today_str()
        guard = "own_offwork:" + day
        try:
            if self.db.kv_get(guard, False):
                return
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._maybe_own_offwork_sync", _amo_exc)
            return
        current = self.state.current_activity()
        if str(current.get("category") or "") != "work":
            return
        old_items = self.state.schedule.get(day) or self.db.get_schedule(day) or []
        start = str(current.get("start") or "")

        def _offwork(rows):
            patched = [dict(item) if isinstance(item, dict) else item for item in rows]
            for item in patched:
                if isinstance(item, dict) and str(item.get("start") or "") == start:
                    item["activity"] = "下班回家，路上"
                    item["place"] = "路上"
                    item["zone"] = "road"
                    item["category"] = "commute"
                    return patched
            return None

        if _offwork(old_items) is None:
            return
        try:
            apply = getattr(self.state, "apply_schedule", None)
            if callable(apply):
                result = apply(day, _offwork, source="self_offwork",
                               reason="她自己说下班了")
            else:
                result = self.state.commit_schedule_change(
                    day, _offwork(old_items), old_items=old_items,
                    source="self_offwork", reason="她自己说下班了",
                )
            if isinstance(result, dict) and result.get("skipped"):
                return
            self.db.kv_set(guard, True)
            logger.info("[amo] 她说下班了：当前日程改成下班回家")
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._maybe_own_offwork_sync", _amo_exc)

    def _maybe_own_desk_sync(self, text):
        """她自己说出「我回工位 / 去会议室 / 去茶水间 / 去食堂」时，把当前段挪到那儿。

        2026-11-19：12:34 她说「把餐盘放回去，走回工位坐下」，日程还停在「食堂吃午饭」，
        12:5x 就冒出「在饭桌上呢」。规则表 OWN_PLACE_RULES 只覆盖公司内部（zone 不变），
        所以不补通勤块，也不会把她瞬移出公司。
        """
        raw = str(text or "")
        if not raw:
            return
        if re.search(r"(他|她|你|别人)(刚|刚刚|已经)?(回|去|到|进)", raw):
            return
        target = None
        for pattern, activity, category in OWN_PLACE_RULES:
            if re.search(pattern, raw):
                target = (activity, category)
                break
        if target is None:
            return
        activity, category = target
        day = today_str()
        current = self.state.current_activity()
        start = str(current.get("start") or "")
        if not start:
            return
        place = str(current.get("place") or "")
        if not any(word in place for word in ("公司", "写字楼", "office")):
            return
        if str(current.get("activity") or "") == activity:
            return
        guard = "own_place:%s:%s:%s" % (day, start, category)
        try:
            if self.db.kv_get(guard, False):
                return
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._maybe_own_desk_sync", _amo_exc)
            return
        old_items = self.state.schedule.get(day) or self.db.get_schedule(day) or []

        def _place(rows):
            patched = [dict(item) if isinstance(item, dict) else item for item in rows]
            for item in patched:
                if isinstance(item, dict) and str(item.get("start") or "") == start:
                    item["activity"] = activity
                    item["place"] = "公司"
                    item["zone"] = "office"
                    item["category"] = category
                    return patched
            return None

        if _place(old_items) is None:
            return
        reason = "她说自己去了：" + activity
        try:
            apply = getattr(self.state, "apply_schedule", None)
            if callable(apply):
                result = apply(day, _place, source="self_place", reason=reason)
            else:
                result = self.state.commit_schedule_change(
                    day, _place(old_items), old_items=old_items,
                    source="self_place", reason=reason,
                )
            if isinstance(result, dict) and result.get("skipped"):
                return
            self.db.kv_set(guard, True)
            logger.info("[amo] 她说到某处了：当前日程改成「%s」", activity)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._maybe_own_desk_sync", _amo_exc)

    # ---------- 小日记调度（统一 tick，不建 per-hour timer） ----------
    def _diary_lock(self, day, start):
        """同一个 (day, start) 的生成锁，防止双击/调度器重复调用模型。"""
        locks = getattr(self, "_diary_locks", None)
        if locks is None:
            locks = {}
            self._diary_locks = locks
        key = (str(day), str(start))
        lock = locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            locks[key] = lock
        return lock

    def _cancel_diary_tasks(self, day, from_hour):
        """取消某天 from_hour 及以后还在跑的小日记任务，返回取消列表。"""
        tasks = getattr(self, "_diary_tasks", None)
        if not tasks:
            return []
        day = str(day)
        try:
            start_hour = int(from_hour)
        except (TypeError, ValueError):
            start_hour = 0
        cancelled = []
        for key, task in list(tasks.items()):
            if key[0] != day:
                continue
            try:
                hour = int(str(key[1])[:2])
            except (TypeError, ValueError):
                hour = 0
            if hour < start_hour:
                continue
            tasks.pop(key, None)
            try:
                task.cancel()
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule._cancel_diary_tasks", _amo_exc)
            cancelled.append("%s:task-%s" % (key[1], id(task)))
            logger.info(
                "[DIARY_DEBUG] hour=%s timer_cancel=task-%s next_run=- reason=schedule_changed version=%s",
                key[1], id(task), self.db.schedule_version(day),
            )
        return cancelled

    def _spawn_diary_task(self, day, item, items, version, reason="slot_ended"):
        """启动一个后台小日记任务，并按 (day, start) 去重。"""
        tasks = getattr(self, "_diary_tasks", None)
        if tasks is None:
            tasks = {}
            self._diary_tasks = tasks
        start = str(item.get("start") or "")
        key = (str(day), start)
        running = tasks.get(key)
        if running is not None and not running.done():
            return running
        task = self._spawn(self._run_diary_task(day, item, items, version))
        if task is None:
            return None
        tasks[key] = task

        def _done(done):
            if tasks.get(key) is done:
                tasks.pop(key, None)

        task.add_done_callback(_done)
        logger.info(
            "[DIARY_DEBUG] hour=%s timer_cancel=none next_run=- reason=%s version=%s",
            start, reason, version,
        )
        return task

    def _diary_expand_callable(self):
        """块模式走 expand_block；开关关掉或旧假 state 只有 expand_hour 时退回旧逻辑。"""
        state = self.state
        if block_diary_enabled(self):
            expand = getattr(state, "expand_block", None)
            if callable(expand):
                return expand
        expand = getattr(state, "expand_hour", None)
        return expand if callable(expand) else None

    def _block_key(self, day, item):
        """块模式下这一块的 id；开关关掉（回滚）时返回空串，读写继续走旧整点表。"""
        if not block_diary_enabled(self):
            return ""
        try:
            return block_id(day, item)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._block_key", _amo_exc)
            return ""

    def _diary_cached_text(self, day, item, start=""):
        """这一块（或旧整点）已经存在的正文；块模式只读 block_diary。"""
        key = self._block_key(day, item)
        try:
            if key:
                return str(self.db.get_block_diary(day, key) or "").strip()
            return str(self.db.get_diary(day, start) or "").strip()
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._diary_cached_text", _amo_exc)
            return ""

    def _save_diary_text(self, day, item, text, version, status="valid"):
        """落库：块模式写 block_diary；写旧整点表只属于 [deprecated] 回滚路径。"""
        start = str((item or {}).get("start") or "")
        key = self._block_key(day, item)
        try:
            if key:
                self.db.save_block_diary(
                    day, key, text, start=start, end=str((item or {}).get("end") or ""),
                    place=str((item or {}).get("place") or ""),
                    version=version, status=status)
            else:
                self.db.save_diary(day, start, text, version=version, status=status)
            return True
        except TypeError:
            # 兼容旧测试里的简化 DB
            try:
                self.db.save_diary(day, start, text)
                return True
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule._save_diary_text.legacy", _amo_exc)
                return False
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._save_diary_text", _amo_exc)
            return False

    async def _run_diary_task(self, day, item, items, version):
        """后台小日记：生成失败（含想记未连接/remember 抛错拖垮引擎）也必须落缓存。"""
        start = str((item or {}).get("start") or "")
        result = {}
        try:
            expand = self._diary_expand_callable()
            if callable(expand):
                result = await expand(day, item, items, force=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[DIARY_DEBUG] hour=%s reason=bg_error error=%r", start, exc)
            result = {}
        return self._land_diary_task(day, item, result, version)

    def _land_diary_task(self, day, item, result, version):
        """收尾：生成/缓存都空时写一句本地兜底，实在写不进才标记失败给详情页兜底文案。"""
        start = str((item or {}).get("start") or "")
        if isinstance(result, dict) and (result.get("pending") or result.get("archived")
                                         or result.get("frozen") or result.get("locked")):
            # 这一块还没过完/已冻结：不能伪造一句兜底小日记。
            return result
        text = ""
        if isinstance(result, dict):
            text = str(result.get("diary") or "").strip()
        if not text:
            text = self._diary_cached_text(day, item, start)
        if text:
            try:
                self.db.kv_set("diary_task_failed:%s:%s" % (day, start), 0)
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule._land_diary_task.clear_mark", _amo_exc)
            _note_diary_episodic(self, day, item, text)
            return result
        fallback = ""
        try:
            engine = getattr(self.state, "engine", None)
            if engine is not None:
                fallback = engine.fallback_diary({}, dict(item or {}), None)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._land_diary_task.fallback", _amo_exc)
            fallback = ""
        fallback = str(fallback or "").strip() or "（这一块有点忙乱，记不太清了，只记得时间悄悄过去了。）"
        if not self._save_diary_text(day, item, fallback, version, status="valid"):
            logger.warning("[DIARY_DEBUG] hour=%s reason=bg_fallback_failed", start)
            try:
                self.db.kv_set("diary_task_failed:%s:%s" % (day, start), now_ts())
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule._land_diary_task.mark", _amo_exc)
            return {"diary": "", "failed": True, "degraded": True}
        logger.info("[DIARY_DEBUG] hour=%s reason=bg_fallback version=%s", start, version)
        return {"diary": fallback, "fallback": True, "degraded": True}

    async def _diary_scheduler_tick(self):
        """统一调度：补生成「已经过完、但仍没有当前版本小日记」的日程块。

        块模式扫原始日程块（raw schedule，不是 _hourly_schedule）：每 tick 最多 2 条，
        不再写旧的 hour 键；开关关掉则退回旧的整点扫描，保证一键回滚。
        """
        # 当天连载小记：块结束后 debounce 到点追加一条；独立于补生成预算，失败不影响补生成。
        try:
            try:
                from .amo_state_diary import diary_live_tick
            except ImportError:
                from amo_state_diary import diary_live_tick
            diary_live_tick(self)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._diary_scheduler_tick.live", _amo_exc)
        if getattr(self, "_diary_scheduler_running", False):
            return
        self._diary_scheduler_running = True
        try:
            try:
                if not self.cfg_bool("diary_enabled", True):
                    logger.debug("[DIARY] scheduler skip reason=diary_disabled")
                    return
            except Exception as exc:
                logger.debug("[DIARY] scheduler config failed: %s", exc)
            try:
                from .amo_human_state import slot_has_ended
            except ImportError:
                from amo_human_state import slot_has_ended

            tasks = getattr(self, "_diary_tasks", None)
            if tasks is None:
                tasks = {}
                self._diary_tasks = tasks
            generated = 0
            now = _now()
            day = today_str()
            items = Timeline(self).blocks(day)
            # 第八轮 D：日程主干也只在后台 tick 里补（每天一次，已有就跳过），GET 不再生成
            if not items:
                try:
                    self._spawn(self.state.ensure_schedule(self._last_umo))
                except Exception as _amo_exc:
                    logger.debug("[DIARY] tick ensure_schedule failed: %s", _amo_exc)
                return
            # 限速：每分钟最多补 2 条（tick 每 30 秒跑一次，多轮不叠加）
            _minute_key = now.strftime("%Y%m%d%H%M")
            try:
                _budget = self.db.kv_get("diary_backfill_minute", {}) or {}
            except Exception:  # cleanup: 日记容错
                _budget = {}
            _used = (int(_budget.get("count") or 0)
                     if isinstance(_budget, dict) and _budget.get("minute") == _minute_key else 0)
            _cap = max(0, 2 - _used)
            if _cap <= 0:
                return
            version = self.db.schedule_version(day)
            if block_diary_enabled(self):
                generated = _backfill_blocks(
                    self, day, items, tasks, version, _cap, slot_has_ended)
            else:
                generated = _backfill_hours(
                    self, day, items, tasks, version, _cap, slot_has_ended)
            if generated:
                try:
                    self.db.kv_set("diary_backfill_minute",
                                   {"minute": _minute_key, "count": _used + generated})
                except Exception as _amo_exc:
                    _note_swallowed("amo_schedule.diary_backfill_budget", _amo_exc)
            # 跨日补昨天最后一段：0 点后 scheduler 只看今天，昨天最后一块会漏。
            if now.hour == 0:
                _backfill_rollover(self, now, tasks, slot_has_ended)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._diary_scheduler_tick", _amo_exc)
        finally:
            self._diary_scheduler_running = False


    def _migrate_schedule_defaults(self):
        """5.1.1：位置/资产改为“人格优先、缺失随机”，清掉 5.1.0 自动写入的固定默认值。"""
        try:
            if self.db.kv_get("schedule_defaults_511", False):
                return
            auto_filled = bool(self.db.kv_get("schedule_defaults_510", False))
            changed = False
            cleared_city = False
            # 这些是旧版 5.1.0 自动写进去的固定默认值，只用来“清理”老配置，不是现在的默认值。
            # 现在 schedule_city / schedule_home / schedule_workplace 的默认值是空
            # （_conf_schema.json default 都是 ""），城市为空时日程走「不写具体城市」的分支。
            old_defaults = {
                "schedule_city": "贵阳",
                "schedule_home": "观山湖区·小窝",
                "schedule_workplace": "公司（贵阳·观山湖区）",
                "schedule_car": "黑色奔驰C级",
            }
            for key, value in old_defaults.items():
                if self.cfg_str(key, "").strip() == value:
                    self.config[key] = ""
                    changed = True
                    if key == "schedule_city":
                        cleared_city = True
            weather_city = self.cfg_str("weather_city", "").strip()
            if weather_city == "杭州" or ((cleared_city or auto_filled) and weather_city == "贵阳"):
                self.config["weather_city"] = ""
                changed = True
            if changed and hasattr(self.config, "save_config"):
                self.config.save_config()
            self.db.kv_set("schedule_defaults_511", True)
        except Exception as exc:
            logger.debug("[amo] 日程默认配置迁移失败: %s" % exc)

    def _migrate_diary_continuity(self):
        """5.2.1 清一次旧缓存；块化后顺带把旧 schedule_diary 行幂等映射到当天块。"""
        try:
            self.db.migrate_block_diary()
        except Exception as exc:
            logger.debug("[amo] 旧小日记 → 块日记迁移失败: %s" % exc)
        try:
            if self.db.kv_get("diary_continuity_521", False):
                return
            self.db.conn.execute("DELETE FROM schedule_diary")
            self.db.conn.commit()
            self.db.kv_set("diary_continuity_521", True)
            logger.info("[amo] 已清理旧版日程日记缓存（5.2.1 连续性修复）")
        except Exception as exc:
            logger.debug("[amo] 清理日记缓存失败: %s" % exc)

    def _schedule_delayed_reply(self, event, profile):
        umo = event.unified_msg_origin
        delay = random.randint(
            self.cfg_int("delay_min_minutes", 4),
            self.cfg_int("delay_max_minutes", 18),
        ) * 60
        text = event.message_str or ""
        started = now_ts()

        async def runner():
            try:
                await asyncio.sleep(delay)
                if not self._running or not self._ready:
                    return
                # 这 4-18 分钟里他已经又说话了、或者她已经回过 → 这条补话已经过期，
                # 再用当初那句 event 走一遍会答非所问（真机上出现过重复回答旧问题）。
                if to_float(self._last_reply_at.get(umo, 0), 0) > started:
                    logger.debug("[amo] 延迟回复已过期（期间已回复）: %s" % umo)
                    return
                try:
                    latest = self._recent(umo, 1) or []
                    if latest and to_float(latest[-1].get("created_at"), 0) > started:
                        logger.debug("[amo] 延迟回复已过期（期间有新消息）: %s" % umo)
                        return
                except Exception as _amo_exc:
                    _note_swallowed("amo_schedule.ScheduleMixin._schedule_delayed_reply.runner", _amo_exc)
                try:  # task-30：此刻地点/动作唯一读场景账本（含请假/出差硬约束）
                    from .amo_state_snapshot import snapshot_with_scene as _snap_with_scene
                except ImportError:
                    from amo_state_snapshot import snapshot_with_scene as _snap_with_scene
                snap = _snap_with_scene(self)
                note = "你刚刚忙完，主动补一句；不要提等待时间，语气自然。"
                reply = await self._compose_reply(
                    event, text, snap, profile, [], "", False, note=note
                )
                if reply:
                    # task-16：延迟补话是被控入口（可 defer 到 chat 批之后）
                    await self._send_to_umo(umo, reply, source="push", kind="delayed_reply")
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.debug("[amo] 延迟回复失败: %s" % exc)

        self._spawn(runner())

    def _diary_data_root(self):
        """插件数据目录 data/plugin_data/<plugin_name>（上架规范要求写在这里）。"""
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path
            return os.path.join(str(get_astrbot_data_path()), "plugin_data", PLUGIN_NAME)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._diary_data_root", _amo_exc)
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

    def _diary_dir(self):
        """日记目录：默认 <插件数据目录>/diary，配置只能落在数据目录内。"""
        setting = str(self.cfg_str("diary_dir", "") or "").strip()
        root = self._diary_data_root()
        if callable(_diary_resolve_dir):
            return _diary_resolve_dir(setting, root)
        return os.path.join(root, "diary") if root else (_DIARY_DEFAULT_DIR or "")

    def _diary_ready_time(self):
        """大日记最早可生成的时刻 = diary_time + diary_window_minutes（默认 08:00 + 30 = 08:30）。"""
        hh, mm = self._diary_clock()
        window = max(0, self.cfg_int("diary_window_minutes", 30))
        total = hh * 60 + mm + window
        return (total // 60) % 24, total % 60

    def _diary_gate_open(self, now):
        """现在是否在「日记时间 + 窗口」里（只给 18:00 后的旧配置 / 面板判断用）。

        早上型不再用窗口：第二天任意时间都会补写昨天；旧配置 22:00 的窗口是 22:00-22:30。
        """
        hh, mm = self._diary_clock()
        window = max(0, self.cfg_int("diary_window_minutes", 30))
        return diary_in_window(now, hh, mm, window)

    def _diary_clock(self):
        """diary_time 默认 08:00（早上型：第二天任意时间写昨天）；18:00 后的旧配置到点写当天。"""
        raw = str(self.cfg_str("diary_time", "08:00") or "08:00").strip()
        try:
            hour, _, minute = raw.partition(":")
            return max(0, min(23, int(hour))), max(0, min(59, int(minute or 0)))
        except (TypeError, ValueError):
            return 8, 0

    def _mark_daily_diary_stale(self, day, version=0):
        """把已写好的每日日记标 stale（待事实对账）；不删文件、不立即调模型。

        下一次日记检查会读原文做事实一致性判断：矛盾才重写，只是遗漏就清掉 stale 保留原文。
        """
        day = str(day or "")
        if not day:
            return False
        try:
            written = str(self.db.kv_get("diary_written:" + day, "") or "")
            if not written:
                return False
            self.db.kv_set("diary_stale:" + day, True)
            logger.info(
                "[DIARY_DEBUG] day=%s daily_stale=1 version=%s reason=manual_correction",
                day, version,
            )
            return True
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._mark_daily_diary_stale", _amo_exc)
            return False

    def _stale_daily_days(self, now, lookback=7):
        """最近几天里被标 stale 的每日日记（待事实对账），最旧的先写。

        只扫最近 8 天（含今天）；真正写不写还要过 _diary_days_to_write 的 T+1 窗口，
        改不改由 _write_one_daily_diary 的事实门决定。
        """
        days = []
        try:
            today = now.date()
            for offset in range(max(0, int(lookback)), -1, -1):
                day = today - timedelta(days=offset)
                if self.db.kv_get("diary_stale:" + day.isoformat(), False):
                    days.append(day)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._stale_daily_days", _amo_exc)
            return []
        return days

    async def _maybe_daily_diary(self):
        """写一篇大日记：早上型第二天任意时间补昨天，旧配置 22:00 到点写当天。

        - 默认（08:00，早上型）：只要昨天没写/和事实矛盾就写，开机/首个 tick 立即补，
          不再限制 08:00-08:30；自动补写只允许 T+1（目标日=昨天），更早的缺日记 skipped；
        - 旧配置 diary_time >= 18:00（例如 22:00）：到点后写当天，不含 23:30 宵夜；
        - 单飞：_diary_writing 保证启动补写和定时器不会并发写多篇。
        """
        if not self.cfg_bool("diary_enabled", True) or not callable(_diary_write):
            return
        now = _now()
        hh, mm = self._diary_clock()
        # 早上型（18 点前）没有窗口：第二天任何时候都允许补昨天；旧配置到点才写当天。
        if diary_end_day(now, hh, mm) is None:
            logger.debug("[DIARY] daily skip reason=before_diary_time time=%02d:%02d", now.hour, now.minute)
            return
        if getattr(self, "_diary_writing", False):
            return
        days = self._diary_days_to_write(now, hh, mm)
        if not days:
            return
        self._diary_writing = True
        try:
            for day in days:
                await self._write_one_daily_diary(day, now)
        finally:
            self._diary_writing = False

    def _diary_days_to_write(self, now, hh, mm):
        """这一轮写哪几天：只允许 T+1（目标日=昨天/当天）；更早的缺日/失效日记记 skipped。

        以前允许「连续缺几天就一天补一篇、最多补 7 天」，于是 11-16/11-18 这种迟到
        2~4 天的大日记会在 11-20 一次性补写，素材/日期标签都容易串日。现在自动补写
        只写窗口内的一天；窗口外的缺日只落 diary_skipped:<day> 审计，不生成正文
        （人工补写走面板/命令的 diary_manual_target，显式指定目标日才放行）。
        """
        days = set()
        end_day = diary_end_day(now, hh, mm)
        if end_day is None:
            return []
        today = now.date()
        window_start = today - timedelta(days=max(1, int(DIARY_BACKFILL_WINDOW_DAYS)))
        for day in self._stale_daily_days(now):
            if day > end_day:
                continue  # 今天/未来还没过完：这次不处理（保持原 stale 标记）
            if day < window_start:
                _record_diary_skip(self, day, now, "stale_out_of_window")
                try:
                    self.db.kv_set("diary_stale:" + day.isoformat(), False)
                except Exception as _amo_exc:
                    _note_swallowed("amo_schedule.daily_diary.skip_stale", _amo_exc)
                continue
            days.add(day)
        try:
            last_written = str(self.db.kv_get("diary_last_day", "") or "")
            start_day = (
                (date.fromisoformat(last_written) + timedelta(days=1))
                if last_written else end_day
            )
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._maybe_daily_diary", _amo_exc)
            start_day = end_day
        if start_day <= end_day:
            cursor = start_day
            while cursor <= end_day:
                if cursor < window_start:
                    _record_diary_skip(self, cursor, now, "out_of_window")
                else:
                    days.add(cursor)
                cursor += timedelta(days=1)
        return sorted(days)

    def _diary_existing_text(self, day):
        """已写好的大日记正文：先按 kv 记的路径读，读不到再按日期目录读。"""
        day_text = day.isoformat()
        try:
            path = str(self.db.kv_get("diary_written:" + day_text, "") or "")
        except Exception:  # cleanup: 日记容错
            path = ""
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    return fh.read()
            except (OSError, UnicodeDecodeError):  # cleanup: 读不到旧正文就当没有
                pass
        try:
            if callable(_diary_read):
                return _diary_read(self._diary_dir(), day) or ""
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._diary_existing_text", _amo_exc)
        return ""

    def diary_fact_mismatch(self, day, text):
        """大日记和当天事实有没有「矛盾」（只查矛盾，不查遗漏）；规则在 amo_diary_guard。"""
        try:
            try:
                from .amo_diary_guard import diary_fact_mismatch as _mismatch
            except ImportError:
                from amo_diary_guard import diary_fact_mismatch as _mismatch
            return bool(_mismatch(self, day, text))
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.diary_fact_mismatch", _amo_exc)
            return False

    def _daily_diary_rewrites(self, day_text):
        """这一天的大日记自动重写了几次（共用 diary_rewrites，key 用 day|daily）。"""
        try:
            data = self.db.kv_get("diary_rewrites", {}) or {}
        except Exception:  # cleanup: 日记容错
            data = {}
        return int((data or {}).get(str(day_text) + "|daily") or 0)

    def _bump_daily_diary_rewrite(self, day_text):
        try:
            data = dict(self.db.kv_get("diary_rewrites", {}) or {})
        except Exception:  # cleanup: 日记容错
            data = {}
        key = str(day_text) + "|daily"
        data[key] = int(data.get(key) or 0) + 1
        try:
            self.db.kv_set("diary_rewrites", data)
        except Exception:  # cleanup: 日记容错
            pass  # cleanup: 预算记不上就放行，不能因为记账失败把日记卡死
        return data[key]

    def _day_digest(self, day_text):
        """取某天缓冲折叠句：有当天条目就现折，没有就用已存的 digest。"""
        try:
            try:
                from .amo_episodic import fold_day
            except ImportError:
                from amo_episodic import fold_day
            try:
                items = self.db.kv_get("episodic_memory", []) or []
            except Exception:  # cleanup: 日记容错
                items = []
            try:
                stored = str(self.db.kv_get("day_digest:" + day_text, "") or "")
            except Exception:  # cleanup: 日记容错
                stored = ""
            digest, keep = fold_day(items, day_text, now_ts())
            return (digest or stored), keep, bool(digest)
        except Exception as exc:
            _note_swallowed("amo_schedule._day_digest", exc)
            return "", [], False

    async def _write_one_daily_diary(self, day, now):
        # 写一天的日记：版本变化就静默重来（最多 3 次），写成功才落 kv
        day_text = day.isoformat()
        guard = "diary_written:" + day_text
        stale_key = "diary_stale:" + day_text
        attempt_key = "diary_attempt:" + day_text
        was_rewrite = False
        try:
            written = self.db.kv_get(guard, "")
            stale = bool(self.db.kv_get(stale_key, False))
            if written and not stale:
                return
            if written and stale:
                # 事实门：只改「和事实矛盾」的；只是遗漏（没提宵夜/没提出门/没提某顿饭）就保留原文
                existing = self._diary_existing_text(day)
                if not self.diary_fact_mismatch(day, existing):
                    self.db.kv_set(stale_key, False)
                    logger.info(
                        "[DIARY_DEBUG] day=%s daily_stale=0 reason=fact_consistent",
                        day_text,
                    )
                    return
                if self._daily_diary_rewrites(day_text) >= DIARY_REWRITE_LIMIT:
                    self.db.kv_set(stale_key, False)
                    logger.info(
                        "[DIARY_DEBUG] day=%s daily_stale=0 reason=rewrite_cap",
                        day_text,
                    )
                    return
                was_rewrite = True
            last = to_float(self.db.kv_get(attempt_key, 0), 0)
            if last and now_ts() - last < 1800:
                return
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._maybe_daily_diary", _amo_exc)
        try:
            committed = False
            path = ""
            # 日程在生成期间变了：丢弃已生成的结果，静默按最新日程重来，最多 3 次。
            for attempt in range(3):
                version = self.db.schedule_version(day_text)
                text = await self._generate_daily_diary(day)
                if not text:
                    try:
                        self.db.kv_set(attempt_key, now_ts())
                    except Exception as _amo_exc:
                        _note_swallowed("amo_schedule.ScheduleMixin._maybe_daily_diary", _amo_exc)
                    break
                current_version = self.db.schedule_version(day_text)
                if current_version != version:
                    logger.info(
                        "[DIARY_DEBUG] day=%s silent_retry=1 reason=version_changed "
                        "attempt=%d version=%s->%s",
                        day_text, attempt + 1, version, current_version,
                    )
                    continue
                try:
                    from .amo_diary_guard import sanitize_diary as _sanitize_diary
                except ImportError:
                    from amo_diary_guard import sanitize_diary as _sanitize_diary
                _digest, _keep_episodic, _had_episodic = self._day_digest(day_text)
                if _digest:
                    # 跨天去重 + 240 字上限：前两天的 digest 已经说过的事实不再重复。
                    _digest = _dedupe_day_digest(self, day, _digest)
                if _digest and _digest not in text:
                    text = text.rstrip() + "\n\n" + _digest
                try:
                    _together = bool(self.db.kv_get("diary_together:" + day_text, False))
                except Exception:  # cleanup: 日记容错
                    _together = False
                text = _sanitize_diary(text, together=_together)
                # 跨天复读兜底：和前一天/前两天完全相同的整句删掉（同一句连看几天像模板）。
                try:
                    try:
                        from .amo_diary_guard import strip_repeated_sentences as _strip_repeat
                    except ImportError:
                        from amo_diary_guard import strip_repeated_sentences as _strip_repeat
                    _previous_texts = []
                    for _offset in (1, 2):
                        _previous_text = _diary_read(
                            self._diary_dir(), day - timedelta(days=_offset)) or ""
                        if _previous_text:
                            _previous_texts.append(_previous_text)
                    text = _strip_repeat(text, _previous_texts, day=day_text)
                except Exception as _amo_exc:  # cleanup: 复读兜底失败不能拖垮日记
                    _note_swallowed("amo_schedule.daily_diary.repeat", _amo_exc)
                # 场景过渡兜底：相邻句换地点却没过渡词时补「后来，」（含上面拼进来的 digest）。
                try:
                    try:
                        from .amo_diary_guard import add_scene_transitions as _add_scene
                    except ImportError:
                        from amo_diary_guard import add_scene_transitions as _add_scene
                    text = _add_scene(text)
                except Exception as _amo_exc:  # cleanup: 过渡补齐失败不能拖垮日记
                    _note_swallowed("amo_schedule.daily_diary.transitions", _amo_exc)
                # 落盘前最后一道：日期标签必须是 target_day 的（防重写候选/兜底句带错标签）。
                text = _normalize_diary_date_label(text, day)
                path = _diary_write(self._diary_dir(), day, text)
                latest_version = self.db.schedule_version(day_text)
                if latest_version != current_version:
                    logger.info(
                        "[DIARY_DEBUG] day=%s silent_retry=1 reason=version_changed_after_write "
                        "attempt=%d version=%s->%s",
                        day_text, attempt + 1, current_version, latest_version,
                    )
                    continue
                try:
                    self.db.kv_set(guard, path)
                    try:
                        self.db.kv_set("diary_mood:" + day_text, self.state.mood_score())
                    except Exception as _amo_exc:
                        _note_swallowed("amo_schedule.ScheduleMixin._maybe_daily_diary.mood", _amo_exc)
                    self.db.kv_set("diary_last_day", day_text)
                    self.db.kv_set("diary_version:" + day_text, current_version)
                    self.db.kv_set(stale_key, False)
                    if was_rewrite:
                        self._bump_daily_diary_rewrite(day_text)
                    try:
                        self.db.kv_set("diary_notes:" + day_text, [])
                    except Exception as _amo_exc:
                        _note_swallowed("amo_schedule.diary_notes.clear", _amo_exc)
                    # 定稿即锁定：以后任何自动/手动触发都不许再调模型重写
                    self.db.kv_set("diary_locked:" + day_text, True)
                    if _digest:
                        self.db.kv_set("day_digest:" + day_text, _digest)
                        if _had_episodic:
                            self.db.kv_set("episodic_memory", _keep_episodic)
                    final_version = self.db.schedule_version(day_text)
                    if final_version != current_version:
                        logger.info(
                            "[DIARY_DEBUG] day=%s silent_retry=1 reason=version_changed_after_kv "
                            "attempt=%d version=%s->%s",
                            day_text, attempt + 1, current_version, final_version,
                        )
                        continue
                except Exception as _amo_exc:
                    _note_swallowed("amo_schedule.ScheduleMixin._maybe_daily_diary", _amo_exc)
                committed = True
                logger.info("[amo] 日记已写: %s" % path)
                logger.info(
                    "[DIARY_DEBUG] day=%s daily_stale=0 version=%s reason=daily_regenerated",
                    day_text, current_version,
                )
                break
            if committed:
                try:
                    await _seed_day_digest(self, day)
                except Exception as _amo_exc:
                    _note_swallowed("amo_schedule._write_one_daily_diary.seed_digest", _amo_exc)
                self._finalize_live_diary(day_text, now)
            if not committed:
                self.db.kv_set(stale_key, True)
        except Exception as exc:
            logger.warning("[amo] 写日记失败: %s" % exc)
            try:
                self.db.kv_set(attempt_key, now_ts())
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule.ScheduleMixin._maybe_daily_diary", _amo_exc)


    def _finalize_live_diary(self, day_text, now=None):
        """当天连载小记收束：大日记定稿后合并/去重归档，连载本身清掉（只留 2 天）。"""
        try:
            try:
                from .amo_state_diary import diary_live_finalize
            except ImportError:
                from amo_state_diary import diary_live_finalize
            return diary_live_finalize(self, day_text, now)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._finalize_live_diary", _amo_exc)
            return {}

    async def _diary_chat_lines(self, day):
        """当天聊天 → 「谁：内容」行；只收 target_day 当天 00:00-24:00 的消息。

        补写跨天时素材必须按 target_day 硬隔离：11-21 的日记只能拿 11-21 的聊天，
        11-20 晚间的对话不能因为「最近」被写进 11-21（2026-11-22 审计）。
        """
        target = _day_date_of(day)
        if target is None:
            return []
        day_start = int(datetime.combine(target, datetime.min.time()).timestamp())
        day_end = day_start + 86400
        mem = self._data_mem()
        if mem is None or not callable(getattr(mem, "messages_between", None)):
            return []
        try:
            rows = await self._data_call(
                "messages_between", self._data_bot_id(), day_start, day_end, "", 500,
            ) or []
        except Exception as exc:
            logger.debug("[amo] 日记取聊天失败: %s" % exc)
            return []
        # 日记是她的私人视角，之后还会进记忆：群里的对话、别人的对话都不该写进来
        rows = _private_rows(rows)
        lines = []
        for row in rows:
            timestamp = _row_timestamp(row)
            if timestamp and not (day_start <= timestamp < day_end):
                continue  # 数据源越界返回别的日期：不进 target_day 的日记素材
            text = str(row.get("content") or "").strip().replace("\n", " ")
            if not text or str(row.get("role") or "") == "system":
                continue
            who = "我" if str(row.get("role") or "") == "bot" else (str(row.get("name") or "") or "他")
            lines.append(who + "：" + text[:200])
        return lines

    def _current_block_diary_rows(self, day):
        """块表里身份仍对得上当前日程的行（孤儿/旧内容哈希行不进大日记素材）。

        2026-11-22 P0：11-18 的 25 条孤儿块日记正文会被 _diary_hourly_lines 当
        「当天素材」喂给大日记 fallback，公司加班/地铁的旧正文就这样回流成事实。
        这里按 block_id 对齐当前块身份，孤儿一律不读；日程读不到时退回旧行为。
        """
        try:
            raw = self.db.conn.execute(
                "SELECT block_id, start AS hour, text FROM block_diary "
                "WHERE day=? AND status!='stale' ORDER BY start ASC",
                (day.isoformat(),),
            ).fetchall()
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._current_block_diary_rows", _amo_exc)
            return []
        try:
            blocks = Timeline(self).blocks(day.isoformat())
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._current_block_diary_rows.ids", _amo_exc)
            return list(raw)
        if not blocks:  # 没有日程可对账（老库/极简假件）：退回旧行为，别把日记全吞掉
            return list(raw)
        ids = {str(block_id(day.isoformat(), row))
               for row in blocks if isinstance(row, dict)}
        return [row for row in raw if str(row[0] or "") in ids]

    def _diary_hourly_lines(self, day):
        """当天每块小日记 → 「HH:MM 内容」行（只读 target_day、身份仍在当前日程里的行）。"""
        target = _day_date_of(day)
        if target is None:
            return []
        day = target
        rows = []
        try:
            if block_diary_enabled(self):
                rows = self._current_block_diary_rows(day)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._diary_hourly_lines.block", _amo_exc)
            rows = []
        if not rows:
            try:
                rows = self.db.conn.execute(
                    "SELECT hour, text FROM schedule_diary WHERE day=? ORDER BY hour ASC",
                    (day.isoformat(),),
                ).fetchall()
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule.ScheduleMixin._diary_hourly_lines", _amo_exc)
                return []
        lines = []
        for row in rows:
            if len(row) >= 3:
                hour, text = row[1], row[2]
            else:  # 旧整点表回滚分支：只有 (hour, text)
                hour, text = row[0], row[1]
            text = str(text or "").strip()
            if text:
                lines.append(str(hour)[:5] + " " + text[:160])
        try:
            log = self.db.kv_get("travel_log", []) or []
            for entry in log[-10:]:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("day") or "") != day.isoformat():
                    continue
                city = str(entry.get("city") or "").strip()
                hour = str(entry.get("hour") or "").strip()
                if city and hour:
                    if entry.get("journey"):
                        lines.append(hour + " 她坐车去" + city + "陪他，路上约 2 小时")
                    elif entry.get("solo"):
                        if entry.get("back"):
                            lines.append(hour + " 她自己从" + city + "旅游回来了")
                        else:
                            lines.append(hour + " 她自己出门去" + city + "旅游，晚上住酒店")
                    elif entry.get("back"):
                        lines.append(hour + " 跟他从" + city + "回来了")
                    else:
                        lines.append(hour + " 跟他出门到了" + city + "，晚上住酒店")
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._diary_hourly_lines", _amo_exc)
        try:
            rejects = self.db.kv_get("follow_reject_log", []) or []
            for entry in rejects[-5:]:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("day") or "") != day.isoformat():
                    continue
                city = str(entry.get("city") or "").strip()
                hour = str(entry.get("hour") or "").strip()
                if city:
                    lines.append(hour + " 她说想陪他去" + city + "，他说让她好好上班，她闷闷不乐")
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._diary_hourly_lines", _amo_exc)
        return lines

    async def _generate_daily_diary(self, day):
        """用 target_day 的聊天 + 日程小日记，写一篇大肥鱼口吻的日记。

        day 只接受 date（或可解析成 ISO 日期）；素材、日期标签、时间边界全部按这一天，
        不接受「现在」兜底，避免补写跨天时把别的日子写进来。
        """
        if not callable(_diary_write):
            return ""
        target = _day_date_of(day)
        if target is None:
            logger.warning("[amo] 日记目标日无效，拒绝生成: %r", day)
            return ""
        day = target
        chat_lines = await self._diary_chat_lines(day)
        hourly_lines = self._diary_hourly_lines(day)
        umo = self._last_umo or self._first_private_umo()
        try:
            persona = await self.llm.persona(umo)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._generate_daily_diary", _amo_exc)
            persona = ""
        _residence_rule = ""
        try:
            try:
                from . import amo_household as _hh
            except ImportError:
                import amo_household as _hh
            _residence_rule = _hh.wording("schedule_boundary", plugin=self)
        except Exception:  # cleanup: 日记容错
            _residence_rule = ""
        _residence_rule = _residence_rule or (
            "物理边界：你们同城不同住、各有住处；平时他不在你身边，只能手机聊天；"
            "只有约会的那段时间可以写他坐在对面，约会前后和结束之后都只能写一个人/手机，各回各家。"
        )
        system = (
            (persona or "你是一个生活在中国城市的普通女孩，说话自然口语化。") +
            "\n\n你现在要保持这个人格，用自己的口吻写日记：第一人称、像真人写给自己的，"
            "不是助手汇报，也不要提 AI、模型、插件、日程表这些词。"
            "文中的“我”指你自己（bot），用户是“老公”；"
            "第一次提到他必须写“老公”，不许一上来就写没有指代的“他”；不要把“我”写成用户。"
            + _residence_rule +
            "\n\n硬性结尾约束：日记必须是纯粹的内心独白（第一人称），"
            "严禁在结尾输出任何方括号、圆括号括起来的系统状态说明或代码逻辑！"
        )
        try:
            from .amo_diary_guard import fact_rules, rewrite_together_chat, sanitize_diary
        except ImportError:
            from amo_diary_guard import fact_rules, rewrite_together_chat, sanitize_diary
        try:
            try:
                from .amo_fact_pool import build_fact_pool, render_prompt, validate_text
            except ImportError:
                from amo_fact_pool import build_fact_pool, render_prompt, validate_text
            pool = await build_fact_pool(self, day)
            prompt = render_prompt(pool)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._generate_daily_diary.pool", _amo_exc)
            pool = None
            prompt = _diary_build_prompt(day, chat_lines, hourly_lines)
        # 约会当天：系统规则换成「面对面」，并把状态记下来给落库那一步用
        dating = bool(((pool or {}).get("meta") or {}).get("date_together"))
        _together_home = False
        try:
            try:
                from . import amo_household as _hh
            except ImportError:
                import amo_household as _hh
            # 2026-11-21：不再只看 21:00-22:00 一块；当天任何一块他在家，
            # 这篇日记就按「在家共处」规则写（否则白天在家、晚上出门会整篇写错）。
            _day_key = day.isoformat() if hasattr(day, "isoformat") else str(day)
            _rows = list((self.state.schedule or {}).get(_day_key)
                         or self.db.get_schedule(_day_key) or [])
            if not _rows:
                _rows = [{"start": "21:00", "end": "22:00", "place": "家", "zone": "home"}]
            for _row in _rows:
                if not isinstance(_row, dict):
                    continue
                _sc = _hh.scene_at(self, _day_key, _row.get("start"), _row.get("end"),
                                   _row.get("place") or "", zone=_row.get("zone") or "",
                                   activity=_row.get("activity") or "")
                if _sc.presence == "home":
                    _together_home = True
                    break
        except Exception:  # cleanup: 日记容错
            _together_home = False
        _call_title = "老公"
        try:
            try:
                from . import amo_person_ref as _pr
            except ImportError:
                import amo_person_ref as _pr
            _call_title = _pr.title(self)
        except Exception:  # cleanup: 日记容错
            _call_title = "老公"
        system = fact_rules(dating, home=_together_home, title=_call_title) + "\n\n" + system
        try:
            self.db.kv_set("diary_together:" + day.isoformat(), dating)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._generate_daily_diary.together", _amo_exc)
        if not chat_lines and not hourly_lines:
            # 2026-11-22：以前只看聊天/块日记，不看日程事实，于是有外出块的当天也被
            # 暗示「非常平淡」，模型就写成「一整天窝在家」。有日程/身体/天气事实时
            # 改成按素材写，不再下「平淡」结论。
            if _pool_has_day_facts(pool):
                prompt += ("\n\n今天没有聊天和块小日记记录，但素材包里有当天真实的日程、"
                           "身体和天气事实：按素材写这一天的日常（去了哪儿、吃了什么、"
                           "身体怎么样），不要因为没聊天就写成整天窝在家里。")
            else:
                prompt += ("\n\n今天没有任何聊天、消费或外出记录，非常平淡。"
                           "请结合她的日常状态写一篇碎碎念：可以吐槽无聊、想念老公、抱怨天气、"
                           "分享刷剧感受；消费和事件只写素材包里真实有的。")
        prompt += ("\n\n硬性要求：即使今天很平淡也要写，每天都有一篇；"
                   "素材包里真实发生的事才写；消费、地点、经历都以素材包为准；"
                   "不写“心情+5”这类数值；篇幅 200-400 字，语言每天要有变化。"
                   "素材里没有做饭/下厨/煎炒蒸煮的证据时，不要写自己做饭，"
                   "照实写现成的饭（买好的、热剩菜、点外卖）；"
                   "相邻两句换了地点就写过渡词（然后/后来/接着/出门/回家/到了），不要直接跳；"
                   "日程里没有加班/晚走的事实，就不要写加班、熬到几点、忙到几点才走；"
                   "素材/账本里没有消费，就不要写花了多少钱、心疼钱。")
        prompt += _diary_time_rules(self, day)
        try:
            wardrobe = getattr(self, "wardrobe", None)
            if wardrobe is not None:
                worn = wardrobe.current(day.isoformat()) or {}
                outfit_text = str(worn.get("text") or "").strip()
                if outfit_text:
                    prompt += ("\n\n今天实际穿的是：%s。日记里如果写到衣服，"
                               "就写这一套。" % outfit_text)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._generate_daily_diary", _amo_exc)
        try:
            lock = self.db.kv_get("last_date_event", {}) or {}
            if str((lock or {}).get("day") or "") == day.isoformat():
                prompt += ("\n\n今天你和老公约会了（%s）：日记里必须重点回味这次约会，"
                           "写清一起做了什么、当时的心情；不要写得像没发生过。"
                           % str((lock or {}).get("activity") or "约会"))
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._generate_daily_diary.date", _amo_exc)
        try:
            text = await self.llm.chat_raw(umo, system, prompt)
        except Exception as exc:
            logger.warning("[amo] 日记生成失败: %s" % exc)
            return ""
        text = str(text or "").strip()
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
        text = re.sub(r"^我的日记[^\n]*\n+", "", text)
        text = self._clean_diary_text(text)
        text = sanitize_diary(text, together=dating)
        try:
            try:
                from .amo_household import fix_apart_text
            except ImportError:
                from amo_household import fix_apart_text
            text = fix_apart_text(self, text)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.diary_household", _amo_exc)
        if dating:
            text = rewrite_together_chat(text)
        if pool is not None:
            # 大日记也接 LLM 地点重试：确定性删句之前先给模型一次改正机会（最多 2 次）。
            try:
                text, _loc_fallback = await self._guard_daily_location(
                    text, pool, umo, system, prompt, dating)
                if _loc_fallback:
                    self.db.kv_set("diary_location_fallback:" + day.isoformat(), True)
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule.ScheduleMixin._generate_daily_diary.location", _amo_exc)
            try:
                # 兜底句按「这一天」轮换：先用带日期的清洗，validate_text 里就不会
                # 再插入那句固定的「突然想起他以前叮嘱过我的话」（跨天复读）。
                text = _strip_fabricated_interaction_for_day(text, pool, day.isoformat())
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule.diary.strip_fabrication", _amo_exc)
            try:
                text = validate_text(text, pool)
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule.ScheduleMixin._generate_daily_diary.validate", _amo_exc)
            try:
                # 确定性删句也可能仍然落到整天兜底：同一个标记，方便面板/日志排查。
                if (pool.get("meta") or {}).get("location_fallback"):
                    self.db.kv_set("diary_location_fallback:" + day.isoformat(), True)
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule.ScheduleMixin._generate_daily_diary.location_flag", _amo_exc)
        if callable(_diary_ensure_ending):
            text = _diary_ensure_ending(day, text)
        try:
            try:
                from .amo_marriage_guard import rewrite as _marriage_rewrite
            except ImportError:
                from amo_marriage_guard import rewrite as _marriage_rewrite
            text = _marriage_rewrite(text)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.diary.marriage", _amo_exc)
        try:
            # 守护写进来的事件片段：并进当天大日记，不能被一句话顶掉
            _notes = self.db.kv_get("diary_notes:" + day.isoformat(), []) or []
            if _notes:
                text = text.rstrip() + "\n\n" + "\n\n".join(str(n) for n in _notes)
                # 不在这里清：等日记真正落库成功再清，避免重试丢片段
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.diary_notes", _amo_exc)
        # 生成层守卫：素材里没有做饭证据时，去掉「煎了两根油条/炒了两个菜」这类强做饭断言。
        text = _strip_ungrounded_cooking_for_pool(text, pool)
        try:
            try:
                from .amo_diary_guard import strip_ungrounded_overtime as _strip_overtime
            except ImportError:
                from amo_diary_guard import strip_ungrounded_overtime as _strip_overtime
            text = _strip_overtime(text, _day_has_overtime(self, day.isoformat()))
        except Exception as _amo_exc:  # cleanup: 加班守卫失败不能拖垮日记
            _note_swallowed("amo_schedule.daily_diary.overtime", _amo_exc)
        text = _strip_ungrounded_spending_for_pool(text, pool)
        # 日期标签只认 target_day：模型自写的星期/节气/节日一律改回唯一标签。
        text = _normalize_diary_date_label(text, day)
        return text if len(text) >= 10 else ""

    def _finalize_daily_candidate(self, raw, dating, pool):
        """重写候选的清洗：和主链路同一套 sanitize，不提前跑确定性删句。"""
        try:
            from .amo_diary_guard import rewrite_together_chat, sanitize_diary
        except ImportError:
            from amo_diary_guard import rewrite_together_chat, sanitize_diary
        text = str(raw or "").strip()
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
        text = re.sub(r"^我的日记[^\n]*\n+", "", text)
        text = self._clean_diary_text(text)
        text = sanitize_diary(text, together=dating)
        try:
            try:
                from .amo_household import fix_apart_text
            except ImportError:
                from amo_household import fix_apart_text
            text = fix_apart_text(self, text)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.diary_household", _amo_exc)
        if dating:
            text = rewrite_together_chat(text)
        day_text = str(((pool or {}).get("meta") or {}).get("day") or "")
        text = _strip_fabricated_interaction_for_day(text, pool, day_text)
        text = _strip_ungrounded_cooking_for_pool(text, pool)
        if day_text:
            try:
                try:
                    from .amo_diary_guard import strip_ungrounded_overtime as _strip_overtime
                except ImportError:
                    from amo_diary_guard import strip_ungrounded_overtime as _strip_overtime
                text = _strip_overtime(text, _day_has_overtime(self, day_text))
            except Exception as _amo_exc:  # cleanup: 加班守卫失败不能拖垮日记
                _note_swallowed("amo_schedule.daily_candidate.overtime", _amo_exc)
        text = _strip_ungrounded_spending_for_pool(text, pool)
        text = _normalize_diary_date_label(text, day_text)  # 重写候选也守 target_day
        return text if len(text) >= 10 else ""

    async def _guard_daily_location(self, text, pool, umo, system, prompt, dating):
        """整天日记的地点闸：命中「今天没去过的地点」最多让模型重写 2 次，仍冲突走兜底句。"""
        try:
            from . import amo_location_rules as _loc
        except ImportError:
            import amo_location_rules as _loc
        meta = dict((pool or {}).get("meta") or {})
        places = [str(place) for place in (meta.get("places") or []) if str(place).strip()]
        if not places and str(meta.get("location") or "").strip():
            places = [str(meta.get("location"))]
        value = str(text or "")
        if not places or not _loc.enabled(self):
            return value, False
        violations = _loc.day_check(value, places, True)
        if not violations:
            return value, False
        retries = 2
        for attempt in range(1, retries + 1):
            hint = (
                "\n\n【地点校验失败】你写了今天没去过的地点细节：%s。"
                "今天只去过：%s。只重写这一段日记，删掉这些细节，"
                "不要提到没去过的地方。" % ("、".join(violations), "、".join(places))
            )
            try:
                raw = await self.llm.chat_raw(umo, system, prompt + hint)
            except Exception as exc:
                logger.warning("[DIARY_GUARD] daily regenerate 失败: %r", exc)
                break
            fresh = self._finalize_daily_candidate(raw, dating, pool)
            if not fresh:
                logger.info("[DIARY_GUARD] daily violations=%s retry=%d empty",
                            "、".join(violations), attempt)
                continue
            value = fresh
            violations = _loc.day_check(value, places, True)
            logger.info("[DIARY_GUARD] daily violations=%s retry=%d",
                        "、".join(violations) or "none", attempt)
            if not violations:
                return value, False
        value = _loc.day_fallback_line()
        meta["location_fallback"] = True
        try:
            pool["meta"] = meta
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._guard_daily_location.meta", _amo_exc)
        logger.info("[DIARY_GUARD] daily violations=%s retry=%d fallback=1 scope=day",
                    "、".join(violations) or "none", retries)
        return value, True

    @staticmethod
    def _clean_diary_text(text):
        """清除模型/旧缓存混进正文的系统标记，保证用户只看到人话。"""
        value = str(text or "")
        # 整行的 [..] / <..> / （..）系统说明直接删掉
        value = re.sub(r"(?im)^\s*[\[<（(].{0,160}(?:重写|系统|日程又变|提示|版本|stale).{0,160}[\]>）)]\s*$", "", value)
        # 夹在正文里的方括号/尖括号系统说明
        value = re.sub(r"[\[<][^\]>\n]{0,160}(?:重写|系统提示|日程又变|正在重写|按新日程)[^\]>\n]{0,160}[\]>]", "", value)
        value = value.replace("这篇日记写完后日程又变了，正在按新日程重写", "")
        value = value.replace("正在按新日程重写", "")
        lines = [line for line in value.splitlines()
                 if not re.search(r"(系统提示|正在重写|按新日程重写|日程又变了)", line)]
        return "\n".join(lines).strip()

    def _weather_alert_umos(self):
        """天气提醒的候选会话，按最近活跃排序。

        只用她**真的收到过消息**的会话（channels:<uid> + private_umo），
        没配飞书的人候选里就只有 QQ，不会随机到一个不存在的平台。
        """
        pairs = []
        try:
            for profile in self.db.proactive_profiles(limit=20):
                uid = str(profile.get("uid") or "")
                data = self.db.kv_get("channels:%s" % uid, {}) or {}
                if isinstance(data, dict):
                    for value in data.values():
                        if isinstance(value, dict) and value.get("umo"):
                            pairs.append((float(value.get("ts") or 0), str(value["umo"])))
                item = str(profile.get("private_umo") or "")
                if item:
                    pairs.append((0.0, item))
        except Exception as exc:
            logger.debug("[amo] 天气提醒选平台失败: %s" % exc)
        pairs.sort(key=lambda pair: -pair[0])
        seen = []
        for _, umo in pairs:
            if umo and umo not in seen:
                seen.append(umo)
        if not seen:
            fallback = self._last_umo or self._first_private_umo()
            if fallback:
                seen.append(fallback)
        return seen

    def _pick_weather_alert_umo(self):
        """兼容旧调用：返回最该优先尝试的那个会话。"""
        items = self._weather_alert_umos()
        return items[0] if items else ""

    @staticmethod
    def _clean_weather_alert(text):
        """把模型偶尔蹦出的客服腔/播报腔挡掉，宁可退回模板。"""
        value = str(text or "").strip().strip('"').strip("“”").strip()
        value = re.sub(r"^(?:天气提醒|提醒你|温馨提示)[：:，,\s]*", "", value)
        for bad in ("请注意", "建议您", "提醒您", "天气预报", "温馨提示", "作为AI", "作为 AI", "助手"):
            if bad in value:
                return ""
        if len(value) > 80:
            return ""
        return value

    async def _weather_alert_text(self, umo, tomorrow, fallback, target_day=None):
        """用她自己的语气说这句提醒；模型失败就退回模板。

        提醒只认 target_day：日期标签（星期/节气/节日）由 amo_shared_time.day_label
        统一生成，模型不许自己换算「明天是几号/星期几」。
        """
        facts = []
        target = _day_date_of(target_day)
        if target is None:
            target = _day_date_of(str(tomorrow.get("fxDate") or ""))
        if target is None:
            target = (_now() + timedelta(days=1)).date()
        label_text = str(_day_label_of(target).get("text") or "").strip()
        if label_text:
            facts.append("提醒的目标日期（明天）：" + label_text)
        day_text = (str(tomorrow.get("textDay") or "") + " " + str(tomorrow.get("textNight") or "")).strip()
        if day_text:
            facts.append("明天天气：" + day_text)
        if tomorrow.get("tempMax"):
            facts.append("明天最高 " + str(tomorrow.get("tempMax")) + "℃")
        recent_lines = []
        try:
            for row in self._recent(umo, 8):
                content = str(row.get("content") or "").strip()
                if not content:
                    continue
                who = "他" if str(row.get("role") or "") == "user" else "你"
                recent_lines.append(who + "：" + content[:80])
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._weather_alert_text", _amo_exc)
            recent_lines = []
        try:
            try:  # task-30：此景用账本快照（prompt_text 读 activity/period/health）
                from .amo_state_snapshot import snapshot_with_scene as _snap_with_scene
            except ImportError:
                from amo_state_snapshot import snapshot_with_scene as _snap_with_scene
            state_text = self.state.prompt_text(_snap_with_scene(self))
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._weather_alert_text", _amo_exc)
            state_text = ""
        system = (
            await self.llm.persona(umo) + "\n\n"
            "规则：你就是角色本人，只输出要发出去的消息，不要解释、不要旁白、不要复述数据。"
        )
        prompt = (
            (state_text + "\n" if state_text else "")
            + "事实：\n" + "\n".join(facts) + "\n"
            + ("最近聊天：\n" + "\n".join(recent_lines) + "\n" if recent_lines else "")
            + "参考说法（不要照抄）：" + fallback + "\n"
            + "请用你平时跟他说话的语气，主动提一句明天的天气。\n"
            "日期只能说上面「提醒的目标日期」那一天，不要自己换算星期/节气，也不要说成今天或后天；\n"
            "要求：只发一句，20-30 字以内；像真人随口说的，不要像天气播报、客服或助手；\n"
            "不要用“提醒您/请注意/建议您/记得带伞哦/注意保暖”这种模板，不要罗列温度风力；\n"
            "可以带一点情绪（关心、吐槽、嫌麻烦都行），但别煽情；不要问“要不要带伞”，不要重复你刚说过的话。"
        )
        try:
            text = await self.llm.chat(umo, system, prompt, limit=60)
        except Exception as exc:
            logger.debug("[amo] 天气提醒文案生成失败: %s" % exc)
            text = ""
        text = self._clean_weather_alert(text)
        return text or fallback

    async def _weather_query_context(self, text=""):
        """他问天气时，用缓存里的「现在天气 + 今天 + 明天」拼一段给他看。

        只有缓存过期才会真的请求和风（现在天气 30 分钟一份、预报 3 小时一份），
        所以连着问十遍通常一次都不多请。
        """
        key = self.cfg_str("hefeng_key", "")
        city = city_or_empty(self)
        if not city:
            logger.info("[amo] 天气查询：还没设置城市")
            return "【天气查询】还没设置城市，这会儿取不到天气；别编温度，直接说看不太准。"
        now_data = {}
        try:
            now_data = await self.state.refresh_weather()
        except Exception as exc:
            logger.debug("[amo] 天气查询取现在天气失败: %s" % exc)
        if not now_data:
            now_data = getattr(self.state, "weather", {}) or {}
        items = []
        if key:
            try:
                items = await self.state._fetch_qweather_daily(key, city, 3)
            except Exception as exc:
                logger.debug("[amo] 天气查询取预报失败: %s" % exc)
        today = items[0] if items else {}
        tomorrow = items[1] if len(items) > 1 else {}
        brief = forecast_brief(now_data, today, tomorrow)
        if not brief:
            logger.info("[amo] 天气查询：没有可用数据")
            return "【天气查询】这会儿取不到天气数据，别编温度，直接说看不太准。"
        logger.info("[amo] 天气查询: %s" % brief)
        return "【天气查询】" + brief

    #: 天气提醒迟到窗口（分钟）：槽位过了这么久才跑（重启补跑/接口卡住）就记 missed，
    #: 不再补发一条迟到的天气提醒（11-20 09:54 补跑早间槽位）。
    WEATHER_ALERT_LATE_MINUTES = 15
    #: 单飞占位 TTL：占位超过这么久还没释放（进程被杀）就允许下一轮接管。
    WEATHER_ALERT_CLAIM_TTL = 120.0

    def _weather_alert_mutex(self):
        """同进程单飞：天气提醒/日记质检这类定时任务不能并发跑两次。"""
        lock = getattr(self, "_weather_alert_lock_obj", None)
        if lock is None:
            lock = asyncio.Lock()
            self._weather_alert_lock_obj = lock
        return lock

    def _cleanup_weather_state(self, today):
        """清过期状态：weather_alert_open/sent 只对当天有效，claim 只保留 TTL 内。

        11-19 的 weather_alert_open 一直留到 11-22，会让对话 prompt 误以为
        “今晚已经提醒过他天气”。过期即删，不再靠读取方自己比对日期。
        """
        try:
            opened = self.db.kv_get("weather_alert_open", "")
            if opened and str(opened) != str(today):
                self.db.kv_set("weather_alert_open", "")
                logger.info("[amo] 清理过期 weather_alert_open: %s", opened)
            sent = self.db.kv_get("weather_alert_sent", "")
            if sent and str(sent) != str(today):
                self.db.kv_set("weather_alert_sent", "")
            claim = self.db.kv_get("weather_alert_claim", {}) or {}
            if isinstance(claim, dict) and claim:
                stale = (str(claim.get("day") or "") != str(today)
                         or now_ts() - to_float(claim.get("at"), 0) > self.WEATHER_ALERT_CLAIM_TTL)
                if stale:
                    self.db.kv_set("weather_alert_claim", {})
        except Exception as exc:
            logger.debug("[amo] 天气提醒过期状态清理失败（继续）: %r", exc)

    def _mark_weather_status(self, today, slot, status, **extra):
        """把每个槽位的结果写进 kv 可审计：sent/normal/missed/error 都留痕。"""
        payload = {"day": str(today), "slot": str(slot), "status": str(status),
                   "at": now_ts()}
        payload.update(extra)
        try:
            self.db.kv_set("weather_alert_status", payload)
        except Exception as exc:
            logger.debug("[amo] 写天气提醒状态失败: %r", exc)
        if status in ("missed", "error", "send_failed"):
            logger.warning("[amo] 天气提醒（%s）未发出：%s %s"
                           % (slot, status, extra.get("reason") or ""))
        else:
            logger.info("[amo] 天气提醒（%s）状态：%s" % (slot, status))

    def _claim_weather_slot(self, today, slot, token):
        """先原子占位再发网络请求：4 秒 4 次就是因为 check 和 act 之间隔着 await。"""
        try:
            current = self.db.kv_get("weather_alert_claim", {}) or {}
            if (isinstance(current, dict) and str(current.get("day") or "") == str(today)
                    and str(current.get("slot") or "") == str(slot)
                    and now_ts() - to_float(current.get("at"), 0) < self.WEATHER_ALERT_CLAIM_TTL):
                return ""
            self.db.kv_set("weather_alert_claim", {
                "day": str(today), "slot": str(slot), "token": str(token),
                "at": now_ts(),
            })
            mine = self.db.kv_get("weather_alert_claim", {}) or {}
            if isinstance(mine, dict) and str(mine.get("token") or "") == str(token):
                return str(token)
        except Exception as exc:
            logger.debug("[amo] 天气提醒占位失败: %r", exc)
        return ""

    def _release_weather_slot(self, token):
        try:
            current = self.db.kv_get("weather_alert_claim", {}) or {}
            if (isinstance(current, dict)
                    and str(current.get("token") or "") == str(token or "")):
                self.db.kv_set("weather_alert_claim", {})
        except Exception as exc:
            logger.debug("[amo] 释放天气提醒占位失败: %r", exc)

    async def _maybe_weather_alert(self):
        """明天天气变坏时，到点提醒一次（每天最多一条）。

        判断一天只做两次：
        - 早上 `weather_alert_morning_time`（默认 08:00）：只判断、不发送；
        - 晚上 `weather_alert_time`（默认 20:00）：到点才真的发预报，
          且**跳过预报缓存**拿最新一份，保证当天晚上这条的准确性。

        2026-11-22 P0（11-21 20:00 四连/11-20 迟到 1h54m/11-22 失败 12 次静默）：
        - 单飞：同进程 asyncio.Lock + 跨重载 kv 原子占位（先占位再请求）；
        - 迟到窗口：槽位过 15 分钟记 missed，不补发迟到提醒；
        - 失败可见：接口失败/没城市/没会话都写 weather_alert_status（missed/error）；
        - 过期清理：weather_alert_open/sent 只对当天有效。
        """
        today = today_str()
        # 过期状态清理放在 enabled 判断之前：关掉开关也要把陈旧状态清掉。
        self._cleanup_weather_state(today)
        if not self.cfg_bool("weather_alert_enabled", False):
            return
        key = self.cfg_str("hefeng_key", "")
        if not key:
            return
        target = self.cfg_str("weather_alert_time", "20:00")
        evening_min = parse_clock(target, 20, 0)
        morning_raw = str(self.cfg_str("weather_alert_morning_time", "08:00") or "").strip()
        morning_min = parse_clock(morning_raw, 8, 0) if morning_raw else None
        lock = self._weather_alert_mutex()
        async with lock:
            now = _now()
            today = today_str()
            if str(self.db.kv_get("weather_alert_sent", "") or "") == today:
                return
            checked = str(self.db.kv_get("weather_alert_checked", "") or "")
            now_min = now.hour * 60 + now.minute
            slot = next_check_slot(now_min, morning_min, evening_min, checked, today)
            if not slot:
                return
            label = "早上" if slot == "morning" else "晚上"
            slot_min = morning_min if slot == "morning" else evening_min
            try:
                late = int(now_min) - int(slot_min)
            except (TypeError, ValueError):
                late = 0
            # 迟到窗口只约束会真的发消息的晚间槽位；早上槽位只做判断，晚跑不打扰用户。
            if slot == "evening" and late > self.WEATHER_ALERT_LATE_MINUTES:
                self.db.kv_set("weather_alert_checked", slot_key(today, slot))
                self._mark_weather_status(today, slot, "missed", reason="late",
                                          delay_minutes=late)
                return
            token = "weather:%s:%s:%.3f" % (today, slot, now_ts())
            if not self._claim_weather_slot(today, slot, token):
                logger.debug("[amo] 天气提醒（%s）已有另一轮在跑，跳过重复请求", label)
                return
            try:
                await self._weather_alert_slot(today, slot, label, key, target)
            finally:
                self._release_weather_slot(token)

    async def _weather_alert_slot(self, today, slot, label, key, target):
        """执行一个槽位（已占位）：取预报、判断、发送；任何失败都留状态可审计。"""
        city = city_or_empty(self)
        if not city:
            # 没设置城市就别每 30 秒重试，今天不提醒。
            self.db.kv_set("weather_alert_checked", slot_key(today, slot))
            self._mark_weather_status(today, slot, "missed", reason="no_city")
            return
        try:
            # 晚上要发预警，必须用最新预报：跳过 3 小时缓存。
            items = await self.state._fetch_qweather_daily(
                key, city, 3, fresh=(slot == "evening")
            )
        except Exception as exc:
            # 11-22 08:00 接口失败 12 次、无日志无状态：现在必须留痕，不能静默丢提醒。
            self.db.kv_set("weather_alert_checked", slot_key(today, slot))
            self._mark_weather_status(today, slot, "error", reason=type(exc).__name__)
            return
        if len(items) < 2:
            self.db.kv_set("weather_alert_checked", slot_key(today, slot))
            self._mark_weather_status(today, slot, "missed", reason="forecast_unavailable")
            return
        text = evaluate_weather_alert(
            items[0], items[1], self.cfg_int("weather_alert_temp_drop", 8)
        )
        self.db.kv_set("weather_alert_checked", slot_key(today, slot))
        if not text:
            logger.info("[amo] 天气提醒（%s）：明天天气正常，不提醒" % label)
            self._mark_weather_status(today, slot, "normal")
            return
        if slot == "morning":
            # 早上只判断，真正的预报晚上再发，一天一条不追发。
            logger.info("[amo] 天气提醒（早上）：明天要变天，晚上 %s 再发给他" % target)
            self._mark_weather_status(today, slot, "morning_alert_pending")
            return
        umos = self._weather_alert_umos()
        if not umos:
            logger.info("[amo] 天气提醒：没有可发送的会话")
            self._mark_weather_status(today, slot, "missed", reason="no_channel")
            return
        _alert_day = _day_date_of(str((items[1] or {}).get("fxDate") or ""))
        if _alert_day is None:
            _alert_day = (_now() + timedelta(days=1)).date()
        text = await self._weather_alert_text(umos[0], items[1], text, target_day=_alert_day)
        if self.cfg_bool("weather_alert_dry_run", False):
            logger.info("[amo] 天气提醒（试跑，不发送）: %s -> %s" % (text, umos[0]))
            self.db.kv_set("weather_alert_sent", today)
            self._mark_weather_status(today, slot, "dry_run")
            return
        # 一个一个平台试：某个平台的会话失效时换下一个，而不是整天发不出去
        sent_umo = ""
        for umo in umos:
            if await self._send_to_umo(umo, text, source="system", kind="weather"):
                sent_umo = umo
                break
            logger.warning("[amo] 天气提醒这个平台发送失败，换下一个: %s" % umo)
        if sent_umo:
            self.db.kv_set("weather_alert_sent", today)
            self.db.kv_set("weather_alert_open", today)
            self._mark_weather_status(today, slot, "sent", umo=sent_umo)
            logger.info("[amo] 天气提醒已发送: %s -> %s" % (text, sent_umo))
        else:
            # 所有平台都失败：清掉 checked 让下一轮重试（原来也是每轮重试）。
            self.db.kv_set("weather_alert_checked", "")
            self._mark_weather_status(today, slot, "send_failed", reason="all_channels_failed")
            logger.warning("[amo] 天气提醒所有平台都失败: %s" % umos)

    async def _switch_weather_city(self, hit):
        """按城市跟随的结果改 weather_city / 显示名，并立刻刷一次天气。"""
        city_id = str(hit.get("id") or "").strip()
        name = str(hit.get("name") or "").strip()
        if not city_id or not name:
            return
        changed = False
        if str(self.config.get("weather_city") or "") != city_id:
            self.config["weather_city"] = city_id
            changed = True
        if self.cfg_str("weather_city_name", "").strip() != name:
            self.config["weather_city_name"] = name
            changed = True
        if not changed:
            return
        try:
            save = getattr(self.config, "save_config", None)
            if callable(save):
                save()
        except Exception as exc:
            logger.warning("[amo] 城市跟随：保存天气城市失败: %s" % exc)
        await self.state.refresh_weather(force=True, city_switch=True)

    def _sync_weather_city_config(self):
        """配置里填了中文城市名时，自动换算并回填 LocationID（幂等）。"""
        raw = self.cfg_str("weather_city", "").strip()
        if not raw or raw.isdigit() or "," in raw:
            return False
        hit = self._city_lookup(raw)
        if not hit:
            # 同一个错城市名每小时只提醒一次，避免后台 30 秒一轮刷日志
            cache = getattr(self, "_city_warn_cache", None)
            if not isinstance(cache, dict):
                cache = {}
                self._city_warn_cache = cache
            now = _amo_clock.now_ts()
            if now - to_float(cache.get(raw), 0) > 3600:
                cache[raw] = now
                logger.warning(
                    "[amo] 城市名「%s」不在离线城市表里，天气会退回模拟数据；"
                    "可以写「省+市」或直接填 LocationID" % raw
                )
            return False
        if hit.get("ambiguous"):
            cache = getattr(self, "_city_warn_cache", None)
            if not isinstance(cache, dict):
                cache = {}
                self._city_warn_cache = cache
            now = _amo_clock.now_ts()
            key = "amb:" + raw
            if now - to_float(cache.get(key), 0) > 3600:
                cache[key] = now
                logger.warning(
                    "[amo] 天气城市%s" % (hit.get("note") or "有多个同名城市，已默认选一个")
                )
        changed = False
        if str(hit["id"]) != raw:
            self.config["weather_city"] = str(hit["id"])
            changed = True
        if not self.cfg_str("weather_city_name", "").strip():
            self.config["weather_city_name"] = hit["name"]
            changed = True
        if changed:
            try:
                if hasattr(self.config, "save_config"):
                    self.config.save_config()
                logger.info("[amo] 城市「%s」→ LocationID %s（已回填）" % (raw, hit["id"]))
            except Exception as exc:
                logger.error("[amo] 回填城市配置失败: %s" % exc)
        return changed

def _diary_time_rules(self, target_day=None):
    """大日记的时间边界：不写未来、熬夜写成进行时、没到的东西写成期待。

    只接受 target_day（date/ISO 字符串）：日期标签、星期、节气、节日都由
    amo_shared_time.day_label(target_day) 同源生成，禁止用「生成时刻」的标签。
    不传（旧配置/旧调用）时取今天。
    B 方案（第二天写昨天）里目标日已经过完：00:00-24:00 都算事实，深夜宵夜也算；
    旧配置 22:00 写当天时仍按「今天的事实只到当前时刻」约束。
    """
    parts = []
    now = _now()
    target = _day_date_of(target_day) if target_day is not None else now.date()
    if target is None:  # 坏日期不猜：退回今天，日志由 _day_date_of 的调用方保证
        target = now.date()
    finished = target < now.date()
    label_text = str(_day_label_of(target).get("text") or "").strip()
    if label_text:
        parts.append(
            "\n\n日期标签（唯一正确，正文开头要写日期就照这一行）：%s。"
            "这一天没有出现在标签里的星期/节气/节日都不要写；"
            "不要把第二天（尤其节气/节日）或别的日期的星期写进这一天。"
            % label_text)
    if finished:
        target_text = target.strftime("%Y-%m-%d")
        next_text = (target + timedelta(days=1)).strftime("%Y-%m-%d")
        parts.append(
            "\n\n时间边界：这是已经过完的 %s，素材里的 [今日] 都指 %s，"
            "00:00-24:00 一整天都是事实（包括 23:30 的宵夜、深夜的聊天）。"
            "现在是 %s %02d:%02d 在补写，日记里的「今天」一律是 %s、不是 %s；"
            "%s 及以后的事（第二天的清晨、上班）一个字都不要写。"
            % (target_text, target_text, now.strftime("%Y-%m-%d"), now.hour, now.minute,
               target_text, now.strftime("%Y-%m-%d"), next_text))
    else:
        parts.append("\n\n时间边界：今天的事实只到 %02d:%02d。绝对不要写明天要做什么、"
                     "明天的天气、明天才到的账单/快递；素材包里没有的未来内容一律不写。"
                     % (now.hour, now.minute))
    try:
        try:  # task-30：日记里的「此刻」也读账本，不拿旧快照的 activity
            from .amo_state_snapshot import snapshot_with_scene as _snap_with_scene
        except ImportError:
            from amo_state_snapshot import snapshot_with_scene as _snap_with_scene
        activity = (_snap_with_scene(self).get("activity") or {})
    except Exception as _amo_exc:
        _note_swallowed("amo_schedule.ScheduleMixin._generate_daily_diary.activity", _amo_exc)
        activity = {}
    if not finished and str(activity.get("category") or "") != "sleep" and now.hour >= 22:
        parts.append("\n\n她还没睡：现在 %02d:%02d 还在「%s」。日记结尾要写成进行时，"
                     "例如「今天大概就是这样啦，不过我还没睡，打算再熬一会儿」；"
                     "严禁写「今天结束了/我睡了/早点睡」这种终结句。"
                     % (now.hour, now.minute, activity.get("activity") or "做自己的事"))
    pending = ""
    try:
        for key in ("pending_delivery", "pending_order", "delivery_pending"):
            value = self.db.kv_get(key, None)
            if value:
                pending = str(value)[:60]
                break
    except Exception as _amo_exc:
        _note_swallowed("amo_schedule.ScheduleMixin._generate_daily_diary.pending", _amo_exc)
    if pending:
        parts.append("\n\n还有没到的东西：%s。日记里要写期待，"
                     "不能写成已经吃到了/已经收到了。" % pending)
    return "".join(parts)

