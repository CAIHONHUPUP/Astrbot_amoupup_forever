# -*- coding: utf-8 -*-
"""宵夜领域入口：自主决定 / 日程块 / 扣款 / 主动 / 生病门 / 去重（2026-11-17）。"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_food_cost as fc  # noqa: E402
import amo_food_event as fe  # noqa: E402
import amo_timeline  # noqa: E402
import micro_events as me  # noqa: E402
from amo_wallet import Wallet  # noqa: E402
from plugin_fakes import WalletFakeDB  # noqa: E402

DAY = "2026-11-17"
REST = {"start": "22:00", "end": "23:00", "activity": "休息", "place": "家",
        "zone": "home", "category": "sleep"}
HOTEL_REST = {"start": "21:00", "end": "24:00", "activity": "在酒店休息",
              "place": "内蒙古自治区·呼和浩特·酒店", "zone": "hotel", "category": "rest"}
COMMUTE = {"start": "21:40", "end": "22:30", "activity": "坐地铁回家", "place": "路上",
           "zone": "road", "category": "commute", "source": "transit_block"}
PLAYGROUND = {"start": "21:00", "end": "24:00", "activity": "在园里看夜场巡游",
              "place": "东莞·游乐园", "zone": "playground", "category": "play"}


def at(hour, minute):
    return dt.datetime(2026, 11, 17, hour, minute, 0)


class FakeDB:
    def __init__(self):
        self.kv = {}
        self.diaries = []

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def save_diary(self, day, hour, text):
        self.diaries.append((day, hour, text))
        self.kv["diary_written:" + day] = "/fake/%s.md" % day

    def get_schedule(self, day):
        return []


class FakeState:
    def __init__(self, rows=None, sick=False, fail_block=False):
        self.schedule = {DAY: [dict(row) for row in (rows or [])]}
        self.db = None
        self.health = {"status": "健康", "sick": bool(sick)}
        self.fail_block = fail_block

    def health_info(self):
        return dict(self.health)

    def mood_score(self):
        return 55.0

    def commit_schedule_change(self, day, new_items, old_items=None, source="", reason=""):
        if self.fail_block:
            return {"changed": True, "saved": False, "version": 1}
        self.schedule[day] = [dict(row) for row in new_items]
        return {"changed": True, "saved": True, "version": 1}


class FakePlugin:
    def __init__(self, cash=50000, rows=None, sick=False, fail_block=False, active=True):
        self.db = FakeDB()
        self.wallet = Wallet(WalletFakeDB())
        self.wallet.ensure_schema()
        self.wallet.credit(cash, "测试初始化", "test:init")
        self.state = FakeState(rows, sick, fail_block)
        self.state.db = self.db
        self.active = active
        self.hunger = 15.0
        self.pushed = []
        self.stale = []
        self.snacks = []

    def late_night_state(self):
        return {"active": self.active}

    def get_hunger(self):
        return self.hunger

    def add_hunger(self, target, amount):
        self.hunger += amount

    def note_late_snack(self, amount, food=""):
        self.snacks.append((amount, food))

    async def push_to_owner(self, text):
        self.pushed.append(str(text))
        return True

    def _mark_daily_diary_stale(self, day, version=0):
        self.stale.append(day)


def run(plugin, now, roll=0, say=None):
    return asyncio.run(fe.late_snack_tick(plugin, now=now, roll=roll, say=say))


def spend_rows(plugin):
    return [row for row in plugin.wallet.ledger(100) if int(row.get("delta_cents") or 0) < 0]


class SplitAndChargeTests(unittest.TestCase):
    def test_rest_block_splits_and_charges_once(self):
        plugin = FakePlugin(rows=[REST])
        result = run(plugin, at(22, 10), roll=0)
        self.assertTrue(result.get("eat"), result)
        self.assertGreater(result.get("charged"), 0)
        rows = plugin.state.schedule[DAY]
        self.assertEqual([(r["start"], r["end"]) for r in rows],
                         [("22:00", "22:10"), ("22:10", "22:30"), ("22:30", "23:00")])
        food_block = rows[1]
        self.assertIn(result["food"], food_block["activity"])
        self.assertIn("宵夜", food_block["activity"])
        self.assertIn("来源 late_snack", food_block["detail"])
        self.assertEqual(result["block_id"], amo_timeline.block_id(DAY, food_block))
        spends = spend_rows(plugin)
        self.assertEqual(len(spends), 1)
        self.assertEqual(-int(spends[0]["delta_cents"]), result["charged"])
        self.assertEqual(spends[0]["item_name"], result["food"])
        self.assertEqual(spends[0]["ref"], "block:%s 22:10-22:30" % result["block_id"])
        self.assertEqual(plugin.stale, [], "B 方案：宵夜后不标 stale、不强刷当天大日记")
        self.assertTrue(any(result["food"] in text for _d, _h, text in plugin.db.diaries))

    def test_not_eat_leaves_money_and_schedule_alone(self):
        plugin = FakePlugin(rows=[REST])
        plugin.hunger = 55.0
        result = run(plugin, at(22, 10), roll=99)
        self.assertFalse(result.get("eat"), result)
        self.assertEqual(result.get("skipped"), "not_hungry")
        self.assertEqual(plugin.state.schedule[DAY], [REST])
        self.assertEqual(spend_rows(plugin), [])
        self.assertEqual(plugin.pushed, [])
        self.assertEqual(plugin.db.diaries, [])
        audit = plugin.db.kv.get("late_snack_decision:%s:22" % DAY)
        self.assertIsNotNone(audit, "决定必须写 kv 可审计")
        self.assertFalse(audit["eat"])

    def test_second_tick_same_hour_does_not_double_charge(self):
        plugin = FakePlugin(rows=[REST])
        first = run(plugin, at(22, 10), roll=0, say=True)
        second = run(plugin, at(22, 20), roll=0, say=True)
        self.assertTrue(first.get("charged"))
        self.assertEqual(second.get("skipped"), "already_decided")
        self.assertEqual(len(spend_rows(plugin)), 1)
        self.assertEqual(len(plugin.pushed), 1)


class HealthGateTests(unittest.TestCase):
    def test_sick_skips_late_snack_everything(self):
        plugin = FakePlugin(rows=[REST], sick=True)
        result = run(plugin, at(22, 10), roll=0)
        self.assertFalse(result.get("eat"), result)
        self.assertEqual(result.get("skipped"), "sick")
        self.assertEqual(plugin.state.schedule[DAY], [REST], "生病不许补宵夜块")
        self.assertEqual(spend_rows(plugin), [])
        self.assertEqual(plugin.pushed, [], "生病 22-24 也不主动说想吃")
        self.assertTrue(any("温水" in text for _d, _h, text in plugin.db.diaries),
                        "可以有无消费的喝温水叙述")

    def test_recovered_allows_snack(self):
        plugin = FakePlugin(rows=[REST], sick=False)
        result = run(plugin, at(22, 10), roll=0)
        self.assertTrue(result.get("charged"), result)

    def test_sick_in_quiet_hours_also_skips(self):
        plugin = FakePlugin(rows=[{"start": "00:00", "end": "06:00", "activity": "睡觉",
                                   "place": "家", "zone": "home", "category": "sleep"}],
                            sick=True)
        result = run(plugin, at(1, 10), roll=0)
        # 2026-11-18：0-6 点不再生成宵夜决策（睡着的人不做花钱决定）
        self.assertEqual(result.get("skipped"), "outside_window")
        self.assertEqual(plugin.pushed, [])


class SickLateSnackTests(unittest.TestCase):
    def _pay(self, plugin, event, when):
        return asyncio.run(me.MicroEventEngine(plugin)._pay(event, when))

    def test_fever_colds_stomach_headache_all_skip_late_snack(self):
        for status in ("发烧", "感冒", "肠胃炎", "头痛"):
            plugin = FakePlugin(sick=True)
            plugin.state.health["status"] = status
            paid = self._pay(plugin, {"key": "late_snack", "cost": 2800, "food": "烧烤",
                                      "text": "点了份烧烤当夜宵"}, at(23, 10))
            self.assertFalse(paid, status + " 期间不许宵夜")
            self.assertEqual(spend_rows(plugin), [], status)
            result = run(plugin, at(23, 10), roll=0)
            self.assertEqual(result.get("skipped"), "sick", status)
            self.assertEqual(plugin.pushed, [], status)
            self.assertEqual(plugin.state.schedule[DAY], [], status)

    def test_sick_can_still_order_daytime_takeout(self):
        plugin = FakePlugin(sick=True)
        paid = self._pay(plugin, {"key": "takeout", "cost": 2500, "food": "外卖",
                                  "text": "点了个外卖"}, at(17, 30))
        self.assertTrue(paid, "生病也可以点白天外卖（不想做饭）")
        self.assertEqual(len(spend_rows(plugin)), 1)

    def test_home_cook_allowed_when_sick(self):
        plugin = FakePlugin(sick=True)
        paid = self._pay(plugin, {"key": "home_cook", "cost": 800, "food": "粥",
                                  "text": "自己煮了粥"}, at(17, 30))
        self.assertTrue(paid, "在家清淡煮食可以")

    def test_recovered_allows_both(self):
        plugin = FakePlugin(sick=False)
        self.assertTrue(self._pay(plugin, {"key": "takeout", "cost": 2500, "food": "外卖",
                                           "text": "点了个外卖"}, at(17, 30)))
        result = run(plugin, at(23, 10), roll=0)
        self.assertTrue(result.get("charged"), result)

    def test_pay_food_night_rejects_sick_but_allows_porridge(self):
        plugin = FakePlugin(sick=True)
        blocked = fc.pay_food(plugin, "烧烤", "烧烤", source="late", now=at(23, 10))
        self.assertEqual(blocked["reason"], "sick")
        self.assertEqual(spend_rows(plugin), [])
        allowed = fc.pay_food(plugin, "粥", "粥", source="late", now=at(23, 10))
        self.assertTrue(allowed.get("ok"), allowed)

    def test_removing_sick_gate_would_charge_late_snack(self):
        plugin = FakePlugin(sick=True)
        with mock.patch.object(fc, "sick_status", return_value=False):
            paid = self._pay(plugin, {"key": "late_snack", "cost": 2800, "food": "烧烤",
                                      "text": "点了份烧烤当夜宵"}, at(22, 30))
        self.assertTrue(paid, "去掉健康门就会扣款——反向证明门在生效")


class AutonomyTests(unittest.TestCase):
    def test_hungry_can_still_choose_not_to_eat(self):
        plugin = FakePlugin(rows=[REST])
        plugin.hunger = 5.0
        result = run(plugin, at(22, 10), roll=99)
        self.assertFalse(result.get("eat"), result)
        self.assertEqual(spend_rows(plugin), [])
        self.assertEqual(plugin.state.schedule[DAY], [REST])
        self.assertEqual(plugin.pushed, [])

    def test_not_hungry_can_crave(self):
        plugin = FakePlugin(rows=[REST])
        plugin.hunger = 55.0
        plugin.state.mood_score = lambda: 30.0
        result = run(plugin, at(23, 10), roll=0)
        self.assertTrue(result.get("eat"), result)
        self.assertGreater(result.get("charged") or 0, 0)

    def test_results_vary_with_seed_not_forced(self):
        outcomes = [run(FakePlugin(rows=[dict(REST)]), at(22, 10), roll=roll).get("eat")
                    for roll in (0, 90, 0, 90)]
        self.assertIn(True, outcomes, "想吃的时候可以吃")
        self.assertIn(False, outcomes, "不想吃的时候可以不吃")

    def test_proactive_not_automatic(self):
        plugin = FakePlugin(rows=[REST])
        plugin.hunger = 55.0
        result = run(plugin, at(22, 10), roll=99)
        self.assertFalse(result.get("eat"))
        self.assertEqual(plugin.pushed, [], "不打算吃就不主动说")

    def test_eat_but_choose_not_to_say(self):
        plugin = FakePlugin(rows=[REST])
        result = run(plugin, at(22, 10), roll=0, say=False)
        self.assertTrue(result.get("eat"), result)
        self.assertTrue(result.get("charged"))
        self.assertEqual(plugin.pushed, [], "想吃也可以选择不主动说")

    def test_removing_health_gate_would_allow_entry_snack(self):
        plugin = FakePlugin(rows=[REST], sick=True)
        # 2026-11-22：健康门收口到 amo_food_cost（pay_food 统一查 sick_status），
        # 只去掉宵夜领域入口的 _health 不再能绕开扣费门。
        with mock.patch.object(fe, "_health", return_value={"status": "健康", "sick": False}), \
                mock.patch.object(fc, "sick_status", return_value=False):
            result = run(plugin, at(22, 10), roll=0)
        self.assertTrue(result.get("charged"), "两道健康门都去掉就会吃——反向证明门在生效")


class FailureAndWindowTests(unittest.TestCase):
    def test_no_money_skips(self):
        plugin = FakePlugin(cash=1000, rows=[REST])
        result = run(plugin, at(22, 10), roll=0)
        self.assertEqual(result.get("skipped"), "no_money")
        self.assertEqual(spend_rows(plugin), [])
        self.assertEqual(plugin.state.schedule[DAY], [REST])

    def test_no_block_skips_without_charge(self):
        plugin = FakePlugin(rows=[REST], fail_block=True)
        result = run(plugin, at(22, 10), roll=0)
        self.assertEqual(result.get("skipped"), "no_block")
        self.assertEqual(spend_rows(plugin), [])

    def test_quiet_hours_eat_without_push(self):
        plugin = FakePlugin(rows=[{"start": "00:00", "end": "06:00", "activity": "睡觉",
                                   "place": "家", "zone": "home", "category": "sleep"}])
        result = run(plugin, at(1, 10), roll=0)
        # 2026-11-18：睡觉时间不生成宵夜决定、不落块、不扣钱
        self.assertEqual(result.get("skipped"), "outside_window")
        self.assertFalse(result.get("charged"), result)
        self.assertEqual(plugin.pushed, [])
        self.assertEqual(len(plugin.state.schedule[DAY]), 1)

    def test_inactive_skips(self):
        plugin = FakePlugin(rows=[REST], active=False)
        result = run(plugin, at(22, 10), roll=0)
        self.assertEqual(result.get("skipped"), "inactive")




class SnackProbabilityTests(unittest.TestCase):
    """2026-11-17 用户口径：吃饱 20%、饿 40%、很饿+低落 60%。"""

    def _score(self, hungry, mood, cash=50000, hour=22, sick=False):
        return fe.decide(dt.datetime(2026, 11, 17, hour, 30), hungry, mood, cash, 0, sick)["score"]

    def test_full_belly_is_20(self):
        self.assertEqual(20.0, self._score(80, 55))

    def test_hungry_is_40(self):
        self.assertEqual(40.0, self._score(40, 55))

    def test_starving_with_low_mood_is_60(self):
        self.assertEqual(60.0, self._score(20, 30))

    def test_quiet_hours_penalty(self):
        self.assertEqual(10.0, self._score(80, 55, hour=1))

    def test_sick_never_eats(self):
        out = fe.decide(dt.datetime(2026, 11, 17, 22, 30), 20, 30, 50000, 0, True)
        self.assertFalse(out["eat"])
        self.assertEqual(0.0, out["score"])


class NightOnceTests(unittest.TestCase):
    def test_second_snack_same_night_blocked(self):
        plugin = FakePlugin(rows=[REST])
        first = run(plugin, at(22, 10), roll=0)
        self.assertTrue(first.get("eat"), first)
        second = run(plugin, at(23, 10), roll=0)
        self.assertFalse(second.get("eat"))
        self.assertEqual("already_eaten", second.get("skipped"))
        self.assertEqual(1, len(spend_rows(plugin)), "同一晚只扣一次宵夜")


class SnackPlaceFollowTests(unittest.TestCase):
    """R13：宵夜块的 place/zone 跟随当前地点，不许写死「家」、不许劈开通勤。"""

    def test_snack_at_hotel_keeps_hotel_place(self):
        plugin = FakePlugin(rows=[HOTEL_REST])
        result = run(plugin, at(22, 10), roll=0)
        self.assertTrue(result.get("eat"), result)
        rows = plugin.state.schedule[DAY]
        self.assertEqual([(r["start"], r["end"]) for r in rows],
                         [("21:00", "22:10"), ("22:10", "22:30"), ("22:30", "24:00")])
        snack = rows[1]
        self.assertIn("宵夜", snack["activity"])
        self.assertEqual(snack["place"], HOTEL_REST["place"])
        self.assertEqual(snack["zone"], "hotel")
        self.assertNotEqual(snack["place"], "家")
        self.assertEqual(spend_rows(plugin)[0]["ref"],
                         "block:%s 22:10-22:30" % result["block_id"])

    def test_snack_at_playground_keeps_playground_place(self):
        plugin = FakePlugin(rows=[PLAYGROUND])
        result = run(plugin, at(22, 10), roll=0)
        self.assertTrue(result.get("eat"), result)
        snack = plugin.state.schedule[DAY][1]
        self.assertEqual((snack["place"], snack["zone"]),
                         (PLAYGROUND["place"], "playground"))

    def test_snack_on_road_does_not_split_commute(self):
        plugin = FakePlugin(rows=[COMMUTE])
        result = run(plugin, at(22, 10), roll=0)
        self.assertTrue(result.get("eat"), result)
        rows = plugin.state.schedule[DAY]
        self.assertEqual(len(rows), 1, "通勤块不能被宵夜劈成两截: %r" % (rows,))
        merged = rows[0]
        self.assertEqual((merged["start"], merged["end"]), ("21:40", "22:30"))
        self.assertEqual((merged["place"], merged["zone"]), ("路上", "road"))
        self.assertIn("宵夜", merged["activity"])
        self.assertIn("来源 late_snack", merged["detail"])
        self.assertEqual(spend_rows(plugin)[0]["ref"],
                         "block:%s 21:40-22:30" % result["block_id"])
        self.assertEqual(result["block_id"], amo_timeline.block_id(DAY, merged))

    def test_snack_place_uses_zone_fallback_when_place_empty(self):
        plugin = FakePlugin(rows=[dict(HOTEL_REST, place="")])
        result = run(plugin, at(22, 10), roll=0)
        self.assertTrue(result.get("eat"), result)
        snack = plugin.state.schedule[DAY][1]
        self.assertEqual((snack["place"], snack["zone"]), ("酒店房间", "hotel"))

    def test_snack_at_home_still_home(self):
        plugin = FakePlugin(rows=[REST])
        result = run(plugin, at(22, 10), roll=0)
        self.assertTrue(result.get("eat"), result)
        snack = plugin.state.schedule[DAY][1]
        self.assertEqual((snack["place"], snack["zone"]), ("家", "home"))


class WeeklyCapTests(unittest.TestCase):
    def test_cap_blocks_fourth_snack(self):
        plugin = FakePlugin(rows=[REST])
        plugin.db.kv["late_snack_week"] = {"week": fe._week_key(at(22, 10)), "used": 3}
        out = run(plugin, at(22, 10), roll=0)
        self.assertFalse(out.get("eat"))
        self.assertEqual("weekly_cap", out.get("skipped"))
        self.assertEqual([], spend_rows(plugin))

    def test_eat_increments_week_counter(self):
        plugin = FakePlugin(rows=[REST])
        run(plugin, at(22, 10), roll=0)
        week = plugin.db.kv["late_snack_week"]
        self.assertEqual(1, week["used"])
        self.assertEqual(fe._week_key(at(22, 10)), week["week"])

if __name__ == "__main__":
    unittest.main()
