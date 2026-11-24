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

try:  # 吞异常留痕（2026-11-24）
    from .amo_observability import note as _note_swallowed
except ImportError:
    from amo_observability import note as _note_swallowed
from typing import TYPE_CHECKING, Any

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

try:  # Phase 11：地点门面 + 分钟时钟统一实现
    from .amo_place import repair_zone_rows, resolve_zone, zone_for_place
    from .amo_numbers import clock_after as _clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_place import repair_zone_rows, resolve_zone, zone_for_place
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
    # 2026-11-24：裸「睡觉」会把「那我摸着睡觉」误判成去洗漱。
    # 只认明确要睡/已睡的说法；「睡觉吧」这种陪睡语气不再单独起过渡。
    ("sleep", ("去睡", "该睡了", "要睡了", "准备睡", "先睡",
               "睡觉了", "睡了", "躺下睡", "晚安")),
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
    if intent["kind"] == "sleep":
        # 已经睡着/在床上时，不再插一条「在洗漱」过渡（2026-11-24 线上：
        # 「那我摸着睡觉」把 00:50 拆成洗漱 20 分钟 + 到达块 5 分钟）。
        try:
            current = plugin.state.current_activity() or {}
        except Exception:
            current = {}
        _activity = str(current.get("activity") or current.get("action") or "")
        _category = str(current.get("category") or "")
        if _category == "sleep" or any(word in _activity for word in ("睡", "床", "被窝")):
            logger.info("[amo] 已在睡眠/床上，不启动 sleep 过渡：%s", str(text or "")[:40])
            return None
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
                "place": step.get("place") or HOME,
                "zone": resolve_zone(step.get("place") or HOME,
                                     data.get("category") or data.get("kind") or "",
                                     step.get("zone") or "home"),
                "name": step.get("name") or "", "kind": data.get("kind") or "commute",
                "from": data.get("from") or HOME, "to": data.get("to") or "",
            }
        cursor += length
    return {
        "index": len(steps), "step": steps[-1], "elapsed": elapsed, "remaining": 0,
        "remaining_total": 0, "arrived": True,
        "activity": _arrival_activity(data.get("to") or "", data.get("activity") or ""),
        "place": data.get("to") or "",
        "zone": resolve_zone(data.get("to") or "",
                             data.get("category") or data.get("kind") or "", ""),
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
#: 过渡状态存活期 = 计划分钟数 + 宽限；超过当作废，不再往日程里补旧计划。
TRANSITION_TTL_GRACE = 10
#: 同一 to+kind 的过渡去重窗口：只按当前过渡 + 相邻 15 分钟，不按全天同地点。
TRANSITION_DEDUPE_MINUTES = 15
#: 最后一步已经在「离开」的口径：到了这种步骤后不能再补写「到达同一地点」。
_LEAVE_STEP_WORDS = ("准备走", "准备离开", "离开", "走了", "回家", "结账",
                     "办完了", "吃完了", "逛完了")


def _is_leaving_step(step):
    """这一步是不是已经在离开/收尾（「吃完在结账，准备走」）。"""
    text = str((step or {}).get("action") or "") + str((step or {}).get("name") or "")
    if not text:
        return False
    if "还没" in text or "没吃完" in text or "没逛完" in text:
        return False
    return any(word in text for word in _LEAVE_STEP_WORDS)


def _is_leaving_row(row):
    return isinstance(row, dict) and _is_leaving_step({
        "action": row.get("activity"), "name": row.get("detail")})


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


def _hard_constraint_block(plugin, to_place, now=None):
    """场景账本硬约束：请假/事假/出差期间不允许的目的地，返回约束类型（空=放行）。

    2026-11-22 task-25：JIT/过渡写者不再自己判「病假不去公司」——病假/事假/出差
    的唯一事实源是 amo_scene_ledger.constraints()。请假只能在家/医院；出差日
    不能凭空回家。返回非空时调用方必须放弃这次过渡/到达块。
    """
    place = str(to_place or "").strip()
    if not place:
        return ""
    now = _now(now)
    try:
        try:
            from .amo_scene_ledger import constraints as _scene_constraints
        except ImportError:
            from amo_scene_ledger import constraints as _scene_constraints
        cons = _scene_constraints(plugin, now.strftime("%Y-%m-%d") if now else None, now)
    except Exception as exc:
        logger.debug("[amo] 场景硬约束读取失败: %s", exc)
        return ""
    kind = str((cons or {}).get("kind") or "")
    if not kind:
        return ""
    zone = str(resolve_zone(place, "", "") or "")
    allowed_zones = cons.get("allowed_zones")
    if allowed_zones is not None and zone and zone not in allowed_zones:
        return kind
    allowed_places = cons.get("allowed_places")
    if allowed_places is not None and place and place not in allowed_places:
        return kind
    if kind == "trip" and zone == "home":
        return kind
    return ""


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
    _scene_block = _hard_constraint_block(plugin, to_place, now)
    if _scene_block:
        logger.warning("[amo] 场景硬约束（%s）不允许去 %s，过渡不启动（kind=%s）",
                       _scene_block, to_place, kind)
        return None
    state = getattr(plugin, "state", None)
    current_data = _kv_get(plugin, STATE_KEY, {}) or {}
    # TTL：挂起/排队的过渡过期就作废，避免半夜把旧计划写进日程。
    if isinstance(current_data, dict) and current_data.get("steps"):
        _started = float(current_data.get("started_at") or 0)
        _alive = (total_minutes(current_data.get("steps") or []) + TRANSITION_TTL_GRACE) * 60
        if _started and now.timestamp() - _started > _alive:
            logger.warning("[amo] 过渡已过期，作废：%s→%s（%.0f 分钟）",
                           current_data.get("from"), current_data.get("to"),
                           (now.timestamp() - _started) / 60.0)
            _kv_set(plugin, STATE_KEY, {})
            _kv_set(plugin, "activity_now", {})
            current_data = {}
    if isinstance(current_data, dict) and current_data.get("steps"):
        stage = stage_at(current_data, now)
        if stage and not stage.get("arrived"):
            if (str(current_data.get("kind") or "") == kind
                    and str(current_data.get("to") or "") == to_place):
                logger.warning(
                    "[amo] 过渡去重：同一 to+kind 已在执行，忽略新过渡 kind=%s to=%s "
                    "original=%s cleaned=%s fallback=none",
                    kind, to_place, stage.get("activity") or "-",
                    current_data.get("activity") or "-")
                return None
            history = list(current_data.get("queued") or [])
            history.append({"kind": kind, "to": to_place, "queued_at": _amo_clock.now_ts()})
            current_data["queued"] = history[-5:]
            _kv_set(plugin, STATE_KEY, current_data)
            logger.info("[amo] 过渡冲突：%s 优先，新的 %s→%s 排队", stage.get("activity"), kind, to_place)
            return None
    # 刚刚完成过同一 to+kind（相邻 15 分钟）：不再重复启动，避免「离开小馆又到达小馆」。
    _last = _kv_get(plugin, "transit:last", {}) or {}
    try:
        _last_at = float(_last.get("at") or 0)
        if (isinstance(_last, dict) and to_place
                and str(_last.get("to") or "") == to_place
                and str(_last.get("kind") or "") == kind
                and now.timestamp() - _last_at <= TRANSITION_DEDUPE_MINUTES * 60):
            logger.warning(
                "[amo] 过渡去重：%d 分钟内刚完成过同一 to+kind，忽略新过渡 kind=%s to=%s "
                "original=%s cleaned=%s fallback=none",
                TRANSITION_DEDUPE_MINUTES, kind, to_place,
                _last.get("fact") or "-", "-")
            return None
    except (TypeError, ValueError):
        # intentional: 配置/旧值/可选依赖/清理失败按默认值处理
        pass
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
        # 幂等键：同一种过渡在同一分钟只允许启动一次。
        "idem": "%s|%s|%s" % (kind, to_place, now.strftime("%Y%m%d%H%M")),
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


def _sync_current_action(plugin, items, now=None):
    """过渡写块后同步 current_action，避免日程在吃饭、聊天还说在忙。"""
    now = now or _amo_clock.now()
    minute = now.hour * 60 + now.minute
    for item in items or ():
        if not isinstance(item, dict):
            continue
        begin = _minute(item.get("start"))
        end = _minute(item.get("end"))
        if begin is None or end is None or not (begin <= minute < end):
            continue
        setter = getattr(plugin, "set_current_action", None)
        if not callable(setter):
            return None
        try:
            setter(str(item.get("activity") or "在忙"),
                   until_ts=now.timestamp() + max(60, (end - minute) * 60),
                   place=str(item.get("place") or "家"),
                   zone=str(item.get("zone") or "home"),
                   category=str(item.get("category") or "life"))
        except Exception as exc:
            logger.debug("[amo] 过渡同步 current_action 失败: %s", exc)
        return item
    return None


def _commit(plugin, day, items, old_items):
    state = getattr(plugin, "state", None)
    result = None
    try:
        # 写库前不变量：具体地点 + road 纠正（只今天/未来），warning 由 helper 打。
        today = _amo_clock.now().strftime("%Y-%m-%d")
        items, _zone_fixed = repair_zone_rows(items, day, today=today)
    except Exception as _amo_exc:  # cleanup: 纠正失败不能挡住过渡写库
        logger.debug("[amo] 过渡分块 zone 纠正失败: %s", _amo_exc)
    try:
        apply = getattr(state, "apply_schedule", None)
        if callable(apply):
            # 过渡分块也走写入网关：锁内读当前块 → 交回唯一提交入口（审计带 transit）。
            result = apply(day, lambda current: list(items), source="transit",
                           reason="过渡分块")
        elif state is not None and callable(getattr(state, "commit_schedule_change", None)):
            result = Timeline(plugin).save(day, items, old_items=old_items,
                                           source="transit", reason="过渡分块")
    except Exception as exc:
        logger.debug("[amo] 过渡分块落库失败: %s", exc)
    if result:
        _sync_current_action(plugin, items)
    return result





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
    _blocked = (_office_place(data.get("to")) and _no_office_allowed(plugin, now))
    _scene_block = _hard_constraint_block(plugin, data.get("to"), now)
    if _blocked or _scene_block:
        if readonly:
            return  # GET 纯只读：这里只跳过，不取消、不写库
        logger.warning("[amo] 取消过渡（%s）：to=%s hour=%s",
                       _scene_block or "病假/夜间/工作日下班后", data.get("to"),
                       now.hour if now is not None else "-")
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
            "place": step.get("place") or HOME,
            "zone": resolve_zone(
                step.get("place") or HOME,
                "commute" if data.get("kind") == "commute" else str(data.get("kind") or "life"),
                step.get("zone") or "home"),
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
    _scene_block = _hard_constraint_block(plugin, data.get("to"), now)
    if (_office_place(data.get("to")) and _no_office_allowed(plugin, now)) or _scene_block:
        logger.warning("[amo] 到站后不写活动块（%s）：to=%s hour=%s",
                       _scene_block or "病假/夜间/工作日下班后", data.get("to"), now.hour)
        _kv_set(plugin, STATE_KEY, {})
        _kv_set(plugin, "activity_now", {})
        _drop_future_transit(plugin, now)
        return None
    stage = stage or stage_at(data, now) or {}
    day = now.strftime("%Y-%m-%d") if now else ""
    to_place = str(data.get("to") or "")
    category = str(data.get("category") or data.get("kind") or "life")
    kind = str(data.get("kind") or "commute")
    elapsed = int(round(float(stage.get("elapsed") or data.get("minutes") or 0)))
    last_step = (data.get("steps") or [{}])[-1]
    if (_is_leaving_step(last_step)
            and str((last_step or {}).get("place") or "") == to_place):
        # 最后一步已经「吃完结账准备走」：不能再补一条「到了同一家小馆」的到达块。
        fact = (("吃完从%s出来，准备回去。" % to_place) if kind == "meal"
                else ("从%s出来，准备回去。" % to_place))
        logger.warning(
            "[amo] 到达块跳过 reason=leaving_step original=%s cleaned=%s fallback=%s kind=%s",
            str((last_step or {}).get("action") or last_step or "-")[:80],
            "不写到达块", fact[:80], kind)
        _kv_set(plugin, STATE_KEY, {})
        _kv_set(plugin, "activity_now", {})
        _kv_set(plugin, "transit:last", {
            "to": to_place, "kind": kind, "elapsed": elapsed,
            "fact": fact, "at": float(now.timestamp()) if now else _amo_clock.now_ts()})
        return fact
    item = {
        "start": now.strftime("%H:%M") if now else "", "end": "",
        "activity": stage.get("activity") or _arrival_activity(to_place),
        "place": to_place or (stage.get("place") or HOME),
        "zone": resolve_zone(to_place, category, ""),
        "category": category,
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
                            and str(row.get("start") or "") >= now_text
                            and not _is_leaving_row(row))]
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
        follow_category = "play" if _chain == "shopping" else "work"
        follow = {"start": follow_start, "end": follow_end, "activity": activity_text,
                  "place": to_place,
                  "zone": resolve_zone(to_place, follow_category, ""),
                  "category": follow_category,
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
    # 记忆写者统一过场景守卫：到达事实不能再带「此刻不在的地方」（病假/出差硬约束）。
    try:
        try:
            from .amo_scene_ledger import guard_text as _scene_guard_text
        except ImportError:
            from amo_scene_ledger import guard_text as _scene_guard_text
        fact = str(_scene_guard_text(plugin, fact, now) or fact)
    except Exception as _amo_exc:
        logger.debug("[amo] 到达事实场景守卫失败: %s", _amo_exc)
    _kv_set(plugin, "transit:last", {"to": to_place, "kind": kind,
                                     "elapsed": elapsed, "fact": fact,
                                     "at": float(now.timestamp()) if now else _amo_clock.now_ts()})
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
    kept: list[Any] = []
    trimmed: list[Any] = []
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
        return str(resolve_zone(place, "", "") or "")
    except Exception:
        return ""


#: 外地/城外复合地名（「河北省·石家庄·酒店」这种）会让 zone_for_place 回落成 home；
#: 这些地标即使 zone 认不出也绝不能当作家内部，否则出差日不补通勤、回程还会写成「回家」。
_AWAY_PLACE_WORDS = (
    "酒店", "宾馆", "民宿", "旅馆", "客栈", "车站", "机场", "高铁站", "火车站",
    "客户公司", "公司", "写字楼", "办公室", "商场", "餐厅", "饭馆", "小馆", "食堂",
    "小吃街", "咖啡", "医院", "学校", "教室", "游乐园", "乐园", "景区", "公园",
    "古镇", "海边", "沙滩",
)


def _is_home_place(place):
    """家/卧室/客厅/沙发/浴室这种「家内部」地点：来回走不用通勤块。"""
    text = str(place or "").strip()
    if not text:
        return False
    if text in INDOOR_PLACES:
        return True
    if any(word in text for word in _AWAY_PLACE_WORDS):
        return False
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
                except Exception as exc:  # cleanup
                    _note_swallowed("amo_transition.write_activity_memory", exc)
                continue
        else:
            try:
                asyncio.get_running_loop().create_task(coro)
            except Exception:
                try:
                    coro.close()
                except Exception as exc:  # cleanup
                    _note_swallowed("amo_transition.write_activity_memory", exc)
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
        if stage and not stage.get("arrived"):
            # 读时纠偏：旧行/旧过渡里的 zone 可能被写成 road，但地点其实是小馆/粉店。
            # 只有 resolve_zone 算出来的有效 zone 真的是 road 才拦「到家了」。
            place = str(stage.get("place") or "").strip()
            category = str((transition or {}).get("category")
                           or (transition or {}).get("kind") or "")
            zone = resolve_zone(place, category, str(stage.get("zone") or ""))
            if zone == "road":
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
            category = str(current_activity.get("category") or "").strip()
            if resolve_zone(place, category, zone) == "road":
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
        logger.warning(
            "[amo] 路上终态守卫 reason=road_home original=%s cleaned=%s fallback=%s",
            text[:80], value[:80], "还在车上")
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
        logger.warning(
            "[amo] 过渡期终态守卫 reason=terminal original=%s cleaned=%s fallback=%s",
            str(text or "")[:80], value[:80], "还在路上")
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
