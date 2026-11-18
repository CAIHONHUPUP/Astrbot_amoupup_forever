# -*- coding: utf-8 -*-
"""通用过渡引擎：任何跨地点/跨状态的行为都按真实物理时间分步推进。

医院只是 commute 的一个例子。模型：
    kind(commute/bath/meal/sleep/shopping/errand) + from/to + steps[{name,minutes,place,zone,action}]
    + started_at/status，状态存 DB kv（重载不丢），每次读取按 now 重算当前 step。

规则（写死可测，配置可覆盖）：
- 只允许一个进行中的过渡；新过渡与旧过渡冲突时「正在做的事优先」，旧的记一条冲突日志后
  排队（保留在 kv 的历史里），不会凭空消失；
- 目的地只出现在最后一步之后（到达块），之前地点只能是 家/门口/路上/交通工具/浴室；
- ≥15 分钟的过渡写进 schedule 分块（source=transit，step=N），<15 分钟只写当前小时的 detail；
- 聊天/面板/snap/主动消息/生图都读同一个当前 step；
- 「刚下车/到楼下了」→ 立刻截断当前块、写下一块并推进状态。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

try:  # Phase 11：地点门面 + 分钟时钟统一实现
    from .amo_place import zone_for_place
    from .amo_numbers import clock_after as _clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_place import zone_for_place
        from amo_numbers import clock_after as _clock

import asyncio
import logging
import random

logger = logging.getLogger("amo.transition")

try:  # 时间轴：块读取/写入的唯一入口（第 1 阶段重构）
    from .amo_timeline import Timeline
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_timeline import Timeline

KEY = "transition"
STATE_KEY = "transition"

TRANSIT_RULE = (
    "你身处真实世界，任何事都需要时间。刚答应去做某件事时，必须先描述正在做的过程，"
    "严禁直接跳过过程报结果。严格按照当前真实时间线与物理状态回答：如果你还在床上/"
    "刚开始穿衣服，用户催你，你只能回答你正在起床、出门或在路上；"
    "严禁直接说你已经在目的地坐着/挂号/到了。"
)

MIN_BLOCK_MINUTES = 15
HOME = "家"
INDOOR_PLACES = frozenset({
    "家", "卧室", "客厅", "厨房", "浴室", "阳台", "出租屋", "床", "被窝", "家里",
})
DESTINATION_WORDS = ("医院", "公司", "商场", "景区", "餐厅", "车站", "学校", "酒店", "超市", "公园")

#: 各 kind 的「终态」词：过渡没走完就不许说（洗澡说洗完、吃饭说吃完…）
KIND_TERMINAL = {
    "bath": (("洗完了", "还在洗澡呢，等下"), ("洗完澡了", "还在洗澡呢，等下"),
             ("洗完啦", "还在洗澡呢，等下"), ("洗好了", "还在洗澡呢，等下")),
    "meal": (("吃完了", "还在吃呢，等下"), ("吃好了", "还在吃呢，等下"),
             ("吃饱了", "还在吃呢，等下"), ("结完账了", "还在结账，等下")),
    "sleep": (("睡着了", "还在洗漱，马上睡"), ("已经睡了", "还在洗漱，马上睡")),
    "errand": (("倒完了", "还在楼下，马上回去"), ("拿完了", "还在楼下，马上回去"),
               ("回来了", "还在楼下，马上回去"), ("买好了", "还在楼下，马上回去")),
}

#: 文本意图（用户说 / 她自己说都要能启动过渡）
INTENT_PATTERNS = (
    ("bath", ("洗澡", "洗个澡", "冲个凉", "去洗")),
    ("sleep", ("睡觉", "去睡", "该睡了", "躺下睡")),
    ("meal", ("去吃饭", "吃个饭", "我去吃饭", "点外卖吃")),
    ("errand", ("倒垃圾", "扔垃圾", "去倒垃圾", "拿快递", "取快递", "下楼买", "取外卖")),
    ("shopping", ("逛街", "买东西", "去商场")),
    ("work", ("上班", "去公司", "去上班", "去单位")),
)
NEGATIONS = ("不去", "不想去", "别去", "不用去", "才不去", "懒得去", "不要去")
DEST_HINTS = ("医院", "公司", "超市", "商场", "景区", "餐厅", "车站", "学校", "酒店", "公园")
ADVANCE_WORDS = ("刚下车", "到楼下了", "到了", "进商场了", "挂号了")
ERRAND_DETAIL = {
    "倒垃圾": "下楼倒垃圾", "扔垃圾": "下楼扔垃圾", "去倒垃圾": "下楼倒垃圾",
    "拿快递": "下楼拿快递", "取快递": "下楼取快递", "下楼买": "下楼买东西",
    "取外卖": "下楼取外卖",
}


def detect_intent(text):
    """纯函数：从一句话里认出「要去做什么」（negation 直接 None）。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    if any(word in raw for word in NEGATIONS):
        return None
    for kind, words in INTENT_PATTERNS:
        for word in words:
            if word not in raw:
                continue
            if kind == "meal":
                indoor = ("在家" in raw or "点外卖" in raw)
                return {"kind": kind, "to": (HOME if indoor else "楼下小馆"),
                        "minutes": KIND_MINUTES["meal"], "detail": ""}
            if kind == "errand":
                return {"kind": kind, "to": HOME, "minutes": 8,
                        "detail": ERRAND_DETAIL.get(word, "下楼办点事")}
            if kind == "shopping":
                return {"kind": kind, "to": "商场", "minutes": KIND_MINUTES["shopping"],
                        "detail": ""}
            if kind == "work":
                return {"kind": kind, "to": "公司",
                        "minutes": DESTINATION_MINUTES["公司"], "detail": ""}
            return {"kind": kind, "to": HOME, "minutes": KIND_MINUTES[kind], "detail": ""}
    for place in DEST_HINTS:
        if ("去" + place) in raw or ("到" + place) in raw or ("回" + place) in raw:
            return {"kind": "commute", "to": place,
                    "minutes": DESTINATION_MINUTES.get(place, KIND_MINUTES["commute"]),
                    "detail": ""}
    return None


def maybe_start_from_text(plugin, text, now=None, source="user"):
    """聊天入口：用户说「去洗澡/去倒垃圾/去医院」或她自己说，都先进过渡再回复。"""
    # 旅游目的地锁（10B1 单点）：出发后「改去大理」不能起新过渡；叫回直接切 returning。
    try:
        try:
            from . import amo_trip as _trip_guard
        except ImportError:
            import amo_trip as _trip_guard
        _guard = _trip_guard.guard_change(plugin, text)
        _action = str(_guard.get("action") or "")
        if _action == "blocked":
            logger.info("[trip] 过渡入口被目的地锁拦下：%s", str(text or "")[:40])
            return None
        if _action == "return" and _trip_guard.is_return_command(plugin, text):
            _trip_guard.early_return(plugin)
            logger.info("[trip] 过渡入口收到叫回：切到 returning")
            return None
    except Exception as exc:
        logger.debug("[trip] 过渡入口旅游守卫失败: %s", exc)
    intent = detect_intent(text)
    if not intent:
        return None
    now = _now(now)
    current_data = _kv_get(plugin, STATE_KEY, {}) or {}
    if isinstance(current_data, dict) and current_data.get("steps"):
        stage = stage_at(current_data, now)
        if stage and not stage.get("arrived"):
            same = (current_data.get("kind") == intent["kind"]
                    and str(current_data.get("to") or "") == str(intent.get("to") or ""))
            if same:
                logger.info("[amo] 已经在做同一件事，不重复启动：%s", intent["kind"])
                return None
            logger.info("[amo] 过渡冲突：%s 进行中，%s 排队", stage.get("name"), intent["kind"])
    kind = str(intent.get("kind") or "commute")
    to_place = str(intent.get("to") or "")
    then_kind, then_activity, then_minutes = "", "", 0
    if kind in ("shopping", "work"):
        to_place = "商场" if kind == "shopping" else "公司"
        then_kind = kind
        then_activity = "和柳如烟逛街" if kind == "shopping" else "在工位上忙工作"
        then_minutes = 180 if kind == "shopping" else 120
        kind = "commute"
    elif kind == "commute" and to_place == "公司":
        then_kind, then_activity, then_minutes = "work", "在工位上忙工作", 120
    data = start(plugin, kind, to_place, minutes=intent.get("minutes"),
                 now=now, category=("health" if to_place == "医院" else ""),
                 then_kind=then_kind, then_activity=then_activity,
                 then_minutes=then_minutes)
    if not data:
        return None
    if intent["kind"] == "meal" and str(intent.get("to")) in INDOOR_PLACES:
        data["no_blocks"] = True
        _kv_set(plugin, STATE_KEY, data)
    if intent["kind"] == "errand" and int(data.get("minutes") or 0) < MIN_BLOCK_MINUTES:
        note_short(plugin, intent.get("detail") or "下楼办点事", now)
    logger.info("[amo] 文本意图开启过渡（%s）：%s→%s，%d 分钟（来源=%s）",
                intent["kind"], data.get("from"), data.get("to"),
                int(data.get("minutes") or 0), source)
    return data


def maybe_advance(plugin, text, now=None):
    """她说「刚下车/到楼下了」→ 截断当前块、推进到下一块（只在最后一段才推进）。"""
    raw = str(text or "")
    if not any(word in raw for word in ADVANCE_WORDS):
        return None
    now = _now(now)
    data = _kv_get(plugin, STATE_KEY, {}) or {}
    if not isinstance(data, dict) or not data.get("steps"):
        return None
    stage = stage_at(data, now)
    if not stage or stage.get("arrived"):
        return None
    steps = data.get("steps") or []
    if int(stage.get("index") or 0) < len(steps) - 1:
        logger.info("[amo] 过渡推进忽略：还在第 %d 步，交给终态守卫改写", stage.get("index") + 1)
        return None
    advance(plugin, now, reason="说到了：%s" % raw[:20])
    logger.info("[amo] 过渡推进：%s", raw[:20])
    return stage_at(data, now)
DESTINATION_MINUTES = {
    "医院": 30, "公司": 40, "商场": 30, "超市": 20, "景区": 40, "餐厅": 25,
    "车站": 30, "学校": 30, "酒店": 35, "公园": 30,
}

KIND_MINUTES = {"commute": 30, "bath": 25, "meal": 50, "sleep": 20,
                "shopping": 30, "errand": 12}

# ---------- 第十轮 B1：跨地点切换必须补「回程/去程」通勤块 ----------
# 从这些地方回「家」默认用时（分钟区间）；公司按通勤 15-40，医院 25-35，商场 20-30。
TRANSIT_RETURN_RANGES = {
    "医院": (25, 35), "商场": (20, 30), "公司": (15, 40), "景区": (25, 40),
    "餐厅": (15, 25), "超市": (10, 20), "学校": (15, 30), "车站": (15, 30),
    "酒店": (15, 25), "公园": (10, 20),
}
TRANSIT_OTHER_RANGE = (15, 40)   # 非「回家」的跨地点切换
TRANSIT_MIN_MINUTES = 10         # 再短也要给 10 分钟，不能凭空瞬移
TRANSIT_MAX_MINUTES = 40
TRANSIT_KNOWN_PLACES = frozenset({
    "家", "卧室", "客厅", "厨房", "浴室", "阳台", "出租屋", "家里",
    "公司", "商场", "医院", "景区", "餐厅", "车站", "学校", "酒店房间",
    "路上", "超市", "公园", "楼下小馆",
})
_VEHICLE_RETURN = {
    "医院": "出租车", "商场": "出租车", "景区": "出租车", "酒店房间": "出租车",
    "车站": "出租车", "餐厅": "公交", "超市": "公交", "公园": "公交",
}
#: vehicle 中文名 -> transit 字段代码（地铁/公交/打车；自驾用 drive）
_TRANSIT_MODE_BY_VEHICLE = {
    "出租车": "taxi", "地铁": "subway", "公交": "bus", "开车": "drive",
}
#: 在车上时绝对不能说的「已经到家」类终态词（到站后由日程放行）。
_ROAD_HOME_TERMINAL = (
    ("已经到家了", "还在车上，快到楼下了"),
    ("快到家了", "还在车上，快到了"),
    ("到家啦", "还在车上，快到楼下了"),
    ("到家了", "还在车上，快到楼下了"),
    ("进家门", "还在车上，快到楼下了"),
    ("瘫在沙发上", "还在车上"),
    ("躺在沙发上", "还在车上"),
    ("瘫沙发", "还在车上"),
    ("躺沙发", "还在车上"),
    ("在沙发上", "还在车上"),
    ("躺下了", "还在车上"),
    ("在家里", "还在路上"),
    ("在家看电视", "还在路上，电视还没看上"),
    ("在家", "还在路上"),
    ("看电视", "还在车上"),
    ("躺在床上", "还在路上"),
    ("躺床上", "还在路上"),
    ("窝着", "还在路上"),
    ("在沙发上", "还在车上"),
    ("沙发", "还在车上"),
    ("卧室", "还在路上"),
    ("被窝", "还在路上"),
)

EMOTION_HINT = {
    "wronged": "你现在又委屈又气：可以气鼓鼓地抱怨（例：「（气鼓鼓套外套）去去去，你这就催，"
               "你凶什么凶嘛」），但台词仍要符合安全规则。",
    "reluctant": "你现在有点不情愿：可以嘴上抱怨两句（例：「这破医院人怎么这么多，都怪你非要我来」），"
                 "但还是要照做。",
}





def needs_transit(from_place, to_place):
    dest = str(to_place or "").strip()
    src = str(from_place or "").strip()
    if not dest or dest in INDOOR_PLACES or dest == src:
        return False
    return dest in DESTINATION_MINUTES or dest in DESTINATION_WORDS


def vehicle_for(to_place, minutes=30):
    place = str(to_place or "")
    if place == "公司":
        return "地铁"
    if place in ("景区", "酒店") or int(minutes or 0) >= 40:
        return "出租车"
    if place in ("餐厅", "公园", "超市"):
        return "公交"
    return "地铁"


def steps_for(kind, to_place="", minutes=None, from_place=HOME, activity=""):
    """按 kind 生成步骤列表（每步 name/minutes/place/zone/action）。"""
    kind = str(kind or "commute")
    place = str(to_place or "")
    if kind == "commute":
        total = int(minutes or DESTINATION_MINUTES.get(place, KIND_MINUTES["commute"]))
        first = max(5, total // 3)
        second = max(first + 5, (total * 2) // 3)
        vehicle = vehicle_for(place, total)
        return [
            {"name": "起床换衣服", "minutes": first, "place": from_place or HOME, "zone": "home",
             "action": "刚爬起来换衣服，还没出门"},
            {"name": "出门等车", "minutes": second - first, "place": "家门口", "zone": "home",
             "action": "出门了，在门口等车"},
            {"name": "在路上", "minutes": total - second, "place": vehicle, "zone": "road",
             "action": "在%s上，路上" % vehicle},
        ]
    if kind == "bath":
        return [
            {"name": "找衣服", "minutes": 5, "place": HOME, "zone": "home",
             "action": "在翻衣服，准备去洗澡"},
            {"name": "洗澡", "minutes": 15, "place": "浴室", "zone": "home",
             "action": "在洗澡，水声很大，没空看手机"},
            {"name": "擦头发", "minutes": 5, "place": HOME, "zone": "home",
             "action": "在擦头发，还没吹干"},
        ]
    if kind == "meal":
        return [
            {"name": "点菜", "minutes": 10, "place": place or "楼下小馆", "zone": "restaurant",
             "action": "刚坐下在点菜，还没上菜"},
            {"name": "吃饭", "minutes": 30, "place": place or "楼下小馆", "zone": "restaurant",
             "action": "正在吃，还没吃完"},
            {"name": "结账", "minutes": 10, "place": place or "楼下小馆", "zone": "restaurant",
             "action": "吃完在结账，准备走"},
        ]
    if kind == "sleep":
        return [
            {"name": "洗漱", "minutes": int(minutes or KIND_MINUTES["sleep"]), "place": "浴室",
             "zone": "home", "action": "在洗漱，准备睡了"},
        ]
    if kind == "shopping":
        return [
            {"name": "逛", "minutes": int(minutes or KIND_MINUTES["shopping"]), "place": place or "商场",
             "zone": "mall", "action": "在%s里逛" % (place or "商场")},
        ]
    total = int(minutes or KIND_MINUTES["errand"])
    return [
        {"name": "办事", "minutes": total, "place": place or HOME, "zone": "home",
         "action": "在忙一件小事"},
    ]


def _now(now=None):
    if now is not None:
        return now
    try:
        return _amo_clock.now()
    except Exception:  # pragma: no cover
        return None


def total_minutes(steps):
    return sum(max(1, int(step.get("minutes") or 0)) for step in steps or [])


def stage_at(transition, now=None):
    """按 now 重算当前 step（纯函数，数据只来自 transition 字典）。"""
    data = transition if isinstance(transition, dict) else {}
    steps = data.get("steps") or []
    if not data or not steps:
        return None
    now = _now(now)
    try:
        started_at = float(data.get("started_at") or 0)
        elapsed = max(0.0, (float(now.timestamp()) - started_at) / 60.0)
    except Exception:
        elapsed = 0.0
    total = total_minutes(steps)
    remaining_total = max(0, int(round(total - elapsed)))
    cursor = 0
    for index, step in enumerate(steps):
        length = max(1, int(step.get("minutes") or 0))
        if elapsed < cursor + length:
            return {
                "index": index, "step": step, "elapsed": elapsed,
                "remaining": max(0, int(round(cursor + length - elapsed))),
                "remaining_total": remaining_total, "arrived": False,
                "activity": step.get("action") or step.get("name") or "",
                "place": step.get("place") or HOME, "zone": step.get("zone") or "home",
                "name": step.get("name") or "", "kind": data.get("kind") or "commute",
                "from": data.get("from") or HOME, "to": data.get("to") or "",
            }
        cursor += length
    return {
        "index": len(steps), "step": steps[-1], "elapsed": elapsed, "remaining": 0,
        "remaining_total": 0, "arrived": True,
        "activity": _arrival_activity(data.get("to") or "", data.get("activity") or ""),
        "place": data.get("to") or "", "zone": zone_for_place(data.get("to") or "") or "road",
        "name": "到达", "kind": data.get("kind") or "commute",
        "from": data.get("from") or HOME, "to": data.get("to") or "",
    }


def _arrival_activity(to_place, original=""):
    place = str(to_place or "")
    if place == "医院":
        return "到了医院，在排队挂号"
    if place == "公司":
        return "到了公司，进工位了"
    if place in ("商场", "超市", "景区", "公园"):
        return "到了%s，正在往里走" % place
    base = str(original or "").strip()
    return ("到了%s，" % place) + (base or "先找个地方坐下") if place else (base or "办完了")


def _kv_get(plugin, key, default=None):
    try:
        return plugin.db.kv_get(key, default)
    except Exception:
        return default


def _kv_set(plugin, key, value):
    try:
        plugin.db.kv_set(key, value)
        return True
    except Exception as exc:  # pragma: no cover
        logger.debug("[amo] transition kv 写入失败: %s", exc)
        return False


def _num(values, key):
    try:
        return float((values or {}).get(key) or 0)
    except Exception:
        return 0.0


def current(plugin, now=None):
    """当前过渡（到点就收尾并返回 None）；首次读取会补写日程分块。"""
    data = _kv_get(plugin, STATE_KEY, {}) or {}
    if not isinstance(data, dict) or not data.get("steps"):
        return None
    stage = stage_at(data, now)
    if stage and stage.get("arrived"):
        finish(plugin, data, stage, now)
        return None
    _ensure_blocks(plugin, data, now, readonly=True)
    if data.get("cancelled"):
        return None
    return data


OFFICE_WORDS = ("公司", "写字楼", "办公室", "工位")
WORK_START_HOUR = 9
WORK_END_HOUR = 18


def _office_place(place):
    return any(word in str(place or "") for word in OFFICE_WORDS)


def _no_office_allowed(plugin, now=None):
    """病假当天、或夜间（>=20:00 / <06:00）不再安排去公司。

    2026-11-18：21:18 还起了一次「去公司」的过渡，到站后写了 120 分钟在工位忙工作。
    """
    now = now or _now()
    try:
        state = getattr(plugin, "state", None)
        checker = getattr(state, "on_sick_leave", None)
        if callable(checker) and checker():
            return True
    except Exception as exc:
        logger.debug("[amo] 请假状态读取失败: %s", exc)
    try:
        until = float(_kv_get(plugin, "health:sick_until", 0) or 0)
        if until > _amo_clock.now_ts():
            return True
    except Exception as exc:
        logger.debug("[amo] 病假截止时间读取失败: %s", exc)
    # 只有正常上班时间能去公司（周末加班也按早 9 晚 6）：09:00-18:00 之外不安排。
    return now.hour >= WORK_END_HOUR or now.hour < WORK_START_HOUR


def start(plugin, kind, to_place="", activity="", category="", minutes=None,
          now=None, from_place="", emotion="", then_kind="", then_activity="",
          then_minutes=0):
    """开始一次过渡；已有进行中的过渡按「正在做的事优先」排队。"""
    now = _now(now)
    kind = str(kind or "commute")
    to_place = str(to_place or "")
    if _office_place(to_place) and _no_office_allowed(plugin, now):
        logger.warning("[amo] 病假/夜间不安排去公司：to=%s hour=%s kind=%s", to_place, now.hour, kind)
        return None
    state = getattr(plugin, "state", None)
    current_data = _kv_get(plugin, STATE_KEY, {}) or {}
    if isinstance(current_data, dict) and current_data.get("steps"):
        stage = stage_at(current_data, now)
        if stage and not stage.get("arrived"):
            history = list(current_data.get("queued") or [])
            history.append({"kind": kind, "to": to_place, "queued_at": _amo_clock.now_ts()})
            current_data["queued"] = history[-5:]
            _kv_set(plugin, STATE_KEY, current_data)
            logger.info("[amo] 过渡冲突：%s 优先，新的 %s→%s 排队", stage.get("activity"), kind, to_place)
            return None
    if not from_place:
        try:
            from_place = str((state.current_activity() or {}).get("place") or "") if state else ""
        except Exception:
            from_place = ""
    from_place = from_place or HOME
    steps = steps_for(kind, to_place, minutes=minutes, from_place=from_place, activity=activity)
    total = total_minutes(steps)
    mood = {}
    try:
        mood = dict(getattr(state, "mood", {}) or {})
    except Exception:
        mood = {}
    emotion = emotion or ("wronged" if (_num(mood, "委屈") >= 60 or _num(mood, "郁闷") >= 60)
                          else "reluctant")
    data = {
        "kind": kind, "from": from_place, "to": str(to_place or ""),
        "activity": str(activity or ""), "category": str(category or ""),
        "started_at": float(now.timestamp()) if now else _amo_clock.now_ts(),
        "minutes": total, "steps": steps, "emotion": emotion,
        "status": "running", "blocks_written": False, "created_at": _amo_clock.now_ts(),
        "vehicle": vehicle_for(to_place, total),
    }
    # 第八轮 A：新过渡启动先截断与 now 重叠的旧活动块（否则 17:00 的逛街尾巴会穿帮）
    try:
        truncate_open_activity(plugin, now)
    except Exception as _amo_exc:
        logger.debug("[amo] 截断旧活动块失败: %s", _amo_exc)
    # 新过渡接手一切读状态：旧的「活动块」状态必须清掉，避免到站后又冒出上一段活动
    _kv_set(plugin, "activity_now", {})
    if then_kind:
        data["then_kind"] = str(then_kind)
        data["then_activity"] = str(then_activity or "")
        data["then_minutes"] = int(then_minutes or 0)
    _kv_set(plugin, STATE_KEY, data)
    logger.info("[amo] 过渡开始：%s %s→%s，%d 分钟（后续=%s）", kind, from_place, to_place, total,
                then_kind or "-")
    return data


def _state_items(plugin, day):
    """当天的块：统一走 Timeline（排序/去重/空 end 防御），不再直读缓存。"""
    try:
        return Timeline(plugin).blocks(day)
    except Exception:
        return []


def _commit(plugin, day, items, old_items):
    state = getattr(plugin, "state", None)
    try:
        if state is not None and callable(getattr(state, "commit_schedule_change", None)):
            return Timeline(plugin).save(day, items, old_items=old_items,
                                         source="transit", reason="过渡分块")
    except Exception as exc:
        logger.debug("[amo] 过渡分块落库失败: %s", exc)
    return None





def _drop_future_transit(plugin, now=None):
    """取消过渡时把还没开始的 transit 块一起删掉（避免留下半截日程）。"""
    now = _now(now)
    if now is None:
        return
    day = now.strftime("%Y-%m-%d")
    now_text = now.strftime("%H:%M")
    try:
        items = _state_items(plugin, day)
        kept, filled_to = [], ""
        for row in items:
            if (str(row.get("source") or "") == "transit"
                    and str(row.get("start") or "") >= now_text):
                filled_to = max(filled_to, str(row.get("end") or ""))
                continue
            kept.append(row)
        if filled_to and kept:
            # 删掉未落地的过渡块会把时间轴挖空；把前一块补到被删块的末尾（保持连续覆盖）。
            last = dict(kept[-1])
            if str(last.get("end") or "") < filled_to:
                last["end"] = filled_to
                kept[-1] = last
        if len(kept) != len(items) or filled_to:
            _commit(plugin, day, kept, items)
    except Exception as exc:
        logger.debug("[amo] 清理未落地过渡块失败: %s", exc)


def _ensure_blocks(plugin, data, now, readonly=False):
    """≥15 分钟的过渡写成分块日程（source=transit，带 step 下标）。

    readonly=True 时只清状态、不写库（GET /schedule 必须纯只读）。
    """
    if not isinstance(data, dict) or data.get("blocks_written"):
        return
    if data.get("no_blocks"):
        data["blocks_written"] = True
        _kv_set(plugin, STATE_KEY, data)
        return
    now = _now(now)
    if _office_place(data.get("to")) and _no_office_allowed(plugin, now):
        if readonly:
            return  # GET 纯只读：这里只跳过，不取消、不写库
        logger.warning("[amo] 取消去公司的过渡（病假/夜间/工作日下班后）：to=%s hour=%s",
                       data.get("to"), now.hour)
        data["cancelled"] = True
        data["blocks_written"] = True
        _kv_set(plugin, STATE_KEY, {})
        _kv_set(plugin, "activity_now", {})
        return
    started = float(data.get("started_at") or 0)
    total_planned = total_minutes(data.get("steps") or [])
    if started and now is not None and (now.timestamp() - started) > (total_planned + 15) * 60:
        if readonly:
            return
        logger.warning("[amo] 过渡已过期（%.0f 分钟没落地），丢弃：%s→%s",
                       (now.timestamp() - started) / 60.0, data.get("from"), data.get("to"))
        data["cancelled"] = True
        data["blocks_written"] = True
        _kv_set(plugin, STATE_KEY, {})
        _kv_set(plugin, "activity_now", {})
        return
    total = total_planned
    if total < MIN_BLOCK_MINUTES or now is None:
        data["blocks_written"] = True
        _kv_set(plugin, STATE_KEY, data)
        return
    day = now.strftime("%Y-%m-%d")
    items = _state_items(plugin, day)
    now_text = now.strftime("%H:%M")
    stage = stage_at(data, now) or {}
    first_index = int(stage.get("index") or 0)
    # 只保留 now 之前的块；正在走的这块在 now 截断（「刚下车」推进时不重叠、不断档）
    kept = []
    for row in items:
        if str(row.get("source") or "") == "transit":
            start = str(row.get("start") or "")
            if start >= now_text:
                continue
            if str(row.get("end") or "") > now_text:
                row = dict(row)
                row["end"] = now_text
        kept.append(row)
    items = kept
    added = []
    cursor = 0.0
    for offset, step in enumerate((data.get("steps") or [])[first_index:]):
        index = first_index + offset
        length = max(1, int(step.get("minutes") or 0))
        if offset == 0 and stage.get("remaining"):
            length = max(1, int(stage.get("remaining") or length))
        start_text = _clock(now, cursor)
        end_text = "24:00" if start_text == "23:59" else _clock(now, cursor + length)
        added.append({
            "start": start_text, "end": end_text,
            "activity": step.get("action") or step.get("name"),
            "place": step.get("place") or HOME, "zone": step.get("zone") or "home",
            "category": "commute" if data.get("kind") == "commute" else str(data.get("kind") or "life"),
            "source": "transit", "step": index, "transit_kind": data.get("kind"),
            "happened": True,
        })
        cursor += length
    if added:
        _commit(plugin, day, items + added, items)
    data["blocks_written"] = True
    data["block_day"] = day
    _kv_set(plugin, STATE_KEY, data)


def note_short(plugin, detail, now=None):
    """<15 分钟的短动作不独立成块，写进当前小时的 detail 备注。"""
    text = str(detail or "").strip()
    if not text:
        return None
    now = _now(now)
    day = now.strftime("%Y-%m-%d")
    items = _state_items(plugin, day)
    patched = []
    touched = False
    for item in items:
        row = dict(item)
        if str(row.get("start") or "") <= now.strftime("%H:%M") and not touched:
            detail_text = str(row.get("detail") or "")
            note = "（期间%s）" % text
            if note not in detail_text:
                row["detail"] = (detail_text + note).strip()
                touched = True
        patched.append(row)
    if touched:
        _commit(plugin, day, patched, items)
    return text


def advance(plugin, now=None, reason="用户说到了"):
    """「刚下车/到楼下了」：截断当前块，立刻推进到下一块。"""
    now = _now(now)
    data = _kv_get(plugin, STATE_KEY, {}) or {}
    if not isinstance(data, dict) or not data.get("steps"):
        return None
    steps = list(data.get("steps") or [])
    stage = stage_at(data, now)
    if not stage or stage.get("arrived"):
        return None
    index = int(stage.get("index") or 0)
    if index >= len(steps) - 1:
        # 最后一段说到站：把状态拨到「已到达」，截断旧块并落到达块
        total = total_minutes(steps)
        data["started_at"] = float(now.timestamp()) - total * 60.0
        data["blocks_written"] = True
        _kv_set(plugin, STATE_KEY, data)
        arrived = stage_at(data, now)
        finish(plugin, data, arrived, now)
        return arrived
    # 把已用时间补到当前步结束，让下一块从 now 开始
    elapsed = float(stage.get("elapsed") or 0)
    consumed = sum(max(1, int(s.get("minutes") or 0)) for s in steps[:index])
    remain = max(0.0, consumed + max(1, int(steps[index].get("minutes") or 0)) - elapsed)
    data["started_at"] = float(now.timestamp()) - (elapsed + remain) * 60.0
    data["blocks_written"] = False
    data["advance_reason"] = str(reason or "")
    _kv_set(plugin, STATE_KEY, data)
    logger.info("[amo] 过渡推进：%s（%s）", stage.get("name"), reason)
    return stage_at(data, now)


def finish(plugin, transition=None, stage=None, now=None):
    """到达：写到达块、记一条事实、清状态。"""
    data = transition if isinstance(transition, dict) else (_kv_get(plugin, STATE_KEY, {}) or {})
    now = _now(now)
    if _office_place(data.get("to")) and _no_office_allowed(plugin, now):
        logger.warning("[amo] 到站后不写公司活动块（病假/夜间/工作日下班后）：to=%s hour=%s",
                       data.get("to"), now.hour)
        _kv_set(plugin, STATE_KEY, {})
        _kv_set(plugin, "activity_now", {})
        _drop_future_transit(plugin, now)
        return None
    stage = stage or stage_at(data, now) or {}
    day = now.strftime("%Y-%m-%d") if now else ""
    to_place = str(data.get("to") or "")
    elapsed = int(round(float(stage.get("elapsed") or data.get("minutes") or 0)))
    item = {
        "start": now.strftime("%H:%M") if now else "", "end": "",
        "activity": stage.get("activity") or _arrival_activity(to_place),
        "place": to_place or (stage.get("place") or HOME),
        "zone": zone_for_place(to_place) or (stage.get("zone") or "home"),
        "category": str(data.get("category") or data.get("kind") or "life"),
        "source": "transit", "step": "arrive", "happened": True,
    }
    # 先定好 end 再落库：以前先写 end="" 的到达块、再改内存对象不提交，
    # 线上就留下 19:06 end="" 的坏行（bad_time / 阻断 JIT 与块日记）。
    _chain = str(data.get("then_kind") or "")
    import datetime as _dt
    arrive_end = ((now + _dt.timedelta(minutes=5)).strftime("%H:%M")
                  if now is not None else "")
    if arrive_end:
        item["end"] = arrive_end
    written = False
    try:
        if day:
            items = _state_items(plugin, day)
            now_text = now.strftime("%H:%M")
            # 还没开始的过渡块会被到达块取代（时间连续、不重叠）
            kept = [row for row in items
                    if not (str(row.get("source") or "") == "transit"
                            and str(row.get("start") or "") >= now_text)]
            if item.get("end"):
                # 到达块不能越过紧随其后的块：取两者更早者，保证首尾相接/0 重叠。
                _next_start = min(
                    (str(row.get("start") or "") for row in kept
                     if now_text < str(row.get("start") or "") < str(item["end"])),
                    default="",
                )
                if _next_start:
                    item["end"] = _next_start
            _commit(plugin, day, kept + [item], items)
            written = True
    except Exception as exc:
        logger.debug("[amo] 过渡到达写日程失败: %s", exc)
    if _chain and day and now is not None:
        arrive_end = str(item.get("end") or "")
        activity_text = str(data.get("then_activity") or
                            ("和柳如烟逛街" if _chain == "shopping" else "在工位上忙工作"))
        duration = max(15, int(data.get("then_minutes") or (180 if _chain == "shopping" else 120)))
        follow_start = arrive_end
        follow_end = (now + _dt.timedelta(minutes=5 + duration)).strftime("%H:%M")
        follow = {"start": follow_start, "end": follow_end, "activity": activity_text,
                  "place": to_place,
                  "zone": zone_for_place(to_place) or "home",
                  "category": "play" if _chain == "shopping" else "work",
                  "source": "activity", "step": "activity", "happened": True}
        try:
            items2 = _state_items(plugin, day)
            _commit(plugin, day, items2 + [follow], items2)
        except Exception as exc:
            logger.debug("[amo] 活动块写入失败: %s", exc)
        _kv_set(plugin, "activity_now", {
            "started_at": (now + _dt.timedelta(minutes=5)).timestamp(),
            "minutes": duration, "activity": activity_text, "place": to_place,
            "zone": follow["zone"], "category": follow["category"], "kind": _chain,
        })
        logger.info("[amo] 到达后进入活动块：%s（%d 分钟）", activity_text, duration)
    fact = "折腾了%d分钟才到%s。" % (elapsed, to_place) if to_place else "折腾了%d分钟才忙完。" % elapsed
    _kv_set(plugin, "transit:last", {"to": to_place, "elapsed": elapsed,
                                     "fact": fact, "at": _amo_clock.now_ts()})
    try:
        writer = getattr(plugin, "_data_call", None)
        spawn = getattr(plugin, "_spawn", None)
        bot_id = "amo"
        if callable(getattr(plugin, "_data_bot_id", None)):
            bot_id = plugin._data_bot_id()
        if callable(writer) and callable(spawn):
            spawn(writer("remember", bot_id, "amo", fact, kind="activity", importance=0.4))
    except Exception as exc:
        logger.debug("[amo] 过渡到达写记忆失败: %s", exc)
    _kv_set(plugin, STATE_KEY, {})
    try:
        _spawn_diary(plugin, day, item)
    except Exception as _amo_exc:
        logger.debug("[amo] 过渡完成触发小日记失败: %s", _amo_exc)
    if not _chain:
        _kv_set(plugin, "activity_now", {})
    logger.info("[amo] 过渡完成：%s（%d 分钟，日程写库=%s）", to_place, elapsed, written)
    return fact


def current_activity(plugin, now=None):
    """读侧入口：过渡中返回当前 step；到达后返回活动块；都过期就 None。"""
    data = current(plugin, now)
    if not data:
        state_now = _kv_get(plugin, "activity_now", {}) or {}
        if isinstance(state_now, dict) and state_now.get("activity"):
            now = _now(now)
            try:
                start_at = float(state_now.get("started_at") or 0)
                minutes = float(state_now.get("minutes") or 0)
                end_at = start_at + minutes * 60.0
                if now is not None and start_at and now.timestamp() < end_at:
                    return {"activity": state_now["activity"],
                            "place": state_now.get("place") or "",
                            "zone": state_now.get("zone") or "",
                            "category": state_now.get("category") or "life",
                            "transit": False, "_source": "activity"}
                if start_at and minutes and now is not None and now.timestamp() >= end_at:
                    try:
                        _start_dt = __import__("datetime").datetime.fromtimestamp(start_at)
                        write_activity_memory(plugin, [{
                            "start": _start_dt.strftime("%H:%M"),
                            "end": now.strftime("%H:%M"),
                            "activity": state_now.get("activity"),
                            "place": state_now.get("place"),
                            "category": state_now.get("category"), "truncated": True,
                        }], now)
                    except Exception as _amo_exc:
                        logger.debug("[amo] 到期活动写记忆失败: %s", _amo_exc)
                    _kv_set(plugin, "activity_now", {})
            except Exception as _amo_exc:
                logger.debug("[amo] 活动状态读取失败: %s", _amo_exc)
        return None
    stage = stage_at(data, now)
    if not stage or stage.get("arrived"):
        return None
    return {
        "activity": stage["activity"], "place": stage["place"], "zone": stage["zone"],
        "category": "commute" if data.get("kind") == "commute" else str(data.get("kind") or "life"),
        "transit": True, "transit_kind": data.get("kind"), "transit_step": stage["index"],
        "transit_remaining": stage["remaining"], "_source": "transit",
        "_reason": "过渡中：%s→%s" % (data.get("from"), data.get("to")),
    }


def prompt_block(plugin, now=None):
    data = current(plugin, now)
    if not data:
        return ""
    stage = stage_at(data, now)
    if not stage or stage.get("arrived"):
        return ""
    lines = [
        "【过渡状态·最高优先级】" + TRANSIT_RULE,
        "【当前步骤】%d/%d %s：%s，地点=%s，还剩约 %d 分钟。"
        % (stage["index"] + 1, len(data.get("steps") or []), stage["name"],
           stage["activity"], stage["place"], stage["remaining"]),
        EMOTION_HINT.get(str(data.get("emotion") or "reluctant"), EMOTION_HINT["reluctant"]),
    ]
    return "\n".join(lines)




# ---------- 第七轮：重叠互斥 + doing 标记 ----------


def _minute(text):
    body = str(text or "").strip()[:5]
    if body in ("24:00", "0:00", "00:00"):
        return 24 * 60
    try:
        hour_text, minute_text = body.split(":")[:2]
        return int(hour_text) * 60 + int(minute_text)
    except Exception:
        return None


def _minute_text(value):
    value = max(0, min(24 * 60, int(value)))
    if value >= 24 * 60:
        return "24:00"
    return "%02d:%02d" % (value // 60, value % 60)


def _span(item):
    if not isinstance(item, dict):
        return None
    begin = _minute(item.get("start"))
    if begin is None:
        return None
    finish = _minute(item.get("end"))
    if finish is None or finish <= begin:
        finish = 24 * 60 if begin >= 23 * 60 else begin + 60
    return begin, finish


def covers_span(item, start_min, end_min):
    span = _span(item)
    if not span:
        return False
    begin, finish = span
    return begin <= start_min and finish >= end_min


def overlaps(items, start, end):
    """按分钟返回与 [start,end) 重叠的日程条目。"""
    start_min, end_min = _minute(start), _minute(end)
    if start_min is None or end_min is None or end_min <= start_min:
        return []
    hits = []
    for item in items or []:
        span = _span(item)
        if not span:
            continue
        begin, finish = span
        if begin < end_min and finish > start_min:
            hits.append(item)
    return hits


def resolve_overlaps(old_items, new_items):
    """旧块优先：新块与 happened 旧块重叠时截断到空档（或丢弃）。返回 (items, trimmed)。"""
    old = [row for row in (old_items or [])
           if isinstance(row, dict) and row.get("happened")]
    kept, trimmed = [], []
    for item in new_items or []:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        span = _span(item)
        if not span:
            kept.append(item)
            continue
        begin, finish = span
        gaps = [(begin, finish)]
        for blocker in old:
            block_span = _span(blocker)
            if not block_span:
                continue
            b0, b1 = block_span
            nxt = []
            for g0, g1 in gaps:
                if b1 <= g0 or b0 >= g1:
                    nxt.append((g0, g1))
                    continue
                if b0 > g0:
                    nxt.append((g0, b0))
                if b1 < g1:
                    nxt.append((b1, g1))
            gaps = nxt
            if not gaps:
                break
        if gaps == [(begin, finish)]:
            kept.append(item)
            continue
        trimmed.append(item)
        for g0, g1 in gaps:
            if g1 - g0 < 5:
                continue
            row = dict(item)
            row["start"] = _minute_text(g0)
            row["end"] = _minute_text(g1)
            kept.append(row)
    return kept, trimmed


def _day_start_minute(value):
    """00:00 是当天起点（_minute 把 00:00 当 24:00 是给 end 用的）。"""
    body = str(value or "").strip()[:5]
    if body in ("0:00", "00:00"):
        return 0
    return _minute(value)


def _day_span(item):
    """按「一天之内」解析 start/end：00:00 排最前，24:00 排最后。"""
    if not isinstance(item, dict):
        return None
    begin = _day_start_minute(item.get("start"))
    if begin is None:
        return None
    finish = _minute(item.get("end"))
    if finish is None or finish <= begin:
        finish = 24 * 60 if begin >= 23 * 60 else begin + 60
    return begin, finish


def resolve_new_overlaps(new_items):
    """新增块自身去重叠（分钟级）：按 start 排序，后块 start 截到前块 end；
    被完全覆盖的块丢弃；截断后不足 5 分钟的碎片并入前块。返回 (items, trimmed)。

    为什么还要这一步：commit 的外部 blocker 只算「不在 new 列表里的旧块」，
    所以「原样旧块 + 一条重叠追加」时旧块被当成同批过滤掉，追加块就没人拦，
    重叠直接落库。这里把 new_items 自己也按时间轴压直，是外部去重叠之后的第二道闸。
    """
    rows = []
    for index, item in enumerate(new_items or []):
        if not isinstance(item, dict):
            rows.append((None, index, item))
            continue
        rows.append((_day_span(item), index, item))
    rows.sort(key=lambda trio: (trio[0][0] if trio[0] else 24 * 60, trio[1]))
    kept, trimmed = [], []
    for span, _index, item in rows:
        row = dict(item) if isinstance(item, dict) else item
        if span and kept and isinstance(kept[-1], dict):
            prev_end = _minute(kept[-1].get("end"))
            begin, finish = span
            if prev_end is not None and begin < prev_end:
                trimmed.append(item)
                if finish <= prev_end:
                    continue
                row["start"] = _minute_text(prev_end)
                tail = _minute(row.get("end"))
                if tail is not None and tail - prev_end < 5:
                    kept[-1]["end"] = row.get("end")
                    continue
        kept.append(row)
    return kept, trimmed


# ---------- 第十轮 B1：跨地点切换写侧兜底 ----------


def _place_zone(place):
    try:
        return str(zone_for_place(place) or "")
    except Exception:
        return ""


def _is_home_place(place):
    """家/卧室/客厅/沙发/浴室这种「家内部」地点：来回走不用通勤块。"""
    text = str(place or "").strip()
    if not text:
        return False
    if text in INDOOR_PLACES:
        return True
    return _place_zone(text) == "home"


def _is_known_place(place):
    """只给认识的地点补通勤块：脏数据/随便一个词不当成跨地点。"""
    text = str(place or "").strip()
    return bool(text) and (text in TRANSIT_KNOWN_PLACES or bool(_place_zone(text)))


def transit_minutes(day, start_min, from_place, to_place):
    """两地点之间的路程分钟；同一段（day+start+两端）永远同一个数。

    回家：按出发地点给 15-40 分钟（医院 25-35、商场 20-30、公司 15-40）；
    从家出门：沿用现有去程规则 DESTINATION_MINUTES；
    其它跨地点：15-40；家内部移动（客厅→卧室）返回 0。
    """
    from_place = str(from_place or "").strip()
    to_place = str(to_place or "").strip()
    if not from_place or not to_place or from_place == to_place:
        return 0
    from_home = _is_home_place(from_place)
    to_home = _is_home_place(to_place)
    if from_home and to_home:
        return 0
    if from_home:
        base = int(DESTINATION_MINUTES.get(to_place, 20))
        return max(TRANSIT_MIN_MINUTES, min(TRANSIT_MAX_MINUTES, base))
    if to_home:
        low, high = TRANSIT_RETURN_RANGES.get(from_place, TRANSIT_OTHER_RANGE)
    else:
        low, high = TRANSIT_OTHER_RANGE
    rng = random.Random("transit|%s|%s|%s|%s" % (
        str(day or ""), _minute_text(int(start_min)), from_place, to_place))
    value = rng.randint(min(low, high), max(low, high))
    return max(TRANSIT_MIN_MINUTES, min(TRANSIT_MAX_MINUTES, int(value)))


def _needs_transit_block(prev, nxt):
    if not isinstance(prev, dict) or not isinstance(nxt, dict):
        return False
    prev_place = str(prev.get("place") or "").strip()
    next_place = str(nxt.get("place") or "").strip()
    if not prev_place or not next_place or prev_place == next_place:
        return False
    if not (_is_known_place(prev_place) and _is_known_place(next_place)):
        return False
    if _is_home_place(prev_place) and _is_home_place(next_place):
        return False
    if str(prev.get("zone") or "") == "road" or _place_zone(prev_place) == "road":
        return False
    if str(nxt.get("zone") or "") == "road" or _place_zone(next_place) == "road":
        return False
    return True


def _transit_row(day, start_min, minutes, prev, nxt, now_min):
    from_place = str(prev.get("place") or "").strip()
    to_place = str(nxt.get("place") or "").strip()
    end_min = max(int(start_min) + 1, min(24 * 60, int(start_min) + int(minutes)))
    to_home = _is_home_place(to_place)
    from_home = _is_home_place(from_place)
    if to_home:
        vehicle = _VEHICLE_RETURN.get(from_place) or vehicle_for(from_place, minutes)
    else:
        vehicle = vehicle_for(to_place, minutes)
    ride = {"出租车": "打车", "地铁": "坐地铁", "公交": "坐公交"}.get(vehicle, "赶路")
    mode = _TRANSIT_MODE_BY_VEHICLE.get(str(vehicle), "")
    if to_home:
        activity = "刚出%s，%s回家（在路上）" % (from_place, ride)
        kind = "return"
    elif from_home:
        activity = "出门去%s，%s（在路上）" % (to_place, ride)
        kind = "outbound"
    else:
        activity = "从%s去%s，%s（在路上）" % (from_place, to_place, ride)
        kind = "transfer"
    return {
        "start": _minute_text(int(start_min)), "end": _minute_text(end_min),
        "activity": activity, "place": "路上", "zone": "road",
        "vehicle": vehicle, "category": "commute",
        "source": "transit_block",
        "transit": mode or kind, "transit_kind": kind,
        "happened": int(start_min) <= int(now_min),
    }


def _transit_order(rows):
    order = []
    for index, row in enumerate(rows):
        span = _day_span(row)
        if span:
            order.append((span[0], span[1], index))
    order.sort()
    return order


def _push_after(rows, order, from_pos, floor_min):
    """通勤块占掉空档后，把还没开始的块依次后移，保持时间轴严格递增。"""
    cursor = int(floor_min)
    for start, end, index in order[from_pos:]:
        if start >= cursor:
            cursor = end
            continue
        duration = max(1, end - start)
        new_start = cursor
        new_end = min(24 * 60, new_start + duration)
        if new_start >= 24 * 60 or new_end - new_start < 5:
            rows[index] = None
            logger.warning("[amo] 通勤块放不下，丢弃被挤掉的日程块 day_start=%s",
                           _minute_text(start))
            continue
        row = rows[index]
        if isinstance(row, dict):
            row["start"] = _minute_text(new_start)
            row["end"] = _minute_text(new_end)
        logger.info("[amo] 通勤块占位：日程块 %s 顺延到 %s", _minute_text(start),
                    _minute_text(new_start))
        cursor = new_end


def _has_overlap(rows):
    spans = sorted(span for span in (_day_span(row) for row in rows) if span)
    return any(left[1] > right[0] for left, right in zip(spans, spans[1:]))


def _row_signature(row):
    if not isinstance(row, dict):
        return None
    return (str(row.get("start") or ""), str(row.get("end") or ""),
            str(row.get("activity") or ""), str(row.get("place") or ""))


def insert_transit_blocks(day, items, old_items=None, now=None):
    """写侧兜底：刚写入的块与按开始时间紧邻的上一块 place 不同时，补一段通勤块。

    - 只在「新旧有变化的那条边界」插，避免整份重排时把前后无关的块也改掉；
    - 时长按 day+start 稳定取整，刷新/重载不变；
    - 空档放不下最短 10 分钟时把后块（及其后重叠的块）顺延，绝不省掉通勤；
    - 已有路上块夹在中间时不再重复插（幂等）。
    返回 (rows, inserted)。
    """
    rows = [dict(row) if isinstance(row, dict) else row for row in (items or [])]
    old_keys = {_row_signature(row) for row in (old_items or [])}
    old_keys.discard(None)
    now = _now(now)
    now_min = int(now.hour) * 60 + int(now.minute) if now is not None else 0
    inserted = []
    order = _transit_order(rows)
    cursor = 0
    guard = 0
    while cursor < len(order) - 1 and guard < 512:
        guard += 1
        prev_span, next_span = order[cursor], order[cursor + 1]
        prev, nxt = rows[prev_span[2]], rows[next_span[2]]
        touched = (_row_signature(prev) not in old_keys
                   or _row_signature(nxt) not in old_keys)
        minutes = 0
        if (touched and _needs_transit_block(prev, nxt)
                and prev_span[1] + TRANSIT_MIN_MINUTES <= 24 * 60):
            # 当天剩余时间不够一段合法通勤就不插：避免 1 分钟碎片和同分钟两个地点
            minutes = min(
                transit_minutes(day, prev_span[1], prev.get("place"), nxt.get("place")),
                24 * 60 - prev_span[1],
            )
        if not minutes:
            cursor += 1
            continue
        block = _transit_row(day, prev_span[1], minutes, prev, nxt, now_min)
        rows.append(block)
        inserted.append(block)
        logger.info("[amo] 跨地点补通勤块 %s-%s：%s", block["start"], block["end"],
                    block["activity"])
        _push_after(rows, order, cursor + 1, prev_span[1] + minutes)
        order = _transit_order(rows)
        cursor = 0
    rows = [row for row in rows if row is not None]
    rows.sort(key=lambda row: (_day_span(row)[0] if _day_span(row) else 24 * 60))
    if inserted and _has_overlap(rows):
        # 硬兜底：宁可把重叠块压直，也绝不让重叠落库
        logger.error("[amo] 通勤块插入后仍有重叠，已二次压直 day=%s", day)
        rows, _trimmed = resolve_new_overlaps(rows)
    # T1：通勤块也是路上块，插完按文案统一 activity/vehicle/transit，别让两种交通方式并存。
    try:
        try:
            from .schedule_slots import ScheduleSlotsMixin as _slots
        except ImportError:
            from schedule_slots import ScheduleSlotsMixin as _slots
        rows = _slots._sync_transit_vehicle(rows)
    except Exception as _amo_exc:
        logger.debug("[amo] 通勤块 activity/vehicle 收口失败：%s", _amo_exc)
    return rows, inserted


def _row_is_road(row):
    place = str(row.get("place") or "").strip()
    return (place == "路上" or str(row.get("zone") or "") == "road"
            or _place_zone(place) == "road")


def _trip_generated_rows(rows):
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("source") or "") in ("trip_pool", "trip_rest", "transit"):
            return True
        if str(row.get("trip") or "").strip():
            return True
    return False


def _return_leg_source(rows, home_row):
    """回程起点：同地点连续段里最大的 end（嵌套子块视为同一次活动）。"""
    home_span = _day_span(home_row)
    if not home_span:
        return None
    home_start = home_span[0]
    best = None
    for row in rows:
        if row is home_row or not isinstance(row, dict):
            continue
        if _is_home_place(row.get("place")) or _row_is_road(row):
            continue
        if not _is_known_place(row.get("place")):
            continue
        span = _day_span(row)
        if not span or span[1] > home_start:
            continue
        if best is None or span[1] > best[1][1]:
            best = (row, span)
    return best


def _road_between(rows, floor_min, home_start):
    for row in rows:
        if not isinstance(row, dict) or not _row_is_road(row):
            continue
        span = _day_span(row)
        if not span:
            continue
        if span[0] <= home_start and span[1] > floor_min:
            return True
    return False


def _home_already_before(rows, home_row, floor_min):
    home_span = _day_span(home_row)
    if not home_span:
        return False
    for row in rows:
        if row is home_row or not isinstance(row, dict):
            continue
        if not _is_home_place(row.get("place")):
            continue
        span = _day_span(row)
        if not span:
            continue
        if span[0] < home_span[0] and span[1] > floor_min:
            return True
    return False


def _shift_home_and_after(rows, home_start, floor_min):
    """只顺延家块及其后的块；活动块（含嵌套子块）的 start 一律不动。"""
    ordered = sorted(
        [row for row in rows if isinstance(row, dict)],
        key=lambda row: (_day_span(row) or (24 * 60, 24 * 60))[0])
    cursor = int(floor_min)
    dropped = []
    for row in ordered:
        span = _day_span(row)
        if not span or span[0] < home_start:
            continue
        duration = max(1, span[1] - span[0])
        new_start = max(span[0], cursor)
        new_end = min(24 * 60, new_start + duration)
        if new_end <= new_start:
            # 今天剩下的时间放不下这一段：宁可丢掉它，也不能留下和回程块重叠的幽灵块
            dropped.append(row)
            continue
        row["start"] = _minute_text(new_start)
        row["end"] = _minute_text(new_end)
        cursor = new_end
    if dropped:
        drop_ids = {id(row) for row in dropped}
        rows[:] = [row for row in rows if id(row) not in drop_ids]


def _has_blocking_overlap(rows):
    """只把「部分交叠」当重叠；完全嵌套的子块（同一活动）不算。"""
    spans = sorted(span for span in (_day_span(row) for row in rows) if span)
    for index, (start, finish) in enumerate(spans):
        for other_start, other_finish in spans[index + 1:]:
            if other_start >= finish:
                break
            if (start <= other_start and other_finish <= finish) or \
                    (other_start <= start and finish <= other_finish):
                continue
            return True
    return False


def repair_cross_place_blocks(plugin, day, items=None, now=None):
    """读/加载时只补「回程」：从外面回家时在活动段末尾插一段路上块。

    不改写去医院/去商场的出发时间（历史小日记/记忆不能对不上）；嵌套子块
    视为同一次活动（取同地点连续段的最大 end 作为回程起点）。只修今天；
    旅游自己写的块（trip/transit）不重复修。返回 (rows, inserted)。
    """
    day = str(day or "")
    now = _now(now)
    today = now.strftime("%Y-%m-%d") if now is not None else ""
    if not day:
        return [], []
    rows = items
    if rows is None:
        rows = []
        state = getattr(plugin, "state", None)
        try:
            if state is not None:
                rows = list((getattr(state, "schedule", {}) or {}).get(day) or [])
        except Exception:
            rows = []
        if not rows:
            try:
                rows = list(plugin.db.get_schedule(day) or [])
            except Exception:
                rows = []
    rows = [dict(row) if isinstance(row, dict) else row for row in (rows or [])]
    if today and day != today:
        return rows, []
    if _trip_generated_rows(rows):
        return rows, []
    now_min = int(now.hour) * 60 + int(now.minute) if now is not None else 0
    inserted = []
    guard = 0
    while guard < 32:
        guard += 1
        target = None
        ordered = sorted(
            [row for row in rows if isinstance(row, dict)],
            key=lambda row: (_day_span(row) or (24 * 60, 24 * 60))[0])
        for home_row in ordered:
            if not _is_home_place(home_row.get("place")):
                continue
            home_span = _day_span(home_row)
            if not home_span:
                continue
            source = _return_leg_source(rows, home_row)
            if source is None:
                continue
            place_row, place_span = source
            if _road_between(rows, place_span[1], home_span[0]):
                continue
            if _home_already_before(rows, home_row, place_span[1]):
                continue
            available = 24 * 60 - place_span[1]
            if available < TRANSIT_MIN_MINUTES:
                # 今天已经放不下一段合法回程：保持原样，绝不再插 1 分钟/重复块
                break
            minutes = min(
                transit_minutes(day, place_span[1], place_row.get("place"),
                                home_row.get("place")),
                available,
            )
            if minutes < TRANSIT_MIN_MINUTES:
                break
            target = (home_row, place_row, place_span[1], minutes)
            break
        if target is None:
            break
        home_row, place_row, start_min, minutes = target
        block = _transit_row(day, start_min, minutes, place_row, home_row, now_min)
        home_start = (_day_span(home_row) or (start_min, start_min))[0]
        _shift_home_and_after(rows, home_start, start_min + minutes)
        rows.append(block)
        inserted.append(block)
        logger.info("[amo] 回程通勤修复 %s-%s：%s", block["start"],
                    block["end"], block["activity"])
    if inserted and _has_blocking_overlap(rows):
        logger.error("[amo] 回程修复后仍部分重叠，放弃本次修复 day=%s", day)
        return [dict(row) for row in (items or []) if isinstance(row, dict)], []
    rows.sort(key=lambda row: (_day_span(row) or (24 * 60, 24 * 60))[0])
    return rows, inserted


def normalize_day(items):
    """把一天里互相重叠的块压成一条直线（读侧兜底）：较早的块优先，后块起点截到前块 end；
    完全被覆盖的块丢弃；截断后不足 5 分钟的碎片并入前块。返回 (kept, changed)。"""
    rows = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        span = _span(item)
        if not span:
            rows.append((None, item))
            continue
        rows.append((span, item))
    rows.sort(key=lambda pair: (pair[0][0] if pair[0] else 24 * 60, str(pair[1].get("start") or "")))
    kept = []
    changed = False
    for span, item in rows:
        row = dict(item)
        if span and kept:
            prev_end = _minute(kept[-1].get("end"))
            begin, finish = span
            if prev_end is not None and begin < prev_end:
                changed = True
                if finish <= prev_end:
                    # 整段被前块覆盖：丢弃（除非是当前 doing 的过渡块）
                    continue
                row["start"] = _minute_text(prev_end)
                if _minute(row["end"]) - prev_end < 5 and kept:
                    kept[-1]["end"] = row["end"]
                    continue
        kept.append(row)
    return kept, changed


def mark_doing(items, now=None):
    """给覆盖当前分钟（按分钟，不是整点）的条目打 doing/current。"""
    now = _now(now)
    if now is None:
        return items
    now_min = now.hour * 60 + now.minute
    for item in items or []:
        if isinstance(item, dict) and covers_span(item, now_min, now_min + 1):
            item["doing"] = True
            item["current"] = True
    return items


def mark_doing_tree(tree, now=None):
    for block in tree or []:
        if not isinstance(block, dict):
            continue
        children = block.get("children") or []
        mark_doing(children, now)
        if any(child.get("current") for child in children if isinstance(child, dict)):
            block["current"] = True
        if children:
            block["doing"] = block.get("doing") or any(
                child.get("doing") for child in children if isinstance(child, dict))
    return tree




# ---------- 第八轮 B2：有剧情的活动块写长期记忆 ----------
import re

ACTIVITY_MEM_KEY = "activity_mem_done"
ACTIVITY_MEM_FALLBACK_KEY = "activity_mem_fallback"
#: 想记不认 tags 时把 event_id 落这里，标签没写进去也要能查回来，不静默丢
ACTIVITY_MEM_TAG_LOST_KEY = "activity_mem_tag_lost"
MEM_CATEGORIES = {"social", "date", "appointment", "health", "shopping",
                  "errand", "travel", "outing", "play"}
MEM_WORDS = ("逛街", "购物", "买东西", "电影", "看剧", "约会", "看病", "医院",
             "挂号", "看诊", "旅游", "聚会", "聚餐", "办事", "商场", "出游")
MEM_SKIP_WORDS = ("发呆", "休息", "睡觉", "躺着", "窝在", "在家", "通勤",
                  "地铁", "路上", "赶路", "洗澡", "洗漱")
MEM_SKIP_CATEGORIES = {"sleep", "rest"}


def _memory_duration(minutes):
    minutes = max(0, int(minutes))
    if minutes >= 120 and minutes % 60 == 0:
        return "%d个小时" % (minutes // 60)
    if minutes % 60 == 0:
        return "%d小时" % (minutes // 60)
    if minutes == 90:
        return "1个半小时"
    if minutes == 150:
        return "2个半小时"
    if minutes % 30 == 0 and minutes > 30:
        return "%d个半小时" % (minutes // 60)
    return "%d分钟" % minutes


def activity_memory_text(start, end, activity, place, category=""):
    """白名单：只有有剧情的活动才进长期记忆；返回人话内容，不合格返回空串。"""
    text = str(activity or "").strip()
    if not text:
        return ""
    category = str(category or "").strip().lower()
    if category in MEM_SKIP_CATEGORIES:
        return ""
    if any(word in text for word in MEM_SKIP_WORDS):
        return ""
    if ("吃饭" in text or "吃午饭" in text or "吃晚饭" in text) and not (
            "聚餐" in text or "约会" in text):
        return ""
    if not (category in MEM_CATEGORIES or any(word in text for word in MEM_WORDS)):
        return ""
    try:
        begin = _minute(start)
        finish = _minute(end)
    except Exception:
        return ""
    if begin is None or finish is None or finish <= begin:
        return ""
    if begin < 11 * 60:
        period = "上午"
    elif begin < 13 * 60:
        period = "中午"
    elif begin < 18 * 60:
        period = "下午"
    else:
        period = "晚上"
    matched = re.search(r"和([^\s，,。！!？?、]{1,6})", text)
    who = ("和%s" % matched.group(1)) if matched else ""
    where = ("在%s" % place) if str(place or "").strip() else ""
    return "%s%s%s待了%s" % (period, who, where, _memory_duration(finish - begin))


def _memory_uid(plugin):
    for getter in ("_memory_uid", "owner_uid", "_owner_uid"):
        value = getattr(plugin, getter, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        if value:
            return str(value)
    try:
        return str(plugin.db.kv_get("owner_uid", "") or "")
    except Exception:
        return ""


def _memory_coro(plugin, uid, text, idem, event_id=""):
    """优先走想记的 _data_memory_write（支持 tags/idem_key/importance），没有就退回 _write_memory_checked。

    tags 里带 "event:<id>"：这条长期记忆和日程块 / 账本 / 块日记说的是同一个事件。
    想记旧版不认 tags 时不硬造：记降级次数 + 把 id 存 ACTIVITY_MEM_TAG_LOST_KEY 留档。
    """
    event_id = str(event_id or "").strip()
    tags = "#活动" + ((" event:%s" % event_id) if event_id else "")
    writer = getattr(plugin, "_data_memory_write", None)
    if callable(writer):
        try:
            return writer(uid, text, "activity", 1, "private", "", subject="bot",
                          idem_key=idem, tags=tags, importance=0.25)
        except TypeError as exc:
            # 旧版想记没有 tags/idem_key/importance：记一笔降级次数，别静默丢标签/权重
            logger.warning("[amo] 想记不支持 tags/idem_key/importance（event:%s），"
                           "退回通用写入口: %s", event_id or "-", exc)
            try:
                failed = int(_kv_get(plugin, ACTIVITY_MEM_FALLBACK_KEY, 0) or 0)
            except (TypeError, ValueError):
                failed = 0
            _kv_set(plugin, ACTIVITY_MEM_FALLBACK_KEY, failed + 1)
            if event_id:
                try:
                    lost = dict(_kv_get(plugin, ACTIVITY_MEM_TAG_LOST_KEY, {}) or {})
                    lost[str(idem)] = event_id
                    _kv_set(plugin, ACTIVITY_MEM_TAG_LOST_KEY, lost)
                except Exception as _exc:
                    logger.debug("[amo] 记录未写入的事件标签失败: %s", _exc)
    checked = getattr(plugin, "_write_memory_checked", None)
    if callable(checked):
        try:
            return checked(uid, text, "activity", 1, "private", "", subject="bot")
        except TypeError as exc:
            logger.debug("[amo] 通用写入口参数不匹配: %s", exc)
            return None
    return None


def write_activity_memory(plugin, rows, now=None):
    """活动块结束/被截断时，异步写一条长期记忆（幂等 idem，失败只记日志）。"""
    now = _now(now)
    if now is None:
        return []
    day = now.strftime("%Y-%m-%d")
    now_min = now.hour * 60 + now.minute
    try:
        done = _kv_get(plugin, ACTIVITY_MEM_KEY, {}) or {}
    except Exception:
        done = {}
    if not isinstance(done, dict):
        done = {}
    written = []
    uid = _memory_uid(plugin)
    spawn = getattr(plugin, "_spawn", None)
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        span = _span(row)
        if not span:
            continue
        begin, finish = span
        if finish > now_min and not row.get("truncated"):
            continue
        text = activity_memory_text(row.get("start"), row.get("end"),
                                    row.get("activity"), row.get("place"),
                                    row.get("category"))
        if not text:
            continue
        idem = "activity:%s:%s" % (day, str(row.get("start") or "")[:5])
        if done.get(idem):
            continue
        done[idem] = True
        eid = ""
        try:
            try:
                from .amo_timeline import ensure_event_id as _ensure_event_id
            except ImportError:
                from amo_timeline import ensure_event_id as _ensure_event_id
            eid = _ensure_event_id(day, row)
        except Exception as exc:
            logger.debug("[amo] 活动记忆取 event_id 失败: %s", exc)
        try:
            coro = _memory_coro(plugin, uid, text, idem, eid)
        except Exception as exc:
            logger.debug("[amo] 活动记忆构造失败: %s", exc)
            coro = None
        if coro is None:
            continue
        if callable(spawn):
            try:
                spawn(coro)
            except Exception as exc:
                logger.warning("[amo] 活动记忆入队失败（不影响活动收尾）: %s", exc)
                try:
                    coro.close()
                except Exception:  # cleanup
                    pass
                continue
        else:
            try:
                asyncio.get_running_loop().create_task(coro)
            except Exception:
                try:
                    coro.close()
                except Exception:  # cleanup
                    pass
        written.append(text)
        logger.info("[amo] 活动写进长期记忆（idem=%s）：%s", idem, text)
    if written:
        try:
            _kv_set(plugin, ACTIVITY_MEM_KEY, done)
        except Exception as exc:
            logger.debug("[amo] 活动记忆幂等表写入失败: %s", exc)
    return written


def _spawn_diary(plugin, day, item):
    """过渡/活动结束时把「生成这段小日记」丢给后台队列（GET 绝不跑 LLM）。"""
    state = getattr(plugin, "state", None)
    if state is None or not day or not isinstance(item, dict):
        return None
    spawn = getattr(state, "_spawn_diary_task", None)
    if not callable(spawn):
        return None
    try:
        items = _state_items(plugin, day)
        version = plugin.db.schedule_version(day)
        spawn(day, item, items, version, reason="transit_done")
    except Exception as exc:
        logger.debug("[amo] 后台小日记入队失败: %s", exc)
    return None


def truncate_open_activity(plugin, now=None):
    """新过渡启动时，把与 now 重叠的当前活动块在 now 截断（防幽灵日程尾巴）。"""
    now = _now(now)
    if now is None:
        return []
    day = now.strftime("%Y-%m-%d")
    now_text = now.strftime("%H:%M")
    now_min = now.hour * 60 + now.minute
    try:
        items = _state_items(plugin, day)
    except Exception as _amo_exc:
        logger.debug("[amo] 读活动块失败: %s", _amo_exc)
        return []
    kept, trimmed = [], []
    for row in items:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        source = str(row.get("source") or "")
        if source not in ("activity", "transit") or row.get("truncated"):
            kept.append(row)
            continue
        span = _span(row)
        if not span:
            kept.append(row)
            continue
        begin, finish = span
        if not (begin < now_min < finish):
            kept.append(row)
            continue
        remaining = finish - now_min
        if remaining < 5:
            trimmed.append(row)
            logger.info("[amo] 活动块剩余 %d 分钟，直接丢弃：%s", remaining, row.get("activity"))
            continue
        cut = dict(row)
        cut["end"] = now_text
        cut["truncated"] = True
        trimmed.append(row)
        kept.append(cut)
        logger.info("[amo] 活动块在 %s 截断（原至 %s）：%s", now_text, row.get("end"), row.get("activity"))
    if trimmed:
        try:
            _commit(plugin, day, kept, items)
        except Exception as exc:
            logger.debug("[amo] 活动块截断写库失败: %s", exc)
        try:
            write_activity_memory(plugin, [row for row in kept if row.get("truncated")], now)
        except Exception as exc:
            logger.debug("[amo] 截断活动写记忆失败: %s", exc)
    return trimmed


def guard_road_home(plugin, text, now=None):
    """在车上时不许说「到家了/瘫在沙发上」：交通还没结束，人还没进门。

    通勤块（place=路上/zone=road）覆盖当前分钟时生效；到站后（家/公司…）自动放行。
    进行中的过渡优先：state 的日程缓存可能还没刷新到「路上」块。
    """
    value = str(text or "")
    if not value:
        return value
    on_road = False
    try:
        transition = current(plugin, now)
        stage = stage_at(transition, now) if transition else None
        if stage and not stage.get("arrived") and str(stage.get("zone") or "") == "road":
            on_road = True
    except Exception as exc:
        logger.debug("[amo] 路上守卫读过渡失败: %s", exc)
    state = getattr(plugin, "state", None)
    if not on_road and state is not None:
        try:
            current_activity = state.current_activity() or {}
        except Exception as exc:
            logger.debug("[amo] 路上守卫读当前动作失败: %s", exc)
            current_activity = {}
        if isinstance(current_activity, dict):
            place = str(current_activity.get("place") or "").strip()
            zone = str(current_activity.get("zone") or "").strip() or _place_zone(place)
            if zone == "road" or place == "路上":
                on_road = True
    if not on_road:
        return value
    changed = False
    for old, new in _ROAD_HOME_TERMINAL:
        if old in value:
            value = value.replace(old, new)
            changed = True
    if changed:
        value = re.sub(r"(，\s*还在车上)+$", "", value).rstrip("，")
        logger.info("[amo] 路上终态守卫 -> %s", value[:60])
    return value

def guard_terminal(plugin, text, now=None):
    """确定性兜底：过渡/通勤期说终态（我在医院/到家了）→ 改成「在路上/快到了」。"""
    value = str(text or "")
    if not value:
        return value
    data = current(plugin, now)
    if not data:
        return guard_road_home(plugin, value, now)
    stage = stage_at(data, now)
    if not stage or stage.get("arrived"):
        return guard_road_home(plugin, value, now)
    target = str(data.get("to") or "")
    words = [word for word in DESTINATION_WORDS if word == target and word in value]
    changed = False
    replacement = "快到了" if stage["index"] >= 1 else "还在收拾，马上出门"
    for word in words:
        value = value.replace(word, replacement)
        changed = True
    # 洗澡/吃饭/睡觉：没走完不许说终态（「洗完了」→「还在洗澡呢，等下」）
    for old, new in KIND_TERMINAL.get(str(data.get("kind") or ""), ()):
        if old in value:
            value = value.replace(old, new)
            changed = True
    if changed:
        logger.info("[amo] 过渡期终态守卫 -> %s", value[:60])
    return guard_road_home(plugin, value, now)


def maybe_start(plugin, day, new_items, old_items, source, now=None):
    """写侧：聊天/命令/工具把地点改成户外目的地 → 开 commute 过渡。"""
    if str(source or "") not in ("chat", "command", "tool"):
        return None
    now = _now(now)
    today = now.strftime("%Y-%m-%d") if now else ""
    if not day or str(day) != today:
        return None
    old_by_start = {}
    for item in old_items or []:
        if isinstance(item, dict):
            old_by_start[str(item.get("start") or "")] = item
    for item in new_items or []:
        if not isinstance(item, dict):
            continue
        place = str(item.get("place") or "")
        if place in INDOOR_PLACES:
            continue
        previous = old_by_start.get(str(item.get("start") or "")) or {}
        old_place = str(previous.get("place") or "")
        if old_place == place or not needs_transit(old_place or HOME, place):
            continue
        return start(plugin, "commute", place, activity=str(item.get("activity") or ""),
                     category=str(item.get("category") or ""), from_place=old_place, now=now)
    return None
