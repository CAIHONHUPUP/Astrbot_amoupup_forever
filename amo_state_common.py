# -*- coding: utf-8 -*-
"""状态机公共层：地点/时段纯 helper、日程兜底挑选，以及会被测试改写的模块级函数代理。

从 amo_human_state.py 拆出（只搬代码，行为零变化）。amo_human_state 继续 re-export 这里
的全部名字，外部 `from amo_human_state import ...` 不受影响；四个 mixin 也从这里拿。

代理层（_now / today_str / minutes_now / _city_* / _validate_activity）每次调用都回查
amo_human_state 的最新绑定：测试会直接给 `amo_human_state._now` 之类赋值，方法搬进
mixin 之后必须还看得见这些补丁。
"""
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

try:  # 纯规则模块：日程 → 地点/场景优先级（照片严格跟随日程用）
    from .photo_rules import (
        CATEGORY_PRIORITY,
        zone_for_place,
    )
except ImportError:  # 直接按文件加载时
    from photo_rules import (
        CATEGORY_PRIORITY,
        zone_for_place,
    )

try:  # amo_shared：时间/解析共享底层
    from .amo_shared import (
        in_minute_range,
        now_ts,
        parse_hhmm,
    )
except ImportError:  # 直接按文件加载时
    from amo_shared import (
        in_minute_range,
        now_ts,
        parse_hhmm,
    )


#: 同一小时小日记最多允许重写的次数（超过后冻结，保留最后一版）
DIARY_REWRITE_LIMIT = 5


PLACE_WHITELIST = frozenset({
    "家", "卧室", "客厅", "公司", "楼下小馆", "地铁", "商场",
})
_PLACE_ALIASES = (
    ("他桌面上", "家"), ("桌面", "家"), ("电脑里", "家"), ("家里", "家"),
    ("家中", "家"), ("出租屋", "家"), ("小窝", "家"), ("房间", "家"),
)
_PLACE_ZONE_KEEP = frozenset({
    "road", "health", "hospital", "hotel", "scenic", "restaurant", "station", "school",
})


def normalize_place(value, zone=""):
    """地点白名单：桌面投影/旧数据一律洗成「家」；通勤/就医/酒店等真实场景保留。"""
    text = str(value or "").strip()
    if text in PLACE_WHITELIST:
        return text
    for alias, canonical in _PLACE_ALIASES:
        if alias in text:
            return canonical
    if text in ("路上", "医院", "酒店房间", "景区", "餐厅", "车站", "学校"):
        return text
    zone = str(zone or "")
    if zone == "office":
        return "公司"
    if zone == "mall":
        return "商场"
    if zone == "restaurant":
        return "楼下小馆"
    if zone == "road":
        if "地铁" in text or "公交" in text:
            return "地铁"
        return text or "路上"
    if zone in ("health", "hospital", "hotel", "scenic", "station", "school"):
        return text or "家"
    return "家"


def slot_has_ended(day, start, end, now=None):
    """这一小时是否已经过完（到 end 之后才允许生成小日记）。"""
    try:
        now = now or _now()
        day_text = str(day or "")
        today_text = now.strftime("%Y-%m-%d")
        if day_text < today_text:
            return True
        if day_text > today_text:
            return False
        end_text = str(end or "").strip()[:5]
        if end_text in ("24:00", "0:00"):
            end_min = 24 * 60
        else:
            end_min = parse_hhmm(end_text)
        return now.hour * 60 + now.minute >= end_min
    except Exception:
        return True


def slot_is_future(day, start, now=None):
    """这一小时还没到（包括明天/以后的日程），小日记先不生成。"""
    try:
        now = now or _now()
        day_text = str(day or "")
        today_text = now.strftime("%Y-%m-%d")
        if day_text > today_text:
            return True
        if day_text != today_text:
            return False

        def _minutes(text):
            body = str(text or "")[:5]
            try:
                hour_text, minute_text = body.split(":")[:2]
                return int(hour_text) * 60 + int(minute_text)
            except (ValueError, TypeError):
                return None

        # 比分钟数而不是比字符串：add_schedule_item 允许 "9:00" 这种一位小时，
        # 字符串比 "9:00" > "14:00" 会成立，那一小时的小日记就永远不生成。
        start_min = _minutes(start)
        now_min = now.hour * 60 + now.minute
        if start_min is None:
            return False
        return start_min > now_min
    except Exception:
        return False


# ---------- 回查 amo_human_state 的代理层（测试会直接改它的模块级函数） ----------
_STATE = None


def _state_module():
    """延迟取 amo_human_state：导入期不能反向 import（会循环），运行期才回查。"""
    global _STATE
    if _STATE is None:
        try:
            from . import amo_human_state as module
        except ImportError:  # 直接按文件加载时
            import amo_human_state as module
        _STATE = module
    return _STATE


def _now(*args, **kwargs):
    return _state_module()._now(*args, **kwargs)


def today_str(*args, **kwargs):
    return _state_module().today_str(*args, **kwargs)


def minutes_now(*args, **kwargs):
    return _state_module().minutes_now(*args, **kwargs)


def _city_pick_spots(*args, **kwargs):
    return _state_module()._city_pick_spots(*args, **kwargs)


def _city_travel_events(*args, **kwargs):
    return _state_module()._city_travel_events(*args, **kwargs)


def _city_travel_to_events(*args, **kwargs):
    return _state_module()._city_travel_to_events(*args, **kwargs)


def _validate_activity(activity, approved=False):
    return _state_module()._validate_activity(activity, approved=approved)


def _jit_activity(self):
    """到点即时决策的当前活动（JIT 优先），没有就返回 None。"""
    try:
        jit = self.jit_action()
        if jit and float(jit.get("until") or 0) > now_ts():
            place = str(jit.get("place") or "家")
            return {"activity": jit.get("action"), "place": place,
                    "zone": str(jit.get("zone") or zone_for_place(place) or "home"),
                    "category": str(jit.get("category") or "life"),
                    "_source": "jit", "_reason": "到点即时决策"}
    except Exception as exc:
        _note_swallowed("amo_human_state.current_activity_info.jit", exc)
    return None


def _today_items(self, today):
    try:
        items = self.schedule.get(today) or []
    except Exception:
        items = []
    return items if isinstance(items, list) else []


def _pick_covering(self, items, current):
    """覆盖当前分钟的那条；重叠时按「最近开始 + 类别优先级」选，并打日志。"""
    hits = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            if in_minute_range(current, parse_hhmm(item.get("start")), parse_hhmm(item.get("end"))):
                hits.append(item)
        except Exception:
            continue
    if len(hits) > 1:
        hits.sort(key=lambda it: (
            parse_hhmm(it.get("start")),
            CATEGORY_PRIORITY.get(str(it.get("category") or ""), 0),
        ))
        chosen = hits[-1]
        self._photo_info(
            "[PHOTO_DEBUG] schedule_conflict=%d chosen=%s-%s zone=%s others=%s"
            % (len(hits), chosen.get("start"), chosen.get("end"), chosen.get("zone"),
               "|".join("%s-%s" % (it.get("start"), it.get("end")) for it in hits[:-1]))
        )
        return dict(chosen), "schedule_conflict"
    if hits:
        return dict(hits[0]), "schedule"
    return None, ""


def _pick_stale(self, items, current):
    """今天有日程但这小时没覆盖：沿用最近开始的一段。"""
    if not items:
        return None
    started = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            if parse_hhmm(item.get("start")) <= current:
                started.append(item)
        except Exception:
            continue
    if not started:
        return None
    chosen = max(started, key=lambda it: parse_hhmm(it.get("start")))
    data = dict(chosen)
    data["_source"] = "stale_slot"
    data["_reason"] = "今天日程这小时没有覆盖，沿用最近开始的一段"
    self._photo_info(
        "[PHOTO_DEBUG] fallback=stale_slot chosen=%s-%s zone=%s"
        % (chosen.get("start"), chosen.get("end"), chosen.get("zone"))
    )
    return data


def _pick_yesterday_hour(self, current):
    """今天还没生成日程：沿用昨天同一小时。"""
    try:
        yesterday = (_now().date() - timedelta(days=1)).isoformat()
    except Exception:
        yesterday = ""
    if not yesterday:
        return None
    try:
        y_items = self.schedule.get(yesterday) or self.db.get_schedule(yesterday) or []
    except Exception:
        y_items = []
    for item in y_items or []:
        if not isinstance(item, dict):
            continue
        try:
            if in_minute_range(current, parse_hhmm(item.get("start")), parse_hhmm(item.get("end"))):
                data = dict(item)
                data["_source"] = "yesterday_same_hour"
                data["_reason"] = "今天日程还没生成，沿用昨天同一小时"
                self._photo_info(
                    "[PHOTO_DEBUG] fallback=no_schedule_today reused=yesterday_same_hour %s zone=%s"
                    % (yesterday, data.get("zone"))
                )
                return data
        except Exception:
            continue
    return None
