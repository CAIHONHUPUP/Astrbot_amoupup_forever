# -*- coding: utf-8 -*-
"""网购与物流：只返回结构化事实；下单不瞬间入库。"""
from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import logging
logger = logging.getLogger("amo")

import random
import time
import uuid

PLATFORMS = ("拼多多", "淘宝", "京东", "闲鱼")


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
            "status": "待发货",
            "created": float(now),
            "eta": 0,
            "logistics": "待发货",
            "user_triggered": bool(user_triggered),
        }
        data["orders"].append(order)
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
            elapsed = now - float(order.get("created") or 0)
            while True:
                status = order.get("status")
                if status == "待发货" and elapsed >= 86400:
                    order["status"] = "运输中"; order["logistics"] = "运输中"; changed = True
                    continue
                if status == "运输中" and elapsed >= 2 * 86400:
                    order["status"] = "派送中"; order["logistics"] = "派送中"; changed = True
                    if not order.get("incident") and random.Random(str(order.get("id")) + ":incident").random() < 0.05:
                        order["incident"] = random.Random(str(order.get("id")) + ":kind").choice(
                            ("卡在派送中好几天", "快递盒破损"))
                        order["incident_at"] = now
                    continue
                if status == "派送中" and elapsed >= 3 * 86400:
                    order["status"] = "已签收"; order["logistics"] = "已签收"; changed = True
                    self._receive(data, order)
                break
        if changed:
            self._save(data)
        return self.snapshot(now)

    def _receive(self, data, order):
        order["received_at"] = _amo_clock.now_ts()
        for item in order.get("items") or []:
            kind = str(item.get("type") or "clothes")
            if kind == "appliance" or kind in ("washer", "dryer"):
                data.setdefault("assets", {})[kind] = {"order": order["id"], "name": item.get("name")}
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
            except Exception as exc:
                logger.debug("[amo] optional step failed: %r", exc)

    def _mark_clean(self, item_id):
        try:
            wardrobe = getattr(self.plugin, "wardrobe", None)
            data = wardrobe._load()
            for item in data.get("items") or []:
                if str(item.get("id")) == str(item_id):
                    item["clean_status"] = "clean"
                    break
            wardrobe._save(data)
        except Exception as exc:
            logger.debug("[amo] optional step failed: %r", exc)

    def return_order(self, order_id, insurance=False, now=None):
        data = self._state()
        order = next((o for o in data["orders"] if o.get("id") == order_id), None)
        if order is None:
            return {"ok": False, "reason": "not_found"}
        fee = 0 if insurance else random.Random(order_id).randint(800, 1200)
        if fee and not self._debit(fee, "退货运费", "return:" + order_id, "她", "她余额"):
            return {"ok": False, "reason": "short"}
        order["status"] = "已退货"
        order["return_fee_cents"] = fee
        self._save(data)
        return {"ok": True, "order_id": order_id, "fee_cents": fee}

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


ECOMMERCE_TOOLS = (
    ("online_order", make_online_order_tool,
     [{"type": "string", "name": "platform", "description": "拼多多/淘宝/京东"},
      {"type": "string", "name": "item_name", "description": "商品名"},
      {"type": "number", "name": "price", "description": "价格（元）"}],
     "她决定网购时调用；扣她余额并生成物流单，不会瞬间入库。"),
    ("add_to_cart", make_cart_tool,
     [{"type": "string", "name": "platform", "description": "平台"},
      {"type": "string", "name": "item_name", "description": "商品名"},
      {"type": "number", "name": "price", "description": "价格（元）"}],
     "她舍不得买、只想放购物车时调用；不扣款。"),
    ("user_gift", make_user_gift_tool,
     [{"type": "string", "name": "item_name", "description": "用户买的礼物"},
      {"type": "number", "name": "price", "description": "价格（元）"}],
     "用户说已经网购送她礼物时调用；actor=用户，不扣她余额。"),
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

