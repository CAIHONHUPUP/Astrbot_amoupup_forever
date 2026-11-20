# -*- coding: utf-8 -*-
"""宵夜自主决定：面板只读查询（默认今天）+ 只留今天/昨天（2026-11-17）。"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_feature_api as fa  # noqa: E402
import amo_food_event as fe  # noqa: E402

TODAY = dt.date(2026, 11, 17)
YESTERDAY = TODAY - dt.timedelta(days=1)
TWO_DAYS_AGO = TODAY - dt.timedelta(days=2)
THREE_DAYS_AGO = TODAY - dt.timedelta(days=3)


class FakeClock:
    """只实现 now()：决定查询/清理都走 amo_feature_api._amo_clock.now()。"""

    def __init__(self, day=TODAY):
        self.day = day

    def now(self):
        return dt.datetime.combine(self.day, dt.time(23, 30))


class FakeDB:
    """kv 字典桩：kv_rows / kv_keys / kv_delete_many 与 amo_db 同形。"""

    def __init__(self, kv=None):
        self.kv = dict(kv or {})
        self.deleted = []

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def kv_rows(self, prefix="", limit=10000):
        rows = []
        for key in sorted(self.kv):
            if not str(key).startswith(str(prefix or "")):
                continue
            rows.append({"key": str(key), "value": self.kv[key], "updated": 0.0})
        return rows[:limit]

    def kv_keys(self, prefix="", limit=200):
        return [row["key"] for row in self.kv_rows(prefix, limit)]

    def kv_delete(self, key):
        if key in self.kv:
            del self.kv[key]
            self.deleted.append(key)
            return True
        return False

    def kv_delete_many(self, keys):
        return sum(1 for key in keys if self.kv_delete(key))


class Api(fa.FeatureApiMixin):
    def __init__(self, db):
        self.db = db


def dkey(day, hour, prefix="late_snack_decision"):
    return "%s:%s:%02d" % (prefix, day, hour)


def payload(eat=True, score=71.5, roll=12, food="烧烤", amount=1500,
            block_id="abc12345deadbeef"):
    return {"eat": eat,
            "decision": {"eat": eat, "score": score, "roll": roll,
                         "factors": {"hunger": 43.0, "mood": 4.0, "cash": 10.0,
                                     "time": 5.0, "health": 0.0},
                         "reason": "hungry"},
            "food": food, "charged": amount, "block_id": block_id,
            "at": 1763380000.0, "seed": "late_snack:%s:22" % TODAY}


class ClockCase(unittest.TestCase):
    def setUp(self):
        self._real_clock = fa._amo_clock
        fa._amo_clock = FakeClock()

    def tearDown(self):
        fa._amo_clock = self._real_clock


class DecisionReadTests(ClockCase):
    def test_newest_first_and_fields(self):
        db = FakeDB({dkey(TODAY, 22): payload(amount=1500),
                     dkey(TODAY, 23): payload(eat=False, score=40.0, roll=88,
                                              food="", amount=0, block_id="")})
        rows = Api(db).late_snack_decisions()
        self.assertEqual([row["hour"] for row in rows], [23, 22])
        newest = rows[0]
        self.assertFalse(newest["eat"])
        self.assertEqual(newest["score"], 40.0)
        self.assertEqual(newest["roll"], 88)
        self.assertEqual(newest["food"], "")
        self.assertEqual(newest["amount_cents"], 0)
        self.assertEqual(newest["block_id"], "")
        eat = rows[1]
        self.assertTrue(eat["eat"])
        self.assertEqual(eat["day"], TODAY.isoformat())
        self.assertEqual(eat["food"], "烧烤")
        self.assertEqual(eat["amount_cents"], 1500)
        self.assertEqual(eat["block_id"], "abc12345deadbeef")
        self.assertEqual(eat["factors"]["hunger"], 43.0)

    def test_default_only_today(self):
        db = FakeDB({dkey(TODAY, 23): payload(), dkey(YESTERDAY, 23): payload()})
        rows = Api(db).late_snack_decisions()
        self.assertEqual([row["day"] for row in rows], [TODAY.isoformat()])

    def test_days_window_can_include_yesterday(self):
        db = FakeDB({dkey(TODAY, 23): payload(), dkey(YESTERDAY, 23): payload()})
        rows = Api(db).late_snack_decisions(days=2)
        self.assertEqual([row["day"] for row in rows],
                         [TODAY.isoformat(), YESTERDAY.isoformat()])

    def test_limit(self):
        db = FakeDB({dkey(TODAY, hour): payload() for hour in (20, 21, 22, 23)})
        self.assertEqual(len(Api(db).late_snack_decisions(limit=2)), 2)

    def test_empty(self):
        self.assertEqual(Api(FakeDB()).late_snack_decisions(), [])

    def test_bad_rows_do_not_crash(self):
        db = FakeDB({dkey(TODAY, 22): None,
                     dkey(TODAY, 23): "not-a-dict",
                     "late_snack_decision:bad": {"eat": True},
                     "other:key": {"eat": True}})
        rows = Api(db).late_snack_decisions()
        self.assertEqual([row["hour"] for row in rows], [23, 22])
        for row in rows:
            self.assertEqual(row["day"], TODAY.isoformat())
            self.assertEqual(row["score"], "")
            self.assertEqual(row["roll"], "")
            self.assertEqual(row["factors"], {})
            self.assertFalse(row["eat"])


class PruneTests(ClockCase):
    def _seed(self):
        return FakeDB({
            dkey(TODAY, 22): payload(),
            dkey(YESTERDAY, 22): payload(),
            dkey(TWO_DAYS_AGO, 22): payload(),
            dkey(THREE_DAYS_AGO, 23): payload(),
            dkey(TODAY, 22, "late_snack"): "eat",
            dkey(THREE_DAYS_AGO, 23, "late_snack"): "skip",
            "late_snack_block:abc12345deadbeef": True,
            "late_snack_push:%s" % THREE_DAYS_AGO: True,
        })

    def test_keeps_today_and_yesterday(self):
        db = self._seed()
        result = Api(db).prune_decisions(2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["deleted"], 3)
        self.assertIn(dkey(TODAY, 22), db.kv)
        self.assertIn(dkey(YESTERDAY, 22), db.kv)
        self.assertIn(dkey(TODAY, 22, "late_snack"), db.kv)
        self.assertNotIn(dkey(TWO_DAYS_AGO, 22), db.kv)
        self.assertNotIn(dkey(THREE_DAYS_AGO, 23), db.kv)
        self.assertNotIn(dkey(THREE_DAYS_AGO, 23, "late_snack"), db.kv)

    def test_default_is_two_days(self):
        db = self._seed()
        result = Api(db).prune_decisions()
        self.assertEqual(result["kept_days"], 2)
        self.assertEqual(result["deleted"], 3)

    def test_does_not_touch_block_or_push_keys(self):
        db = self._seed()
        Api(db).prune_decisions(2)
        self.assertIn("late_snack_block:abc12345deadbeef", db.kv)
        self.assertIn("late_snack_push:%s" % THREE_DAYS_AGO, db.kv)

    def test_repeat_is_idempotent(self):
        db = self._seed()
        api = Api(db)
        first = api.prune_decisions(2)
        second = api.prune_decisions(2)
        self.assertEqual(first["deleted"], 3)
        self.assertEqual(second["deleted"], 0)
        self.assertEqual(len(db.kv), 5)


class TickPruneTests(ClockCase):
    def test_tick_prunes_only_once_a_day(self):
        db = FakeDB({dkey(THREE_DAYS_AGO, 23): payload()})
        api = Api(db)
        real = fe.late_snack_tick
        calls = []

        async def fake_entry(plugin, now=None, roll=None, say=None):
            calls.append(plugin)
            return {"ok": True}

        fe.late_snack_tick = fake_entry
        try:
            asyncio.run(api.late_snack_tick())
            self.assertNotIn(dkey(THREE_DAYS_AGO, 23), db.kv, "首次 tick 要清掉过期决定")
            db.kv[dkey(THREE_DAYS_AGO, 22)] = payload()
            asyncio.run(api.late_snack_tick())
            self.assertIn(dkey(THREE_DAYS_AGO, 22), db.kv, "同一天不重复清理")
        finally:
            fe.late_snack_tick = real
        self.assertEqual(len(calls), 2)


class CopiedSurfaceTests(ClockCase):
    def test_tick_works_when_only_public_attrs_are_copied(self):
        """main.py 只把 FeatureApiMixin 的公开方法搬到插件类上，tick 不能私有依赖。"""
        exposed = [name for name in dir(fa.FeatureApiMixin) if not name.startswith("_")]
        for name in ("late_snack_tick", "late_snack_decisions", "prune_decisions"):
            self.assertIn(name, exposed)
        db = FakeDB({dkey(THREE_DAYS_AGO, 23): payload()})
        # 和 main.py 一样：把公开方法 setattr 到「类」上（setattr 到实例不会绑 self）
        attrs = {name: getattr(fa.FeatureApiMixin, name) for name in exposed}
        attrs["db"] = db
        plugin = type("Copied", (), attrs)()
        real = fe.late_snack_tick

        async def fake_entry(_plugin, now=None, roll=None, say=None):
            return {"ok": True}

        fe.late_snack_tick = fake_entry
        try:
            asyncio.run(plugin.late_snack_tick())
        finally:
            fe.late_snack_tick = real
        self.assertNotIn(dkey(THREE_DAYS_AGO, 23), db.kv, "复制到插件类上也要能清理")


if __name__ == "__main__":
    unittest.main()
