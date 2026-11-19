# -*- coding: utf-8 -*-
"""旅游成本层：价签、钱包真扣、逐块结算、断粮/被宰这类纯金额口径。

依赖方向：只依赖 amo_trip_state（kv/save/日期工具），不依赖 schedule/report/门面。
"""

from __future__ import annotations
from typing import Any, TYPE_CHECKING

try:  # Phase 11：金额/元格式化唯一实现在 amo_numbers
    from .amo_numbers import yuan as _yuan
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_numbers import yuan as _yuan

try:  # 价目表：只读静态表（v1 默认口径，坏了就回退旧常量）
    from . import price_book as _price_book
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import price_book as _price_book

import logging
import random

try:  # 包内加载（AstrBot 插件）
    from .amo_trip_state import (
    LOCKED_PHASES, _cfg_get, _day_rows, _days_between, _kv_get, _kv_set, _load_trip,
    _minute_of_text, _now_dt, _shift, cfg, enabled, load, save,
)
except ImportError:  # 直接按文件加载时（tests / 仿真脚本）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_trip_state import (
        LOCKED_PHASES, _cfg_get, _day_rows, _days_between, _kv_get, _kv_set, _load_trip,
        _minute_of_text, _now_dt, _shift, cfg, enabled, load, save,
    )

logger = logging.getLogger("amo.trip")

MULTIPLIER_KINDS = ("food", "transport", "daily")
EXEMPT_CATEGORIES = {"salary", "bill", "mortgage", "utility", "fund", "fine", "refund"}
SPEND_RANGES = {"hotel": (300, 1000), "feast": (200, 500), "ticket": (100, 300),
                "souvenir": (100, 300)}
ASSASSIN_PROB = 0.06
ASSASSIN_RANGE = (80, 200)
# ---------- 第十轮 B3：旅游日逐块结算（成本元数据 + 只结已经发生的块）----------
#: 一块旅游事件只可能属于这四类；住宿每晚一次、大餐/门票每天最多一次、日常按小时。

TRIP_COST_KINDS = ("hotel", "meal", "ticket", "daily")
#: 单位一律是「分」：住宿 300-1000 元/晚、特色大餐 200-500 元、门票/纪念品 100-300 元、日常 20-80 元。

TRIP_COST_RANGES = {
    "hotel": (30000, 100000),
    "meal": (20000, 50000),
    "ticket": (10000, 30000),
    "daily": (2000, 8000),
}
#: 真实扣款的分类：只有日常走 travel_spend 的 1.6 溢价（food/transport/daily），
#: 住宿/大餐/门票按原价；大餐用 feast（不在 MULTIPLIER_KINDS 里）。

TRIP_COST_CATEGORY = {"hotel": "hotel", "meal": "feast", "ticket": "ticket"}
PENDING_PREFIX = "trip_pending:"
DONE_PREFIX = "trip_spent:"
STRANDED_LINE = ("我钱不够买回程票了…能不能先借我一点？"
                 "实在不行我就缩短行程，或者等工资入账再走。")

def return_estimate(plugin):
    return int(cfg(plugin, "travel_return_cost", 800))


def budget_precheck(plugin, balance, nights):
    hotel_min = int(cfg(plugin, "travel_hotel_min", 300))
    food_min = int(cfg(plugin, "travel_food_min", 100))
    budget_min = int(cfg(plugin, "travel_budget_min", 2000))
    need = return_estimate(plugin) + hotel_min * max(0, int(nights)) + food_min * max(0, int(nights))
    need = max(need, budget_min)
    return {"ok": int(balance) >= need, "need": need, "balance": int(balance)}


def silence_ok(plugin):
    """出发后是否允许安静少发消息（10B2 主动消息频率用；这里先把配置接好）。"""
    try:
        return bool(cfg(plugin, "travel_silence_ok", True))
    except Exception as exc:
        logger.debug("[trip] 读 travel_silence_ok 失败，按默认允许: %s", exc)
        return True


#: _conf_schema.json 的默认倍率（静态测试要求 cfg(...) 的字面量一致）。
_DEFAULT_MULTIPLIER = 1.6


def _book_multiplier(plugin):
    """价目表版本倍率；表缺失/非法返回 0（调用方回退配置/1.6）。"""
    try:
        return float(_price_book.multiplier(plugin) or 0)
    except Exception as exc:
        logger.warning("[trip] 读价目表倍率失败，回退配置: %s", exc)
        return 0.0


def price(plugin, kind, base):
    """溢价只作用于 food/transport/daily；默认倍率取价目表，显式配置优先。"""
    if kind not in MULTIPLIER_KINDS:
        return int(base)
    configured = float(cfg(plugin, "travel_price_multiplier", 1.6) or 1.6)
    mult = configured
    if abs(configured - _DEFAULT_MULTIPLIER) <= 1e-9:
        mult = _book_multiplier(plugin) or configured  # 默认口径以价目表为准
    return int(round(float(base) * mult))


def spend(plugin, trip, amount, reason, idem, category="", balance=None):
    """旧入口保留签名；真实扣款统一走 travel_spend（幂等 + 余额下限）。"""
    return travel_spend(plugin, trip, amount, reason, idem,
                        category=category, balance=balance)


def range_amount(kind, rng=None):
    low, high = SPEND_RANGES.get(kind, (100, 300))
    return int((rng or random).randint(low, high))


def assassin(plugin, trip, rng=None, balance=None):
    """刺客：整趟最多一次，概率 6%，扣 80-200（真扣），心情 -8 走安全阀。"""
    trip = dict(trip or {})
    tid = str(trip.get("id") or "")
    key = "trip_assassin:" + tid
    if _kv_get(plugin, key, False):
        return {"hit": False, "reason": "already"}
    rng = rng or random
    prob = float(cfg(plugin, "travel_assassin_prob", 0.06))
    if rng.random() >= prob:
        return {"hit": False, "reason": "dice"}
    amount = rng.randint(*ASSASSIN_RANGE)
    # 消费统一入口：刺客不是 food/transport/daily，不吃旅游溢价。
    result = travel_spend(plugin, trip, amount, "旅游被宰", "assassin",
                          category="", balance=balance)
    if not result.get("ok"):
        return {"hit": False, "reason": str(result.get("reason") or "no_money"),
                "trip": trip}
    trip = dict(result.get("trip") or trip)
    _kv_set(plugin, key, True)
    try:
        plugin.state.add_mood(-8)
    except Exception as exc:
        logger.debug("[trip] 刺客扣心情失败: %s", exc)
    return {"hit": True, "amount": amount, "trip": trip}


def stranded(plugin, balance):
    """断粮三兜底：借闺蜜 → 缩短行程 → 等入账后继续回程。"""
    need = return_estimate(plugin)
    if int(balance) >= need:
        return {"stranded": False, "need": need}
    return {"stranded": True, "need": need,
            "actions": ["borrow", "shorten", "wait"]}


# _yuan 已迁到 amo_numbers.yuan（金额/元格式化统一构造器）。


def _wallet_balance(plugin):
    try:
        return int(plugin.wallet.balance())
    except Exception as exc:
        logger.debug("[trip] 读余额失败: %s", exc)
        return None


def _wallet_has_idem(wallet, idem):
    fn = getattr(wallet, "has_idem", None)
    if not callable(fn):
        return False
    try:
        return bool(fn(idem))
    except Exception as exc:
        logger.debug("[trip] 查幂等键失败: %s", exc)
        return False


def _trip_idem(trip_id, idem):
    text = str(idem or "")
    if trip_id and not text.startswith("trip:%s:" % trip_id):
        return "trip:%s:%s" % (trip_id, text or "misc")
    return text or "misc"


def stranded_line(plugin):
    """断粮求助一句话：night quiet / silence_ok 时不发。"""
    if not silence_ok(plugin):
        return ""
    try:
        try:
            from .amo_shared import quiet_now as _quiet_now
        except ImportError:
            from amo_shared import quiet_now as _quiet_now
    except ImportError as exc:
        logger.debug("[trip] quiet_now 不可用: %s", exc)
        _quiet_now = None  # type: ignore[assignment, unused-ignore]
    if callable(_quiet_now):
        quiet_hours = _cfg_get(plugin, "quiet_hours", "23:30-07:30")
        try:
            if _quiet_now(str(quiet_hours or "")):
                return ""
        except Exception as exc:
            logger.debug("[trip] quiet_now 判断失败: %s", exc)
    return STRANDED_LINE


def check_stranded(plugin, balance=None, trip=None):
    """回程前断粮检查：stranded + shorten/wait 两个可执行兜底。"""
    need = return_estimate(plugin)
    trip = dict(trip or load(plugin))
    if balance is None:
        balance = _wallet_balance(plugin)
    if balance is None:
        return {"stranded": False, "need": need, "balance": None,
                "actions": [], "line": "", "trip": trip}
    if int(balance) >= need:
        return {"stranded": False, "need": need, "balance": int(balance),
                "actions": [], "line": "", "trip": trip}
    return {"stranded": True, "need": need, "balance": int(balance),
            "actions": ["shorten", "wait"], "line": stranded_line(plugin),
            "trip": trip}


def travel_spend(plugin, trip, amount_cents, reason, idem, category="",
                 sub_category="", item_name="", trigger_reason="", balance=None):
    """旅游花钱唯一入口：pending kv → wallet.debit → done + spent。

    余额不足/幂等重复都返回 False：duplicate（has_idem 命中，视为成功不重扣）
    与 no_money（可重试）分开；绝不把余额扣成负数。
    """
    trip = dict(trip or {})
    category = str(category or "")
    if category in EXEMPT_CATEGORIES:
        return {"ok": False, "reason": "exempt"}
    wallet = getattr(plugin, "wallet", None)
    if wallet is None:
        return {"ok": False, "reason": "no_wallet"}
    idem = _trip_idem(str(trip.get("id") or ""), idem)
    if _kv_get(plugin, DONE_PREFIX + idem, False):
        return {"ok": False, "reason": "duplicate"}
    value = price(plugin, category, amount_cents)
    if balance is None:
        balance = _wallet_balance(plugin)
    if balance is not None and int(balance) < value:
        # 上次扣款成功但 done 没落盘（崩溃/写失败）：余额虽低也不能再扣，补记成功。
        if _wallet_has_idem(wallet, idem):
            return _record_spent(plugin, trip, value, idem, duplicate=True)
        return {"ok": False, "reason": "no_money", "amount": value,
                "retryable": True}
    pending_key = PENDING_PREFIX + idem
    if _kv_get(plugin, pending_key, None) is not None and _wallet_has_idem(wallet, idem):
        return _record_spent(plugin, trip, value, idem, duplicate=True)
    if not _kv_set(plugin, pending_key, value):
        return {"ok": False, "reason": "dedupe_write_failed"}
    if _kv_get(plugin, pending_key, None) is None:
        return {"ok": False, "reason": "dedupe_write_failed"}
    paid = _wallet_debit(wallet, value, reason, idem, category, sub_category,
                         item_name, trigger_reason)
    if not paid:
        if _wallet_has_idem(wallet, idem):
            return _record_spent(plugin, trip, value, idem, duplicate=True)
        return {"ok": False, "reason": "no_money", "amount": value, "retryable": True}
    return _record_spent(plugin, trip, value, idem, duplicate=False)


def _wallet_debit(wallet, value, reason, idem, category="", sub_category="",
                  item_name="", trigger_reason=""):
    kwargs = {"category": str(category or ""),
              "sub_category": str(sub_category or ""),
              "item_name": str(item_name or reason or ""),
              "trigger_reason": str(trigger_reason or reason or ""),
              "actor": "bot", "fund_source": "travel"}
    try:
        return bool(wallet.debit(int(value), str(reason or ""), idem, **kwargs))
    except TypeError as exc:
        logger.warning("[trip] 钱包 debit 不支持完整字段，退回基础参数: %s", exc)
        try:
            return bool(wallet.debit(int(value), str(reason or ""), idem))
        except Exception as exc2:
            logger.warning("[trip] 钱包扣款失败: %s", exc2)
            return False
    except Exception as exc:
        logger.warning("[trip] 钱包扣款异常: %s", exc)
        return False


def _record_spent(plugin, trip, value, idem, duplicate=False):
    done_key = DONE_PREFIX + idem
    if _kv_get(plugin, done_key, False):
        return {"ok": False, "reason": "duplicate"}
    if not _kv_set(plugin, done_key, True) or not _kv_get(plugin, done_key, False):
        logger.error("[trip] 幂等键落库失败，退款 %s 元（idem=%s）", _yuan(value), idem)
        _refund(plugin, value, idem)
        return {"ok": False, "reason": "dedupe_write_failed"}
    trip["spent"] = int(trip.get("spent") or 0) + int(value)
    save(plugin, trip)
    logger.info("[trip] 消费 %s 元（旅游，idem=%s，duplicate=%s）", _yuan(value),
                idem, duplicate)
    return {"ok": True, "amount": int(value), "trip": trip, "idem": idem,
            "duplicate": bool(duplicate)}


def _refund(plugin, value, idem):
    wallet = getattr(plugin, "wallet", None)
    credit = getattr(wallet, "credit", None)
    if not callable(credit):
        return False
    try:
        return bool(credit(int(value), "旅游退款", "trip:rollback:" + idem, ref=idem))
    except Exception as exc:
        logger.warning("[trip] 旅游退款失败（需人工核对 %s 元）: %s", _yuan(value), exc)
        return False


# ---------- 第十轮 B3：旅游日逐块结算（只结已经发生的块）----------
def _cost_quote(kind):
    """价签区间：默认查价目表（v1 与旧常量逐项一致）；表坏/缺就回退旧常量。"""
    try:
        return _price_book.quote(None, kind)
    except Exception as exc:
        logger.warning("[trip] 价目表不可用，回退 TRIP_COST_RANGES: %s", exc)
        low, high = TRIP_COST_RANGES.get(kind, TRIP_COST_RANGES["daily"])
        return {"version": "", "min_cents": low, "max_cents": high}


def _trip_cost(kind, trip_id, day, activity, start_text):
    """一块旅游事件的价签：kind + 金额（分）+ 钱包分类 + 价目表版本。"""
    quote = _cost_quote(kind)
    rng = random.Random("trip-cost|%s|%s|%s|%s" % (trip_id, day, kind, start_text))
    return {"kind": kind,
            "base_cents": int(rng.randint(int(quote["min_cents"]),
                                          int(quote["max_cents"]))),
            "category": _cost_category(kind, activity),
            "price_version": str(quote.get("version") or "")}


def _cost_category(kind, activity):
    """只有日常走 food/transport/daily（吃 1.6 溢价）；住宿/大餐/门票按原价。"""
    if kind in TRIP_COST_CATEGORY:
        return TRIP_COST_CATEGORY[kind]
    text = str(activity or "")
    if "打车" in text or "车站" in text or "机场" in text:
        return "transport"
    if "小吃" in text or "夜市" in text or "餐" in text:
        return "food"
    return "daily"


def cost_idem(kind, day, trip, row):
    """固定幂等键：trip:<id>:day<no>:hotel / :meal / :ticket / :daily:<hour>。"""
    night = max(1, _days_between((trip or {}).get("start_day"), day) + 1)
    prefix = "day%d:" % night
    if kind == "daily":
        begin = _minute_of_text((row or {}).get("start"))
        return prefix + "daily:%02d" % (int(begin or 0) // 60)
    return prefix + kind


def cost_reason(trip, kind, day, row):
    """流水文案只写人话：三亚住宿（第1晚）/ 海鲜大餐 / 景区门票 / 打车。"""
    city = str((trip or {}).get("city") or "").strip() or "旅游"
    if kind == "hotel":
        night = max(1, _days_between((trip or {}).get("start_day"), day) + 1)
        return "%s住宿（第%d晚）" % (city, night)
    if kind == "meal":
        return "海鲜大餐"
    if kind == "ticket":
        text = str((row or {}).get("activity") or "")
        return "纪念品" if "纪念品" in text else "景区门票"
    text = str((row or {}).get("activity") or "")
    if "打车" in text or "机场" in text or "车站" in text:
        return "打车"
    if "小吃" in text or "夜市" in text:
        return "当地小吃"
    return "旅游零花"


def _settle_days(trip, today):
    """要结算的日期：今天 + 昨天（跨午夜/离线补结）；昨天早于出发日就跳过。"""
    start_day = str((trip or {}).get("start_day") or "")
    days = [today]
    yesterday = _shift(today, -1)
    if yesterday and (not start_day or yesterday >= start_day):
        days.append(yesterday)
    return days


SETTLE_REPORT_KEY = "trip_settle_last"


def settle_report_line(new_count, recovered_count):
    """只读展示行：新扣 N / 补记 M；有补记才带「崩溃恢复，未重复扣款」。"""
    new_count = max(0, int(new_count or 0))
    recovered_count = max(0, int(recovered_count or 0))
    line = "新扣 %d / 补记 %d" % (new_count, recovered_count)
    if recovered_count:
        line += "（崩溃恢复，未重复扣款）"
    return line


def record_settle_report(plugin, summary):
    """把最近一次结算摘要写进 kv，供 /status 与面板只读展示。

    只做读数落盘：不扣款、不改 trip、不重试结算；同一份 summary 重复调用结果一致。
    """
    summary = summary if isinstance(summary, dict) else {}
    recovered = list(summary.get("recovered") or [])
    settled = list(summary.get("settled") or [])
    new_count = max(0, len(settled) - len(recovered))
    try:
        stamp = int(_now_dt().timestamp())
    except Exception:
        stamp = 0
    report = {
        "day": str(summary.get("day") or ""),
        "new": new_count,
        "recovered": len(recovered),
        "settled": len(settled),
        "duplicate": len(summary.get("duplicate") or []),
        "no_money": len(summary.get("no_money") or []),
        "ok": bool(summary.get("ok")),
        "ts": stamp,
    }
    report["line"] = settle_report_line(new_count, len(recovered))
    _kv_set(plugin, SETTLE_REPORT_KEY, report)
    return report


def settle_report(plugin):
    """只读最近一次结算摘要；没有记录返回 {}（状态页显示「—」）。"""
    data = _kv_get(plugin, SETTLE_REPORT_KEY, None)
    if not isinstance(data, dict):
        return {}
    report = dict(data)
    report["line"] = settle_report_line(report.get("new"), report.get("recovered"))
    return report

def settle_due(plugin, now=None):
    """旅游日逐块结算：只结 start <= now 的块，重复调用绝不重扣。

    钱只走 travel_spend 唯一入口（wallet.debit + pending→done + has_idem 区分
    duplicate / no_money）；余额不足记一笔 no_money 继续往下跑，绝不扣成负数，
    下一次 sync/load 还能重试；旅游关闭或不在 traveling 阶段一律不碰钱包。

    崩溃恢复（钱包已扣、done 没落盘）走 _record_spent(duplicate=True) 补记：
    归入 settled 并带 duplicate=True 标记 + recovered 列表，绝不进 duplicate；
    duplicate 桶只表示本次没扣款的「已结算过」块。
    """
    #: trip 只在真的拿到 trip 后才有值；调用方用 isinstance(..., dict) 判空，
    #: 早退分支绝不能塞 {} 进去（会把状态机手里的 trip 覆盖成空）。
    summary: dict[str, Any] = {"ok": False, "reason": "", "day": "", "settled": [],
               "recovered": [], "duplicate": [], "no_money": [], "future": [],
               "failed": [], "trip": None}
    if not enabled(plugin):
        summary["reason"] = "disabled"
        return summary
    trip = _load_trip(plugin)
    if not trip:
        summary["reason"] = "no_trip"
        return summary
    if str(trip.get("phase") or "") not in LOCKED_PHASES:
        summary["reason"] = "not_traveling"
        return summary
    now_dt = _now_dt(now)
    today = now_dt.date().isoformat()
    now_min = int(now_dt.hour) * 60 + int(now_dt.minute)
    summary["ok"] = True
    summary["day"] = today
    for day in _settle_days(trip, today):
        for row in _day_rows(plugin, day):
            cost = row.get("cost")
            if not isinstance(cost, dict):
                continue
            kind = str(cost.get("kind") or "")
            base = int(cost.get("base_cents") or 0)
            if kind not in TRIP_COST_KINDS or base <= 0:
                continue
            begin = _minute_of_text(row.get("start"))
            if begin is None:
                continue
            # 对外/落库的幂等键带 trip 前缀：trip:<id>:day<no>:hotel 这一套固定格式。
            idem = _trip_idem(str(trip.get("id") or ""),
                              cost_idem(kind, day, trip, row))
            if day == today and begin > now_min:
                summary["future"].append(idem)  # 未来块绝不预扣
                continue
            reason = cost_reason(trip, kind, day, row)
            category = str(cost.get("category") or "") or "daily"
            try:
                paid = travel_spend(plugin, trip, base, reason, idem,
                                    category=category, sub_category=kind,
                                    item_name=reason, trigger_reason=reason)
            except Exception as exc:
                logger.warning("[trip] 结算失败 day=%s idem=%s: %s", day, idem, exc)
                summary["failed"].append({"idem": idem, "reason": "exception"})
                continue
            why = str(paid.get("reason") or "")
            recovered = bool(paid.get("duplicate"))
            if paid.get("ok"):
                trip = dict(paid.get("trip") or trip)
                summary["settled"].append({"idem": idem, "kind": kind,
                                           "amount": int(paid.get("amount") or 0),
                                           "reason": reason,
                                           "duplicate": recovered})
                if recovered:
                    summary["recovered"].append(idem)
            elif why == "duplicate":
                summary["duplicate"].append(idem)
            elif why == "no_money":
                summary["no_money"].append(idem)
            else:
                summary["failed"].append({"idem": idem, "reason": why})
    summary["trip"] = dict(trip)
    if summary["settled"]:
        new_count = len(summary["settled"]) - len(summary["recovered"])
        logger.info("[trip] 旅游日结算 %d 笔（新扣 %d / 补记 %d），共 %d 分",
                    len(summary["settled"]), new_count,
                    len(summary["recovered"]),
                    sum(item["amount"] for item in summary["settled"]))
    # B1b：只把「新扣/补记」写进 kv 供状态页展示；钱包与幂等口径不变。
    record_settle_report(plugin, summary)
    return summary
