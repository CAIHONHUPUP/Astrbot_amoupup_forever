# -*- coding: utf-8 -*-
"""日程引擎 v2：事件时间线 + 过渡全派生（纯函数，无 IO）。

设计（2026-11-22，A1）：
- **事件时间线**：真正存库的只有「事件」——她实际在做什么（睡觉/上班/吃饭/逛街…）。
  通勤块 ``source=transit_block`` 是旧引擎把过渡写进库的派生物，v2 不再持久化它们。
- **过渡全派生**：读侧按相邻两个事件的「前块地点 → 后块地点」现算过渡段，文案只用
  最终地名；同一地点/家内部移动不生成过渡，「刚出家…回家」这类前后矛盾文案在结构上
  不可能再出现（2026-11-22 16:46→17:21 事故根修）。
- **坏块降级**：``safe_view`` 只输出能安全展示的行；无法解析/重叠/空文案的块降级成
  「在家待着 / 在路上」，绝不把坏数据抛给面板和聊天。
- **零缺陷指标**：8 项指标（非法块/空洞/重叠/路径断裂/旧社交人名残留/页面聊天不一致/
  并发丢更新/写异常）由 ``validate`` 与调度层统计，任一 >0 即阻断灰度。

本模块只做纯计算，不读数据库、不写日志，方便 1000 天属性测试。
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Iterable, Mapping, Sequence

#: 块最短时长（分钟）：更短的碎片没有信息量，落库前丢弃/读侧降级。
MIN_BLOCK_MINUTES = 5
#: 派生过渡的最短/最长时长（分钟）。
TRANSIT_MIN_MINUTES = 10
TRANSIT_MAX_MINUTES = 40
#: 完整一天的时间线允许的最大空档（分钟）；超过记「空洞」。
MAX_HOLE_MINUTES = 90
#: 一天最后一分钟。
DAY_END = 24 * 60

HOME = "家"

# ---- 8 项指标（发布协议口径，顺序固定，报告直接按这个顺序贴） ----
METRIC_ILLEGAL = "illegal_block"          # 非法块：起止缺失/倒置/文案空
METRIC_HOLE = "hole"                      # 空洞：完整一天里超过阈值的空档
METRIC_OVERLAP = "overlap"                # 重叠：同一分钟被两块占用
METRIC_PATH_BREAK = "path_break"          # 路径断裂：跨地点但没有足够路程
METRIC_SOCIAL = "social_residue"          # 旧社交人名残留：A1 后日程不许有自动社交块
METRIC_PAGE_CHAT = "page_chat_mismatch"   # 页面/聊天不一致：两套读法输出不同
METRIC_LOST_UPDATE = "lost_update"        # 并发丢更新：提交时版本已变
METRIC_WRITE_ERROR = "write_error"        # 写异常：落库/影子计算抛错

METRIC_KEYS: tuple[str, ...] = (
    METRIC_ILLEGAL,
    METRIC_HOLE,
    METRIC_OVERLAP,
    METRIC_PATH_BREAK,
    METRIC_SOCIAL,
    METRIC_PAGE_CHAT,
    METRIC_LOST_UPDATE,
    METRIC_WRITE_ERROR,
)

METRIC_LABELS: dict[str, str] = {
    METRIC_ILLEGAL: "非法块",
    METRIC_HOLE: "空洞",
    METRIC_OVERLAP: "重叠",
    METRIC_PATH_BREAK: "路径断裂",
    METRIC_SOCIAL: "旧社交人名残留",
    METRIC_PAGE_CHAT: "页面/聊天不一致",
    METRIC_LOST_UPDATE: "并发丢更新",
    METRIC_WRITE_ERROR: "写异常",
}

#: 交通方式文案（与 amo_transition 的 _TRANSIT_RIDE_* 保持同一口径）。
_RIDE_BY_CODE = {
    "subway": "坐地铁", "bus": "坐公交", "taxi": "打车", "drive": "开车",
    "train": "坐高铁", "plane": "坐飞机", "walk": "走路", "bicycle": "骑车",
    "bike": "骑车",
}
_RIDE_BY_VEHICLE = {
    "地铁": "坐地铁", "公交": "坐公交", "打车": "打车", "出租车": "打车",
    "开车": "开车", "步行": "走路", "自行车": "骑车", "骑车": "骑车",
    "高铁": "坐高铁", "火车": "坐火车", "飞机": "坐飞机",
}

_HOME_WORDS = ("家", "卧室", "客厅", "厨房", "浴室", "阳台", "出租屋", "被窝", "床上")
_ROAD_WORDS = ("路上", "地铁", "公交", "出租车", "高铁", "火车", "飞机", "车站", "机场")


class Defect(dict):
    """一条缺陷记录（dict 子类，方便直接塞进审计/日志）。"""


def _defect(code: str, detail: str, *, index: int = -1, start: str = "") -> Defect:
    return Defect(code=code, detail=str(detail), index=int(index), start=str(start))


def empty_counts() -> dict[str, int]:
    """8 项指标的零值计数表。"""
    return {key: 0 for key in METRIC_KEYS}


def count_defects(defects: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """把缺陷列表折成 8 项计数（未出现的指标为 0）。"""
    counts = empty_counts()
    for item in defects or ():
        code = str((item or {}).get("code") or "")
        if code in counts:
            counts[code] += 1
    return counts


def merge_counts(*tables: Mapping[str, int] | None) -> dict[str, int]:
    """合并多份计数表（影子对比/多天累计用）。"""
    total = empty_counts()
    for table in tables:
        for key in METRIC_KEYS:
            try:
                total[key] += int((table or {}).get(key) or 0)
            except (TypeError, ValueError):
                continue
    return total


def zero(counts: Mapping[str, int] | None) -> bool:
    """8 项是否全 0（灰度晋级条件）。"""
    return all(int((counts or {}).get(key) or 0) == 0 for key in METRIC_KEYS)


# ---------------------------------------------------------------- 基础解析


def zone_of(place: Any, zone: Any = "") -> str:
    """地点 → 区域。优先用块自带 zone，其次走仓库统一地点表，最后按词猜。"""
    text = str(zone or "").strip()
    if text:
        return text
    place_text = str(place or "").strip()
    if not place_text:
        return ""
    mapper: Callable[[str], str] | None = None
    try:  # 仓库的统一地点表（纯函数域）
        from .domains.schedule.place import zone_for_place as mapper
    except ImportError:  # 直接按文件加载时
        try:
            from domains.schedule.place import zone_for_place as mapper
        except ImportError:
            mapper = None
    if mapper is not None:
        try:
            mapped = str(mapper(place_text) or "")
        except Exception:
            mapped = ""
        if mapped:
            return mapped
    if any(word in place_text for word in _ROAD_WORDS):
        return "road"
    if any(word in place_text for word in _HOME_WORDS):
        return "home"
    return ""


def _is_home(row: Mapping[str, Any]) -> bool:
    if str(row.get("zone") or "") == "home":
        return True
    place = str(row.get("place") or "")
    return any(word in place for word in _HOME_WORDS) or place == HOME


def _is_road(row: Mapping[str, Any]) -> bool:
    if str(row.get("zone") or "") == "road":
        return True
    return any(word in str(row.get("place") or "") for word in _ROAD_WORDS)


def _is_known_place(place: Any) -> bool:
    text = str(place or "").strip()
    return bool(text) and bool(zone_of(text))


def _minutes(text: Any) -> int | None:
    """HH:MM → 分钟。只接受 0:00-24:00；'24:00' 归一成 1440。"""
    raw = str(text or "").strip()
    if not raw or ":" not in raw:
        return None
    head, _, tail = raw.partition(":")
    try:
        hour, minute = int(head), int(tail[:2])
    except (TypeError, ValueError):
        return None
    if hour < 0 or hour > 24 or minute < 0 or minute > 59:
        return None
    if hour == 24 and minute != 0:
        return None
    return hour * 60 + minute


def _hm(value: int) -> str:
    value = max(0, min(DAY_END, int(value)))
    if value >= DAY_END:
        return "24:00"
    return "%02d:%02d" % (value // 60, value % 60)


def span_of(row: Mapping[str, Any]) -> tuple[int, int] | None:
    """取一块的 (start, end) 分钟；跨天（end < start 且 end<=06:00）按 +24h 算。

    起止缺失、格式非法、时长非正一律返回 None —— 调用方记「非法块」。
    """
    begin = _minutes(row.get("start"))
    finish = _minutes(row.get("end"))
    if begin is None or finish is None:
        return None
    if finish <= begin:
        # 跨天：22:30-01:10 这类，凌晨结束视为次日
        if finish <= 6 * 60:
            finish += DAY_END
        else:
            return None
    return begin, finish


def _clamp_span(begin: int, finish: int) -> tuple[int, int]:
    begin = max(0, min(DAY_END - 1, int(begin)))
    finish = max(begin + 1, min(DAY_END, int(finish)))
    return begin, finish


def _ride_of(row: Mapping[str, Any]) -> str:
    code = str(row.get("transit") or "")
    if code in _RIDE_BY_CODE:
        return _RIDE_BY_CODE[code]
    vehicle = str(row.get("vehicle") or "").strip()
    if vehicle in _RIDE_BY_VEHICLE:
        return _RIDE_BY_VEHICLE[vehicle]
    return "赶路"


def _is_derived_transit(row: Mapping[str, Any]) -> bool:
    """是不是旧引擎写进库的「过渡派生物」（v2 不再持久化，读侧重算）。"""
    if row.get("derived") or str(row.get("source") or "") in ("transit_block", "transit_v2",
                                                               "derived_idle"):
        return True
    activity = str(row.get("activity") or "")
    return (str(row.get("zone") or "") == "road"
            and str(row.get("category") or "") == "commute"
            and activity.endswith("（在路上）"))


def _stable_minutes(day: str, start_min: int, from_place: str, to_place: str) -> int:
    """确定性路程：同一天同一段永远同一个数（不依赖 random 全局状态）。"""
    seed = "transit|%s|%s|%s|%s" % (str(day or ""), int(start_min), from_place, to_place)
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    span = TRANSIT_MAX_MINUTES - TRANSIT_MIN_MINUTES + 1
    return TRANSIT_MIN_MINUTES + int(digest[:8], 16) % span


def _normalize_event(row: Mapping[str, Any],
                     mapper: Callable[[str], str] | None) -> dict[str, Any]:
    out = dict(row)
    place = str(out.get("place") or "").strip()
    zone = str(out.get("zone") or "").strip()
    if not zone and place:
        zone = (mapper(place) if callable(mapper) else "") or zone_of(place)
    if zone:
        out["zone"] = zone
    if place:
        out["place"] = place
    out["activity"] = str(out.get("activity") or "").strip()
    return out


def parse_events(
    rows: Sequence[Mapping[str, Any]] | None,
    *,
    zone_mapper: Callable[[str], str] | None = None,
    drop_transit: bool = True,
) -> tuple[list[dict[str, Any]], list[Defect]]:
    """把库里的行解析成「事件」：丢掉过渡派生物、坏块记非法、按开始时间排序。"""
    events: list[dict[str, Any]] = []
    defects: list[Defect] = []
    for index, row in enumerate(rows or ()):
        if not isinstance(row, Mapping):
            defects.append(_defect(METRIC_ILLEGAL, "非字典行", index=index))
            continue
        if drop_transit and _is_derived_transit(row):
            continue
        span = span_of(row)
        if span is None:
            defects.append(_defect(
                METRIC_ILLEGAL,
                "起止非法 start=%r end=%r" % (row.get("start"), row.get("end")),
                index=index, start=str(row.get("start") or "")))
            continue
        event = _normalize_event(row, zone_mapper)
        begin, finish = _clamp_span(*span)
        if not event.get("activity"):
            defects.append(_defect(METRIC_ILLEGAL, "空文案块", index=index,
                                   start=_hm(begin)))
            continue
        event["start"] = _hm(begin)
        event["end"] = _hm(finish)
        events.append(event)
    events.sort(key=lambda item: span_of(item) or (0, 0))
    return events, defects


# ---------------------------------------------------------------- 过渡派生


def _transit_row(
    begin: int,
    finish: int,
    prev: Mapping[str, Any],
    nxt: Mapping[str, Any],
    minutes: int,
) -> dict[str, Any]:
    from_place = str(prev.get("place") or "").strip()
    to_place = str(nxt.get("place") or "").strip()
    to_home = _is_home(nxt)
    from_home = _is_home(prev)
    vehicle = str(nxt.get("vehicle") or prev.get("vehicle") or "") or "地铁"
    ride = _ride_of({"transit": nxt.get("transit") or prev.get("transit") or "",
                     "vehicle": vehicle})
    if to_home:
        # 回程必须断言「出发地不是家」；这是 16:46 事故的结构性防线。
        activity = "从%s回家，%s（在路上）" % (from_place or "外面", ride)
        kind = "return"
    elif from_home:
        activity = "出门去%s，%s（在路上）" % (to_place, ride)
        kind = "outbound"
    else:
        activity = "离开%s，%s去%s（在路上）" % (from_place or "上一个地方", ride, to_place)
        kind = "transfer"
    row: dict[str, Any] = {
        "start": _hm(begin), "end": _hm(finish),
        "activity": activity, "place": "路上", "zone": "road",
        "vehicle": vehicle, "category": "commute",
        "source": "transit_v2", "transit_kind": kind, "derived": True,
        "transit_minutes": int(minutes),
    }
    code = str(nxt.get("transit") or prev.get("transit") or "")
    if code:
        row["transit"] = code
    return row


def _idle_row(begin: int, finish: int, prev: Mapping[str, Any],
              nxt: Mapping[str, Any]) -> dict[str, Any]:
    """相邻两块同地点/家内部时的空档填充：不生成交通，只写「待着」。"""
    place = str(prev.get("place") or nxt.get("place") or HOME)
    zone = str(prev.get("zone") or nxt.get("zone") or "") or zone_of(place)
    if _is_home(prev) and _is_home(nxt):
        place, zone = HOME, "home"
        label = "在家待着"
    else:
        label = "在%s待着" % place if place else "待着"
    return {
        "start": _hm(begin), "end": _hm(finish), "activity": label,
        "place": place or HOME, "zone": zone or "home",
        "category": "rest", "source": "derived_idle", "derived": True,
    }


def _needs_transit(prev: Mapping[str, Any], nxt: Mapping[str, Any]) -> bool:
    """两个事件之间是否需要一段路：同地点/家内部/任一端在路上都不需要。"""
    prev_place = str(prev.get("place") or "").strip()
    next_place = str(nxt.get("place") or "").strip()
    if not prev_place or not next_place or prev_place == next_place:
        return False
    if _is_road(prev) or _is_road(nxt):
        return False
    if _is_home(prev) and _is_home(nxt):
        return False
    return _is_known_place(prev_place) and _is_known_place(next_place)


def derive_transitions(
    events: Sequence[Mapping[str, Any]],
    *,
    day: str = "",
    minutes_of: Callable[[str, int, str, str], int] | None = None,
) -> tuple[list[dict[str, Any]], list[Defect]]:
    """事件时间线 → 带派生过渡的展示时间线；每个空档都会被过渡或「待着」填满。

    规则（写死可测）：
    - 相邻事件跨地点：空档 >= 所需路程时，派生一段落脚在下一块开始前的过渡；
      空档不够时记「路径断裂」，退化成整段空档的过渡（仍不瞬移）。
    - 相邻事件同地点/家内部：派生「在家待着 / 在X待着」，绝不写「回家」。
    - 派生行 source=transit_v2/derived_idle，不参与落库、不占 event_id。
    """
    rows: list[dict[str, Any]] = []
    defects: list[Defect] = []
    ordered = sorted((dict(item) for item in events or () if isinstance(item, Mapping)),
                     key=lambda item: span_of(item) or (0, 0))
    for index, event in enumerate(ordered):
        span = span_of(event)
        if span is None:
            defects.append(_defect(METRIC_ILLEGAL, "派生输入块非法", index=index))
            continue
        rows.append(event)
        if index + 1 >= len(ordered):
            continue
        nxt = ordered[index + 1]
        next_span = span_of(nxt)
        if next_span is None:
            continue
        gap = next_span[0] - span[1]
        if gap <= 0:
            continue
        if _needs_transit(event, nxt):
            from_place = str(event.get("place") or "")
            to_place = str(nxt.get("place") or "")
            need = _stable_minutes(day, span[1], from_place, to_place)
            if callable(minutes_of):
                try:
                    need = int(minutes_of(day, span[1], from_place, to_place) or 0)
                except Exception:
                    need = _stable_minutes(day, span[1], from_place, to_place)
            need = max(TRANSIT_MIN_MINUTES, min(TRANSIT_MAX_MINUTES, need))
            if gap < TRANSIT_MIN_MINUTES:
                # 连一段最短的路都放不下：物理上不可能，记路径断裂并整段降级。
                defects.append(_defect(
                    METRIC_PATH_BREAK,
                    "%s→%s 只有 %d 分钟，放不下最短 %d 分钟路程" % (
                        from_place, to_place, gap, TRANSIT_MIN_MINUTES),
                    index=index, start=_hm(span[1])))
                rows.append(_transit_row(span[1], next_span[0], event, nxt, gap))
            else:
                # 路程比空档长时按空档压缩（不再移动真实事件），过渡吃掉整段空档。
                ride_minutes = min(need, gap)
                start = next_span[0] - ride_minutes
                rows.append(_transit_row(start, next_span[0], event, nxt, ride_minutes))
                if start > span[1]:
                    rows.append(_idle_row(span[1], start, event, event))
        else:
            rows.append(_idle_row(span[1], next_span[0], event, nxt))
    rows.sort(key=lambda item: span_of(item) or (0, 0))
    return rows, defects


# ---------------------------------------------------------------- 校验


def validate(
    rows: Sequence[Mapping[str, Any]] | None,
    *,
    day: str = "",
    zone_mapper: Callable[[str], str] | None = None,
    require_full_day: bool = False,
    forbidden_names: Iterable[str] = (),
    social_categories: Sequence[str] = ("social", "date"),
    auto_sources: Iterable[str] = ("",),
) -> list[Defect]:
    """8 项指标里可在纯计算阶段判定的 5 项：非法块/空洞/重叠/路径断裂/社交残留。

    并发丢更新、页面聊天不一致、写异常由调度层统计。
    """
    defects: list[Defect] = []
    events, parse_defects = parse_events(rows, zone_mapper=zone_mapper, drop_transit=False)
    defects.extend(parse_defects)
    names = [str(name) for name in (forbidden_names or ()) if str(name or "").strip()]
    social_set = {str(item) for item in social_categories}
    auto_set = {str(item) for item in auto_sources}
    for index, event in enumerate(events):
        # A1 只禁「自动生成」的社交块；人/约会/watch 写入的社交块是合法事实，不计残留。
        source = str(event.get("source") or "")
        auto = source in auto_set
        if auto and str(event.get("category") or "") in social_set:
            defects.append(_defect(
                METRIC_SOCIAL,
                "自动社交块 category=%s activity=%s" % (
                    event.get("category"), event.get("activity")),
                index=index, start=str(event.get("start") or "")))
            continue
        if not auto:
            continue
        activity = str(event.get("activity") or "")
        hit = next((name for name in names if name and name in activity), "")
        if hit:
            defects.append(_defect(
                METRIC_SOCIAL,
                "旧社交人名残留 %s：%s" % (hit, activity),
                index=index, start=str(event.get("start") or "")))
    spans = [(span_of(event), event) for event in events]
    pairs = [(span, event) for span, event in spans if span is not None]
    for index in range(1, len(pairs)):
        prev_span, prev = pairs[index - 1]
        cur_span, cur = pairs[index]
        if prev_span is None or cur_span is None:
            continue
        if cur_span[0] < prev_span[1]:
            defects.append(_defect(
                METRIC_OVERLAP,
                "重叠 %s-%s 与 %s-%s" % (_hm(prev_span[0]), _hm(prev_span[1]),
                                          _hm(cur_span[0]), _hm(cur_span[1])),
                index=index, start=_hm(cur_span[0])))
        gap = cur_span[0] - prev_span[1]
        if require_full_day and gap > MAX_HOLE_MINUTES:
            defects.append(_defect(
                METRIC_HOLE,
                "空档 %d 分钟（%s-%s）" % (gap, _hm(prev_span[1]), _hm(cur_span[0])),
                index=index, start=_hm(prev_span[1])))
        if gap >= 0 and _needs_transit(prev, cur):
            from_place = str(prev.get("place") or "")
            to_place = str(cur.get("place") or "")
            if gap < TRANSIT_MIN_MINUTES:
                # 跨地点却几乎没有空档：物理上到不了，记路径断裂。
                defects.append(_defect(
                    METRIC_PATH_BREAK,
                    "跨地点 %s→%s 只有 %d 分钟，放不下最短 %d 分钟路程" % (
                        from_place, to_place, gap, TRANSIT_MIN_MINUTES),
                    index=index, start=_hm(prev_span[1])))
    return defects


# ---------------------------------------------------------------- 读侧降级


def _degrade_label(row: Mapping[str, Any]) -> dict[str, Any]:
    """坏块统一降级：在家 → 「在家待着」，其余 → 「在路上」。"""
    if _is_home(row):
        return {"activity": "在家待着", "place": HOME, "zone": "home", "category": "rest"}
    return {"activity": "在路上", "place": "路上", "zone": "road", "category": "commute"}


def safe_view(
    rows: Sequence[Mapping[str, Any]] | None,
    *,
    day: str = "",
    now_min: int | None = None,
    zone_mapper: Callable[[str], str] | None = None,
    minutes_of: Callable[[str, int, str, str], int] | None = None,
    derive: bool = True,
) -> tuple[list[dict[str, Any]], list[Defect]]:
    """读侧唯一安全视图：坏块降级、重叠压直、过渡全派生。

    返回 (rows, defects)。rows 保证：span 合法、按时间升序、互不重叠、每块都有
    非空 activity；带 derived 标记的行是现算的，不落库。
    """
    defects: list[Defect] = []
    events, parse_defects = parse_events(rows, zone_mapper=zone_mapper, drop_transit=True)
    defects.extend(parse_defects)
    # 非法块降级：能读出 start 的按原 start 给一个最小安全块，读不出 start 的直接丢。
    degraded: list[dict[str, Any]] = []
    for index, row in enumerate(rows or ()):
        if not isinstance(row, Mapping):
            continue
        if span_of(row) is not None:
            continue
        begin = _minutes(row.get("start"))
        if begin is None:
            continue
        label = _degrade_label(row)
        begin, finish = _clamp_span(begin, begin + 30)
        degraded_row = dict(row)
        degraded_row.update(label)
        degraded_row.update({"start": _hm(begin), "end": _hm(finish),
                             "source": "safe_view_degraded", "degraded": True})
        degraded.append(degraded_row)
        defects.append(_defect(METRIC_ILLEGAL, "坏块已降级为%s" % label["activity"],
                               index=index, start=_hm(begin)))
    merged = events + degraded
    merged.sort(key=lambda item: span_of(item) or (0, 0))
    # 重叠压直：后块起点推到前块结束；推没了才丢。
    fixed: list[dict[str, Any]] = []
    cursor = 0
    for event in merged:
        span = span_of(event)
        if span is None:
            continue
        begin, finish = span
        if begin < cursor:
            defects.append(_defect(METRIC_OVERLAP,
                                   "重叠已压直 %s-%s" % (_hm(begin), _hm(finish)),
                                   start=_hm(begin)))
            begin = cursor
        if finish <= begin:
            continue
        event = dict(event)
        event["start"], event["end"] = _hm(begin), _hm(finish)
        fixed.append(event)
        cursor = finish
    if not derive:
        return fixed, defects
    derived, derive_defects = derive_transitions(fixed, day=day, minutes_of=minutes_of)
    defects.extend(derive_defects)
    # 当前时段标记：只给展示层用，不改数据。
    if now_min is not None:
        for row in derived:
            span = span_of(row)
            if span and span[0] <= int(now_min) < span[1]:
                row["current"] = True
            if span and span[1] <= int(now_min):
                row["happened"] = True
    return derived, defects


# ---------------------------------------------------------------- 页面/聊天一致性


def timeline_text(rows: Sequence[Mapping[str, Any]] | None, limit: int = 24) -> list[str]:
    """把时间线拍成「HH:MM-HH:MM 活动（地点）」行，页面/聊天共用这一种口径。"""
    lines: list[str] = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        span = span_of(row)
        activity = str(row.get("activity") or "").strip()
        if span is None or not activity:
            continue
        place = str(row.get("place") or "").strip()
        # 路上块文案里已经写了「（在路上）」，不再重复贴地点。
        suffix = ("（%s）" % place) if place and str(row.get("zone") or "") != "road" else ""
        lines.append("%s-%s %s%s" % (_hm(span[0]), _hm(span[1]), activity, suffix))
        if len(lines) >= limit:
            break
    return lines


def consistency_defects(
    tree_rows: Sequence[Mapping[str, Any]] | None,
    text_lines: Sequence[str] | None,
    *,
    limit: int = 24,
) -> list[Defect]:
    """页面（树）与聊天（文本）是否同一份时间线；不一致逐条记指标。"""
    left = timeline_text(tree_rows, limit=limit)
    right = [str(line).strip() for line in (text_lines or ()) if str(line).strip()]
    defects: list[Defect] = []
    if left == right:
        return defects
    longest = max(len(left), len(right))
    for index in range(longest):
        a = left[index] if index < len(left) else ""
        b = right[index] if index < len(right) else ""
        if a != b:
            defects.append(_defect(METRIC_PAGE_CHAT, "页面=%r 聊天=%r" % (a, b),
                                   index=index))
    return defects


# ---------------------------------------------------------------- 影子对比


def shadow_diff(
    old_rows: Sequence[Mapping[str, Any]] | None,
    new_rows: Sequence[Mapping[str, Any]] | None,
    *,
    limit: int = 12,
) -> dict[str, Any]:
    """影子对比：同一份输入，旧引擎行 vs v2 视图行的差异摘要（只读证据用）。"""
    old_text = timeline_text(old_rows, limit=limit)
    new_text = timeline_text(new_rows, limit=limit)
    return {
        "same": old_text == new_text,
        "old_lines": old_text,
        "new_lines": new_text,
        "old_count": len(list(old_rows or ())),
        "new_count": len(list(new_rows or ())),
    }
