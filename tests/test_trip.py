# -*- coding: utf-8 -*-
"""第九轮一期：旅游状态机 / 触发矩阵 / 目的地锁 / 消费 / 断粮 / 手动入口。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_trip as trip  # noqa: E402
from fixtures import FixedClock  # noqa: E402

TODAY = "2026-11-16"


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeWallet:
    def __init__(self, balance=8000):
        self._balance = balance
        self.idems = {}
        self.calls = []

    def balance(self):
        return self._balance

    def has_idem(self, idem):
        return idem in self.idems

    def debit(self, cost, reason="", idem="", **kwargs):
        self.calls.append(("debit", cost, reason, idem, kwargs))
        if idem in self.idems or int(cost) > self._balance:
            return False
        self.idems[idem] = -int(cost)
        self._balance -= int(cost)
        return True

    def credit(self, cost, reason="", idem="", **kwargs):
        if idem in self.idems:
            return False
        self.idems[idem] = int(cost)
        self._balance += int(cost)
        return True


class FakeState:
    def __init__(self):
        self.moods = []
        self.spots = [{"city": "杭州", "name": "西湖"}, {"city": "厦门", "name": "鼓浪屿"},
                      {"city": "大理", "name": "洱海"}, {"city": "成都", "name": "宽窄巷子"},
                      {"city": "西安", "name": "城墙"}, {"city": "三亚", "name": "亚龙湾"}]

    def scenic_spots(self):
        return list(self.spots)

    def add_mood(self, delta):
        self.moods.append(delta)


class FakePlugin:
    def __init__(self, balance=8000):
        self.db = FakeDB()
        self.state = FakeState()
        self.wallet = FakeWallet(balance)
        self.conf = {"travel_cooldown_days": 20, "travel_max_per_year": 6,
                     "travel_days_max": 3, "travel_budget_max": 5000,
                     "travel_hotel_min": 300, "travel_food_min": 100,
                     "travel_return_cost": 800, "travel_price_multiplier": 1.6,
                     "travel_enabled": True, "travel_prob_holiday": 0.6,
                     "travel_prob_low": 0.35}

    def cfg_int(self, name, default=0):
        return int(self.conf.get(name, default))

    def cfg_float(self, name, default=0.0):
        return float(self.conf.get(name, default))

    def cfg_bool(self, name, default=False):
        return bool(self.conf.get(name, default))

    def cfg_str(self, name, default=""):
        return str(self.conf.get(name, default))


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


class CandidateTests(unittest.TestCase):
    def test_pool_capped_from_scenic(self):
        plugin = FakePlugin()
        pool = trip.candidates(plugin)
        self.assertEqual(len(pool), 4)
        self.assertEqual(pool[0], "杭州")

    def test_pool_no_city_invented(self):
        plugin = FakePlugin()
        plugin.state.spots = []
        self.assertEqual(trip.candidates(plugin), [])


class DecideTests(unittest.TestCase):
    def test_long_holiday_goes(self):
        plugin = FakePlugin()
        result = trip.decide(plugin, TODAY, None, 8000, long_holiday=True,
                             rng=FixedRng(0.1))
        self.assertTrue(result["go"])
        self.assertIn(result["city"], trip.candidates(plugin))

    def test_low_mood_prob(self):
        plugin = FakePlugin()
        plugin.conf["travel_prob_low"] = 0.35
        self.assertFalse(trip.decide(plugin, TODAY, None, 8000, long_holiday=False,
                                     low_mood=True, rng=FixedRng(0.9))["go"])
        self.assertTrue(trip.decide(plugin, TODAY, None, 8000, long_holiday=False,
                                    low_mood=True, rng=FixedRng(0.1))["go"])

    def test_no_trigger_stops(self):
        plugin = FakePlugin()
        self.assertEqual(trip.decide(plugin, TODAY, None, 8000,
                                     rng=FixedRng(0.0))["reason"], "no_trigger")

    def test_once_per_day(self):
        plugin = FakePlugin()
        plugin.db.kv["trip_decided:" + TODAY] = True
        self.assertEqual(trip.decide(plugin, TODAY, None, 8000, long_holiday=True,
                                     rng=FixedRng(0.0))["reason"], "decided_today")

    def test_cooldown_blocks(self):
        plugin = FakePlugin()
        plugin.db.kv[trip.COOLDOWN_KEY] = "2026-11-10"
        self.assertEqual(trip.decide(plugin, TODAY, None, 8000, long_holiday=True,
                                     rng=FixedRng(0.0))["reason"], "cooldown")

    def test_budget_blocks(self):
        plugin = FakePlugin()
        result = trip.decide(plugin, TODAY, None, 100, long_holiday=True,
                             rng=FixedRng(0.0))
        self.assertEqual(result["reason"], "budget")

    def test_max_per_year(self):
        plugin = FakePlugin()
        plugin.db.kv[trip.COUNT_KEY] = 6
        self.assertEqual(trip.decide(plugin, TODAY, None, 8000, long_holiday=True,
                                     rng=FixedRng(0.0))["reason"], "max_per_year")


class LockTests(unittest.TestCase):
    def _with_phase(self, phase):
        plugin = FakePlugin()
        state = trip.new_trip("杭州", TODAY, 3)
        state["phase"] = phase
        trip.save(plugin, state)
        return plugin

    def test_after_departure_change_blocked(self):
        plugin = self._with_phase("traveling")
        result = trip.guard_change(plugin, "别去杭州了，改去大理吧")
        self.assertEqual(result["action"], "blocked")
        self.assertIn("票都买好了", result["reply"])

    def test_after_departure_return_allowed(self):
        with FixedClock(dt.datetime(2026, 11, 16, 16, 0)):  # 别让真实时钟把阶段推进掉
            plugin = self._with_phase("traveling")
            self.assertEqual(trip.guard_change(plugin, "回来吧")["action"], "return")

    def test_before_departure_change_allowed(self):
        plugin = self._with_phase("approved")
        self.assertEqual(trip.guard_change(plugin, "改去大理吧")["action"], "change")

    def test_outbound_change_blocked(self):
        plugin = self._with_phase("outbound")
        self.assertEqual(trip.guard_change(plugin, "换成成都")["action"], "blocked")


class SpendTests(unittest.TestCase):
    def _trip(self):
        return trip.new_trip("杭州", TODAY, 3)

    def test_multiplier_only_three_kinds(self):
        plugin = FakePlugin()
        self.assertEqual(trip.price(plugin, "food", 100), 160)
        self.assertEqual(trip.price(plugin, "transport", 50), 80)
        self.assertEqual(trip.price(plugin, "daily", 10), 16)
        self.assertEqual(trip.price(plugin, "hotel", 500), 500)
        self.assertEqual(trip.price(plugin, "ticket", 120), 120)

    def test_idem_dedupe(self):
        plugin = FakePlugin()
        t = self._trip()
        first = trip.spend(plugin, t, 100, "晚饭", "trip:1:2026-11-16:food",
                           category="food", balance=5000)
        second = trip.spend(plugin, t, 100, "晚饭", "trip:1:2026-11-16:food",
                            category="food", balance=5000)
        self.assertTrue(first["ok"])
        self.assertEqual(second["reason"], "duplicate")

    def test_balance_floor(self):
        plugin = FakePlugin()
        t = self._trip()
        result = trip.spend(plugin, t, 500, "大餐", "trip:1:x", category="food",
                            balance=100)
        self.assertEqual(result["reason"], "no_money")

    def test_exempt_categories(self):
        plugin = FakePlugin()
        for category in ("salary", "bill", "mortgage", "utility", "fund", "fine"):
            self.assertEqual(
                trip.spend(plugin, self._trip(), 100, "x", "k-" + category,
                           category=category)["reason"], "exempt")

    def test_ranges(self):
        for kind, (low, high) in trip.SPEND_RANGES.items():
            amount = trip.range_amount(kind, FixedRng())
            self.assertTrue(low <= amount <= high, (kind, amount))

    def test_assassin_once(self):
        plugin = FakePlugin()
        t = self._trip()
        hit = trip.assassin(plugin, t, rng=FixedRng(0.0), balance=5000)
        self.assertTrue(hit["hit"])
        self.assertEqual(plugin.state.moods, [-8])
        again = trip.assassin(plugin, hit["trip"], rng=FixedRng(0.0), balance=5000)
        self.assertEqual(again["reason"], "already")

    def test_assassin_prob_miss(self):
        plugin = FakePlugin()
        self.assertFalse(trip.assassin(plugin, self._trip(), rng=FixedRng(0.9))["hit"])


class StrandedTests(unittest.TestCase):
    def test_stranded_fallbacks(self):
        plugin = FakePlugin()
        result = trip.stranded(plugin, 100)
        self.assertTrue(result["stranded"])
        self.assertEqual(result["actions"], ["borrow", "shorten", "wait"])

    def test_not_stranded(self):
        plugin = FakePlugin()
        self.assertFalse(trip.stranded(plugin, 5000)["stranded"])


class MemoryTests(unittest.TestCase):
    def test_memory_payload(self):
        t = trip.new_trip("杭州", TODAY, 3)
        t["spent"] = 258000  # 分：2580 元
        payload = trip.memory_payload(t)
        self.assertEqual(payload["importance"], 0.9)
        self.assertEqual(payload["tag"], "#旅游")
        self.assertIn("杭州玩了3天", payload["content"])
        self.assertIn("2580元", payload["content"])
        self.assertNotIn("258000", payload["content"])

    def test_finish_archives_log(self):
        plugin = FakePlugin()
        t = trip.new_trip("厦门", TODAY, 2)
        finished = trip.finish(plugin, t, t["return_day"])
        self.assertEqual(finished["phase"], "rest")
        self.assertEqual(finished["rest_day"], "2026-11-18")
        self.assertEqual(len(plugin.db.kv[trip.TRAVEL_LOG_KEY]), 1)
        # rest 日过完才 done：重复 finish/sync 不重复归档
        trip.sync(plugin, now=dt.datetime(2026, 11, 19, 9, 0))
        self.assertEqual(trip.load(plugin)["phase"], "done")
        self.assertEqual(len(plugin.db.kv[trip.TRAVEL_LOG_KEY]), 1)


class MigrationTests(unittest.TestCase):
    def test_legacy_migrates_once(self):
        plugin = FakePlugin()
        plugin.db.kv["wander_trip"] = {"city": "厦门", "start_day": TODAY, "days": 2}
        loaded = trip.load(plugin)
        self.assertEqual(loaded["city"], "厦门")
        self.assertEqual(loaded["phase"], "approved")
        self.assertIn("wander_trip", loaded["flags"].get("migrated_from", ""))
        self.assertEqual(trip.load(plugin)["id"], loaded["id"])


class ManualStartTests(unittest.TestCase):
    def test_manual_start_outbound_and_lock(self):
        plugin = FakePlugin()
        result = trip.trip_manual_start(plugin, city="大理", days=2, now_day=TODAY)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["phase"], "outbound")
        self.assertEqual(trip.status(plugin)["city"], "大理")
        self.assertEqual(trip.guard_change(plugin, "改去成都")["action"], "blocked")

    def test_manual_start_budget_rejected(self):
        plugin = FakePlugin(balance=100)
        result = trip.trip_manual_start(plugin, now_day=TODAY)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "budget")

    def test_manual_start_city_outside_pool(self):
        plugin = FakePlugin()
        result = trip.trip_manual_start(plugin, city="贵阳", now_day=TODAY)
        self.assertEqual(result["reason"], "city_not_in_pool")

    def test_manual_start_cooldown_skipped_with_force(self):
        plugin = FakePlugin()
        plugin.db.kv[trip.COOLDOWN_KEY] = TODAY
        self.assertEqual(trip.trip_manual_start(plugin, now_day=TODAY)["reason"],
                         "cooldown")
        self.assertTrue(trip.trip_manual_start(plugin, now_day=TODAY, force=True)["ok"])

    def test_cancel_only_before_departure(self):
        plugin = FakePlugin()
        trip.save(plugin, trip.new_trip("杭州", TODAY, 2))
        self.assertTrue(trip.cancel(plugin)["ok"])
        plugin2 = FakePlugin()
        trip.save(plugin2, trip.trip_manual_start(plugin2, now_day=TODAY)["trip"])
        self.assertEqual(trip.cancel(plugin2)["reason"], "locked")

    def test_status_matches_kv(self):
        plugin = FakePlugin()
        trip.trip_manual_start(plugin, city="厦门", days=3, now_day=TODAY)
        status = trip.status(plugin)
        raw = plugin.db.kv[trip.KEY]
        self.assertEqual(status["phase"], raw["phase"])
        self.assertEqual(status["city"], raw["city"])
        self.assertEqual(status["days"], raw["days"])




class QualityHardeningTests(unittest.TestCase):
    def test_disabled_never_fires(self):
        plugin = FakePlugin()
        plugin.conf["travel_enabled"] = False
        self.assertEqual(trip.decide(plugin, TODAY, None, 8000, long_holiday=True,
                                     rng=FixedRng(0.0))["reason"], "disabled")
        self.assertEqual(trip.load(plugin), {})
        plugin.db.kv[trip.KEY] = {"id": "x", "phase": "traveling", "city": "杭州"}
        self.assertEqual(trip.guard_change(plugin, "改去大理")["action"], "none")
        self.assertFalse(trip.trip_manual_start(plugin, now_day=TODAY)["ok"])

    def test_spend_idem_forced_with_trip_id(self):
        plugin = FakePlugin()
        t = trip.new_trip("杭州", TODAY, 2)
        result = trip.spend(plugin, t, 100, "晚饭", "food", category="food",
                            balance=5000)
        self.assertTrue(result["ok"], result)
        keys = [key for key in plugin.db.kv if key.startswith("trip_spent:")]
        self.assertTrue(keys)
        self.assertIn("trip:%s:" % t["id"], keys[0])

    def test_dedupe_write_failure_rolls_back(self):
        class BadDB(FakeDB):
            def kv_set(self, key, value):
                if key.startswith("trip_spent:"):
                    raise RuntimeError("disk full")
                super().kv_set(key, value)

        plugin = FakePlugin()
        plugin.db = BadDB()
        t = trip.new_trip("杭州", TODAY, 2)
        result = trip.spend(plugin, t, 100, "晚饭", "food", category="food",
                            balance=5000)
        self.assertEqual(result["reason"], "dedupe_write_failed")
        self.assertEqual(trip.load(plugin).get("spent") or 0, 0)

    def test_destination_locked_logged(self):
        import inspect as _inspect
        body = _inspect.getsource(trip.guard_change)
        self.assertIn("[trip] destination_locked", body)


if __name__ == "__main__":
    unittest.main()
