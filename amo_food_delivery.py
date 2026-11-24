# -*- coding: utf-8 -*-
"""食物送达 -> 吃饭事实（2026-11-23）。

只写事实层，不写日程块：
- 下单只记 pending_food（2 小时 TTL，一个会话一单）；
- 他说“到了/吃吧/吃完了”才消费，调 mark_meal 并写一条 meal_facts；
- meal_facts 按 day+slot 去重，只留最近 N 条；
- 绝不往时间轴 append meal 块，避免日程爆炸。
"""
from __future__ import annotations

try:  # 吞异常留痕（2026-11-24）
    from .amo_observability import note as _note_swallowed
except ImportError:
    from amo_observability import note as _note_swallowed

import re
import time

try:
    from .amo_shared import now_ts
except ImportError:
    from amo_shared import now_ts

FOOD_WORDS = (
    "炒米粉", "米粉", "米线", "炒面", "面条", "面", "粥", "饭", "炒饭", "盖饭",
    "外卖", "奶茶", "咖啡", "烧烤", "火锅", "炸鸡", "汉堡", "披萨", "寿司",
    "麻辣烫", "小吃", "甜品", "蛋糕", "零食", "饺子", "包子", "汤",
)
_ORDER_RE = re.compile(
    r"(?:请(?:你)?|送(?:你)?|点(?:了|个|一份|份)?|买(?:了|个)?|来(?:一份|个|杯|碗)?|要(?:一份|个|杯|碗)?)"
    r"(?:吃|喝)?(?:个|份|杯|碗|点|一些)?"
    r"(?P<food>[^，。！？,.!?；;：:\s]{1,18})"
)
_NEGATION = re.compile(r"(?:不|别|没|不要|不用)(?:想|要|用)?$")
PENDING_KEY = "pending_food"
FACTS_KEY = "meal_facts"
PENDING_TTL = 2 * 3600
MAX_FACTS = 60


def _now(now=None):
    return float(now if now is not None else now_ts())


def detect_food_order(text):
    """从“请你个炒米粉/送炒米粉/点外卖”里取出食物名；不是下单就返回空串。"""
    raw = str(text or "").strip()
    if not raw or is_arrival(raw):
        return ""
    match = _ORDER_RE.search(raw)
    if not match:
        return ""
    if _NEGATION.search(raw[:match.start()] + raw[match.start():match.start() + 6]):
        return ""
    food = str(match.group("food") or "").strip(" 的了吧呀啊哦，。！？")
    if not food:
        return ""
    if not any(word in food for word in FOOD_WORDS) and "外卖" not in raw:
        return ""
    # “送到家/送给谁”不是食物
    if food in ("你", "我", "他", "她", "家", "公司", "过来", "过来吧"):
        return ""
    return food[:20]


def is_arrival(text):
    raw = str(text or "").strip("，。！？,.!? \t\r\n")
    if not raw:
        return False
    # “外卖到了/送到门口/饭到了”这类强信号
    if any(word in raw for word in ("送到了", "送来了", "拿上来了", "到门口", "到楼下",
                                    "外卖到", "饭到", "餐到", "吃的到")):
        return True
    # “吃完了/吃吧/我吃了”这类直接吃饭动作
    if any(word in raw for word in ("吃吧", "开吃", "可以吃了", "我吃了", "吃完了",
                                    "吃过了", "吃上了")):
        return True
    # 单说“到了”才算； “我到了公司/我到家了”不算外卖到了
    return raw in ("到了", "送达", "送来了")


def meal_slot(now=None):
    hour = time.localtime(_now(now)).tm_hour
    if 11 <= hour < 15:
        return "lunch"
    if 17 <= hour < 22:
        return "dinner"
    return "snack"


def _pending(plugin):
    try:
        data = plugin.db.kv_get(PENDING_KEY, {})
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def place_order(plugin, umo, food, raw="", now=None):
    now = _now(now)
    existing = _pending(plugin)
    same = (existing and str(existing.get("umo") or "") == str(umo or "")
            and now - float(existing.get("ts") or 0) <= PENDING_TTL)
    foods = list(existing.get("foods") or [existing.get("food")]) if same else []
    foods = [str(item) for item in foods if str(item or "").strip()]
    food = str(food or "").strip()
    if food and food not in foods:
        foods.append(food)
    data = {
        "umo": str(umo or ""),
        "food": "、".join(foods),
        "foods": foods,
        "raw": str(raw or "")[:120],
        "ts": now,
    }
    try:
        plugin.db.kv_set(PENDING_KEY, data)
        setattr(plugin, "_meal_missing_cache", None)
    except Exception:
        return {}
    return data


def pending_order(plugin, now=None):
    data = _pending(plugin)
    if not data:
        return {}
    try:
        if _now(now) - float(data.get("ts") or 0) > PENDING_TTL:
            return {}
    except (TypeError, ValueError):
        return {}
    return data


def _facts(plugin):
    try:
        data = plugin.db.kv_get(FACTS_KEY, [])
    except Exception:
        return []
    return list(data) if isinstance(data, list) else []


def today_facts(plugin, day):
    day = str(day or "")
    return [dict(item) for item in _facts(plugin)
            if isinstance(item, dict) and str(item.get("day") or "") == day]


def _save_fact(plugin, fact):
    rows = [dict(item) for item in _facts(plugin) if isinstance(item, dict)]
    incoming = list(fact.get("foods") or [fact.get("food")])
    incoming = [str(item) for item in incoming if str(item or "").strip()]
    for item in rows:
        if (str(item.get("day") or "") != fact["day"]
                or str(item.get("slot") or "") != fact["slot"]):
            continue
        foods = list(item.get("foods") or [item.get("food")])
        foods = [str(food) for food in foods if str(food or "").strip()]
        for food in incoming:
            if food not in foods:
                foods.append(food)
        item.update(fact)
        item["foods"] = foods
        item["food"] = "、".join(foods)
        item["ts"] = max(float(item.get("ts") or 0), float(fact.get("ts") or 0))
        try:
            plugin.db.kv_set(FACTS_KEY, rows[-MAX_FACTS:])
        except Exception:
            return {}
        return item
    fact = dict(fact)
    fact["foods"] = incoming
    fact["food"] = "、".join(incoming)
    rows.append(fact)
    rows = rows[-MAX_FACTS:]
    try:
        plugin.db.kv_set(FACTS_KEY, rows)
    except Exception:
        return {}
    return fact


def consume_arrival(plugin, umo, text, now=None):
    """他说到了/吃吧：消费 pending，写 meal_fact；没有 pending 返回空。"""
    if not is_arrival(text):
        return {}
    data = pending_order(plugin, now=now)
    if not data:
        return {}
    if str(data.get("umo") or "") and str(data.get("umo") or "") != str(umo or ""):
        return {}
    try:
        plugin.db.kv_set(PENDING_KEY, {})
        setattr(plugin, "_meal_missing_cache", None)
    except Exception as exc:
        _note_swallowed("amo_food_delivery.consume_arrival", exc)
    foods = [str(item) for item in (data.get("foods") or [data.get("food")])
             if str(item or "").strip()]
    food = "、".join(foods)
    now = _now(now)
    fact = {
        "day": time.strftime("%Y-%m-%d", time.localtime(now)),
        "slot": meal_slot(now),
        "food": food,
        "foods": foods,
        "ts": now,
        "source": "gift",
    }
    saved = _save_fact(plugin, fact)
    if saved:
        fact = saved
    try:
        mark = getattr(plugin, "mark_meal", None)
        if callable(mark):
            mark(food)
    except Exception as exc:
        _note_swallowed("amo_food_delivery.consume_arrival", exc)
    # 只改 current_action（聊天/照片看得到“正在吃”），不写日程块。
    try:
        setter = getattr(plugin, "set_current_action", None)
        if callable(setter):
            setter("正在吃%s" % food, until_ts=now + 1800,
                   place="家", zone="home", category="meal")
    except Exception as exc:
        _note_swallowed("amo_food_delivery.consume_arrival", exc)
    return fact


#: 上下文里和「外卖待送达」互斥的餐馆词：有 pending 时这些行要压掉。
RESTAURANT_CONTEXT_WORDS = ("小馆", "餐厅", "饭馆", "餐馆")


def pending_state(plugin, now=None):
    """pending_food 的一行上下文快照；没有待送达就返回空 dict。"""
    data = pending_order(plugin, now=now)
    if not data:
        return {}
    food = str(data.get("food") or "外卖")
    return {"food": food,
            "activity": "在家等%s，外卖还没到" % food,
            "place": "家",
            "line": pending_context_line(food)}


def pending_context_line(food):
    """pending_food 期间唯一允许的吃饭口径：在家等，外卖还没到。"""
    return ("【吃饭】你点的「%s」还没到，你在家等外卖；这一顿不在外面吃。"
            % str(food or "外卖"))


def pending_context_lines(lines, food):
    """把上下文里的「在小馆/餐厅吃」压掉，换成「在家等外卖」。"""
    kept = [str(line) for line in (lines or ())
            if not any(word in str(line) for word in RESTAURANT_CONTEXT_WORDS)]
    kept.insert(0, pending_context_line(food))
    return kept


def waiting_hint(food):
    return (
        "【吃饭】他点的「%s」还在路上，正等着；不要催他再点，也不要假装已经吃了。" % food
    )


def order_hint(food):
    return (
        "【他给你点了吃的】他刚给你点了「%s」，已经下单/在路上（待送达）。"
        "你可以说等他、到了叫你；但不要假装已经吃到，等他说到了/吃吧再吃。" % food
    )


def arrival_hint(fact):
    food = str((fact or {}).get("food") or "这顿")
    return (
        "【饭到了】他点的「%s」到了/已经吃了。可以自然地接一句吃上了，"
        "不要再问什么时候到，也不要重复说饿。" % food
    )
