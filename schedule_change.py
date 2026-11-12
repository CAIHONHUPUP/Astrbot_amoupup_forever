"""从用户说的话里识别「今天要改的日程」，交给 Amo 去改那一段。

只认明确改变当天行程的说法（去接她 / 提前回家 / 不去了 / 出去吃 / 该睡了），
不认泛泛的闲聊，也跳过否定句（「别来接我」「不想去吃」）。

改日程是**会落库**的副作用，所以这里刻意保守：
- 每条规则带一个锚点（anchor），决定改哪一段，而不是无脑改"现在"：
  ``off_work`` = 接下来那段下班通勤，``meal`` = 接下来那顿饭，``now`` = 当前这一段；
- 睡觉只在地点合理的时间（20:00 之后或凌晨）才生效，白天说「早点睡」不会把整天的
  晚饭、休息全改成睡觉；
- 已经是睡觉的时段，不会因为一句「去吃」被叫醒；
- 目标和现状一样就不写库（避免每句话都落一次盘）。
"""
from __future__ import annotations

import datetime
import re
from typing import Any, TypedDict


class Rule(TypedDict, total=False):
    keys: tuple[str, ...]
    activity: str
    place: str
    category: str
    anchor: str
    negatable: bool
    split: bool
    duration: int

# (触发词, 新的活动, 地点, 分类, 锚点, 是否受否定词保护)
_RULES: list[Rule] = [
    {
        # 主语是"她"：你来接我 / 你去接我 —— 她去把主人接回来
        "keys": ("你来接我", "你来接下我", "你来接一下我", "你去接我", "你下班来接我"),
        "activity": "我去接他，一起回家",
        "place": "路上",
        "category": "commute",
        "anchor": "off_work",
        "negatable": True,
    },
    {
        # 主语是"他"：我来接你 / 来接我 / 接你下班 —— 他开车来接她
        "keys": ("我来接你", "我下班来接你", "来接我", "来接下我", "接我一下", "过来接我",
                 "开车来接", "来接你", "来接一下", "接你下班", "接你回去"),
        "activity": "他开车来接我，一起回家",
        "place": "路上",
        "category": "commute",
        "anchor": "off_work",
        "negatable": True,
    },
    {
        # 「下班了，赶快回家吧」：让她尽快回家；只在人还在上班/路上时改当前这段，
        # 已经在家/在睡就不动（否则会把「休息」改成「在路上」）。
        "keys": ("赶快回家", "快回家", "赶紧回家", "快点回家", "马上回家",
                 "现在就回家", "快回去", "赶紧回去", "回家吧"),
        "activity": "下班回家，路上",
        "place": "路上",
        "category": "commute",
        "anchor": "off_work_now",
        "negatable": True,
    },
    {
        # 打吊水/输液：把当前这段拆出一段医院吊水（默认 3 小时），日程上能看到具体时间
        "keys": ("打吊水", "输液", "挂水", "去打吊水", "去输液"),
        "activity": "在医院打吊水",
        "place": "医院",
        "category": "health",
        "anchor": "now",
        "negatable": True,
        "split": True,
        "duration": 180,
    },
    {
        # 生病请假类：主人直接说「别去上班 / 今天不上班」
        "keys": ("别去上班", "不去上班", "今天不上班", "别上班了", "不用上班"),
        "activity": "请假在家休息",
        "place": "家",
        "category": "health",
        "anchor": "now",
        "negatable": False,
    },
    {
        "keys": ("早点回家", "提前回家", "先回去了", "想回家", "早点回去"),
        "activity": "提前收拾东西回家",
        "place": "家",
        "category": "rest",
        "anchor": "off_work",
        "negatable": True,
    },
    {
        # 直接对她说「你生病了/感冒了/发烧了」：改今天为生病请假在家养病
        "keys": ("你生病了", "你感冒了", "你发烧了", "你不舒服", "她生病了", "生病请假"),
        "activity": "生病了，请假在家养病",
        "place": "家",
        "category": "health",
        "anchor": "now",
        "negatable": True,
    },
    {
        # 生病相关：请假回家 / 在家养病 / 去医院，优先级放在「回家」前面
        "keys": ("请假回家", "回家休息", "回去休息", "回家养病", "回家躺着", "先回家休息"),
        "activity": "请了假，回家躺着养病",
        "place": "家",
        "category": "health",
        "anchor": "now",
        "negatable": True,
    },
    {
        "keys": ("去医院", "去看病", "看病", "挂号", "看医生", "去诊所"),
        "activity": "在医院排队看诊",
        "place": "医院",
        "category": "health",
        "anchor": "now",
        "negatable": True,
        "split": True,
        "duration": 120,
    },
    {
        "keys": ("请个假", "请假", "请假在家", "在家休息一天"),
        "activity": "请假在家休息",
        "place": "家",
        "category": "health",
        "anchor": "now",
        "negatable": True,
    },
    {
        # 这几句本身就是否定式（不加班 / 不去了），不再做否定词判断
        "keys": ("不去了", "不加班了", "别加班了", "不去加班了"),
        "activity": "不去了，改回家待着",
        "place": "家",
        "category": "rest",
        "anchor": "now",
        "negatable": False,
    },
    {
        "keys": ("一起吃", "去吃", "出来吃", "出去吃", "请你吃饭"),
        "activity": "和他一起出去吃饭",
        "place": "外面",
        "category": "meal",
        "anchor": "meal",
        "negatable": True,
    },
    {
        "keys": ("一起睡", "早点睡", "睡吧", "该睡了", "去睡觉"),
        "activity": "上床睡觉",
        "place": "家",
        "category": "sleep",
        "anchor": "now",
        "negatable": True,
    },
]

_NEGATION_TOKENS = ("别", "不", "没", "甭", "勿")
#: 这些词出现时是「直接出发」，不算长期呆家，不换睡衣
TRAVEL_WORDS = ("旅游", "旅行", "出发", "出门", "机场", "车站", "高铁", "动车",
                "赶车", "去玩", "度假", "酒店", "住店", "出差")
# 「不早了」「别太晚」「差不多了」是关心/口癖，不是否定关键词
_SOFTENERS = ("不早", "不晚", "别太", "不太", "不久", "不多", "没多", "差不")
_NEGATION_WINDOW = 4
_MAX_TEXT = 200
# 只改"今天"：说了明天/后天/昨天的事，不该动今天的日程
_OTHER_DAYS = ("明天", "后天", "下周", "下个月", "明早", "明晚", "明年",
               "昨天", "前天", "上周", "上个月", "上次", "那时候")


def _negated(raw: str, pos: int) -> bool:
    """关键词前 4 个字里出现否定词就算否定句，但要排除「不早了」这类关心话。

    - 「别来接我」「不想去吃」「别开车来接我」→ 否定，不改日程；
    - 「不早了，该睡了」「别太晚，早点睡」「差不多了，该睡了」→ 不是否定，照常改。
    """
    window = raw[max(0, pos - _NEGATION_WINDOW):pos]
    if window.startswith(_SOFTENERS):
        return False
    return any(token in window for token in _NEGATION_TOKENS)


def should_switch_to_pajamas(change, text: str) -> bool:
    """这次改日程之后要不要换成睡衣。

    只有「生病/请假回家养病、需要长期待在家」才换；如果是旅游/出差要直接出发，
    即使说了请假/回家也不换，保持出门那套。
    """
    if not change:
        return False
    raw = str(text or "")
    if any(word in raw for word in TRAVEL_WORDS):
        return False
    if str(change.get("place") or "") != "家":
        return False
    if str(change.get("category") or "") == "health":
        return True
    activity = str(change.get("activity") or "")
    return any(word in activity for word in ("养病", "休息", "躺着", "睡觉"))


def detect_schedule_change(text: str) -> dict[str, Any] | None:
    """返回 {activity, place, category, anchor} 或者 None。"""
    raw = re.sub(r"\s+", "", str(text or ""))
    if not raw or len(raw) > _MAX_TEXT:
        return None
    if any(word in raw for word in _OTHER_DAYS):
        return None
    for rule in _RULES:
        for key in rule["keys"]:
            pos = raw.find(key)
            if pos < 0:
                continue
            if rule.get("negatable", True) and _negated(raw, pos):
                continue
            change = {
                "activity": rule["activity"],
                "place": rule["place"],
                "category": rule["category"],
                "anchor": rule.get("anchor") or "now",
            }
            if rule.get("split"):
                change["split"] = True
                change["duration"] = int(rule.get("duration") or 0)
            return change
    return None


def _minutes(text: Any) -> int | None:
    try:
        hour, minute = str(text).split(":")[:2]
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def _pick_target(items: list[dict[str, Any]], anchor: str, now_min: int) -> dict[str, Any] | None:
    """找要改的那一段：锚点优先，找不到再退回当前这一段。"""
    current = None
    for item in items:
        if not isinstance(item, dict):
            continue
        start = _minutes(item.get("start"))
        end = _minutes(item.get("end"))
        if start is None or end is None:
            continue
        if start <= now_min < end:
            current = item
            break
    if anchor == "off_work_now":
        if current is not None and str(current.get("category") or "") in ("work", "commute"):
            return current
        anchor = "off_work"
    if anchor in ("off_work", "meal"):
        wanted = "commute" if anchor == "off_work" else "meal"
        for item in items:
            if not isinstance(item, dict):
                continue
            start = _minutes(item.get("start"))
            if start is None or start < now_min:
                continue
            if str(item.get("category") or "") == wanted:
                return item
        if current is not None and str(current.get("category") or "") == wanted:
            return current
        return None
    return current


def apply_change(items: list[dict[str, Any]], change: dict[str, Any] | None,
                 now: datetime.datetime) -> list[dict[str, Any]]:
    """改写「锚点那一段」；睡觉类还会把之后到凌晨的整段并成睡觉。"""
    if not items or not change:
        return items
    category = str(change.get("category") or "")
    # 「早点睡」多半是关心对方；只有在晚上 20:00-23:59 才当成"今天就睡这了"。
    # 凌晨也不能改：那时候后面的 24 小时都会被她睡过去。
    if category == "sleep" and not (20 <= now.hour <= 23):
        return items
    now_min = now.hour * 60 + now.minute
    target = _pick_target(items, str(change.get("anchor") or "now"), now_min)
    if target is None:
        return items
    if str(target.get("category") or "") == "sleep" and category != "sleep":
        # 已经在睡了，别因为一句「去吃」把人叫醒
        return items
    activity = str(change.get("activity") or "").strip()
    place = str(change.get("place") or target.get("place") or "").strip()
    if not activity or str(target.get("activity") or "") == activity:
        return items
    sleeping = category == "sleep"
    split = bool(change.get("split"))
    duration = int(change.get("duration") or 0)
    target_start = _minutes(target.get("start"))
    target_end = _minutes(target.get("end"))
    split_start = split_end = None
    if split and duration > 0 and target_start is not None and target_end is not None:
        split_start = max(now_min, target_start)
        split_end = min(24 * 60, split_start + duration)
        if split_end <= split_start:
            split_start = split_end = None

    def _hhmm(value):
        return "%02d:%02d" % divmod(int(value), 60)

    out: list[dict[str, Any]] = []
    hit = False
    absorb_until = None
    for item in items:
        if not isinstance(item, dict):
            out.append(item)
            continue
        if hit and absorb_until:
            start = _minutes(item.get("start"))
            end = _minutes(item.get("end"))
            if start is None or end is None:
                out.append(item)
                continue
            if end <= absorb_until:
                continue
            if start < absorb_until:
                piece = dict(item)
                piece["start"] = _hhmm(absorb_until)
                out.append(piece)
                absorb_until = None
                continue
            absorb_until = None
        if not hit and item is target:
            hit = True
            if split_start is not None:
                if split_start > target_start:
                    before = dict(item)
                    before["end"] = _hhmm(split_start)
                    out.append(before)
                visit = dict(item)
                visit["start"] = _hhmm(split_start)
                visit["end"] = _hhmm(split_end)
                visit["activity"] = activity
                if place:
                    visit["place"] = place
                if category:
                    visit["category"] = category
                visit["detail"] = (
                    "打吊水，约%d分钟" % (split_end - split_start)
                    if "打吊水" in activity else "看诊拿药，约%d分钟" % (split_end - split_start)
                )
                out.append(visit)
                if split_end < target_end:
                    after = dict(item)
                    after["start"] = _hhmm(split_end)
                    out.append(after)
                else:
                    absorb_until = split_end
                continue
            new_item = dict(item)
            new_item["activity"] = activity
            if place:
                new_item["place"] = place
            if category:
                new_item["category"] = category
            out.append(new_item)
            continue
        if hit and sleeping:
            new_item = dict(item)
            new_item["activity"] = "睡觉"
            new_item["place"] = "家"
            new_item["category"] = "sleep"
            out.append(new_item)
            continue
        out.append(item)
    return out
