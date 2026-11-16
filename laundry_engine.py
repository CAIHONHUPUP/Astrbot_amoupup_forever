# -*- coding: utf-8 -*-
"""生活系统：洗衣状态机 / 家电决策 / 换季整理。只输出结构化事实，不生成人话。"""
from __future__ import annotations

import logging
logger = logging.getLogger("amo")

import datetime as _dt

try:
    from .clock import now as _clock_now
except ImportError:
    from clock import now as _clock_now

WASH_COST = (200, 500)
DRY_CLEAN_COST = (2000, 3000)
BEDDING_COST = (500, 1200)
APPLIANCES = {"washer": 1500.0, "dryer": 1500.0}


def _now(now=None):
    return now or _clock_now()


def _season_of(month):
    if month in (12, 1, 2):
        return "冬"
    if month in (3, 4, 5):
        return "春"
    if month in (6, 7, 8):
        return "夏"
    return "秋"


class LifeFacts:
    """读写 wardrobe.json 里的 laundry 子状态；所有返回值都是 dict/list。"""

    def __init__(self, plugin):
        self.plugin = plugin

    def _wardrobe(self):
        return getattr(self.plugin, "wardrobe", None)

    def _data(self):
        wardrobe = self._wardrobe()
        if wardrobe is None:
            return None, None
        try:
            return wardrobe, wardrobe._load()
        except Exception:
            return wardrobe, None

    def _save(self, wardrobe, data):
        try:
            wardrobe._save(data)
        except Exception as exc:
            logger.debug("[amo] optional step failed: %r", exc)

    def _items(self, data):
        return list((data or {}).get("items") or [])

    def _state(self, data):
        state = data.setdefault("laundry", {})
        state.setdefault("washing", {})
        state.setdefault("drying", {})
        state.setdefault("assets", {})
        state.setdefault("organized", "")
        state.setdefault("bedding_at", "")
        return state

    def status(self, item):
        return str(item.get("clean_status") or "clean")

    def set_status(self, item, status):
        item["clean_status"] = str(status)

    def dirty_items(self, data=None):
        data = data if data is not None else self._data()[1]
        return [item for item in self._items(data) if self.status(item) == "dirty"]

    def washing_items(self, data=None):
        data = data if data is not None else self._data()[1]
        return [item for item in self._items(data) if self.status(item) == "washing"]

    def clean_items(self, data=None):
        data = data if data is not None else self._data()[1]
        return [item for item in self._items(data) if self.status(item) == "clean"]

    def weather(self):
        try:
            weather = getattr(self.plugin.state, "weather", {}) or {}
        except Exception:
            weather = {}
        text = str(weather.get("text") or "")
        return {"text": text, "temperature": weather.get("temp"),
                "rain": any(w in text for w in ("雨", "雪", "回南"))}

    def snapshot(self, now=None):
        now = _now(now)
        wardrobe, data = self._data()
        state = self._state(data) if data is not None else {}
        items = self._items(data)
        dirty = [i for i in items if self.status(i) == "dirty"]
        washing = [i for i in items if self.status(i) == "washing"]
        weather = self.weather()
        season = _season_of(now.month)
        organized = str(state.get("organized") or "")
        has_warm = any((self.season_of(i) in (season, "四季")) and self.status(i) == "clean" for i in items)
        mismatch = season in ("冬", "秋") and organized != season and not has_warm
        return {
            "dirty_count": len(dirty),
            "washing_count": len(washing),
            "clean_count": len([i for i in items if self.status(i) == "clean"]),
            "no_clean": bool(items) and not any(self.status(i) == "clean" for i in items),
            "weather": weather,
            "has_washer": "washer" in (state.get("assets") or {}),
            "has_dryer": "dryer" in (state.get("assets") or {}),
            "season": season,
            "organized": organized,
            "season_mismatch": mismatch,
            "bedding_due": str(state.get("bedding_at") or "") != now.strftime("%Y-%m"),
            "appliance_quote": self.quote("dryer", now=now),
        }

    @staticmethod
    def season_of(item):
        value = str(item.get("season") or "").strip()
        if value in ("春", "夏", "秋", "冬", "四季"):
            return value
        name = str(item.get("name") or "")
        if any(w in name for w in ("羽绒", "大衣", "毛呢", "毛衣", "绒", "加厚", "棉服")):
            return "冬"
        if any(w in name for w in ("短袖", "短裤", "凉鞋", "吊带", "雪纺")):
            return "夏"
        if any(w in name for w in ("衬衫", "卫衣", "长裤", "运动鞋")):
            return "春"
        return "四季"

    def quote(self, kind="dryer", now=None):
        price = APPLIANCES.get(str(kind), 0.0)
        balance = 0.0
        try:
            balance = float(self.plugin.wallet.balance()) / 100.0
        except Exception:
            try:
                balance = float(self.plugin.wallet.balance_cents()) / 100.0
            except Exception:
                balance = 0.0
        return {"kind": kind, "price": price, "balance": round(balance, 2),
                "affordable": balance >= price}

    def start_wash(self, now=None, force=False):
        now = _now(now)
        wardrobe, data = self._data()
        if data is None:
            return {"ok": False, "reason": "no_wardrobe"}
        state = self._state(data)
        dirty = self.dirty_items(data)
        weekend = now.weekday() >= 5 and now.hour >= 19
        # 这里只给 LLM/调度层提供“可以洗”的状态，不替她决定；force 供 LLM 决策后调用
        if not force and len(dirty) < 3 and not weekend:
            return {"ok": False, "reason": "not_enough", "dirty_count": len(dirty)}
        chosen = dirty[:8]
        for item in chosen:
            self.set_status(item, "washing")
            state["washing"][str(item.get("id"))] = now.timestamp() + 3600
        self._save(wardrobe, data)
        self._charge(300)
        return {"ok": True, "count": len(chosen), "wash_until": now.timestamp() + 3600,
                "dirty_count": len(self.dirty_items(data))}

    def tick(self, now=None):
        now = _now(now)
        wardrobe, data = self._data()
        if data is None:
            return {}
        state = self._state(data)
        weather = self.weather()
        changed = False
        for item in self._items(data):
            if self.status(item) != "washing":
                continue
            item_id = str(item.get("id"))
            until = float(state["washing"].get(item_id) or 0)
            if until and now.timestamp() >= until:
                if state.get("assets", {}).get("dryer"):
                    self.set_status(item, "clean")
                else:
                    item["drying_until"] = now.timestamp() + (12 if weather["rain"] else 6) * 3600
                state["washing"].pop(item_id, None)
                changed = True
        for item in self._items(data):
            if self.status(item) != "washing" or not item.get("drying_until"):
                continue
            if now.timestamp() >= float(item["drying_until"]):
                if weather["rain"] and now.timestamp() - float(item["drying_until"]) > 36 * 3600:
                    self.set_status(item, "dirty")
                    state["mold"] = True
                else:
                    self.set_status(item, "clean")
                item.pop("drying_until", None)
                changed = True
        if changed:
            self._save(wardrobe, data)
        return self.snapshot(now)

    def dry_clean(self, now=None):
        now = _now(now)
        wardrobe, data = self._data()
        if data is None:
            return {"ok": False}
        dirty = self.dirty_items(data)
        if not dirty:
            return {"ok": False, "reason": "none"}
        for item in dirty:
            self.set_status(item, "clean")
        self._save(wardrobe, data)
        self._charge(2500)
        return {"ok": True, "count": len(dirty)}

    def buy_appliance(self, kind="dryer", now=None):
        kind = str(kind or "").strip()
        if kind not in APPLIANCES:
            return {"ok": False, "reason": "unknown"}
        quote = self.quote(kind, now)
        if not quote["affordable"]:
            return {"ok": False, "reason": "short", **quote}
        self._charge(int(APPLIANCES[kind] * 100))
        wardrobe, data = self._data()
        if data is not None:
            state = self._state(data)
            state.setdefault("assets", {})[kind] = {"price": APPLIANCES[kind]}
            self._save(wardrobe, data)
        return {"ok": True, "kind": kind, "price": APPLIANCES[kind],
                "balance": round(quote["balance"] - APPLIANCES[kind], 2)}

    def organize(self, now=None):
        now = _now(now)
        wardrobe, data = self._data()
        if data is None:
            return {"ok": False}
        state = self._state(data)
        state["organized"] = _season_of(now.month)
        self._save(wardrobe, data)
        self._charge(200)
        return {"ok": True, "season": state["organized"]}

    def _charge(self, cents):
        try:
            wallet = self.plugin.wallet
            for name in ("spend", "debit", "pay"):
                fn = getattr(wallet, name, None)
                if callable(fn):
                    fn(int(cents))
                    return True
        except Exception as exc:
            logger.debug("[amo] optional step failed: %r", exc)
        return False


BUY_TOOL_NAME = "buy_appliance"
BUY_TOOL_ARGS = [
    {"type": "string", "name": "appliance_name",
     "description": "washer 或 dryer"},
    {"type": "number", "name": "price",
     "description": "她看到的标价，单位元；仅用于确认，不覆盖系统价"},
]


def make_buy_appliance_tool(plugin):
    async def buy_appliance(event=None, appliance_name="dryer", price=0.0):
        import json as _json
        result = LifeFacts(plugin).buy_appliance(str(appliance_name or "dryer"))
        return _json.dumps(result, ensure_ascii=False)
    return buy_appliance


def register_buy_appliance(plugin, manager):
    """幂等注册：先移除同名旧工具，再挂新 handler；失败只记日志。"""
    if manager is None:
        return False
    try:
        if manager.get_func(BUY_TOOL_NAME) is not None:
            manager.remove_func(BUY_TOOL_NAME)
        manager.add_func(BUY_TOOL_NAME, BUY_TOOL_ARGS,
                         "当她想买洗衣机/烘干机时调用；会校验余额、扣款并把家电写入资产。",
                         make_buy_appliance_tool(plugin))
        return True
    except Exception:
        return False

