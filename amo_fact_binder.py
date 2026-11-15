# -*- coding: utf-8 -*-
"""事实强绑定（2026-11-15 v1）：聊天前把「这一小时在干嘛 + 今天花了什么钱」钉进去。

事故复盘（2026-11-15 13:32）：
- 日程：12:00-13:00「在家做饭」，13:00-14:00「吃完饭后趴着歇一会儿」；
- 微型事件 13:32:49 抽到 takeout（25 元）→ 真扣了钱，写进 micro_event:last；
- 主动分享只拿到 snapshot（既没有账单、也没有日程明细），于是说出
  「我今天懒得做饭，点了个外卖 / 刚吃完，趴着歇一会儿，撑得动不了」。
账上确实扣了 25 元，但它和「在家做饭」的日程是同一天的两套叙事。

三条规则（本模块负责 1、2）：
1. 【绝对事实】当前小时的事件 + 今天的账单，以最高优先级注入聊天上下文；
2. 【消费红线】聊天里说的每一笔消费都必须能在账本里查到，查不到就是没发生；
3. 发送前校验：编出来的消费直接删掉/改写成事实，或者（charge 模式）先真扣钱再说。
"""
from __future__ import annotations

import datetime as _dt
import hashlib

#: 说了这些就等于"点了外卖"，必须有账单兜底
TAKEOUT_WORDS = ("外卖", "美团", "饿了么", "骑手", "送餐", "配送")
#: 日程里出现这些词 = 她自己在家做了饭
COOK_WORDS = ("做饭", "煮", "下厨", "炒", "炖", "蒸", "煎", "做顿", "做了顿", "包饺子", "煲汤")
#: 表示"花钱买/点"的动作
CLAIM_VERBS = ("点了", "点了个", "点了份", "点了一份", "叫了", "叫了个", "叫了份",
               "下单", "下了单", "买了", "买了个", "买了一份")
#: 可以拿来判断"她说的是哪种消费"的实物词
FOOD_OBJECTS = ("奶茶", "咖啡", "烧烤", "火锅", "炸鸡", "汉堡", "披萨", "寿司", "麻辣烫",
                "米线", "饺子", "包子", "面包", "蛋糕", "零食", "水果", "泡面", "夜宵",
                "宵夜", "汤面", "牛肉面", "炒粉", "粥", "便当", "饭团", "酸奶")
SHOP_OBJECTS = ("衣服", "裙子", "裤子", "鞋", "口红", "化妆品", "面膜", "快递", "包",
                "毛衣", "外套", "睡衣")

#: 否定说法（"我不爱吃外卖"）不是在声称消费，别误伤
NEGATION_WORDS = ("不爱", "不太爱", "不喜欢", "不太喜欢", "不想", "不太想",
                  "不吃", "不太吃", "不点", "没点", "没吃", "没有点", "不会点", "别点", "戒了")

#: 一次"编出来的外卖"按这个金额补扣（charge 模式）
TAKEOUT_CENTS = 2500
FOOD_CENTS = 1500


def _now():
    return _dt.datetime.now()


def _minutes(text):
    """'12:30' -> 750；解析不了返回 -1。"""
    try:
        hour, minute = [int(x) for x in str(text or "").split(":")[:2]]
        return hour * 60 + minute
    except Exception:
        return -1


def _kv(plugin, key, default=None):
    try:
        return plugin.db.kv_get(key, default)
    except Exception:
        return default


def schedule_items(plugin, day):
    """当天日程（优先内存态，回落到库）。"""
    items = []
    try:
        items = list(plugin.state.schedule.get(day) or [])
    except Exception:
        items = []
    if not items:
        try:
            items = list(plugin.db.get_schedule(day) or [])
        except Exception:
            items = []
    return [item for item in items or [] if isinstance(item, dict)]


def ledger_today(plugin, now=None):
    """今天"到现在为止"的账本流水（正=收入，负=支出），按时间升序。

    2026-11-15：必须截到 now——12:30 那会儿不能把 13:32 未来才会发生的扣款
    当成"已完成事实"，否则禁止词会算反。
    """
    now = now or _now()
    start = int(_dt.datetime.combine(now.date(), _dt.time.min).timestamp())
    until = now.timestamp() + 1.0
    try:
        rows = list(plugin.wallet.ledger(200) or [])
    except Exception:
        rows = []
    out = []
    for row in rows:
        try:
            ts = float(row.get("ts") or 0)
        except Exception:
            continue
        if ts < start or ts > until:
            continue
        try:
            delta = int(row.get("delta_cents") or 0)
        except Exception:
            delta = 0
        out.append({"ts": ts, "delta": delta,
                    "reason": str(row.get("reason") or ""),
                    "category": str(row.get("category") or ""),
                    "idem": str(row.get("idem") or ""),
                    "ref": str(row.get("ref") or "")})
    out.sort(key=lambda item: item["ts"])
    return out


def reversed_idems(ledger):
    """已经被退回的那几笔（退款流水的 ref 指向原扣款的 idem）。"""
    out = set()
    for row in ledger or []:
        ref = str(row.get("ref") or "")
        if ref and row.get("delta", 0) > 0:
            out.add(ref)
    return out


def spent_rows(ledger):
    """真正的支出：已退回的那笔不算（2026-11-15 退 25 元外卖后修正）。"""
    bounced = reversed_idems(ledger)
    return [row for row in ledger
            if row["delta"] < 0 and str(row.get("idem") or "") not in bounced]


def meal_band(now=None):
    """当前这顿饭的窗口：lunch 10-15 / dinner 16-22，其余时间不判"吃了什么"。"""
    now = now or _now()
    hour = now.hour
    if 10 <= hour < 15:
        return "lunch"
    if 16 <= hour < 22:
        return "dinner"
    return ""


def meal_facts(items, ledger, now=None):
    """这一餐的事实：是不是自己做的、账上有没有外卖（都只看当前这一顿）。"""
    now = now or _now()
    band = meal_band(now)
    minutes = now.hour * 60 + now.minute
    cooked = []
    if band:
        low = 10 * 60 if band == "lunch" else 16 * 60
        for item in items or []:
            start = _minutes(item.get("start"))
            if start < 0 or start > minutes or start < low:
                continue
            activity = str(item.get("activity") or "")
            if any(word in activity for word in COOK_WORDS):
                cooked.append(item)
    takeout_rows, cook_rows = [], []
    band_start = None
    if band:
        band_start = int(_dt.datetime.combine(
            now.date(), _dt.time(10 if band == "lunch" else 16, 0)).timestamp())
        for row in spent_rows(ledger):
            if band_start is not None and row["ts"] < band_start:
                continue
            reason = row["reason"]
            if any(word in reason for word in TAKEOUT_WORDS):
                takeout_rows.append(row)
            elif any(word in reason for word in COOK_WORDS):
                # "自己做饭 × 1" 这种扣款：她自己下厨的铁证
                cook_rows.append(row)
    return {"band": band, "cooked": bool(cooked or cook_rows),
            "cooked_items": cooked, "cooked_rows": cook_rows,
            "takeout": bool(takeout_rows), "takeout_rows": takeout_rows}


def _clock(ts):
    try:
        return _dt.datetime.fromtimestamp(ts).strftime("%H:%M")
    except Exception:
        return ""


#: 在家的这一小时不许出现的"出门"类词
HOME_BAN = ("出门", "在公司", "在上班", "地铁", "商场", "在路上")
#: 不在家时不许出现的"居家"类词
AWAY_BAN = ("在家", "在被窝", "穿着睡衣", "在床上")
#: 自己做了饭、账上没外卖时，不许出现的"点外卖"类词
COOK_BAN = ("外卖", "骑手", "快递小哥", "送餐", "点单")


def ban_words(plugin, now=None, facts=None):
    """当前这一小时绝对不能出现的矛盾词（系统警告与重试提示共用）。"""
    now = now or _now()
    if facts is None:
        items = schedule_items(plugin, now.strftime("%Y-%m-%d"))
        facts = meal_facts(items, ledger_today(plugin, now), now)
    zone = "home"
    try:
        try:
            from .amo_current_action import get_current_action as _get_action
        except ImportError:
            from amo_current_action import get_current_action as _get_action
        zone = str((_get_action(plugin, now) or {}).get("zone") or "home")
    except Exception:
        zone = "home"
    words = []
    if zone in ("home", "hotel"):
        words.extend(HOME_BAN)
    else:
        words.extend(AWAY_BAN)
    if facts.get("cooked") and not facts.get("takeout"):
        words.extend(COOK_BAN)
    elif facts.get("takeout") and not facts.get("cooked"):
        words.extend(("自己做饭", "在家做饭", "我做的"))
    seen, out = set(), []
    for word in words:
        if word in seen:
            continue
        seen.add(word)
        out.append(word)
    return out[:8]


def warning_line(plugin, now=None, facts=None):
    """【系统警告】硬编码前置校验：这一小时在哪、做完了什么、不许说什么。"""
    now = now or _now()
    if facts is None:
        items = schedule_items(plugin, now.strftime("%Y-%m-%d"))
        facts = meal_facts(items, ledger_today(plugin, now), now)
    place_word, done = "家", "在忙"
    try:
        try:
            from .amo_current_action import get_current_action as _get_action
        except ImportError:
            from amo_current_action import get_current_action as _get_action
        data = _get_action(plugin, now) or {}
        zone = str(data.get("zone") or "home")
        place_word = {"home": "家", "office": "公司", "school": "学校", "mall": "商场",
                      "restaurant": "餐厅", "scenic": "景区", "hotel": "酒店",
                      "road": "路上", "hospital": "医院", "station": "车站"}.get(zone, "家")
        done = str(data.get("action") or "在忙")
    except Exception:
        pass
    words = ban_words(plugin, now, facts)
    return ("【系统警告】你这一小时在【%s】，已经完成了【%s】。"
            "严禁在回复中说出“%s”等矛盾词汇。" % (place_word, done, "、".join(words)))


def facts_lines(plugin, now=None):
    """聊天前注入的最高优先级事实块（清单条数有上限，别把上下文撑爆）。"""
    now = now or _now()
    day = now.strftime("%Y-%m-%d")
    items = schedule_items(plugin, day)
    minutes = now.hour * 60 + now.minute
    done = [item for item in items if 0 <= _minutes(item.get("start")) <= minutes]
    ledger = ledger_today(plugin, now)
    facts = meal_facts(items, ledger, now)
    if not done and not ledger:
        # 今天既没有日程也没有账单：没有事实可钉，就别往上下文里塞空规则
        return []
    lines = []

    # 0) 系统警告（硬编码，排在最前面，优先级最高）
    lines.append(warning_line(plugin, now, facts))

    # 1) 绝对事实：已经发生过的日程 + 这一小时在干嘛
    chunks = []
    for item in done[-3:]:
        chunks.append("%s-%s %s（%s）" % (
            item.get("start", ""), item.get("end", ""),
            str(item.get("activity") or "").strip() or "在忙",
            str(item.get("place") or "家").strip() or "家"))
    if chunks:
        lines.append("【绝对事实】你今天真实发生的事，聊天只能顺着这些说：" + "；".join(chunks) + "。")

    # 2) 吃这件事的结论：做饭 or 外卖
    dishes = [str(item.get("activity") or "") for item in facts["cooked_items"][-2:]]
    dishes += [str(row["reason"]) for row in facts["cooked_rows"][-2:]]
    if facts["cooked"]:
        evidence = "；".join([dish for dish in dishes if dish])
        if facts["takeout"]:
            # 同一天既有自己做饭、又有外卖扣款：以做饭为准，外卖那笔是系统误记
            lines.append("【绝对事实·吃】今天这一餐是你在家自己做饭吃的（" + (evidence or "日程里写着")
                         + "）；账单里那笔外卖是系统误记的，聊天里不许提，也不许说点了/在等外卖。")
        else:
            lines.append("【绝对事实·吃】今天这一餐是你在家自己做饭吃的（" + (evidence or "日程里写着")
                         + "），你绝对没有点外卖，也不许说自己点了/快到了。")
    elif facts["takeout"]:
        lines.append("【绝对事实·吃】今天你确实点过一次外卖（账单里有这笔），可以顺着说，"
                     "金额和次数都以账单为准。")

    # 3) 今天的账单流水（最近 5 笔）
    rows = ledger[-5:]
    if rows:
        pieces = []
        for row in rows:
            bounced = reversed_idems(ledger)
            if row["delta"] < 0:
                sign = "花"
                mark = "（已退回）" if str(row.get("idem") or "") in bounced else ""
            else:
                sign = "退回" if str(row.get("ref") or "") else "进"
                mark = ""
            pieces.append("%s %s %.2f 元 %s%s" % (
                _clock(row["ts"]), sign, abs(row["delta"]) / 100.0, row["reason"], mark))
        lines.append("【今天的账单】" + "；".join(pieces) + "。")

    # 4) 红线
    lines.append(
        "【消费红线】聊天里说的每一笔花钱、外卖、点单、买东西，都必须能在【今天的账单】里找到；"
        "找不到就是没发生，绝对不许说。没发生的“刚吃完/点好了/在等外卖”一样不许编。"
        "想吃什么就说想吃，不要说自己已经点了或者已经吃了。")
    if facts["cooked"]:
        lines.append(
            "【顺着事实说】日程写的是在家做饭，你就说自己做的"
            "（比如“（摸了摸肚子）刚刚自己煮了碗面，吃得好撑呀~”），不许改口成点外卖。")
    return lines


def _split_sentences(text):
    """按中英文句末符号切句，保留标点。"""
    out = []
    buf = ""
    for ch in str(text or ""):
        buf += ch
        if ch in "。！？!?~\n；;":
            out.append(buf)
            buf = ""
    if buf.strip():
        out.append(buf)
    return [s for s in out if s.strip()]


def _negated(text, word):
    """这个词附近有没有否定词（"我不太爱吃外卖"不算声称）。"""
    idx = str(text).find(word)
    if idx < 0:
        return False
    window = str(text)[max(0, idx - 8): idx + len(word) + 3]
    return any(neg in window for neg in NEGATION_WORDS)


def claim_kinds(sentence):
    """这一句在声称哪种消费；没声称返回空列表。"""
    text = str(sentence or "")
    kinds = []
    if any(word in text for word in TAKEOUT_WORDS):
        # 只有"真的在说点了/吃了/在等外卖"才算声称，单纯提到"外卖"两个字不算
        if (any(verb in text for verb in CLAIM_VERBS)
                or "点外卖" in text or "叫外卖" in text
                or "外卖到" in text or "等外卖" in text
                or "吃外卖" in text or "外卖还没" in text):
            if not _negated(text, "外卖"):
                kinds.append("takeout")
    if not kinds and any(verb in text for verb in CLAIM_VERBS):
        if any(word in text for word in FOOD_OBJECTS):
            kinds.append("food")
        elif any(word in text for word in SHOP_OBJECTS):
            kinds.append("shop")
    return kinds


def _keywords(sentence, kind):
    text = str(sentence or "")
    words = []
    if kind == "takeout":
        words.extend(TAKEOUT_WORDS)
    elif kind == "food":
        words.extend([word for word in FOOD_OBJECTS if word in text])
    elif kind == "shop":
        words.extend([word for word in SHOP_OBJECTS if word in text])
    return [word for word in words if word]


def supported(sentence, kind, ledger, facts=None):
    """账本里找得到这笔钱吗？"""
    keywords = _keywords(sentence, kind)
    if kind == "takeout":
        if facts and facts.get("cooked"):
            # 这一餐自己做了饭：就算账上有外卖扣款（微型事件误记），也不许说点外卖
            return False
        # 外卖独占一条证据线：今天的支出里必须真有外卖/配送
        return bool([row for row in spent_rows(ledger)
                     if any(word in row["reason"] for word in TAKEOUT_WORDS)])
    if not keywords:
        return True
    for row in spent_rows(ledger):
        if any(word in row["reason"] for word in keywords):
            return True
    return False


def _fallback_line(plugin, facts, now=None):
    """被改掉之后补一句符合事实的话。"""
    now = now or _now()
    if facts.get("cooked"):
        dishes = [str(item.get("activity") or "") for item in facts.get("cooked_items") or []]
        dish = dishes[-1] if dishes else "饭"
        if "面" in dish:
            return "（摸了摸肚子）刚刚自己煮了碗面，吃得好撑呀~"
        return "（摸了摸肚子）刚刚自己在家做的饭，吃得好撑呀~"
    try:
        try:
            from .amo_current_action import get_current_action
        except ImportError:
            from amo_current_action import get_current_action
        action = str(get_current_action(plugin, now).get("action") or "").strip()
    except Exception:
        action = ""
    if action:
        return "（%s）" % action
    return "唔…我先不说了，等我缓缓。"


def _mode(plugin):
    try:
        if not plugin.cfg_bool("chat_claim_guard", True):
            return "off"
        if plugin.cfg_bool("chat_claim_charge", False):
            return "charge"
    except Exception:
        pass
    return "rewrite"


def _charge(plugin, sentence, kind, now=None):
    """charge 模式：先真扣钱，再让她说这句话。"""
    now = now or _now()
    amount = TAKEOUT_CENTS if kind == "takeout" else FOOD_CENTS
    reason = str(sentence or "").strip()[:60] or "点了点吃的"
    idem = "chatclaim:%s:%s" % (
        now.strftime("%Y-%m-%d"),
        hashlib.sha1(reason.encode("utf-8")).hexdigest()[:12])
    try:
        return bool(plugin.wallet.debit(amount, str(reason or "花销"), idem))
    except Exception:
        return False


def guard(reply, plugin, now=None, mode=""):
    """发送前校验：编出来的消费直接改写。

    返回 (新文本, 是否改动, 原因)。
    """
    text = str(reply or "")
    if not text:
        return text, False, ""
    mode = mode or _mode(plugin)
    if mode == "off":
        return text, False, ""
    now = now or _now()
    day = now.strftime("%Y-%m-%d")
    items = schedule_items(plugin, day)
    ledger = ledger_today(plugin, now)
    facts = meal_facts(items, ledger, now)
    sentences = _split_sentences(text)
    kept, dropped = [], []
    for sentence in sentences:
        kinds = claim_kinds(sentence)
        bad = [kind for kind in kinds if not supported(sentence, kind, ledger, facts)]
        if not bad:
            kept.append(sentence)
            continue
        if mode == "charge" and not facts.get("cooked") and _charge(plugin, sentence, bad[0], now):
            kept.append(sentence)
            continue
        dropped.append(sentence.strip())
    if not dropped:
        return text, False, ""
    if not kept:
        kept = [_fallback_line(plugin, facts, now)]
    new_text = "".join(kept).strip()
    return new_text, True, "；".join(dropped)[:120]
