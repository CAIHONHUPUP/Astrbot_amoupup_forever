# -*- coding: utf-8 -*-
"""日记/日程的「反加戏」守卫（2026-11-15）。

素材包里没有的事（老公打电话/抱我/送药/来看我）不许写；
线上聊天只能写成「在微信/飞书上聊了几句」；称呼不许自创（如「阿明」）。
"""
from __future__ import annotations
from typing import Any, TYPE_CHECKING

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
【严禁虚构】：素材包里没写的动作不要加（例如他突然跑来送花）；「他就在我对面」只限约会时段，其他时间一律写一个人/手机。
"""
_RULES_ALONE = """【严禁虚构】：素材包里没写的人和互动，一律不能出现——老公打电话、老公抱着我、老公摸我的头、老公给我买药、老公来看我、他出现在我身边、他在旁边翻身、他躺在我旁边、他还在屋里、他在家、屋里就我俩。
【约会结束·各回各家】：约会一结束他就回他自己的住处，你一个人回出租屋，剩下的时间只能手机/视频联系；严禁写两个人回同一个家、同一个屋檐下过日子、他抱着我、他就守在你身边。
【线上/线下】：老公今天只可能在飞书/微信上跟你聊天，现实里他在忙、没有出现在你身边；写聊天只能写「在微信/飞书上跟老公聊了几句」，绝不能写「刚跟老公通完电话」「他打电话来关心我」。
"""
_RULES_TAIL = """【思念的合法写法】示例：「一个人在出租屋里，看着黑漆漆的房间，突然好想他。要是他能在身边抱抱我就好了……算了，不打扰他了，我自己喝点热水吧。」
【称呼】：只能叫「老公」「亲爱的」或「他」；不许出现「阿明」这类自创名字。
"""


#: 聊天≠语音：没明说语音/连麦/电话/视频，日记里就不许写这些（2026-11-17 用户要求）。
NO_VOICE_RULE = ("\n【聊天≠语音】聊天记录只是文字消息：素材里没有明确出现「语音/连麦/电话/视频/通话」，就不能写语音、连麦、电话、视频通话、耳机里的声音；一律写成「在微信上聊天/发消息」。素材里没有明确说「约好/说好」的，也不许写成约定。")

#: 2026-11-18：日记里出现「病假扣了一个朋友和日薪」「迟到罚款」——钱和考勤不进日记。
NO_MONEY_RULE = ("\n【钱和考勤不进日记】账本/工资/扣款的具体金额、余额、迟到罚款一律不写进日记；"
                 "病假当天不许写迟到/早退；「全勤」不许写成「朋友」或别的词；"
                 "必须提钱时只写「这个月工资要少一点」这种模糊感受，不写数字。")


def fact_rules(together=False):
    """日记的系统硬规则；约会当天换成「面对面」版本，别把老公写成网友。"""
    body = _RULES_TOGETHER if together else _RULES_ALONE
    return _RULES_HEAD + body + _RULES_TAIL + NO_VOICE_RULE + NO_MONEY_RULE


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
            from .photo_scene import zone_for_place as _zone
        except ImportError:
            from photo_scene import zone_for_place as _zone
        return str(_zone(str(place or "")) or "")
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
    except Exception:  # cleanup: 极简 fake 没有 setattr 也不影响主流程
        pass


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
        except Exception:
            day = None
    use_cache = (now is None) if cache is None else bool(cache)
    if use_cache:
        cached = getattr(plugin, "_narrative_view_cache", None)
        if isinstance(cached, dict):
            try:
                fresh = (cached.get("day") == day
                         and time.monotonic() - float(cached.get("at") or 0.0)
                         <= _VIEW_CACHE_SECONDS)
            except Exception:
                fresh = False
            if fresh and isinstance(cached.get("view"), dict):
                return cached["view"]
    view = day_view(plugin, day, now)
    if use_cache:
        try:
            plugin._narrative_view_cache = {
                "day": day, "at": time.monotonic(), "view": view}
        except Exception:  # cleanup: 缓存写不进去也不影响视图本身
            pass
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
        return [item for item in (_read_items(plugin, day_text) or [])
                if isinstance(item, dict)]
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
    current = _view_current(view, moment)
    if current is not None:
        block = _block_of_event(blocks, current)
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
    if not facts.get("together") and _presence_claim(body):
        out.append(_diary_hit("companion", body, "当天没有面对面约会事实"))
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
_PAST_MARKERS = ("以前", "之前", "曾经", "那会儿", "那天", "上次", "从前")
_SUBJECTS = ("他", "老公")
_SAFE_INTERACTION_LINE = "突然想起他以前叮嘱过我的话，心里软了一下。"


def has_chat_material(facts_text):
    """素材里到底有没有聊天记录（[他对我说] / 线上聊天 / 微信 / 飞书）。"""
    text = str(facts_text or "")
    return any(word in text for word in ("线上聊天", "[他对我说]", "[我对他说]",
                                          "微信", "飞书"))


def claims_today_interaction(sentence, facts_text="", has_chat=None):
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
    chat = True if has_chat else has_chat_material(facts_text)
    if not chat:
        return True
    # 有聊天素材时，只有素材里确实提到这句的互动词才放行（“他提醒我多喝水”在 facts 里）。
    facts = str(facts_text or "")
    return not any(word in facts for word in _INTERACTION_VERBS if word in text)


def strip_fabricated_interaction(text, facts_text="", has_chat=None,
                                 safe_line=_SAFE_INTERACTION_LINE):
    """按句删/改“素材里没有、却声称今天发生”的互动；其余句子（情绪/体感）原样保留。"""
    out: list[Any] = []
    stripped = 0
    for sentence in _sentences(text):
        if not sentence.strip():
            continue
        if claims_today_interaction(sentence, facts_text, has_chat):
            stripped += 1
            replacement = str(safe_line or "")
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
    try:  # 防线四：名单外人名绝不入库（日记也兜一层）
        try:
            from .amo_immersion_guard import scrub_names as _scrub
        except ImportError:
            from amo_immersion_guard import scrub_names as _scrub
        cleaned = _scrub(cleaned)
    except Exception as exc:  # 人名兜底失败不能拖垮日记，但要留痕
        logger.debug("[amo] 日记人名兜底跳过: %s" % exc)
    if not together:
        cleaned = _FABRICATED.sub("好想" + address, cleaned)
    cleaned = cleaned.replace("挂了电话", "")
    return cleaned
