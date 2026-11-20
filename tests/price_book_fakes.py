# -*- coding: utf-8 -*-
"""价目表测试的离线假件：内存 DB / 钱包 / 状态，零真实库读写。"""
from __future__ import annotations

import datetime as dt

import amo_trip as trip

DAY = "2026-11-17"


def at(day, hour, minute=0):
    return dt.datetime.combine(dt.date.fromisoformat(day), dt.time(int(hour), int(minute)))


class FakeDB:
    def __init__(self):
        self.kv = {}
        self.schedules = {}
        self.versions = {}
        self.saves = 0
        self.todos_map = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return [dict(row) for row in (self.schedules.get(str(day)) or [])]

    def save_schedule(self, day, items):
        self.saves += 1
        self.schedules[str(day)] = [dict(row) for row in items]

    def schedule_version(self, day):
        return self.versions.get(str(day), 0)

    def bump_schedule_version(self, day):
        self.versions[str(day)] = self.schedule_version(str(day)) + 1
        return self.versions[str(day)]

    def mark_diaries_stale(self, day, start_hour, version):
        return []

    def todos(self, day, include_done=False):
        return [dict(row) for row in self.todos_map.get(str(day), [])]

    def clear_todos(self, day):
        self.todos_map[str(day)] = []
        return True

    def add_todo(self, day, item, source=""):
        row = dict(item)
        row.setdefault("id", len(self.todos_map.get(str(day), [])) + 1)
        self.todos_map.setdefault(str(day), []).append(row)
        return int(row["id"])

    def close_todo(self, todo_id):
        return True


class FakeWallet:
    """和真钱包同语义：idem 命中或余额不足返回 False，余额绝不为负。"""

    def __init__(self, balance=0):
        self.cents = int(balance)
        self.idems = {}
        self.debits = []
        self.attempts = []
        self.rows = []

    def balance(self):
        return self.cents

    def has_idem(self, idem):
        return str(idem) in self.idems

    def debit(self, amount, reason="", idem="", **kwargs):
        row = {"amount": int(amount), "reason": reason, "idem": str(idem)}
        row.update(kwargs)
        self.attempts.append(dict(row))
        if str(idem) in self.idems or self.cents < int(amount):
            return False
        self.idems[str(idem)] = -int(amount)
        self.cents -= int(amount)
        self.debits.append(dict(row))
        self.rows.append({"idem": str(idem), "delta": -int(amount), "reason": reason})
        return True

    def credit(self, amount, reason="", idem="", **kwargs):
        if str(idem) in self.idems:
            return False
        self.idems[str(idem)] = int(amount)
        self.cents += int(amount)
        self.rows.append({"idem": str(idem), "delta": int(amount), "reason": reason})
        return True

    def ledger(self, limit=50):
        return list(reversed(self.rows))[:max(1, int(limit))]


class FakeState:
    def __init__(self, plugin):
        self.p = plugin
        self.db = plugin.db
        self.schedule = {}
        self.mood = {}

    def commit_schedule_change(self, day, new_items, old_items=None, source="",
                               reason="", **kwargs):
        rows = [dict(row) for row in new_items]
        self.schedule[str(day)] = rows
        self.db.save_schedule(str(day), rows)
        return {"changed": True, "saved": True,
                "version": self.db.bump_schedule_version(str(day))}

    def scenic_spots(self):
        return [{"city": "三亚", "name": "亚龙湾"}, {"city": "杭州", "name": "西湖"}]

    def add_mood(self, delta):
        self.mood["added"] = self.mood.get("added", 0) + int(delta)


class FakePlugin:
    def __init__(self, balance=500000, config=None):
        self.db = FakeDB()
        self.wallet = FakeWallet(balance)
        self.state = FakeState(self)
        self.config = {
            "travel_enabled": True,
            "travel_return_cost": 800,
            "travel_price_multiplier": 1.6,
            "travel_silence_ok": True,
            "quiet_hours": "03:00-03:01",
            "travel_days_max": 3,
        }
        if config:
            self.config.update(config)

    def cfg_int(self, name, default=0):
        return int(self.config.get(name, default))

    def cfg_float(self, name, default=0.0):
        return float(self.config.get(name, default))

    def cfg_bool(self, name, default=False):
        return bool(self.config.get(name, default))

    def cfg_str(self, name, default=""):
        return str(self.config.get(name, default))

    def today(self):
        return DAY


def traveling(plugin, city="三亚", start=DAY, days=3, phase="traveling"):
    info = trip.new_trip(city, start, days)
    info["phase"] = phase
    trip.save(plugin, info)
    return info


def make_row(start, end, activity, place, zone, category, kind, base,
             cost_category, trip_id="trip-fixture", version=None):
    cost = {"kind": kind, "base_cents": int(base), "category": cost_category}
    if version is not None:
        cost["price_version"] = version
    return {"start": start, "end": end, "activity": activity, "place": place,
            "zone": zone, "category": category, "source": "trip_pool",
            "trip": trip_id, "cost": cost}


def put_rows(plugin, day, rows):
    plugin.state.schedule[str(day)] = [dict(row) for row in rows]
    plugin.db.schedules[str(day)] = [dict(row) for row in rows]
