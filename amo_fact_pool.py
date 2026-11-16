# -*- coding: utf-8 -*-
"""事实池（2026-11-15 v2）：日记/日程只能基于当天可溯源的客观事实写。

规则：今天 00:00-现在；私聊才进池；[他对我说]/[我对他说] 标签；平台名统一“微信”；
空聊天/只花钱/睡到几点都要成为硬事实，生成后再做代码级兜底。
"""
from __future__ import annotations

import logging
logger = logging.getLogger("amo")

import datetime as _dt
import json
import re

try:
    from .amo_diary_guard import normalize_platform, sanitize_diary
except ImportError:
    from amo_diary_guard import normalize_platform, sanitize_diary

CARE_WORDS = ("关心", "想你", "想我", "多喝", "热水", "吃药", "早点", "注意", "休息",
              "转账", "哄", "抱", "亲", "爱你", "晚安", "早安", "生病", "感冒",
              "医院", "买药", "下班", "回家", "吃饭", "外卖")
_SYSTEM = re.compile(r"\[[^\]]*(?:系统|日程|提示|tool|thought|又变)[^\]]*\]"
                     r"|<thought>.*?</thought>|<[^>]{1,20}>", re.S)
_COMMAND = re.compile(r"(?:^|\s)/[\w\u4e00-\u9fa5]+")
_EXTREME = re.compile(r"(不想活|自杀|去死|活着没意思|结束生命|撑不下去了?)")
_NEW_CLOTHES = re.compile(r"新买的?(?:一条|一件|双|条|件)?[^，。！？\s]{0,6}(?:裙|衣|鞋|裤|外套)")
_BOUGHT = re.compile(r"买了(?:一个|一件|一条|一双|份|杯|只|盒)?[^，。！？\s]{1,10}")
_REVERSE = re.compile(r"我(?:让|叫|提醒|劝|跟|和)他(多喝|吃药|早点|注意|休息|喝水|吃饭)")


def clean_chat_text(text, limit=80):
    value = normalize_platform(str(text or ""))
    value = _COMMAND.sub(" ", value)
    value = _SYSTEM.sub(" ", value)
    value = re.sub(r"\[(?:转账|图片|语音|表情|视频|文件|红包)[^\]]*\]", " ", value)
    value = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9，。！？、,.!? ]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def _is_private(row):
    umo = str(row.get("umo") or "")
    if ":GroupMessage:" in umo:
        return False
    if ":FriendMessage:" in umo:
        return True
    return str(row.get("scope") or "") != "group"


def _tag(role):
    return "[我对他说]" if str(role) == "bot" else "[他对我说]"


def chat_facts(rows, now=None):
    """私聊 → 合并连续发言、打主客体标签；群聊不进私密日记。"""
    merged = []
    for row in rows or []:
        if not _is_private(row):
            continue
        if str(row.get("role") or "") not in ("user", "bot"):
            continue
        text = clean_chat_text(row.get("content"))
        if not text:
            continue
        role = str(row.get("role") or "user")
        if merged and merged[-1]["role"] == role and len(merged[-1]["text"]) < 120:
            merged[-1]["text"] += "；" + text
            merged[-1]["ts"] = row.get("created_at") or merged[-1]["ts"]
        else:
            merged.append({"role": role, "text": text, "ts": row.get("created_at") or 0})
    facts, seen = [], set()
    for item in merged:
        text = str(item["text"])
        if not any(word in text for word in CARE_WORDS):
            continue
        line = "%s：%s" % (_tag(item["role"]), text)
        if line in seen:
            continue
        seen.add(line)
        try:
            clock = _dt.datetime.fromtimestamp(float(item["ts"])).strftime("%H:%M")
        except Exception:
            clock = ""
        facts.append({"time": clock, "type": "线上聊天", "detail": line})
        if len(facts) >= 8:
            break
    return facts


def _routine_facts(items):
    """从当天日程推出真实作息：几点起、是否在家。"""
    facts = []
    wake = 0
    for item in items or []:
        if str(item.get("category") or "") != "sleep":
            continue
        try:
            end_h, end_m = [int(x) for x in str(item.get("end") or "").split(":")]
        except Exception:
            continue
        # 23:00-24:00 的「准备睡觉」也是 sleep，但它记录的是入睡而不是起床；
        # 只有上午结束的睡眠才算“睡到几点起”，24:00 当收尾忽略。
        if end_h >= 24 or end_h >= 12:
            continue
        wake = max(wake, end_h * 60 + end_m)
    if wake >= 9 * 60:
        facts.append({"time": "%02d:%02d" % (wake // 60, wake % 60), "type": "作息",
                      "detail": "今天睡到 %02d:%02d 才起" % (wake // 60, wake % 60)})
    if items and all(str(i.get("place") or "") in ("家", "出租屋", "卧室") for i in items):
        facts.append({"time": "", "type": "状态", "detail": "今天一直在家，没有出门"})
    return facts


async def build_fact_pool(plugin, day, now=None, hour_slot=None):
    now = now or _dt.datetime.now()
    if isinstance(day, str):
        try:
            day = _dt.date.fromisoformat(day)
        except Exception:
            day = now.date()
    day_start = int(_dt.datetime.combine(day, _dt.time.min).timestamp())
    day_end = min(int(now.timestamp()), day_start + 86400)
    try:
        from .amo_date_facts import date_presence_facts, date_windows, transfer_context
    except ImportError:
        from amo_date_facts import date_presence_facts, date_windows, transfer_context
    # 最高优先级：约会/同居的物理状态先落成硬事实，聊天记录只当互动素材（别反推“人在不在”）
    date_facts = date_presence_facts(plugin, day, hour_slot)
    facts = list(date_facts)
    try:
        items = list(plugin.state.schedule.get(day.isoformat()) or
                     plugin.state.db.get_schedule(day.isoformat()) or [])
    except Exception:
        items = []
    # 补写过去某天的日记时，不能用“现在几点”截断那天：整天都算已发生。
    same_day = day == now.date()
    cutoff = (now.hour * 60 + now.minute) if same_day else 24 * 60
    for item in items:
        start = str(item.get("start") or "")
        try:
            hh, mm = [int(x) for x in start.split(":")]
        except Exception:
            continue
        if hh * 60 + mm > cutoff:
            continue
        activity = str(item.get("activity") or "").strip()
        if not activity:
            continue
        facts.append({"time": start, "type": "日程",
                      "detail": "%s（%s）" % (activity, item.get("place") or "家")})
    facts.extend(_routine_facts(items))
    place = ""
    for item in items:
        start = str(item.get("start") or "")
        try:
            hh, mm = [int(x) for x in start.split(":")]
        except Exception:
            continue
        if hh * 60 + mm <= cutoff and item.get("place"):
            place = str(item.get("place"))
    if place:
        facts.append({"time": "", "type": "状态", "detail": "现在在%s" % place})
    try:
        ledger = plugin.wallet.ledger(200)
    except Exception:
        ledger = []
    for row in ledger:
        try:
            ts = float(row.get("ts") or 0)
        except Exception:
            continue
        if not (day_start <= ts < day_end):
            continue
        delta = int(row.get("delta_cents") or 0)
        reason = str(row.get("reason") or "")
        # 约会窗口内的收支不是“异地转账”，而是面对面买单
        try:
            _ctx = transfer_context(plugin, day, ts, delta, reason)
        except Exception:
            _ctx = None
        if _ctx:
            facts.append({"time": _dt.datetime.fromtimestamp(ts).strftime("%H:%M"),
                          "type": _ctx["type"], "detail": _ctx["detail"]})
            continue
        if delta < 0:
            facts.append({"time": _dt.datetime.fromtimestamp(ts).strftime("%H:%M"),
                          "type": "消费", "detail": "%s %.2f 元" % (reason, -delta / 100.0)})
        elif delta > 0:
            facts.append({"time": _dt.datetime.fromtimestamp(ts).strftime("%H:%M"),
                          "type": "收入", "detail": "%s %.2f 元" % (reason, delta / 100.0)})
    meta = await _build_fact_pool_part( day, day_end, day_start, facts, place, plugin)
    meta = dict(meta or {})
    meta["day"] = day.isoformat()
    meta["now"] = now.isoformat()
    meta["date_together"] = bool(date_facts)
    meta["date_hour"] = hour_slot
    if date_facts:
        meta["has_offline_meet"] = True
        meta["date_activity"] = str((date_facts[0] or {}).get("detail") or "")
    try:
        meta["date_windows"] = date_windows(plugin, day)
    except Exception:
        meta["date_windows"] = []
    return {"day": day.isoformat(), "facts": facts, "meta": meta}


def render_prompt(pool):
    facts = list((pool or {}).get("facts") or [])
    meta = (pool or {}).get("meta") or {}
    together = bool(meta.get("date_together"))
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
    if together:
        # 约会/同居当天：物理状态是最高事实，聊天只是当面说的话（2026-11-15 修）
        lines += [
            "【物理场景事实·最高优先级】今天你们在一起：老公就在你对面，面对面坐在一起；"
            "这不是网恋、不是异地。聊天记录只是你们当面说过的话，"
            "绝不能写成“他在微信上跟我说”“他不在我身边”。",
            "【互动素材】聊天记录只用来提取互动细节和情绪，不能用来判断“人在不在”。",
            "【财务语境】约会期间的转账和花钱都是面对面的：他就在你旁边买单，"
            "不许写成“他不在我身边所以转账给我”。",
            "【心理事实】可以尽情写当时的心情、害羞、开心，这些不算虚构。",
            "【严禁虚构】素材里没有的动作不要加；但“他就在我对面”“我们一起吃、一起走、一起回家”"
            "是事实，可以照写。",
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
        "【间接引语】写聊天必须转述，如“他问我要不要去看医生”；禁止直接照抄，"
        "更禁止主客体反转（许写“他提醒我多喝水”，不许写“我提醒他多喝水”）。",
        "【穿搭】只能写素材里的真实穿搭；今天没出门、没买新衣服，就不许写“穿新买的裙子出门”。",
        "【消费】素材只有金额的，就只写“今天花了多少钱，有点心疼”；账单备注没写物品，"
        "严禁编造买了什么。",
        "【作息】以【作息】素材为准；素材说你睡到 10 点，就不许写早起晨跑、8 点就醒。",
        "【情绪余波】如果昨天低落，今天即使没大事也要带一点疲惫的余波，不要秒变狂喜。",
        "【时间】只写今天 00:00 到现在的事，不许把昨天的事或明天的计划当成今天写；"
        "历史日记只读，不要改写昨天。",
    ]
    if not meta.get("has_chat"):
        lines.append("【今天没怎么聊天】老公今天很忙，你们一天没怎么说话。可以写“有点想他”，"
                     "但严禁脑补“他是不是不爱我了”“他是不是去找别人了”，各自忙碌很正常。")
    lines += ["素材（JSON）："]
    for fact in facts:
        lines.append("- %s[%s] %s%s" % (day_label, fact.get("type") or "事实",
                                      (fact.get("time") + " ") if fact.get("time") else "",
                                      fact.get("detail") or ""))
    if meta.get("outfit"):
        lines.append("今天的真实穿搭：%s" % meta["outfit"])
    lines.append("素材 JSON：" + json.dumps(facts, ensure_ascii=False))
    return "\n".join(lines)


def _fix_subject(value):
    """他提醒我多喝水 → 不许写成“我提醒他多喝水”。"""
    return _REVERSE.sub(lambda m: "他提醒我" + m.group(1), value)


def validate_text(text, pool):
    """事后拦截：系统日志、越界互动、主客体反转、空载脑补、消费/作息/极端情绪。"""
    value = sanitize_diary(text)
    value = _SYSTEM.sub("", value)
    value = re.sub(r"\[[^\]]{0,40}\]|<[^>]{0,40}>", "", value)
    value = _COMMAND.sub("", value)
    facts = (pool or {}).get("facts") or []
    meta = (pool or {}).get("meta") or {}
    facts_text = " ".join(str(f.get("detail") or "") for f in facts)
    value = _fix_subject(value)
    if "电话" not in facts_text and "通话" not in facts_text:
        value = value.replace("听见他声音", "想起他的声音")
        value = value.replace("通电话", "在微信上聊了几句")
    if not meta.get("has_offline_meet"):
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
    return re.sub(r"\s+", " ", value).strip()

async def _build_fact_pool_part( day, day_end, day_start, facts, place, plugin):
    try:
        health = plugin.state.health_info() or {}
    except Exception:
        health = {}
    if health.get("sick") or health.get("status") not in ("", "健康"):
        facts.append({"time": "", "type": "状态",
                      "detail": "身体：%s%s" % (health.get("status") or "不舒服",
                                               ("（" + str(health.get("note"))) + "）"
                                               if health.get("note") else "")})
    try:
        rows = await plugin._data_call(
            "messages_between", plugin._data_bot_id(), day_start, day_end, "", 500) or []
    except Exception:
        rows = []
    chats = chat_facts(rows)
    facts.extend(chats)
    outfit = ""
    try:
        worn = plugin.wardrobe.current(day.isoformat()) or {}
        outfit = str(worn.get("text") or "").strip()
    except Exception:
        outfit = ""
    bought = [f for f in facts if f["type"] == "消费" and any(
        w in f["detail"] for w in ("衣", "裙", "鞋", "裤", "外套", "服"))]
    yesterday_low = False
    try:
        yscore = plugin.db.kv_get("diary_mood:" + (day - _dt.timedelta(days=1)).isoformat(), None)
        yesterday_low = yscore is not None and float(yscore) < 40
    except Exception:
        yesterday_low = False
    if yesterday_low:
        facts.append({"time": "", "type": "情绪", "detail": "昨天心情很低落，今天可能还有点疲惫的余波"})
    meta = {"outfit": outfit, "bought_clothes": bought, "has_chat": bool(chats),
            "location": place, "yesterday_low": yesterday_low,
            "wake_late": any(f["type"] == "作息" for f in facts),
            "has_phone": any("电话" in f["detail"] for f in facts),
            "has_offline_meet": any(
                ("约会" in f["detail"] or "和老公" in f["detail"]) and f["type"] in ("日程", "线下约会")
                for f in facts)}
    try:
        for key in ("date_fact", "last_date_event"):
            data = plugin.db.kv_get(key, {}) or {}
            if isinstance(data, dict) and str(data.get("day") or "") == day.isoformat():
                facts.append({"time": "", "type": "线下约会",
                              "detail": str(data.get("line") or data.get("activity") or "和老公约会")})
                meta["has_offline_meet"] = True
    except Exception as exc:
        logger.debug("[amo] optional step failed: %r", exc)
    return meta

