# -*- coding: utf-8 -*-
"""网购与物流：只返回结构化事实；下单不瞬间入库。"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import logging
import time
logger = logging.getLogger("amo")

import random
import time
import uuid

PLATFORMS = ("拼多多", "淘宝", "京东", "闲鱼")


SHOP_MIN_INTERVAL = 24 * 3600
CLOTHES_PRICE_RANGE = (3000, 50000)      # 30-500 元
FURNITURE_PRICE_RANGE = (30000, 500000)  # 300-5000 元
SHOP_DUP_DAYS = 30
ARRIVAL_KV = "shop_arrival"  # 每天最多 1 单，避免一分钟一条刷屏
SHOP_OPEN_HOUR = 9
SHOP_CLOSE_HOUR = 22
SHOP_BUDGET_RATIO = 0.20
TOTAL_MONTHLY_LIMIT = 5
CLOTHES_MONTHLY_LIMIT = 2
FURNITURE_MONTHLY_LIMIT = 1


def _transit_time(ts):
    """17:00 前下单当天 17:00 发车；之后次日 17:00 发车。"""
    import time as _time
    local = _time.localtime(float(ts))
    cutoff = _time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 17, 0, 0, 0, 0, -1))
    return cutoff if float(ts) < cutoff else cutoff + 86400


def _month_start(ts):
    import time as _time
    local = _time.localtime(float(ts))
    return _time.mktime((local.tm_year, local.tm_mon, 1, 0, 0, 0, 0, 0, -1))


CLOTHING_CATEGORIES = ("coat", "top", "bottom", "skirt", "shoes", "socks")


def _item_kind(item):
    kind = str((item or {}).get("type") or "").strip()
    name = str((item or {}).get("name") or "")
    household = ("床单", "被套", "枕套", "枕头", "被子", "毛毯", "毛巾",
                 "牙刷", "地毯", "窗帘", "挂画", "抱枕", "靠垫")
    if any(word in name for word in household):
        return "household"
    if kind in ("furniture", "家具"):
        return "furniture"
    if _looks_furniture(name):
        return "furniture"
    category = str((item or {}).get("category") or "")
    if kind in ("clothes", "gift") or category in CLOTHING_CATEGORIES:
        return "clothes"
    return "other"


def month_request_count(data, now):
    """本月网购请求数 = 已下单（含退货）+ 仍在「想买」的；合计最多 5。"""
    since = _month_start(now)
    orders = [o for o in (data.get("orders") or [])
              if float(o.get("created") or 0) >= since]
    wishes = [w for w in (data.get("wishes") or [])
              if float(w.get("created") or 0) >= since
              and str(w.get("status") or "") == "想买"]
    return len(orders) + len(wishes)


def _same_request(wish, item):
    a = str((wish or {}).get("name") or "")
    b = str((item or {}).get("name") or "")
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    slot_a = None
    try:
        from .housing import furniture_slot as _slot
    except ImportError:
        from housing import furniture_slot as _slot
    slot_a = _slot(a)
    return bool(slot_a and slot_a == _slot(b))


def _monthly_purchase_count(data, kind, now):
    since = _month_start(now)
    count = 0
    for order in data.get("orders") or []:
        if float(order.get("created") or 0) < since:
            continue
        for item in order.get("items") or []:
            if _item_kind(item) == kind:
                count += 1
    if kind == "furniture":
        try:
            stamps = data.get("furniture_purchases") or []
        except Exception:
            stamps = []
        count += sum(1 for ts in stamps if float(ts or 0) >= since)
    return count


def budget_limit_error(items, data, now, cash_cents):
    """每月网购总消费 ≤ 活期存款 20%（退货的单不算）。"""
    since = _month_start(now)
    spent = 0
    for order in data.get("orders") or []:
        if float(order.get("created") or 0) < since:
            continue
        if str(order.get("status") or "") == "已退货":
            continue
        refunded = int(order.get("refund_cents") or 0) + int(order.get("broken_refund_cents") or 0)
        spent += max(0, int(order.get("amount_cents") or 0) - refunded)
    incoming = sum(int((item or {}).get("price_cents") or 0) for item in items)
    cap = int(max(0, int(cash_cents)) * SHOP_BUDGET_RATIO)
    if spent + incoming > cap:
        return {"ok": False, "reason": "budget_limit", "cap": cap / 100.0,
                "spent": spent / 100.0, "incoming": incoming / 100.0}
    return None


def monthly_limit_error(items, data, now):
    """下单前检查：衣服 2 件/月、家具 1 件/月；超了直接拒绝。"""
    for kind, limit in (("clothes", CLOTHES_MONTHLY_LIMIT),
                        ("furniture", FURNITURE_MONTHLY_LIMIT)):
        incoming = sum(1 for item in items if _item_kind(item) == kind)
        if not incoming:
            continue
        used = _monthly_purchase_count(data, kind, now)
        if used + incoming > limit:
            return {"ok": False, "reason": "monthly_limit", "kind": kind,
                    "limit": limit, "used": used, "incoming": incoming}
    return None


class EcommerceEngine:
    def __init__(self, plugin):
        self.plugin = plugin

    def _state(self):
        try:
            data = self.plugin.db.kv_get("ecommerce_state", {}) or {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("orders", [])
        data.setdefault("cart", [])
        data.setdefault("assets", {})
        return data

    def _save(self, data):
        try:
            self.plugin.db.kv_set("ecommerce_state", data)
        except Exception as exc:
            logger.debug("[amo] optional step failed: %r", exc)

    def _wallet(self):
        return getattr(self.plugin, "wallet", None)

    def panel_data(self, now=None):
        """网购面板：已下单/运输中/已到货 三栏 + 本月限额。"""
        now = float(now or _amo_clock.now_ts())
        self.tick(now=now)
        data = self._state()
        month_start = _month_start(now)
        groups: dict[str, list[dict[str, Any]]] = {"wished": [], "ordered": [],
                                                   "transit": [], "delivered": [],
                                                   "returned": []}
        for wish in reversed(data.get("wishes") or []):
            if str(wish.get("status")) != "想买":
                continue
            if float(wish.get("created") or 0) < month_start:
                continue
            groups["wished"].append({"id": wish.get("id"), "status": "想买",
                                     "items": [wish.get("name")],
                                     "amount": int(wish.get("price_cents") or 0) / 100.0,
                                     "platform": wish.get("platform"),
                                     "created": wish.get("created"),
                                     "deliver_at": 0, "transit_at": 0, "refund": 0})
        for order in reversed(data.get("orders") or []):
            status = str(order.get("status") or "")
            if float(order.get("created") or 0) < month_start:
                continue
            row = {"id": order.get("id"), "status": status,
                   "items": [str((it or {}).get("name") or "") for it in order.get("items") or []],
                   "kind": ("furniture" if any(_item_kind_safe(it) == "furniture"
                                               for it in order.get("items") or []) else "clothes"),
                   "amount": int(order.get("amount_cents") or 0) / 100.0,
                   "platform": order.get("platform"),
                   "created": order.get("created"),
                   "transit_at": order.get("transit_at"),
                   "deliver_at": order.get("deliver_at"),
                   "refund": int(order.get("refund_cents") or 0) / 100.0}
            if status == "已退货":
                groups["returned"].append(row)
            elif status == "已到货":
                groups["delivered"].append(row)
            elif status == "运输中":
                groups["transit"].append(row)
            else:
                groups["ordered"].append(row)
        arrival = None
        try:
            raw_arrival = self.plugin.db.kv_get(ARRIVAL_KV, None)
            if isinstance(raw_arrival, dict) and "items" not in raw_arrival:
                raw_arrival = {"items": [{"line": raw_arrival.get("line"),
                                           "at": raw_arrival.get("at"),
                                           "notified": raw_arrival.get("notified", False)}],
                               "seen": raw_arrival.get("seen", False)}
            arrival = raw_arrival
        except Exception:
            arrival = None
        return {"groups": groups, "arrival": arrival,
                "household": list(reversed(data.get("household") or []))[-20:],
                "limits": {"total": TOTAL_MONTHLY_LIMIT, "clothes": CLOTHES_MONTHLY_LIMIT,
                           "furniture": FURNITURE_MONTHLY_LIMIT,
                           "used_total": month_request_count(data, now),
                           "used_clothes": _monthly_purchase_count(data, "clothes", now),
                           "used_furniture": _monthly_purchase_count(data, "furniture", now)}}

    def _cash_cents(self):
        """活期现金存款：只用 cash 账户，不含定期/股票/基金。"""
        try:
            return int(self.plugin.wallet.balance("cash"))
        except Exception:
            try:
                return int(self.plugin.wallet.balance())
            except Exception:
                return 0

    def _balance(self):
        try:
            return int(self.plugin.wallet.balance())
        except Exception:
            return 0

    def _debit(self, cents, reason, idem, actor, fund_source):
        try:
            return bool(self.plugin.wallet.debit(
                int(cents), reason, idem, actor=actor, fund_source=fund_source,
                category="网购", item_name=reason, trigger_reason="ecommerce"))
        except Exception:
            return False

    def wish(self, name, price_cents, platform="淘宝", now=None):
        """她只是「想买」：不扣钱、不占额度，玩家可以劝她别买。"""
        body = str(name or "").strip()
        if not body:
            return {"ok": False, "reason": "empty"}
        data = self._state()
        wishes = data.setdefault("wishes", [])
        import time as _t
        local = _t.localtime(float(now or _amo_clock.now_ts()))
        day_start = _t.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
        month_start = _month_start(now or _amo_clock.now_ts())
        month_wishes = [w for w in wishes if float(w.get("created") or 0) >= month_start
                        and str(w.get("status")) == "想买"]
        limit = 5
        if month_request_count(data, now or _amo_clock.now_ts()) + 1 > limit:
            return {"ok": False, "reason": "wish_monthly_limit", "limit": limit,
                    "used": month_request_count(data, now or _amo_clock.now_ts())}
        today_wishes = [w for w in month_wishes if float(w.get("created") or 0) >= day_start]
        if today_wishes:
            return {"ok": False, "reason": "wish_too_soon", "limit": 1}
        dup_since = float(now or _amo_clock.now_ts()) - SHOP_DUP_DAYS * 86400
        for order in data.get("orders") or []:
            if float(order.get("created") or 0) < dup_since:
                continue
            if any(_same_request({"name": body}, it) for it in order.get("items") or []):
                return {"ok": False, "reason": "duplicate_item",
                        "msg": "同款 30 天内买过了"}
        for old in wishes:
            if str(old.get("name") or "") == body and str(old.get("status")) == "想买":
                return {"ok": True, "duplicate": True, "wish": old}
        row = {"id": "wish-" + uuid.uuid4().hex[:8], "name": body,
               "price_cents": int(price_cents or 0), "platform": str(platform or "淘宝"),
               "created": float(now or _amo_clock.now_ts()), "status": "想买"}
        wishes.append(row)
        self._save(data)
        return {"ok": True, "wish": row}

    def add_to_cart(self, name, price_cents, platform="淘宝"):
        data = self._state()
        item = {"id": "c-" + uuid.uuid4().hex[:8], "name": str(name or ""),
                "price_cents": int(price_cents or 0), "platform": str(platform or "淘宝")}
        data["cart"].append(item)
        self._save(data)
        return {"ok": True, "cart": item, "cart_count": len(data["cart"])}

    def remove_from_cart(self, cart_id):
        data = self._state()
        data["cart"] = [i for i in data["cart"] if i.get("id") != cart_id]
        self._save(data)
        return {"ok": True, "cart_count": len(data["cart"])}

    def order(self, items, platform="淘宝", amount_cents=None, actor="她",
              fund_source="她余额", user_triggered=False, now=None, idem=""):
        now = now or _amo_clock.now_ts()
        items = [dict(i) for i in (items or []) if str((i or {}).get("name") or "").strip()]
        if not items:
            return {"ok": False, "reason": "empty"}
        if len(items) != 1:
            return {"ok": False, "reason": "one_item",
                    "msg": "一次只能买一件（多件请分开下单）"}
        total = int(amount_cents if amount_cents is not None else sum(
            int(i.get("price_cents") or 0) for i in items))
        actor = "用户" if user_triggered else str(actor or "她")
        fund = "用户支付" if user_triggered else str(fund_source or "她余额")
        data = self._state()
        if idem:
            for old in data["orders"]:
                if old.get("idem") == idem:
                    return {"ok": True, "duplicate": True, "order": old,
                            "balance": self._balance()}
        matched_wish = any(_same_request(wish, items[0])
                           for wish in data.get("wishes") or []
                           if str(wish.get("status")) == "想买")
        incoming = 0 if matched_wish else 1
        if month_request_count(data, now) + incoming > TOTAL_MONTHLY_LIMIT:
            return {"ok": False, "reason": "monthly_limit", "kind": "total",
                    "limit": TOTAL_MONTHLY_LIMIT,
                    "used": month_request_count(data, now), "incoming": incoming}
        limited = monthly_limit_error(items, data, now)
        if limited:
            return limited
        budget = budget_limit_error(items, data, now, self._cash_cents())
        if budget:
            return budget
        local = time.localtime(float(now))
        if not (SHOP_OPEN_HOUR <= local.tm_hour < SHOP_CLOSE_HOUR):
            return {"ok": False, "reason": "quiet_hours",
                    "msg": "这个点不网购（9:00-22:00 才买）"}
        kind = _item_kind(items[0])
        price = int(items[0].get("price_cents") or 0)
        ranges = {"clothes": CLOTHES_PRICE_RANGE, "furniture": FURNITURE_PRICE_RANGE}
        low, high = ranges.get(kind, (0, 0))
        if price and low and not (low <= price <= high):
            return {"ok": False, "reason": "price_range",
                    "msg": "价格不合理（衣服 30-500 元、家具 300-5000 元）"}
        dup_since = float(now) - SHOP_DUP_DAYS * 86400
        dup_name = str(items[0].get("name") or "")
        for old in data.get("orders") or []:
            if float(old.get("created") or 0) < dup_since:
                continue
            if str(old.get("status") or "") == "已退货":
                continue
            if any(dup_name and dup_name == str((it or {}).get("name") or "")
                   for it in old.get("items") or []):
                return {"ok": False, "reason": "duplicate_item",
                        "msg": "同款 30 天内买过了"}
        recent = [float(o.get("created") or 0) for o in data["orders"]
                  if str(o.get("status") or "") != "已退货"
                  and float(o.get("created") or 0) <= float(now)]
        if recent and float(now) - max(recent) < SHOP_MIN_INTERVAL:
            return {"ok": False, "reason": "too_soon", "min_interval_hours": 24,
                    "next_at": max(recent) + SHOP_MIN_INTERVAL}
        if not user_triggered:
            if self._balance() < total:
                return {"ok": False, "reason": "short", "total": total,
                        "balance": self._balance()}
            if not self._debit(total, "、".join(i["name"] for i in items),
                               "ecommerce:" + uuid.uuid4().hex, actor, fund):
                return {"ok": False, "reason": "pay_failed"}
        order = {
            "id": "o-" + uuid.uuid4().hex[:8], "idem": str(idem or ""),
            "items": items,
            "platform": str(platform or "淘宝"),
            "amount_cents": total,
            "actor": actor,
            "fund_source": fund,
            "status": "已下单",
            "created": float(now),
            "transit_at": _transit_time(float(now)),
            "deliver_at": _transit_time(float(now)) + 2 * 86400,
            "eta": _transit_time(float(now)) + 2 * 86400,
            "logistics": "已下单",
            "user_triggered": bool(user_triggered),
        }
        data["orders"].append(order)
        for wish in data.setdefault("wishes", []):
            if str(wish.get("status")) == "想买" and _same_request(wish, items[0]):
                wish["status"] = "已下单"
                wish["order_id"] = order["id"]
        if any(_item_kind(item) == "furniture" for item in items):
            data.setdefault("furniture_purchases", []).append(float(now))
        self._save(data)
        return {"ok": True, "order": order, "balance": self._balance()}

    def user_gift(self, text="", items=None, platform="淘宝", now=None):
        """识别用户代买：不扣她余额，actor=用户。"""
        value = str(text or "")
        if not any(w in value for w in ("网上", "网购", "淘宝", "京东", "拼多多", "礼物")):
            return {"ok": False, "reason": "not_gift"}
        if not any(w in value for w in ("给你", "送你", "买给", "礼物", "买了")):
            return {"ok": False, "reason": "not_gift"}
        gift_items = items or [{"name": "礼物", "price_cents": 0, "type": "gift"}]
        return self.order(gift_items, platform=platform, amount_cents=0,
                          actor="用户", fund_source="用户支付",
                          user_triggered=True, now=now)

    def tick(self, now=None):
        now = now or _amo_clock.now_ts()
        data = self._state()
        changed = False
        for order in data["orders"]:
            status = order.get("status")
            if status in ("已到货", "已退货"):
                continue
            transit_at = float(order.get("transit_at") or 0)
            if not transit_at:
                transit_at = _transit_time(float(order.get("created") or now))
                order["transit_at"] = transit_at
                order["deliver_at"] = transit_at + 2 * 86400
                changed = True
            deliver_at = float(order.get("deliver_at") or transit_at + 2 * 86400)
            if status == "已下单" and now >= transit_at:
                order["status"] = "运输中"; order["logistics"] = "运输中"; changed = True
                status = "运输中"
                roll = random.Random(str(order.get("id")) + ":delay").random()
                if roll < 0.08 and not order.get("delay_days"):
                    order["delay_days"] = 1
                    order["incident"] = "快递延误"
                    order["deliver_at"] = float(order.get("deliver_at") or 0) + 86400
                    deliver_at = float(order["deliver_at"])
            if status == "运输中" and now >= deliver_at:
                order["status"] = "已到货"; order["logistics"] = "已到货"; changed = True
                if not order.get("received_at"):
                    self._receive(data, order)
                    broken = random.Random(str(order.get("id")) + ":break").random()
                    if broken < 0.03:
                        refund = int(int(order.get("amount_cents") or 0) * 0.2)
                        if refund > 0:
                            try:
                                self.plugin.wallet.credit(refund, "快递破损补偿",
                                                          "broken:" + str(order.get("id")))
                                order["broken_refund_cents"] = refund
                                order["incident"] = "包装破损，退部分款"
                            except Exception as exc:
                                logger.debug("[amo] 破损补偿失败: %r", exc)
        if changed:
            self._save(data)
        return self.snapshot(now)

    def mark_arrival_seen(self):
        try:
            arrival = self.plugin.db.kv_get(ARRIVAL_KV, None)
            if isinstance(arrival, dict):
                arrival["seen"] = True
                self.plugin.db.kv_set(ARRIVAL_KV, arrival)
        except Exception as exc:
            logger.debug("[amo] 到货标记失败: %r", exc)

    def _receive(self, data, order):
        order["received_at"] = _amo_clock.now_ts()
        names = "、".join(str((it or {}).get("name") or "") for it in order.get("items") or [])
        try:
            arrival = self.plugin.db.kv_get(ARRIVAL_KV, {}) or {}
            items = list(arrival.get("items") or [])
            items.append({"line": names, "at": order["received_at"], "notified": False})
            self.plugin.db.kv_set(ARRIVAL_KV, {"items": items[-10:], "seen": False})
        except Exception as exc:
            logger.debug("[amo] 到货提醒写入失败: %r", exc)
        for item in order.get("items") or []:
            kind = _item_kind(item)
            if kind in ("furniture", "家具"):
                # 买了家具：家的场景图要重画，旧的立刻删（2026-11-19）
                housing = getattr(self.plugin, "housing", None)
                if housing is not None:
                    try:
                        housing.add_furniture(str(item.get("name") or "家具"))
                    except Exception as exc:
                        logger.debug("[amo] 家具入屋失败: %r", exc)
                continue
            if kind in ("household", "other"):
                data.setdefault("household", []).append(
                    {"name": str(item.get("name") or ""), "at": order["received_at"]})
                continue
            if kind == "appliance" or kind in ("washer", "dryer"):
                data.setdefault("assets", {})[kind] = {"order": order["id"], "name": item.get("name")}
                housing = getattr(self.plugin, "housing", None)
                if housing is not None:
                    try:
                        housing.add_furniture(str(item.get("name") or "洗衣机"),
                                              source="网购")
                    except Exception as exc:
                        logger.debug("[amo] 家电进房屋失败: %r", exc)
                try:
                    self.plugin.db.kv_set("house_assets", data["assets"])
                except Exception as exc:
                    logger.debug("[amo] optional step failed: %r", exc)
                continue
            try:
                wardrobe = getattr(self.plugin, "wardrobe", None)
                if wardrobe is not None:
                    result = wardrobe.add_item(str(item.get("category") or "top"),
                                               str(item.get("name") or "礼物"),
                                               source="ecommerce")
                    if result.get("ok"):
                        item["wardrobe_id"] = (result.get("item") or {}).get("id", "")
                        self._mark_clean(item.get("wardrobe_id"))
                        try:
                            from .outfit_refs import spawn_ensure_ref
                        except ImportError:
                            from outfit_refs import spawn_ensure_ref
                        spawn_ensure_ref(self.plugin, result.get("item") or {})
            except Exception as exc:
                logger.debug("[amo] optional step failed: %r", exc)

    def _mark_clean(self, item_id):
        try:
            wardrobe: Any = getattr(self.plugin, "wardrobe", None)
            data = wardrobe._load()
            for item in data.get("items") or []:
                if str(item.get("id")) == str(item_id):
                    item["clean_status"] = "clean"
                    break
            wardrobe._save(data)
        except Exception as exc:
            logger.debug("[amo] optional step failed: %r", exc)

    def return_order(self, order_id="", name="", now=None):
        """她想退货：物品立即消失、钱原路退回钱包、流水显示「退货X退款Y元」。"""
        data = self._state()
        target = None
        for order in reversed(data["orders"]):
            if order_id and str(order.get("id")) != str(order_id):
                continue
            if str(order.get("status")) in ("已退货",):
                continue
            item_names = [str((it or {}).get("name") or "") for it in order.get("items") or []]
            if name and not any(str(name) in n for n in item_names):
                continue
            if not name and not order_id:
                target = order
                break
            target = order
            break
        if target is None:
            return {"ok": False, "reason": "not_found"}
        amount = int(target.get("amount_cents") or 0)
        joined_names = "、".join(str((it or {}).get("name") or "") for it in target.get("items") or [])
        for item in target.get("items") or []:
            kind = _item_kind_safe(item)
            if kind == "furniture":
                housing = getattr(self.plugin, "housing", None)
                if housing is not None:
                    try:
                        housing.remove_furniture(str(item.get("name") or ""))
                    except Exception as exc:
                        logger.debug("[amo] 退货家具失败: %r", exc)
            else:
                wardrobe = getattr(self.plugin, "wardrobe", None)
                if wardrobe is not None:
                    wid = str(item.get("wardrobe_id") or "")
                    if not wid:
                        try:
                            for row in wardrobe._load().get("items", []):
                                if row.get("name") == item.get("name"):
                                    wid = str(row.get("id"))
                                    break
                        except Exception:
                            wid = ""
                    if wid:
                        try:
                            wardrobe.delete_item(wid)
                        except Exception as exc:
                            logger.debug("[amo] 退货衣服失败: %r", exc)
        if amount > 0:
            try:
                self.plugin.wallet.credit(int(amount), "退货" + joined_names + "退款%.2f元" % (amount / 100.0),
                                          "refund:" + str(target.get("id")))
            except Exception as exc:
                logger.debug("[amo] 退货退款失败: %r", exc)
        target["status"] = "已退货"
        target["refund_cents"] = amount
        target["refunded_at"] = float(now or _amo_clock.now_ts())
        self._save(data)
        return {"ok": True, "order_id": target.get("id"), "item": joined_names,
                "refund_cents": amount}

    def snapshot(self, now=None):
        data = self._state()
        recent = []
        try:
            recent = self.plugin.wallet.ledger(8)
        except Exception:
            recent = []
        return {
            "cart_count": len(data["cart"]),
            "price_compare_due": self.price_compare_facts(now),
            "cart": data["cart"][-5:],
            "orders": [{"id": o.get("id"), "status": o.get("status"),
                        "platform": o.get("platform"), "amount_cents": o.get("amount_cents"),
                        "actor": o.get("actor"), "fund_source": o.get("fund_source"),
                        "incident": o.get("incident", ""),
                        "items": [i.get("name") for i in (o.get("items") or [])]}
                       for o in data["orders"][-5:]],
            "ledger": [{"delta_cents": r.get("delta_cents"), "reason": r.get("reason"),
                        "actor": r.get("actor"), "fund_source": r.get("fund_source")}
                       for r in recent],
        }


    def note_price_compare(self, item_name, offline_cents, online_cents=0, now=None):
        now = now or _amo_clock.now_ts()
        data = self._state()
        data.setdefault("price_compare", []).append({
            "id": "pc-" + uuid.uuid4().hex[:8], "item": str(item_name or ""),
            "offline_cents": int(offline_cents or 0), "online_cents": int(online_cents or 0),
            "due": float(now) + 2 * 3600,
        })
        self._save(data)
        return {"ok": True}

    def price_compare_facts(self, now=None):
        now = now or _amo_clock.now_ts()
        data = self._state()
        due = [row for row in data.get("price_compare", []) if float(row.get("due") or 0) <= now]
        return due



FURNITURE_WORDS = ("沙发", "电视", "电视柜", "茶几", "床", "衣柜", "书桌", "电脑桌",
                   "餐桌", "餐椅", "椅子", "书柜", "书架", "冰箱", "灶台", "油烟机",
                   "橱柜", "洗手台", "浴缸", "马桶", "镜子", "窗帘", "地毯", "空调",
                   "落地灯", "床头柜", "绿植", "挂画")


def _item_kind_safe(item):
    kind = str((item or {}).get("type") or "")
    name = str((item or {}).get("name") or "")
    household = ("床单", "被套", "枕套", "枕头", "被子", "毛毯", "毛巾",
                 "牙刷", "地毯", "窗帘", "挂画", "抱枕", "靠垫")
    if any(word in name for word in household):
        return "household"
    if kind in ("furniture", "家具") or _looks_furniture(name):
        return "furniture"
    return "clothes"


def _looks_furniture(name):
    body = str(name or "")
    return any(word in body for word in FURNITURE_WORDS)


def _event_idem(event):
    import hashlib
    raw = (str(getattr(event, "message_str", "") or "") + "|"
           + str(getattr(event, "unified_msg_origin", "") or ""))
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:20]


def make_online_order_tool(plugin):
    async def online_order(event=None, platform="淘宝", item_name="", price=0.0):
        import json as _json
        key = _event_idem(event)
        result = EcommerceEngine(plugin).order(
            [{"name": str(item_name or "小物件"), "category": "top",
              "price_cents": int(round(float(price or 0) * 100))}],
            platform=str(platform or "淘宝"),
            amount_cents=int(round(float(price or 0) * 100)),
            idem="online:" + key)
        return _json.dumps(result, ensure_ascii=False)
    return online_order


def make_cart_tool(plugin):
    async def add_to_cart(event=None, platform="淘宝", item_name="", price=0.0):
        import json as _json
        result = EcommerceEngine(plugin).add_to_cart(
            str(item_name or "小物件"), int(round(float(price or 0) * 100)),
            str(platform or "淘宝"))
        return _json.dumps(result, ensure_ascii=False)
    return add_to_cart


def make_user_gift_tool(plugin):
    async def user_gift(event=None, item_name="", price=0.0):
        import json as _json
        engine = EcommerceEngine(plugin)
        result = engine.order(
            [{"name": str(item_name or "礼物"), "category": "top",
              "price_cents": int(round(float(price or 0) * 100))}],
            platform="淘宝", amount_cents=0, actor="用户",
            fund_source="用户支付", user_triggered=True,
            idem="gift:" + _event_idem(event))
        return _json.dumps(result, ensure_ascii=False)
    return user_gift


def make_shop_wish_tool(plugin):
    async def shop_wish(event=None, item_name="", price=0.0, platform="淘宝"):
        import json as _json
        result = EcommerceEngine(plugin).wish(
            str(item_name or ""), int(round(float(price or 0) * 100)), str(platform or "淘宝"))
        return _json.dumps(result, ensure_ascii=False)
    return shop_wish


def make_return_order_tool(plugin):
    async def return_order(event=None, item_name="", order_id=""):
        import json as _json
        result = EcommerceEngine(plugin).return_order(
            order_id=str(order_id or ""), name=str(item_name or ""))
        return _json.dumps(result, ensure_ascii=False)
    return return_order


ECOMMERCE_TOOLS = (
    ("online_order", make_online_order_tool,
     [{"type": "string", "name": "platform", "description": "拼多多/淘宝/京东"},
      {"type": "string", "name": "item_name", "description": "商品名"},
      {"type": "number", "name": "price", "description": "价格（元）"}],
     "她决定网购时调用；扣她余额并生成物流单，不会瞬间入库。她不是每月都要买，随缘即可，不用买满额度。"),
    ("add_to_cart", make_cart_tool,
     [{"type": "string", "name": "platform", "description": "平台"},
      {"type": "string", "name": "item_name", "description": "商品名"},
      {"type": "number", "name": "price", "description": "价格（元）"}],
     "她舍不得买、只想放购物车时调用；不扣款。"),
    ("user_gift", make_user_gift_tool,
     [{"type": "string", "name": "item_name", "description": "用户买的礼物"},
      {"type": "number", "name": "price", "description": "价格（元）"}],
     "用户说已经网购送她礼物时调用；actor=用户，不扣她余额。"),
    ("shop_wish", make_shop_wish_tool,
     [{"type": "string", "name": "item_name", "description": "想买的东西"},
      {"type": "number", "name": "price", "description": "价格（元）"},
      {"type": "string", "name": "platform", "description": "淘宝/京东/拼多多"}],
     "她只是「想买」、还没下单时调用：不扣钱不占额度，玩家可以劝她别买。"),
    ("return_order", make_return_order_tool,
     [{"type": "string", "name": "item_name", "description": "要退的商品名（可不填）"},
      {"type": "string", "name": "order_id", "description": "订单 id（可不填）"}],
     "她自己决定退货时调用：物品立即消失、钱原路退回、守护流水记「退货X退款Y元」。"),
)


def register_ecommerce_tools(plugin, manager):
    if manager is None:
        return False
    try:
        for name, factory, args, desc in ECOMMERCE_TOOLS:
            if manager.get_func(name) is not None:
                manager.remove_func(name)
            manager.add_func(name, args, desc, factory(plugin))
        return True
    except Exception:
        return False

