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
from typing import Any, TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import logging
logger = logging.getLogger("amo")

import datetime as _dt
import hashlib
import re

#: 外卖/做饭词表、餐别窗口、账本退回判断都在唯一时间线里（Phase 3），这里只转出；
#: micro_events 仍然从 amo_fact_binder 导入 COOK_WORDS，调用方不变。
try:
    from . import amo_fact_timeline as fact_timeline
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_fact_timeline as fact_timeline

#: 说了这些就等于"点了外卖"（词表唯一源：amo_fact_timeline）
TAKEOUT_WORDS = fact_timeline.TAKEOUT_WORDS
#: 日程里出现这些词 = 她自己在家做了饭
COOK_WORDS = fact_timeline.COOK_WORDS
#: 餐别窗口 / 账单退回判断（唯一源：amo_fact_timeline）
meal_band = fact_timeline.meal_band
reversed_idems = fact_timeline.reversed_idems
spent_rows = fact_timeline.spent_rows
#: 表示"花钱买/点"的动作
CLAIM_VERBS = ("点了", "点了个", "点了份", "点了一份", "叫了", "叫了个", "叫了份",
               "下单", "下了单", "买了", "买了个", "买了一份")
#: 可以拿来判断"她说的是哪种消费"的实物词
FOOD_OBJECTS = ("奶茶", "咖啡", "烧烤", "火锅", "炸鸡", "汉堡", "披萨", "寿司", "麻辣烫",
                "米线", "饺子", "包子", "面包", "蛋糕", "零食", "水果", "泡面", "夜宵",
                "宵夜", "汤面", "牛肉面", "炒粉", "粥", "便当", "饭团", "酸奶")
SHOP_OBJECTS = ("衣服", "裙子", "裤子", "鞋", "口红", "化妆品", "面膜", "快递", "包",
                "毛衣", "外套", "睡衣", "开衫", "针织衫", "卫衣", "衬衫", "T恤", "短袖",
                "大衣", "风衣", "夹克", "羽绒服", "短裤", "长裤", "牛仔裤", "打底裤",
                "袜子", "丝袜", "内衣", "内裤", "帽子", "围巾", "手套", "首饰", "项链",
                "耳环", "手链", "戒指", "香水", "包包", "靴子")

#: 否定说法（"我不爱吃外卖"）不是在声称消费，别误伤
NEGATION_WORDS = ("不爱", "不太爱", "不喜欢", "不太喜欢", "不想", "不太想",
                  "不吃", "不太吃", "不点", "没点", "没吃", "没有点", "不会点", "别点", "戒了")

#: 一次"编出来的外卖"按这个金额补扣（charge 模式）
TAKEOUT_CENTS = 2500
FOOD_CENTS = 1500


def _now():
    return _amo_clock.now()


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
    out: list[dict[str, Any]] = []
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


def meal_facts(items, ledger, now=None, plugin=None):
    """这一餐的事实：是不是自己做的、账上有没有外卖（都只看当前这一顿）。

    唯一实现在 amo_fact_timeline.meal_view：做饭/外卖先落成时间线条目，这里只做投射，
    不再自己扫一遍日程和账本（否则 binder 和时间线又是两套口径）。
    """
    return fact_timeline.meal_view(items, ledger, now, plugin=plugin)


#: 在家的这一小时不许出现的"出门"类词
HOME_BAN = ("出门", "在公司", "在上班", "地铁", "商场", "在路上")
#: 不在家时不许出现的"居家"类词
AWAY_BAN = ("在家", "在被窝", "穿着睡衣", "在床上")
#: 自己做了饭、账上没外卖时，不许出现的"点外卖"类词
COOK_BAN = ("外卖", "骑手", "快递小哥", "送餐", "点单")


#: 道具/环境词 grounding（2026-11-23 23:00「盒子/在车上」）：没有事实支撑就删掉分句。
_PROP_BOX_WORDS = ("饭盒", "外卖盒", "餐盒", "盒子", "推远")
_PROP_ROAD_WORDS = ("在车上", "车上", "车厢", "地铁上", "公交上")
_PROP_CLAUSE = re.compile(r"[^。！？!?；;，,\n]+[。！？!?；;，,\n]?")


def _prop_support(plugin, now=None):
    """当前回复能不能说盒子/在车上：只看 current_action + 今天 meal_facts。"""
    now = now or _now()
    action = place = zone = ""
    try:
        try:
            from .amo_current_action import get_current_action
        except ImportError:
            from amo_current_action import get_current_action
        data = get_current_action(plugin, now) or {}
        action = str(data.get("action") or "")
        place = str(data.get("place") or "")
        zone = str(data.get("zone") or "")
    except Exception as exc:  # cleanup: 取不到当前动作就按「无支撑」处理
        logger.debug("[amo_fact_binder._prop_support] optional step failed: %s", exc)
    box_ok = False
    try:
        items = schedule_items(plugin, now.strftime("%Y-%m-%d"))
        facts = meal_facts(items, ledger_today(plugin, now), now, plugin=plugin)
        box_ok = bool(facts.get("takeout") or facts.get("takeout_rows"))
    except Exception:  # cleanup: 事实层不可用就不放开道具
        box_ok = False
    context = action + place
    if any(word in context for word in ("外卖", "盒子", "饭盒", "餐盒")):
        box_ok = True
    road_ok = (zone == "road"
               or any(word in context for word in ("地铁", "公交", "车上", "车厢", "路上")))
    return box_ok, road_ok


def ground_props(plugin, text, now=None):
    """没有 meal_facts/current_action 支撑时，删掉「盒子/饭盒/推远/在车上」分句。"""
    body = str(text or "")
    if not body.strip():
        return body
    # 绝大多数回复不带道具词：先做纯文本短路，避免每条都读 current_action/meal_facts。
    if not any(word in body for word in _PROP_BOX_WORDS + _PROP_ROAD_WORDS):
        return body
    box_ok, road_ok = _prop_support(plugin, now)
    if box_ok and road_ok:
        return body
    kept = []
    dropped = []
    for clause in _PROP_CLAUSE.findall(body):
        if not box_ok and any(word in clause for word in _PROP_BOX_WORDS):
            dropped.append(clause)
            continue
        if not road_ok and any(word in clause for word in _PROP_ROAD_WORDS):
            dropped.append(clause)
            continue
        kept.append(clause)
    if not dropped:
        return body
    out = "".join(kept).strip()
    out = re.sub(r"^[\s，。、；：,.;:!?！？~～]+", "", out)
    out = re.sub(r"[\s，、；：,;:]+$", "", out)
    logger.info("[amo] 道具 grounding 清理 reason=unsupported_prop original=%s cleaned=%s fallback=%s",
                body[:80], out[:80], "drop_clause")
    return out


def ban_words(plugin, now=None, facts=None):
    """当前这一小时绝对不能出现的矛盾词（系统警告与重试提示共用）。"""
    now = now or _now()
    if facts is None:
        items = schedule_items(plugin, now.strftime("%Y-%m-%d"))
        facts = meal_facts(items, ledger_today(plugin, now), now, plugin=plugin)
    zone = "home"
    try:
        try:
            from .amo_current_action import get_current_action as _get_action
        except ImportError:
            from amo_current_action import get_current_action as _get_action
        zone = str((_get_action(plugin, now) or {}).get("zone") or "home")
    except Exception:
        zone = "home"
    words: list[Any] = []
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
        facts = meal_facts(items, ledger_today(plugin, now), now, plugin=plugin)
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
    except Exception as exc:
        logger.debug("[amo] optional step failed: %r", exc)
    words = ban_words(plugin, now, facts)
    return ("【系统警告】你这一小时在【%s】，已经完成了【%s】。"
            "严禁在回复中说出“%s”等矛盾词汇。" % (place_word, done, "、".join(words)))


def facts_lines(plugin, now=None):
    """聊天前注入的最高优先级事实块（清单条数有上限，别把上下文撑爆）。

    「已经发生」「这一餐」「今天的账单」全部来自 amo_fact_timeline 的条目，这里只负责
    排版；事实口径只有一份，binder 不再自己从 schedule + ledger 推第二遍。
    """
    now = now or _now()
    day = now.strftime("%Y-%m-%d")
    items = schedule_items(plugin, day)
    minutes = now.hour * 60 + now.minute
    ledger = ledger_today(plugin, now)
    facts = meal_facts(items, ledger, now, plugin=plugin)
    # 唯一时间线：已经发生的块 + 账单都是时间线条目（不是这里二次推的）
    entries = fact_timeline.collect(day, now, items, ledger, plugin=plugin)
    done = [entry for entry in fact_timeline.sort_stable(entries)
            if entry.get("type") == "日程" and entry.get("start") is not None
            and entry["start"] <= minutes]
    if not done and not ledger:
        # 今天既没有日程也没有账单：没有事实可钉，就别往上下文里塞空规则
        return []
    lines = []

    # 0) 系统警告（硬编码，排在最前面，优先级最高）
    lines.append(warning_line(plugin, now, facts))

    # 1) 绝对事实：已经发生过的日程 + 这一小时在干嘛（条目来自时间线）
    chunks = []
    day_label = "[今日] "
    for entry in done[-3:]:
        chunks.append(day_label + "%s-%s %s（%s）" % (
            fact_timeline.clock_of(entry["start"]),
            fact_timeline.clock_of(entry.get("end")),
            str(entry.get("activity") or "").strip() or "在忙",
            str(entry.get("place") or "家").strip() or "家"))
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
        food_rows = [str((row or {}).get("food") or "").strip()
                     for row in (facts.get("takeout_rows") or ())
                     if isinstance(row, dict)]
        food_rows = [food for food in food_rows if food]
        if food_rows:
            lines.append("【绝对事实·吃】今天他给你点了%s，已经送到/吃了；"
                         "不要再说还没吃、在等，也不要再问什么时候到。"
                         % "、".join(food_rows))
        else:
            lines.append("【绝对事实·吃】今天你确实点过一次外卖（账单里有这笔），可以顺着说，"
                         "金额和次数都以账单为准。")

    # 3) 今天的账单流水（最近 5 笔，条目来自时间线：方向词/已退回标记都已算好）
    rows = [entry for entry in entries if entry.get("source") in ("ledger", "date_ledger")]
    if rows:
        pieces = []
        for entry in rows[-5:]:
            pieces.append(day_label + "%s %s %.2f 元 %s%s" % (
                entry.get("time"), entry.get("sign"),
                abs(int(entry.get("delta") or 0)) / 100.0,
                entry.get("reason") or "", entry.get("mark") or ""))
        lines.append("【今天的账单】" + "；".join(pieces) + "。")

    # 4) 红线：只在真有账单时注入（2026-11-24；钱包已退役，账本恒空，
    #    再写「只按【今天的账单】说」等于禁止她提任何花钱的事，还指向一个不存在的块）
    if rows:
        lines.append(
            "【消费红线】花钱、外卖、点单、买东西，只按【今天的账单】说；"
            "账单里没有的，就说还没买/还没点。想吃什么可以直接说想吃。")
    if facts["cooked"]:
        lines.append(
            "【顺着事实说】日程写的是在家做饭，你就说自己做的"
            "（比如“（摸了摸肚子）刚刚自己煮了碗面，吃得好撑呀~”），不许改口成点外卖。")
    return lines


def view_lines(plugin, now=None):
    """回复上下文里的事实视图行：块日记来自 build_day_view（唯一真源）。

    facts_lines 管「这一小时在干嘛 + 今天的账单」；这一层补上事实池没有的
    块日记（当天每一块真实写了什么），没有小日记就返回空列表。
    """
    explicit_now = now
    now = now or _now()
    try:
        # N1：真实时钟路径与回复事实门共用同一份 build_day_view（按请求缓存）。
        try:
            from .amo_diary_guard import narrative_view as _narrative_view
        except ImportError:
            from amo_diary_guard import narrative_view as _narrative_view
        view = _narrative_view(plugin, explicit_now, cache=explicit_now is None)
        rows = fact_timeline.render_lines(fact_timeline.view_facts(view), "[今日] ")
        # 2026-11-24：日记行原来直接跟在【消费红线】后面，块解析会把 800 多字日记
        # 算成红线的一部分（她最后读到的是一屏日记+一句规则）。这里给它自己的块头。
        return (["【今天的小日记】"] + rows) if rows else []
    except Exception as exc:
        logger.debug("[amo] 事实视图读取失败: %r", exc)
        return []


def view_detail(plugin, now=None):
    """回复/日记消费方可读的「今天的人/东西/场合」：只读唯一事实视图。"""
    explicit_now = now
    try:
        try:
            from .amo_diary_guard import narrative_view as _narrative_view
        except ImportError:
            from amo_diary_guard import narrative_view as _narrative_view
        view = _narrative_view(plugin, explicit_now, cache=explicit_now is None)
    except Exception as exc:  # cleanup: 视图读不到就给空细节
        logger.debug("[amo] 事实视图细节读取失败: %r", exc)
        return {"day": "", "people": [], "companions": [], "items": [],
                "occasions": []}
    return {
        "day": str((view or {}).get("day") or ""),
        "people": [str(item) for item in (view or {}).get("people") or ()],
        "companions": [str(item) for item in (view or {}).get("companions") or ()],
        "items": [str(item) for item in (view or {}).get("items") or ()],
        "occasions": [str(item) for item in (view or {}).get("occasions") or ()],
    }


def view_detail_lines(plugin, now=None):
    """把 view_detail 排成可注入的两三行；没有事实就空列表（不占上下文）。"""
    detail = view_detail(plugin, now)
    lines = []
    if detail["people"]:
        lines.append("【今天的人】" + "、".join(detail["people"]) + "。")
    if detail["items"]:
        lines.append("【今天的东西】" + "、".join(detail["items"]) + "。")
    if detail["occasions"]:
        lines.append("【今天的场合】" + "、".join(detail["occasions"]) + "。")
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
    words: list[Any] = []
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
        # 2026-11-24：钱包已退役（_RetiredWallet：账本恒空、debit 恒 True）。
        # 再跑消费守卫只剩副作用——她每说一句「我吃了/我买了」都被删掉换成固定句。
        # 不涉及钱就整条关闭，配置项 chat_claim_guard=False 的语义不变（手工关也能关）。
        try:
            from .amo_init import _RetiredWallet
        except ImportError:
            from amo_init import _RetiredWallet
        if isinstance(getattr(plugin, "wallet", None), _RetiredWallet):
            return "off"
        if not plugin.cfg_bool("chat_claim_guard", True):
            return "off"
        if plugin.cfg_bool("chat_claim_charge", False):
            return "charge"
    except Exception as exc:
        logger.debug("[amo] optional step failed: %r", exc)
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
    facts = meal_facts(items, ledger, now, plugin=plugin)
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
