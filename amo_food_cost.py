# -*- coding: utf-8 -*-
"""餐饮消费口径（2026-11-17）：夜间不扣款、10 分钟内同物不重复、金额走 price_book item。

背景（用户账单）：02:12 仍扣烧烤和泡面、17:30/17:31 两条面条、小蛋糕 36.67/41.95
这种随机分位。这里把「吃什么、多少钱、能不能扣」收口成纯函数：
- 夜间 00:00-06:00 只叙述不扣钱（饥饿可以写，钱包不动）；
- 同一 item 10 分钟内只允许一条（跨 meal/hunger/late/micro 路径）；
- 金额先查 price_book v1 的 meal item（min=max 的固定价），查不到用固定兜底表，
  绝不随机分位。
"""
from __future__ import annotations
from typing import Any, TYPE_CHECKING

import datetime as _dt
import hashlib
import logging
import re

try:  # Phase 11：统一时钟
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

try:
    from . import price_book as _price_book
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import price_book as _price_book

try:  # 地点归一唯一实现（doms 纯函数，未知 zone 不回落「家」）
    from .domains.schedule.place import normalize_place as _normalize_place
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from domains.schedule.place import normalize_place as _normalize_place

logger = logging.getLogger("amo.food")

NIGHT_START_HOUR = 0
NIGHT_END_HOUR = 6
DEDUPE_MINUTES = 10
#: 一天最多四顿：早/午/晚/宵夜。宵夜横跨 21:00-次日 05:00，00:00-05:00 归前一天的宵夜。
MAX_MEALS_PER_DAY = 4
#: 同一餐次（同一个 meal 实例）45 分钟内只允许一笔扣款；超过 45 分钟属于加餐，仍挂在同一实例上。
SLOT_DEDUPE_MINUTES = 45
#: 餐次窗口（当天分钟；宵夜窗口跨零点，>24:00 的部分表示次日凌晨）。
MEAL_SLOT_WINDOWS = {
    "breakfast": (5 * 60, 10 * 60),
    "lunch": (10 * 60, 15 * 60),
    "dinner": (16 * 60, 21 * 60),
    "late": (21 * 60, 29 * 60),
}
MEAL_SLOT_ORDER = ("breakfast", "lunch", "dinner", "late")
SLOT_LABELS = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "late": "夜宵"}
#: 餐块时长（分钟）。
MEAL_BLOCK_MINUTES = 15

#: 日常餐饮固定价（分）；price_book.json 有同 item 的 v1 行时以表为准。
FIXED_ITEMS = {
    "包子": 200, "豆浆和包子": 800, "豆浆": 300, "面条": 1500, "泡面": 500,
    "烧烤": 2800, "夜宵": 1500, "小蛋糕": 3000, "奶茶": 1500, "果茶": 1500,
    "外卖": 2500, "清淡外卖": 2000, "家常菜": 2000, "粥": 1000, "咖啡": 1500,
    "零食": 1000,
}

#: 说法 → 价目表 item 名（顺序敏感：长的在前）。
ALIASES = (
    ("豆浆和包子", "豆浆和包子"),
    ("清淡外卖", "清淡外卖"),
    ("牛肉面", "面条"),
    ("火腿肠", "零食"),
    ("小蛋糕", "小蛋糕"),
    ("家常菜", "家常菜"),
    ("烧烤和泡面", "烧烤"),
    ("烧烤", "烧烤"),
    ("泡面", "泡面"),
    ("面条", "面条"),
    ("包子", "包子"),
    ("豆浆", "豆浆"),
    ("夜宵", "夜宵"),
    ("奶茶", "奶茶"),
    ("果茶", "果茶"),
    ("外卖", "外卖"),
    ("白粥", "粥"),
    ("粥", "粥"),
    ("咖啡", "咖啡"),
    ("零食", "零食"),
)

#: 微型事件的 key → 价目表 item；late_snack 用事件自带的 food。
EVENT_ITEMS = {
    "breakfast": "豆浆和包子",
    "takeout": "外卖",
    "sick_takeout": "清淡外卖",
    "tea_time": "奶茶",
    "fruit_tea": "果茶",
}

_QUANTITY_RE = re.compile(r"\s*[×xX*]\s*\d+\s*$")
_FOOD_WORDS = ("吃", "饭", "面", "粥", "包子", "烧烤", "泡面", "蛋糕", "外卖",
               "夜宵", "奶茶", "果茶", "咖啡", "零食", "小吃")

#: 通勤说法 → 统一方式（顺序敏感：共享单车在单车前）。
TRAFFIC_MODES = (("共享单车", "共享单车"), ("单车", "共享单车"), ("骑车", "共享单车"),
                 ("自行车", "共享单车"), ("公交", "公交"), ("巴士", "公交"),
                 ("地铁", "地铁"), ("打车", "打车"), ("出租", "打车"), ("网约", "打车"),
                 ("开车", "开车"), ("自驾", "开车"))


def normalize_item(text):
    """去掉「× 1」「一份/一碗」这类修饰，得到可比较的 item 文本。"""
    body = str(text or "").strip()
    body = _QUANTITY_RE.sub("", body).strip()
    for prefix in ("刚刚", "正在", "刚", "在", "去", "买了", "点了", "吃了", "吃的",
                   "一份", "一碗", "一杯", "一个", "一份"):
        if body.startswith(prefix) and len(body) > len(prefix):
            body = body[len(prefix):].strip()
    return body


def item_key(text):
    """任意说法 → 价目表 item 名；认不出时原样返回（后备表查不到返回空价）。"""
    body = normalize_item(text)
    if not body:
        return ""
    for alias, key in ALIASES:
        if alias in body:
            return key
    return body


def event_item(event):
    """微型/餐饮事件 → 价目表 item。"""
    event = event or {}
    key = str(event.get("key") or "")
    food = str(event.get("food") or "").strip()
    if key == "late_snack":
        return item_key(food or "夜宵") or "夜宵"
    if food:
        return item_key(food)
    if key in EVENT_ITEMS:
        return item_key(EVENT_ITEMS[key])
    return item_key(event.get("text"))


def looks_like_food(event):
    """事件是不是餐饮类（给微事件和旧路径共用）。"""
    event = event or {}
    key = str(event.get("key") or "")
    if key in EVENT_ITEMS or key == "late_snack":
        return True
    if event.get("food"):
        return True
    body = str(event.get("text") or "")
    return any(word in body for word in _FOOD_WORDS)


#: 生病（发烧/感冒/不舒服）期间禁止外卖/宵夜/重口；在家煮粥煮面可以。
SICK_FORBIDDEN_WORDS = ("外卖", "点餐", "烧烤", "炸鸡", "小蛋糕", "奶茶", "果茶",
                        "泡面", "夜宵", "宵夜", "串串", "火锅")
SICK_ALLOWED_WORDS = ("粥", "面条", "家常菜", "清淡", "煮", "蒸", "温水", "自己做", "家里做")
SICK_FORBIDDEN_KEYS = ("takeout", "sick_takeout", "late_snack", "tea_time", "fruit_tea")


def late_snack_hours(now):
    """宵夜/夜间加餐窗口：22:00-次日 06:00（健康门只作用于这个窗口）。"""
    return int(now.hour) >= 22 or int(now.hour) < 6


def sick_status(plugin):
    """读 amo 权威健康状态：只认 state.health_info()['sick']，不在调用方各判各的。"""
    info = getattr(getattr(plugin, "state", None), "health_info", None)
    if not callable(info):
        return False
    try:
        return bool(dict(info() or {}).get("sick"))
    except Exception as exc:
        logger.debug("[amo] 读健康状态失败，按健康处理: %r", exc)
        return False


def sick_food_allowed(event):
    """生病期间这份餐饮能不能出现：外卖/外卖类事件一律不许，自家清淡煮食可以。"""
    event = event or {}
    key = str(event.get("key") or "")
    if key in SICK_FORBIDDEN_KEYS:
        return False
    if key in ("home_cook", "cook"):
        return True
    text = "%s %s" % (event.get("text") or "", event.get("food") or "")
    if any(word in text for word in SICK_FORBIDDEN_WORDS):
        return False
    if any(word in text for word in SICK_ALLOWED_WORDS):
        return True
    return True


def price_for(item, city="", moment=None):
    """item 的固定价（分）：price_book v1 的 item 行优先，其次固定兜底；绝不出随机分位。"""
    key = item_key(item)
    if not key:
        return 0
    try:
        low, high = _price_book.price(None, "meal", item=key, city=city, moment=moment)
        if int(low) == int(high):
            return int(low)
    except Exception as exc:  # 表缺失/没这个 item：用兜底价，不阻断吃饭
        logger.debug("[amo] 价目表查不到 %r，用固定兜底: %r", key, exc)
    return int(FIXED_ITEMS.get(key, 0))


def in_night(now):
    return NIGHT_START_HOUR <= int(now.hour) < NIGHT_END_HOUR


def _minute_of(text):
    try:
        hour, minute = str(text or "").strip()[:5].split(":", 1)
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def _text_of(minute):
    minute = max(0, min(24 * 60, int(minute)))
    return "24:00" if minute >= 24 * 60 else "%02d:%02d" % (minute // 60, minute % 60)


def _overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def _block_id_for(day, row):
    """日程块稳定 id：优先用 amo_timeline.block_id，导入失败时按同口径哈希。"""
    try:
        try:
            from .amo_timeline import block_id as _bid
        except ImportError:
            from amo_timeline import block_id as _bid
        return _bid(day, row)
    except Exception as exc:
        logger.debug("[amo] block_id 不可用，按哈希兜底: %r", exc)
        raw = "%s-%s|%s|%s" % (str(row.get("start") or ""), str(row.get("end") or ""),
                               str(row.get("activity") or ""), str(row.get("place") or ""))
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _new_event_id():
    """新块的新身份（让返回的 block_id 与落库 event_id 一致）；失败返回空串。"""
    try:
        try:
            from .amo_timeline import new_event_id as _new
        except ImportError:
            from amo_timeline import new_event_id as _new
        return str(_new() or "")
    except Exception as exc:
        logger.debug("[amo] 新事件 id 不可用，交给启发式兜底: %r", exc)
        return ""


def _split_block(day, parent, parts, inherit_index=0):
    """显式切块（H5）：父 id 只落 inherit_index 段；导入失败时退回启发式。"""
    try:
        try:
            from .amo_timeline import split_schedule_block as _split
        except ImportError:
            from amo_timeline import split_schedule_block as _split
        rows, _info = _split(day, parent, parts, inherit_index=inherit_index)
        return rows
    except Exception as exc:
        logger.debug("[amo] 显式切块不可用，退回启发式: %r", exc)
        return parts


def schedule_rows(state, day):
    """读当天日程：内存缓存优先，缺了再读库；坏行不参与切分。"""
    rows = []
    try:
        rows = [dict(row) for row in (getattr(state, "schedule", {}) or {}).get(day) or []
                if isinstance(row, dict)]
    except Exception as exc:
        logger.debug("[amo] 读 schedule 缓存失败: %r", exc)
        rows = []
    if rows:
        return rows
    try:
        getter = getattr(getattr(state, "db", None), "get_schedule", None)
        if callable(getter):
            rows = [dict(row) for row in (getter(day) or []) if isinstance(row, dict)]
    except Exception as exc:
        logger.debug("[amo] 读 schedule 表失败: %r", exc)
        rows = []
    return rows


#: 路上/车站类 zone：宵夜落在通勤途中时不能把一段通勤劈成两截（schedule_report R13）。
_TRANSIT_ZONES = frozenset({"road", "street", "transit", "commute", "station"})
_TRANSIT_SOURCES = frozenset({"transit_block"})
_TRANSIT_CATEGORIES = frozenset({"commute", "transit"})
_SNACK_WORDS = ("宵夜", "夜宵")
#: 当前地点推不出来时的兜底（沿用旧口径：夜里一般在家）。
_DEFAULT_PLACE_ZONE = ("家", "home")


def _is_transit_row(row):
    """这行是不是「在路上/在车站」：这类块不能被宵夜切分。"""
    if not isinstance(row, dict):
        return False
    zone = str(row.get("zone") or "").strip().lower()
    if zone in _TRANSIT_ZONES:
        return True
    if str(row.get("source") or "").strip().lower() in _TRANSIT_SOURCES:
        return True
    return str(row.get("category") or "").strip().lower() in _TRANSIT_CATEGORIES


def _place_zone_of(row):
    """日程块的 (place, zone) 当「当前地点」；未知 zone 保留原文、不回落「家」。"""
    row = row or {}
    zone = str(row.get("zone") or "").strip()
    place = str(row.get("place") or "").strip()
    try:
        return _normalize_place(place, zone), zone
    except Exception as exc:  # 归一失败不能阻断落块
        logger.debug("[amo] 地点归一失败，沿用原值: %r", exc)
        return place, zone


def _covering_row(rows, begin, finish):
    """覆盖 [begin,finish) 的日程块；没有返回 None。"""
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        start, end = _minute_of(row.get("start")), _minute_of(row.get("end"))
        if start is None or end is None or end <= start:
            continue
        if start <= begin and end >= finish:
            return row
    return None


def _adjacent_place_zone(rows, begin, finish):
    """没有覆盖块时只借紧邻（无空档）的前/后块，避免远处地点串味。"""
    prev_row = next_row = None
    prev_end = next_start = None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        start, end = _minute_of(row.get("start")), _minute_of(row.get("end"))
        if start is None or end is None or end <= start:
            continue
        if end == begin and (prev_end is None or end > prev_end):
            prev_row, prev_end = row, end
        elif start == finish and (next_start is None or start < next_start):
            next_row, next_start = row, start
    for row in (prev_row, next_row):
        if row is not None:
            return _place_zone_of(row)
    return _DEFAULT_PLACE_ZONE


def _current_place_zone(rows, begin, finish):
    """宵夜块要跟随的当前 place/zone（R13）：覆盖块优先，其次紧邻块。"""
    covering = _covering_row(rows, begin, finish)
    if covering is not None:
        return _place_zone_of(covering), covering
    return _adjacent_place_zone(rows, begin, finish), None


def _save_block_rows(state, day, kept, rows, source, reason):
    """把切分/合并后的块写回日程；返回是否成功。"""
    try:
        commit = getattr(state, "commit_schedule_change", None)
        apply = getattr(state, "apply_schedule", None)
        saver = getattr(state, "save_day", None)
        source_text = str(source or "food_spend")
        reason_text = str(reason or "餐饮落日程")
        if callable(apply):
            saved = apply(day, lambda current: list(kept), source=source_text,
                          reason=reason_text)
            return bool(saved.get("saved", True)) if isinstance(saved, dict) else bool(saved)
        if callable(commit):
            saved = commit(day, kept, old_items=rows, source=source_text,
                           reason=reason_text)
            return bool(saved.get("saved", True)) if isinstance(saved, dict) else bool(saved)
        if callable(saver):
            return bool(saver(day, kept))
    except Exception as exc:
        logger.warning("[amo] 补餐饮块失败 day=%s: %r", day, exc)
        return False
    return False


# ---------------- 餐次实例：一天最多四顿的唯一身份 ----------------
def meal_instance_id(day, slot):
    """餐次实例 ID（幂等/event_id/日程块共用）：meal:<day>:<slot>。"""
    day = str(day or "").strip()
    slot = str(slot or "").strip()
    if not day or slot not in MEAL_SLOT_WINDOWS:
        return ""
    return "meal:%s:%s" % (day, slot)


def parse_meal_instance(value):
    """meal:<day>:<slot> → (day, slot)；不是餐次实例返回 ("", "")。"""
    parts = str(value or "").strip().split(":")
    if len(parts) == 3 and parts[0] == "meal" and parts[2] in MEAL_SLOT_WINDOWS:
        return parts[1], parts[2]
    return "", ""


def _instance_of_row(row):
    """账本行属于哪个餐次实例：优先 event_id，其次旧格式 idem=meal:<day>:<slot>:<seq>。"""
    row = row or {}
    event = str(row.get("event_id") or "")
    if event.startswith("meal:"):
        return event
    idem = str(row.get("idem") or "")
    if idem.startswith("meal:"):
        parts = idem.split(":")
        if len(parts) >= 3:
            return ":".join(parts[:3])
    return ""


def meal_slot_at(now=None):
    """此刻属于哪一顿：返回 (target_day, slot, instance_id)。

    宵夜窗口 21:00-次日 05:00；00:00-05:00 归**前一天**的宵夜，保证「一天最多四顿」
    不会被零点切成第五顿。15:00-16:00 是加餐空档，返回 ("", "", "")：不占四顿配额。
    """
    now = now or _amo_clock.now()
    minute = int(now.hour) * 60 + int(now.minute)
    day = now.date()
    if minute < 5 * 60:
        day = day - _dt.timedelta(days=1)
        minute += 24 * 60
    for slot in MEAL_SLOT_ORDER:
        start, end = MEAL_SLOT_WINDOWS[slot]
        if start <= minute < end:
            day_text = day.isoformat()
            return day_text, slot, meal_instance_id(day_text, slot)
    return "", "", ""


def _wallet_ledger_rows(plugin, limit=200):
    """只读钱包流水（坏的/非 dict 行直接跳过，不阻断吃饭）。"""
    wallet = getattr(plugin, "wallet", None)
    if wallet is None:
        return []
    try:
        return [row for row in (wallet.ledger(limit) or []) if isinstance(row, dict)]
    except Exception as exc:
        logger.debug("[amo] 读钱包流水失败，按空处理: %r", exc)
        return []


def meal_instance_rows(plugin, instance_id):
    """某个餐次实例已经扣过的流水（只读，按时间升序）。"""
    instance = str(instance_id or "").strip()
    if not instance:
        return []
    day, slot = parse_meal_instance(instance)
    if not day:
        return []
    prefix = instance + ":"
    out = []
    for row in _wallet_ledger_rows(plugin):
        if int(row.get("delta_cents") or 0) >= 0:
            continue
        idem = str(row.get("idem") or "")
        if str(row.get("event_id") or "") == instance or idem == instance or idem.startswith(prefix):
            out.append(row)
    out.sort(key=lambda row: float(row.get("ts") or 0))
    return out


def meal_charged(plugin, instance_id):
    """这个餐次实例是不是已经扣过款。"""
    return bool(meal_instance_rows(plugin, instance_id))


def meal_instances_today(plugin, day):
    """今天已经吃了哪几顿：{slot: [rows]}（只读账本，按实例去重）。"""
    day_text = str(day or "")
    out: dict[str, list[dict[str, Any]]] = {}
    for row in _wallet_ledger_rows(plugin):
        if int(row.get("delta_cents") or 0) >= 0:
            continue
        instance = _instance_of_row(row)
        inst_day, slot = parse_meal_instance(instance)
        if inst_day != day_text or slot not in MEAL_SLOT_WINDOWS:
            continue
        out.setdefault(slot, []).append(row)
    return out


def _slot_of_block_minute(minute):
    """日程块开始分钟 → 餐次 slot（0-5 点按宵夜算；15-16 空档返回空串）。"""
    value = int(minute)
    if value < 5 * 60:
        value += 24 * 60
    for slot in MEAL_SLOT_ORDER:
        start, end = MEAL_SLOT_WINDOWS[slot]
        if start <= value < end:
            return slot
    return ""


def coalesce_meal_blocks(plugin, day, slot=""):
    """同一餐次只保留一个餐块：重叠/首尾相接的 meal 块合并成一个。

    扣费入口已经保证「一个实例一个块」；这里是给旧数据/并发写留下的重复餐块做收口，
    只合并彼此重叠或首尾相接的块，中间夹着别的块（睡眠/通勤）时绝不硬吞。
    """
    state = getattr(plugin, "state", None)
    if state is None:
        return {"ok": False, "reason": "no_schedule", "merged": 0}
    day = str(day or "")
    rows = schedule_rows(state, day)
    if not rows:
        return {"ok": True, "merged": 0}
    groups: dict[str, list[dict[str, Any]]] = {}
    others: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("category") or "") != "meal":
            others.append(row)
            continue
        begin = _minute_of(row.get("start"))
        row_slot = _slot_of_block_minute(begin) if begin is not None else ""
        if not row_slot or (slot and row_slot != slot):
            others.append(row)
            continue
        groups.setdefault(row_slot, []).append(row)
    merged = 0
    for group in groups.values():
        if len(group) < 2:
            others.extend(group)
            continue
        group.sort(key=lambda row: _minute_of(row.get("start")) or 0)
        current = dict(group[0])
        for nxt in group[1:]:
            cur_end = _minute_of(current.get("end"))
            nxt_start = _minute_of(nxt.get("start"))
            nxt_end = _minute_of(nxt.get("end"))
            if cur_end is None or nxt_start is None or nxt_end is None or nxt_start > cur_end:
                others.append(current)
                current = dict(nxt)
                continue
            current["end"] = _text_of(max(cur_end, nxt_end))
            note = str(nxt.get("detail") or "").strip()
            if note and note not in str(current.get("detail") or ""):
                old = str(current.get("detail") or "").strip()
                current["detail"] = ("%s；%s" % (old, note)) if old else note
            if not str(current.get("event_id") or "").startswith("meal:"):
                candidate = str(nxt.get("event_id") or "")
                if candidate.startswith("meal:"):
                    current["event_id"] = candidate
            merged += 1
        others.append(current)
    if not merged:
        return {"ok": True, "merged": 0}
    others.sort(key=lambda row: _minute_of(row.get("start"))
                if _minute_of(row.get("start")) is not None else 24 * 60)
    if not _save_block_rows(state, day, others, rows, "meal_coalesce", "同一餐次只保留一个块"):
        return {"ok": False, "reason": "save_failed", "merged": 0}
    try:
        state.schedule[day] = others
    except Exception as exc:
        logger.debug("[amo] 回写 schedule 快照失败: %r", exc)
    logger.info("[amo] 同一餐次重复餐块已合并 day=%s merged=%d", day, merged)
    return {"ok": True, "merged": merged}


def ensure_block_window(plugin, day, start, end, activity, detail="", category="meal",
                        source="food_spend", reason="餐饮落日程", instance_id=""):
    """在 day 的日程里为核心窗口 [start,end) 落一个块；覆盖块切成「前段/中段/后段」。

    宵夜块的 place/zone 跟随当前地点（schedule_report R13）：
    - 出差住酒店/景区/商场/餐厅：沿用覆盖块的原地点，不再写死「家」；
    - 正在路上/车站：不切分通勤块（避免劈成两截），把宵夜并进当前通勤块。
    餐饮消费（微型事件、宵夜领域入口）必须先在日程里有块才允许扣款，这里是唯一实现。
    返回 {ok, block_id, day, start, end, activity, reused}；补不上返回 {ok: False, reason}。
    """
    state = getattr(plugin, "state", None)
    if state is None:
        return {"ok": False, "reason": "no_schedule"}
    day = str(day or "")
    begin = _minute_of(str(start or "")[:5])
    finish = _minute_of(str(end or "")[:5])
    if not day or begin is None or finish is None or finish <= begin:
        return {"ok": False, "reason": "bad_window"}
    start_text, end_text = _text_of(begin), _text_of(finish)
    rows = schedule_rows(state, day)
    body = str(activity or "宵夜")
    instance = str(instance_id or "").strip()
    if instance:
        # 同一餐次实例永远复用同一个块：先按 event_id 找，跨时段也不再切新块。
        for row in rows:
            if str(row.get("event_id") or "") != instance:
                continue
            note = str(detail or "").strip()
            old_detail = str(row.get("detail") or "").strip()
            if note and note not in old_detail:
                row["detail"] = ("%s；%s" % (old_detail, note)) if old_detail else note
                kept = list(rows)
                if not _save_block_rows(state, day, kept, rows, source, reason):
                    return {"ok": False, "reason": "save_failed"}
                try:
                    state.schedule[day] = kept
                except Exception as exc:
                    logger.debug("[amo] 回写 schedule 快照失败: %r", exc)
            return {"ok": True, "block_id": _block_id_for(day, row), "day": day,
                    "start": str(row.get("start") or ""), "end": str(row.get("end") or ""),
                    "activity": str(row.get("activity") or ""), "reused": True,
                    "instance_id": instance}
    for row in rows:
        a, b = _minute_of(row.get("start")), _minute_of(row.get("end"))
        if a is None or b is None or not _overlaps(a, b, begin, finish):
            continue
        text = str(row.get("activity") or "")
        # 饭点里已经有了 meal 块（不管文案怎么写）就复用，不能一次消费切一个新块。
        if str(row.get("category") or "") == "meal":
            return {"ok": True, "block_id": _block_id_for(day, row), "day": day,
                    "start": str(row.get("start") or ""), "end": str(row.get("end") or ""),
                    "activity": text, "reused": True,
                    "instance_id": str(row.get("event_id") or "")}
    (place, zone), covering = _current_place_zone(rows, begin, finish)
    # R13：在途吃宵夜——不切通勤块，把宵夜并进当前块，地点跟原地（路上/车站）。
    if covering is not None and _is_transit_row(covering):
        merged = dict(covering)
        text = str(merged.get("activity") or "")
        if not any(word in text for word in _SNACK_WORDS):
            merged["activity"] = "%s，吃宵夜" % (text or "在路上")
        note = str(detail or "").strip()
        if note:
            old_detail = str(merged.get("detail") or "").strip()
            merged["detail"] = ("%s；%s" % (old_detail, note)) if old_detail else note
        kept = [merged if row is covering else row for row in rows]
        if not _save_block_rows(state, day, kept, rows, source, reason):
            return {"ok": False, "reason": "save_failed"}
        try:
            state.schedule[day] = kept
        except Exception as exc:
            logger.debug("[amo] 回写 schedule 快照失败: %r", exc)
        return {"ok": True, "block_id": _block_id_for(day, merged), "day": day,
                "start": str(merged.get("start") or ""), "end": str(merged.get("end") or ""),
                "activity": str(merged.get("activity") or ""), "reused": True,
                "merged": True}
    new_row = {"start": start_text, "end": end_text, "activity": body,
               "place": place, "zone": zone, "category": str(category or "meal"),
               "source": str(source or "food_spend"), "appointment": True,
               "detail": str(detail or "")}
    if instance:
        new_row["event_id"] = instance
    kept, inserted = [], False
    for row in rows:
        a, b = _minute_of(row.get("start")), _minute_of(row.get("end"))
        if a is None or b is None or not _overlaps(a, b, begin, finish):
            kept.append(row)
            continue
        if a <= begin and b >= finish:
            if inserted:
                logger.warning("[amo] 餐饮时段命中多个覆盖块，拒绝补块 day=%s %s-%s",
                               day, start_text, end_text)
                return {"ok": False, "reason": "overlap"}
            # H5：显式切块，父 id 由这里指定落在最早那段（before，没有则新块），
            # 不再交给 stamp_event_ids 按重叠比例猜。
            parts = []
            new_index = 0
            if a < begin:
                before = dict(row)
                before["end"] = start_text
                parts.append(before)
                new_index = 1
            parts.append(new_row)
            if b > finish:
                after = dict(row)
                after["start"] = end_text
                parts.append(after)
            pieces = _split_block(day, row, parts, 0)
            if new_index < len(pieces):
                piece_id = str(pieces[new_index].get("event_id") or "")
                if piece_id:
                    new_row["event_id"] = piece_id
            kept.extend(pieces)
            inserted = True
            continue
        logger.warning("[amo] 餐饮时段与已有块部分重叠，拒绝补块 day=%s %s-%s", day,
                       start_text, end_text)
        return {"ok": False, "reason": "overlap"}
    if not inserted:
        kept.append(new_row)
        # 全新块没有父 id 可继承：显式拿一个 event_id（餐次实例优先），返回的
        # block_id 才能写进账本 ref（H4 只解析 block:ev_<id>）。
        if instance:
            new_row["event_id"] = instance
        else:
            fresh = _new_event_id()
            if fresh:
                new_row["event_id"] = fresh
    kept.sort(key=lambda row: _minute_of(row.get("start"))
              if _minute_of(row.get("start")) is not None else 24 * 60)
    if not _save_block_rows(state, day, kept, rows, source, reason):
        return {"ok": False, "reason": "save_failed"}
    try:
        state.schedule[day] = kept
    except Exception as exc:
        logger.debug("[amo] 回写 schedule 快照失败: %r", exc)
    return {"ok": True, "block_id": _block_id_for(day, new_row), "day": day,
            "start": start_text, "end": end_text, "activity": body, "reused": False}


def ensure_food_block(plugin, item, now, reason="", minutes=15):
    """夜间餐饮扣款前，确保 schedule 里有对应显示块（02:12 就要有 02:12-02:27 宵夜）。

    返回 True 表示已有块或已补块；False 表示补不上（那就不许扣钱）。
    切块/落库统一走 ensure_block_window，微型事件与宵夜领域入口共用同一实现。
    """
    label = str(item or "宵夜")
    start = int(now.hour) * 60 + int(now.minute)
    end = min(24 * 60, start + max(1, int(minutes)))
    activity = "吃%s（宵夜）" % label if in_night(now) else "吃%s" % label
    _day, _slot, instance = meal_slot_at(now)
    result = ensure_block_window(plugin, now.strftime("%Y-%m-%d"), _text_of(start),
                                 _text_of(end), activity, str(reason or ""),
                                 instance_id=instance)
    return bool(result.get("ok"))


def night_quiet(now=None):
    now = now or _amo_clock.now()
    return in_night(now)


def recent_duplicate(wallet, item, now=None, minutes=DEDUPE_MINUTES):
    """同一 item 在 minutes 分钟内是否已经扣过款（只读账本，不改余额）。"""
    target = item_key(item)
    if not target:
        return False
    now = now or _amo_clock.now()
    rows = []
    since = getattr(wallet, "rows_between", None)
    ledger = getattr(wallet, "ledger", None)
    try:
        if callable(since):
            rows = since(float(now.timestamp()) - minutes * 60, float(now.timestamp()) + 1)
        elif callable(ledger):
            rows = [row for row in (ledger(50) or [])
                    if float(row.get("ts") or 0) >= float(now.timestamp()) - minutes * 60]
    except Exception as exc:
        logger.debug("[amo] 查近期餐饮流水失败，按未重复处理: %r", exc)
        return False
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if int(row.get("delta_cents") or 0) >= 0:
            continue
        body = str(row.get("item_name") or row.get("reason") or "")
        if item_key(body) == target:
            return True
    return False


def food_idem(source, item, now=None, minutes=DEDUPE_MINUTES):
    """餐饮幂等键：来源 + 日期 + item + 10 分钟桶，17:30/17:31 落同一个键。"""
    now = now or _amo_clock.now()
    bucket = int(now.minute) // max(1, int(minutes))
    return "food:%s:%s:%s:%02d%02d" % (source, now.strftime("%Y-%m-%d"),
                                       item_key(item) or "meal", int(now.hour), bucket)


#: 早/午/晚饭点窗口（分钟）。只用于判断“这一餐吃过了没”，不参与聊天措辞。
MEAL_WINDOWS = {"breakfast": (6 * 60, 10 * 60), "lunch": (10 * 60, 15 * 60),
                "dinner": (16 * 60, 22 * 60)}
#: 能算“一顿饭”的词；奶茶/果茶/咖啡属于饮料，不算饭。
MEAL_WORDS = ("面条", "牛肉面", "米饭", "米饭套餐", "快餐", "包子", "豆浆", "白粥",
              "粥", "外卖", "家常菜", "泡面", "饺子", "汉堡", "披萨", "烧烤", "肠粉", "米粉")
DRINK_WORDS = ("奶茶", "果茶", "咖啡", "果汁", "可乐", "饮料")


def meal_slot(now=None):
    """当前饭点：breakfast / lunch / dinner；不在饭点返回空串。"""
    now = now or _amo_clock.now()
    minute = int(now.hour) * 60 + int(now.minute)
    for slot, (start, end) in MEAL_WINDOWS.items():
        if start <= minute < end:
            return slot
    return ""


#: 判重用的宽窗口：与守护三餐的窄窗口（7:30-9 / 11-13 / 18-19:30）分工不同——
#: 宽窗口决定「这一顿有没有着落」，窄窗口决定「几点吃」。改动请同步 watch 侧
#: services/daily_engine.CLAIM_WINDOWS（tests/test_meal_claim_contract.py 会比对）。
CLAIM_WINDOWS = MEAL_WINDOWS

#: 算「一顿饭」的活动词。不收单字「吃」：16:30 的「吃点东西/吃冰淇淋」不能占掉晚饭。
#: 必须与 watch 侧 services/date_rules.CLAIM_WORDS 逐字一致（契约测试比对）。
CLAIM_WORDS = ("饭", "餐", "面条", "牛肉面", "米饭", "快餐", "包子", "豆浆", "白粥",
               "粥", "外卖", "家常菜", "泡面", "饺子", "汉堡", "披萨", "烧烤", "肠粉",
               "米粉", "火锅", "烤肉", "聚餐", "夜宵", "宵夜", "麻辣烫", "小龙虾", "寿司")


def _looks_like_meal(text, category=""):
    """日程里的这一块算不算一顿饭。

    category=meal 的块本身就是一顿（「在家随便弄点吃的」也算），
    social/date 只有命中餐饮词才算，避免逛街/电影/咖啡占掉饭点。
    """
    if str(category or "") == "meal":
        return True
    body = str(text or "")
    return any(word in body for word in CLAIM_WORDS)


def meal_claim(plugin, day, slot):
    """这个饭点在日程里是不是已经有着落（饭局/约会/餐饮块）。

    返回占住它的活动文字；空串表示空闲。2026-11-20：18:00 买面条时，19:09 的
    火锅合同还没到点扣款，账本干干净净——只看流水必然漏。
    """
    window = CLAIM_WINDOWS.get(slot)
    if not window:
        return ""
    low, high = int(window[0]), int(window[1])
    rows = schedule_rows(getattr(plugin, "state", None), str(day or ""))
    for row in rows:
        if str(row.get("category") or "") not in ("meal", "social", "date"):
            continue
        begin, finish = _minute_of(row.get("start")), _minute_of(row.get("end"))
        if begin is None or finish is None or finish <= begin:
            continue
        if not _overlaps(begin, finish, low, high):
            continue
        text = str(row.get("activity") or "")
        if _looks_like_meal(text, row.get("category")):
            return text
    return ""


def meal_spent(plugin, now=None, ledger=None):
    """这个饭点是不是已经吃过了（任何来源的一顿都算）。

    只读共享钱包流水：
    - 守护三餐：idem = meal:<day>:<slot>:N（最可靠）；
    - 其它餐饮：reason 命中主食/外卖词；奶茶/果茶这类饮料不算一顿饭。
    2026-11-18 事故：11:01 守护买了「面条 × 1」，12:53 陪伴又抽到外卖；
    旧判定只看 cooked（自己做饭），买来的面条既不是做饭也不是外卖，直接漏掉。
    """
    now = now or _amo_clock.now()
    slot = meal_slot(now)
    if not slot:
        return False
    day = now.strftime("%Y-%m-%d")
    # 先看日程：已经排了饭局/约会/餐饮块，这一顿就算有着落（不看钱到没到点）
    if meal_claim(plugin, day, slot):
        return True
    wallet = getattr(plugin, "wallet", None)
    start, end = MEAL_WINDOWS[slot]
    if ledger is not None:
        rows = list(ledger)
    elif wallet is None:
        return False
    else:
        try:
            rows = list(wallet.ledger(200) or [])
        except Exception as exc:
            logger.debug("[amo] 查饭点流水失败，按没吃过处理: %r", exc)
            return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            ts = float(row.get("ts") or 0)
            raw_delta = row.get("delta_cents")
            delta = int(raw_delta if raw_delta is not None else (row.get("delta") or 0))
        except (TypeError, ValueError):
            continue
        if delta >= 0:
            continue
        try:
            moment = _dt.datetime.fromtimestamp(ts)
        except (OverflowError, OSError, ValueError):
            continue
        if moment.strftime("%Y-%m-%d") != day:
            continue
        minute = moment.hour * 60 + moment.minute
        if not (start <= minute < end):
            continue
        idem = str(row.get("idem") or "")
        if idem.startswith("meal:%s:%s" % (day, slot)):
            return True
        reason = str(row.get("reason") or "")
        if any(word in reason for word in DRINK_WORDS) and not any(
                word in reason for word in ("饭", "面", "套餐")):
            continue
        if any(word in reason for word in MEAL_WORDS):
            return True
    return False


def _today_spend_rows(plugin, now):
    """今天到此刻为止的支出流水（只读钱包，归一 delta_cents）。"""
    wallet = getattr(plugin, "wallet", None)
    if wallet is None:
        return []
    day = now.strftime("%Y-%m-%d")
    try:
        rows = list(wallet.ledger(200) or [])
    except Exception as exc:
        logger.debug("[amo] 查今日流水失败，按没有处理: %r", exc)
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            ts = float(row.get("ts") or 0)
            raw = row.get("delta_cents")
            delta = int(raw if raw is not None else (row.get("delta") or 0))
        except (TypeError, ValueError):
            continue
        if delta >= 0:
            continue
        try:
            moment = _dt.datetime.fromtimestamp(ts)
        except (OverflowError, OSError, ValueError):
            continue
        if moment.strftime("%Y-%m-%d") != day:
            continue
        out.append((moment, str(row.get("idem") or ""), str(row.get("reason") or "")))
    return out


def claimed(plugin, now=None, prefixes=(), words=(), low=0, high=24 * 60):
    """今天某个时间窗内是否已有指定来源/说法的支出（跨插件共用钱包流水）。"""
    now = now or _amo_clock.now()
    for moment, idem, reason in _today_spend_rows(plugin, now):
        minute = moment.hour * 60 + moment.minute
        if not (low <= minute < high):
            continue
        if any(idem.startswith(prefix) for prefix in prefixes):
            return True
        if any(word in reason for word in words):
            return True
    return False


def commute_mode(plugin, now=None):
    """今天的通勤方式：地铁/公交/共享单车/打车/开车/走路；拿不到返回空串。"""
    now = now or _amo_clock.now()
    day = now.strftime("%Y-%m-%d")
    rows = schedule_rows(getattr(plugin, "state", None), day)
    low, high = 6 * 60, 10 * 60 + 30
    mapping = {"subway": "地铁", "bus": "公交", "taxi": "打车", "drive": "开车",
               "bicycle": "共享单车", "bike": "共享单车", "walk": "走路"}
    for row in rows:
        if not isinstance(row, dict):
            continue
        start, end = _minute_of(row.get("start")), _minute_of(row.get("end"))
        if start is None or end is None or not (start < high and low < end):
            continue
        code = str(row.get("transit") or "").strip().lower()
        if code in mapping:
            return mapping[code]
        text = "%s %s %s" % (row.get("activity") or "", row.get("vehicle") or "",
                             row.get("detail") or "")
        for word, mode in TRAFFIC_MODES:
            if word in text:
                return mode
    return ""


def traffic_spent(plugin, now=None):
    """今天早高峰是否已经有一笔通勤（守护 traffic: 或陪伴 micro:subway）。"""
    now = now or _amo_clock.now()
    day = now.strftime("%Y-%m-%d")
    return claimed(plugin, now, prefixes=("traffic:%s" % day, "micro:%s:subway" % day),
                   words=("地铁", "公交", "共享单车", "打车"),
                   low=6 * 60, high=10 * 60 + 30)


def drink_spent(plugin, item, now=None, hours=2):
    """同一杯饮料（奶茶/果茶等）在 hours 内是否已经买过。"""
    target = item_key(item)
    if not target:
        return False
    now = now or _amo_clock.now()
    wallet = getattr(plugin, "wallet", None)
    if wallet is None:
        return False
    cutoff = float(now.timestamp()) - int(hours) * 3600
    try:
        rows = list(wallet.ledger(200) or [])
    except Exception as exc:
        logger.debug("[amo] 查饮料流水失败，按没买过处理: %r", exc)
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            ts = float(row.get("ts") or 0)
            raw = row.get("delta_cents")
            delta = int(raw if raw is not None else (row.get("delta") or 0))
        except (TypeError, ValueError):
            continue
        if delta >= 0 or ts < cutoff:
            continue
        if item_key(str(row.get("reason") or "")) == target:
            return True
    return False


def late_snack_spent(plugin, now=None, hours=3):
    """hours 内是否已经有一顿夜宵（守护 late_night 或陪伴 micro/late_snack）。"""
    now = now or _amo_clock.now()
    cutoff = float(now.timestamp()) - int(hours) * 3600
    wallet = getattr(plugin, "wallet", None)
    if wallet is None:
        return False
    try:
        rows = list(wallet.ledger(200) or [])
    except Exception as exc:
        logger.debug("[amo] 查夜宵流水失败，按没有处理: %r", exc)
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            ts = float(row.get("ts") or 0)
            raw = row.get("delta_cents")
            delta = int(raw if raw is not None else (row.get("delta") or 0))
        except (TypeError, ValueError):
            continue
        if delta >= 0 or ts < cutoff:
            continue
        idem = str(row.get("idem") or "")
        reason = str(row.get("reason") or "")
        if idem.startswith("late_snack:") or any(
                word in reason for word in ("夜宵", "宵夜", "烧烤", "泡面")):
            return True
    return False


def pay_food(plugin, item, reason, source="meal", now=None, amount=None, idem=None,
             allow_night=False, instance_id="", ensure_block=True, minutes=None):
    """餐饮扣款唯一入口：JIT/微型事件/宵夜/日程都必须走这里。

    规则（2026-11-22 P0）：
    - 一天最多四顿：餐次实例 meal:<day>:<slot>（早/午/晚/宵夜，宵夜跨零点归前一天）；
    - 同一餐次实例 45 分钟内只允许一笔；超时再扣算「同一实例的加餐」，idem 带序号，
      账本 event_id 始终是实例 ID，日程块也只复用同一个；
    - 金额只来自价目表（price_for）；调用方给了 amount 就用调用方的固定值；
    - 餐点消费先落/复用日程块；夜间（00:00-06:00）必须有块才允许扣钱；
    - 15:00-16:00 加餐空档不占四顿配额，只做 10 分钟同物去重。

    返回 {ok, charged, reason, amount, idem, instance_id, slot, day, block_id}。
    """
    now = now or _amo_clock.now()
    item = item_key(item) or "餐饮"
    wallet = getattr(plugin, "wallet", None)
    if wallet is None:
        return {"ok": False, "charged": False, "reason": "no_wallet", "amount": 0,
                "idem": "", "instance_id": "", "slot": "", "day": "", "block_id": ""}
    if (sick_status(plugin) and late_snack_hours(now)
            and not sick_food_allowed({"text": item, "food": item})):
        logger.info("[amo] 生病期间禁止宵夜/夜间加餐，跳过扣款：%s", item)
        return {"ok": False, "charged": False, "reason": "sick", "amount": 0,
                "idem": "", "instance_id": "", "slot": "", "day": "", "block_id": ""}
    day, slot, auto_instance = meal_slot_at(now)
    instance = str(instance_id or auto_instance or "").strip()
    if instance:
        inst_day, inst_slot = parse_meal_instance(instance)
        if not inst_day:
            instance = ""
        else:
            day, slot = inst_day, inst_slot
    block_day = now.strftime("%Y-%m-%d")
    # 饮料（奶茶/果茶/咖啡）不算“一顿”，不占四顿配额、不切餐块。
    is_drink = item in DRINK_WORDS or any(word in item for word in DRINK_WORDS)
    if is_drink:
        instance, slot = "", ""
    # 加餐空档/饮料：不占四顿、不切餐块，仍走价目表 + 10 分钟同物去重。
    if not instance:
        if recent_duplicate(wallet, item, now=now):
            logger.warning("[amo] %d 分钟内重复餐饮已拦截：%s", DEDUPE_MINUTES, item)
            return {"ok": False, "charged": False, "reason": "duplicate", "amount": 0,
                    "idem": "", "instance_id": "", "slot": "", "day": day, "block_id": ""}
        price = int(amount if amount is not None else price_for(item))
        if price <= 0:
            return {"ok": False, "charged": False, "reason": "no_price", "amount": 0,
                    "idem": "", "instance_id": "", "slot": "", "day": day, "block_id": ""}
        charge_idem = idem or food_idem(source, item, now)
        try:
            paid = bool(wallet.debit(price, str(reason or item), charge_idem,
                                     category="餐饮", sub_category="加餐",
                                     item_name=item, trigger_reason=str(reason or "")))
        except Exception as exc:
            logger.warning("[amo] 餐饮扣款异常: %r", exc)
            return {"ok": False, "charged": False, "reason": "exception", "amount": price,
                    "idem": charge_idem, "instance_id": "", "slot": "", "day": day,
                    "block_id": ""}
        if not paid:
            return {"ok": False, "charged": False, "reason": "no_money", "amount": price,
                    "idem": charge_idem, "instance_id": "", "slot": "", "day": day,
                    "block_id": ""}
        return {"ok": True, "charged": True, "reason": "", "amount": price,
                "idem": charge_idem, "instance_id": "", "slot": "", "day": day,
                "block_id": ""}
    previous = meal_instance_rows(plugin, instance)
    if previous:
        last_ts = float(previous[-1].get("ts") or 0)
        if float(now.timestamp()) - last_ts <= SLOT_DEDUPE_MINUTES * 60:
            logger.warning("[amo] 同一餐次 %d 分钟内重复扣款已拦截：%s（%s）",
                           SLOT_DEDUPE_MINUTES, item, instance)
            return {"ok": False, "charged": False, "reason": "duplicate_slot",
                    "amount": 0, "idem": str(previous[-1].get("idem") or ""),
                    "instance_id": instance, "slot": slot, "day": day,
                    "block_id": "", "reused": True}
        seq = len(previous)
    else:
        seq = 0
    price = int(amount if amount is not None else price_for(item))
    if price <= 0:
        return {"ok": False, "charged": False, "reason": "no_price", "amount": 0,
                "idem": "", "instance_id": instance, "slot": slot, "day": day,
                "block_id": ""}
    block = {}
    if ensure_block and slot:
        start_min = int(now.hour) * 60 + int(now.minute)
        span = max(1, int(minutes if minutes is not None else MEAL_BLOCK_MINUTES))
        end_min = min(24 * 60, start_min + span)
        label = SLOT_LABELS.get(slot, "吃饭")
        activity = ("吃%s（宵夜）" % item) if slot == "late" else ("吃%s" % item)
        detail = "%s %.2f 元｜来源 %s" % (label, price / 100.0, source)
        block = ensure_block_window(plugin, block_day, _text_of(start_min), _text_of(end_min),
                                    activity, detail, category="meal", source=source,
                                    reason="餐饮落日程", instance_id=instance)
        if not block.get("ok"):
            # 日程层存在（可写块）或夜间时，扣款必须有块；极简假件没有日程写入能力才放行。
            state = getattr(plugin, "state", None)
            writable = any(callable(getattr(state, name, None)) for name in
                           ("commit_schedule_change", "apply_schedule", "save_day"))
            if in_night(now) or writable:
                logger.warning("[amo] 餐饮找不到/补不上日程块，拒绝扣款：%s", item)
                return {"ok": False, "charged": False, "reason": "no_block", "amount": 0,
                        "idem": "", "instance_id": instance, "slot": slot, "day": day,
                        "block_id": ""}
            block = {}
    charge_idem = str(idem or ("%s:%d" % (instance, seq)))
    ref = ""
    if block.get("block_id"):
        ref = "block:%s %s-%s" % (block.get("block_id"),
                                  block.get("start") or _text_of(start_min),
                                  block.get("end") or _text_of(end_min))
    sub_category = SLOT_LABELS.get(slot, "餐饮") if seq == 0 else (
        SLOT_LABELS.get(slot, "餐饮") + "加餐")
    try:
        paid = bool(wallet.debit(price, str(reason or item), charge_idem, ref=ref,
                                 category="餐饮", sub_category=sub_category,
                                 item_name=item, trigger_reason=str(reason or ""),
                                 event_id=instance))
    except Exception as exc:
        logger.warning("[amo] 餐饮扣款异常: %r", exc)
        return {"ok": False, "charged": False, "reason": "exception", "amount": price,
                "idem": charge_idem, "instance_id": instance, "slot": slot, "day": day,
                "block_id": str(block.get("block_id") or "")}
    if not paid:
        return {"ok": False, "charged": False, "reason": "no_money", "amount": price,
                "idem": charge_idem, "instance_id": instance, "slot": slot, "day": day,
                "block_id": str(block.get("block_id") or "")}
    if ensure_block and slot:
        coalesce_meal_blocks(plugin, block_day, slot=slot)
    return {"ok": True, "charged": True, "reason": "", "amount": price,
            "idem": charge_idem, "instance_id": instance, "slot": slot, "day": day,
            "block_id": str(block.get("block_id") or "")}
