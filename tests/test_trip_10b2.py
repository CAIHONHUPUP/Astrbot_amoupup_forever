# -*- coding: utf-8 -*-
"""第十轮 B2：长途去/返程、天气切换、钱包真扣、断粮、finish/重载。

反向验证：去掉 _start_outbound / begin_return / travel_spend 的真实
wallet.debit / restore_weather / finish 的记忆幂等，对应用例都会红。
"""
from __future__ import annotations

import asyncio
import copy
import datetime as dt
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_shared  # noqa: E402
import amo_trip as trip  # noqa: E402
from fixtures import FixedClock  # noqa: E402

DAY = "2026-11-16"


def _day(offset):
    return (dt.date(2026, 11, 16) + dt.timedelta(days=offset)).isoformat()


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


class FakeDB:
    def __init__(self):
        self.kv = {}
        self.schedules = {}
        self.todos_map = {}
        self.versions = {}
        self.saves = 0
        self.deleted = []

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return [dict(row) for row in (self.schedules.get(day) or [])]

    def save_schedule(self, day, items):
        self.saves += 1
        self.schedules[day] = [dict(row) for row in items]

    def delete_schedule(self, day):
        self.deleted.append(day)
        self.schedules.pop(day, None)
        return True

    def schedule_version(self, day):
        return self.versions.get(day, 0)

    def bump_schedule_version(self, day):
        self.versions[day] = self.versions.get(day, 0) + 1
        return self.versions[day]

    def mark_diaries_stale(self, day, from_hour, version):
        return []

    def todos(self, day, include_done=False):
        return [dict(row) for row in self.todos_map.get(day, [])]

    def clear_todos(self, day):
        self.todos_map[day] = []
        return True

    def add_todo(self, day, item, source=""):
        row = dict(item)
        row.setdefault("id", len(self.todos_map.get(day, [])) + 1)
        self.todos_map.setdefault(day, []).append(row)
        return int(row["id"])

    def close_todo(self, todo_id):
        return True


class FakeWallet:
    def __init__(self, balance=8000):
        self.cents = int(balance)
        self.idems = {}
        self.debits = []
        self.credits = []

    def balance(self):
        return self.cents

    def has_idem(self, idem):
        return str(idem) in self.idems

    def debit(self, amount, reason="", idem="", **kwargs):
        self.debits.append({"amount": int(amount), "reason": reason,
                            "idem": idem, **kwargs})
        if str(idem) in self.idems or self.cents < int(amount):
            return False
        self.idems[str(idem)] = -int(amount)
        self.cents -= int(amount)
        return True

    def credit(self, amount, reason="", idem="", **kwargs):
        self.credits.append({"amount": int(amount), "reason": reason, "idem": idem})
        if str(idem) in self.idems:
            return False
        self.idems[str(idem)] = int(amount)
        self.cents += int(amount)
        return True


class FakeState:
    def __init__(self, plugin):
        self.p = plugin
        self.db = plugin.db
        self.schedule = {}
        self.mood = {}
        self.weather_refreshes = 0

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

    async def refresh_weather(self, force=False):
        self.weather_refreshes += 1
        return {"city": "三亚", "text": "晴"}


class FakePlugin:
    def __init__(self, balance=8000, config=None):
        self.db = FakeDB()
        self.wallet = FakeWallet(balance)
        self.state = FakeState(self)
        self.config = {
            "travel_enabled": True,
            "travel_return_cost": 800,
            "travel_price_multiplier": 1.6,
            "travel_silence_ok": True,
            "quiet_hours": "03:00-03:01",
        }
        if config:
            self.config.update(config)
        self.memory_writes = []

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

    def _memory_uid(self):
        return "owner-1"

    async def _data_memory_write(self, uid, content, kind="fact", tier=1,
                                 scope="private", group_id="", subject="user",
                                 **kwargs):
        row = {"uid": uid, "content": content, "kind": kind, "subject": subject}
        row.update(kwargs)
        self.memory_writes.append(row)
        return True


class FixedRng:
    def __init__(self, value=0.0, pick=0):
        self.value = value
        self.pick = pick

    def random(self):
        return self.value

    def choice(self, pool):
        return pool[self.pick % len(pool)]

    def randint(self, low, high):
        return high


def _trip(plugin, phase="traveling", city="三亚", start_day=DAY, days=3):
    info = trip.new_trip(city, start_day, days)
    info["phase"] = phase
    trip.save(plugin, info)
    return info


def _raw(plugin):
    return dict(plugin.db.kv[trip.KEY])


def _schedule_legs(plugin, day=DAY, kind=None):
    return [dict(row) for row in plugin.state.schedule.get(day, [])
            if row.get("leg") and (kind is None or row.get("leg") == kind)]


class OutboundTests(unittest.TestCase):
    def test_manual_start_writes_contiguous_3_to_5_hour_blocks(self):
        plugin = FakePlugin()
        now = dt.datetime(2026, 11, 16, 10, 0)
        result = trip.trip_manual_start(plugin, city="三亚", days=3, force=True,
                                        now_day=DAY, now=now)
        self.assertTrue(result["ok"], result)
        self.assertEqual(_raw(plugin)["phase"], "outbound")
        rows = plugin.state.schedule[DAY]
        legs = [row for row in rows if row.get("source") == "transit"]
        self.assertEqual(len(legs), 3, rows)
        self.assertEqual([row.get("leg") for row in legs], ["outbound"] * 3)
        self.assertTrue(_no_overlap(rows), rows)
        self.assertIn("收拾行李", legs[0]["activity"])
        self.assertIn(legs[1]["place"], ("车站", "机场"))
        self.assertTrue(legs[2]["activity"] == "在飞机上"
                        or legs[2]["activity"] == "坐高铁去三亚", legs[2])
        self.assertEqual(_minute(legs[0]["end"]), _minute(legs[1]["start"]))
        self.assertEqual(_minute(legs[1]["end"]), _minute(legs[2]["start"]))
        total = _minute(legs[-1]["end"]) - _minute(legs[0]["start"])
        self.assertTrue(180 <= total <= 300, total)
        self.assertEqual(_raw(plugin)["outbound_start"], "10:00")

    def test_arrival_switches_to_traveling_and_pool(self):
        # 真实时钟已经过了行程日，load() 推进阶段会把它推到 done；这里把时钟钉在到达后 1 分钟。
        with FixedClock(dt.datetime(2026, 11, 16, 15, 1)):
            plugin = FakePlugin()
            now = dt.datetime(2026, 11, 16, 10, 0)
            trip.trip_manual_start(plugin, city="三亚", days=3, force=True,
                                   now_day=DAY, now=now)
            arrive = dt.datetime.fromtimestamp(int(_raw(plugin)["outbound_ts"]))
            updated = trip.sync(plugin, now=arrive + dt.timedelta(minutes=1))
            self.assertEqual(updated["phase"], "traveling")
            self.assertEqual(updated["day_no"], 1)
            self.assertEqual(trip.day_kind(plugin, DAY), "traveling")
            plan = trip.travel_event_for_hour(plugin, DAY, 12)
            self.assertIn(plan["action"], [event[0] for event in trip.TRAVEL_EVENT_POOL])

    def test_departure_keeps_weather_prev(self):
        plugin = FakePlugin(config={"weather_city": "101281601",
                                    "weather_city_name": "东莞"})
        trip.trip_manual_start(plugin, city="三亚", days=3, force=True,
                               now_day=DAY,
                               now=dt.datetime(2026, 11, 16, 9, 0))
        prev = _raw(plugin)["weather_prev"]
        self.assertEqual(prev["weather_city"], "101281601")
        self.assertEqual(prev["weather_city_name"], "东莞")


class ReturnTests(unittest.TestCase):
    def test_early_return_writes_blocks_then_rest(self):
        plugin = FakePlugin()
        _trip(plugin, "traveling")
        result = trip.early_return(plugin, now_day=DAY,
                                   now=dt.datetime(2026, 11, 16, 14, 0))
        self.assertTrue(result["ok"], result)
        self.assertEqual(_raw(plugin)["phase"], "returning")
        legs = _schedule_legs(plugin)
        self.assertEqual(len(legs), 3, plugin.state.schedule[DAY])
        self.assertEqual(legs[0]["start"], "14:00")
        total = _minute(legs[-1]["end"]) - _minute(legs[0]["start"])
        self.assertTrue(180 <= total <= 300, total)
        arrive = dt.datetime.fromtimestamp(int(_raw(plugin)["return_ts"]))
        updated = trip.sync(plugin, now=arrive + dt.timedelta(minutes=1))
        self.assertEqual(updated["phase"], "rest")
        self.assertEqual(updated["rest_day"], _day(1))

    def test_days_over_switches_to_returning(self):
        plugin = FakePlugin()
        info = _trip(plugin, "traveling", start_day=_day(-3), days=3)
        self.assertEqual(info["return_day"], _day(-1))
        updated = trip.sync(plugin, now=dt.datetime(2026, 11, 16, 17, 0))
        self.assertEqual(updated["phase"], "returning")
        legs = _schedule_legs(plugin)
        self.assertEqual(len(legs), 3, plugin.state.schedule[DAY])
        arrive = dt.datetime.fromtimestamp(int(_raw(plugin)["return_ts"]))
        finished = trip.sync(plugin, now=arrive + dt.timedelta(minutes=1))
        self.assertEqual(finished["phase"], "rest")


class WeatherTests(unittest.TestCase):
    def test_switch_and_restore(self):
        plugin = FakePlugin(config={"weather_city": "101281601",
                                    "weather_city_name": "东莞"})
        info = _trip(plugin, "outbound")
        switched = trip.switch_weather(plugin, info)
        self.assertTrue(switched["ok"], switched)
        hit = trip._resolve_weather_city("三亚")
        self.assertIsNotNone(hit)
        self.assertEqual(plugin.config["weather_city"], str(hit["id"]))
        self.assertEqual(plugin.config["weather_city_name"], hit["name"])
        saved = _raw(plugin)
        self.assertEqual(saved["weather_prev"]["weather_city_name"], "东莞")
        restored = trip.restore_weather(plugin, saved)
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(plugin.config["weather_city"], "101281601")
        self.assertEqual(plugin.config["weather_city_name"], "东莞")
        self.assertNotIn("weather_prev", _raw(plugin))

    def test_unresolved_city_keeps_original(self):
        plugin = FakePlugin(config={"weather_city": "101281601",
                                    "weather_city_name": "东莞"})
        info = _trip(plugin, "outbound", city="不存在城")
        result = trip.switch_weather(plugin, info)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "city_unresolved")
        self.assertEqual(plugin.config["weather_city"], "101281601")
        self.assertEqual(plugin.config["weather_city_name"], "东莞")
        self.assertFalse(_raw(plugin).get("weather_prev"))

    def test_city_or_empty_is_trip_aware_only_when_traveling(self):
        plugin = FakePlugin(config={"weather_city": "101281601",
                                    "weather_city_name": "东莞"})
        self.assertEqual(amo_shared.city_or_empty(plugin), "东莞")
        _trip(plugin, "traveling", city="三亚")
        self.assertEqual(amo_shared.city_or_empty(plugin), "三亚")
        plugin.config["travel_enabled"] = False
        self.assertEqual(amo_shared.city_or_empty(plugin), "东莞")

    def test_no_trip_behavior_unchanged(self):
        plugin = FakePlugin(config={"schedule_city": "东莞",
                                    "weather_city": "101281601"})
        self.assertEqual(amo_shared.city_or_empty(plugin), "东莞")
        plugin2 = FakePlugin()
        self.assertEqual(amo_shared.city_or_empty(plugin2), "")


class WalletSpendTests(unittest.TestCase):
    def test_debit_order_and_fields(self):
        plugin = FakePlugin()
        info = _trip(plugin)
        seen = {}

        class OrderWallet(FakeWallet):
            def debit(self, amount, reason="", idem="", **kwargs):
                seen["pending"] = plugin.db.kv_get("trip_pending:" + idem, None)
                return super().debit(amount, reason, idem, **kwargs)

        plugin.wallet = OrderWallet()
        result = trip.travel_spend(
            plugin, info, 100, "吃海鲜", "food", category="food",
            sub_category="餐饮", item_name="海鲜大餐", trigger_reason="饿了")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["amount"], 160)
        self.assertEqual(seen["pending"], 160, "debit 前必须先落 pending")
        self.assertEqual(plugin.wallet.cents, 8000 - 160)
        debit = plugin.wallet.debits[0]
        self.assertEqual(debit["category"], "food")
        self.assertEqual(debit["actor"], "bot")
        self.assertEqual(debit["fund_source"], "travel")
        self.assertEqual(debit["item_name"], "海鲜大餐")
        self.assertEqual(debit["trigger_reason"], "饿了")
        self.assertEqual(_raw(plugin)["spent"], 160)
        self.assertTrue(plugin.db.kv_get("trip_spent:" + debit["idem"], False))

    def test_duplicate_uses_has_idem_without_charging(self):
        plugin = FakePlugin()
        info = _trip(plugin)
        first = trip.travel_spend(plugin, info, 100, "晚饭", "food",
                                  category="food")
        second = trip.travel_spend(plugin, first["trip"], 100, "晚饭", "food",
                                   category="food")
        self.assertTrue(first["ok"], first)
        self.assertEqual(second["reason"], "duplicate")
        self.assertEqual(len(plugin.wallet.debits), 1)
        self.assertEqual(_raw(plugin)["spent"], 160)

    def test_no_money_is_retryable_and_never_negative(self):
        plugin = FakePlugin(balance=100)
        info = _trip(plugin)
        low = trip.travel_spend(plugin, info, 500, "大餐", "feast",
                                category="feast")
        self.assertFalse(low["ok"])
        self.assertEqual(low["reason"], "no_money")
        self.assertTrue(low["retryable"])
        self.assertEqual(plugin.wallet.cents, 100)
        self.assertFalse(plugin.db.kv_get("trip_spent:" + low.get("idem", ""), False))
        plugin.wallet.cents = 5000
        retry = trip.travel_spend(plugin, info, 500, "大餐", "feast",
                                  category="feast")
        self.assertTrue(retry["ok"], retry)
        self.assertEqual(plugin.wallet.cents, 5000 - 500)

    def test_premium_only_food_transport_daily(self):
        plugin = FakePlugin()
        info = _trip(plugin)
        cases = (("hotel", 500, 500), ("food", 100, 160),
                 ("transport", 50, 80), ("daily", 10, 16), ("ticket", 120, 120))
        for index, (category, amount, expected) in enumerate(cases):
            result = trip.travel_spend(plugin, info, amount, "x",
                                       "case-%d" % index, category=category)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["amount"], expected, (category, result))

    def test_exempt_categories_never_touch_wallet(self):
        plugin = FakePlugin()
        info = _trip(plugin)
        for category in ("salary", "bill", "mortgage", "utility", "fund", "fine"):
            result = trip.travel_spend(plugin, info, 100, "x", "k-" + category,
                                       category=category)
            self.assertEqual(result["reason"], "exempt")
        self.assertEqual(plugin.wallet.debits, [])
        self.assertEqual(_raw(plugin)["spent"], 0)

    def test_assassin_really_debits_once(self):
        plugin = FakePlugin()
        info = _trip(plugin)
        hit = trip.assassin(plugin, info, rng=FixedRng(0.0), balance=5000)
        self.assertTrue(hit["hit"], hit)
        self.assertEqual(len(plugin.wallet.debits), 1)
        again = trip.assassin(plugin, hit["trip"], rng=FixedRng(0.0), balance=5000)
        self.assertEqual(again["reason"], "already")
        self.assertEqual(len(plugin.wallet.debits), 1)
        self.assertEqual(plugin.state.mood["added"], -8)


class StrandedTests(unittest.TestCase):
    def test_help_line_and_two_actions(self):
        plugin = FakePlugin(balance=100)
        _trip(plugin)
        with mock.patch("amo_shared.quiet_now", return_value=False):
            result = trip.check_stranded(plugin)
        self.assertTrue(result["stranded"])
        self.assertEqual(result["actions"], ["shorten", "wait"])
        self.assertEqual(result["line"], trip.STRANDED_LINE)

    def test_night_quiet_or_silence_suppresses_line(self):
        plugin = FakePlugin(balance=100)
        _trip(plugin)
        with mock.patch("amo_shared.quiet_now", return_value=False):
            plugin.config["travel_silence_ok"] = False
            quiet = trip.check_stranded(plugin)
            self.assertEqual(quiet["line"], "")
            plugin.config["travel_silence_ok"] = True
        with mock.patch("amo_shared.quiet_now", return_value=True):
            night = trip.check_stranded(plugin)
        self.assertEqual(night["line"], "")

    def test_shorten_falls_back_to_returning(self):
        plugin = FakePlugin(balance=100)
        _trip(plugin)
        result = trip.shorten(plugin, now=dt.datetime(2026, 11, 16, 15, 0))
        self.assertTrue(result["ok"], result)
        self.assertEqual(_raw(plugin)["phase"], "returning")
        self.assertTrue(_raw(plugin)["flags"]["shortened"])
        self.assertEqual(len(_schedule_legs(plugin)), 3)

    def test_wait_resumes_when_money_arrives(self):
        plugin = FakePlugin(balance=100)
        _trip(plugin)
        waiting = trip.wait_for_funds(plugin)
        self.assertTrue(waiting["ok"], waiting)
        self.assertTrue(_raw(plugin)["flags"]["waiting_funds"])
        self.assertEqual(_raw(plugin)["phase"], "traveling")
        plugin.wallet.cents = 5000
        updated = trip.sync(plugin, now=dt.datetime(2026, 11, 16, 16, 0))
        self.assertEqual(updated["phase"], "returning")
        self.assertNotIn("waiting_funds", updated["flags"])

    def test_enough_money_does_not_trigger(self):
        plugin = FakePlugin(balance=5000)
        _trip(plugin)
        result = trip.check_stranded(plugin)
        self.assertFalse(result["stranded"])
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["line"], "")


class FinishTests(unittest.TestCase):
    def _returning_plugin(self, balance=8000):
        plugin = FakePlugin(balance=balance)
        info = _trip(plugin, "traveling")
        result = trip.early_return(plugin, now_day=DAY,
                                   now=dt.datetime(2026, 11, 16, 14, 0))
        self.assertTrue(result["ok"], result)
        return plugin

    def test_arrival_writes_rest_day_and_truncates_old_blocks(self):
        plugin = self._returning_plugin()
        rest_day = _day(1)
        old = [{"start": "06:00", "end": "21:00", "activity": "上班",
                "place": "公司", "category": "work"}]
        plugin.state.schedule[rest_day] = copy.deepcopy(old)
        plugin.db.schedules[rest_day] = copy.deepcopy(old)
        arrive = dt.datetime.fromtimestamp(int(_raw(plugin)["return_ts"]))
        updated = trip.sync(plugin, now=arrive + dt.timedelta(minutes=1))
        self.assertEqual(updated["phase"], "rest")
        rows = plugin.state.schedule[rest_day]
        self.assertTrue(_no_overlap(rows), rows)
        rest = [row for row in rows if row.get("activity") == trip.REST_DAY_ACTIVITY]
        self.assertEqual(len(rest), 1, rows)
        self.assertEqual((rest[0]["start"], rest[0]["end"]), ("08:00", "20:00"))
        self.assertIn("06:00", [row.get("start") for row in rows])
        self.assertIn("20:00", [row.get("start") for row in rows])

    def test_memory_and_fact_params(self):
        plugin = FakePlugin()
        info = _trip(plugin, "rest", start_day=_day(-3), days=3)
        info["rest_day"] = _day(1)
        trip.save(plugin, info)
        result = asyncio.run(trip.finalize_async(plugin, info))
        self.assertEqual(result, {"memory": True, "fact": True})
        memory = [row for row in plugin.memory_writes if row["kind"] == "activity"][0]
        self.assertEqual(memory["importance"], 0.9)
        self.assertEqual(memory["tags"], "#旅游")
        self.assertEqual(memory["idem_key"],
                         "trip:%s:memory" % info["id"])
        self.assertEqual(memory["subject"], "bot")
        fact = [row for row in plugin.memory_writes if row["kind"] == "fact"][0]
        self.assertEqual(fact["tags"], "#旅游")
        self.assertEqual(fact["idem_key"], "trip:%s:fact" % info["id"])
        again = asyncio.run(trip.finalize_async(plugin, info))
        self.assertEqual(again, {"memory": True, "fact": True})
        self.assertEqual(len(plugin.memory_writes), 2, "重复 load 不能重复写记忆")

    def test_done_archive_and_travel_log_cap(self):
        plugin = self._returning_plugin()
        arrive = dt.datetime.fromtimestamp(int(_raw(plugin)["return_ts"]))
        trip.sync(plugin, now=arrive + dt.timedelta(minutes=1))
        self.assertEqual(len(plugin.db.kv[trip.TRAVEL_LOG_KEY]), 1)
        done = trip.sync(plugin, now=dt.datetime.fromisoformat(_day(2) + "T09:00"))
        self.assertEqual(done["phase"], "done")
        self.assertEqual(len(plugin.db.kv[trip.TRAVEL_LOG_KEY]), 1)
        trip.sync(plugin, now=dt.datetime(2026, 11, 25, 9, 0))
        self.assertEqual(len(plugin.db.kv[trip.TRAVEL_LOG_KEY]), 1)


class ReloadTests(unittest.TestCase):
    def test_outbound_timeout_becomes_traveling_day_one(self):
        plugin = FakePlugin()
        trip.trip_manual_start(plugin, city="三亚", days=3, force=True,
                               now_day=DAY,
                               now=dt.datetime(2026, 11, 16, 9, 0))
        arrive = dt.datetime.fromtimestamp(int(_raw(plugin)["outbound_ts"]))
        updated = trip.sync(plugin, now=arrive + dt.timedelta(minutes=1))
        self.assertEqual((updated["phase"], updated["day_no"]), ("traveling", 1))

    def test_traveling_past_days_becomes_returning(self):
        plugin = FakePlugin()
        _trip(plugin, "traveling", start_day=_day(-4), days=2)
        updated = trip.sync(plugin, now=dt.datetime(2026, 11, 16, 10, 0))
        self.assertEqual(updated["phase"], "returning")
        self.assertEqual(len(_schedule_legs(plugin)), 3)

    def test_returning_timeout_becomes_rest(self):
        plugin = FakePlugin()
        _trip(plugin, "traveling", start_day=_day(-2), days=3)
        trip.sync(plugin, now=dt.datetime(2026, 11, 16, 17, 0))
        arrive = dt.datetime.fromtimestamp(int(_raw(plugin)["return_ts"]))
        updated = trip.sync(plugin, now=arrive + dt.timedelta(minutes=1))
        self.assertEqual(updated["phase"], "rest")

    def test_rest_day_passed_becomes_done(self):
        plugin = FakePlugin()
        info = _trip(plugin, "rest", start_day=_day(-3), days=3)
        info["rest_day"] = _day(-1)
        trip.save(plugin, info)
        updated = trip.sync(plugin, now=dt.datetime(2026, 11, 16, 9, 0))
        self.assertEqual(updated["phase"], "done")

    def test_repeated_reload_is_idempotent(self):
        # settle_due 走真实时钟算「今天」；不钉住的话，跨过午夜（或 fixture 日期在昨天）
        # 会把 rest 直接判成 done。固定到测试 fixture 的 11-16 20:01。
        frozen = FixedClock(dt.datetime(2026, 11, 16, 20, 1))
        frozen.start()
        self.addCleanup(frozen.stop)
        plugin = FakePlugin()
        _trip(plugin, "traveling", start_day=_day(-2), days=3)
        first = trip.sync(plugin, now=dt.datetime(2026, 11, 16, 17, 0))
        self.assertEqual(first["phase"], "returning")
        saves = plugin.db.saves
        stamp = int(_raw(plugin)["return_ts"])
        trip.sync(plugin, now=dt.datetime.fromtimestamp(stamp + 60))
        saves_after_finish = plugin.db.saves
        again = trip.sync(plugin, now=dt.datetime.fromtimestamp(stamp + 60))
        self.assertEqual(again["phase"], "rest")
        self.assertEqual(plugin.db.saves, saves_after_finish)
        asyncio.run(trip.sync_async(plugin, now=dt.datetime.fromtimestamp(stamp + 60)))
        self.assertEqual(len(plugin.memory_writes), 2)
        asyncio.run(trip.sync_async(plugin, now=dt.datetime.fromtimestamp(stamp + 60)))
        self.assertEqual(len(plugin.memory_writes), 2)
        self.assertGreaterEqual(plugin.db.saves, saves)

    def test_traveling_day_no_advances_by_date(self):
        plugin = FakePlugin()
        _trip(plugin, "traveling", start_day=_day(-2), days=5)
        updated = trip.sync(plugin, now=dt.datetime(2026, 11, 16, 10, 0))
        self.assertEqual(updated["phase"], "traveling")
        self.assertEqual(updated["day_no"], 3)

    def test_load_itself_advances_outbound(self):
        # DAY 固定是 2026-11-16；load 用真实墙钟推进状态机，跨 0 点后 day_no 会变成 2。
        frozen = FixedClock(dt.datetime(2026, 11, 16, 10, 0))
        frozen.start()
        self.addCleanup(frozen.stop)
        plugin = FakePlugin()
        info = _trip(plugin, "outbound", start_day=DAY)
        info["outbound_ts"] = 1
        trip.save(plugin, info)
        loaded = trip.load(plugin)
        self.assertEqual(loaded["phase"], "traveling")
        self.assertEqual(loaded["day_no"], 1)
        self.assertEqual(trip.load(plugin)["phase"], "traveling")

    def test_load_recovers_returning_to_rest_once(self):
        plugin = FakePlugin()
        info = _trip(plugin, "returning", start_day=_day(-3), days=3)
        info["return_ts"] = 1
        info["return_day"] = DAY
        trip.save(plugin, info)
        first = trip.load(plugin)
        self.assertEqual(first["phase"], "rest")
        self.assertEqual(len(plugin.db.kv[trip.TRAVEL_LOG_KEY]), 1)
        second = trip.load(plugin)
        self.assertEqual(second["phase"], "rest")
        self.assertEqual(len(plugin.db.kv[trip.TRAVEL_LOG_KEY]), 1,
                         "重复 load 不能重复归档")


if __name__ == "__main__":
    unittest.main()
