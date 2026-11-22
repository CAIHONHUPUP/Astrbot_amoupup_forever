# -*- coding: utf-8 -*-
"""日程内容政策（P1）：长发呆 / 文案地点对齐 / 同地点碎块合并（纯函数）。

用户 2026-11-22 反馈「外边原地发呆一小时太夸张」，规则：
1. 户外（非家/公司）发呆 >= 20 分钟 → 换成具体活动（逛店/坐下歇脚/慢慢走）；
2. 家/公司的发呆保留，但文案要和地点一致（不再出现「路上刷手机」却记在家/公司）；
3. 同地点、同为发呆/待着的相邻块合并，合并后不超过 90 分钟；
4. 只改文案/合并空转碎块，**不动事件起止时间、不动过渡、不动约会社交块**。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

try:
    from . import schedule_engine_v2 as engine
except ImportError:
    import schedule_engine_v2 as engine

IDLE_WORDS = ("发呆", "放空", "待着", "闲着", "没事干", "缓一缓", "躺会儿", "无所事事")
HOME_WORDS = ("在家", "家里", "沙发", "被窝", "床上", "窝着")
ROAD_WORDS = ("路上", "地铁", "公交", "打车", "开车", "车厢", "候车")
OFFICE_WORDS = ("工位", "公司", "上班", "办公室")
#: 合并上限：两段发呆合起来超过这个分钟数就不并，避免把整个晚上吞成一块。
MERGE_CAP_MINUTES = 90
#: 户外发呆允许的最长时长；超过就换成具体活动。
OUTDOOR_IDLE_LIMIT = 20
#: 户外地点 → 具体活动。
_OUTDOOR_ACTIVITY = (
    (("商场", "店", "街", "超市"), "随便逛逛，看看店"),
    (("饭店", "餐厅", "小馆", "咖啡", "茶"), "坐着慢慢喝点东西"),
    (("景区", "公园", "海边", "沙滩", "古镇"), "慢慢走，拍几张照"),
    (("医院", "诊所"), "在候诊区坐着等"),
    (("车站", "机场", "高铁", "火车站"), "在候车区等车"),
)


#: 这些分类的块即使文案短也算「有信息量」，值得单独生成块日记。
INFORMATIVE_CATEGORIES = (
    "work", "meal", "play", "social", "date", "travel", "health", "leave",
    "commute", "shopping", "scenic", "appointment", "finance",
)


def is_informative(row: Mapping[str, Any]) -> bool:
    """块日记分级：有信息量的块才生成小日记；发呆/待着/睡觉交给大日记带过。"""
    if not isinstance(row, Mapping):
        return False
    if is_idle(row):
        return False
    category = str(row.get("category") or "")
    activity = str(row.get("activity") or "").strip()
    if category == "sleep" or activity in ("休息", "歇着", "睡觉"):
        return False
    if category in INFORMATIVE_CATEGORIES:
        return True
    return len(activity) >= 2


def _zone(row: Mapping[str, Any]) -> str:
    return str(row.get("zone") or "") or engine.zone_of(str(row.get("place") or ""))


def is_idle(row: Mapping[str, Any]) -> bool:
    activity = str(row.get("activity") or "")
    return any(word in activity for word in IDLE_WORDS)


def _place_activity(place: str, zone: str) -> str:
    if zone == "home":
        return "窝在沙发上刷手机，缓一缓"
    if zone == "office":
        return "在工位上摸鱼刷手机"
    for words, text in _OUTDOOR_ACTIVITY:
        if any(word in place for word in words):
            return text
    return "四处走走看看"


def align_text(row: Mapping[str, Any]) -> dict[str, Any]:
    """文案里写「路上」但地点在家/公司（或反过来）时，改成贴地点的说法。"""
    out = dict(row)
    activity = str(out.get("activity") or "")
    zone = _zone(out)
    place = str(out.get("place") or "")
    if any(word in activity for word in ROAD_WORDS) and zone in ("home", "office"):
        out["activity"] = _place_activity(place, zone)
        return out
    if any(word in activity for word in HOME_WORDS) and zone in ("office", "road"):
        out["activity"] = _place_activity(place, zone)
    return out


def constrain_idle(row: Mapping[str, Any]) -> dict[str, Any]:
    """户外长发呆 → 具体活动；时间/地点/分类不变。"""
    out = dict(row)
    if not is_idle(out):
        return out
    span = engine.span_of(out)
    if span is None:
        return out
    zone = _zone(out)
    if zone in ("home", "office"):
        return out
    if span[1] - span[0] < OUTDOOR_IDLE_LIMIT:
        return out
    place = str(out.get("place") or "")
    out["activity"] = _place_activity(place, zone)
    out["content_policy"] = "outdoor_idle"
    return out


def _label(row: Mapping[str, Any]) -> str:
    activity = str(row.get("activity") or "")
    if any(word in activity for word in ("刷手机", "摸鱼")):
        return activity
    return "窝着刷手机，发发呆"


def merge_idle(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """合并相邻同地点、同为发呆的块（间隔为 0，合并后 <= 90 分钟）。"""
    ordered = [dict(row) for row in rows or () if isinstance(row, Mapping)]
    ordered.sort(key=lambda row: engine.span_of(row) or (0, 0))
    out: list[dict[str, Any]] = []
    for row in ordered:
        span = engine.span_of(row)
        if not span or not is_idle(row):
            out.append(row)
            continue
        merged = False
        if out:
            prev = out[-1]
            prev_span = engine.span_of(prev)
            if (prev_span and is_idle(prev)
                    and str(prev.get("place") or "") == str(row.get("place") or "")
                    and _zone(prev) == _zone(row)
                    and prev_span[1] == span[0]
                    and span[1] - prev_span[0] <= MERGE_CAP_MINUTES):
                prev["end"] = engine._hm(span[1])
                prev["activity"] = _label(prev)
                prev["merged_blocks"] = int(prev.get("merged_blocks") or 1) + 1
                merged = True
        if not merged:
            out.append(row)
    return out


def polish(rows: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """对齐文案 → 约束户外长发呆 → 合并同地点碎块（幂等）。"""
    aligned = [constrain_idle(align_text(row)) for row in (rows or ())
               if isinstance(row, Mapping)]
    return merge_idle(aligned)
