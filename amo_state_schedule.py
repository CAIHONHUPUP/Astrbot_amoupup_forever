# -*- coding: utf-8 -*-
"""日程状态机 mixin：从 amo_human_state.py 按域拆出（只搬方法，行为零变化）。"""
import asyncio
import json
import re
from datetime import date
from datetime import timedelta
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

try:  # 公共层：helper/常量 + 会被测试改写的模块级函数代理
    from .amo_state_common import (
        _now,
        _validate_activity,
        minutes_now,
        normalize_place,
        people_from_text,
        today_str,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_state_common import (
            _now,
            _validate_activity,
            minutes_now,
            normalize_place,
            today_str,
        )

# 稳定事件：日程写完后发布；当前订阅者为空，行为与直接调用完全兼容。
try:
    from .amo_events import ScheduleChanged
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_events import ScheduleChanged

try:  # 事实层统一：一个 event_id 贯穿日程 / 账本 / 块日记 / 记忆
    from .amo_timeline import block_id, ensure_event_id, stamp_event_ids
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_timeline import block_id, ensure_event_id, stamp_event_ids

try:  # 日类型（纯函数）：工作日/周末/节假日
    from .day_types import normalize_day_types, pick_day_type
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            from day_types import normalize_day_types, pick_day_type
        except ImportError:
            normalize_day_types = None  # type: ignore[assignment]
            pick_day_type = None  # type: ignore[assignment]

try:  # amo_shared：时间/日程/心情共享底层
    from .amo_shared import (
        MOOD_KEYS,
        SCHEDULE_VERSION,
        city_or_empty,
        day_meta,
        logger,
        now_ts,
        parse_hhmm,
        schedule_category_mood,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared import (
            MOOD_KEYS,
            SCHEDULE_VERSION,
            city_or_empty,
            day_meta,
            logger,
            now_ts,
            parse_hhmm,
            schedule_category_mood,
        )

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO
    _MixinBase = object


def _minute_of(text):
    """'HH:MM' -> 分钟；认不出返回 None（追加日程的重叠判定用）。"""
    try:
        hour, minute = str(text or "").strip().split(":", 1)
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def _item_overlaps_any(row, others):
    """row 是否与 others 里任一块在分钟上重叠（同日半开区间 [start, end)）。"""
    start, end = _minute_of((row or {}).get("start")), _minute_of((row or {}).get("end"))
    if start is None or end is None or end <= start:
        return False
    for other in others or []:
        if not isinstance(other, dict):
            continue
        begin, finish = _minute_of(other.get("start")), _minute_of(other.get("end"))
        if begin is None or finish is None or finish <= begin:
            continue
        if start < finish and begin < end:
            return True
    return False


def _vehicle_text(item):
    """面板显示用：有 transit 就以 transit 为准，不再让旧的 vehicle 文案打架。"""
    transit = str((item or {}).get("transit") or "")
    mapped = {"subway": "地铁", "bus": "公交", "taxi": "打车", "drive": "开车"}.get(transit)
    return mapped or str((item or {}).get("vehicle") or "")


#: 块日记关心的事实字段：只有这些变了才让这一块的日记失效。快照/结算顺带写的
#: 附加字段（cost_settled / locked / source…）不该把日记打 stale——11-21 23:59
#: 快照 1 分钟 bump 14 次就是这样制造出的 stale 风暴。
_DIARY_BLOCK_FIELDS = ("start", "end", "activity", "place", "zone", "category",
                       "detail", "people", "companions")


def _diary_block_signature(row):
    """块里跟日记正文有关的字段指纹（people 的顺序也会影响措辞，按顺序比）。"""
    row = row if isinstance(row, dict) else {}
    out = []
    for key in _DIARY_BLOCK_FIELDS:
        value = row.get(key)
        if isinstance(value, (list, tuple)):
            value = tuple(str(x) for x in value)
        else:
            value = str(value or "")
        out.append((key, value))
    return tuple(out)

# ---------------------------------------------------------------- 日程写入收口
# 纯函数：锁定 / 过去只读 / 写入后体检。由 _commit_schedule_change_locked 调用，
# tests/test_schedule_guard.py 也直接测。2026-11-18 收口：15 个写入方没有锁、没有边界检查。
_OFFICE_WORDS = ("公司", "写字楼", "办公室", "工位")
#: 这些来源写的块会被打锁，jit/分段重算不许覆盖。
#: 注意：这里只放"整天都该锁"的来源；社交块用 item 自带的 locked=True 单条上锁
#: （2026-11-20：曾把 social 加进来，结果一次社交提交锁死整天，jit 再也更新不了）。
_LOCK_SOURCES = ("manual", "command")   # date 只锁单条约会块（item.locked），不锁整天
_WORK_START_HOUR = 9
_WORK_END_HOUR = 18


def _hm(value):
    text = str(value or "")
    if len(text) < 5 or text[2] != ":":
        return None
    try:
        return int(text[:2]) * 60 + int(text[3:5])
    except ValueError:
        return None


def _row_overlaps(a, b):
    a_start, a_end = _hm(a.get("start")), _hm(a.get("end"))
    b_start, b_end = _hm(b.get("start")), _hm(b.get("end"))
    if a_start is None or b_start is None:
        return False
    if a_end is None or b_end is None:
        return True
    return a_start < b_end and b_start < a_end


def _office_shift_starts(items, work_start=_WORK_START_HOUR, work_end=_WORK_END_HOUR):
    """给每个 office 块标出「白天连续在岗段」的起始分钟（按 id(row) 索引）。

    连续 = 上一块的 end 正好等于本块 start，且上一块也是 office。用来区分：
    - 白天上班一直连到晚上：14:00-18:00 接着 18:00-19:00…… 是同一个在岗段，
      属于真实的延时/夜班，不能折回家；
    - 夜里突然冒出来的孤立公司块（21:23 在工位）：没有白天连续段，按旧规则折家。
    """
    try:
        work_start = int(work_start)
        work_end = int(work_end)
    except (TypeError, ValueError):
        work_start, work_end = _WORK_START_HOUR, _WORK_END_HOUR
    rows = []
    for row in items or []:
        if not isinstance(row, dict):
            continue
        start, end = _hm(row.get("start")), _hm(row.get("end"))
        if start is None:
            continue
        rows.append((start, end if end is not None else start, row))
    rows.sort(key=lambda item: (item[0], item[1]))
    starts = {}
    prev_end, prev_chain = None, None
    for start, end, row in rows:
        place = str(row.get("place") or "")
        at_office = str(row.get("zone") or "") == "office" or any(
            word in place for word in _OFFICE_WORDS)
        if not at_office:
            prev_end, prev_chain = None, None
            continue
        if prev_end is not None and prev_end == start:
            chain = prev_chain
        else:
            chain = start
        starts[id(row)] = chain
        prev_end, prev_chain = end, chain
    return starts


def _office_shift_continues(chain_start, work_start=_WORK_START_HOUR,
                            work_end=_WORK_END_HOUR):
    """连续在岗段是否始于正常上班时段（白天连到晚上才算真实在岗）。"""
    if chain_start is None:
        return False
    try:
        hour = int(chain_start) // 60
    except (TypeError, ValueError):
        return False
    return int(work_start) <= hour < int(work_end)


def mark_locked(items, source):
    """面板/命令/约会写的块打锁；返回新 list。"""
    if str(source or "") not in _LOCK_SOURCES:
        return list(items or [])
    return [dict(row, locked=True) if isinstance(row, dict) and row.get("start") else row
            for row in items or []]


def protect_locked(old_items, new_items, source):
    """非锁定来源不许覆盖已锁块。"""
    if str(source or "") in _LOCK_SOURCES or str(source or "") == "date_remove":
        return list(new_items or [])
    locked = [row for row in (old_items or []) if isinstance(row, dict) and row.get("locked")]
    if not locked:
        return list(new_items or [])
    kept = [row for row in new_items or []
            if not (isinstance(row, dict) and any(_row_overlaps(row, keep) for keep in locked))]
    kept.extend(locked)
    return sorted(kept, key=lambda row: str((row or {}).get("start") or ""))


#: 用户/对话直接改的文案原样保留；生成类来源才做「快到目的地」这类补全。
_HUMAN_TEXT_SOURCES = frozenset({
    "chat", "command", "manual", "manual_edit", "manual_edit_api", "llm_tool",
})

#: 日程块最短时长：低于它的碎片没有意义，落库前丢掉（2026-11-20）
MIN_BLOCK_MINUTES = 5

#: 这些来源是"整理/修复/用户明确编辑"，允许删掉任何块；其余来源不许悄悄吞掉预约
AUTHORITATIVE_SOURCES = frozenset({
    "manual", "manual_edit", "manual_edit_api", "command", "chat", "user", "api",
    "cleanup_backfill", "audit", "audit_fill", "audit_inject", "audit_repair",
    "audit_seed", "audit_smooth", "audit_transit", "backfill", "migrate", "date_remove",
    "normalize", "ensure_normalize", "snapshot", "synthetic", "test", "writer",
})

#: 代表"和别人约好了"的类别：重算/追加写入不许把它们悄悄删掉
APPOINTMENT_CATEGORIES = ("social", "date")

#: 这些"填充类"块要给预约块让位（切掉重叠部分）；工作/通勤等硬安排不让
GENERIC_CATEGORIES = ("rest", "life", "sleep")


def _block_identity(row):
    if not isinstance(row, dict):
        return None
    eid = str(row.get("event_id") or "")
    if eid:
        return ("id", eid)
    return ("span", str(row.get("start") or ""), str(row.get("end") or ""),
            str(row.get("activity") or ""))


def _same_activity(a, b):
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    return (str(a.get("activity") or "") == str(b.get("activity") or "")
            and str(a.get("place") or "") == str(b.get("place") or ""))


def drop_short_blocks(items, min_minutes=MIN_BLOCK_MINUTES):
    """丢掉「截断残骸」，保留真实短事件（2026-11-21 用户口径）。

    只有满足一条才丢：
    - 带 truncated_by（now/transit/date_finish 等截断标记）；
    - 与相邻块同活动同地点（整点切碎/回滚残片）。
    「到了楼下」这种 4 分钟的真实短事件必须保留。
    """
    rows = [dict(row) if isinstance(row, dict) else row for row in (items or [])]
    indexed = [(index, row) for index, row in enumerate(rows) if isinstance(row, dict)]
    short_flags = {}
    for index, row in indexed:
        begin, finish = _hm(row.get("start")), _hm(row.get("end"))
        short_flags[index] = (begin is not None and finish is not None
                              and finish > begin and finish - begin < int(min_minutes))
    kept, dropped = [], []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not short_flags.get(index):
            kept.append(row)
            continue
        if row.get("truncated_by"):
            dropped.append(row)
            continue
        neighbours = []
        if index > 0:
            neighbours.append(rows[index - 1])
        if index + 1 < len(rows):
            neighbours.append(rows[index + 1])
        if any(_same_activity(row, other) for other in neighbours):
            dropped.append(row)
            continue
        kept.append(row)   # 真实短事件
    return kept, dropped


def restore_appointments(old_items, new_items, source=""):
    """重算/追加类写入丢掉的「预约块」原样加回来，返回 (items, 加回)。

    2026-11-20：jit 整小时重算、ensure 重建、分段重排都会拿 new_items 整段替换；
    别人刚排的饭局/约会如果没打锁，就被静默吞掉。这里做第二道保险：
    只保护 category ∈ (social,date) 且没被新块占住时间的块；
    用户/整理/修复类来源不受限。
    """
    if str(source or "") in AUTHORITATIVE_SOURCES:
        return list(new_items or []), []

    def _rows_overlap(a, b):
        a1, a2 = _hm(a.get("start")), _hm(a.get("end"))
        b1, b2 = _hm(b.get("start")), _hm(b.get("end"))
        if a1 is None or a2 is None or b1 is None or b2 is None:
            return False
        return a1 < b2 and b1 < a2

    def _carve(rows, hole):
        """把 rows 里与 hole 重叠的部分切掉（通用块让位给预约块）。"""
        h1, h2 = _hm(hole.get("start")), _hm(hole.get("end"))
        out = []
        for row in rows:
            if not _rows_overlap(hole, row):
                out.append(row)
                continue
            b1, b2 = _hm(row.get("start")), _hm(row.get("end"))
            if b1 is None or b2 is None:
                out.append(row)
                continue
            if b1 < h1:
                left = dict(row)
                left["end"] = "%02d:%02d" % (h1 // 60, h1 % 60)
                out.append(left)
            if b2 > h2:
                right = dict(row)
                right["start"] = "%02d:%02d" % (h2 // 60, h2 % 60)
                out.append(right)
        return out

    current = [row for row in (new_items or []) if isinstance(row, dict)]
    seen = {_block_identity(row) for row in current}
    restored = []
    for row in old_items or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("category") or "") not in APPOINTMENT_CATEGORIES:
            continue
        ident = _block_identity(row)
        if ident in seen:
            continue
        clashes = [other for other in current if _rows_overlap(row, other)]
        hard = [other for other in clashes
                if str(other.get("category") or "") not in GENERIC_CATEGORIES]
        if hard:
            continue  # 时间被工作/通勤等硬安排占住，不硬塞
        if clashes:
            current = _carve(current, row)
        restored.append(dict(row))
        seen.add(ident)
        current.append(dict(row))
    if not restored:
        return list(new_items or []), []
    merged = [row for row in (new_items or [])] + restored
    merged.sort(key=lambda row: str((row or {}).get("start") or ""))
    return merged, restored


#: 需要"下楼/走路/坐车"才能过去的区域组合
_TRAVEL_ZONES = ("home", "office", "restaurant", "mall", "scenic", "hospital", "street")


def place_continuity_problems(items):
    """相邻块地点跳变、中间却没有路上块 → 瞬移（2026-11-21）。

    「楼下小馆」这种同楼内的走动不算：地点前缀相同或含"楼下"时放行。
    """
    rows = [dict(row) for row in (items or []) if isinstance(row, dict)]
    rows.sort(key=lambda row: str(row.get("start") or ""))

    def _zone(row):
        zone = str(row.get("zone") or "")
        if zone:
            return zone
        place = str(row.get("place") or "")
        return "home" if any(w in place for w in ("家", "卧室", "客厅", "沙发")) else ""

    def _area(place):
        """地点归属的大区域：按「·」取前缀，同一栋楼/同一个商圈算一处。"""
        text = str(place or "").strip()
        return text.split("·")[0] if "·" in text else text

    def _same_area(a_place, b_place):
        a, b = str(a_place or ""), str(b_place or "")
        if not a or not b:
            return False
        if a == b or a in b or b in a:
            return True
        return bool(_area(a)) and _area(a) == _area(b)

    problems = []
    for prev, row in zip(rows, rows[1:]):
        if str(prev.get("end") or "") != str(row.get("start") or ""):
            continue
        a_zone, b_zone = _zone(prev), _zone(row)
        if not a_zone or not b_zone or a_zone == b_zone:
            continue
        if a_zone in ("road", "transit") or b_zone in ("road", "transit"):
            continue
        if a_zone.startswith("home") and b_zone.startswith("home"):
            continue
        a_place, b_place = str(prev.get("place") or ""), str(row.get("place") or "")
        if "楼下" in b_place or "楼下" in a_place:
            continue
        if _same_area(a_place, b_place):
            continue   # 写字楼 ↔ 写字楼附近、商场 ↔ 商场·餐厅：同一区域，不用通勤块
        if a_zone not in _TRAVEL_ZONES and not a_zone.startswith("home"):
            continue
        if b_zone not in _TRAVEL_ZONES and not b_zone.startswith("home"):
            continue
        problems.append("%s %s→%s（%s→%s）" % (str(prev.get("end") or ""),
                                              a_place or a_zone, b_place or b_zone,
                                              prev.get("activity"), row.get("activity")))
    return problems


#: 重生成去抖：同一天 30 秒内被多个功能触发时只落库一次（2026-11-21）
SCHEDULE_REGEN_DEBOUNCE_SECONDS = 30

#: 用户/聊天/命令/工具/面板：这些是"人"的决定
HUMAN_SOURCES = frozenset({
    "manual", "manual_edit", "manual_edit_api", "command", "chat", "llm_tool",
    "user", "api", "panel", "panel_", "date", "social", "watch",
})

#: 自动生成：这些写入必须给"人"让路
GENERATED_SOURCES = frozenset({
    "jit", "jit_recover", "ensure", "ensure_regen", "regen", "snapshot",
    "auto", "segment", "life", "wander", "generated", "normalize", "ensure_normalize",
})

#: 手动改过日程后的静默期：这段时间内的自动重生成一律放弃
HUMAN_EDIT_GRACE_SECONDS = 60


def generation_should_yield(db, day, source, now=None):
    """自动重生成是否应当给刚发生的手动编辑让路（2026-11-21 协调层）。"""
    if str(source or "") not in GENERATED_SOURCES:
        return False
    try:
        last_human = float(db.kv_get("schedule_human_ts:" + str(day), 0) or 0)
    except Exception:
        return False
    if not last_human:
        return False
    current = now_ts() if now is None else float(now)
    return current - last_human < HUMAN_EDIT_GRACE_SECONDS


def regen_debounced(db, day, force=False, gap=SCHEDULE_REGEN_DEBOUNCE_SECONDS):
    """重生成是否应当被去抖掉；放行时顺手记时间戳。"""
    if force or int(gap) <= 0:
        return False
    key = "schedule_ensure_ts:" + str(day)
    try:
        last = float(db.kv_get(key, 0) or 0)
    except Exception:
        last = 0.0
    if last and now_ts() - last < int(gap):
        return True
    try:
        db.kv_set(key, now_ts())
    except Exception as _amo_exc:
        _note_swallowed("amo_state_schedule.regen_debounce", _amo_exc)
    return False


def freeze_past(old_items, new_items, now_text, enabled=True):
    """今天已结束的时段只读：保留旧块，丢掉新块对过去的改写。"""
    if not enabled:
        return list(new_items or [])
    cut = _hm(now_text)
    if cut is None:
        return list(new_items or [])
    past = [row for row in (old_items or [])
            if isinstance(row, dict) and (_hm(row.get("end")) or 24 * 60) <= cut]
    out = list(past)
    for row in new_items or []:
        if not isinstance(row, dict):
            continue
        start, end = _hm(row.get("start")), _hm(row.get("end"))
        if end is not None and end <= cut:
            continue
        if start is not None and start < cut < (end or 24 * 60):
            # 2026-11-21：截断必须留痕，否则碎片和真实短事件分不开
            row = dict(row, start=now_text, truncated_by="now",
                       origin_start=row.get("start"))
        out.append(row)
    return sorted(out, key=lambda row: str((row or {}).get("start") or ""))


def health_problems(items, sick=False, work_start=_WORK_START_HOUR, work_end=_WORK_END_HOUR):
    """日程体检：时间倒挂、重叠、非上班时间出现在公司。"""
    problems = []
    rows = sorted([row for row in (items or []) if isinstance(row, dict) and row.get("start")],
                  key=lambda row: str(row.get("start") or ""))
    # 白天在岗一直连到晚上的块是真实延时/夜班，不算「非上班时间出现在公司」。
    office_chains = _office_shift_starts(items, work_start, work_end)
    furthest = None
    for row in rows:
        start_text = str(row.get("start") or "")
        start, end = _hm(start_text), _hm(row.get("end"))
        if end is not None and start is not None and end <= start:
            problems.append("bad_time:%s-%s" % (start_text, row.get("end")))
        if furthest is not None and start is not None and start < furthest:
            problems.append("overlap:%s<%.2d:%.2d" % (start_text, furthest // 60, furthest % 60))
        if end is not None:
            furthest = end if furthest is None else max(furthest, end)
        place = str(row.get("place") or "")
        at_office = row.get("zone") == "office" or any(w in place for w in _OFFICE_WORDS)
        if at_office and start is not None:
            hour = start // 60
            late_shift = (hour >= work_end and _office_shift_continues(
                office_chains.get(id(row)), work_start, work_end))
            if sick or hour < work_start or (hour >= work_end and not late_shift):
                problems.append("office_out_of_window:%s" % start_text)
    return problems


class ScheduleStateMixin(_MixinBase):
    #: 兄弟 mixin / 宿主提供的成员：只做类型声明，运行时由 HumanState 创建
    schedule_ctx: Any
    schedule_context: Any
    _schedule_gen_locks: Any
    on_sick_leave: Any
    """日程域：主干生成、待办/已定行程、展示优先级、日程读写与持久化。"""
    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        _booked_tree: Any
        _commit_todo_day: Any
        _date_approved: Any
        _jit_home: Any
        _jit_items: Any
        _jit_schedule_tree: Any
        _schedule_apply_travel: Any
        _schedule_commit_lock: Any
        _trip_day_items: Any
        current_activity: Any
        decide_sick_action: Any
        engine: Any
        ensure_worldview: Any
        health_info: Any
        mood: Any
        nudge: Any
        period_info: Any
        refresh_weather: Any
        roll_health: Any
        settle_date_end: Any
        sick_decision: Any

    #: 由 HumanState 注入的插件实例 / JitStateMixin 提供的开关（仅类型声明）
    p: Any
    _jit_enabled: Any

    # ---------- 日程（主干生成 + 动态展开） ----------

    @staticmethod
    def _hourly_schedule(items):
        """[deprecated 兼容层] 把任意日程整理成 24 条整点日程；最后一条固定 23:00-24:00。

        第 1 阶段重构后，日记/详情/调度已经统一读 amo_timeline.Timeline；
        这里只保留给 changed_hours / _brief_schedule / 非 JIT schedule_tree 与
        block_diary_enabled=false 的旧整点回滚路径，不再作为块身份的来源。
        """
        # start 必须是 HH:MM：脏数据（比如「坏数据」）以前会被 parse_hhmm 当成 00:00，
        # 于是午夜凭空多出一段活动，24 小时骨架也不对了。
        def _good_start(item):
            if not isinstance(item, dict):
                return False
            text = str(item.get("start") or "").strip()
            if not re.match(r"^\d{1,2}:\d{2}$", text):
                return False
            return 0 <= parse_hhmm(text) < 24 * 60

        valid = [item for item in (items or []) if _good_start(item)]
        if not valid:
            # 没有日程就是没有：不能凭空造 24 小时「休息/睡觉」，否则会生成假小日记。
            return []
        valid.sort(key=lambda item: parse_hhmm(item.get("start")))
        result = []
        for hour in range(24):
            start_min = hour * 60
            found = None
            found_overlap = 0
            found_begin = -1
            for item in valid:
                begin = parse_hhmm(item.get("start"))
                finish = parse_hhmm(item.get("end")) or (begin + 60)
                # 一小时内被切成两段时，取占时更长的那段；并列取更晚开始的那段
                # （例如 13:30 改日程：13:00-13:30 旧、13:30-14:00 新 -> 13 点日记算新）。
                overlap = min(finish, start_min + 60) - max(begin, start_min)
                if overlap <= 0:
                    continue
                if overlap > found_overlap or (overlap == found_overlap and begin >= found_begin):
                    found = item
                    found_overlap = overlap
                    found_begin = begin
            entry: dict[str, Any] = {
                "start": "%02d:00" % hour,
                "end": "24:00" if hour == 23 else "%02d:00" % (hour + 1),
                "activity": "睡觉" if hour < 7 or hour >= 23 else "休息",
                "place": "家",
                "category": "sleep" if hour < 7 or hour >= 23 else "rest",
                "subject": "bot",
            }
            if found:
                entry.update({
                    "activity": found.get("activity", "休息"),
                    "place": found.get("place", "家"),
                    "category": found.get("category", "rest"),
                })
                for key in ("zone", "transit", "vehicle", "detail", "spot",
                            "event", "mood", "mood_valence", "swim"):
                    if found.get(key) not in (None, "", 0):
                        entry[key] = found.get(key)
                if isinstance(found.get("effects"), dict):
                    entry["effects"] = found.get("effects")
                if found.get("soothe"):
                    entry["soothe"] = True
            result.append(entry)
        return result

    @classmethod
    def changed_hours(cls, old_items, new_items):
        """比较两版日程在 24 个整点槽上的差异，返回变化的小时列表。"""
        try:
            old = cls._hourly_schedule(old_items)
            new = cls._hourly_schedule(new_items)
        except Exception:
            return []
        if not old and not new:
            return []
        changed = []
        fields = ("activity", "place", "category", "zone", "detail", "event")
        for hour in range(24):
            a = old[hour] if hour < len(old) else {}
            b = new[hour] if hour < len(new) else {}
            if any(str(a.get(key) or "") != str(b.get(key) or "") for key in fields):
                changed.append(hour)
        return changed

    @classmethod
    def _brief_schedule(cls, items, hours, limit=3):
        """日志用：只打变化小时的活动/地点/分类，避免整份日程刷屏。"""
        hourly = cls._hourly_schedule(items)
        by_hour = {}
        for item in hourly:
            try:
                by_hour[int(str(item.get("start") or "0")[:2])] = item
            except Exception:
                continue
        picked = []
        for hour in (hours or [])[:limit]:
            item = by_hour.get(int(hour)) or {}
            picked.append("%02d:00|%s|%s|%s" % (
                int(hour), item.get("activity") or "-",
                item.get("place") or "-", item.get("category") or "-",
            ))
        if hours and len(hours) > limit:
            picked.append("...+%d" % (len(hours) - limit))
        return ",".join(picked) or "-"

    def _schedule_current_items(self, day):
        """当天真实块：内存缓存优先，schedule 表兜底（网关读侧）。"""
        try:
            rows = (self.schedule or {}).get(day)
        except Exception:
            rows = None
        if rows:
            return [dict(row) if isinstance(row, dict) else row for row in rows]
        try:
            return list(self.db.get_schedule(day) or [])
        except Exception:
            return []

    def apply_schedule(self, day, mutator, *, source="", reason="", invalidate_from=None):
        """日程写入网关（唯一「读当前 + 改 + 提交」入口）。

        锁内读当天当前块 → mutator(current) 返回新列表 → 交回唯一提交入口
        commit_schedule_change（体检/审计/落库都在它里面）。mutator 返回 None
        表示这次不写（例如追加块与已有块重叠），返回里带 skipped。

        为什么要有这一层：以前调用方各自「读一遍当前 + 改 + 提交」，读和写之间
        没有同一把锁，改的还是上一次的快照；收敛到网关后读、改、提交在同一把
        RLock 里串行，旁路（直接 save_day）也没了理由。
        """
        day = str(day or "")
        empty = {"changed": False, "version": 0, "changed_hours": [],
                 "cancelled": [], "saved": False}
        if not day or not callable(mutator):
            empty["skipped"] = "bad_args"
            return empty
        with self._schedule_commit_lock:
            current = self._schedule_current_items(day)
            new_items = mutator(list(current))
            if new_items is None:
                return self._skipped_add_result(day, "mutator")
            return self.commit_schedule_change(
                day, list(new_items), old_items=current, source=source,
                reason=reason, invalidate_from=invalidate_from)

    def commit_schedule_change(self, day, new_items, old_items=None, source="",
                               reason="", invalidate_from=None):
        """所有日程写入口的唯一落库点；外层锁保证同一天的读改写串行。

        未来日期不再铺日程（防剧透）：只把“已经定下来的事”写进待办表，
        到了那天由 JIT 按待办锚点落实成真实日程。

        2026-11-21 协调层：自动重生成在「刚有人手动改过」的 60 秒内直接让行，
        避免一个慢速重生成把用户刚改的日程覆盖掉。
        """
        day_text = str(day or "")
        _src = str(source or "")
        if day_text == today_str() and generation_should_yield(self.db, day_text, _src):
            logger.info("[amo] 生成写入让行：%d 秒内有人手动改过日程（source=%s）",
                        HUMAN_EDIT_GRACE_SECONDS, _src)
            try:
                _ver = self.db.schedule_version(day_text)
            except Exception:
                _ver = 0
            return {"changed": False, "stale": True, "version": _ver,
                    "changed_hours": [], "cancelled": [], "saved": False}
        # 旅游目的地锁（10B1 单点）：出发后用户改城市/叫回，进入落库前就拦下，
        # 日程/城市/状态零改动，只回一句「票都买好了」的 context。
        if str(source or "") in self._TRIP_GUARD_SOURCES:
            _guard = self._trip_change_guard(reason)
            if _guard.get("blocked"):
                logger.info("[trip] destination_guard blocked source=%s day=%s action=%s",
                            str(source or ""), day_text, _guard.get("action"))
                try:
                    _version = self.db.schedule_version(day_text)
                except Exception as _amo_exc:
                    _note_swallowed("amo_state_schedule.commit.trip_guard_version", _amo_exc)
                    _version = 0
                return {"changed": False, "version": _version, "changed_hours": [],
                        "cancelled": [], "saved": True,
                        "blocked": _guard.get("action"),
                        "context": _guard.get("context") or ""}
        if day_text and day_text > today_str():
            # 10B3：跨午夜长途的第二程是「今晚已经出发」的事实，不是剧透；
            # 只有 trip_leg 允许落到第二天的时间轴，其它未来日期照旧只进待办。
            if str(source or "") not in self._FUTURE_OK_SOURCES:
                return self._commit_todo_day(day_text, new_items, source=source)
        # 防御：空 end / end<=start 的行绝不落库（过渡到达块曾以 end="" 写进去，
        # 时间轴 bad_time、JIT 与块日记都按坏区间算）。丢弃并留日志。
        kept_items = []
        for row in new_items or []:
            if not isinstance(row, dict):
                kept_items.append(row)
                continue
            start_text = str(row.get("start") or "").strip()
            end_text = str(row.get("end") or "").strip()
            try:
                span_ok = (bool(start_text and end_text)
                           and parse_hhmm(end_text) > parse_hhmm(start_text))
            except Exception:
                span_ok = False
            if span_ok:
                kept_items.append(row)
            else:
                logger.warning(
                    "[amo] 丢弃空/倒置时段的日程行 day=%s start=%s end=%s activity=%s",
                    day_text, start_text, end_text, str(row.get("activity") or ""))
        new_items = kept_items
        try:  # 社交块把同席 NPC 写进 people，日记/照片不必再从文字猜
            _npc = self.db.kv_get("npc_summary", {}) or {}
            for _row in new_items:
                if not isinstance(_row, dict):
                    continue
                _names = people_from_text(str(_row.get("activity") or "") + str(_row.get("detail") or ""), _npc)
                if _names:
                    _row["people"] = _names
                    _row["companions"] = _names
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.commit.people", _amo_exc)
        # 第四轮防穿帮：聊天/命令/工具把地点改成户外目的地时，先开过渡（30 分钟路程），
        # 不再瞬间传送（老 bug：被子里 → 医院走廊）。
        try:
            try:
                from .amo_transit import maybe_start as _maybe_transit
            except ImportError:
                from amo_transit import maybe_start as _maybe_transit
            _maybe_transit(self.p, day_text, new_items, old_items, source)
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.commit.transit", _amo_exc)
        # 第七轮：落库前去重叠（旧块优先，新块截断到空档或丢弃）
        try:
            try:
                from .amo_transition import resolve_overlaps as _resolve_overlaps
            except ImportError:
                from amo_transition import resolve_overlaps as _resolve_overlaps
            # 只拿“不在新列表里的旧块”当 blocker：ensure/JIT 重写是整份替换，
            # 旧块和新块其实是同一批，如果不过滤会把所有块都当成冲突截掉（14:1x 的事故）。
            def _ident(row):
                return (str(row.get("start") or ""), str(row.get("end") or ""),
                        str(row.get("activity") or ""))

            _new_ids = {_ident(x) for x in (new_items or []) if isinstance(x, dict)}
            _base = [x for x in (old_items or [])
                     if isinstance(x, dict) and _ident(x) not in _new_ids]
            new_items, _trimmed = _resolve_overlaps(_base, new_items)
            if _trimmed:
                logger.info("[amo] 日程去重叠：截断/丢弃 %d 条新块（旧块优先）", len(_trimmed))
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.commit.overlap", _amo_exc)
        # 第十轮 B1：外部 blocker 过滤掉「同批旧块」之后，new_items 自己还会重叠
        # （原样旧块 + 一条重叠追加），这里再按分钟压直一遍，作为第二道闸。
        try:
            try:
                from .amo_transition import resolve_new_overlaps as _resolve_new
            except ImportError:
                from amo_transition import resolve_new_overlaps as _resolve_new
            new_items, _self_trimmed = _resolve_new(new_items)
            if _self_trimmed:
                logger.info("[amo] 日程去重叠：新增块自身重叠，截断/丢弃 %d 条",
                            len(_self_trimmed))
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.commit.self_overlap", _amo_exc)
        # 第十轮 B1：跨地点切换补一段独立通勤块，别再 5 分钟瞬移回家。
        # 2026-11-21：保持白名单——放宽到所有来源会把 finance 的 10 分钟覆盖块
        # 顺延走（test_finance_overlay 立刻红），地点连续性交给守卫+修复脚本。
        if day_text == today_str() and str(source or "") in self._TRANSIT_SOURCES:
            try:
                try:
                    from .amo_transition import insert_transit_blocks as _insert_transit
                except ImportError:
                    from amo_transition import insert_transit_blocks as _insert_transit
                _old_for_transit = old_items if old_items is not None else (
                    self.schedule.get(day_text) or self.db.get_schedule(day_text) or [])
                new_items, _transit_rows = _insert_transit(
                    day_text, new_items, old_items=_old_for_transit)
                if _transit_rows:
                    logger.info("[amo] 跨地点切换补通勤块 %d 条（day=%s source=%s）",
                                len(_transit_rows), day_text, source or "-")
                    self._mark_transit_action(_transit_rows)
            except Exception as _amo_exc:
                _note_swallowed("amo_state_schedule.commit.transit_blocks", _amo_exc)
        # T1：落库前统一收口路上块的 activity/vehicle/transit（锁外纯内存计算）。
        new_items = self._sync_transit_items(new_items)
        with self._schedule_commit_lock:
            result = self._commit_schedule_change_locked(
                day, new_items, old_items=old_items, source=source,
                reason=reason, invalidate_from=invalidate_from,
            )
        if _src in HUMAN_SOURCES:
            # 记下"人刚改过"的时间，自动重生成在静默期内让行（2026-11-21）
            try:
                self.db.kv_set("schedule_human_ts:" + day_text, now_ts())
            except Exception as _amo_exc:
                _note_swallowed("amo_state_schedule.human_ts", _amo_exc)
        return result

    def _sync_transit_items(self, items):
        """T1：落库前统一收口路上块的 activity/vehicle/transit；失败返回原列表。"""
        try:
            try:
                from .schedule_slots import ScheduleSlotsMixin as _slots
            except ImportError:
                from schedule_slots import ScheduleSlotsMixin as _slots
            return _slots._sync_transit_vehicle(items)
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.commit.transit_sync", _amo_exc)
            return items

    def _trip_change_guard(self, text):
        """旅游目的地锁：出发后改城市 → 拦下并带回她该说的那句话；叫回 → 切 returning。"""
        try:
            try:
                from . import amo_trip as _trip
            except ImportError:
                import amo_trip as _trip
            plugin = getattr(self, "p", None) or self
            result = _trip.guard_change(plugin, text)
            action = str(result.get("action") or "")
            if action == "blocked":
                return {"blocked": True, "action": action,
                        "context": str(result.get("reply") or "")}
            if action == "return" and _trip.is_return_command(plugin, text):
                returned = _trip.early_return(plugin)
                return {"blocked": True, "action": "return",
                        "context": str((returned or {}).get("line") or "")}
            return {}
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule._trip_change_guard", _amo_exc)
            return {}

    def _mark_transit_action(self, rows):
        """通勤块覆盖当前分钟时，把 current_action 也切到「在路上」，聊天不再说目的地。

        2026-11-22 task-25：请假/出差硬约束下「road」不是允许 zone，绝不把
        current_action 切成通勤（否则病假在家聊天却说在地铁上）。
        """
        try:
            try:
                from .amo_scene_ledger import constraints as _constraints
            except ImportError:
                from amo_scene_ledger import constraints as _constraints
            _allowed = (_constraints(self, today_str()) or {}).get("allowed_zones")
            if _allowed is not None and "road" not in _allowed:
                logger.info("[amo] 硬约束下不切通勤 current_action（allowed_zones=%s）",
                            sorted(_allowed))
                return
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.mark_transit.constraint", _amo_exc)
        now_min = minutes_now()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            try:
                begin = parse_hhmm(row.get("start"))
                finish = parse_hhmm(row.get("end"))
            except Exception as _amo_exc:
                _note_swallowed("amo_state_schedule._mark_transit_action.parse", _amo_exc)
                continue
            if not (begin <= now_min < finish):
                continue
            try:
                stamp = now_ts()
                self.db.kv_set("current_action", {
                    "action": row.get("activity"), "place": row.get("place") or "路上",
                    "zone": "road", "category": "commute",
                    "at": stamp, "until": stamp + max(60, (finish - now_min) * 60),
                    "hour": begin // 60, "source": "transit_block",
                })
            except Exception as _amo_exc:
                _note_swallowed("amo_state_schedule._mark_transit_action.write", _amo_exc)
            return

    def _repair_transit_blocks(self, day):
        """读/加载时修复历史「跨地点瞬移」：按日程版本只修一次，不每次读都写库。"""
        day = str(day or today_str())
        if day != today_str():
            return []
        try:
            version = str(self.db.schedule_version(day))
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.transit_repair.version", _amo_exc)
            version = "0"
        flag = "transit_repair:" + day
        try:
            if str(self.db.kv_get(flag, "")) == version:
                return []
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.transit_repair.flag", _amo_exc)
        try:
            rows = list(self.schedule.get(day) or self.db.get_schedule(day) or [])
        except Exception:
            rows = []
        try:
            try:
                from .amo_transition import repair_cross_place_blocks as _repair
                from .amo_transition import _has_blocking_overlap as _overlaps
            except ImportError:
                from amo_transition import repair_cross_place_blocks as _repair
                from amo_transition import _has_blocking_overlap as _overlaps
            repaired, inserted = _repair(getattr(self, "p", None) or self, day, rows)
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.transit_repair.compute", _amo_exc)
            return []
        if not inserted:
            try:
                self.db.kv_set(flag, version)
            except Exception as _amo_exc:
                _note_swallowed("amo_state_schedule.transit_repair.mark", _amo_exc)
            return []
        if _overlaps(repaired):
            logger.error("[amo] 通勤块修复后仍有重叠，放弃写入 day=%s", day)
            return []
        try:
            result = self.commit_schedule_change(
                day, repaired, old_items=rows, source="transit_repair",
                reason="修复跨地点瞬移")
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.transit_repair.commit", _amo_exc)
            return []
        try:
            new_version = str((result or {}).get("version") or version)
            self.db.kv_set(flag, new_version)
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.transit_repair.mark_after", _amo_exc)
        try:
            self._mark_transit_action(inserted)
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.transit_repair.mark_action", _amo_exc)
        logger.info("[amo] 修复历史跨地点瞬移：补通勤块 %d 条（day=%s）", len(inserted), day)
        return inserted

    def reconcile_health_schedule(self, day=None, now=None):
        """状态一致性巡检：健康已恢复但日程还挂着病假 → 物理删除那几条。"""
        try:
            from .amo_schedule_health import reconcile_stale_health_schedule
        except ImportError:
            from amo_schedule_health import reconcile_stale_health_schedule
        day = str(day or today_str())
        with self._schedule_commit_lock:
            current = list(self.schedule.get(day) or self.db.get_schedule(day) or [])
            if not current:
                return 0
            kept, removed = reconcile_stale_health_schedule(
                current, self.health_info(), now)
            if not removed:
                return 0
            # 第八轮 C：删完病假行也先归一一次，别把重叠写回库（旧代码这里直接 save）
            try:
                try:
                    from .amo_transition import normalize_day as _normalize_day
                except ImportError:
                    from amo_transition import normalize_day as _normalize_day
                kept, _fixed = _normalize_day(kept)
                if _fixed:
                    logger.info("[amo] 健康巡检写入前归一了重叠块")
            except Exception as _amo_exc:
                _note_swallowed("amo_state_schedule.reconcile_health.normalize", _amo_exc)
            result = self._commit_schedule_change_locked(
                day, kept, old_items=current, source="health_audit",
                reason="健康已恢复，清理过期病假日程",
            )
            if isinstance(result, dict) and result.get("changed"):
                logger.info("[amo] 状态巡检：健康已恢复，清理过期病假日程 %d 条", removed)
                return removed
            # 未变更（或体检拒绝）时不再手动 save_day：save_day 只留给锁内提交核心。
            logger.info("[amo] 状态巡检：健康已恢复，日程无需重写（%d 条过期病假）", removed)
            return removed

    def save_day(self, day, items):
        """日程唯一落库入口：schedule 表 + kv['schedule'] 必须同一次写完。

        2026-11-17 事故：amo_runtime_tick 只调 db.save_schedule（只写表），
        这里的两处是「表 + kv」两份都写，两份存储于是漂移（表 26 项含 06:12
        finance，kv 25 项）。清理脚本读 kv 就永远看不到那块，面板读表又能看到。
        以后写日程一律走这里，别再直接 db.save_schedule。
        """
        day = str(day or "")
        if not day:
            return False
        rows = [item for item in (items or []) if isinstance(item, dict)]
        if not isinstance(getattr(self, "schedule", None), dict):
            self.schedule = {}
        self.schedule[day] = rows
        # 表是唯一权威：先写表，再按「表里读回来的一版」写 kv 镜像。
        # kv_schedule 从此只是只读镜像（读侧见 HumanState.__init__：先读表，
        # 表为空才回落老 kv 库），直接改 kv 不再影响读出来的块。
        self.db.save_schedule(day, rows)
        try:
            authoritative = self.db.get_schedule(day)
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.save_day.readback", _amo_exc)
            authoritative = None
        if isinstance(authoritative, list):
            self.schedule[day] = [dict(row) if isinstance(row, dict) else row
                                  for row in authoritative]
        mirror = self._schedule_mirror_from_table()
        self.db.kv_set("schedule", mirror if mirror is not None else self.schedule)
        return True

    def _schedule_mirror_from_table(self):
        """从 schedule 表重建 kv schedule 镜像；没有全量读接口时返回 None。"""
        loader = getattr(self.db, "all_schedules", None)
        if not callable(loader):
            return None
        try:
            table = loader()
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.mirror.load", _amo_exc)
            return None
        return table if isinstance(table, dict) else None

    def _audit_schedule(self, day, source, reason, version, changed, old_items, new_items,
                        rejected=None, extra=None):
        """日程变更审计：谁改的、改前改后、为什么，写 kv schedule_audit:<day>（最多 200 条）。

        rejected 非空 = 体检拒绝写入：changed_hours 固定为空，before/after 仍是本次尝试，
        面板 /schedule/audit 能直接看到「哪条规则拒了谁」。
        """
        key = "schedule_audit:%s" % str(day)
        try:
            rows = self.db.kv_get(key, []) or []
        except Exception:
            rows = []

        def brief(items):
            return [{"start": row.get("start"), "end": row.get("end"),
                     "activity": row.get("activity"), "place": row.get("place"),
                     "zone": row.get("zone"), "source": row.get("source")}
                    for row in (items or []) if isinstance(row, dict)]

        entry = {"ts": now_ts(), "source": str(source or ""), "reason": str(reason or ""),
                 "version": version, "changed_hours": list(changed or []),
                 "before": brief(old_items), "after": brief(new_items)}
        if rejected:
            entry["rejected"] = [str(item) for item in rejected]
        if extra:
            entry.update({str(k): v for k, v in dict(extra).items()})
        rows.append(entry)
        self.db.kv_set(key, rows[-200:])

    def patch_schedule(self, day, upserts=None, deletes=None, source="", reason=""):
        """块级补丁：按 event_id（缺省用 start+activity）改/删，不再整段替换。

        2026-11-20：58 个写者都在构造 new_items 整段覆盖，谁漏带谁的块就丢；
        新代码请优先用这个接口，只动自己要动的那一两块。
        """
        day = str(day or "")
        if not day:
            return {"changed": False, "version": 0, "patched": 0, "deleted": 0}
        stats = {"patched": 0, "deleted": 0}

        def _mutate(current):
            rows = [dict(row) for row in (current or []) if isinstance(row, dict)]
            for target in deletes or []:
                if not isinstance(target, dict):
                    continue
                ident = _block_identity(target)
                before = len(rows)
                if ident[0] == "id":
                    rows = [row for row in rows
                            if str(row.get("event_id") or "") != ident[1]]
                else:
                    key = (str(target.get("start") or ""), str(target.get("activity") or ""))
                    rows = [row for row in rows
                            if (str(row.get("start") or ""),
                                str(row.get("activity") or "")) != key]
                stats["deleted"] += before - len(rows)
            for patch in upserts or []:
                if not isinstance(patch, dict):
                    continue
                ident = _block_identity(patch)
                hit = None
                for row in rows:
                    same_id = (ident[0] == "id"
                               and str(row.get("event_id") or "") == ident[1])
                    same_span = (ident[0] == "span"
                                 and (str(row.get("start") or ""), str(row.get("end") or ""),
                                      str(row.get("activity") or "")) == ident[1:])
                    if same_id or same_span:
                        hit = row
                        break
                if hit is not None:
                    hit.update(patch)
                else:
                    rows.append(dict(patch))
                stats["patched"] += 1
            rows.sort(key=lambda row: str((row or {}).get("start") or ""))
            return rows

        try:
            result = self.apply_schedule(day, _mutate, source=source or "patch",
                                         reason=reason or "块级补丁")
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.patch", _amo_exc)
            return {"changed": False, "version": 0, **stats}
        if isinstance(result, dict):
            return {**result, **stats}
        return {"changed": bool(result), "version": 0, **stats}

    def add_schedule_item(self, day, item, source="finance", reason=""):
        """在当天日程里追加一条（锁内读改写）；整天覆盖仍走 commit_schedule_change。"""
        day = str(day or "")
        empty = {"changed": False, "version": 0, "changed_hours": [], "cancelled": []}
        if not day or not isinstance(item, dict):
            return empty
        start = str(item.get("start") or "").strip()
        if not re.match(r"^\d{1,2}:\d{2}$", start):
            return empty
        try:
            from .amo_diary_guard import clean_schedule_text
            item = dict(item)
            for key in ("activity", "detail"):
                if item.get(key):
                    item[key] = clean_schedule_text(item[key]) or item[key]
        except Exception as exc:
            _note_swallowed("amo_human_state.py.1096", exc)

        def _minute(text):
            try:
                hh, mm = str(text).split(":", 1)
                return int(hh) * 60 + int(mm)
            except Exception:
                return 24 * 60

        def _append(current):
            merged = list(current) + [dict(item)]
            merged.sort(key=lambda row: _minute(str((row or {}).get("start") or "")))
            # 2026-11-17 06:12 事故：以前这里直接落库，绕过了第二道闸
            # （resolve_new_overlaps）；在 sleep 06:00-07:00 上追加 finance
            # 06:12-06:22，重叠会原样写进 schedule 表，面板/详情都能看到。
            merged, added = self._resolve_added_item_overlaps(day, current, merged, item)
            return merged if added is not None else None

        result = self.apply_schedule(day, _append, source=source, reason=reason)
        if isinstance(result, dict) and result.get("skipped"):
            return self._skipped_add_result(day, "overlap")
        return result

    def _skipped_add_result(self, day, skipped):
        """追加被去重叠拦下时的返回（形状与 _commit_schedule_change_locked 对齐）。"""
        try:
            version = self.db.schedule_version(str(day or ""))
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.add_skipped.version", _amo_exc)
            version = 0
        return {"changed": False, "version": version, "changed_hours": [],
                "cancelled": [], "saved": True, "skipped": str(skipped or "overlap")}

    def _resolve_added_item_overlaps(self, day, current, merged, item):
        """追加块也走 commit_schedule_change 的第二道闸 resolve_new_overlaps。

        返回 (可落库的 items, 追加块)。追加块被完全覆盖、或去重叠之后仍与别的块
        重叠（含去重叠模块不可用）时返回 (merged, None)：宁可整条不写，也不把
        重叠静默写进库（2026-11-17 06:12 finance 事故）。
        """
        try:
            try:
                from .amo_transition import resolve_new_overlaps as _resolve_new
            except ImportError:
                from amo_transition import resolve_new_overlaps as _resolve_new
            resolved, trimmed = _resolve_new(merged)
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.add_overlap.resolve", _amo_exc)
            logger.warning("[amo] 追加日程去重叠不可用，拒绝写入 day=%s", day)
            return merged, None
        activity = str((item or {}).get("activity") or "")
        end_text = str((item or {}).get("end") or "")
        start_text = str((item or {}).get("start") or "")
        current_ids = {(str(row.get("start") or ""), str(row.get("end") or ""),
                        str(row.get("activity") or ""))
                       for row in (current or []) if isinstance(row, dict)}
        added = None
        for row in resolved or []:
            if not isinstance(row, dict):
                continue
            if (str(row.get("activity") or "") != activity
                    or str(row.get("end") or "") != end_text):
                continue
            identity = (str(row.get("start") or ""), str(row.get("end") or ""), activity)
            if str(row.get("start") or "") == start_text or identity not in current_ids:
                added = row
                break
        if added is None:
            logger.info("[amo] 追加日程与已有块重叠，整条丢弃 day=%s %s-%s %s",
                        day, start_text, end_text, activity)
            return merged, None
        others = [row for row in resolved if row is not added]
        if _item_overlaps_any(added, others):
            logger.warning("[amo] 追加日程去重叠后仍重叠，拒绝写入 day=%s %s-%s %s",
                           day, added.get("start"), added.get("end"), activity)
            return merged, None
        if trimmed:
            logger.info("[amo] 追加日程去重叠：截断/丢弃 %d 条", len(trimmed))
        if str(added.get("start") or "") != start_text:
            logger.info("[amo] 追加日程起点挪到 %s（原 %s）day=%s %s",
                        added.get("start"), start_text, day, activity)
        return resolved, added

    #: 只有「用户改日程」的写入才过旅游目的地锁；JIT/生成来源不受影响。
    _TRIP_GUARD_SOURCES = ("chat", "command", "manual", "llm_tool")
    #: 跨午夜长途第二程（trip_leg）允许写第二天日程：已经出发的事实，不是剧透。
    _FUTURE_OK_SOURCES = frozenset({"trip_leg"})
    #: 只有这些来源的当天写入才自动补跨地点通勤块（测试/过渡引擎自己写步骤的不参与）。
    _TRANSIT_SOURCES = frozenset({
        "jit", "ensure", "ensure_regen", "chat", "command", "manual", "llm_tool",
        "self_offwork", "date", "social", "wander", "activity",
        "travel_overlay", "travel_follow_start", "travel_return",
    })

    #: 只有这些来源生成的活动做主语校验；用户明确改的日程（chat/command/manual）按他说的来。
    ACTIVITY_GUARD_SOURCES = (
        "jit", "ensure", "ensure_regen", "social", "auto", "life", "wander", "generated",
    )

    def _guard_activity_subjects(self, items, source, day=""):
        """生成来源的日程：没约会、且居住关系不允许时，把「和老公」改写成「和闺蜜」。

        2026-11-20：按**块自己的日期/时间**判断在场，不再用「现在」。
        同居/半同居窗口内放行「和老公」；分居档行为与 7.5.9 一致。
        """
        if str(source or "") not in self.ACTIVITY_GUARD_SOURCES:
            return items
        approved = self._date_approved()
        out = []
        for item in items:
            if not isinstance(item, dict):
                out.append(item)
                continue
            row = dict(item)
            # 2026-11-21：约会/社交块的主语由约会会话负责，生成侧不许把「和老公」改成「和闺蜜」
            if (str(row.get("category") or "") in ("social", "date")
                    or row.get("locked") or row.get("appointment")
                    or str(row.get("source") or "") in ("date", "watch", "social")):
                out.append(row)
                continue
            allow = bool(approved)
            if not allow and day:
                try:
                    try:
                        from . import amo_household as _hh
                    except ImportError:
                        import amo_household as _hh
                    scene = _hh.scene_at(
                        self.p, day,
                        row.get("start"), row.get("end"),
                        row.get("place") or "", zone=row.get("zone") or "",
                        activity=row.get("activity") or "",
                    )
                    allow = bool(scene.partner)
                except Exception:
                    allow = False
            result = _validate_activity(row.get("activity"), approved=allow)
            if not result.get("ok"):
                logger.info(
                    "[amo] 日程主语校验：%r -> %r（%s，source=%s）",
                    row.get("activity"), result.get("activity"), result.get("reason"), source,
                )
                row["activity"] = result.get("activity") or row.get("activity")
            out.append(row)
        try:
            try:
                from . import amo_person_ref as _pr
            except ImportError:
                import amo_person_ref as _pr
            out = _pr.normalize_schedule(out, enabled=_pr.enabled(self.p))
        except Exception:  # cleanup: 指代归一失败不拦日程写入
            pass
        return out

    def _constrain_scene_items(self, day, items):
        """场景硬约束钳制（2026-11-22 task-25）：病假/事假→家或医院，出差→出差地 zone。

        日程/过渡块/块日记/外部插件（watch 的 add_schedule）全都从
        commit_schedule_change 这一个落库点进来，所以在这里统一按场景账本钳制：
        非法地点块改成允许场景（请假在家休息 / 酒店休息），不再让「病假在家却排了
        公司/通勤」写进 schedule —— schedule 干净了，块日记/大日记才不会被污染。
        返回 (items, clamped)；无硬约束时原样返回，零额外开销。
        """
        try:
            try:
                from .amo_scene_ledger import constrain_activity as _constrain
                from .amo_scene_ledger import constraints as _constraints
                from .amo_scene_ledger import write as _scene_write
            except ImportError:
                from amo_scene_ledger import constrain_activity as _constrain
                from amo_scene_ledger import constraints as _constraints
                from amo_scene_ledger import write as _scene_write
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.scene_constraint.import", _amo_exc)
            return items, []
        try:
            cons = _constraints(self, str(day or "")) or {}
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.scene_constraint.read", _amo_exc)
            return items, []
        kind = str(cons.get("kind") or "")
        if not kind:
            return items, []
        kept = []
        clamped = []
        for row in items or []:
            if not isinstance(row, dict):
                kept.append(row)
                continue
            try:
                fixed = _constrain(self, row, str(day or ""))
            except Exception as _amo_exc:
                _note_swallowed("amo_state_schedule.scene_constraint.row", _amo_exc)
                fixed = row
            if not isinstance(fixed, dict) or fixed == row:
                kept.append(row)
                continue
            out = dict(row)
            for key in ("activity", "place", "zone", "category"):
                if fixed.get(key):
                    out[key] = fixed.get(key)
            # 场景被钳掉 = 原来那件事没发生：扣费元数据一起清掉（否则 settle 会按旧 trip/逛街块扣钱）。
            out.pop("cost", None)
            out["scene_constraint"] = kind
            kept.append(out)
            clamped.append({"start": row.get("start"), "from": row.get("place"),
                            "to": out.get("place"), "kind": kind})
            try:
                _scene_write(self, out, source="schedule_constraint",
                             reason="病假/事假/出差硬约束钳制")
            except Exception as _amo_exc:
                _note_swallowed("amo_state_schedule.scene_constraint.audit", _amo_exc)
        if clamped:
            logger.info("[amo] 场景硬约束：改写 %d 条日程行（%s，day=%s）",
                        len(clamped), kind, day)
        return kept, clamped

    def _commit_schedule_change_locked(self, day, new_items, old_items=None, source="",
                                       reason="", invalidate_from=None):
        """commit_schedule_change 的锁内实现。"""
        day = str(day)
        if old_items is None:
            old_items = list(self.schedule.get(day) or [])
        old_items = list(old_items or [])
        new_items = list(new_items or [])
        # 四道收口（2026-11-18）：过去只读 → 锁块不被覆盖 → 打锁 → 归一 → 体检（不合格不落库）。
        try:
            import time as _time
            _stamp = _time.localtime(now_ts())
            now_text, today = _time.strftime("%H:%M", _stamp), _time.strftime("%Y-%m-%d", _stamp)
        except Exception:
            now_text, today = "", ""
        _restored, _short = [], []
        try:
            new_items = protect_locked(old_items, new_items, source)
            # 只有 transit 会走到这里（2026-11-21 核对）：跨过 now 的交通块把起点挪到
            # 现在并打 truncated_by 标记；其它来源不截断，避免"约定"被改写。
            new_items = freeze_past(old_items, new_items, now_text,
                                    enabled=(str(source or "") == "transit" and day == today))
            # 2026-11-20：整段重算会悄悄吞掉别人刚排的预约块；这里加回来（用户/整理类来源不受限）
            new_items, _restored = restore_appointments(old_items, new_items, source)
            # 碎片块（<5 分钟）没有信息量，落库前丢掉
            new_items, _short = drop_short_blocks(new_items)
            if _restored:
                logger.info("[amo] 预约块保护：加回 %d 条被覆盖的社交/约会块（source=%s day=%s）",
                            len(_restored), source or "-", day)
            if _short:
                logger.info("[amo] 丢弃 %d 条 <5 分钟碎片（source=%s day=%s）",
                            len(_short), source or "-", day)
            new_items = mark_locked(new_items, source)
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.guard.pre", _amo_exc)
        new_items = self._normalize_schedule_items(new_items, source, day)
        # 2026-11-22 task-25：落库前统一硬约束——病假/事假/出差期间非法地点块在这里改写，
        # 块日记/大日记只从这里往下读，不再出现「病假在家却写公司/通勤」。
        try:
            new_items, _scene_clamped = self._constrain_scene_items(day, new_items)
            if _scene_clamped:
                self._audit_schedule(day, source, "场景硬约束钳制", 0, [],
                                     old_items, new_items, extra={"scene_clamped": _scene_clamped})
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.scene_constraint.apply", _amo_exc)
        try:
            _jumps = place_continuity_problems(new_items)
            if _jumps:
                logger.warning("[amo] 日程地点不连续 %d 处（source=%s day=%s）：%s",
                               len(_jumps), source or "-", day, "；".join(_jumps[:3]))
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.continuity", _amo_exc)
        try:
            _sick_fn = getattr(self, "on_sick_leave", None)
            _sick = bool(_sick_fn(day)) if callable(_sick_fn) and day else False
            _introduced = [p for p in health_problems(new_items, sick=_sick)
                           if p not in health_problems(old_items, sick=_sick)]
            # 追加型（finance 等）和健康巡检有自己的去重叠，别用体检把它们拦掉。
            _guard_health = str(source or "") not in ("finance", "health_audit", "snapshot")
            if _introduced and _guard_health:
                logger.warning("[amo] 日程体检不通过，拒绝本次写入(%s): %s",
                               source or "-", _introduced[:6])
                _version = self.db.schedule_version(day)
                # 只 warning 不够：拒绝也要留痕，面板 /schedule/audit 能看到
                # 「谁在几点想把块改成什么、被哪条体检规则挡了」。
                try:
                    self._audit_schedule(
                        day, source,
                        "体检拒绝: " + "；".join(str(item) for item in _introduced[:6]),
                        _version, [], old_items, new_items, rejected=_introduced)
                except Exception as _amo_exc:
                    _note_swallowed("amo_state_schedule.audit.rejected", _amo_exc)
                return {"changed": False, "version": _version,
                        "changed_hours": [], "cancelled": [], "saved": False,
                        "rejected": _introduced}
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.guard.health", _amo_exc)
        # 事实层统一：落库前给每块定 event_id（旧块继承、切块父块保留、新块新 id）。
        # 放在内容比较之前；内容没变只是补 id 时只落库，不升版本、不让日记失效。
        try:
            new_items, _id_stats = stamp_event_ids(day, old_items, new_items)
            if _id_stats.get("assigned") or _id_stats.get("inherited"):
                logger.debug("[amo] 事件身份：新分配 %d / 继承 %d（day=%s）",
                             int(_id_stats.get("assigned") or 0),
                             int(_id_stats.get("inherited") or 0), day)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.commit_schedule_change.event_id", _amo_exc)
            _id_stats = {"kept": 0, "inherited": 0, "assigned": 0}

        def _content_without_event_id(rows):
            out = []
            for row in rows or []:
                if isinstance(row, dict):
                    out.append({key: value for key, value in row.items()
                                if key != "event_id"})
                else:
                    out.append(row)
            return out

        if (_content_without_event_id(old_items) == _content_without_event_id(new_items)
                and invalidate_from is None):
            if old_items != new_items:
                try:
                    self.save_day(day, new_items)
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.commit_schedule_change.event_id_save",
                                    _amo_exc)
            # 只补 event_id 也算身份变化：旧内容哈希行会变成孤儿，按 diff 清一遍。
            try:
                self._sync_block_diaries(day, old_items, new_items,
                                         self.db.schedule_version(day))
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.commit.block_diary_sync_same", _amo_exc)
            return {
                "changed": False,
                "version": self.db.schedule_version(day),
                "changed_hours": [],
                "cancelled": [],
                "saved": True,
            }
        changed = self.changed_hours(old_items, new_items)
        try:
            version = self.db.bump_schedule_version(day)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.commit_schedule_change", _amo_exc)
            version = 0
        # 第八轮 C 审计结论：这里是今天日程的唯一落库点，写入语义是**整份替换**（不是增量），
        # 因此不需要在这里二次去重叠——去重叠只对「新块 vs 真正的外部旧块」做，
        # 由 commit_schedule_change 公共入口（_resolve_overlaps，先过滤同批旧块）和
        # 健康巡检 / 23:59 快照（各自 normalize 后再写）负责；否则整份替换会自撞（14:1x 事故）。
        self.schedule[day] = new_items
        if source in ("chat", "command", "manual"):
            try:
                ctx = dict(self.schedule_ctx.get(day) or {})
                ctx["manual_override"] = True
                ctx["manual_source"] = source
                ctx["manual_reason"] = str(reason or "")
                self.schedule_ctx[day] = ctx
                self.db.kv_set("schedule_ctx", self.schedule_ctx)
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.commit_schedule_change.manual", _amo_exc)
        # 落库成败要能传出去：JIT 到点决策是先扣钱后落库，落库失败要原路退款，
        # 以前这里把异常吞了，调用方永远以为「写成功了」（2026-11-15）。
        saved = True
        try:
            # 表 + kv 一起写（save_day）：只写一张会让两份存储漂移（2026-11-17）。
            self.save_day(day, new_items)
        except Exception as _amo_exc:
            saved = False
            _note_swallowed("amo_human_state.HumanState.commit_schedule_change", _amo_exc)
        # P0（2026-11-22）：块日记按 event_id diff 精确失效 + 清孤儿。
        # 放在落库之后、发布事件之前：事件订阅者只再负责旧整点表和取消任务。
        try:
            _diary_sync = self._sync_block_diaries(day, old_items, new_items, version)
            if (_diary_sync or {}).get("stale"):
                logger.info(
                    "[amo] 块日记失效: %d 条（改 %d / 删 %d / 孤儿 %d，day=%s）",
                    int((_diary_sync or {}).get("stale") or 0),
                    len((_diary_sync or {}).get("invalidated") or []),
                    len((_diary_sync or {}).get("orphans") or []),
                    int((_diary_sync or {}).get("stale") or 0)
                    - len((_diary_sync or {}).get("orphans") or []),
                    day,
                )
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.commit.block_diary_sync", _amo_exc)
        if invalidate_from is not None:
            try:
                start_hour = max(0, min(23, int(invalidate_from)))
            except (TypeError, ValueError):
                start_hour = 0
        elif changed:
            start_hour = min(changed)
        else:
            start_hour = None
        cancelled = self._publish_schedule_change(
            day, version, source, reason, changed, start_hour)
        logger.info(
            "[SCHEDULE_DEBUG] day=%s old=%s new=%s version=%s source=%s subject=bot "
            "target_hours=%s invalidate_from=%s cancel=%s reason=%s manual_override=%s",
            day,
            self._brief_schedule(old_items, changed or ([start_hour] if start_hour is not None else [])),
            self._brief_schedule(new_items, changed or ([start_hour] if start_hour is not None else [])),
            version, source or "-", changed or ([start_hour] if start_hour is not None else []),
            start_hour if start_hour is not None else "-", cancelled, reason or "-",
            bool((self.schedule_ctx.get(day) or {}).get("manual_override")),
        )
        try:
            self._audit_schedule(day, source, reason, version, changed, old_items, new_items,
                                 extra={"restored_appointments": len(_restored),
                                        "dropped_short_blocks": len(_short)})
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.audit", _amo_exc)
        return {
            "changed": True,
            "version": version,
            "changed_hours": changed,
            "cancelled": cancelled,
            "saved": saved,
        }

    def _normalize_schedule_items(self, new_items, source, day=""):
        # 清文案 → 归一地点 → 纠偏活动主语（三步各自兜底，坏一步不连累别的）
        try:
            from .amo_diary_guard import clean_schedule_text
            cleaned = []
            for row in new_items:
                if isinstance(row, dict):
                    row = dict(row)
                    for key in ("activity", "detail"):
                        if row.get(key):
                            row[key] = clean_schedule_text(row[key]) or row[key]
                cleaned.append(row)
            new_items = cleaned
        except Exception as exc:
            _note_swallowed("amo_human_state.py.1170", exc)
        try:
            new_items = [
                dict(row, place=normalize_place(row.get("place"), str(row.get("zone") or "")))
                if isinstance(row, dict) else row
                for row in new_items
            ]
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.commit_schedule_change.place", _amo_exc)
        try:
            # R9：地点归一化之后，用最终的前后块 place 刷新通勤块文案（「离开X，坐地铁去Y」）。
            # 必须放在 place 归一之后、zone 总闸之前，文案里才是用户真正看到的地名。
            try:
                from .amo_transition import refresh_transit_texts as _refresh_transit
            except ImportError:
                from amo_transition import refresh_transit_texts as _refresh_transit
            new_items = _refresh_transit(new_items)
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.normalize.transit_text", _amo_exc)
        try:
            new_items = self._guard_activity_subjects(new_items, source, day)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState._commit_schedule_change_locked", _amo_exc)
        try:
            # 病假当天 / 夜间一律不许出现「公司·上班」：JIT、改安排、过渡三条写路径都从
            # 这里过闸，所以把判断放在总闸里（2026-11-18 用户反馈 21:18 突然去公司上班）。
            sick = bool(self.on_sick_leave(day)) if day else False
            if not sick:
                try:
                    sick = bool((self.health_info() or {}).get("sick"))
                except Exception:
                    sick = False
            new_items = self._enforce_zone_activity(new_items, day, source, sick=sick)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState._enforce_zone_activity", _amo_exc)
        if sick:
            # 病假当天不许出现「通勤去公司/在公司」：整块换成在家休息
            fixed = []
            for row in new_items:
                if not isinstance(row, dict):
                    fixed.append(row)
                    continue
                text = str(row.get("activity") or "")
                place = str(row.get("place") or "")
                if ("公司" in place or "公司" in text) and str(row.get("category") or "") in ("work", "commute"):
                    row = dict(row, activity="在家歇着", place="家", zone="home", category="rest")
                fixed.append(row)
            new_items = fixed
        return new_items

    #: 病假/夜间/工作日下班后不许去公司（2026-11-18 用户要求：工作日回家后不准再回公司，周末可加班）。
    _OFFICE_WORDS = ("公司", "写字楼", "办公室", "工位")
    #: 只有正常上班时间能去公司：09:00-18:00 之外一律折回家（周末加班也按早 9 晚 6）。
    _WORK_START_HOUR = 9
    _WORK_END_HOUR = 18
    #: 区域 vs 文案的强冲突词；只拦“明显不可能”的组合，不碰擦边文案。
    _ZONE_CONFLICT = {
        "home": ("挤地铁", "坐地铁", "在公司", "到公司", "在工位", "办公室", "在商场", "逛街", "在路上", "开车去", "打车去"),
        "office": ("在家里", "在家", "窝在", "躺在床上", "被窝", "厨房", "客厅", "卧室"),
        # 路上块只拦「明显人已经到室内」的说法。以前裸词「公司/家」也拦：
        # 「坐高铁去石家庄」里的「家」被误当成「在家」，整句被抹成「在路上赶路」（R6）；
        # 「快到公司，楼下便利店…」也会被误判成在公司（R9）。改成短语/室内词。
        "road": ("工位", "办公室", "在公司", "到公司了", "在公司了", "沙发", "床",
                 "电视", "厨房", "被窝", "在家", "家里"),
    }
    _ZONE_FALLBACK = {
        "home": "在家待着，做点自己的事",
        "office": "在工位上忙工作",
        "road": "在路上赶路",
    }
    _ROAD_EXEMPT = ("回家", "到家", "路上", "地铁", "公交", "开车", "打车", "走",
                    "车厢", "等车", "赶路", "离开", "高铁", "火车", "飞机")
    #: 非约会日程里绝不能出现的实体陪伴词（用户物理上不在她身边）。
    _CATEGORY_WORDS = {
        "sleep": ("睡", "被窝", "床", "枕头", "眯", "梦", "醒", "赖", "翻", "烧", "冷", "热", "闹钟", "困", "闭眼"),
        # 通勤词表除了交通工具，也算「在路上/刚到达」的细节词：以前只认交通词，
        # 「到楼下便利店买了早餐，边吃边等电梯」「坐高铁去呼和浩特」都被抹成
        # 「在路上赶路」，细节全丢（2026-11-22 R6）。这里只补真实通勤细节，
        # 办公室/居家词仍交给下面的 zone 冲突表处理。
        "commute": ("地铁", "公交", "车", "路上", "站", "走", "通勤", "堵", "车厢", "赶", "出门",
                    "打车", "骑车", "扶", "下班", "回家", "到家",
                    "高铁", "火车", "动车", "飞机", "机场", "候车", "进站", "出站", "换乘",
                    "检票", "站台", "便利店", "早餐", "电梯", "楼下", "门口", "小区", "打卡",
                    "导航", "高架", "红灯", "路口", "停车场", "车位", "路边"),
    }
    _CATEGORY_FALLBACK = {"sleep": "一个人裹着被子睡着", "commute": "在路上赶路"}
    _PARTNER_PHYSICAL = ("黏着老公", "抱着老公", "搂着老公", "靠在老公", "躺在老公",
                         "老公怀里", "老公身边", "老公旁边", "和老公一起睡",
                         "他翻了个身", "他还在屋里", "他在屋里", "躺在他怀里")

    @classmethod
    def _enforce_zone_activity(cls, items, day="", source="", sick=False):
        """所有写日程路径的总闸：zone 和 activity 明显打架时纠正文案，保留物理骨架。"""
        # 白天在岗一直连到晚上的块是真实延时/夜班：只按「起点小时」判会把整段加班
        # 折成「在家休息」，办公室瞬间回家、还不补通勤（2026-11-22 R4）。
        office_chains = _office_shift_starts(items, cls._WORK_START_HOUR, cls._WORK_END_HOUR)
        # 「到楼下便利店买了早餐」这种到达类通勤块：目的地要对齐归一化后的下一块，
        # 不然仿真会把「便利店」当成这一程的目的地，和下一块的「公司」对不上（R9）。
        ordered = sorted(
            [row for row in (items or []) if isinstance(row, dict)
             and _hm(row.get("start")) is not None],
            key=lambda row: (_hm(row.get("start")) or 0, _hm(row.get("end")) or 0))
        position = {id(row): index for index, row in enumerate(ordered)}
        for row in items or []:
            if not isinstance(row, dict):
                continue
            zone = str(row.get("zone") or "")
            if zone not in cls._ZONE_CONFLICT:
                continue
            act = str(row.get("activity") or "")
            if not act:
                continue
            category = str(row.get("category") or "")
            start = str(row.get("start") or "")
            hour = int(start[:2]) if start[:2].isdigit() else -1
            place = str(row.get("place") or "")
            if (zone == "road" and category == "commute" and act.startswith("到")
                    and str(row.get("source") or "") != "transit_block"
                    and str(source or "") not in _HUMAN_TEXT_SOURCES):
                index = position.get(id(row))
                next_row = None
                if index is not None:
                    for ahead in ordered[index + 1:]:
                        ahead_zone = str(ahead.get("zone") or "")
                        ahead_place = str(ahead.get("place") or "")
                        if ahead_zone == "road" or ahead_place == "路上":
                            continue
                        next_row = ahead
                        break
                next_place = str((next_row or {}).get("place") or "").strip()
                if next_place and next_place != "路上":
                    # 「快到公司，楼下便利店买了早餐，边吃边等电梯」：既保留细节，
                    # 又把第一处「到X」变成真正的目的地。
                    row["activity"] = "快到%s，%s" % (next_place, act[1:])
                    act = str(row["activity"])
            # 只按「所在地」判，不按文案判：08:00 的 zone=road 写「在工位上发呆」是文案问题，
            # 该走下面的 zone 纠偏，不该被当成「去公司」折回家（2026-11-18）。
            at_office = zone == "office" or any(word in place for word in cls._OFFICE_WORDS)
            blocked = bool(sick) or hour >= cls._WORK_END_HOUR or (0 <= hour < cls._WORK_START_HOUR)
            if at_office and blocked:
                # 病假照旧全天折家；非病假但「白天连到晚上」的在岗段保留 office，
                # 只走下面的文案纠偏（防止把加班块写成居家）。夜里孤立的公司块
                # （没有白天连续段，例如 21:18 突然去公司）仍按旧规则折家。
                late_shift = (not sick and hour >= cls._WORK_END_HOUR
                              and _office_shift_continues(
                                  office_chains.get(id(row)),
                                  cls._WORK_START_HOUR, cls._WORK_END_HOUR))
                if not late_shift:
                    safe = "在家养病休息" if sick else "在家休息"
                    logger.warning("[amo] 病假/夜间禁止去公司，已折回家: day=%s %s category=%s zone=%s %r -> %r source=%s sick=%s",
                                   day, start, category, zone, act[:40], safe, source, sick)
                    row["activity"] = safe
                    row["place"] = "家"
                    row["zone"] = "home"
                    row["category"] = "rest"
                    row["detail"] = ""
                    row.pop("transit", None)
                    row.pop("vehicle", None)
                    continue
            allowed = cls._CATEGORY_WORDS.get(category)
            if allowed and not any(word in act for word in allowed):
                safe = cls._CATEGORY_FALLBACK[category]
                logger.warning("[amo] 日程类别/文案冲突已纠正: day=%s %s category=%s %r -> %r source=%s",
                               day, row.get("start"), category, act[:40], safe, source)
                row["activity"] = safe
                row["detail"] = ""
                continue
            if category not in ("social", "date") and any(word in act for word in cls._PARTNER_PHYSICAL):
                safe = "一个人裹着被子睡着" if category == "sleep" else cls._ZONE_FALLBACK.get(zone, "一个人待着")
                logger.warning("[amo] 非约会日程出现实体陪伴，已改写: day=%s %s zone=%s %r -> %r source=%s",
                               day, row.get("start"), zone, act[:40], safe, source)
                row["activity"] = safe
                row["detail"] = ""
                continue
            if zone == "road" and any(word in act for word in cls._ROAD_EXEMPT):
                continue
            if not any(word in act for word in cls._ZONE_CONFLICT[zone]):
                continue
            fallback = cls._ZONE_FALLBACK[zone]
            logger.warning("[amo] 日程区域/文案冲突已纠正: day=%s %s-%s zone=%s %r -> %r source=%s",
                           day, row.get("start"), row.get("end"), zone, act[:40], fallback, source)
            row["activity"] = fallback
            row["detail"] = ""
        return items

    def settle_schedule_costs(self, now=None):
        """日程块结束后按 LLM 定价结算一次；同一 event_id 绝不重复扣款。

        event_id 是块持久化的身份（不是临时拼的 hash）：账本行带同一个 id，
        kv 标记与钱包幂等键都认它。旧库里没有 event_id 的块按 day+start+end
        确定性补齐（与迁移同口径），所以升级当天的旧账也不会重复扣；钱包幂等键
        沿用旧格式 sch_<day>_<start>_<category>，历史已入账的行不会被再扣一次。
        """
        now = now or _now()
        day = now.date().isoformat()
        now_min = int(now.hour) * 60 + int(now.minute)
        try:
            items = list(self.schedule.get(day) or [])
        except Exception:
            items = []
        for item in items:
            cost = int(item.get("cost_cents") or 0)
            if cost <= 0 or item.get("cost_settled"):
                continue
            end = parse_hhmm(item.get("end"), 24 * 60)
            if now_min < end:
                continue
            eid = ensure_event_id(day, item)
            item["event_id"] = eid
            idem = "sch_%s_%s_%s" % (day, item.get("start"), item.get("category") or "life")
            flag = "cost_settled:" + idem
            try:
                if self.db.kv_get(flag, False):
                    item["cost_settled"] = True
                    continue
            except Exception:  # cleanup: KV read is best-effort
                pass
            payload = {"ok": False, "reason": "余额不足"}
            try:
                reason = "日程消费·%s" % (item.get("activity") or "花销")
                debit = self.p.wallet.debit
                try:
                    ok = bool(debit(cost, reason, idem, event_id=eid,
                                    category="schedule",
                                    item_name=str(item.get("activity") or "")))
                except TypeError:
                    # 旧版钱包没有 event_id 形参：退回旧签名，金额语义不变。
                    ok = bool(debit(cost, reason, idem, category="schedule",
                                    item_name=str(item.get("activity") or "")))
                payload = {"ok": ok, "reason": "" if ok else "余额不足",
                           "cost_cents": cost, "event_id": eid, "idem": idem}
                if not ok:
                    logger.warning("[amo] 日程消费失败（余额不足）: %s %s 分", idem, cost)
            except Exception as exc:
                payload = {"ok": False, "reason": "扣款异常: %s" % exc}
                logger.warning("[amo] 日程消费异常: %s", exc)
            try:
                self.db.kv_set(flag, payload)
            except Exception as _amo_exc:
                _note_swallowed("amo_state_schedule.settle_cost.flag", _amo_exc)
            item["cost_settled"] = True
            item["cost_result"] = payload
        return items

    def _changed_block_ids(self, day, old_items, new_items):
        """日程里「被删 / 内容被改」的块身份（按 event_id 算 diff，不按小时猜）。"""
        before: dict[str, dict[str, Any]] = {}
        after: dict[str, dict[str, Any]] = {}
        for rows, target in ((old_items, before), (new_items, after)):
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                try:
                    target[str(block_id(day, row))] = row
                except Exception:  # cleanup: 单条坏块不参与 diff
                    continue
        changed = {bid for bid in before if bid not in after}
        for bid, old_row in before.items():
            new_row = after.get(bid)
            if new_row is None:
                continue
            if _diary_block_signature(old_row) != _diary_block_signature(new_row):
                changed.add(bid)
        return changed

    def _sync_block_diaries(self, day, old_items, new_items, version):
        """按 event_id diff 精确失效块日记 + 清孤儿（日程落库点唯一入口）。

        2026-11-22 P0：以前按 changed_hours 的最小小时粗扫整张块表，既会误杀
        后面没变的块（stale 风暴），又清不掉被删块留下的孤儿行（11-18 25 条
        valid 孤儿）。现在只失效「被删/内容变了」的 event_id，并把日程里已经
        不存在的 id 全部标 stale；快照内容不变时 diff 为空，不产生任何失效。
        """
        sync = getattr(self.db, "sync_block_diaries", None)
        if not callable(sync):
            return {}
        changed = self._changed_block_ids(day, old_items, new_items)
        if not changed:
            # 身份都没变也要清孤儿（例如升级后旧内容哈希行、手工改库留下的行）
            return sync(day, new_items, stale_ids=(), version=version)
        return sync(day, new_items, stale_ids=changed, version=version)

    def _publish_schedule_change(self, day, version, source, reason, changed, start_hour):
        # 有事件总线就发 ScheduleChanged（订阅者取消日记任务）；旧实例/单测走兼容分支
        cancelled = []
        events = getattr(self.p, "events", None)
        has_bus = bool(events is not None
                       and callable(getattr(events, "has_subscribers", None))
                       and events.has_subscribers(ScheduleChanged))
        if has_bus and events is not None:
            try:
                results = events.publish_collect(ScheduleChanged(
                    day=str(day), version=int(version or 0),
                    source=str(source or ""), reason=str(reason or ""),
                    manual_override=bool((self.schedule_ctx.get(day) or {}).get("manual_override")),
                    start_hour=start_hour if start_hour is not None else -1,
                    changed_hours=tuple(changed or ()),
                )) or []
                for result in results:
                    if isinstance(result, (list, tuple)):
                        cancelled = list(result)
                        break
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.HumanState.commit_schedule_change.event", _amo_exc)
        else:
            # 兼容没有事件总线的旧实例/单测；线上由 main 的订阅者接管。
            if start_hour is not None:
                try:
                    self.db.mark_diaries_stale(day, start_hour, version)
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.HumanState.commit_schedule_change", _amo_exc)
                cancel = getattr(self.p, "_cancel_diary_tasks", None)
                if callable(cancel):
                    try:
                        cancelled = cancel(day, start_hour) or []
                    except Exception as _amo_exc:
                        _note_swallowed("amo_human_state.HumanState.commit_schedule_change", _amo_exc)
            wardrobe = getattr(self.p, "wardrobe", None)
            if wardrobe is not None:
                try:
                    invalidate = getattr(wardrobe, "invalidate", None)
                    if callable(invalidate):
                        invalidate(day, reason="schedule_changed")
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.HumanState.commit_schedule_change", _amo_exc)
            photo = getattr(self.p, "photo", None)
            changed_hook = getattr(photo, "on_schedule_changed", None) if photo is not None else None
            if callable(changed_hook):
                try:
                    changed_hook(day, version, "schedule_changed")
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.HumanState.commit_schedule_change.photo", _amo_exc)
        return cancelled

    @staticmethod
    def _fmt_minutes(value):
        value = max(0, min(24 * 60, int(value)))
        if value >= 24 * 60:
            return "24:00"
        return "%02d:%02d" % (value // 60, value % 60)

    _DISPLAY_BOUNDARIES = (6 * 60, 9 * 60, 12 * 60, 14 * 60, 18 * 60, 22 * 60)

    @staticmethod
    def _display_priority(item):
        """展示时谁占这一分钟：具体流水（财务/约会/看病）压过背景日程。"""
        source = str(item.get("source") or "")
        if source in ("chat", "command", "manual"):
            return 90
        category = str(item.get("category") or "")
        if category in ("finance", "appointment", "date", "health"):
            return 70
        if category == "meal":
            return 55
        if category == "commute":
            return 50
        if category == "work":
            return 45
        if category in ("play", "social"):
            return 40
        if category == "life":
            return 30
        if category == "rest":
            return 20
        if category == "sleep":
            return 10
        return 25

    @staticmethod
    def _display_event(value):
        """只保留真正的事件 kind；财务情绪串不能冒充「突发事件」标签。"""
        kind = str(value or "")
        if kind in ("overtime", "friend_dinner", "business_trip", "date", "sick", "hospital"):
            return kind
        return ""

    @staticmethod
    def _finance_activity(activity, detail):
        """把「财务·支出」翻译成人话：买了个小蛋糕（花销 ¥41.95）。"""
        text = str(activity or "")
        if not any(word in text for word in ("财务", "工资", "支出", "收入", "入账")):
            return text
        detail_text = str(detail or "")
        amount = ""
        match = re.search(r"¥\s*([0-9][0-9,]*\.?[0-9]*)", detail_text)
        if not match:
            match = re.search(r"([0-9][0-9,]*\.?[0-9]*)\s*元", detail_text)
        if match:
            amount = match.group(1)
        if "工资" in text:
            return ("工资到账（¥%s）" % amount) if amount else "工资到账"
        reason = ""
        for part in re.split(r"[，,]", detail_text):
            if "：" in part or ":" in part:
                reason = re.split(r"[：:]", part)[-1].strip()
        if "支出" in text or "花" in text:
            if reason and amount:
                return "买了%s（花销 ¥%s）" % (reason, amount)
            if reason:
                return "买了%s" % reason
            if amount:
                return "花销 ¥%s" % amount
        return text

    def _default_slot(self, begin, finish, office=False):
        """过去没有事件的空档用默认生活状态填满，不写库，也不算剧透。

        2026-11-20：当天已经有「公司」块时，工作日 09:00-18:00 的空档必须
        留在公司/公司附近，不能默认成「在家发呆」——否则面板会把上班日
        写成「10:40 从公司变成家、下午不用上班」。
        """
        home = self._jit_home()
        if office and 9 * 60 <= begin < 18 * 60:
            if 12 * 60 <= begin < 14 * 60:
                return {"activity": "在公司附近吃午饭，歇一会儿",
                        "place": "公司", "category": "rest", "zone": "office"}
            return {"activity": "在工位上忙工作",
                    "place": "公司", "category": "work", "zone": "office"}
        if finish <= 6 * 60:
            return {"activity": "深度睡眠", "place": home, "category": "sleep", "zone": "home"}
        if begin < 9 * 60:
            return {"activity": "起床后的琐事", "place": home, "category": "life", "zone": "home"}
        if begin < 12 * 60:
            return {"activity": "日常发呆", "place": home, "category": "life", "zone": "home"}
        if begin < 14 * 60:
            return {"activity": "午饭后的放空", "place": home, "category": "rest", "zone": "home"}
        if begin < 18 * 60:
            return {"activity": "日常发呆", "place": home, "category": "life", "zone": "home"}
        if begin < 22 * 60:
            return {"activity": "窝在家里的晚上", "place": home, "category": "rest", "zone": "home"}
        return {"activity": "准备睡觉", "place": home, "category": "life", "zone": "home"}

    def _jit_visible_slots(self, raw, now_min):
        """把真实事件按分钟铺成连续时间轴；只补已经过去的空档。"""
        now_min = max(0, min(24 * 60, int(now_min)))
        now_hour_end = min(24 * 60, (now_min // 60 + 1) * 60)
        events = []
        just_started = []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            try:
                begin = parse_hhmm(item.get("start"))
            except Exception:
                continue
            # 块开始时间 == 当前分钟 → 它已经开始了，不能当未来藏掉（边界语义）。
            if begin > now_min:
                continue
            end_text = str(item.get("end") or "").strip()
            try:
                finish = 24 * 60 if end_text in ("24:00", "0:00", "00:00") else parse_hhmm(end_text)
            except Exception:
                finish = begin + 60
            if finish <= begin:
                finish = min(24 * 60, begin + 60)
            raw_finish = finish
            finish = min(finish, now_min)
            if finish < begin:
                continue
            event = {
                "begin": begin,
                "finish": finish,
                "raw_finish": raw_finish,
                "priority": self._display_priority(item),
                "activity": self._finance_activity(item.get("activity"), item.get("detail")),
                "place": str(item.get("place") or ""),
                "category": str(item.get("category") or "rest"),
                "zone": item.get("zone", ""),
                "transit": item.get("transit", ""),
                "vehicle": _vehicle_text(item),
                "detail": item.get("detail", ""),
                "event": self._display_event(item.get("event")),
                "mood": item.get("mood", ""),
                "open_sleep": bool(item.get("open_sleep")),
                "sleep_from": str(item.get("sleep_from") or ""),
            }
            if finish == begin:
                # 零长度只可能出现在「块刚好在这一分钟开始」：它不是没发生，而是刚发生。
                # 先攒着，等分段结束后补一条 current/doing 尾巴（和 15:01 看到的完全一致）。
                if begin == now_min and raw_finish > now_min:
                    just_started.append(event)
                continue
            events.append(event)
        points = {0, now_min}
        for event in events:
            points.add(max(0, min(now_min, event["begin"])))
            points.add(max(0, min(now_min, event["finish"])))
        ordered = sorted(points)
        office_day = any(str(item.get("zone") or "") == "office" for item in events)
        slots = []
        for start, end in zip(ordered, ordered[1:]):
            if end <= start:
                continue
            covering = [item for item in events if item["begin"] <= start and item["finish"] >= end]
            # 约会/社交进行中：财务流水只留在账本，不打断正在进行的日程块
            if any(item["category"] in ("social", "date") for item in covering):
                social_only = [item for item in covering if item["category"] != "finance"]
                if social_only:
                    covering = social_only
            if covering:
                winner = max(covering, key=lambda item: (item["priority"], item["finish"] - item["begin"]))
                slot = {
                    "start_min": start,
                    "end_min": end,
                    "activity": winner["activity"],
                    "place": winner["place"],
                    "category": winner["category"],
                    "zone": winner.get("zone", ""),
                    "transit": winner.get("transit", ""),
                    "vehicle": winner.get("vehicle", ""),
                    "detail": winner.get("detail", ""),
                    "event": winner.get("event", ""),
                    "mood": winner.get("mood", ""),
                    "open_sleep": winner["open_sleep"],
                    "sleep_from": winner.get("sleep_from", "") if start == winner["begin"] else "",
                    "current": False,
                    "doing": False,
                }
                if end == now_min and winner.get("raw_finish", 0) > now_min:
                    slot["end_min"] = min(winner["raw_finish"], now_hour_end)
                    slot["current"] = True
                    slot["doing"] = True
                slots.append(slot)
                continue
            cuts = [start] + [point for point in self._DISPLAY_BOUNDARIES if start < point < end] + [end]
            for part_start, part_end in zip(cuts, cuts[1:]):
                if part_end <= part_start:
                    continue
                default = self._default_slot(part_start, part_end, office=office_day)
                slots.append({
                    "start_min": part_start,
                    "end_min": part_end,
                    "activity": default["activity"],
                    "place": default["place"],
                    "category": default["category"],
                    "zone": default["zone"],
                    "transit": "",
                    "vehicle": "",
                    "detail": "",
                    "event": "",
                    "mood": "",
                    "open_sleep": False,
                    "sleep_from": "",
                    "current": False,
                    "doing": False,
                })
        if just_started:
            winner = max(just_started, key=lambda item: (item["priority"], item["raw_finish"]))
            slots.append({
                "start_min": now_min,
                "end_min": min(winner["raw_finish"], now_hour_end),
                "activity": winner["activity"],
                "place": winner["place"],
                "category": winner["category"],
                "zone": winner.get("zone", ""),
                "transit": winner.get("transit", ""),
                "vehicle": winner.get("vehicle", ""),
                "detail": winner.get("detail", ""),
                "event": winner.get("event", ""),
                "mood": winner.get("mood", ""),
                "open_sleep": winner["open_sleep"],
                "sleep_from": winner.get("sleep_from", ""),
                "current": True,
                "doing": True,
            })
        return slots

    @staticmethod
    def _merge_quiet_slots(slots):
        """相邻且同一件事的时段合并成自然跨度；不跨 6/12/18 点分组。"""
        def part(value):
            if value < 6 * 60:
                return 0
            if value < 12 * 60:
                return 1
            if value < 18 * 60:
                return 2
            return 3

        merged: list[Any] = []
        for slot in slots:
            if merged:
                last = merged[-1]
                if (last["end_min"] == slot["start_min"]
                        and last["activity"] == slot["activity"]
                        and last["place"] == slot["place"]
                        and last["category"] == slot["category"]
                        and part(last["start_min"]) == part(slot["start_min"])):
                    last["end_min"] = slot["end_min"]
                    last["current"] = last["current"] or slot["current"]
                    last["doing"] = last["doing"] or slot["doing"]
                    last["open_sleep"] = last["open_sleep"] or slot["open_sleep"]
                    continue
            merged.append(dict(slot))
        # 尾巴太碎：晚上最后一段休息/生活不足 1 小时，直接并到睡前（23:00），
        # 不允许出现「21:19-21:29 窝在家里的晚上」这种 10 分钟碎片
        if merged:
            last = merged[-1]
            if (str(last.get("category") or "") in ("rest", "life", "sleep")
                    and not bool(last.get("doing"))  # 刚开始的 doing 块不许被"睡前合并"拉长
                    and int(last.get("start_min") or 0) >= 18 * 60
                    and int(last.get("end_min") or 0) - int(last.get("start_min") or 0) < 60):
                last["end_min"] = max(int(last["end_min"]), 23 * 60)
        return merged

    #: 已经答应/约好的事（未来日期也要展示，不能当盲盒藏起来）
    _BOOKED_CATEGORIES = ("social", "date", "appointment", "health", "leave", "travel")

    def apply_schedule_summaries(self, day, tree):
        """只读缓存：把已生成的 4 字小标题贴回 tree（GET 绝不调 LLM）。"""
        try:
            cache = self.db.kv_get("schedule_block_summaries", {}) or {}
            if not isinstance(cache, dict):
                return tree
            for block in tree or []:
                facts = block.get("facts")
                if not isinstance(facts, dict) or not facts.get("events"):
                    continue
                slot_key = "%s|%s" % (day, block.get("start"))
                cached = cache.get(slot_key)
                if not isinstance(cached, dict) or not cached.get("summary"):
                    continue
                facts_key = json.dumps(facts, ensure_ascii=False, sort_keys=True)
                if cached.get("facts") == facts_key:
                    block["summary"] = str(cached["summary"])
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.apply_schedule_summaries", _amo_exc)
        return tree

    async def enrich_schedule_tree(self, day, tree, umo=""):
        """把每段日程的冷事实交给大模型起 4 字小标题；Python 不写现成人话。"""
        try:
            llm = getattr(self.p, "llm", None)
            if llm is None or not hasattr(llm, "chat_raw"):
                return tree
            cache = self.db.kv_get("schedule_block_summaries", {}) or {}
            if not isinstance(cache, dict):
                cache = {}
            changed = False
            for block in tree or []:
                facts = block.get("facts")
                if not isinstance(facts, dict) or not facts.get("events"):
                    continue
                slot_key = "%s|%s" % (day, block.get("start"))
                facts_key = json.dumps(facts, ensure_ascii=False, sort_keys=True)
                cached = cache.get(slot_key)
                if (isinstance(cached, dict) and cached.get("facts") == facts_key
                        and cached.get("summary")):
                    block["summary"] = str(cached["summary"])
                    continue
                system = ("你只负责给日程分组起一个四个字的拟人化小标题。"
                          "根据用户给的事实（时间、活动、地点、类别）概括，只输出四个汉字，"
                          "不要引号、不要解释、不要标点。")
                try:
                    text = await llm.chat_raw(str(umo or ""), system, facts_key)
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.enrich_schedule_tree.chat", _amo_exc)
                    text = ""
                summary = re.sub(r"[^\u4e00-\u9fff]", "", str(text or ""))[:4]
                if len(summary) == 4:
                    block["summary"] = summary
                    cache[slot_key] = {"facts": facts_key, "summary": summary}
                    changed = True
            if changed:
                if len(cache) > 200:
                    for key in list(cache)[:max(0, len(cache) - 200)]:
                        cache.pop(key, None)
                self.db.kv_set("schedule_block_summaries", cache)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.enrich_schedule_tree", _amo_exc)
        return tree

    def resolve_schedule_item(self, day, start="", end="", item_id=""):
        """按精确时间范围/ID 找真实日程；补出来的默认段返回 None，由 API 友好降级。"""
        day = str(day or today_str())
        start = str(start or "").strip()[:5]
        end = str(end or "").strip()[:5]
        try:
            from .amo_timeline import Timeline
        except ImportError:  # 直接按文件加载时
            from amo_timeline import Timeline
        try:
            items = Timeline(self).blocks(day)
        except Exception:
            items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if start and str(item.get("start") or "")[:5] == start:
                if not end or str(item.get("end") or "")[:5] == end:
                    return dict(item)
        try:
            point = parse_hhmm(start)
        except Exception:
            return None
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                begin = parse_hhmm(item.get("start"))
                finish = parse_hhmm(item.get("end"))
            except Exception:
                continue
            if finish <= begin:
                finish = begin + 60
            if begin <= point < finish:
                return dict(item)
        return None

    def schedule_blind_hint(self, day=None):
        """时间轴底部的盲盒提示：未来还没发生，不剧透。"""
        if not self._jit_enabled():
            return ""
        day = str(day or today_str())
        today = today_str()
        if day < today:
            return ""
        if day > today:
            return "—— 那天还没到，她会怎么过？到时候才知道 ——"
        return "—— 今天还没过完呢，她接下来会做什么？——"

    def _schedule_provenance(self, day):
        """当天每块的来源索引（读侧）：从 kv schedule_audit:day 的 before/after 还原。

        返回 index（键 (start, end, activity) -> {source, reason, version}）、
        latest（当天最后一条审计）和 version（当天版本号）。没有审计的老库全部
        回落空值，时间轴行仍然带 source/reason/version 三个字段。
        """
        index, latest = {}, None
        try:
            rows = list(self.db.kv_get("schedule_audit:%s" % str(day), []) or [])
        except Exception:
            rows = []
        for entry in rows[-200:]:
            if not isinstance(entry, dict):
                continue
            latest = entry
            meta = {"source": str(entry.get("source") or ""),
                    "reason": str(entry.get("reason") or ""),
                    "version": entry.get("version") or 0}
            for row in entry.get("after") or []:
                if isinstance(row, dict):
                    index[(str(row.get("start") or ""), str(row.get("end") or ""),
                           str(row.get("activity") or ""))] = meta
        try:
            version = int(self.db.schedule_version(str(day)) or 0)
        except Exception:
            version = 0
        return {"index": index, "latest": latest, "version": version}

    def _row_provenance(self, day, item, prov):
        """给时间轴每一行补 source/reason/version：审计里找不到就退回当天最后一条。"""
        prov = prov if isinstance(prov, dict) else {}
        index = prov.get("index") or {}
        latest = prov.get("latest")
        item = item if isinstance(item, dict) else {}
        meta = index.get((str(item.get("start") or ""), str(item.get("end") or ""),
                          str(item.get("activity") or ""))) or {}
        source = str(meta.get("source") or "")
        reason = str(meta.get("reason") or "")
        version = meta.get("version") or 0
        if not source and isinstance(latest, dict):
            source = str(latest.get("source") or "")
            reason = reason or str(latest.get("reason") or "")
            version = version or latest.get("version") or 0
        try:
            version = int(version or 0)
        except (TypeError, ValueError):
            version = 0
        if not version:
            version = int(prov.get("version") or 0)
        return {"source": source or "unknown", "reason": reason, "version": version}

    def schedule_tree(self, day=None, items=None):
        """一级 4 段、二级每小时；不再生成三级 10 分钟。

        JIT 盲盒模式：今天只展示已经发生的时段和正在进行的这一小时，
        未来不预生成、不占位；未来日期整张表留空，等到了那天再由 Tick 写入。

        P2-6：读接口纯读——不再在这里跑 _repair_transit_blocks / settle_date_end；
        修复拆到显式维护任务 maintain_schedule_day（后台 tick / 生成日历时调用）。
        P2-1：items 可由调用方传入（面板 GET 已读过一份），避免二次全日程读。
        """
        day = str(day or today_str())
        if day > today_str():
            return self._booked_tree(day, items=items)
        if self._jit_enabled() and day == today_str():
            return self._jit_schedule_tree(day, items=items)
        raw = items if isinstance(items, list) else (
            self.schedule.get(day) or self.db.get_schedule(day) or [])
        prov = self._schedule_provenance(day)
        hourly = self._hourly_schedule(raw)
        if not hourly:
            return []
        now_min = minutes_now()
        is_today = day == today_str()
        blocks: list[dict[str, Any]] = [
            {"start": "00:00", "end": "06:00", "title": "深夜"},
            {"start": "06:00", "end": "12:00", "title": "上午"},
            {"start": "12:00", "end": "18:00", "title": "中午"},
            {"start": "18:00", "end": "24:00", "title": "晚上"},
        ]
        for block in blocks:
            bstart = parse_hhmm(block["start"])
            bend = 24 * 60 if block["end"] == "24:00" else parse_hhmm(block["end"])
            children = []
            for item in hourly:
                start = max(bstart, parse_hhmm(item.get("start")))
                end = min(bend, parse_hhmm(item.get("end")))
                if start >= end:
                    continue
                hour = {
                    "id": "%s|%s-%s|%s" % (day, start, end, item.get("category", "rest")),
                    "start": self._fmt_minutes(start),
                    "end": self._fmt_minutes(end),
                    "activity": self._finance_activity(item.get("activity", "休息"), item.get("detail")),
                    "place": item.get("place", "家"),
                    "category": item.get("category", "rest"),
                    "zone": item.get("zone", ""),
                    "transit": item.get("transit", ""),
                    "vehicle": _vehicle_text(item),
                    "detail": item.get("detail", ""),
                    "event": self._display_event(item.get("event")),
                    "mood": item.get("mood", ""),
                    "current": bool(is_today and start <= now_min < end),
                    "doing": bool(is_today and start <= now_min < end),
                }
                hour.update(self._row_provenance(day, item, prov))
                children.append(hour)
            block["date"] = day
            block["children"] = children
            block["current"] = any(child["current"] for child in children)
            if children:
                block["activity"] = ""
                block["place"] = ""
                block["facts"] = {
                    "part": str(block.get("title") or ""),
                    "start": str(block.get("start") or ""),
                    "end": str(block.get("end") or ""),
                    "events": [
                        {
                            "time": (child.get("start", "") + "-" + child.get("end", "")),
                            "activity": child.get("activity", ""),
                            "place": child.get("place", ""),
                            "category": child.get("category", ""),
                        }
                        for child in children
                    ],
                }
        return blocks

    def schedule_tree_from_items(self, day, items):
        """面板 GET 用：用已经读过的 items 建树（不重复读日程，也不写库）。"""
        return self.schedule_tree(day, items=items)

    def maintain_schedule_day(self, day=None):
        """显式维护（后台 tick / 生成日历）：历史瞬移修复 + 约会收尾截断。

        读接口（schedule_tree / 面板 GET）不再触发这两个写操作；维护失败只记日志。
        """
        day = str(day or today_str())
        try:
            repair = getattr(self, "_repair_transit_blocks", None)
            if callable(repair):
                repair(day)
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.maintain.transit_repair", _amo_exc)
        try:
            self.settle_date_end(day)
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.maintain.settle", _amo_exc)
        return True

    @staticmethod
    def _cover_schedule(items):
        """兼容旧数据：补上 00:00 到第一条安排之间的睡眠段。"""
        if not isinstance(items, list) or not items:
            return items
        valid = [item for item in items if isinstance(item, dict) and item.get("start")]
        if not valid:
            return items
        ordered = sorted(valid, key=lambda item: parse_hhmm(item.get("start")))
        first_start = ordered[0].get("start", "00:00")
        if parse_hhmm(first_start) > 0:
            ordered.insert(0, {
                "start": "00:00", "end": first_start,
                "activity": "睡觉", "place": "家", "category": "sleep", "zone": "home",
            })
        return ordered

    def _emergency_schedule(self):
        """新引擎不可用时的最小兜底，保证 24 小时闭环、不瞬移。"""
        profile = getattr(self, "schedule_profile", {}) or {}
        home = self.p.cfg_str("schedule_home", "") or profile.get("home") or "家"
        office = self.p.cfg_str("schedule_workplace", "") or profile.get("workplace") or "公司"
        items = []
        for hour in range(24):
            if hour < 7 or hour >= 23:
                activity, place, zone, category = "睡觉", home, "home", "sleep"
            elif hour < 8:
                activity, place, zone, category = "起床洗漱", home, "home", "life"
            elif hour < 9:
                activity, place, zone, category = "通勤去公司", "路上", "road", "commute"
            elif hour < 12:
                activity, place, zone, category = "上班", office, "office", "work"
            elif hour < 13:
                activity, place, zone, category = "吃午饭", office + "附近", "restaurant", "meal"
            elif hour < 14:
                activity, place, zone, category = "午休", office, "office", "rest"
            elif hour < 18:
                activity, place, zone, category = "上班", office, "office", "work"
            elif hour < 19:
                activity, place, zone, category = "下班回家", "路上", "road", "commute"
            elif hour < 20:
                activity, place, zone, category = "吃晚饭", home, "home", "meal"
            elif hour < 22:
                activity, place, zone, category = "休息", home, "home", "rest"
            else:
                activity, place, zone, category = "洗澡护肤", home, "home", "life"
            items.append({
                "start": "%02d:00" % hour,
                "end": "24:00" if hour == 23 else "%02d:00" % (hour + 1),
                "activity": activity, "place": place, "zone": zone,
                "category": category,
            })
        return items

    def _day_type_config(self) -> list:
        """读 schedule_day_types：新版走 cfg_list，老配置/测试桩里只有 cfg()。"""
        for getter_name in ("cfg_list", "cfg"):
            getter = getattr(self.p, getter_name, None)
            if not callable(getter):
                continue
            try:
                value = getter("schedule_day_types", [])
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state._day_type_config", _amo_exc)
                continue
            if isinstance(value, list) and value:
                return value
        return []

    def _day_types(self) -> list:
        """读配置里的日子类型；没配就用默认那六种。"""
        if normalize_day_types is None:
            return []
        return normalize_day_types(self._day_type_config() or None)

    def _day_type_store(self) -> dict:
        """当天是什么日子按日期存一份：重启后不变，改权重也不会半路换类型。"""
        try:
            raw = self.db.kv_get("day_types", {}) or {}
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state._day_type_store", _amo_exc)
            return {}
        return dict(raw) if isinstance(raw, dict) else {}

    def _today_day_type(self, day: str) -> dict:
        """今天是什么日子：上班日 / 休息日 / 外出日 / 宅家日 / 加班日 / 摸鱼日。"""
        if pick_day_type is None:
            return {"id": "workday", "name": "上班日", "guide": ""}
        try:
            key = str(day)
            store = self._day_type_store()
            cached = store.get(key)
            if isinstance(cached, dict) and cached.get("id"):
                return cached
            # 法定节假日也按“休息日池”抽；不能只看 weekday>=5，否则节假日
            # 仍可能抽到加班日/上班日。
            try:
                rest = bool(day_meta(key).get("rest"))
            except Exception:
                rest = date.fromisoformat(key).weekday() >= 5
            picked = dict(pick_day_type(self._day_types(), rest, key))
            store[key] = picked
            if len(store) > 30:
                for old in sorted(store)[:-30]:
                    store.pop(old, None)
            self.db.kv_set("day_types", store)
            return picked
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state._today_day_type", _amo_exc)
            return {"id": "workday", "name": "上班日", "guide": ""}

    def schedule_text_of(self, day: str) -> str:
        """把某天的日程拍成一段文本（/日程 用）。"""
        try:
            key = str(day)
            items = self.schedule.get(key) or self.db.get_schedule(key) or []
            if self._jit_enabled() and key == today_str():
                now_min = minutes_now()
                # 盲盒：未来的时段连预约也不提前展示，到了那小时再由 Tick 写入
                items = [item for item in items
                         if parse_hhmm(item.get("start")) <= now_min]
            if not items:
                return "那天还没有安排。"
            info = self._day_type_store().get(key) or {}
            lines = []
            if isinstance(info, dict) and info.get("name"):
                lines.append("今天是%s。" % info["name"])
            for item in items:
                activity = str(item.get("activity") or "").strip()
                if not activity:
                    continue
                place = str(item.get("place") or "").strip()
                lines.append("%s-%s %s%s" % (
                    item.get("start") or "", item.get("end") or "", activity,
                    ("（%s）" % place) if place else "",
                ))
            return "\n".join(lines[:24]) or "那天还没有安排。"
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.schedule_text_of", _amo_exc)
            return "想不起来…"

    def _schedule_gen_lock(self, day):
        key = str(day or today_str())
        lock = self._schedule_gen_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._schedule_gen_locks[key] = lock
        return lock

    async def ensure_schedule(self, umo, force=False):
        """每天只生成一次主干；同一日期加锁，避免并发生成两份。"""
        async with self._schedule_gen_lock(today_str()):
            result = await self._ensure_schedule_locked(umo, force=force)
            try:
                repair = getattr(self, "_repair_transit_blocks", None)
                if callable(repair):
                    repair(today_str())
            except Exception as _amo_exc:
                _note_swallowed("amo_state_schedule.ensure_schedule.transit_repair", _amo_exc)
            return result

    async def _ensure_schedule_locked(self, umo, force=False):
        """ensure_schedule 的锁内实现；点击二级卡片时再动态生成这一小时的日记。"""
        today = today_str()
        meta = day_meta(today)
        try:
            await self.decide_sick_action(umo, _now())
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.ensure_schedule.sick_decision", _amo_exc)
        # 旅游日池：旅行日只用旅游事件池，休息日只留「旅游回来累瘫在家」单块。
        _trip_kind_of = getattr(self, "_trip_day_kind", None)
        _trip_kind = _trip_kind_of(today) if callable(_trip_kind_of) else ""
        if _trip_kind:
            return self._trip_day_items(today, _trip_kind)
        if not force and self._jit_enabled():
            return self._schedule_jit_purge(today)
        if force:
            self._schedule_reset_forced(today)
        previous = list(self.schedule.get(today) or [])
        existing = None if force else self.schedule.get(today)
        context = self.schedule_ctx.get(today) or {}
        if not force and context.get("manual_override") and self.schedule.get(today):
            logger.info("[SCHEDULE_DEBUG] day=%s manual_override=true skip=auto_ensure", today)
            return self._hourly_schedule(self.schedule.get(today) or [])
        version_ok = (
            self.db.kv_get("schedule_version", "") == SCHEDULE_VERSION
            and isinstance(context, dict)
            and context.get("version") == "backbone-v5"
        )
        day_type = self._today_day_type(today)
        if existing and version_ok and not force:
            hourly = self._hourly_schedule(existing)
            if hourly != existing:
                self.commit_schedule_change(
                    today, hourly, old_items=existing,
                    source="ensure_normalize", reason="hourly_normalize",
                )
            return hourly
        items, context = await self._schedule_rebuild(umo, today, meta, day_type, force)
        self._schedule_persist(today, items, previous, context, force)
        await self._schedule_apply_travel(today)
        try:
            if (self.sick_decision(today) or {}).get("action") == "take_sick_leave":
                self.db.kv_set("sick_rebuilt:%s" % today, True)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.ensure_schedule.sick_rebuilt", _amo_exc)
        return items

    def _schedule_jit_purge(self, today):
        """JIT 盲盒：只读过滤（预约 + 已发生流水），不写回库。

        以前这里每次 tick 都把过滤结果 commit 回去，既制造写入风暴，又会让
        「整份替换」被去重叠逻辑当成新块截断（2026-11-16 幽灵日程事故）。
        """
        return self._jit_items(today)

    def _schedule_reset_forced(self, today):
        """force 重生成前清掉当天的 day_type / manual_override，否则会读回旧类型。"""
        # /amo schedule regen、面板重生成：先丢掉当天缓存的 day_type，
        # 否则配置改成 weekend 之后还会读回旧的 homebody。
        try:
            store = self._day_type_store()
            if today in store:
                store.pop(today, None)
                self.db.kv_set("day_types", store)
            ctx = dict(self.schedule_ctx.get(today) or {})
            if ctx.pop("manual_override", None):
                self.schedule_ctx[today] = ctx
                self.db.kv_set("schedule_ctx", self.schedule_ctx)
                logger.info("[SCHEDULE_DEBUG] day=%s manual_override=cleared reason=force_regen", today)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.ensure_schedule.clear_day_type", _amo_exc)

    async def _schedule_rebuild(self, umo, today, meta, day_type, force):
        """重建主干：世界观/人设/天气/健康/周期 → engine.build_context/skeleton → 兜底。"""
        world = await self.ensure_worldview(umo)
        persona = ""
        try:
            if self.p.llm is not None:
                persona = await self.p.llm.persona(umo)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.ensure_schedule", _amo_exc)
            persona = ""
        if self.engine is not None:
            try:
                self.schedule_profile = self.engine.resolve_profile(world, persona, seed=today)
            except Exception as exc:
                logger.debug("[amo] 角色位置解析失败: %s" % exc)
        weather = await self.refresh_weather()
        try:
            self.roll_health()
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.ensure_schedule", _amo_exc)
        period = self.period_info()
        health = self.health_info()
        context = None
        items = None
        if self.engine is not None:
            try:
                context = self.engine.build_context(
                    today, meta, weather, world, self.mood, period, health,
                    persona=persona, salt=str(now_ts()) if force else "",
                )
                self.schedule_profile = context.get("profile") or self.schedule_profile
                context["day_type"] = day_type
                try:
                    context["sick_action"] = str((self.sick_decision(today) or {}).get("action") or "")
                except Exception:
                    context["sick_action"] = ""
                items = self.engine.build_skeleton(context)
                try:
                    items = await self.engine.enrich(umo, context, items, self.p.llm)
                except Exception as exc:
                    logger.debug("[amo] 日程主干文案润色失败，保留骨架: %s" % exc)
            except Exception as exc:
                logger.warning("[amo] 日程主干生成失败，使用兜底: %s" % exc)
                items = None
        if not items:
            profile = getattr(self, "schedule_profile", {}) or {}
            fallback_city = city_or_empty(self.p)
            context = context or {
                "version": "backbone-v5",
                "day": today,
                "meta": meta,
                "weather": weather,
                "world": world,
                "city": fallback_city,
                "home": self.p.cfg_str("schedule_home", "") or profile.get("home") or "家",
                "workplace": self.p.cfg_str("schedule_workplace", "") or profile.get("workplace") or "公司",
                "car": profile.get("car") or "",
                "mood": dict(self.mood),
                "period": period,
                "health": health,
                "event": {"kind": "none"},
                "scenic": None,
                "profile": profile,
                "location": fallback_city,
                "good_weather": True,
                "yesterday": self.engine.yesterday_summary(today) if self.engine is not None else {},
            }
            items = self._emergency_schedule()
        items = self._hourly_schedule(items)
        return items, context

    def _schedule_persist(self, today, items, previous, context, force):
        """落库：context/version、commit、清理 14 天前旧日程与 day_type。"""
        if context:
            context["version"] = "backbone-v5"
            context["day"] = today
            self.schedule_ctx[today] = context
            self.db.kv_set("schedule_ctx", self.schedule_ctx)
        self.db.kv_set("schedule_version", SCHEDULE_VERSION)
        # 2026-11-21：ensure / ensure_regen / 面板 reconcile / 快照会互相触发；
        # 同一天 30 秒内只允许落库一次，避免多个功能各生成一版、来回覆盖。
        if regen_debounced(self.db, today, force=force):
            logger.info("[amo] 日程重生成去抖：%d 秒内刚重建过，跳过本次落库",
                        SCHEDULE_REGEN_DEBOUNCE_SECONDS)
            return False
        self.commit_schedule_change(
            today, items, old_items=previous,
            source="ensure_regen" if force else "ensure",
            reason="force" if force else "rebuild",
            invalidate_from=0 if force else None,
        )
        cutoff = (_now().date() - timedelta(days=14)).isoformat()
        for day in list(self.schedule.keys()):
            if day < cutoff:
                self.schedule.pop(day, None)
                self.schedule_ctx.pop(day, None)
        self.db.kv_set("schedule_ctx", self.schedule_ctx)
        day_types = self._day_type_store()
        stale_types = [key for key in day_types if key < cutoff]
        if stale_types:
            for key in stale_types:
                day_types.pop(key, None)
            self.db.kv_set("day_types", day_types)
        logger.info("[amo] 生成日程主干 %s：%s" % (
            today, (context or {}).get("event", {}).get("kind", "none"),
        ))

    def apply_schedule_events(self):
        """当前小时的日程事件只触发一次，避免每轮都扣心情。"""
        item = self.current_activity()
        effects = item.get("effects") if isinstance(item.get("effects"), dict) else {}
        ctx = self.schedule_context(today_str())
        key = "schedule_event_%s_%s_%s" % (
            today_str(), item.get("start", ""), ctx.get("seed", ""),
        )
        if self.db.kv_get(key, False):
            return
        applied = False
        clean = {}
        for mood_key, delta in effects.items():
            if mood_key in MOOD_KEYS:
                try:
                    clean[mood_key] = float(delta)
                    applied = True
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.HumanState.apply_schedule_events", _amo_exc)
                    continue
        if not applied:
            # 日常活动也要影响真实心情：上班会累、逛街会开心、睡觉回血。
            # CATEGORY_MOOD 是日程内部用的「每小时」增量，真实心情每轮都会向基准
            # 回落，所以乘一个增益（schedule_mood_gain，默认 3）才看得出来。
            try:
                gain = max(0.0, self.p.cfg_float("schedule_mood_gain", 3.0))
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.HumanState.apply_schedule_events", _amo_exc)
                gain = 3.0
            category = str(item.get("category") or "")
            for mood_key, delta in (schedule_category_mood().get(category) or {}).items():
                if mood_key not in MOOD_KEYS:
                    continue
                try:
                    clean[mood_key] = float(delta) * gain
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.HumanState.apply_schedule_events", _amo_exc)
                    continue
            applied = bool(clean)
        if clean:
            self.nudge(**clean)
        # 兼容旧版日程里用 detail 标记的事件链。
        detail = str(item.get("detail") or "")
        if not applied and detail:
            legacy = {
                "熬夜": {"疲惫": 7, "暴躁": 3, "开心": -3},
                "精神恍惚": {"疲惫": 5, "焦虑": 5},
                "老板": {"生气": 8, "委屈": 8, "郁闷": 6, "暴躁": 4},
                "扣工资": {"郁闷": 8, "生气": 6},
                "同事闹脾气": {"生气": 5, "委屈": 3},
                "同事聊天": {"开心": 3, "平静": 2},
            }
            for word, deltas in legacy.items():
                if word in detail:
                    self.nudge(**deltas)
                    applied = True
                    break
        self.db.kv_set(key, True)
