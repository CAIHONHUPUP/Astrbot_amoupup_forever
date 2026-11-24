# -*- coding: utf-8 -*-
"""日记/日程的「反加戏」守卫（2026-11-15）。

素材包里没有的事（老公打电话/抱我/送药/来看我）不许写；
线上聊天只能写成「在微信/飞书上聊了几句」；称呼不许自创（如「阿明」）。
"""
from __future__ import annotations

try:  # 吞异常留痕（2026-11-24）
    from .amo_observability import note as _note_swallowed
except ImportError:
    from amo_observability import note as _note_swallowed
from typing import Any, TYPE_CHECKING

import hashlib
import logging
import re
import time

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from .clock import now as _clock_now
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from clock import now as _clock_now

try:  # 时间换算唯一实现（amo_numbers；不再本地重写一套）
    from .amo_numbers import clock_of_fact as _clock_of_minute, minutes_of_fact as _block_minute
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_numbers import clock_of_fact as _clock_of_minute, minutes_of_fact as _block_minute

try:  # 健康状态唯一口径（state.health_info 的 sick 就是这张表）
    from .amo_shared_config import SICK_STATUSES as _SICK_STATUSES
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared_config import SICK_STATUSES as _SICK_STATUSES

logger = logging.getLogger("amo")

_RULES_HEAD = """【事实分级·最高优先级】
【绝对事实】：只写素材包里明确给出的事（真实聊天、消费记录、日程、系统事件）。
【内心活动】：可以自由写想念、委屈、疲惫、开心、孤独。
"""
_RULES_TOGETHER = """【约会面对面·只在约会时段】：今天有约会，但只有约会那段时间他就在你对面、在你身边；约会开始前和结束后他都不在、各回各家，只能手机联系。面对面时段可以写他给你夹菜、你抬头看他；严禁写成「他在微信上跟我说」「他不在我身边」「我一个人吃」。
【他是谁】：约会里的「他」就是老公（用户本人），不是别人；不许写「有个男人」「神秘的他」或第二个男性。
【严禁虚构】：素材包里没写的动作不要加（例如他突然跑来送花）；「他就在我对面」只限约会时段，其他时间一律写一个人/手机。
"""
_RULES_ALONE = """【严禁虚构】：素材包里没写的人和互动，一律不能出现——老公打电话、老公抱着我、老公摸我的头、老公给我买药、老公来看我、他出现在我身边、他在旁边翻身、他躺在我旁边、他还在屋里、他在家、屋里就我俩。
【约会结束·各回各家】：约会一结束他就回他自己的住处，你一个人回出租屋，剩下的时间只能手机/视频联系；严禁写两个人回同一个家、同一个屋檐下过日子、他抱着我、他就守在你身边。
【线上/线下】：老公今天只可能在飞书/微信上跟你聊天，现实里他在忙、没有出现在你身边；写聊天只能写「在微信/飞书上跟老公聊了几句」，绝不能写「刚跟老公通完电话」「他打电话来关心我」。
"""
_RULES_HOME = """【同居·在家共处】：你们住在一起。在家时他就在屋里（客厅/厨房/卧室），可以写日常互动：他看电视、他做饭、他递东西、你靠在他肩上、他抱着我。
【他不在家】：他上班/出差时不在家，这时候才写一个人、手机联系；素材里没说他出门，就默认他在家。
【严禁虚构】：素材里没有的具体事件不要加（比如他突然买花回来）；日常相处的小动作可以自然扩写。
"""
_RULES_TAIL = """【思念的合法写法】示例：「一个人在出租屋里，看着黑漆漆的房间，突然好想他。要是他能在身边抱抱我就好了……算了，不打扰他了，我自己喝点热水吧。」
【称呼】：只能叫「老公」「亲爱的」或「他」；不许出现「阿明」这类自创名字。
【指代】：一段里第一次提到他，写「老公」，不要一上来就写裸「他」。
【他是谁·最高优先级】：这里的「他」只能是你老公，也就是正在跟你聊天的用户本人。不许出现第二个男性，不许写「有个男人」「那个人」「神秘的他」「他朋友」这类没有交代的人；同一段里「他」自始至终是同一个人（你老公）。
"""


#: 聊天≠语音：没明说语音/连麦/电话/视频，日记里就不许写这些（2026-11-17 用户要求）。
NO_VOICE_RULE = ("\n【聊天≠语音】聊天记录只是文字消息：素材里没有明确出现「语音/连麦/电话/视频/通话」，就不能写语音、连麦、电话、视频通话、耳机里的声音；一律写成「在微信上聊天/发消息」。素材里没有明确说「约好/说好」的，也不许写成约定。")

#: 2026-11-18：日记里出现「病假扣了一个朋友和日薪」「迟到罚款」——钱和考勤不进日记。
NO_MONEY_RULE = ("\n【钱和考勤不进日记】账本/工资/扣款的具体金额、余额、迟到罚款一律不写进日记；"
                 "病假当天不许写迟到/早退；「全勤」不许写成「朋友」或别的词；"
                 "必须提钱时只写「这个月工资要少一点」这种模糊感受，不写数字。")


def fact_rules(together=False, scene=None, home=False, title="老公"):
    """日记的系统硬规则。

    - 约会当天：面对面版本，别把老公写成网友；
    - 同居/半同居且这一块他在家：在家共处版本；
    - 其他：独处版本（7.5.9 原行为）。
    """
    if home or (scene is not None and str(getattr(scene, "presence", "") or "") == "home"):
        body = _RULES_HOME
    else:
        body = _RULES_TOGETHER if together else _RULES_ALONE
    text = _RULES_HEAD + body + _RULES_TAIL + NO_VOICE_RULE + NO_MONEY_RULE
    if title and title != "老公":
        # 用户让她喊别的称呼：规则里的「老公」统一换掉（2026-11-21）
        text = text.replace("老公", title)
    return text


#: 默认（没约会）规则，保持旧调用方不变
FACT_RULES = fact_rules(False)

BAD_NAMES = ("阿明",)
_PLATFORM = re.compile(r"飞书|钉钉|QQ|lark|Lark|qq")


def normalize_platform(text):
    return _PLATFORM.sub("微信", str(text or ""))
_ONLINE_MARKERS = ("微信", "飞书", "消息", "聊天", "语音", "短信", "QQ", "qq", "电话里发")
_FABRICATED = re.compile(
    r"(?:他|老公)[^。！？!?\n]{0,10}?"
    r"(?:打来电话|打电话来|打电话|电话(?:马上|立刻)?打过来|抱着我|抱住我|"
    r"摸了摸我的头|摸我的头|给我买药|买药带过来|来看我|赶过来|守着我|"
    r"陪在我身边|出现在我身边|站在我面前|躺在?我旁边|在我旁边|翻(?:了)?个?身|从背后抱(?:住)?我|屋里就我俩|"
    r"还?在(?:屋里|家里|家|房间|卧室|我旁边|我身边)|在门口等我|送我出门|看着我出门|跟我一起出门)"
)


def _sentences(text):
    return re.split(r"(?<=[。！？!?\n])", str(text or ""))


# ---------------- 事实一致性：只查「矛盾」，不查「遗漏」（2026-11-17 B 方案） ----------------
#
# 大日记不再「一改日程就重写」，而是先做事实对账：
# - 日记说「没吃宵夜 / 没出门 / 一直在家 / 没生病」，但日程/账本/健康有相反事实 → 矛盾；
# - 日记只是没提宵夜、没提某顿饭、没提某次出门 → 遗漏，不算不一致，不重写。

_HOME_PLACES = ("家", "家里", "home")
_NIGHT_SNACK_WORDS = ("宵夜", "夜宵", "泡面", "外卖", "烧烤", "炸鸡", "串串", "火锅")
_SICK_WORDS = ("医院", "看病", "病假", "生病", "发烧", "感冒", "不舒服", "在家休息")

_DIARY_NEG_SNACK = re.compile(r"(?:没|没有|并没有|并未|不)(?:吃|点|要|买)?(?:宵夜|夜宵|外卖)")
_DIARY_NEG_OUT = re.compile(
    r"(?:没|没有|并没有|并未)(?:出门|出去|出过门|离开家)"
    r"|(?:一直|整天|一整天|全天|一天)(?:都)?(?:在|待在|窝在|待)家")
#: 「在家」必须是排他性断言才算事实矛盾；单独一句「晚上在家吃饭」不算
_DIARY_AT_HOME = re.compile(
    r"(?:今天|一天|整天|一整天|全天|一直)(?:都|就)?(?:在|待在|窝在)家"
    r"|在家(?:待|窝)了?(?:一天|整天)")
_DIARY_NEG_SICK = re.compile(
    r"(?:没|没有|并没有|并未)(?:生病|感冒|发烧|不舒服)"
    r"|身体(?:很|挺|一直|还)?(?:好|没事|健康)|很健康|没病"
    r"|(?:感冒|发烧|生病|病)(?:都)?(?:已经)?(?:好|没事|痊愈)")
_DIARY_WORK = re.compile(r"上班|在公司|去公司|工作|加班|打卡|工位")
_DIARY_EARLY_SLEEP = re.compile(r"早(?:早|点)?(?:就)?睡|很早就?睡|睡得早|提前睡|早早地?睡")
_DIARY_NO_FOOD = re.compile(
    r"没吃(?:东西|饭|什么|一口)|没点(?:外卖|吃的)|没吃外卖|晚饭没吃|一天没吃|没吃东西")


def _home_place(place):
    text = str(place or "").strip()
    return (not text) or text.startswith("家")


_MEAL_WORDS = ("吃", "饭", "餐", "外卖", "宵夜", "夜宵", "泡面", "火锅", "烧烤", "炸鸡")
_SLEEP_WORDS = ("睡", "被窝", "赖床", "午休", "午睡", "补觉")
#: 「吃了什么」能答出口的食物名（只从视图原文里挑，不自己编）
_FOOD_NOUNS = ("面", "饺子", "饭", "粥", "火锅", "烧烤", "包子", "米线", "粉",
               "汉堡", "炒饭", "便当", "麻辣烫", "三明治", "沙拉", "外卖")
#: 日记里「他就在身边」的断言（愿望句不算，见 _WISH_MARKERS）
_PRESENCE_CLAIM = re.compile(
    r"(?:他|老公)[^。！？!?]{0,8}?(?:抱着我|抱住我|搂着我|亲了我|刚抱我|刚亲我"
    r"|在我旁边|躺我旁边|坐在我旁边|守着我|来看我|出现在我身边|站在我面前)")
_WISH_MARKERS = ("想", "要是", "如果", "希望", "以后", "将来", "盼")


def _zone_for_place(place):
    """地点词 → zone（唯一实现仍在 photo_scene）；拿不到就空串。"""
    try:
        try:
            from .photo_scene import resolve_zone as _zone
        except ImportError:
            from photo_scene import resolve_zone as _zone
        return str(_zone(str(place or ""), "", "") or "")
    except Exception as exc:  # cleanup: 地点表读不到就不推 zone
        logger.debug("[narrative] zone 反推失败: %r", exc)
        return ""


def _fact_text(event):
    """视图事件的「事实字段」正文（不含 diary/detail：那里是叙事，不是事实）。"""
    event = event or {}
    return "".join(str(event.get(key) or "") for key in ("activity", "place", "reason"))


def _event_minute(event):
    start = (event or {}).get("start")
    return start if isinstance(start, int) else None


def _is_night(minute):
    return minute is not None and (minute >= 22 * 60 or minute < 6 * 60)


def _mealish(text, category):
    return category == "meal" or any(word in text for word in _MEAL_WORDS)


def _meal_band(minute):
    hour = int(minute or 0) // 60
    if hour < 10:
        return "breakfast"
    if hour < 15:
        return "lunch"
    if hour < 22:
        return "dinner"
    return "night"


def _health_status(event):
    """身体事实 → 状态文本（「身体：感冒（备注）」→「感冒」）。"""
    text = str((event or {}).get("activity") or "").strip()
    if text.startswith("身体："):
        text = text[len("身体："):]
    for mark in ("（", "("):
        text = text.split(mark)[0]
    return text.strip() or "不舒服"


def day_view(plugin, day=None, now=None):
    """当天唯一事实视图（只读 amo_fact_timeline.build_day_view）；读失败给空视图。"""
    try:
        try:
            from .amo_fact_timeline import build_day_view as _build
        except ImportError:
            from amo_fact_timeline import build_day_view as _build
        return _build(plugin, day, now)
    except Exception as exc:  # cleanup: 视图读不到就按「没有事实」处理
        logger.debug("[narrative] 事实视图读取失败: %r", exc)
        return {"day": str(day or ""), "now": now, "events": [], "sources": {}, "count": 0}


#: 同一请求内事实视图复用窗口（秒）：生成上下文和发送前事实门各建一次 -> 共用一次
_VIEW_CACHE_SECONDS = 2.0


def invalidate_narrative_view(plugin):
    """日程/账本/日记变化后主动丢视图缓存；下一次 narrative_view 重新建。"""
    try:
        plugin._narrative_view_cache = None
    except Exception as exc:  # cleanup: 极简 fake 没有 setattr 也不影响主流程
        _note_swallowed("amo_diary_guard.invalidate_narrative_view", exc)


def narrative_view(plugin, now=None, cache=None):
    """当天事实视图（day 取 now 那一天）；读失败给空视图。

    N1：同一条回复里「生成上下文」和「发送前事实门」各自 build_day_view 一次；
    真实时钟路径（now=None）按天缓存 2 秒复用一份，显式 now（测试/回放）默认不缓存。
    缓存带天键 + 短 TTL，并可用 invalidate_narrative_view(plugin) 立即失效。
    """
    day = None
    if now is not None:
        try:
            day = now.date().isoformat()
        except AttributeError:  # cleanup: 不是 datetime 就让 build 自己归一
            day = None
    else:
        try:
            day = _clock_now().date().isoformat()
        except Exception:  # cleanup: 日记容错
            day = None
    use_cache = (now is None) if cache is None else bool(cache)
    if use_cache:
        cached = getattr(plugin, "_narrative_view_cache", None)
        if isinstance(cached, dict):
            try:
                fresh = (cached.get("day") == day
                         and time.monotonic() - float(cached.get("at") or 0.0)
                         <= _VIEW_CACHE_SECONDS)
            except Exception:  # cleanup: 日记容错
                fresh = False
            if fresh and isinstance(cached.get("view"), dict):
                return cached["view"]
    view = day_view(plugin, day, now)
    # 食物送达事实层是“视图的一部分”，日记仍只读视图，不直接读 kv。
    if isinstance(view, dict) and day:
        try:
            try:
                from .amo_food_delivery import today_facts
            except ImportError:
                from amo_food_delivery import today_facts
            view = dict(view)
            view["meal_facts"] = today_facts(plugin, day)
        except Exception as exc:
            _note_swallowed("amo_diary_guard.narrative_view", exc)
    if use_cache:
        try:
            plugin._narrative_view_cache = {
                "day": day, "at": time.monotonic(), "view": view}
        except Exception as exc:  # cleanup: 缓存写不进去也不影响视图本身
            _note_swallowed("amo_diary_guard.narrative_view", exc)
    return view


def _view_blocks(plugin, day_text):
    """视图不带 category/zone：回查当天日程原块补位（唯一补位处）；读不到就空。"""
    if plugin is None or not day_text:
        return []
    try:
        try:
            from .amo_fact_timeline import read_items as _read_items
        except ImportError:
            from amo_fact_timeline import read_items as _read_items
        rows = []
        for item in _read_items(plugin, day_text) or []:
            if not isinstance(item, dict):
                continue
            zone = str(item.get("zone") or "")
            place = str(item.get("place") or "")
            if zone == "road" and place:
                try:
                    from .photo_scene import resolve_zone as _resolve
                except ImportError:
                    from photo_scene import resolve_zone as _resolve
                fixed = str(_resolve(place, item.get("category"), zone) or "")
                if fixed and fixed != "road":
                    item = dict(item)
                    item["zone"] = fixed
            rows.append(item)
        return rows
    except Exception as exc:  # cleanup: 读不到原块就不补类别
        logger.debug("[narrative] 日程原块读取失败: %r", exc)
        return []


def _block_of_event(blocks, event):
    """按 起点+活动 找视图事件对应的原块；找不到返回空 dict。"""
    start = _event_minute(event)
    activity = str((event or {}).get("activity") or "").strip()
    for item in blocks or ():
        if _block_minute(item.get("start")) != start:
            continue
        if str(item.get("activity") or "").strip() == activity:
            return item
    return {}


def _view_current(view, now=None):
    """视图里此刻正在进行的那条事实（复用 F1 的唯一实现）。"""
    try:
        try:
            from .amo_fact_timeline import current_view_event as _current
        except ImportError:
            from amo_fact_timeline import current_view_event as _current
        return _current(view, now or (view or {}).get("now"))
    except Exception as exc:  # cleanup: 推不出此刻就不给 current
        logger.debug("[narrative] 当前事实推导失败: %r", exc)
        return None


def narrative_facts(view, plugin=None, now=None):
    """唯一事实视图 → 叙事要用的客观事实。

    覆盖统一规则需要的七类：地点（out/places/hospital）、活动（current）、
    吃饭（meals/meal）、健康（sick）、陪伴（together）、宵夜（snack）、
    早睡与夜间事实（late_activity）。只从视图读，不再各自拼日程/账本/健康。
    """
    events = [event for event in (view or {}).get("events") or ()
              if isinstance(event, dict)]
    moment = now or (view or {}).get("now") or _clock_now()
    day_text = str((view or {}).get("day") or "")
    facts = {"day": day_text, "now": moment, "events": events,
             "snack": False, "out": False, "sick": False, "hospital": False,
             "late_activity": False, "meal": False, "together": False,
             "partner_home": False,
             "places": [], "meals": [], "current": None,
             "people": [], "companions": [], "items": [], "occasions": [],
             "occasion": "",
             "health": {"status": "健康", "sick": False}}
    blocks = _view_blocks(plugin, day_text)
    for event in events:
        text = _fact_text(event)
        for key in ("people", "companions", "items"):
            for value in event.get(key) or ():
                value = str(value or "").strip()
                if value and value not in facts[key]:
                    facts[key].append(value)
        occasion = str(event.get("occasion") or "").strip()
        if occasion and occasion not in facts["occasions"]:
            facts["occasions"].append(occasion)
        place = str(event.get("place") or "").strip()
        sources = [str(name) for name in event.get("sources") or ()]
        category = str((_block_of_event(blocks, event) or {}).get("category") or "")
        minute = _event_minute(event)
        if place and not _home_place(place):
            facts["out"] = True
            if place not in facts["places"]:
                facts["places"].append(place)
        if "health" in sources:
            status = _health_status(event)
            sick = status in _SICK_STATUSES
            facts["sick"] = sick
            facts["health"] = {"status": status, "sick": sick}
        if "医院" in place or any(word in text for word in _SICK_WORDS):
            facts["hospital"] = True
        sleepish = any(word in text for word in _SLEEP_WORDS)
        if not sleepish and _is_night(minute):
            facts["late_activity"] = True
        if _mealish(text, category):
            facts["meal"] = True
            if minute is not None:
                end = event.get("end")
                facts["meals"].append({
                    "category": "meal",
                    "start": _clock_of_minute(minute),
                    "end": _clock_of_minute(end if isinstance(end, int) else minute + 40),
                    "band": _meal_band(minute),
                    "minute": minute,
                    "text": text})
            if _is_night(minute) or any(word in text for word in _NIGHT_SNACK_WORDS):
                facts["snack"] = True
        if "约会" in text:
            facts["together"] = True
    # 食物送达事实层：只从视图里的 meal_facts 读，不直接读 kv。
    if day_text:
        try:
            for fact in (view.get("meal_facts") or ()):
                food = str(fact.get("food") or "外卖")
                ts = float(fact.get("ts") or 0)
                minute = time.localtime(ts).tm_hour * 60 + time.localtime(ts).tm_min
                band = str(fact.get("slot") or _meal_band(minute))
                facts["meal"] = True
                facts["meals"].append({
                    "category": "meal",
                    "start": _clock_of_minute(minute),
                    "end": _clock_of_minute(minute + 40),
                    "band": band,
                    "minute": minute,
                    "text": "他点了%s" % food,
                })
                if _is_night(minute):
                    facts["snack"] = True
        except Exception as exc:
            _note_swallowed("amo_diary_guard.narrative_facts", exc)
    current = _view_current(view, moment)
    if current is not None:
        block = _block_of_event(blocks, current)
        # 同居/半同居：这一块他是不是在家。真正的判定只在 amo_household.scene_at。
        try:
            try:
                from . import amo_household as _hh
            except ImportError:
                import amo_household as _hh
            _scene = _hh.scene_at(
                plugin, day_text, current.get("start"), current.get("end"),
                current.get("place") or "", zone=str(block.get("zone") or ""),
                activity=str(current.get("activity") or ""),
            )
            facts["partner_home"] = bool(_scene.partner and _scene.presence == "home")
        except Exception as exc:  # cleanup: 居住状态读不到就按「不在家」处理
            logger.debug("[narrative] 居住场景读取失败: %r", exc)
        facts["current"] = {
            "activity": str(current.get("activity") or ""),
            "place": str(current.get("place") or ""),
            "zone": str(block.get("zone") or _zone_for_place(current.get("place"))),
            "category": str(block.get("category") or ""),
            "start": current.get("start"), "end": current.get("end"),
            "time": str(current.get("time") or ""),
            "people": list(current.get("people") or []),
            "companions": list(current.get("companions") or []),
            "items": list(current.get("items") or [])}
    for key in ("people", "companions", "items"):
        for value in (view or {}).get(key) or ():
            value = str(value or "").strip()
            if value and value not in facts[key]:
                facts[key].append(value)
    for value in (view or {}).get("occasions") or ():
        value = str(value or "").strip()
        if value and value not in facts["occasions"]:
            facts["occasions"].append(value)
    facts["occasion"] = "、".join(facts["occasions"])
    return facts


def _ctx_from_facts(facts, activity=None, date=None, trip=None, today_chat=None,
                    now=None):
    """事实 → reply_fact_guard 的只读上下文（唯一事实入口）。"""
    act = dict(activity or {})
    current = dict(facts.get("current") or {})
    if not act:
        act = dict(current)
    else:
        for key in ("zone", "category"):
            if not act.get(key) and current.get(key):
                act[key] = current[key]
    return {"now": now or facts.get("now") or _clock_now(),
            "activity": act,
            "health": dict(facts.get("health") or {"status": "健康", "sick": False}),
            "date": date or {}, "trip": trip or {},
            "nearby": list(facts.get("meals") or []),
            "today_chat": bool(today_chat),
            "facts": facts}


def narrative_ctx(view, plugin=None, now=None, activity=None, date=None,
                  trip=None, today_chat=None):
    """视图 → reply_fact_guard 的只读上下文（三类叙事共用）。"""
    facts = narrative_facts(view, plugin=plugin, now=now)
    return _ctx_from_facts(facts, activity=activity, date=date, trip=trip,
                           today_chat=today_chat, now=now)


def narrative_place(plugin, now=None):
    """此刻地点（只读唯一视图）；没有事实就空串。"""
    facts = narrative_facts(narrative_view(plugin, now), plugin=plugin, now=now)
    return str((facts.get("current") or {}).get("place") or "")


def food_answer(plugin, now=None):
    """他问「吃啥」时先答的一句：吃了什么只从唯一事实视图里挑，不编。

    视图里有午饭/晚饭事实就说吃了什么（煮了碗面 / 吃了饺子 / 点了外卖）；
    什么都没记录就照实说还没顾上吃，别答非所问。
    """
    moment = now or _clock_now()
    try:
        facts = narrative_facts(narrative_view(plugin, moment), plugin=plugin, now=moment)
    except Exception as exc:  # cleanup: 读不到事实就不给答案
        logger.debug("[narrative] 吃饭事实读取失败: %r", exc)
        facts = {}
    minute = moment.hour * 60 + moment.minute if hasattr(moment, "hour") else None
    meals = [item for item in facts.get("meals") or ()
             if item.get("minute") is None or minute is None or item["minute"] <= minute]
    if not meals:
        return "还没顾上吃呢"
    last = meals[-1]
    band = {"breakfast": "早上", "lunch": "中午", "dinner": "晚上"}.get(
        str(last.get("band") or ""), "刚刚")
    text = str(last.get("text") or "")
    if "外卖" in text:
        return "%s点了外卖" % band
    noun = next((word for word in _FOOD_NOUNS if word in text), "")
    if noun and "煮" in text:
        return "%s煮了碗%s" % (band, noun) if noun in ("面", "饺子") else "%s煮了%s" % (band, noun)
    if noun:
        return "%s吃了%s" % (band, noun)
    return "%s吃了点东西" % band


def _diary_hit(rule, sentence, reason):
    return {"rule": rule, "sentence": sentence, "replacement": "", "reason": reason}


def _said_no_meal(body, word):
    return bool(re.search(r"(?:没|没有)(?:吃|做|点|碰)?%s" % word, body))


def _presence_claim(body):
    """日记里「他就在身边」的断言（想/要是 这类愿望句不算）。"""
    for sentence in _sentences(body):
        if not _PRESENCE_CLAIM.search(sentence):
            continue
        if any(mark in sentence for mark in _WISH_MARKERS):
            continue
        return True
    return False


def _diary_contradictions(text, facts):
    """日记和事实「矛盾」的句子（只报矛盾，遗漏不报；调用方决定重写）。"""
    body = str(text or "").strip()
    out: list[Any] = []
    if not body:
        return out
    facts = facts or {}
    if facts.get("snack") or facts.get("late_activity"):
        if _DIARY_NEG_SNACK.search(body):
            out.append(_diary_hit("snack", body, "账本/日程里有宵夜事实，日记却说没吃"))
        elif _DIARY_NO_FOOD.search(body):
            out.append(_diary_hit("meal", body, "当天有吃饭/宵夜事实，日记却说没吃"))
        elif _DIARY_EARLY_SLEEP.search(body):
            out.append(_diary_hit("night", body, "当天 22:00 后还有活动，日记却说早睡"))
    if facts.get("out") and (_DIARY_NEG_OUT.search(body) or _DIARY_AT_HOME.search(body)):
        out.append(_diary_hit("place", body, "当天出过门：%s" % "、".join(
            list(facts.get("places") or [])[:2])))
    for band, word in (("breakfast", "早饭"), ("lunch", "午饭"), ("dinner", "晚饭")):
        if _said_no_meal(body, word) and any(
                item.get("band") == band for item in facts.get("meals") or []):
            out.append(_diary_hit("meal", body, "当天有%s" % word))
    if not out and _DIARY_NO_FOOD.search(body) and facts.get("meals"):
        out.append(_diary_hit("meal", body, "当天有吃饭事实，日记却说没吃"))
    if (facts.get("sick") or facts.get("hospital")) and (
            _DIARY_WORK.search(body) or _DIARY_NEG_SICK.search(body)):
        out.append(_diary_hit("health", body, "健康/日程里有生病或医院事实"))
    if not (facts.get("together") or facts.get("partner_home")) and _presence_claim(body):
        out.append(_diary_hit("companion", body, "当天没有面对面约会/同居在家事实"))
    return out


def _night_reply_rule(text, facts):
    """回复/主动消息里的「没吃宵夜/一天没吃/早睡了」和夜间事实打架时就地改口。

    只改成有事实支撑的说法（吃了点东西 / 还没睡呢），不补具体食物或时间。
    """
    value = str(text or "")
    hits: list[Any] = []
    if not value:
        return value, hits
    if facts.get("snack"):
        matched = _DIARY_NEG_SNACK.search(value)
        if matched:
            value = value[:matched.start()] + "吃了点东西" + value[matched.end():]
            hits.append({"rule": "meal", "sentence": matched.group(0),
                         "replacement": "吃了点东西",
                         "reason": "账本/日程里有宵夜事实"})
    if facts.get("meal"):
        matched = _DIARY_NO_FOOD.search(value)
        if matched:
            value = value[:matched.start()] + "吃了点东西" + value[matched.end():]
            hits.append({"rule": "meal", "sentence": matched.group(0),
                         "replacement": "吃了点东西",
                         "reason": "当天有吃饭事实，不能说没吃"})
    if facts.get("late_activity"):
        matched = _DIARY_EARLY_SLEEP.search(value)
        if matched:
            value = value[:matched.start()] + "还没睡呢" + value[matched.end():]
            hits.append({"rule": "night", "sentence": matched.group(0),
                         "replacement": "还没睡呢",
                         "reason": "当天 22:00 后还有活动"})
    return value, hits


def narrative_check(text, view, scope="reply", plugin=None, now=None,
                    activity=None, date=None, trip=None, today_chat=None):
    """叙事只引用事实：矛盾才改，遗漏不改。

    scope="diary"      大日记/块日记：返回 (原文, 矛盾列表)；不改写，调用方按矛盾重写
    scope="reply"      聊天回复：矛盾的小句就地改写，返回 (改写后, 违规列表)
    scope="proactive"  主动消息：同 reply（切句前补一次）
    宵夜/早睡与夜间事实在 reply/proactive 也查（改口成「吃了点东西 / 还没睡呢」）。
    """
    value = str(text or "")
    if not value or not isinstance(view, dict):
        return value, []
    try:
        facts = narrative_facts(view, plugin=plugin, now=now)
    except Exception as exc:  # cleanup: 事实推导失败就不拦叙事
        logger.debug("[narrative] 事实推导失败: %r", exc)
        return value, []
    if str(scope or "reply") == "diary":
        return value, _diary_contradictions(value, facts)
    context = _ctx_from_facts(facts, activity=activity, date=date, trip=trip,
                              today_chat=today_chat, now=now)
    try:
        try:
            from . import reply_fact_guard as _guard
        except ImportError:
            import reply_fact_guard as _guard
        fixed, violations = _guard.guard(value, context)
    except Exception as exc:  # cleanup: 守卫异常绝不拦发送
        logger.debug("[narrative] 回复守卫异常: %r", exc)
        return value, []
    fixed, night_hits = _night_reply_rule(fixed or value, facts)
    return fixed or value, list(violations) + night_hits


def diary_day_facts(plugin, day):
    """目标日期的客观事实（只读唯一视图）：宵夜/出门/生病/夜间活动。"""
    day_text = str(getattr(day, "isoformat", lambda: day)())[:10]
    facts = narrative_facts(day_view(plugin, day_text))
    return {"day": day_text,
            "snack": bool(facts.get("snack")),
            "out": bool(facts.get("out")),
            "sick": bool(facts.get("sick")),
            "late_activity": bool(facts.get("late_activity")),
            "hospital": bool(facts.get("hospital")),
            "meal": bool(facts.get("meal")),
            "together": bool(facts.get("together")),
            "meals": list(facts.get("meals") or []),
            "places": list(facts.get("places") or [])}


def diary_fact_mismatch(plugin, day, text):
    """日记和当天事实「矛盾」吗？只查矛盾，不查遗漏（事实唯一来源：build_day_view）。

    日记写「没吃宵夜 / 没出门 / 一直在家 / 没生病」，但日程/账本/块日记/健康有相反事实
    → True；日记只是没提宵夜、没提某顿饭、没提某次出门 → False（遗漏不算不一致，不重写）。
    """
    _fixed, violations = narrative_check(
        text, day_view(plugin, day), "diary", plugin=plugin)
    return bool(violations)


# ---------------- 今天具体互动：素材里没有就不许写（2026-11-17） ----------------
#
# 实锤：当天 messages 表 0 条聊天，块日记却写着“想起他早上在微信上提醒我多喝水”。
# 根因是提示词里拿“他提醒我多喝水”当示例讲主客体反转，模型把示例当成了素材。
# 只靠提示词纠不住，这里补一条确定性的按句清洗：删/改“他今天做过具体互动”的句子，
# 但不整段丢掉情绪（换成不声称今天发生过的想念句）。

#: 这些时间词说明句子在讲今天；没有时间词就不判定（“他提醒我多喝水”不带时间，
#: 可能是转述素材，交给上游事实闸，不在这里误杀）。
_TODAY_MARKERS = ("今天", "今日", "今早", "早上", "上午", "中午", "下午", "晚上",
                  "刚才", "刚刚", "在微信上", "在飞书上", "微信上", "飞书上")
#: 具体互动动词（用户报障里的那一类）。
_INTERACTION_VERBS = ("提醒", "说", "问", "发消息", "打电话", "叮嘱", "关心",
                      "让我", "叫我", "跟我说", "告诉我")
#: 讲“以前”的句子不算声称今天发生，是真·安全写法。
_PAST_MARKERS: tuple[str, ...] = ("以前", "之前", "曾经", "那会儿", "那天", "上次", "从前")
_SUBJECTS = ("他", "老公")
_SAFE_INTERACTION_LINE = "突然想起他以前叮嘱过我的话，心里软了一下。"
#: 兜底句不再只有一句：多天写日记时同一句话反复出现会变成模板（2026-11-22）。
#: 都只写「以前」的想念，不声称今天发生过具体互动（不含今天的时间词/互动动词）。
_SAFE_INTERACTION_LINES = (
    _SAFE_INTERACTION_LINE,
    "也不知道他这会儿在忙什么，忽然有点想他。",
    "脑子里闪过以前他说过的话，自己笑了笑。",
    "有些话他以前说过，我一直记着。",
    "想起以前他关心我的样子，鼻子有点酸。",
    "忽然有点想他，也不知道他忙不忙。",
    "他以前总念叨我要照顾好自己，我没忘。",
)

#: 长得像人名、其实是物品/文件/食物名的结尾字：人名兜底不许替换这类词。
_THING_SUFFIX = frozenset(
    "粉面饭粥菜肉茶奶汤包饼饺锅串果酒水蛋糖瓜豆件档表纸本书稿"
    "衣物鞋裙裤袜帽袋箱柜门窗床被枕巾杯碗盘筷勺刀叉机脑视话灯"
    "墙地砖朵枝叶花草树米油盐酱醋壶盆店铺街路站场院校司楼房"
    "屋车船港园贵好差慢快累忙热冷香甜苦酸辣咸淡新旧多少高低"
    "长短远近早晚玩睡醒饿渴饱困疼痛红绿蓝白黑亮暗乱脏净空满"
)


#: 不像人名的字：地点/方位/助词/动作词。foreign_names 会把「家刷剧」「家看剧」
#: 这类「地点+动词」当候选，人名不会带这些字（2026-11-22 复跑「一个人在一个朋友、刷手机」）。
_NON_NAME_CHARS = frozenset("家在一的了着过上下里外来去回跟和与把被给对向从到再又还就才也都")
#: 长得像「姓+名」、其实是常用词：人名兜底不许换（「终于说」≠ 有人叫终于）。
_NOT_PERSON_WORDS = frozenset("""
终于 马上 后来 本来 原来 当然 果然 居然 突然 忽然 已经 曾经 直接 真正 其实
确实 清楚 明白 容易 简单 麻烦 及时 顺便 随意 故意 特意 万一 向来 仍然 仍然
方法 方便 方向 方面 方案 高兴 常常 实在 时间 白天 一直 一样 一定 一般 一边
文章 金额 石头 关系 关于 对于 由于 因为 所以 但是 如果 虽然 而且 不过 然后
将来 将近 刚刚 刚才 现在 目前 以后 以前 之后 之前 时候 什么 怎么 多少
沙发 米饭 牛肉 牛奶 文档 公司 公园 阳台 商场 医院 学校 地铁 公交 电梯
电话 手机 电脑 电视 冰箱 空调 被子 枕头 毛巾 咖啡 奶茶 火锅 面条 鸡蛋
""".split())


def looks_like_person_name(name):
    """粗糙但保守的人名判定：只在名字像人时才替换（防止「文档/牛肉粉/终于」被换掉）。"""
    text = str(name or "").strip()
    if len(text) < 2 or len(text) > 3:
        return False
    if text in _NOT_PERSON_WORDS:
        return False
    # 人名不会带地点/方位/助词/动作字（「家刷剧」「家发呆」这类直接排除）。
    if any(char in _NON_NAME_CHARS for char in text):
        return False
    # 人名不会带物品/数量/形容类字（「家店贵」「牛肉粉」「文档」这类整词直接排除）。
    if any(char in _THING_SUFFIX for char in text[1:]):
        return False
    return True


def safe_interaction_line(day="", seed=""):
    """给「今天这句具体互动被删掉」补一句兜底：按日期轮换，避免跨天复读。"""
    lines = _SAFE_INTERACTION_LINES
    if not lines:
        return ""
    index = 0
    text = str(day or "").strip()
    if text:
        try:
            import datetime as _dt
            index = _dt.date.fromisoformat(text[:10]).toordinal() % len(lines)
            return lines[index]
        except (ImportError, TypeError, ValueError):  # cleanup: 日期解析失败走哈希轮换
            # intentional: 配置/旧值/可选依赖/清理失败按默认值处理
            pass
    token = str(seed or day or "").encode("utf-8", "ignore")
    if token:
        index = int(hashlib.md5(token).hexdigest(), 16) % len(lines)
    return lines[index]


def scrub_names_guarded(text, first="一个朋友", later="一个同事"):
    """名单外人名兜底，但只替换真的「像人名」的候选。

    2026-11-22 实锤：amo_immersion_guard.scrub_names 把「文档」「牛肉粉」这类
    普通词当人名换成「一个朋友/一个同事」，日记读起来直接崩（吃一个朋友）。
    这里保留防捏造人名的能力，只加一层「像不像人」的过滤。
    """
    value = str(text or "")
    if not value:
        return value
    try:
        try:
            from .amo_immersion_guard import foreign_names as _foreign
        except ImportError:
            from amo_immersion_guard import foreign_names as _foreign
        hits = [name for name in (_foreign(value) or ()) if looks_like_person_name(name)]
    except Exception as exc:  # 人名检测失败不能拖垮日记
        logger.debug("[amo] 日记人名兜底跳过: %s" % exc)
        return value
    if not hits:
        return value
    for index, name in enumerate(hits):
        if name:
            value = value.replace(name, first if index % 2 == 0 else later)
    logger.warning("[amo] 兜底替换名单外人名: %s", hits)
    return value


def has_chat_material(facts_text):
    """素材里到底有没有聊天记录（[他对我说] / 线上聊天 / 微信 / 飞书）。"""
    text = str(facts_text or "")
    return any(word in text for word in ("线上聊天", "[他对我说]", "[我对他说]",
                                          "微信", "飞书"))


#: 同居在家时可以自然扩写的日常动作（即使这一小时没有聊天素材）。
_HOME_DAILY = (
    "看电视", "做饭", "煮", "递", "坐在", "坐", "靠着", "靠", "笑", "说", "聊",
    "吃", "睡", "洗", "收拾", "晾", "抱", "亲", "摸头", "陪",
)


def claims_today_interaction(sentence, facts_text="", has_chat=None, partner_home=False):
    """这一句是否在声称“今天他做过具体互动”，而素材里没有对应聊天。

    纯判定，方便测试直接反向验证：去掉这个判定，清洗就失效。
    """
    text = str(sentence or "")
    if not text.strip():
        return False
    if not any(word in text for word in _SUBJECTS):
        return False
    if not any(word in text for word in _TODAY_MARKERS):
        return False
    if not any(word in text for word in _INTERACTION_VERBS):
        return False
    if any(word in text for word in _PAST_MARKERS):
        return False
    if partner_home and any(word in text for word in _HOME_DAILY):
        # 同居在家：日常动作可以自然扩写；亲密/新事件仍要素材支撑。
        return False
    chat = True if has_chat else has_chat_material(facts_text)
    if not chat:
        return True
    # 有聊天素材时，只有素材里确实提到这句的互动词才放行（“他提醒我多喝水”在 facts 里）。
    facts = str(facts_text or "")
    return not any(word in facts for word in _INTERACTION_VERBS if word in text)


def strip_fabricated_interaction(text, facts_text="", has_chat=None,
                                 safe_line=_SAFE_INTERACTION_LINE, partner_home=False,
                                 day=""):
    """按句删/改“素材里没有、却声称今天发生”的互动；其余句子（情绪/体感）原样保留。

    day 给了就用按日期轮换的兜底句（跨天不复读同一句）；不给保持原来的固定句，
    兼容旧的调用方与测试。
    """
    out: list[Any] = []
    stripped = 0
    for sentence in _sentences(text):
        if not sentence.strip():
            continue
        if claims_today_interaction(sentence, facts_text, has_chat, partner_home=partner_home):
            stripped += 1
            replacement = str(safe_line or "")
            if day and replacement == _SAFE_INTERACTION_LINE:
                replacement = safe_interaction_line(day, sentence)
            if replacement and (not out or out[-1] != replacement):
                out.append(replacement)
            continue
        out.append(sentence)
    if stripped:
        logger.info("[DIARY_GUARD] fabricated_interaction stripped=%d has_chat=%s",
                    stripped, bool(has_chat or has_chat_material(facts_text)))
    return "".join(out)


_SYSTEM_LOG = re.compile(r"\[[^\]]*(?:系统|日程|提示|tool|thought|又变)[^\]]*\]"
                          r"|<thought>.*?</thought>")
_EXTREME_WORDS = re.compile(r"(不想活|自杀|去死|活着没意思|结束生命|撑不下去了?)")


def sanitize_privacy(text):
    """聊天里别用“系统加密/上锁”这种冷冰冰的词。"""
    value = str(text or "")
    value = re.sub(r"(?:系统)?加密(?:了|的)?|上锁(?:了|的)?", "才不给你看", value)
    return value


def clean_schedule_text(text):
    """日程只留客观事实：去平台名/系统标签/结尾的“好想老公”。"""
    value = normalize_platform(str(text or ""))
    value = _SYSTEM_LOG.sub("", value)
    value = re.sub(r"[，,。！？]?\s*(?:突然)?好想老公.*$", "", value).strip(" ，,。")
    return value


#: 约会当天不该出现的“隔空”措辞（模型偶尔还是会把当面说话写成微信）
_TOGETHER_CHAT = (
    ("在微信上跟老公聊了几句，心里稍微踏实了点", "当面跟老公说了几句，心里踏实多了"),
    ("在微信上跟老公聊了几句", "当面跟老公说了几句"),
    ("在微信里跟老公聊了几句", "当面跟老公说了几句"),
    ("微信上跟老公聊了几句", "当面跟老公说了几句"),
    ("在微信上聊了几句", "当面说了几句"),
    ("在微信上问我要不要紧", "当面问我要不要紧"),
)


def rewrite_together_chat(text):
    """dating=True 时把“微信上聊了几句”这种隔空措辞改成面对面（确定性兜底）。"""
    value = str(text or "")
    for old, new in _TOGETHER_CHAT:
        value = value.replace(old, new)
    return value


def sanitize_diary(text, address="老公", together=False):
    """把虚构互动改写成合法的思念/线上聊天；同时清理自创称呼/系统日志/极端负面。

    together=True（约会时段确实在一起）时不再把「他抱着我/他来看我」
    改写成「好想老公」——那是真实发生的面对面互动。
    """
    value = normalize_platform(str(text or ""))
    value = _SYSTEM_LOG.sub("", value)
    value = _EXTREME_WORDS.sub("委屈得只想躲进被子里哭一场", value)
    for bad in BAD_NAMES:
        value = value.replace(bad, address)
    out: list[Any] = []
    for sentence in _sentences(value):
        if not sentence.strip():
            continue
        if not together and (_FABRICATED.search(sentence) or "挂了电话" in sentence):
            if any(marker in sentence for marker in _ONLINE_MARKERS):
                replacement = "在微信上跟%s聊了几句，心里稍微踏实了点。" % address
            else:
                replacement = "一个人待着，突然好想%s。" % address
            # 连续两句都被改写时不要重复同一句
            if not out or out[-1] != replacement:
                out.append(replacement)
            continue
        out.append(sentence)
    cleaned = "".join(out)
    # 防线四：名单外人名绝不入库（日记也兜一层）；但别把物品/食物名当人名换掉。
    cleaned = scrub_names_guarded(cleaned)
    if not together:
        cleaned = _FABRICATED.sub("好想" + address, cleaned)
    cleaned = cleaned.replace("挂了电话", "")
    return cleaned

# ---------------- 大日记收尾守卫（2026-11-22）：做饭证据 / 场景过渡 ----------------
#
# 两条都是「生成层/守卫层」的确定性补强，不是放宽核对器：
# 1) 当天素材里没有做饭证据（没有「做饭/炒/煮/下厨/蒸/煎」这类块活动），
#    日记却写「煎了两根油条/炒了两个菜」——把强做饭断言改成不声称做饭的说法；
# 2) 相邻两句都写了地点、地点组不交叉、后一句又没有过渡词——补一个「后来，」，
#    让场景切换读起来不是瞬移。
# 核对器（work/sim/diary_checks.py）的判定口径（地点组、过渡词、过去/未来句）
# 与这里保持一致；核对器仍是独立实现，不会 import 本文件。

#: 场景地点组（与核对器同一张词表：核对器先判，本守卫必须至少同样敏感）
_SCENE_GROUPS = (
    ("home", ("家里", "家中", "在家", "到家", "回家", "出租屋", "客厅", "卧室", "沙发",
              "厨房", "阳台", "被窝", "床上", "躺床", "卫生间", "浴室", "屋里", "家门")),
    ("office", ("写字楼", "办公室", "会议室", "工位", "公司", "单位", "办公桌")),
    ("mall", ("商场", "专柜", "逛街", "超市", "步行街", "试衣间", "购物袋")),
    ("restaurant", ("咖啡店", "咖啡馆", "奶茶店", "小吃街", "餐厅", "饭店", "饭馆",
                    "食堂", "小馆", "面馆", "粉店", "火锅店", "牛肉粉", "咖啡")),
    ("road", ("车上", "车里", "打车", "出租车", "网约车", "地铁", "公交", "车厢",
              "路上", "开车", "电动车", "小电驴", "站台")),
    ("street", ("街上", "街边", "街头", "路边", "外面", "户外")),
    ("scenic", ("游乐园", "动物园", "植物园", "风景区", "爬山", "海边", "沙滩",
                "景区", "景点", "公园", "山上", "古镇")),
    ("hotel", ("酒店", "宾馆", "民宿", "旅馆", "客栈")),
    ("hospital", ("医院", "诊所", "挂号", "候诊", "输液", "药店")),
    ("gym", ("健身房", "跑步机")),
    ("school", ("图书馆", "教室", "学校", "宿舍", "操场")),
)
_SCENE_WORD_GROUP: dict[str, str] = {}
for _scene_group, _scene_words in _SCENE_GROUPS:
    for _scene_word in _scene_words:
        _SCENE_WORD_GROUP.setdefault(_scene_word, _scene_group)
_SCENE_WORDS = sorted(_SCENE_WORD_GROUP, key=len, reverse=True)

#: 过渡词（与核对器 connectors 同一张表；只在这个表里没有词时才补）
_SCENE_TRANSITIONS = ("然后", "后来", "接着", "之后", "下午", "晚上", "中午", "早上",
                      "出门", "回家", "到家", "到了", "路上", "从", "等", "再", "才",
                      "就", "过了")


def scene_groups(text):
    """一句话里出现的地点组（按词表，和核对器同口径）。"""
    value = str(text or "")
    groups = set()
    for word in _SCENE_WORDS:
        if word in value:
            groups.add(_SCENE_WORD_GROUP[word])
    return groups


def add_scene_transitions(text):
    """相邻句换地点却没有过渡词时，在后一句前补「后来，」（只补连接词，不加事实）。"""
    value = str(text or "")
    if not value:
        return value
    added = 0
    out = []
    previous: set[str] = set()
    for sentence in _sentences(value):
        if not sentence.strip():
            out.append(sentence)
            continue
        groups = scene_groups(sentence)
        if (groups and previous and not (groups & previous)
                and not any(word in sentence for word in _SCENE_TRANSITIONS)):
            sentence = "后来，" + sentence.lstrip()
            added += 1
            groups = scene_groups(sentence)
        out.append(sentence)
        if groups:
            previous = groups
    if added:
        logger.info("[DIARY_GUARD] scene_transition added=%d", added)
    return "".join(out)


#: 做饭证据词：当天日程块/账本/素材里出现过任意一个，就不算「无证据做饭」。
_COOK_EVIDENCE_WORDS = ("做饭", "炒", "煮", "下厨", "蒸", "煎", "炖", "炸", "烤",
                        "焖", "煲", "洗碗")
#: 没有做饭证据时，把核对器会判成「强动作」的做饭断言换掉（不声称今天做饭）。
_COOK_CLAIM_REPLACEMENTS = (
    ("下厨", "进厨房"),
    ("做饭", "弄饭"),
    ("炒菜", "弄菜"),
    ("炒了", "吃了"),
    ("蒸了", "吃了"),
    ("煎了", "吃了"),
)
_PAST_MARKERS = ("昨天", "前天", "以前", "之前", "上次", "那次", "当年", "小时候",
                 "前阵子", "前两天", "去年")
_FUTURE_MARKERS = ("明天", "后天", "下次", "改天", "以后", "将来", "要是", "如果",
                   "回头", "等有空", "等放假", "希望", "打算", "准备去", "有机会")
_REFLECTIVE_MARKERS = ("想起", "想到", "梦到", "听说", "记得", "回忆", "刷到", "看到")


def _cook_clause_muted(clause):
    """过去/未来/回忆句不算「今天做饭」的断言（与核对器 _claim_context 同口径）。"""
    text = str(clause or "")
    return any(marker in text for marker in
               (_PAST_MARKERS + _FUTURE_MARKERS + _REFLECTIVE_MARKERS))


def strip_ungrounded_cooking(text, facts_text=""):
    """当天素材没有做饭证据时，去掉日记里的强做饭断言。返回清洗后的正文。"""
    value = str(text or "")
    if not value:
        return value
    evidence = str(facts_text or "")
    if any(word in evidence for word in _COOK_EVIDENCE_WORDS):
        return value
    hits = 0
    out = []
    for part in re.split(r"([，,。！？!?；;\n])", value):
        if not part:
            continue
        if part in "，,。！？!?；;\n":
            out.append(part)
            continue
        clause = part
        if not _cook_clause_muted(clause):
            for old, new in _COOK_CLAIM_REPLACEMENTS:
                if old in clause:
                    clause = clause.replace(old, new)
                    hits += 1
        out.append(clause)
    if hits:
        logger.info("[DIARY_GUARD] ungrounded_cooking stripped=%d", hits)
    return "".join(out)

#: 跨天复读守卫：整句在别的日子出现过就删掉（同一句连看几天会像模板）。
_REPEAT_MIN_BODY = 8


def sentence_body(sentence):
    """句子去掉标点空白后的正文（和核对器模板指标同口径）。"""
    return re.sub(r"[，,。！？!?；;\s]", "", str(sentence or ""))


def strip_repeated_sentences(text, previous_texts, day=""):
    """删掉和前一天/前两天完全相同的整句（模板复读），其余句子原样保留。"""
    value = str(text or "")
    if not value:
        return value
    previous = [str(item or "") for item in (previous_texts or ()) if str(item or "")]
    if not previous:
        return value
    known = set()
    for old in previous:
        for sentence in _sentences(old):
            body = sentence_body(sentence)
            if len(body) >= _REPEAT_MIN_BODY:
                known.add(body)
    if not known:
        return value
    out = []
    dropped = 0
    for sentence in _sentences(value):
        body = sentence_body(sentence)
        if len(body) >= _REPEAT_MIN_BODY and body in known:
            dropped += 1
            continue
        out.append(sentence)
    if dropped:
        logger.info("[DIARY_GUARD] repeated_sentence stripped=%d day=%s", dropped, day)
        cleaned = "".join(out).strip()
        return cleaned if len(cleaned) >= 10 else value
    return value


#: 加班断言（核对器 OVERTIME_RE 的同一批强说法）；日程没有加班证据时改成不声称加班。
#: 「熬到十点才睡」这种作息句不算加班，这里不动它，只处理落点是走/下班/收工的句子。
_OVERTIME_REPLACEMENTS = (
    ("还在公司", "还在单位忙"),
    ("晚下班", "下班晚了点"),
    ("加班", "多忙了会儿"),
    ("加到", "忙到"),
)


def strip_ungrounded_overtime(text, has_overtime=False):
    """当天 18:30 后没有非居家/加班块时，去掉日记里的加班断言，避免写成加班。"""
    value = str(text or "")
    if not value or has_overtime:
        return value
    hits = 0
    out = []
    for part in re.split(r"([，,。！？!?；;\n])", value):
        if not part:
            continue
        if part in "，,。！？!?；;\n":
            out.append(part)
            continue
        clause = part
        for old, new in _OVERTIME_REPLACEMENTS:
            if old in clause:
                clause = clause.replace(old, new)
                hits += 1
        # 「忙到/熬到十点才走」这类才字句：去掉「才」，不再声称加班晚走
        clause = re.sub(r"(忙到[^，。！？；;]{0,4}点)才(?=[走下班离回家出收])",
                        r"\1", clause)
        clause = re.sub(r"(熬到[^，。！？；;]{0,4}点)才(?=[走下班离回家出收])",
                        r"\1", clause)
        out.append(clause)
    if hits:
        logger.info("[DIARY_GUARD] ungrounded_overtime stripped=%d", hits)
    return "".join(out)


#: 消费断言：没有账本/消费事实时日记不能声称花钱（核对器 fabricated/spend）。
_SPEND_EVIDENCE_WORDS = ("消费", "账单", "付款", "花了")
_SPEND_CLAIM_REPLACEMENTS = (
    (re.compile(r"花了(?:多少|不少|很多|点|[0-9一二三四五六七八九十百千]+)[^，。！？；]{0,4}钱"),
     "也没花什么钱"),
    (re.compile(r"花了(?=[0-9一二三四五六七八九十百千])"), "用了"),
    (re.compile(r"心疼[^，。！？；]{0,6}钱"), "有点舍不得"),
    (re.compile(r"花钱"), "花什么钱"),
    (re.compile(r"账单"), "开销"),
    (re.compile(r"付款"), "付了钱"),
    (re.compile(r"消费了"), "用了一些"),
)


def strip_ungrounded_spending(text, facts_text=""):
    """当天素材/账本里没有消费事实时，去掉日记里的花钱断言（仍可写「没花什么钱」）。"""
    value = str(text or "")
    if not value:
        return value
    if any(word in str(facts_text or "") for word in _SPEND_EVIDENCE_WORDS):
        return value
    hits = 0
    out = []
    for part in re.split(r"([，,。！？!?；;\n])", value):
        if not part:
            continue
        if part in "，,。！？!?；;\n":
            out.append(part)
            continue
        clause = part
        for pattern, replacement in _SPEND_CLAIM_REPLACEMENTS:
            if pattern.search(clause):
                clause = pattern.sub(replacement, clause)
                hits += 1
        out.append(clause)
    if hits:
        logger.info("[DIARY_GUARD] ungrounded_spending stripped=%d", hits)
    return "".join(out)
