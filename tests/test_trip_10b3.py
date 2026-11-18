# -*- coding: utf-8 -*-
"""第十轮 B3：旅游日逐块结算 + 跨午夜出发。

每条都「改回去就红」，反向验证写在文件末尾的注释里（也用 pytest -c 里的
monkeypatch 实测过）：
- 去掉 settle_due 的 start <= now 判断（改成预扣未来）→ FutureBlockTests 红；
- 去掉固定幂等键（day<no>:hotel / :meal / :ticket / :daily:<hour>）→
  HotelOnceTests / DailyHourTests / ReloadSettleTests 红；
- 把 _leg_rows 的跨午夜切片改回单块 → MidnightLegTests 红；
- 去掉 commit_schedule_change 的 trip_leg 未来日豁免 → MidnightStateTests 红。
"""
from __future__ import annotations

import copy
import datetime as dt
import os
import sys
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_trip as trip  # noqa: E402
import amo_trip_schedule  # noqa: E402
from amo_human_state import HumanState  # noqa: E402
from amo_state_common import today_str  # noqa: E402

# 状态类用例跟着真实今天走（跨午夜第二程 = 明天，未来日豁免才测得到）；
# 结算类用例全部显式传 now，不受跑测试的时刻影响。
DAY = today_str()


def _day(offset):
    return (dt.date.fromisoformat(DAY) + dt.timedelta(days=offset)).isoformat()


def _at(day, hour, minute=0):
    return dt.datetime.combine(dt.date.fromisoformat(day), dt.time(int(hour), int(minute)))


def _minute(text):
    body = str(text or "00:00").strip()[:5]
    hour, _sep, minute = body.partition(":")
    return int(hour or 0) * 60 + int(minute or 0)


def _no_overlap(rows):
    ordered = sorted([row for row in rows if isinstance(row, dict)],
                     key=lambda row: _minute(row.get("start")))
    for prev, nxt in zip(ordered, ordered[1:]):
        if _minute(prev.get("end")) > _minute(nxt.get("start")):
            return False
    return True


def _contiguous(rows):
    ordered = sorted([row for row in rows if isinstance(row, dict)],
                     key=lambda row: _minute(row.get("start")))
    for prev, nxt in zip(ordered, ordered[1:]):
        if _minute(prev.get("end")) != _minute(nxt.get("start")):
            return False
    return True


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
    """和真钱包同样的语义：idem 命中或余额不足都返回 False，余额绝不为负。"""

    def __init__(self, balance=0):
        self.cents = int(balance)
        self.idems = {}
        self.rows = []
        self.debits = []
        self.credits = []
        self.attempts = []

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
        self.credits.append({"amount": int(amount), "reason": reason, "idem": str(idem)})
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
        self.events = None
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


def _raw(plugin):
    return dict(plugin.db.kv[trip.KEY])


def _row(start, end, activity, place, zone, category, kind, base, cost_category,
         trip_id="trip-fixture"):
    return {"start": start, "end": end, "activity": activity, "place": place,
            "zone": zone, "category": category, "source": "trip_pool",
            "trip": trip_id,
            "cost": {"kind": kind, "base_cents": int(base),
                     "category": cost_category}}


def _traveling(plugin, city="三亚", start=DAY, days=3, phase="traveling"):
    info = trip.new_trip(city, start, days)
    info["phase"] = phase
    trip.save(plugin, info)
    return info


def _put_rows(plugin, day, rows):
    plugin.state.schedule[str(day)] = [dict(row) for row in rows]
    plugin.db.schedules[str(day)] = [dict(row) for row in rows]


def _legs(plugin, day, kind="outbound"):
    return [dict(row) for row in plugin.state.schedule.get(day, [])
            if str(row.get("leg") or "") == kind]


def _patch_legs(pack=30, station=(30, 30), ride=(180, 180)):
    """Phase 8：长途腿的实现搬到了 amo_trip_schedule，补丁要打在真正读常量的模块上。"""
    return mock.patch.multiple(amo_trip_schedule, LEG_PACK_MIN=pack,
                               LEG_STATION_MIN=station, LEG_RIDE_MIN=ride)


class PoolCostTests(unittest.TestCase):
    def test_day_pool_has_one_hotel_and_capped_meal_ticket(self):
        plugin = FakePlugin()
        _traveling(plugin)
        for offset in range(0, 4):
            day = _day(offset)
            rows = trip.travel_day_blocks(plugin, day)
            self.assertTrue(3 <= len(rows) <= 5, rows)
            kinds = [row["cost"]["kind"] for row in rows]
            self.assertEqual(kinds.count("hotel"), 1, rows)
            self.assertLessEqual(kinds.count("meal"), 1, rows)
            self.assertLessEqual(kinds.count("ticket"), 1, rows)
            for row in rows:
                cost = row["cost"]
                self.assertIn(cost["kind"], trip.TRIP_COST_KINDS)
                low, high = trip.TRIP_COST_RANGES[cost["kind"]]
                self.assertTrue(low <= cost["base_cents"] <= high, row)
                self.assertTrue(_no_overlap(rows), rows)

    def test_hour_event_carries_cost_without_charging(self):
        plugin = FakePlugin()
        _traveling(plugin)
        plan = trip.travel_event_for_hour(plugin, DAY, 10)
        self.assertEqual(plan["cost_cents"], 0)
        self.assertIn(plan["cost"]["kind"], trip.TRIP_COST_KINDS)
        low, high = trip.TRIP_COST_RANGES[plan["cost"]["kind"]]
        self.assertTrue(low <= plan["cost"]["base_cents"] <= high, plan)
        night = trip.travel_event_for_hour(plugin, DAY, 23)
        self.assertEqual(night["zone"], "hotel")
        self.assertIsNone(night["cost"])
        self.assertEqual(plugin.wallet.debits, [], "到点只给价签，不扣钱")


class SettlementTests(unittest.TestCase):
    def test_hotel_charged_once_per_night(self):
        plugin = FakePlugin()
        info = _traveling(plugin)
        rows = trip.travel_day_blocks(plugin, DAY)
        _put_rows(plugin, DAY, rows)
        hotel = [row for row in rows if row["cost"]["kind"] == "hotel"][0]
        summary = trip.settle_due(plugin, now=_at(DAY, 23, 30))
        self.assertTrue(summary["ok"], summary)
        idem = "trip:%s:day1:hotel" % info["id"]
        hits = [row for row in plugin.wallet.debits if row["idem"] == idem]
        self.assertEqual(len(hits), 1, plugin.wallet.debits)
        self.assertEqual(hits[0]["amount"], hotel["cost"]["base_cents"])
        self.assertEqual(hits[0]["reason"], "三亚住宿（第1晚）")
        # 同一晚再 sync/load 不重扣
        trip.settle_due(plugin, now=_at(DAY, 23, 55))
        self.assertEqual(len([row for row in plugin.wallet.debits
                              if row["idem"] == idem]), 1)

    def test_meal_and_ticket_at_most_once_per_day(self):
        plugin = FakePlugin()
        info = _traveling(plugin)
        _put_rows(plugin, DAY, [
            _row("08:00", "09:00", "睡到自然醒", "酒店房间", "hotel", "rest",
                 "hotel", 30000, "hotel", info["id"]),
            _row("10:00", "11:00", "打卡当地景点", "景区", "scenic", "play",
                 "ticket", 15000, "ticket", info["id"]),
            _row("12:00", "13:00", "吃当地小吃", "餐厅", "restaurant", "meal",
                 "meal", 25000, "feast", info["id"]),
            _row("15:00", "16:00", "买纪念品", "商场", "mall", "shopping",
                 "ticket", 12000, "ticket", info["id"]),
            _row("18:00", "19:00", "吃当地小吃", "餐厅", "restaurant", "meal",
                 "meal", 28000, "feast", info["id"]),
        ])
        summary = trip.settle_due(plugin, now=_at(DAY, 22, 0))
        self.assertTrue(summary["ok"], summary)
        idems = [row["idem"] for row in plugin.wallet.debits]
        self.assertEqual(sorted(idems), ["trip:%s:day1:hotel" % info["id"],
                                         "trip:%s:day1:meal" % info["id"],
                                         "trip:%s:day1:ticket" % info["id"]])
        meal = [row for row in plugin.wallet.debits if row["idem"].endswith(":meal")][0]
        ticket = [row for row in plugin.wallet.debits
                  if row["idem"].endswith(":ticket")][0]
        self.assertEqual(meal["amount"], 25000)
        self.assertEqual(ticket["amount"], 15000)
        self.assertEqual(meal["reason"], "海鲜大餐")
        self.assertEqual(ticket["reason"], "景区门票")
        self.assertEqual(len(summary["duplicate"]), 2, summary["duplicate"])

    def test_daily_is_hour_level_idempotent(self):
        plugin = FakePlugin()
        info = _traveling(plugin)
        _put_rows(plugin, DAY, [
            _row("14:00", "14:30", "吃当地小吃", "餐厅", "restaurant", "meal",
                 "daily", 4000, "food", info["id"]),
            _row("14:30", "15:00", "打车被绕路", "路上", "road", "commute",
                 "daily", 4500, "transport", info["id"]),
            _row("16:00", "17:00", "逛夜市", "商场", "mall", "play",
                 "daily", 5000, "daily", info["id"]),
        ])
        summary = trip.settle_due(plugin, now=_at(DAY, 22, 0))
        self.assertTrue(summary["ok"], summary)
        idems = sorted(row["idem"] for row in plugin.wallet.debits)
        self.assertEqual(idems, ["trip:%s:day1:daily:14" % info["id"],
                                 "trip:%s:day1:daily:16" % info["id"]])
        amounts = {row["idem"]: row["amount"] for row in plugin.wallet.debits}
        self.assertEqual(amounts["trip:%s:day1:daily:14" % info["id"]],
                         trip.price(plugin, "food", 4000))
        self.assertEqual(amounts["trip:%s:day1:daily:16" % info["id"]],
                         trip.price(plugin, "daily", 5000))
        self.assertEqual(len(summary["duplicate"]), 1, summary["duplicate"])
        self.assertEqual(summary["no_money"], [])

    def test_future_blocks_never_precharged(self):
        plugin = FakePlugin()
        info = _traveling(plugin)
        _put_rows(plugin, DAY, [
            _row("19:00", "20:00", "吃当地小吃", "餐厅", "restaurant", "meal",
                 "daily", 4000, "food", info["id"]),
        ])
        early = trip.settle_due(plugin, now=_at(DAY, 12, 0))
        self.assertTrue(early["ok"], early)
        self.assertEqual(plugin.wallet.debits, [], "未来块绝不预扣")
        self.assertEqual(plugin.wallet.cents, 500000)
        self.assertEqual(_raw(plugin)["spent"], 0)
        self.assertTrue(early["future"], early)
        later = trip.settle_due(plugin, now=_at(DAY, 19, 5))
        self.assertEqual(len(later["settled"]), 1, later)
        self.assertEqual(len(plugin.wallet.debits), 1)
        self.assertEqual(_raw(plugin)["spent"], trip.price(plugin, "food", 4000))

    def test_no_money_keeps_balance_and_retries_later(self):
        plugin = FakePlugin(balance=1000)
        info = _traveling(plugin)
        _put_rows(plugin, DAY, [
            _row("12:00", "13:00", "吃当地小吃", "餐厅", "restaurant", "meal",
                 "daily", 4000, "food", info["id"]),
            _row("16:00", "17:00", "逛夜市", "商场", "mall", "play",
                 "daily", 100, "daily", info["id"]),
        ])
        first = trip.settle_due(plugin, now=_at(DAY, 22, 0))
        self.assertEqual(first["no_money"], ["trip:%s:day1:daily:12" % info["id"]])
        # 付不起的那笔只记 no_money，后面的块照常结（不抛错、不中断）
        self.assertEqual([row["idem"] for row in plugin.wallet.debits],
                         ["trip:%s:day1:daily:16" % info["id"]])
        self.assertEqual(plugin.wallet.cents, 1000 - trip.price(plugin, "daily", 100))
        self.assertGreaterEqual(plugin.wallet.cents, 0)
        self.assertFalse(plugin.db.kv_get("trip_spent:trip:%s:day1:daily:12"
                                          % info["id"], False))
        plugin.wallet.cents = 100000
        second = trip.settle_due(plugin, now=_at(DAY, 22, 30))
        self.assertEqual(len(second["settled"]), 1, second)
        self.assertEqual(plugin.wallet.cents, 100000 - trip.price(plugin, "food", 4000))
        self.assertEqual(_raw(plugin)["spent"],
                         trip.price(plugin, "food", 4000)
                         + trip.price(plugin, "daily", 100))

    def test_repeated_sync_and_load_do_not_double_charge(self):
        plugin = FakePlugin()
        _traveling(plugin)
        _put_rows(plugin, DAY, [
            _row("08:00", "09:00", "睡到自然醒", "酒店房间", "hotel", "rest",
                 "hotel", 60000, "hotel"),
            _row("12:00", "13:00", "吃当地小吃", "餐厅", "restaurant", "meal",
                 "meal", 25000, "feast"),
            _row("20:00", "21:00", "吃当地小吃", "餐厅", "restaurant", "meal",
                 "daily", 3000, "food"),
        ])
        fixed = _at(DAY, 23, 30)
        with mock.patch.object(trip, "_now_dt", return_value=fixed):
            first = trip.sync(plugin)
            self.assertEqual(first["phase"], "traveling")
            charged = len(plugin.wallet.debits)
            self.assertEqual(charged, 3, plugin.wallet.debits)
            for _ in range(3):
                trip.sync(plugin)
                trip.load(plugin)
            self.assertEqual(len(plugin.wallet.debits), charged, "重复 sync/load 不能重扣")
        spent = _raw(plugin)["spent"]
        self.assertEqual(spent, sum(row["amount"] for row in plugin.wallet.debits))

    def test_load_alone_settles_past_blocks(self):
        plugin = FakePlugin()
        _traveling(plugin)
        _put_rows(plugin, DAY, [
            _row("12:00", "13:00", "吃当地小吃", "餐厅", "restaurant", "meal",
                 "daily", 4000, "food"),
        ])
        with mock.patch.object(trip, "_now_dt", return_value=_at(DAY, 23, 0)):
            loaded = trip.load(plugin)
        self.assertEqual(loaded["phase"], "traveling")
        self.assertEqual(len(plugin.wallet.debits), 1, plugin.wallet.debits)
        self.assertEqual(_raw(plugin)["spent"], trip.price(plugin, "food", 4000))

    def test_spent_matches_wallet_ledger(self):
        plugin = FakePlugin()
        info = _traveling(plugin)
        _put_rows(plugin, DAY, [
            _row("08:00", "09:00", "睡到自然醒", "酒店房间", "hotel", "rest",
                 "hotel", 70000, "hotel", info["id"]),
            _row("10:00", "11:00", "打卡当地景点", "景区", "scenic", "play",
                 "ticket", 20000, "ticket", info["id"]),
            _row("12:00", "13:00", "吃当地小吃", "餐厅", "restaurant", "meal",
                 "meal", 30000, "feast", info["id"]),
            _row("16:00", "17:00", "逛夜市", "商场", "mall", "play",
                 "daily", 5000, "daily", info["id"]),
        ])
        trip.settle_due(plugin, now=_at(DAY, 23, 0))
        ledger_total = -sum(entry["delta"] for entry in plugin.wallet.ledger(50))
        self.assertGreater(ledger_total, 0)
        self.assertEqual(_raw(plugin)["spent"], ledger_total)
        self.assertEqual(plugin.wallet.cents, 500000 - ledger_total)

    def test_disabled_trip_never_settles(self):
        plugin = FakePlugin(config={"travel_enabled": False})
        _traveling(plugin)
        _put_rows(plugin, DAY, [
            _row("08:00", "09:00", "睡到自然醒", "酒店房间", "hotel", "rest",
                 "hotel", 60000, "hotel"),
        ])
        summary = trip.settle_due(plugin, now=_at(DAY, 23, 0))
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["reason"], "disabled")
        self.assertEqual(plugin.wallet.debits, [])
        self.assertEqual(plugin.wallet.cents, 500000)

    def test_non_traveling_phase_never_settles(self):
        for phase in ("planning", "approved", "rest", "done"):
            plugin = FakePlugin()
            _traveling(plugin, phase=phase)
            _put_rows(plugin, DAY, [
                _row("08:00", "09:00", "睡到自然醒", "酒店房间", "hotel", "rest",
                     "hotel", 60000, "hotel"),
            ])
            summary = trip.settle_due(plugin, now=_at(DAY, 23, 0))
            self.assertEqual(summary["reason"], "not_traveling", phase)
            self.assertEqual(plugin.wallet.debits, [], phase)

    def test_ledger_reasons_are_human_text(self):
        plugin = FakePlugin()
        info = _traveling(plugin)
        _put_rows(plugin, DAY, [
            _row("08:00", "09:00", "睡到自然醒", "酒店房间", "hotel", "rest",
                 "hotel", 60000, "hotel", info["id"]),
            _row("10:00", "11:00", "打卡当地景点", "景区", "scenic", "play",
                 "ticket", 15000, "ticket", info["id"]),
            _row("12:00", "13:00", "吃当地小吃", "餐厅", "restaurant", "meal",
                 "meal", 25000, "feast", info["id"]),
            _row("16:00", "17:00", "打车被绕路", "路上", "road", "commute",
                 "daily", 3000, "transport", info["id"]),
        ])
        trip.settle_due(plugin, now=_at(DAY, 23, 0))
        reasons = [row["reason"] for row in plugin.wallet.debits]
        self.assertIn("三亚住宿（第1晚）", reasons)
        self.assertIn("海鲜大餐", reasons)
        self.assertIn("景区门票", reasons)
        self.assertIn("打车", reasons)
        for reason in reasons:
            self.assertTrue(reason)
            for word in ("trip", "day1", "idem", "trip_pool", "settle", "sync"):
                self.assertNotIn(word, reason, reason)

    def test_yesterday_blocks_still_settle_after_midnight(self):
        plugin = FakePlugin()
        _traveling(plugin, start=_day(-1), days=3)
        _put_rows(plugin, _day(-1), [
            _row("20:00", "21:00", "逛夜市", "商场", "mall", "play",
                 "daily", 5000, "daily"),
        ])
        summary = trip.settle_due(plugin, now=_at(DAY, 0, 30))
        self.assertTrue(summary["ok"], summary)
        self.assertEqual(len(plugin.wallet.debits), 1, summary)
        self.assertTrue(plugin.wallet.debits[0]["idem"].endswith("day1:daily:20"))


class MidnightLegTests(unittest.TestCase):
    def _start_2300(self, plugin):
        return trip.trip_manual_start(
            plugin, city="三亚", days=3, force=True, now_day=DAY,
            now=_at(DAY, 23, 0))

    def test_outbound_23_to_03_splits_across_two_days(self):
        plugin = FakePlugin()
        with _patch_legs():
            started = self._start_2300(plugin)
        self.assertTrue(started["ok"], started)
        raw = _raw(plugin)
        self.assertEqual(raw["phase"], "outbound")
        self.assertEqual(raw["outbound_arrive_day"], _day(1))
        arrive = dt.datetime.fromtimestamp(int(raw["outbound_ts"]))
        self.assertEqual((arrive.date().isoformat(), arrive.hour, arrive.minute),
                         (_day(1), 3, 0))
        day1 = _legs(plugin, DAY)
        day2 = _legs(plugin, _day(1))
        self.assertEqual([row["start"] for row in day1], ["23:00", "23:30"])
        self.assertEqual([row["end"] for row in day1], ["23:30", "24:00"])
        self.assertEqual(len(day2), 1, day2)
        self.assertEqual((day2[0]["start"], day2[0]["end"]), ("00:00", "03:00"))
        for row in day1 + day2:
            self.assertLess(_minute(row["start"]), 24 * 60, row)
            self.assertLessEqual(_minute(row["end"]), 24 * 60, row)
        self.assertTrue(_no_overlap(day1), day1)
        self.assertTrue(_no_overlap(day2), day2)
        self.assertTrue(_contiguous(day1), day1)
        self.assertTrue(_contiguous(day2), day2)

    def test_straddling_leg_is_cut_at_midnight(self):
        plugin = FakePlugin()
        with _patch_legs(station=(45, 45)):
            started = self._start_2300(plugin)
        self.assertTrue(started["ok"], started)
        day1 = _legs(plugin, DAY)
        day2 = _legs(plugin, _day(1))
        self.assertEqual(day1[-1]["end"], "24:00")
        self.assertEqual(day2[0]["start"], "00:00")
        self.assertEqual(day2[0]["end"], "00:15")
        self.assertEqual(day2[-1]["end"], "03:15")
        for row in day1 + day2:
            self.assertLess(_minute(row["start"]), 24 * 60, row)
            self.assertLessEqual(_minute(row["end"]), 24 * 60, row)
        self.assertTrue(_no_overlap(day1) and _no_overlap(day2))

    def test_arrival_switches_to_traveling_from_arrival_date(self):
        plugin = FakePlugin()
        with _patch_legs():
            self._start_2300(plugin)
        arrive = dt.datetime.fromtimestamp(int(_raw(plugin)["outbound_ts"]))
        before = trip.sync(plugin, now=arrive - dt.timedelta(minutes=5))
        self.assertEqual(before["phase"], "outbound", "没到点不许提前切 traveling")
        updated = trip.sync(plugin, now=arrive + dt.timedelta(minutes=1))
        self.assertEqual(updated["phase"], "traveling")
        self.assertEqual(updated["day_no"], 1)
        self.assertEqual(updated["start_day"], _day(1))
        self.assertEqual(updated["return_day"], _day(3))
        self.assertEqual(trip.day_kind(plugin, _day(1)), "traveling")
        self.assertEqual(trip.day_kind(plugin, DAY), "")

    def test_midnight_return_splits_and_rest_day_is_arrival_day(self):
        plugin = FakePlugin()
        info = _traveling(plugin, start=_day(-1), days=3)
        with _patch_legs():
            returned = trip.begin_return(plugin, info, now=_at(DAY, 23, 0))
        self.assertEqual(returned["phase"], "returning")
        self.assertEqual(returned["return_day"], DAY)
        self.assertEqual(returned["return_arrive_day"], _day(1))
        day1 = _legs(plugin, DAY, "return")
        day2 = _legs(plugin, _day(1), "return")
        self.assertEqual((day1[0]["start"], day1[-1]["end"]), ("23:00", "24:00"))
        self.assertEqual((day2[0]["start"], day2[-1]["end"]), ("00:00", "03:00"))
        arrive = dt.datetime.fromtimestamp(int(returned["return_ts"]))
        self.assertEqual((arrive.date().isoformat(), arrive.hour), (_day(1), 3))
        before = trip.sync(plugin, now=arrive - dt.timedelta(minutes=5))
        self.assertEqual(before["phase"], "returning")
        finished = trip.sync(plugin, now=arrive + dt.timedelta(minutes=1))
        self.assertEqual(finished["phase"], "rest")
        self.assertEqual(finished["rest_day"], _day(1))
        self.assertEqual(trip.day_kind(plugin, _day(1)), "rest")

    def test_reload_does_not_resplit_midnight_legs(self):
        plugin = FakePlugin()
        with _patch_legs():
            self._start_2300(plugin)
        before1 = copy.deepcopy(plugin.state.schedule[DAY])
        before2 = copy.deepcopy(plugin.state.schedule[_day(1)])
        with mock.patch.object(trip, "_now_dt", return_value=_at(DAY, 23, 30)):
            for _ in range(3):
                trip.sync(plugin)
                trip.load(plugin)
        self.assertEqual(plugin.state.schedule[DAY], before1)
        self.assertEqual(plugin.state.schedule[_day(1)], before2)


def _make_state(plugin):
    state = HumanState.__new__(HumanState)
    state.p = plugin
    state.db = plugin.db
    state._schedule_commit_lock = threading.RLock()
    state._schedule_gen_locks = {}
    state.schedule = {}
    state.schedule_ctx = {}
    state.schedule_profile = {}
    state.mood = {}
    state.scenic_spots = lambda: [{"city": "三亚", "name": "亚龙湾"}]
    plugin.state = state
    return state


class MidnightStateTests(unittest.TestCase):
    def test_arrival_day_keeps_night_legs_and_adds_pool(self):
        plugin = FakePlugin()
        state = _make_state(plugin)
        with _patch_legs():
            started = trip.trip_manual_start(
                plugin, city="三亚", days=3, force=True, now_day=DAY,
                now=_at(DAY, 23, 0))
        self.assertTrue(started["ok"], started)
        day2 = _day(1)
        legs = [row for row in state.schedule.get(day2, [])
                if str(row.get("leg") or "") == "outbound"]
        self.assertTrue(legs, "跨午夜第二程必须落进第二天日程（不是只塞待办）")
        self.assertEqual((legs[0]["start"], legs[-1]["end"]), ("00:00", "03:00"))
        raw = _raw(plugin)
        arrive = dt.datetime.fromtimestamp(int(raw["outbound_ts"]))
        updated = trip.sync(plugin, now=arrive + dt.timedelta(minutes=1))
        self.assertEqual(updated["phase"], "traveling")
        with mock.patch("amo_state_schedule.today_str", return_value=day2):
            rows = state._trip_day_items(day2, "traveling")
        pool = [row for row in rows if str(row.get("source") or "") == "trip_pool"]
        self.assertTrue(3 <= len(pool) <= 5, rows)
        self.assertTrue(any(str(row.get("leg") or "") == "outbound" for row in rows),
                        "旅行日池不能把凌晨的长途腿冲掉")
        self.assertTrue(_no_overlap(rows), rows)
        self.assertEqual(min(_minute(row["start"]) for row in rows), 0)
        self.assertLessEqual(max(_minute(row["end"]) for row in rows), 24 * 60)


class TripDaySettlementIntegrationTests(unittest.TestCase):
    """真 mixin 走一遍：铺旅行日池 → settle_due 逐块扣 → 汇总和钱包流水对齐。"""

    def test_real_state_day_pool_settles_each_past_block_once(self):
        plugin = FakePlugin()
        state = _make_state(plugin)
        info = trip.new_trip("三亚", _day(-1), 3)
        info["phase"] = "traveling"
        trip.save(plugin, info)
        rows = state._trip_day_items(DAY, "traveling")
        pool = [row for row in rows if str(row.get("source") or "") == "trip_pool"]
        self.assertTrue(3 <= len(pool) <= 5, rows)
        expected = 0
        for row in pool:
            cost = row["cost"]
            if cost["kind"] == "daily":
                expected += trip.price(plugin, cost["category"], cost["base_cents"])
            else:
                expected += int(cost["base_cents"])
        summary = trip.settle_due(plugin, now=_at(DAY, 23, 50))
        self.assertTrue(summary["ok"], summary)
        self.assertEqual(len(summary["settled"]), len(pool), summary)
        self.assertEqual(sum(item["amount"] for item in summary["settled"]), expected)
        self.assertEqual(plugin.wallet.cents, 500000 - expected)
        self.assertEqual(_raw(plugin)["spent"], expected)
        self.assertEqual(-sum(entry["delta"] for entry in plugin.wallet.ledger(50)),
                         expected)
        again = trip.settle_due(plugin, now=_at(DAY, 23, 55))
        self.assertEqual(again["settled"], [], "同一批块不许重扣")
        self.assertEqual(again["no_money"], [])
        self.assertEqual(plugin.wallet.cents, 500000 - expected)


if __name__ == "__main__":
    unittest.main()
