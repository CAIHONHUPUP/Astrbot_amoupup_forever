# -*- coding: utf-8 -*-
"""每日日记质检（2026-11-16）：只审「已经过完」的日程块。

为什么有这一层：块级小日记是生成式产物，模型会漏块、写空、把相邻两块写成同一段，
或者写到当天没去过的地方。以前只能靠人翻面板发现，这里逐块对一遍：

- HARD：缺日记 / 空文本 / 已标 stale 待重写 / block_id 对不上 / 命中该地点的 forbidden 词；
- WARN：相邻块日记前 30 字相同（sleep 占位除外）、时间词和块窗口差 >1 小时、过长/过短。

HARD 在 apply 时用 db.mark_block_diary_stale(day, block_id) 标 stale，再交给现有
小日记调度（每 tick 最多 2 条，受 diary_backfill_minute 预算）重写；WARN 只进报告。
只审已结束的块：未结束的块还没生成，报出来只是噪音；sleep 块允许占位/缺。

幂等键：diary_audit_done:<day>；报告：diary_audit:<day>；
私聊通知：diary_audit_notified:<day>。run() 是同步函数（只碰 db 和纯文本），
后台 tick 用 asyncio.to_thread 包一层，CLI 也能直接调。
"""
from __future__ import annotations

import threading
_NOTIFY_LOCK = threading.Lock()
from typing import Any, TYPE_CHECKING

import asyncio
import re
from datetime import datetime as _datetime
from datetime import timedelta

try:  # 包模式优先，按文件直接加载时退回绝对导入
    from .amo_observability import note as _note_swallowed
    from .amo_shared import _now, now_ts
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_observability import note as _note_swallowed
        from amo_shared import _now, now_ts

try:
    from .amo_state_common import block_id, block_places, slot_has_ended
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_state_common import block_id, block_places, slot_has_ended

#: kv 键前缀（尾部的冒号别去掉，后面直接拼日期）
KEY_DONE = "diary_audit_done:"
KEY_REPORT = "diary_audit:"
KEY_NOTIFIED = "diary_audit_notified:"
#: housekeeping 里两次检查的最小间隔（秒）
MIN_INTERVAL_SECONDS = 600
#: 相邻块日记比较的前 N 个字
DUP_PREFIX_CHARS = 30
#: 时间词和块窗口允许的偏差（分钟）
TIME_TOLERANCE_MINUTES = 60
#: 块日记长度阈值（字）；小日记提示词要求 120-260 字，放宽一点只抓明显异常
MIN_TEXT_CHARS = 40
MAX_TEXT_CHARS = 400
DEFAULT_LOOKBACK_DAYS = 3
DEFAULT_QUIET_HOURS = "22-8"

#: 人工冻结/归档的块不审、不动
_FROZEN_STATUS = ("frozen", "archived")

_REASON_TEXT = {
    "missing": "缺日记",
    "stale": "待重写",
    "empty": "空日记",
    "block_mismatch": "块对不上",
    "place_forbidden": "地点不符",
    "dup_prefix": "相邻块重复",
    "time_word": "时间可疑",
    "too_short": "太短",
    "too_long": "太长",
    "orphan": "孤儿日记",
    "version_mismatch": "版本对不上",
    "place_mismatch": "地点对不上",
    "companion": "同伴对不上",
    "meal": "吃饭对不上",
    "money": "金额对不上",
    "health": "健康对不上",
    "weather": "天气对不上",
    "weather_missing": "缺当天天气",
    "health_missing": "缺当天健康",
    "weather_city": "天气城市不一致",
}

#: 时段词 → 代表时刻（分钟），只用来把「晚上」这类模糊词落成一个点
_PERIOD_MINUTES = {
    "凌晨": 60, "早上": 480, "上午": 600, "中午": 720,
    "下午": 900, "傍晚": 1110, "晚上": 1200, "夜里": 1380,
}
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUM = r"(?:\d{1,2}|十[一二]?|[零〇一二两三四五六七八九])"
_CLOCK_RE = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})")
_POINT_RE = re.compile(r"(%s)\s*点\s*(半|[0-9]{1,2}\s*分?)?" % _NUM)


# ---------------------------------------------------------------- 配置读取


def _cfg_bool(plugin, key, default):
    getter = getattr(plugin, "cfg_bool", None)
    if callable(getter):
        try:
            return bool(getter(key, default))
        except Exception as exc:
            _note_swallowed("amo_diary_audit.cfg_bool", exc)
    return bool(default)


def _cfg_int(plugin, key, default):
    getter = getattr(plugin, "cfg_int", None)
    if callable(getter):
        try:
            return int(getter(key, default))
        except Exception as exc:
            _note_swallowed("amo_diary_audit.cfg_int", exc)
    try:
        return int(default)
    except (TypeError, ValueError):
        return 0


def _cfg_str(plugin, key, default):
    getter = getattr(plugin, "cfg_str", None)
    if callable(getter):
        try:
            return str(getter(key, default))
        except Exception as exc:
            _note_swallowed("amo_diary_audit.cfg_str", exc)
    return str(default)


# ---------------------------------------------------------------- 日程块


def _sorted_rows(items):
    """过滤脏 start 后按开始时间排序（和 amo_schedule._sorted_blocks 同口径）。"""
    rows: list[Any] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        start = str(item.get("start") or "").strip()
        if not re.match(r"^\d{1,2}:\d{2}$", start):
            continue
        rows.append(item)

    def _key(item):
        hour_text, _, minute_text = str(item.get("start") or "")[:5].partition(":")
        try:
            return int(hour_text) * 60 + int(minute_text or 0)
        except (TypeError, ValueError):
            return 24 * 60

    rows.sort(key=_key)
    return rows


def _clock_key(value):
    """HH:MM 归一（9:00 与 09:00 是同一格）。"""
    body = str(value or "").strip()[:5]
    hour_text, _, minute_text = body.partition(":")
    try:
        return "%02d:%02d" % (int(hour_text), int(minute_text or 0))
    except (TypeError, ValueError):
        return body


def _minutes_of(value):
    body = str(value or "").strip()[:5]
    hour_text, _, minute_text = body.partition(":")
    try:
        return int(hour_text) * 60 + int(minute_text or 0)
    except (TypeError, ValueError):
        return None


def _day_rows(plugin, day):
    """取当天原始日程块：内存里的优先，退回数据库。"""
    state = getattr(plugin, "state", None)
    schedule = getattr(state, "schedule", None) if state is not None else None
    rows: list[Any] = []
    if isinstance(schedule, dict):
        try:
            rows = schedule.get(day) or []
        except Exception as exc:
            _note_swallowed("amo_diary_audit.day_rows.memory", exc)
    if not rows:
        getter = getattr(getattr(plugin, "db", None), "get_schedule", None)
        if callable(getter):
            try:
                rows = getter(day) or []
            except Exception as exc:
                _note_swallowed("amo_diary_audit.day_rows.db", exc)
    return rows if isinstance(rows, list) else []


def _window_index(db, day):
    """当天已有 block_diary 行按「开始-结束」分组：用来发现对不上的旧 block_id。"""
    rows: list[Any] = []
    lister = getattr(db, "list_block_diaries", None)
    if callable(lister):
        try:
            rows = lister(day) or []
        except Exception as exc:
            _note_swallowed("amo_diary_audit.window_index", exc)
    index: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        # 只按开始时间归组：旧迁移行的 end 可能是空的，按 start+end 会漏判「对不上」。
        index.setdefault(_clock_key(row.get("start")), []).append(row)
    return index


def _same_window(index, item):
    return index.get(_clock_key(item.get("start")), [])


# ---------------------------------------------------------------- 地点闸


def _place_hits(plugin, item, rows, text):
    """单地点块用 loc.check，跨地点块用地点集合的 day 口径（和白天的地点闸一致）。"""
    try:
        try:
            from . import amo_location_rules as loc
        except ImportError:
            import amo_location_rules as loc
    except Exception as exc:
        _note_swallowed("amo_diary_audit.place_hits.import", exc)
        return []
    try:
        if not loc.enabled(plugin):
            return []
        spots = [str(place) for place in block_places(rows, item) if str(place).strip()]
        zone = str(item.get("zone") or "")
        if len(spots) > 1:
            return list(loc.day_check(text, spots, True) or [])
        place = spots[0] if spots else str(item.get("place") or "")
        return list(loc.check(text, place, zone) or [])
    except Exception as exc:
        _note_swallowed("amo_diary_audit.place_hits", exc)
        return []


# ---------------------------------------------------------------- 时间词


def _hour_of(token):
    """「8」/「八」/「十一」→ 小时；认不出返回 -1。"""
    value = str(token or "").strip()
    if not value:
        return -1
    if value.isdigit():
        return int(value)
    if value[0] == "十":
        if len(value) > 2:
            return -1
        return 10 + (_CN_DIGITS.get(value[1:], 0) if len(value) > 1 else 0)
    if len(value) == 1:
        return _CN_DIGITS.get(value, -1)
    return -1


def _time_mentions(text):
    """[(hour, minute, period)]：period 为空表示原文没写时段词。"""
    value = str(text or "")
    mentions = []
    for match in _CLOCK_RE.finditer(value):
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 24 and 0 <= minute < 60:
            mentions.append((hour, minute, ""))
    for match in _POINT_RE.finditer(value):
        hour = _hour_of(match.group(1))
        if hour < 0 or hour > 24:
            continue
        minute = 0
        suffix = str(match.group(2) or "").strip()
        if suffix.startswith("半"):
            minute = 30
        elif suffix:
            digits = re.sub(r"\D", "", suffix)
            if digits:
                minute = int(digits)
        if minute >= 60:
            minute = 0
        prefix = value[max(0, match.start() - 3):match.start()]
        period = ""
        for word in _PERIOD_MINUTES:
            if prefix.endswith(word):
                period = word
                break
        token = str(match.group(1) or "")
        if not token.isdigit() and not period and not suffix:
            # 中文「一点」八成是「有点热 / 一点点 / 一点不」这种量词，不是钟点：
            # 没时段词、没分钟后缀时，只认「十一点」这种复合词或「点多/点了/点左右」。
            tail = value[match.end():match.end() + 1]
            if token not in ("十一", "十二") and tail not in ("多", "了", "钟", "整", "左"):
                continue
        mentions.append((hour, minute, period))
    return mentions


def _hour_candidates(hour, period):
    """一个「X点」在 24 小时制下的可能读法。

    没写时段词时 1-11 点有上午/下午两种读法，两种都留给窗口去筛：中文日记里
    「快四点」落在 15:00-16:00 的块里是下午四点，不能按 04:00 误报；反过来
    「晚上八点半」写了时段词，就只认 20:30，放进早上的块才会报可疑。
    """
    if period in ("下午", "傍晚", "晚上", "夜里"):
        if 1 <= hour < 12:
            return (hour + 12,)
        if hour == 12:
            return (24,)
        return (hour,)
    if period in ("早上", "上午"):
        return (hour,)
    if period == "中午":
        return (hour + 12,) if 1 <= hour < 11 else (hour,)
    if period == "凌晨":
        return (0,) if hour == 12 else (hour,)
    if 1 <= hour < 12:
        return (hour, hour + 12)
    if hour == 12:
        return (0, 12)
    return (hour,)


def parse_time_words(text):
    """从一段中文里抠出「几点」：06:30 / 8点 / 晚上八点半 → 分钟数列表。

    没写时段词的 1-11 点会同时给出两种读法（见 _hour_candidates）；窗口判定用
    _time_word_detail，两种读法有一种贴着窗口就不算可疑。
    """
    out = []
    for hour, minute, period in _time_mentions(text):
        for candidate in _hour_candidates(hour, period):
            value = candidate * 60 + minute
            if value not in out:
                out.append(value)
    return out


def _window_distance(minute, begin, finish):
    """时间点离 [begin, finish] 窗口的分钟数（在窗口里是 0）。"""
    if minute < begin:
        return begin - minute
    if minute > finish:
        return minute - finish
    return 0


def _time_word_detail(text, start, end):
    """时间词和块窗口差 >1 小时 → 返回最接近窗口的那个时间点（HH:MM），否则空串。"""
    mentions = _time_mentions(text)
    if not mentions:
        return ""
    begin = _minutes_of(start)
    if begin is None:
        return ""
    finish = _minutes_of(end)
    if finish is None or finish <= begin:
        finish = begin + 60
    worst = None
    worst_distance = 0
    for hour, minute, period in mentions:
        candidates = [candidate * 60 + minute for candidate in _hour_candidates(hour, period)]
        nearest = min(candidates, key=lambda value: _window_distance(value, begin, finish))
        distance = _window_distance(nearest, begin, finish)
        if distance > worst_distance:
            worst = nearest
            worst_distance = distance
    if worst is None or worst_distance <= TIME_TOLERANCE_MINUTES:
        return ""
    return "%02d:%02d" % (worst // 60, worst % 60)


# ---------------------------------------------------------------- 判定

def _collect_warns(warn, item, text, prev):
    """WARN 三类：相邻块重复、时间可疑、过长/过短。"""
    start = str(item.get("start") or "")
    end = str(item.get("end") or "")
    body = str(text or "").strip()
    if not body:
        return
    if prev and prev.get("text") and not prev.get("sleep"):
        left = str(prev.get("text") or "")[:DUP_PREFIX_CHARS]
        right = body[:DUP_PREFIX_CHARS]
        if len(left) >= 6 and left == right:
            warn.append({"start": start, "end": end, "kind": "dup_prefix",
                         "detail": left[:12]})
    moment = _time_word_detail(body, start, end)
    if moment:
        warn.append({"start": start, "end": end, "kind": "time_word", "detail": moment})
    if len(body) < MIN_TEXT_CHARS:
        warn.append({"start": start, "end": end, "kind": "too_short",
                     "detail": str(len(body))})
    elif len(body) > MAX_TEXT_CHARS:
        warn.append({"start": start, "end": end, "kind": "too_long",
                     "detail": str(len(body))})


# ---------------------------------------------------------------- 事实维度（P0，2026-11-22）
#: 日记里「他就在身边」的强断言；愿望/回忆句不算（和 amo_diary_guard 同口径的本地精简版，
#: 避免 audit 为一条正则新增模块依赖）。
_PRESENCE_CLAIM = re.compile(
    r"(?:他|老公)(?:正|就)?(?:坐在|坐|躺|靠在?|抱着?|搂着?|牵着?|握着?|捏着?|"
    r"递给我|喂我|夹给|摸我|亲我|陪着我)")
_WISH_MARKERS = ("要是", "如果", "希望", "好想", "想他", "梦见", "梦到",
                 "以前", "那次", "记得那", "那年")
#: 明确说没吃（「没吃完」不算：中间夹了「完」）。
_MEAL_NEGATION = re.compile(r"(?:没|没有|还没)(?:吃|吃东西|吃饭|吃上)|不饿|没胃口")
_MEAL_BLOCK_WORDS = ("吃", "饭", "宵夜", "夜宵", "外卖", "火锅", "奶茶", "咖啡", "餐")
_MONEY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:块(?:钱)?|元|rmb)")
_TEMP_RE = re.compile(r"(-?\d{1,2})\s*(?:℃|度)")
_HEALTH_NEGATION = re.compile(
    r"(?:病|感冒|发烧|烧)(?:已经)?(?:好|退)了|没生病|身体(?:很)?好|不(?:难受|发烧|感冒)了")
_HEALTH_BLOCK_WORDS = ("医院", "看病", "发烧", "感冒", "低烧", "生病", "吃药", "打针", "挂水")
#: 居家块里第一人称「我去了外面」的强断言（「他还在公司」这种别人视角不算）。
_FIRST_PERSON_OUT = re.compile(
    r"我(?:正|还|就|又)?(?:在|去|到|回|坐|挤|上|下|赶|逛)(?:了|着)?"
    r"(?:公司|地铁|公交|商场|街上|外面|楼下|小馆|馆子|餐厅|超市|便利店|公园)")
#: 正文里出现这些地点/动作，在家块里就是「日程在家、日记在外面」。
_HOME_CONFLICT_WORDS = ("逛街", "逛商场", "去了商场", "坐地铁", "挤地铁")
#: 居家块的 safe 说法（否定句不算出门）。
_HOME_SAFE_WORDS = ("没出门", "没出去", "不想出去", "不出门", "没下楼")
#: 「我们」= 声称有同席者；公司/这边的固定说法不算。
_WE_CLAIM = re.compile(r"我们(?![公司这那])")


def _sentences_of(text):
    return [part for part in re.split(r"(?<=[。！？!?；;])", str(text or ""))
            if part.strip()]


def _presence_claim_hit(text):
    """正文里「他就在我身边」的那一句（愿望/回忆句跳过）；没有返回空串。"""
    for sentence in _sentences_of(text):
        if not _PRESENCE_CLAIM.search(sentence):
            continue
        if any(mark in sentence for mark in _WISH_MARKERS):
            continue
        return sentence.strip()[:24]
    return ""


def _int_or_none(value):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _db_kv(plugin, key, default=None):
    getter = getattr(getattr(plugin, "db", None), "kv_get", None)
    if not callable(getter):
        return default
    try:
        return getter(key, default)
    except Exception as exc:
        _note_swallowed("amo_diary_audit.db_kv", exc)
        return default


def _cfg_text(plugin, key):
    getter = getattr(plugin, "cfg_str", None)
    if not callable(getter):
        return ""
    try:
        return str(getter(key, "") or "").strip()
    except Exception as exc:
        _note_swallowed("amo_diary_audit.cfg_text", exc)
        return ""


def _canonical_city(plugin):
    """日记只允许一个城市口径：日程城市优先，其次天气城市显示名。"""
    return _cfg_text(plugin, "schedule_city") or _cfg_text(plugin, "weather_city_name")


def _health_facts(plugin, day, rows):
    """按天健康事实：先读 health_day 快照，缺了就从日程的身体块推。"""
    snapshot = _db_kv(plugin, "health_day:" + str(day), None)
    missing = not isinstance(snapshot, dict) or not snapshot
    if isinstance(snapshot, dict) and snapshot:
        status = str(snapshot.get("status") or "")
        sick = bool(snapshot.get("sick")) or status not in ("", "健康")
        return {"status": status or ("生病" if sick else "健康"), "sick": sick}, missing
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("activity") or "") + str(item.get("detail") or "")
        if str(item.get("category") or "") == "health" or any(
                word in text for word in _HEALTH_BLOCK_WORDS):
            return {"status": "身体不适", "sick": True}, missing
    return {"status": "健康", "sick": False}, missing


def _weather_facts(plugin, day):
    """按天天气事实：只认 weather_day 快照。"""
    snapshot = _db_kv(plugin, "weather_day:" + str(day), None)
    if isinstance(snapshot, dict) and snapshot:
        return dict(snapshot), False
    return {}, True


def _spend_rows(plugin):
    wallet = getattr(plugin, "wallet", None)
    getter = getattr(wallet, "ledger", None) if wallet is not None else None
    if not callable(getter):
        return []
    try:
        return [row for row in (getter(200) or []) if isinstance(row, dict)]
    except Exception as exc:
        _note_swallowed("amo_diary_audit.spend_rows", exc)
        return []


def _amounts_in_window(spend, day, start, end):
    """块窗口内的支出金额（元，正数）。"""
    try:
        base = _datetime.strptime(str(day)[:10], "%Y-%m-%d").timestamp()
    except (TypeError, ValueError):
        return []
    begin, finish = _minutes_of(start), _minutes_of(end)
    if begin is None:
        return []
    if finish is None or finish <= begin:
        finish = begin + 60
    out = []
    for row in spend:
        try:
            ts = float(row.get("ts") or 0)
            delta = int(row.get("delta_cents") or 0)
        except (TypeError, ValueError):
            continue
        minute = (_datetime.fromtimestamp(ts) - _datetime.fromtimestamp(base)).total_seconds() / 60
        if delta >= 0 or minute < begin or minute >= finish:
            continue
        out.append(abs(delta) / 100.0)
    return out


def _day_fact_context(plugin, day, rows):
    """审计用的一天事实上下文（健康/天气快照 + 城市口径 + 账本）。"""
    health, health_missing = _health_facts(plugin, day, rows)
    weather, weather_missing = _weather_facts(plugin, day)
    return {"health": health, "health_missing": health_missing,
            "weather": weather, "weather_missing": weather_missing,
            "canonical_city": _canonical_city(plugin),
            "spend": _spend_rows(plugin)}


def _place_mismatch(plugin, item, text):
    """「日程在家、正文在外面」的第一人称断言（location_rules 拦不住的语义级冲突）。"""
    body = str(text or "")
    if not body or any(word in body for word in _HOME_SAFE_WORDS):
        return ""
    place = str(item.get("place") or "")
    zone = str(item.get("zone") or "")
    home = zone == "home" or (not zone and any(
        word in place for word in ("家", "出租屋", "卧室", "客厅")))
    if not home:
        return ""
    match = _FIRST_PERSON_OUT.search(body)
    if match:
        return match.group(0)
    for word in _HOME_CONFLICT_WORDS:
        if word in body:
            return word
    return ""


def _block_fact_entries(plugin, day, item, text, ctx):
    """单块的事实维度核对：地点/同伴/吃饭/金额/健康/天气。

    返回两桶 (hard, warn)。地点冲突是 HARD（标 stale 重写）；其余只进报告，
    避免靠启发式金额/措辞把正常日记反复标 stale。
    """
    hard, warn = [], []
    start = str(item.get("start") or "")
    end = str(item.get("end") or "")
    body = str(text or "")
    if not body:
        return hard, warn
    outside = _place_mismatch(plugin, item, body)
    if outside:
        hard.append({"start": start, "end": end, "kind": "place_mismatch"})
    claim = _presence_claim_hit(body)
    people = list(item.get("people") or item.get("companions") or [])
    together = str(item.get("category") or "") in ("date", "social") or bool(people)
    if not together:
        if claim:
            warn.append({"start": start, "end": end, "kind": "companion",
                         "detail": "本块没有面对面同伴事实：%s" % claim})
        elif _WE_CLAIM.search(body):
            # 块里没有同伴事实却用「我们」：旧版人名兜底把食物换成「一个朋友」
            # 会让日记凭空多一个人，这里只报告不改写。
            warn.append({"start": start, "end": end, "kind": "companion",
                         "detail": "本块没有同伴事实，正文却写了「我们」"})
    activity = str(item.get("activity") or "")
    meal_block = str(item.get("category") or "") == "meal" or any(
        word in activity for word in _MEAL_BLOCK_WORDS)
    if meal_block and _MEAL_NEGATION.search(body):
        warn.append({"start": start, "end": end, "kind": "meal",
                     "detail": "本块是吃饭行程，日记却说没吃"})
    amounts = [float(value) for value in _MONEY_RE.findall(body)]
    if amounts:
        spend = _amounts_in_window(ctx.get("spend") or [], day, start, end)
        if spend and not any(abs(amount - real) <= 1.0
                             for amount in amounts for real in spend):
            warn.append({"start": start, "end": end, "kind": "money",
                         "detail": "正文 %s 元 vs 账本 %s 元" % (
                             "/".join("%g" % value for value in amounts[:3]),
                             "/".join("%g" % value for value in spend[:3]))})
    health = ctx.get("health") or {}
    if health.get("sick") and _HEALTH_NEGATION.search(body):
        warn.append({"start": start, "end": end, "kind": "health",
                     "detail": "当天健康=%s，日记说好了/没病" % (
                         health.get("status") or "身体不适")})
    weather = ctx.get("weather") or {}
    temps = [_int_or_none(value) for value in _TEMP_RE.findall(body)]
    temps = [value for value in temps if value is not None]
    snapshot_temp = _int_or_none(weather.get("temp"))
    if temps and snapshot_temp is not None and all(
            abs(value - snapshot_temp) > 8 for value in temps):
        warn.append({"start": start, "end": end, "kind": "weather",
                     "detail": "正文 %s℃ vs 当天快照 %s℃" % (
                         temps[:2], snapshot_temp)})
    city = str(weather.get("city") or "")
    canonical = str(ctx.get("canonical_city") or "")
    if city and canonical and city != canonical and canonical in body:
        warn.append({"start": start, "end": end, "kind": "weather",
                     "detail": "正文写 %s，天气快照城市是 %s" % (canonical, city)})
    return hard, warn


def _day_fact_warns(day, ctx):
    """跨块/按天的事实覆盖：快照缺失、城市口径冲突（只进报告）。"""
    warn = []
    if ctx.get("weather_missing"):
        warn.append({"start": "", "end": "", "kind": "weather_missing",
                     "detail": "没有 weather_day:%s 快照，历史天气无法对账" % day})
    if ctx.get("health_missing"):
        warn.append({"start": "", "end": "", "kind": "health_missing",
                     "detail": "没有 health_day:%s 快照，历史健康无法对账" % day})
    city = str((ctx.get("weather") or {}).get("city") or "")
    canonical = str(ctx.get("canonical_city") or "")
    if city and canonical and city != canonical:
        warn.append({"start": "", "end": "", "kind": "weather_city",
                     "detail": "天气快照城市 %s ≠ 日程城市 %s" % (city, canonical)})
    return warn


def _diary_rows(db, day):
    """当天全部块日记行（含孤儿/旧 id 行）。"""
    lister = getattr(db, "list_block_diaries", None)
    if not callable(lister):
        return []
    try:
        rows = lister(day) or []
    except Exception as exc:
        _note_swallowed("amo_diary_audit.diary_rows", exc)
        return []
    return [row for row in rows if isinstance(row, dict)]


def _orphan_entries(day, rows, diary_rows, attributed):
    """当前日程里已经没有身份的 valid 日记行：HARD orphan（标 stale 等重写/清理）。"""
    current = set()
    for item in rows or []:
        if isinstance(item, dict):
            try:
                current.add(str(block_id(day, item)))
            except Exception:
                continue
    hard, stale_ids = [], []
    for row in diary_rows or []:
        bid = str(row.get("block_id") or "")
        status = str(row.get("status") or "valid")
        if not bid or status in _FROZEN_STATUS or status == "stale":
            continue
        if bid in current or bid in attributed:
            continue
        if not str(row.get("text") or "").strip():
            continue
        hard.append({"start": str(row.get("start") or ""),
                     "end": str(row.get("end") or ""), "kind": "orphan"})
        stale_ids.append(bid)
    return hard, stale_ids


def _hard_reason(plugin, item, bid, record, window_rows, rows):
    """返回 (HARD 类型, 需要额外标 stale 的旧 block_id)；没问题返回 ("", "")。"""
    if str(item.get("category") or "") == "sleep":
        return "", ""
    if not record:
        for row in window_rows or []:
            other = str(row.get("block_id") or "")
            if other and other != bid and str(row.get("text") or "").strip():
                return "block_mismatch", other
        return "missing", ""
    status = str(record.get("status") or "valid")
    if status in _FROZEN_STATUS:
        return "", ""
    if str(record.get("block_id") or "") not in ("", bid):
        return "block_mismatch", ""
    body = str(record.get("text") or "").strip()
    if not body:
        return ("empty", "") if status == "valid" else ("missing", "")
    if status != "valid":
        return "stale", ""
    start = str(item.get("start") or "")
    end = str(item.get("end") or "")
    if str(record.get("start") or "") and str(record.get("start")) != start:
        return "block_mismatch", ""
    if str(record.get("end") or "") and str(record.get("end")) != end:
        return "block_mismatch", ""
    if _place_hits(plugin, item, rows, body):
        return "place_forbidden", ""
    return "", ""


# ---------------------------------------------------------------- 落库 / 调度


def _cached_report(db, day):
    try:
        if not db.kv_get(KEY_DONE + day, False):
            return {}
        report = db.kv_get(KEY_REPORT + day, {})
    except Exception as exc:
        _note_swallowed("amo_diary_audit.cached", exc)
        return {}
    if not isinstance(report, dict):
        return {}
    cached = dict(report)
    cached["cached"] = True
    return cached


def _save_report(db, day, report):
    try:
        db.kv_set(KEY_REPORT + day, report)
        db.kv_set(KEY_DONE + day, now_ts())
    except Exception as exc:
        _note_swallowed("amo_diary_audit.save_report", exc)


def _mark_stale(plugin, day, bid):
    """HARD 块标 stale：不删正文，下一次读/调度会按块身份重写。"""
    marker = getattr(getattr(plugin, "db", None), "mark_block_diary_stale", None)
    if not callable(marker) or not bid:
        return 0
    try:
        return int(marker(day, bid) or 0)
    except Exception as exc:
        _note_swallowed("amo_diary_audit.mark_stale", exc)
        return 0


def _requeue(plugin, day, rows, now):
    """把刚标 stale 的块交给现有小日记调度（每 tick 最多 2 条，受预算限制）。"""
    spawn = getattr(plugin, "_spawn_diary_task", None)
    if not callable(spawn):
        return 0
    try:
        try:
            from .amo_schedule import _backfill_blocks
            from .amo_state_common import slot_has_ended as _ended
        except ImportError:
            from amo_schedule import _backfill_blocks
            from amo_state_common import slot_has_ended as _ended
    except Exception as exc:
        _note_swallowed("amo_diary_audit.requeue.import", exc)
        return 0
    db = plugin.db
    try:
        minute_key = now.strftime("%Y%m%d%H%M")
        budget = db.kv_get("diary_backfill_minute", {}) or {}
        used = (int(budget.get("count") or 0)
                if isinstance(budget, dict) and budget.get("minute") == minute_key else 0)
        cap = max(0, 2 - used)
        if cap <= 0:
            return 0
        tasks = getattr(plugin, "_diary_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
        generated = int(_backfill_blocks(
            plugin, day, rows, tasks, db.schedule_version(day), cap, _ended,
            reason="diary_audit") or 0)
        if generated:
            db.kv_set("diary_backfill_minute",
                      {"minute": minute_key, "count": used + generated})
        return generated
    except Exception as exc:
        _note_swallowed("amo_diary_audit.requeue", exc)
        return 0


def _add_id(ids, seen, value):
    text = str(value or "").strip()
    if text and text not in seen:
        seen.add(text)
        ids.append(text)


# ---------------------------------------------------------------- 审计入口


def _schedule_version(plugin, day):
    """当天日程版本；读不到返回 None（不做版本判定）。"""
    getter = getattr(getattr(plugin, "db", None), "schedule_version", None)
    if not callable(getter):
        return None
    try:
        return int(getter(day))
    except Exception as exc:
        _note_swallowed("amo_diary_audit.schedule_version", exc)
        return None


def _record_newer(record, version):
    """日记的 version 比当前日程还新（回滚/时间倒流）时返回 True。"""
    if not record or version is None:
        return False
    try:
        return int(record.get("version") or 0) > int(version)
    except (TypeError, ValueError):
        return False


def run(plugin, day, apply=True, now=None):
    """审一天：返回 report。apply=True 才标 stale / 写 kv / 入队重写。"""
    day = str(day or "").strip()
    if not day:
        return {}
    db = plugin.db
    now = now or _now()
    cached = _cached_report(db, day)
    if cached:
        return cached
    rows = _sorted_rows(_day_rows(plugin, day))
    window = _window_index(db, day)
    diary_rows = _diary_rows(db, day)
    context = _day_fact_context(plugin, day, rows)
    version = _schedule_version(plugin, day)
    ended = [item for item in rows
             if slot_has_ended(day, str(item.get("start") or ""), item.get("end"), now)]
    hard: list[dict[str, Any]] = []
    warn: list[dict[str, Any]] = []
    stale_ids: list[str] = []
    attributed: set[str] = set()
    seen: set[str] = set()
    prev = None
    seen_blocks = set()
    checked = 0
    for item in ended:
        bid = block_id(day, item)
        if bid in seen_blocks:
            # 日程里叠了同身份的重复块（同日同起止同活动同地点）只算一次，
            # 否则同一条日记会被重复报 HARD、也会重复标 stale。
            continue
        seen_blocks.add(bid)
        checked += 1
        try:
            record = db.get_block_diary_record(day, bid)
        except Exception as exc:
            _note_swallowed("amo_diary_audit.run.record", exc)
            record = None
        if record is not None and str(record.get("status") or "valid") in _FROZEN_STATUS:
            prev = {"text": str(record.get("text") or ""), "sleep": True}
            continue
        if _record_newer(record, version):
            # 日记写于更晚的日程版本（回滚/时间倒流）：不能当有效缓存。
            hard.append({"start": str(item.get("start") or ""),
                         "end": str(item.get("end") or ""), "kind": "version_mismatch"})
            _add_id(stale_ids, seen, bid)
            prev = None
            continue
        kind, extra = _hard_reason(plugin, item, bid, record, _same_window(window, item), rows)
        if kind:
            hard.append({"start": str(item.get("start") or ""),
                         "end": str(item.get("end") or ""), "kind": kind})
            _add_id(stale_ids, seen, bid)
            _add_id(stale_ids, seen, extra)
            if extra:
                # 这条旧身份行已经在 block_mismatch 里报过，不再重复算孤儿。
                attributed.add(str(extra))
            prev = None
            continue
        text = str((record or {}).get("text") or "")
        sleep = str(item.get("category") or "") == "sleep"
        if not sleep:
            _collect_warns(warn, item, text, prev)
            fact_hard, fact_warn = _block_fact_entries(plugin, day, item, text, context)
            for entry in fact_hard:
                hard.append({"start": entry.get("start") or "",
                             "end": entry.get("end") or "",
                             "kind": str(entry.get("kind") or "")})
                _add_id(stale_ids, seen, bid)
            warn.extend(fact_warn)
        prev = {"text": text, "sleep": sleep}
    # 孤儿行：日程里已经没有这个块身份的 valid 日记（11-18 的 25 条），
    # 自审以前只遍历当前块，所以它们永远不会被清掉。
    orphan_hard, orphan_stale = _orphan_entries(day, rows, diary_rows, attributed)
    hard.extend(orphan_hard)
    for bid in orphan_stale:
        _add_id(stale_ids, seen, bid)
    warn.extend(_day_fact_warns(day, context))
    applied = 0
    requeued = 0
    if apply:
        for bid in stale_ids:
            applied += _mark_stale(plugin, day, bid)
        requeued = _requeue(plugin, day, rows, now)
    report = {
        "day": day,
        "counts": {"blocks": checked, "hard": len(hard), "warn": len(warn),
                   "orphans": len(orphan_hard), "facts": len(context.get("spend") or [])},
        "hard": hard,
        "warn": warn,
        "at": int(now_ts()),
        "applied": bool(apply),
        "stale": int(applied),
        "requeued": int(requeued),
    }
    if apply:
        _save_report(db, day, report)
    return report


def next_day(plugin, now=None):
    """最近 lookback 天里最旧的一个「还没审」的日期；都审过返回空串。"""
    now = now or _now()
    lookback = max(1, min(31, _cfg_int(plugin, "diary_audit_lookback_days",
                                       DEFAULT_LOOKBACK_DAYS)))
    for offset in range(lookback, 0, -1):
        day = (now.date() - timedelta(days=offset)).isoformat()
        try:
            if plugin.db.kv_get(KEY_DONE + day, False):
                continue
        except Exception as exc:
            _note_swallowed("amo_diary_audit.next_day", exc)
            continue
        return day
    return ""


def latest_report_day(plugin, now=None):
    """面板/命令不带日期时：取最近一份报告（昨天往前找）。"""
    now = now or _now()
    lookback = max(1, min(31, _cfg_int(plugin, "diary_audit_lookback_days",
                                       DEFAULT_LOOKBACK_DAYS)))
    for offset in range(1, lookback + 2):
        day = (now.date() - timedelta(days=offset)).isoformat()
        try:
            if plugin.db.kv_get(KEY_REPORT + day, None):
                return day
        except Exception as exc:
            _note_swallowed("amo_diary_audit.latest_report_day", exc)
    return ""


# ---------------------------------------------------------------- 人话 / 通知


def _reason_text(kind):
    return _REASON_TEXT.get(str(kind or ""), str(kind or "未知"))


def report_text(plugin, day=None):
    """/amo audit 的摘要：人话、短、能看出哪几块对不上。"""
    db = plugin.db
    day = str(day or "").strip() or latest_report_day(plugin)
    if not day:
        return "📋 还没有日记质检记录（每天会自动审前一天）。"
    try:
        report = db.kv_get(KEY_REPORT + day, None)
    except Exception as exc:
        _note_swallowed("amo_diary_audit.report_text.kv", exc)
        report = None
    if not isinstance(report, dict) or not report:
        return "📋 %s 还没做过日记质检。" % day
    counts = report.get("counts") or {}
    lines = [
        "📋 日记质检 %s" % day,
        "已审 %s 块：对不上 %s 条，小问题 %s 条" % (
            counts.get("blocks", 0), counts.get("hard", 0), counts.get("warn", 0)),
    ]
    for entry in (report.get("hard") or [])[:8]:
        entry = entry if isinstance(entry, dict) else {}
        lines.append("❌ %s-%s %s" % (entry.get("start"), entry.get("end"),
                                      _reason_text(entry.get("kind"))))
    for entry in (report.get("warn") or [])[:5]:
        entry = entry if isinstance(entry, dict) else {}
        lines.append("⚠️ %s-%s %s" % (entry.get("start"), entry.get("end"),
                                      _reason_text(entry.get("kind"))))
    if int(counts.get("hard") or 0) > 0:
        lines.append("已标记重写 %s 块，调度会在后台补上。" % report.get("stale", 0))
    lines.append("完整报告在面板「日记」页。")
    return "\n".join(lines)


def notify_text(report, day):
    """系统口吻的私聊提示；没有 HARD 返回空串（WARN 只进面板）。"""
    counts = report.get("counts") or {}
    hard = int(counts.get("hard") or 0)
    if hard <= 0:
        return ""
    tally: dict[str, Any] = {}
    for entry in report.get("hard") or []:
        entry = entry if isinstance(entry, dict) else {}
        kind = str(entry.get("kind") or "")
        tally[kind] = tally.get(kind, 0) + 1
    pieces = ["%s %d 条" % (_reason_text(kind), count)
              for kind, count in sorted(tally.items(), key=lambda pair: -pair[1])]
    text = "【日记质检】昨天有 %d 条日记对不上：%s；已标记重写，详情在面板。" % (
        hard, "、".join(pieces))
    warn = int(counts.get("warn") or 0)
    if warn:
        text += "另有 %d 条小问题。" % warn
    return text


def parse_quiet(text):
    """「22-8」→ (22, 8)；解析不了按默认 22-8。"""
    raw = str(text or "").strip().replace("：", ":").replace("~", "-").replace("—", "-")
    parts = [part.strip() for part in raw.split("-") if part.strip()]
    if len(parts) != 2:
        return (22, 8)
    hours = []
    for part in parts:
        try:
            hours.append(int(part.split(":")[0]))
        except (TypeError, ValueError):
            return (22, 8)
    return (max(0, min(24, hours[0])), max(0, min(24, hours[1])))


def in_quiet_hours(hour, window):
    start, end = window
    if not 0 <= hour <= 23 or start == end:
        return False
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end


async def notify(plugin, now=None):
    """到点给主人发一条（每天最多一条、全干净不发、quiet 不发）。"""
    now = now or _now()
    if not _cfg_bool(plugin, "diary_audit_notify", True):
        return False
    hour = max(0, min(23, _cfg_int(plugin, "diary_audit_notify_hour", 9)))
    if now.hour < hour:
        return False
    quiet = parse_quiet(_cfg_str(plugin, "diary_audit_quiet_hours", DEFAULT_QUIET_HOURS))
    if in_quiet_hours(now.hour, quiet):
        return False
    day = (now.date() - timedelta(days=1)).isoformat()
    if not _cfg_bool(plugin, "diary_audit_push", False):
        return False
    db = plugin.db
    with _NOTIFY_LOCK:
        if db.kv_get(KEY_NOTIFIED + day, False):
            return False
        # 先占位再推送，避免并发 tick 各推一次；失败再释放
        db.kv_set(KEY_NOTIFIED + day, now_ts())
    try:
        report = db.kv_get(KEY_REPORT + day, None)
    except Exception as exc:
        _note_swallowed("amo_diary_audit.notify.kv", exc)
        return False
    if not isinstance(report, dict) or not report:
        return False
    text = notify_text(report, day)
    if not text:
        return False
    push = getattr(plugin, "push_to_owner", None)
    if not callable(push):
        return False
    try:
        sent = await push(text)
    except Exception as exc:
        _note_swallowed("amo_diary_audit.notify.push", exc)
        db.kv_set(KEY_NOTIFIED + day, False)
        return False
    if not sent:
        db.kv_set(KEY_NOTIFIED + day, False)
        return False
    return True


async def tick(plugin, now=None):
    """housekeeping 入口：一次只补审一天（最旧优先），顺带尝试发通知。"""
    if not _cfg_bool(plugin, "diary_audit_enabled", True):
        return {"day": "", "report": {}, "notified": False}
    now = now or _now()
    day = next_day(plugin, now)
    report = {}
    if day:
        try:
            report = await asyncio.to_thread(run, plugin, day, True, now)
        except Exception as exc:
            _note_swallowed("amo_diary_audit.tick.run", exc)
    notified = False
    try:
        notified = bool(await notify(plugin, now))
    except Exception as exc:
        _note_swallowed("amo_diary_audit.tick.notify", exc)
    return {"day": day, "report": report if isinstance(report, dict) else {},
            "notified": notified}
