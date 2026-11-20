# -*- coding: utf-8 -*-
"""宵夜领域入口（2026-11-17）：决定 → 日程块 → 扣款 → 日记/主动。

为什么单独一个模块：以前「22-24 饿了就直接扣钱」写在守护插件的 late_night_engine 里，
它不落日程块、不写审计、不主动说；而 amo 的微型事件另有一套餐饮去重。两边各判一次，
以后必然漂移。现在：
- 决定层、块、扣款、日记、主动消息都收口到这里；
- 餐饮成本/去重/块切分复用 amo_food_cost（与 micro_events 同一套）；
- 守护插件只调 amo 的 late_snack_tick()，不再自己拼 schedule/ledger/日记。

规则（2026-11-17 用户确认）：
- 22:00-24:00 可主动说一句「我饿了」再决定；00:00-06:00 不主动发消息（不吵醒他）；
- 生病（发烧/感冒/不舒服）一律 skip：不扣款、不补宵夜块、不主动说，只写「喝温水」日记；
- 不吃则不动钱、不动日程；
- 同一小时只决定一次（kv 审计），同一块只扣一次，同 item 10 分钟去重。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

import datetime as _dt
import hashlib
import logging

try:
    from . import amo_food_cost as _food
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_food_cost as _food

try:
    from .clock import now as _now
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from clock import now as _now

logger = logging.getLogger("amo.food")

SNACKS = ("烧烤", "泡面", "炸鸡", "小蛋糕")
SNACK_START_HOUR = 22
QUIET_START_HOUR = 0
QUIET_END_HOUR = 6
MIN_CASH_CENTS = 2000
BLOCK_MINUTES = 20
#: 一周最多几次宵夜（2026-11-17 用户口径）
WEEKLY_SNACK_CAP = 3


def decide(now, hunger, mood, cash, roll, sick=False):
    """纯决定函数：概率只看「饿不饿 + 心情」，余额/时段做小幅修正；生病直接不吃。

    2026-11-17 用户口径（22-23 点、余额充足时）：
    - 正常吃完晚饭（饥饿 >=60）：20%
    - 饿了（25 <= 饥饿 < 60）：40%
    - 很饿（<25）且心情低落（<40）：60%
    0-6 点再降 10 个点；余额 <50 元降 20 个点。roll 是每小时确定性哈希。
    """
    hunger = float(hunger)
    if hunger >= 60:
        base = 15.0
    elif hunger >= 25:
        base = 35.0
    else:
        base = 45.0
    mood_bonus = 10.0 if float(mood) < 40 else 0.0
    cash_bonus = 5.0 if int(cash) >= 5000 else -20.0
    hour = int(now.hour)
    time_bonus = 0.0 if hour in (22, 23) else -10.0
    health_bonus = -100.0 if sick else 0.0
    score = max(0.0, min(80.0, base + mood_bonus + cash_bonus + time_bonus + health_bonus))
    eat = bool(not sick and float(roll) < score)
    return {
        "eat": eat, "score": round(score, 1), "roll": int(roll),
        "factors": {"hunger": round(base, 1), "mood": mood_bonus,
                    "cash": cash_bonus, "time": time_bonus, "health": health_bonus},
        "reason": "hungry" if eat else ("sick" if sick else "not_enough_pull"),
    }

def _roll(key):
    return int(hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:8], 16) % 100


def _night_key(now):
    """宵夜归属的夜晚：0-6 点算前一天的晚上（22 点吃和 0 点吃算同一晚）。"""
    day = now.date() if int(now.hour) >= 6 else (now.date() - _dt.timedelta(days=1))
    return day.isoformat()


def _week_key(now):
    iso = now.isocalendar()
    return "%04d-W%02d" % (iso[0], iso[1])


def _night_eaten(plugin, now):
    """今晚（22:00 起算，0-6 点算前一晚）是否已经吃过宵夜。"""
    data = _kv_get(plugin, "late_snack_eaten", {})
    return isinstance(data, dict) and str(data.get("night") or "") == _night_key(now)


def _week_used(plugin, now):
    data = _kv_get(plugin, "late_snack_week", {})
    if not isinstance(data, dict) or str(data.get("week") or "") != _week_key(now):
        return 0
    try:
        return int(data.get("used") or 0)
    except (TypeError, ValueError):
        return 0


def _mark_eaten(plugin, now):
    """记下「今晚吃过了」+ 本周计数 +1（跨周自动从 0 开始）。"""
    _kv_set(plugin, "late_snack_eaten", {"night": _night_key(now), "at": _now().timestamp()})
    _kv_set(plugin, "late_snack_week", {"week": _week_key(now), "used": _week_used(plugin, now) + 1})

def _snack_skip(plugin, now, slot_key, audit_key):
    """宵夜硬门：今晚已吃过 / 本周已满 3 次 → 返回 skip 结果；否则 None。"""
    if _night_eaten(plugin, now):
        _kv_set(plugin, slot_key, "already_eaten")
        _audit(plugin, audit_key, {"eat": False, "skipped": "already_eaten",
                                   "night": _night_key(now)})
        return {"ok": True, "eat": False, "skipped": "already_eaten"}
    used = _week_used(plugin, now)
    if used >= WEEKLY_SNACK_CAP:
        _kv_set(plugin, slot_key, "weekly_cap")
        _audit(plugin, audit_key, {"eat": False, "skipped": "weekly_cap",
                                   "week": _week_key(now), "used": used})
        return {"ok": True, "eat": False, "skipped": "weekly_cap", "week_used": used}
    return None


def _hm(minute):
    minute = max(0, min(24 * 60, int(minute)))
    return "24:00" if minute >= 24 * 60 else "%02d:%02d" % (minute // 60, minute % 60)


def _kv_get(plugin, key, default=None):
    try:
        return plugin.db.kv_get(key, default)
    except Exception as exc:
        logger.debug("[amo] 读 kv 失败 %s: %r", key, exc)
        return default


def _kv_set(plugin, key, value):
    try:
        plugin.db.kv_set(key, value)
        return True
    except Exception as exc:
        logger.debug("[amo] 写 kv 失败 %s: %r", key, exc)
        return False


def _audit(plugin, key, payload):
    row = dict(payload or {})
    row.setdefault("at", _now().timestamp())
    _kv_set(plugin, key, row)


def _health(plugin):
    info = getattr(getattr(plugin, "state", None), "health_info", None)
    if not callable(info):
        return {"status": "健康", "sick": False}
    try:
        data = dict(info() or {})
    except Exception as exc:
        logger.debug("[amo] 读健康状态失败: %r", exc)
        return {"status": "健康", "sick": False}
    data["sick"] = bool(data.get("sick"))
    return data


def _diary(plugin, day, hour, text):
    try:
        plugin.db.save_diary(day, "%02d" % int(hour), str(text or ""))
        return True
    except Exception as exc:
        logger.debug("[amo] 写小日记失败: %r", exc)
        return False


def _place_block(plugin, day, hour, now, food, amount, pushed):
    """把宵夜窗口落进日程：返回 amo_food_cost.ensure_block_window 的结果。"""
    start_min = hour * 60 + int(now.minute)
    end_min = min(24 * 60, start_min + BLOCK_MINUTES)
    detail = "宵夜 %s 元｜来源 late_snack｜%s" % ("%.2f" % (amount / 100.0),
                                                 "主动" if pushed else "自主")
    return _food.ensure_block_window(plugin, day, _hm(start_min), _hm(end_min),
                                     "吃%s（宵夜）" % food, detail,
                                     source="late_snack", reason="宵夜冲动")


async def _push(plugin, text):
    push = getattr(plugin, "push_to_owner", None)
    if not callable(push):
        return False
    try:
        return bool(await push(str(text or "")))
    except Exception as exc:
        logger.debug("[amo] 主动消息发送失败: %r", exc)
        return False


async def late_snack_tick(plugin, now=None, roll=None, say=None):
    """守护插件只调这一个入口；返回决定/块/扣款结果，失败不抛。"""
    now = now or _now()
    late = getattr(plugin, "late_night_state", None)
    if getattr(plugin, "state", None) is None or not callable(late):
        return {"ok": False, "skipped": "no_state"}
    try:
        info = dict(late() or {})
    except Exception as exc:
        logger.debug("[amo] 读熬夜状态失败: %r", exc)
        return {"ok": False, "skipped": "state_error"}
    hour = int(now.hour)
    # 2026-11-18 用户反馈：00-05 点人已经睡了，却每小时生成一条「忍住了」的花钱决定。
    # 宵夜只在 22:00-24:00 做一次决策；睡着后的时段不再生成任何宵夜决定。
    proactive = SNACK_START_HOUR <= hour < 24
    if not proactive:
        return {"ok": False, "skipped": "outside_window"}
    if not info.get("active"):
        return {"ok": False, "skipped": "inactive"}
    day = now.strftime("%Y-%m-%d")
    slot_key = "late_snack:%s:%02d" % (day, hour)
    audit_key = "late_snack_decision:%s:%02d" % (day, hour)
    if _kv_get(plugin, slot_key, False):
        return {"ok": True, "skipped": "already_decided", "eat": False}
    blocked = _snack_skip(plugin, now, slot_key, audit_key)
    if blocked:
        return blocked
    health = _health(plugin)
    if health.get("sick"):
        _kv_set(plugin, slot_key, "sick")
        _audit(plugin, audit_key, {"eat": False, "skipped": "sick",
                                   "health": str(health.get("status") or "")})
        _diary(plugin, day, hour, "有点不舒服，今晚不吃宵夜了，喝点温水早点睡。")
        return {"ok": True, "eat": False, "skipped": "sick",
                "health": str(health.get("status") or "")}
    try:
        hunger = float(plugin.get_hunger())
    except Exception:
        hunger = 100.0
    try:
        cash = int(plugin.wallet.balance() or 0)
    except Exception:
        return {"ok": False, "skipped": "no_wallet"}
    try:
        mood = float(plugin.state.mood_score())
    except Exception:
        mood = 60.0
    seed = "late_snack:%s:%s" % (day, hour)
    roll_value = _roll(seed) if roll is None else int(roll)
    decision = decide(now, hunger, mood, cash, roll_value)
    _audit(plugin, audit_key, {"eat": decision["eat"], "decision": decision,
                               "hunger": hunger, "mood": mood, "cash": cash,
                               "seed": seed})
    if cash < MIN_CASH_CENTS:
        _kv_set(plugin, slot_key, "no_money")
        return {"ok": True, "eat": False, "skipped": "no_money", "decision": decision}
    if not decision["eat"]:
        _kv_set(plugin, slot_key, "skip")
        return {"ok": True, "eat": False, "skipped": "not_hungry", "decision": decision}
    food = SNACKS[_roll(seed + ":food") % len(SNACKS)]
    amount = int(_food.price_for(food) or 1500)
    if cash < amount:
        _kv_set(plugin, slot_key, "no_money")
        return {"ok": True, "eat": True, "skipped": "no_money", "decision": decision}
    if _food.recent_duplicate(plugin.wallet, food, now=now):
        _kv_set(plugin, slot_key, "duplicate")
        return {"ok": True, "eat": True, "skipped": "duplicate",
                "food": food, "decision": decision}
    if _food.late_snack_spent(plugin, now=now):
        _kv_set(plugin, slot_key, "duplicate")
        return {"ok": True, "eat": True, "skipped": "duplicate",
                "food": food, "decision": decision}
    pushed = False
    willing = bool(say) if say is not None else _roll(seed + ":say") < 70
    if proactive and willing and not _kv_get(plugin, "late_snack_push:" + day, False):
        pushed = await _push(plugin, "我饿了，想点个%s当宵夜。" % food)
        _kv_set(plugin, "late_snack_push:" + day, True)
    block = _place_block(plugin, day, hour, now, food, amount, pushed)
    if not block.get("ok"):
        _kv_set(plugin, slot_key, "no_block")
        _audit(plugin, audit_key, {"eat": True, "skipped": "no_block", "block": block})
        return {"ok": True, "eat": True, "skipped": "no_block",
                "food": food, "decision": decision}
    bid = str(block.get("block_id") or "")
    if bid and _kv_get(plugin, "late_snack_block:" + bid, False):
        return {"ok": True, "eat": True, "skipped": "block_charged", "block_id": bid}
    idem = "late_snack:%s:%s:%s" % (day, hour, bid[:8])
    ref = "block:%s %s-%s" % (bid, block.get("start"), block.get("end"))
    try:
        paid = bool(plugin.wallet.debit(
            amount, "夜宵：%s" % food, idem, ref=ref, category="餐饮",
            sub_category="夜宵", item_name=food, trigger_reason="饿了想吃宵夜"))
    except Exception as exc:
        logger.warning("[amo] 宵夜扣款异常: %r", exc)
        paid = False
    if not paid:
        _kv_set(plugin, slot_key, "charge_failed")
        _audit(plugin, audit_key, {"eat": True, "skipped": "charge_failed",
                                   "block_id": bid, "amount": amount})
        return {"ok": True, "eat": True, "skipped": "charge_failed",
                "block_id": bid, "decision": decision}
    if bid:
        _kv_set(plugin, "late_snack_block:" + bid, True)
    _kv_set(plugin, slot_key, "eat")
    _mark_eaten(plugin, now)
    _audit(plugin, audit_key, {"eat": True, "charged": amount, "food": food,
                               "block_id": bid, "block": [block.get("start"), block.get("end")],
                               "idem": idem, "ref": ref, "pushed": pushed,
                               "decision": decision})
    try:
        plugin.add_hunger("amo", 15)
    except Exception as exc:
        logger.debug("[amo] 宵夜加饱腹失败: %r", exc)
    try:
        plugin.note_late_snack(amount, food)
    except Exception as exc:
        logger.debug("[amo] 宵夜状态记录失败: %r", exc)
    _diary(plugin, day, hour, "没忍住点了份%s，花了%.0f块。" % (food, amount / 100))
    # 大日记已定 B 方案（次日 08:00 写昨天）：这里不标 stale、不强刷当天。
    # 23:30 的宵夜由次日早上写昨天自然包含；00:00 后算第二天，写进第二天的日记。
    return {"ok": True, "eat": True, "charged": amount, "food": food, "block_id": bid,
            "block": {"start": block.get("start"), "end": block.get("end")},
            "pushed": pushed, "idem": idem, "decision": decision}
