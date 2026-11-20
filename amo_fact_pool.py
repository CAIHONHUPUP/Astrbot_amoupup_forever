# -*- coding: utf-8 -*-
"""事实池（Phase 3）：日记/日程只能基于当天可溯源的客观事实写。

事实的组装已经收敛到 amo_fact_timeline（当天/时段唯一事实时间线）；本模块只保留：
- 对外入口 build_fact_pool / render_prompt / validate_text（调用方与返回结构不变）；
- 事后文本拦截与地点兜底（纯文本规则，不是事实来源）；
- 旧版私有函数名的兼容转发（chat_facts / activity_facts / _routine_facts /
  _slot_window / _minutes / _filter_window_facts / _filter_routine_facts）。

规则（沿用）：今天 00:00-现在；私聊才进池；[他对我说]/[我对他说] 标签；平台名统一
“微信”；空聊天/只花钱/睡到几点都要成为硬事实，生成后再做代码级兜底。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

import logging
logger = logging.getLogger("amo")

import re

try:
    from .amo_diary_guard import sanitize_diary, strip_fabricated_interaction
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_diary_guard import sanitize_diary, strip_fabricated_interaction

try:  # 地点物理法则：日记只写当前地点里有的东西（2026-11-16）
    from . import amo_location_rules as location_rules
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_location_rules as location_rules

try:  # 唯一事实时间线（Phase 3）
    from . import amo_fact_timeline as fact_timeline
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_fact_timeline as fact_timeline

_EXTREME = re.compile(r"(不想活|自杀|去死|活着没意思|结束生命|撑不下去了?)")
_NEW_CLOTHES = re.compile(r"新买的?(?:一条|一件|双|条|件)?[^，。！？\s]{0,6}(?:裙|衣|鞋|裤|外套)")
_BOUGHT = re.compile(r"买了(?:一个|一件|一条|一双|份|杯|只|盒)?[^，。！？\s]{1,10}")
_REVERSE = re.compile(r"我(?:让|叫|提醒|劝|跟|和)他(多喝|吃药|早点|注意|休息|喝水|吃饭)")


# ---------------- 兼容入口：统一转发到唯一时间线 ----------------

def chat_facts(rows, now=None, window=None):
    """私聊事实（唯一实现见 amo_fact_timeline.chat_entries）。"""
    return fact_timeline.to_facts(fact_timeline.chat_entries(rows, window))


def activity_facts(items, now=None):
    """活动/过渡块事实（唯一实现见 amo_fact_timeline.activity_entries）。"""
    return fact_timeline.to_facts(fact_timeline.activity_entries(items, now))


def _routine_facts(items):
    """作息事实（唯一实现见 amo_fact_timeline.routine_entries）。"""
    return fact_timeline.to_facts(fact_timeline.routine_entries(items))


def _slot_window(hour_slot=None, slot_start=None, slot_end=None,
                 cutoff=fact_timeline.DAY_END_MINUTES):
    """旧的窗口解析入口（唯一实现见 amo_fact_timeline.slot_window）。"""
    return fact_timeline.slot_window(hour_slot, slot_start, slot_end, cutoff)


_minutes = fact_timeline.minutes_of


def _filter_window_facts(facts, window, category):
    """兼容入口：旧版事实 dict 的窗口裁剪 + 地点过滤。"""
    return fact_timeline.to_facts(fact_timeline.filter_window(
        fact_timeline.entries_from_facts(facts), window, category))


def _filter_routine_facts(facts, window, category):
    """兼容入口：旧版事实 dict 的作息硬事实过滤。"""
    return fact_timeline.to_facts(fact_timeline.trim_routine(
        fact_timeline.entries_from_facts(facts), window, category))


async def build_fact_pool(plugin, day, now=None, hour_slot=None, slot_start=None,
                          slot_end=None):
    """当天事实池；给了 slot_start/slot_end（或 hour_slot）就只取这个时段。

    小日记按 13:41-15:41 这样的真实日程块过滤聊天/账单/日程/活动，上一时段在家的
    外卖、被窝类事实不会进入医院时段；不给时段时（长日记）保留整天素材。
    事实只由 amo_fact_timeline 生产，返回结构仍是 {day, facts, meta}。
    """
    return await fact_timeline.build_pool(plugin, day, now=now, hour_slot=hour_slot,
                                          slot_start=slot_start, slot_end=slot_end)


def _scope_of(meta):
    """meta.location_scope 的兼容读法：老 pool 没写 scope 时按有无单地点推断。"""
    scope = str((meta or {}).get("location_scope") or "").strip()
    if scope in ("day", "slot"):
        return scope
    return "slot" if ((meta or {}).get("location") or (meta or {}).get("zone")) else "day"


def _location_prompt(meta):
    """给长日记的绝对地点约束句（开关关掉时返回空串）。"""
    return location_rules.prompt_clause(
        meta.get("location"), meta.get("zone"),
        bool(meta.get("location_rules_enabled", True)))


def render_prompt(pool):
    facts = list((pool or {}).get("facts") or [])
    meta = (pool or {}).get("meta") or {}
    together = bool(meta.get("date_together"))
    partner_home = bool(meta.get("partner_home")) and not together
    # 防线一：事实行必须带时间标签（补写昨天的日记时是 [昨日]，不是 [今日]）
    day_label = ""
    try:
        from .amo_immersion_guard import time_label as _time_label
    except ImportError:
        from amo_immersion_guard import time_label as _time_label
    if meta.get("day"):
        day_label = _time_label(meta.get("day"), meta.get("now")) + " "
    lines = [
        "【事实素材·唯一素材】下面是你今天真实发生的事，日记必须且只能基于它扩写：",
    ]
    if _scope_of(meta) == "day":
        _location_line = location_rules.day_prompt_clause(
            meta.get("places"), bool(meta.get("location_rules_enabled", True)))
    else:
        _location_line = _location_prompt(meta)
    if _location_line:
        lines.append(_location_line)
    if together:
        # 约会当天：物理状态是最高事实，聊天只是当面说的话（2026-11-15 修）
        lines += [
            "【物理场景事实·最高优先级】约会那段时间你们在一起：老公就在你对面，面对面坐在一起；"
            "这不是网恋、不是异地。约会开始前和结束之后他都回自己的住处，只能手机/微信联系。",
            "【互动素材】聊天记录只用来提取互动细节和情绪，不能用来判断“人在不在”。",
            "【财务语境】约会期间的转账和花钱都是面对面的：他就在你旁边买单，"
            "不许写成“他不在我身边所以转账给我”。",
            "【心理事实】可以尽情写当时的心情、害羞、开心，这些不算虚构。",
            "【严禁虚构】素材里没有的动作不要加；但“他就在我对面”“我们一起吃、一起走”"
            "是约会时段的事实，可以照写。",
        ]
    elif partner_home:
        lines += [
            "【物理事实·同居】你们住在一起；在家时他就在屋里（客厅/厨房/卧室），"
            "可以写日常互动：他看电视、他做饭、他递东西、你靠在他肩上。",
            "【他不在家】他上班/出差时不在；素材里没说他出门，就默认他在家。",
            "【心理事实】可以尽情写心情、害羞、开心，这些不算虚构。",
            "【严禁虚构】素材里没有的具体事件不要加（比如他突然买花回来）；"
            "日常相处的小动作可以自然扩写。",
        ]
    else:
        lines += [
            "【物理事实】只允许写素材里的地点/日程；你一个人在家就是在出租屋/卧室，不许写成他就在身边。",
            "【线上事实】素材里的聊天都是微信/飞书上的文字；只能写“在微信上聊了几句”，"
            "绝不能升级成“他打电话来/他来看我/他抱我”。",
            "【心理事实】可以尽情写想念、委屈、孤独、开心，这些不算虚构。",
            "【严禁虚构】素材里没有的动作和互动一律不能写：老公打电话、老公抱我、他摸我的头、"
            "他给我买药、他出现在我身边。",
        ]
    lines += [
        "【间接引语】写聊天只能转述大意，不许照抄原话；动作的主语不能和宾语对调："
        "素材里是他对你说的，就写他；是你对他说的，就写你。"
        "提示词里的任何举例都只是格式示意，绝不许当成今天发生的事实写进日记。",
        "【今天没发生的事】素材里没有的聊天、电话、见面、叮嘱，一律不许写成今天发生；"
        "想写他就写“想起他以前……”“有点想他”这种不声称今天发生的句子。",
        "【穿搭】只能写素材里的真实穿搭；今天没出门、没买新衣服，就不许写“穿新买的裙子出门”。",
        "【消费】素材只有金额的，就只写“今天花了多少钱，有点心疼”；账单备注没写物品，"
        "严禁编造买了什么。",
        "【作息】以【作息】素材为准；素材说你睡到 10 点，就不许写早起晨跑、8 点就醒。",
        "【情绪余波】如果昨天低落，今天即使没大事也要带一点疲惫的余波，不要秒变狂喜。",
        "【时间】只写今天 00:00 到现在的事，不许把昨天的事或明天的计划当成今天写；"
        "历史日记只读，不要改写昨天。",
    ]
    if not meta.get("has_chat") and not partner_home:
        lines.append("【今天没怎么聊天】老公今天很忙，你们一天没怎么说话。可以写“有点想他”，"
                     "但严禁脑补“他是不是不爱我了”“他是不是去找别人了”，各自忙碌很正常。")
        # 2026-11-17 实锤：模型把“间接引语”里的示例句当成素材，写进了一条当天
        # 没有任何聊天记录的块日记。硬规则不依赖任何举例，逐条列禁止的句式。
        lines.append("【没聊天就禁止具体互动·硬规则】素材里今天没有任何聊天记录，日记里"
                     "绝不能出现“他今天/早上/刚才提醒我、跟我说、问我、发消息、打电话、"
                     "叮嘱我、关心我、让我……”这类具体互动；只能写“想起他以前叮嘱过”"
                     "“有点想他”这种不声称今天发生过的句子。")
    lines += ["素材（JSON）："]
    lines += fact_timeline.render_lines(facts, day_label)
    if meta.get("outfit"):
        lines.append("今天的真实穿搭：%s" % meta["outfit"])
    lines.append(fact_timeline.render_json(facts))
    return "\n".join(lines)


def _fix_subject(value):
    """主客体反转：我提醒他多喝水 → 他提醒我多喝水（方向纠正）。"""
    return _REVERSE.sub(lambda m: "他提醒我" + m.group(1), value)


def _strip_interaction(text, facts_text, has_chat, partner_home=False):
    """确定性兜底：素材里没有聊天时，删/改“他今天提醒我”这类句。

    单独包一层是为了测试能反向验证：把这里换回原样，虚构句就会留在日记里。
    """
    return strip_fabricated_interaction(text, facts_text, has_chat, partner_home=partner_home)


def validate_text(text, pool):
    """事后拦截：系统日志、越界互动、主客体反转、空载脑补、消费/作息/极端情绪。"""
    value = sanitize_diary(text)
    value = fact_timeline._SYSTEM.sub("", value)
    value = re.sub(r"\[[^\]]{0,40}\]|<[^>]{0,40}>", "", value)
    value = fact_timeline._COMMAND.sub("", value)
    facts = (pool or {}).get("facts") or []
    meta = (pool or {}).get("meta") or {}
    facts_text = " ".join(str(f.get("detail") or "") for f in facts)
    value = _fix_subject(value)
    # 断言「今天他提醒我/在微信上跟我说」这类具体互动，但素材里根本没有聊天 → 删句。
    value = _strip_interaction(value, facts_text, meta.get("has_chat"),
                               partner_home=bool(meta.get("partner_home")))
    if "电话" not in facts_text and "通话" not in facts_text:
        value = value.replace("听见他声音", "想起他的声音")
        value = value.replace("通电话", "在微信上聊了几句")
    if not meta.get("has_offline_meet") and not meta.get("partner_home"):
        for phrase, repl in (("他来看我", "好想他来看我"), ("他抱着我", "好想被他抱着"),
                             ("他在我身边", "好想他在我身边")):
            value = value.replace(phrase, repl)
    if not meta.get("has_chat"):
        for phrase in ("他是不是不爱我了", "他是不是有别人了", "他不要我了", "他去找别的女人"):
            value = value.replace(phrase, "他今天大概很忙")
    if not meta.get("bought_clothes"):
        value = _NEW_CLOTHES.sub("衣服", value)
        value = _BOUGHT.sub("买了点东西", value)
    if meta.get("wake_late"):
        value = value.replace("早起", "起来得晚").replace("晨跑", "在家拉伸")
    value = _EXTREME.sub("委屈得只想躲进被子里哭一场", value)
    value = re.sub(r"\s+", " ", value).strip()
    if _scope_of(meta) == "day":
        return _guard_day_location(value, meta)
    return _guard_location(value, meta)


def _guard_day_location(value, meta):
    """整天日记只拦“当天没去过的地点专属词”，不按单地点 forbidden 删医院。"""
    places = list(meta.get("places") or [])
    if not places and meta.get("location"):
        places = [str(meta.get("location"))]
    if not places or not bool(meta.get("location_rules_enabled", True)):
        return value
    violations = location_rules.day_check(value, places, True)
    if not violations:
        return value
    filtered = location_rules.strip_day_violations(value, places, True)
    if filtered and len(filtered) >= 6 and not location_rules.day_check(filtered, places, True):
        logger.info("[DIARY_GUARD] place=%s violations=%s retry=0 fallback=0 scope=day",
                    "、".join(places), "、".join(violations))
        return filtered
    meta["location_fallback"] = True
    logger.info("[DIARY_GUARD] place=%s violations=%s retry=0 fallback=1 scope=day",
                "、".join(places), "、".join(violations))
    return location_rules.day_fallback_line()


def _guard_location(value, meta):
    """长日记的事后地点闸：命中禁止词先删句，删不干净就降级成地点兜底句。"""
    place = str(meta.get("location") or "").strip()
    zone = str(meta.get("zone") or "").strip()
    if not (place or zone) or not bool(meta.get("location_rules_enabled", True)):
        return value
    violations = location_rules.check(value, place, zone, True)
    if not violations:
        return value
    filtered = location_rules.strip_violations(value, place, zone, True)
    if filtered and len(filtered) >= 6 and not location_rules.check(filtered, place, zone, True):
        logger.info("[DIARY_GUARD] place=%s violations=%s retry=0 fallback=0",
                    place or zone, "、".join(violations))
        return filtered
    meta["location_fallback"] = True
    logger.info("[DIARY_GUARD] place=%s violations=%s retry=0 fallback=1",
                place or zone, "、".join(violations))
    return location_rules.fallback_line(place, zone)
