# -*- coding: utf-8 -*-
"""旅游日程层：目的地候选、旅游日判定、旅行日池、休息日、待办、长途腿、
出发/返程/提前返程的日程读写，以及「旅行中改城市/回来吧」的命令闸门。

依赖方向：依赖 amo_trip_state（读写/日期）与 amo_trip_cost（价签 _trip_cost、返程预算），
不依赖 report / 门面。
"""

from __future__ import annotations
from typing import Any, TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import datetime as _dt
import logging
import random

try:  # 包内加载（AstrBot 插件）
    from .amo_trip_state import (
    LOCKED_PHASES, _day_rows, _days_between, _flags_set, _load_trip, _minute_of_text,
    _now_dt, _shift, _text_of_minute, enabled, load, save, switch_weather,
)
except ImportError:  # 直接按文件加载时（tests / 仿真脚本）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_trip_state import (
        LOCKED_PHASES, _day_rows, _days_between, _flags_set, _load_trip, _minute_of_text,
        _now_dt, _shift, _text_of_minute, enabled, load, save, switch_weather,
    )
try:  # 包内加载（AstrBot 插件）
    from .amo_trip_cost import (
    _trip_cost, _wallet_balance, return_estimate,
)
except ImportError:  # 直接按文件加载时（tests / 仿真脚本）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_trip_cost import (
        _trip_cost, _wallet_balance, return_estimate,
    )

logger = logging.getLogger("amo.trip")

# ---------- 第十轮 B1：旅游日池 / 旅游待办 / 提前返程 ----------
RETURN_COMMAND_WORDS = ("回来吧", "别玩了", "该回来了", "结束旅游", "快回来", "赶紧回来")
REST_DAY_START = 8 * 60
REST_DAY_END = 20 * 60
REST_DAY_ACTIVITY = "旅游回来累瘫在家"
TRIP_PLAN_DAYS_MAX = 3
#: 旅行日只用这 8 条事件：绝不出上班/洗衣/做饭。
#: 第 5 列是价签分类（10B3）：hotel 住宿 / meal 特色大餐 / ticket 门票·纪念品 / daily 日常。

TRAVEL_EVENT_POOL = (
    ("睡到自然醒", "酒店房间", "rest", "hotel", "hotel"),
    ("在海边散步", "海边", "play", "scenic", "daily"),
    ("打卡当地景点", "景区", "play", "scenic", "ticket"),
    ("吃当地小吃", "餐厅", "meal", "restaurant", "meal"),
    ("逛夜市", "商场", "play", "mall", "daily"),
    ("回酒店躺平", "酒店房间", "rest", "hotel", "daily"),
    ("打车被绕路", "路上", "commute", "road", "daily"),
    ("买纪念品", "商场", "shopping", "mall", "ticket"),
)
#: 旅行日 3-5 块的时间窗：都在 07:00 之后、21:30 之前，互不重叠。

TRAVEL_DAY_WINDOWS = (
    (7 * 60, 9 * 60), (9 * 60 + 30, 11 * 60 + 30), (12 * 60, 14 * 60),
    (14 * 60 + 30, 16 * 60 + 30), (17 * 60, 19 * 60), (19 * 60 + 30, 21 * 60 + 30),
)
TRIP_PLAN_BLOCKS = (
    ("09:30", "11:30", "睡到自然醒"),
    ("14:00", "16:00", "打卡当地景点"),
    ("19:30", "21:00", "逛夜市"),
)
#: 旅行日池最早从 07:00 起；长途腿压到 07:00 之后的当天不再铺池（10B2 口径）。

TRAVEL_DAY_START_MIN = 7 * 60
CHANGE_WORDS = ("改去", "换成", "不去", "别去", "改成", "换到", "改到")
RETURN_WORDS = ("回来吧", "别玩了", "该回来了", "回来", "回家", "结束旅游")
DEST_WORDS = ("三亚", "大理", "厦门", "丽江", "杭州", "成都", "西安", "昆明")
# ---------- 第十轮 B2：长途去/返程 / 天气 / 钱包真扣 / 断粮 / 重载 ----------

LEG_SOURCE = "transit"
LEG_PACK_MIN = 30
LEG_STATION_MIN = (45, 60)
LEG_RIDE_MIN = (180, 240)
LEG_TOTAL_MAX = 300
RETURN_START_MINUTE = 16 * 60

def candidates(plugin, limit=4):
    """目的地只从 scenic_spots() 城市池里取，不编城市。"""
    cities = []
    try:
        spots = plugin.state.scenic_spots() or []
    except Exception:
        spots = []
    for spot in spots:
        city = ""
        if isinstance(spot, dict):
            city = str(spot.get("city") or "").strip()
            if not city:
                name = str(spot.get("name") or spot.get("place") or "")
                city = name.split("·")[0].strip()
        else:
            city = str(spot or "").split("·")[0].strip()
        if city and city not in cities:
            cities.append(city)
    return cities[:max(1, int(limit))]


def guard_change(plugin, text):
    """目的地锁：出发后改城市一律拦下，只放行回来。返回 {action, reply}。"""
    if not enabled(plugin):
        return {"action": "none", "reply": ""}
    trip = load(plugin)
    phase = str(trip.get("phase") or "")
    body = str(text or "")
    if any(word in body for word in RETURN_WORDS):
        if phase == "traveling":
            return {"action": "return", "reply": ""}
        if phase in LOCKED_PHASES:
            return {"action": "none", "reply": "已经在路上了，等到了再说吧。"}
        return {"action": "none", "reply": ""}
    if phase in LOCKED_PHASES:
        if any(word in body for word in CHANGE_WORDS) or \
                (any(city in body for city in DEST_WORDS) and "去" in body):
            logger.info("[trip] destination_locked phase=%s text=%s", phase, body[:40])
            return {"action": "blocked",
                    "reply": "票都买好了，都上车了，改不了啦，下次再去吧。"}
    if phase in ("planning", "approved") and any(word in body for word in CHANGE_WORDS):
        return {"action": "change", "reply": ""}
    return {"action": "none", "reply": ""}


def is_return_command(plugin, text):
    """只有真正的「叫回来」指令才算提前返程；「下班回家」这类日程文案不算。"""
    if not enabled(plugin):
        return False
    body = str(text or "")
    return any(word in body for word in RETURN_COMMAND_WORDS)


def day_kind(plugin, day):
    """旅游日：traveling / rest / ""（其他日子完全不变）。

    10B2：outbound/returning 当天已经有自己的长途块，不再生成旅游日池；
    rest 阶段的休息日由 rest_day 标记（兼容 10B1 的 returning+次日 口径）。
    """
    try:
        if not enabled(plugin):
            return ""
        trip = load(plugin)
        phase = str(trip.get("phase") or "")
        start_day = str(trip.get("start_day") or "")
        day = str(day or "")
        if not start_day or not day:
            return ""
        return_day = str(trip.get("return_day") or start_day)
        if phase == "traveling" and start_day <= day <= return_day:
            return "traveling"
        rest_day = str(trip.get("rest_day") or "")
        if phase == "rest" and rest_day and day == rest_day:
            return "rest"
        if phase in ("returning", "done") and day == _shift(return_day, 1):
            return "rest"
        return ""
    except Exception as exc:
        logger.debug("[trip] day_kind 判断失败: %s", exc)
        return ""


def travel_day_blocks(plugin, day):
    """旅行日池：8 条事件里按稳定种子抽 3-5 块，07:00-21:30 内、互不重叠。

    10B3：每块带 cost 元数据（kind + 金额分 + 钱包分类）；住宿每晚恰好一块，
    大餐/门票各最多一块，多出来的同类降级成日常（按块的小时幂等）。
    """
    trip = load(plugin)
    trip_id = str(trip.get("id") or "")
    day = str(day or "")
    rng = random.Random("trip-pool|%s|%s" % (trip_id, day))
    count = rng.randint(3, 5)
    # 住宿每晚且仅一次：先把唯一的「酒店」事件钉进池子，其余随机抽。
    hotel_events = [event for event in TRAVEL_EVENT_POOL if event[4] == "hotel"]
    others = [event for event in TRAVEL_EVENT_POOL if event[4] != "hotel"]
    rng.shuffle(others)
    chosen = hotel_events[:1] + others[:max(0, count - 1)]
    rng.shuffle(chosen)
    windows = sorted(rng.sample(range(len(TRAVEL_DAY_WINDOWS)), count))
    picked = []
    for event, index in zip(chosen, windows):
        start_min, end_min = TRAVEL_DAY_WINDOWS[index]
        picked.append((start_min, end_min, event))
    picked.sort(key=lambda item: item[0])
    rows = []
    claimed = set()
    for start_min, end_min, event in picked:
        activity, place, category, zone, cost_kind = event
        kind = cost_kind
        if kind in claimed:
            kind = "daily"  # 每类每天最多一次，多出来的那条按日常结
        if kind != "daily":
            claimed.add(kind)
        start_text = _text_of_minute(start_min)
        rows.append({
            "start": start_text, "end": _text_of_minute(end_min),
            "activity": activity, "place": place, "zone": zone,
            "category": category, "source": "trip_pool", "trip": trip_id,
            "appointment": True,
            "cost": _trip_cost(kind, trip_id, day, activity, start_text),
        })
    return rows


def travel_event_for_hour(plugin, day, hour):
    """旅行日空档的到点动作：仍只从 8 条事件池里取，绝不出上班/洗衣/做饭。

    22:00-06:00 的深夜约束对所有日子生效：回酒店休息，不再抽「逛夜市/吃小吃」
    这种商场/餐厅到店块（旅游日也要守这条社会逻辑）。

    10B3：返回的 plan 带同一套 cost 元数据；到点不扣钱（cost_cents 仍是 0），
    真实消费只由 settle_due 按「已经发生的块」结算，同一幂等键也不会重复扣。
    """
    hour = int(hour)
    trip = load(plugin)
    trip_id = str(trip.get("id") or "")
    day = str(day or "")
    if hour >= 22 or hour < 6:
        return {"action": "回酒店休息", "place": "酒店房间", "zone": "hotel",
                "category": "rest", "cost_cents": 0, "diary": "",
                "cost": None}
    rng = random.Random("trip-hour|%s|%s|%s" % (trip_id, day, hour))
    activity, place, category, zone, cost_kind = \
        TRAVEL_EVENT_POOL[rng.randrange(len(TRAVEL_EVENT_POOL))]
    return {"action": activity, "place": place, "zone": zone,
            "category": category, "cost_cents": 0, "diary": "",
            "cost": _trip_cost(cost_kind, trip_id, day, activity,
                               "%02d:00" % hour)}


def rest_day_rows(plugin, day, existing=None):
    """休息日：08:00-20:00 一块「旅游回来累瘫在家」，当天旧活动块截断/清掉。"""
    trip = load(plugin)
    trip_id = str(trip.get("id") or "")
    rows = []
    for row in existing or []:
        if not isinstance(row, dict):
            continue
        begin = _minute_of_text(row.get("start"))
        finish = _minute_of_text(row.get("end"))
        if begin is None or finish is None or finish <= begin:
            rows.append(dict(row))
            continue
        if finish <= REST_DAY_START or begin >= REST_DAY_END:
            rows.append(dict(row))
            continue
        if begin < REST_DAY_START:
            kept = dict(row)
            kept["end"] = _text_of_minute(REST_DAY_START)
            rows.append(kept)
        if finish > REST_DAY_END:
            kept = dict(row)
            kept["start"] = _text_of_minute(REST_DAY_END)
            rows.append(kept)
    rows.append({"start": _text_of_minute(REST_DAY_START),
                 "end": _text_of_minute(REST_DAY_END),
                 "activity": REST_DAY_ACTIVITY, "place": "家", "zone": "home",
                 "category": "rest", "source": "trip_rest", "trip": trip_id,
                 "appointment": True})
    rows.sort(key=lambda row: (_minute_of_text(row.get("start")) or 24 * 60))
    return rows, True


def trip_todo_items(plugin, trip):
    """未来 2-3 天的旅游待办（只进 todo 表，不铺未来日程）；锚点 source=trip:<id>。"""
    trip = dict(trip or {})
    city = str(trip.get("city") or "").strip()
    trip_id = str(trip.get("id") or "").strip()
    start_day = str(trip.get("start_day") or "").strip()
    try:
        days = int(trip.get("days") or 0)
    except (TypeError, ValueError):
        days = 0
    if not city or not start_day or days < 2:
        return {}
    today = ""
    try:
        today = str(getattr(plugin, "today", lambda: "")() or "")
    except Exception:
        today = ""
    if not today:
        import datetime as _dt
        today = _dt.date.today().isoformat()
    anchor = "trip:%s" % (trip_id or "unknown")
    out = {}
    for offset in range(1, min(TRIP_PLAN_DAYS_MAX, days - 1) + 1):
        day = _shift(start_day, offset)
        if not day or day <= today:
            continue
        items = []
        for start, end, activity in TRIP_PLAN_BLOCKS:
            items.append({
                "start": start, "end": end, "activity": activity,
                "place": city, "category": "travel", "source": anchor,
                "trip": trip_id, "cost_cents": 0,
            })
        out[day] = items
    return out


def write_todos(plugin, trip):
    """approved/手动出发后：给未来 2-3 天写旅游待办，不直接写未来日程。"""
    plan = trip_todo_items(plugin, trip)
    state: Any = getattr(plugin, "state", None)
    commit = getattr(state, "_commit_todo_day", None)
    if not plan or not callable(commit):
        return {"ok": False, "reason": "no_todo_sink", "days": {}}
    written = {}
    for day, items in plan.items():
        try:
            existing = [dict(row) for row in (state.todos(day) or [])
                        if isinstance(row, dict)]
        except Exception as exc:
            logger.debug("[trip] 读已有待办失败，只写旅游待办: %s", exc)
            existing = []
        try:
            commit(day, existing + items, source="trip")
        except Exception as exc:
            logger.warning("[trip] 写旅游待办失败 day=%s: %s", day, exc)
            continue
        written[day] = len(items)
    if written:
        logger.info("[trip] 旅游待办已写好：%s", ",".join(sorted(written)))
    return {"ok": bool(written), "days": written}


def _row_span(row):
    if not isinstance(row, dict):
        return None
    begin = _minute_of_text(row.get("start"))
    finish = _minute_of_text(row.get("end"))
    if begin is None or finish is None or finish <= begin:
        return None
    return begin, finish


def _commit_day(plugin, day, rows, old_items, source, reason):
    state = getattr(plugin, "state", None)
    commit = getattr(state, "commit_schedule_change", None)
    if callable(commit):
        try:
            return commit(day, rows, old_items=old_items, source=source, reason=reason)
        except Exception as exc:
            logger.warning("[trip] 写日程失败 day=%s source=%s: %s", day, source, exc)
            return {}
    schedule = getattr(state, "schedule", None) if state is not None else None
    if isinstance(schedule, dict):
        schedule[day] = [dict(row) for row in rows]
    db = getattr(plugin, "db", None)
    saver = getattr(db, "save_schedule", None)
    if callable(saver):
        try:
            saver(day, rows)
            return {"changed": True, "saved": True}
        except Exception as exc:
            logger.warning("[trip] 落库失败 day=%s: %s", day, exc)
    return {"changed": False, "saved": False}


def _truncate_from(rows, start_min):
    """出发/返程占掉当天 start_min 之后的时间：截断跨点块、丢弃之后的块。"""
    kept = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        span = _row_span(row)
        if not span:
            kept.append(dict(row))
            continue
        begin, finish = span
        if finish <= start_min:
            kept.append(dict(row))
            continue
        if begin >= start_min:
            continue
        piece = dict(row)
        piece["end"] = _text_of_minute(start_min)
        kept.append(piece)
    return kept


def _leg_rows(plugin, trip, day, start_min, kind):
    """收拾行李 30m → 去车站/机场 45-60m → 高铁/飞机 3-4h，总长 3-5h。

    10B3：跨 0 点的长途按自然日切开（当天到 24:00、第二天从 00:00 接着走），
    绝不出现单块跨 0 点，也不把 start 写成 24:00 以后；返回 ({日期: [块]}, 总分钟)。
    """
    trip_id = str((trip or {}).get("id") or "")
    city = str((trip or {}).get("city") or "")
    rng = random.Random("trip-leg|%s|%s|%s" % (trip_id, day, kind))
    pack = LEG_PACK_MIN
    station = rng.randint(*LEG_STATION_MIN)
    ride = rng.randint(*LEG_RIDE_MIN)
    ride = min(ride, LEG_TOTAL_MAX - pack - station)
    ride = max(60, int(ride))
    total = pack + station + ride
    use_plane = rng.random() < 0.5
    station_place = "机场" if use_plane else "车站"
    station_activity = "打车去机场" if use_plane else "打车去高铁站"
    if kind == "outbound":
        pack_activity = "收拾行李，准备去%s" % (city or "外地")
        ride_activity = "在飞机上" if use_plane else "坐高铁去%s" % (city or "外地")
    else:
        pack_activity = "收拾行李，准备回家"
        ride_activity = "在飞机上" if use_plane else "坐高铁回家"
    plans = (
        (pack_activity, "家", "home", pack),
        (station_activity, station_place, "road", station),
        (ride_activity, "路上", "road", ride),
    )
    try:
        base_day = _dt.date.fromisoformat(str(day or ""))
    except ValueError as exc:
        logger.debug("[trip] 长途起始日解析失败，按今天: %s", exc)
        base_day = _amo_clock.now().date()
    by_day: dict[str, Any] = {}
    cursor = int(start_min)
    for activity, place, zone, minutes in plans:
        remaining = int(minutes)
        while remaining > 0:
            offset, minute_of_day = divmod(cursor, 24 * 60)
            piece = min(24 * 60 - minute_of_day, remaining)
            row_day = (base_day + _dt.timedelta(days=offset)).isoformat()
            by_day.setdefault(row_day, []).append({
                "start": _text_of_minute(minute_of_day),
                "end": _text_of_minute(minute_of_day + piece),
                "activity": activity, "place": place, "zone": zone,
                "category": "commute", "source": LEG_SOURCE,
                "trip": trip_id, "leg": kind,
            })
            cursor += piece
            remaining -= piece
    return by_day, total


def travel_day_legs(rows):
    """旅行日里已经写好的长途腿：返回 (全部腿, 压到 07:00 之后的那几条)。

    跨午夜到达日只占 00:00-到达点，白天还要铺旅行日池；白天出发/返程当天
    长途压到 07:00 以后，继续守 10B2 的「不覆盖」口径。
    """
    legs = [dict(row) for row in (rows or [])
            if isinstance(row, dict)
            and (row.get("leg") or str(row.get("source") or "") == LEG_SOURCE)]
    late = [row for row in legs
            if (_minute_of_text(row.get("end")) or 0) > TRAVEL_DAY_START_MIN]
    return legs, late


def _start_outbound(plugin, trip, now=None, day=""):
    """phase=outbound：写当天长途去程块，并切天气城市。"""
    trip = dict(trip or {})
    now_dt = _now_dt(now)
    day = str(day or trip.get("start_day") or now_dt.date().isoformat())
    try:
        base_day = _dt.date.fromisoformat(day)
    except ValueError as exc:
        logger.debug("[trip] 出发日解析失败，按今天: %s", exc)
        base_day = now_dt.date()
        day = base_day.isoformat()
    start_min = now_dt.hour * 60 + now_dt.minute
    rows_by_day, total = _leg_rows(plugin, trip, day, start_min, "outbound")
    end_min = (int(start_min) + int(total)) % (24 * 60)
    arrive_day = (base_day + _dt.timedelta(
        days=(int(start_min) + int(total)) // (24 * 60))).isoformat()
    trip["phase"] = "outbound"
    trip["start_day"] = day
    trip["day_no"] = 0
    trip["outbound_start"] = _text_of_minute(start_min)
    trip["outbound_end"] = _text_of_minute(end_min)
    trip["outbound_arrive_day"] = arrive_day
    base = _dt.datetime.combine(base_day, _dt.time(start_min // 60, start_min % 60))
    trip["outbound_ts"] = int(base.timestamp()) + int(total) * 60
    switched = switch_weather(plugin, trip)
    if isinstance(switched.get("trip"), dict):
        trip = switched["trip"]
    save(plugin, trip)
    for row_day in sorted(rows_by_day):
        rows = rows_by_day[row_day]
        existing = _day_rows(plugin, row_day)
        if row_day == day:
            _commit_day(plugin, row_day, _truncate_from(existing, start_min) + rows,
                        existing, "trip_depart", "旅游出发")
        else:
            # 第二天 00:00 起由长途占满：当天旧日程整份作废，也不拿旧块当 blocker
            # （否则 00:00 开始的睡眠/旧骨架会把长途切片整个截掉）。
            _commit_day(plugin, row_day, rows, [], "trip_leg", "旅游出发（跨午夜）")
    logger.info("[trip] 出发去%s：%s 出发（%d 分钟），%s %s 到达", trip.get("city"),
                _text_of_minute(start_min), int(total), arrive_day,
                _text_of_minute(end_min))
    return trip


def _should_return(plugin, trip, today, now_min):
    return_day = str(trip.get("return_day") or trip.get("start_day") or "")
    flags = trip.get("flags") or {}
    if flags.get("waiting_funds"):
        balance = _wallet_balance(plugin)
        need = return_estimate(plugin)
        if balance is None or int(balance) < need:
            return False
        return True
    if not return_day:
        return False
    if today > return_day:
        return True
    return today == return_day and int(now_min) >= RETURN_START_MINUTE


def begin_return(plugin, trip, now=None, reason="", start_min=None):
    """phase=returning：写 3-5h 返程块，并记录到家时间戳。"""
    trip = dict(trip or {})
    phase = str(trip.get("phase") or "")
    if phase == "returning" and str(trip.get("return_ts") or ""):
        return trip
    if phase not in LOCKED_PHASES:
        return trip
    now_dt = _now_dt(now)
    day = now_dt.date().isoformat()
    now_min = now_dt.hour * 60 + now_dt.minute
    start = int(start_min if start_min is not None else now_min)
    start = max(0, min(24 * 60 - 1, start))
    rows_by_day, total = _leg_rows(plugin, trip, day, start, "return")
    end_min = (start + int(total)) % (24 * 60)
    arrive_day = (now_dt.date() + _dt.timedelta(
        days=(start + int(total)) // (24 * 60))).isoformat()
    base = _dt.datetime.combine(now_dt.date(), _dt.time(start // 60, start % 60))
    trip["phase"] = "returning"
    trip["return_day"] = day
    trip["return_start"] = _text_of_minute(start)
    trip["return_end"] = _text_of_minute(end_min)
    trip["return_arrive_day"] = arrive_day
    trip["return_ts"] = int(base.timestamp()) + int(total) * 60
    trip["day_no"] = max(1, int(trip.get("day_no") or 0),
                         min(int(trip.get("days") or 1),
                             _days_between(trip.get("start_day"), day) + 1))
    flags = dict(trip.get("flags") or {})
    flags.pop("waiting_funds", None)
    flags["return_reason"] = str(reason or "days_over")
    trip["flags"] = flags
    save(plugin, trip)
    for row_day in sorted(rows_by_day):
        rows = rows_by_day[row_day]
        existing = _day_rows(plugin, row_day)
        if row_day == day:
            _commit_day(plugin, row_day, _truncate_from(existing, start) + rows,
                        existing, "trip_return", "旅游返程")
        else:
            _commit_day(plugin, row_day, rows, [], "trip_leg", "旅游返程（跨午夜）")
    logger.info("[trip] 返程：%s，%s 出发（%d 分钟），%s %s 到家", trip.get("city"),
                _text_of_minute(start), int(total), arrive_day,
                _text_of_minute(end_min))
    return trip


def shorten(plugin, now=None):
    """断粮兜底一：缩短行程，立刻往回走。"""
    trip = _load_trip(plugin)
    phase = str(trip.get("phase") or "")
    if phase not in LOCKED_PHASES:
        return {"ok": False, "reason": "not_traveling", "phase": phase, "line": ""}
    now_dt = _now_dt(now)
    trip["days"] = max(1, int(trip.get("day_no") or 1))
    trip["return_day"] = now_dt.date().isoformat()
    _flags_set(trip, shortened=True)
    save(plugin, trip)
    trip = begin_return(plugin, trip, now_dt, reason="shorten")
    return {"ok": True, "trip": trip, "phase": "returning",
            "line": "那我少玩几天，先回家。"}


def wait_for_funds(plugin):
    """断粮兜底二：等她入账；sync 检测到余额够就继续回程。"""
    trip = _load_trip(plugin)
    phase = str(trip.get("phase") or "")
    if phase not in LOCKED_PHASES:
        return {"ok": False, "reason": "not_traveling", "phase": phase, "line": ""}
    _flags_set(trip, waiting_funds=True)
    save(plugin, trip)
    return {"ok": True, "trip": trip, "phase": phase,
            "line": "那我先在这儿等你打钱，到账我就买票回去。"}
