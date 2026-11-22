# -*- coding: utf-8 -*-
"""餐次实例 P0（2026-11-22）：一天最多四顿、同餐次 45 分钟一笔、扣费路径统一。

线上事实（work/audit/time_facts.md）：
- 11-16 schedule 里 9 个 meal 块（早 3/午 3/晚 3），11-15 有 6 个；
- 11-13~11-22 同日 ≤45 分钟重复餐费 12 对；
- JIT/微型事件/宵夜各自 wallet.debit，各自切块，同一顿能扣 2~3 笔。
"""
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

import amo_food_cost as fc  # noqa: E402
import micro_events as me  # noqa: E402
from amo_wallet import Wallet  # noqa: E402
from plugin_fakes import WalletFakeDB  # noqa: E402

DAY = "2026-11-17"


def at(hour, minute, day=DAY):
    y, m, d = [int(part) for part in day.split("-")]
    return dt.datetime(y, m, d, hour, minute)


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self, rows=None, fail_block=False):
        self.schedule = {DAY: [dict(row) for row in (rows or [])]}
        self.fail_block = fail_block

    def commit_schedule_change(self, day, new_items, old_items=None, source="", reason=""):
        if self.fail_block:
            return {"changed": True, "saved": False}
        self.schedule[day] = [dict(row) for row in new_items]
        return {"changed": True, "saved": True}


class FakePlugin:
    def __init__(self, cash=100000, rows=None, fail_block=False):
        self.db = FakeDB()
        self.wallet = Wallet(WalletFakeDB())
        self.wallet.ensure_schema()
        self.wallet.credit(cash, "测试初始化", "test:init")
        self.state = FakeState(rows, fail_block)

    def cfg_bool(self, name, default=False):
        return default

    def get_hunger(self):
        return 60.0

    def add_hunger(self, target, amount):
        pass


def pay_at(plugin, item, when, **kwargs):
    """按测试时间扣款：钱包流水 ts 也要用同一个假时钟，否则 45 分钟窗口会被真实时钟带偏。"""
    clock = fc._amo_clock
    before_now, before_ts = clock.now, clock.now_ts
    clock.now = lambda: when
    clock.now_ts = lambda: when.timestamp()
    try:
        return fc.pay_food(plugin, item, item, now=when, **kwargs)
    finally:
        clock.now = before_now
        clock.now_ts = before_ts


def spend_rows(plugin):
    return [row for row in plugin.wallet.ledger(100)
            if int(row.get("delta_cents") or 0) < 0]


class SlotTests(unittest.TestCase):
    def test_four_slots_and_midnight_rollover(self):
        self.assertEqual(fc.meal_slot_at(at(7, 0))[:2], (DAY, "breakfast"))
        self.assertEqual(fc.meal_slot_at(at(12, 30))[:2], (DAY, "lunch"))
        self.assertEqual(fc.meal_slot_at(at(18, 0))[:2], (DAY, "dinner"))
        self.assertEqual(fc.meal_slot_at(at(22, 0))[:2], (DAY, "late"))
        # 00:30 算前一天的宵夜，不能变成第二天的第五顿
        self.assertEqual(fc.meal_slot_at(at(1, 30, "2026-11-18"))[:2],
                         (DAY, "late"))
        # 15:00-16:00 是加餐空档，不占四顿
        self.assertEqual(fc.meal_slot_at(at(15, 30))[:2], ("", ""))
        self.assertEqual(fc.meal_instance_id(DAY, "lunch"), "meal:%s:lunch" % DAY)


class SingleChargeTests(unittest.TestCase):
    def test_same_slot_within_45_minutes_is_one_charge(self):
        plugin = FakePlugin()
        first = pay_at(plugin, "豆浆和包子", at(7, 30))
        second = pay_at(plugin, "面条", at(7, 40))
        self.assertTrue(first["charged"], first)
        self.assertEqual(second["reason"], "duplicate_slot")
        rows = spend_rows(plugin)
        self.assertEqual(len(rows), 1, "同一餐次 45 分钟内只能一笔")
        self.assertEqual(rows[0]["event_id"], "meal:%s:breakfast" % DAY)
        self.assertTrue(rows[0]["idem"].startswith("meal:%s:breakfast" % DAY))

    def test_after_45_minutes_is_addon_of_same_instance(self):
        plugin = FakePlugin()
        first = pay_at(plugin, "面条", at(12, 0))
        later = pay_at(plugin, "小蛋糕", at(13, 0))
        self.assertTrue(first["charged"], first)
        self.assertTrue(later["charged"], later)
        rows = spend_rows(plugin)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["event_id"] for row in rows},
                         {"meal:%s:lunch" % DAY}, "加餐仍挂在同一个餐次实例上")
        self.assertEqual(len({row["idem"] for row in rows}), 2, "加餐 idem 带序号")

    def test_four_meals_a_day_max(self):
        plugin = FakePlugin()
        for when, item in ((at(7, 30), "豆浆和包子"), (at(12, 0), "面条"),
                           (at(18, 0), "家常菜"), (at(22, 0), "烧烤")):
            self.assertTrue(pay_at(plugin, item, when)["charged"], when)
        self.assertEqual(len(spend_rows(plugin)), 4)
        self.assertEqual(len(fc.meal_instances_today(plugin, DAY)), 4,
                         "一天最多早/午/晚/宵夜四顿")
        # 宵夜之后再加一份仍是同一顿（超过 45 分钟的加餐），不能变第五顿
        self.assertTrue(pay_at(plugin, "泡面", at(23, 30))["charged"])
        self.assertEqual(len(fc.meal_instances_today(plugin, DAY)), 4)

    def test_drink_does_not_take_a_meal_slot(self):
        plugin = FakePlugin()
        tea = pay_at(plugin, "奶茶", at(14, 30))
        self.assertTrue(tea["charged"], tea)
        self.assertEqual(tea["instance_id"], "")
        self.assertEqual(fc.meal_instances_today(plugin, DAY), {})
        self.assertTrue(pay_at(plugin, "家常菜", at(18, 0))["charged"])


class BlockTests(unittest.TestCase):
    def test_one_instance_reuses_one_block(self):
        plugin = FakePlugin()
        pay_at(plugin, "豆浆和包子", at(7, 30))
        pay_at(plugin, "面条", at(9, 0))
        blocks = [row for row in plugin.state.schedule[DAY]
                  if str(row.get("category")) == "meal"]
        self.assertEqual(len(blocks), 1, "同一个餐次实例只能有一个块: %r" % (blocks,))
        self.assertEqual(blocks[0].get("event_id"), "meal:%s:breakfast" % DAY)

    def test_micro_event_food_goes_through_pay_food(self):
        plugin = FakePlugin(cash=50000)
        engine = me.MicroEventEngine(plugin)
        event = {"key": "takeout", "cost": 2500, "food": "外卖",
                 "text": "点了个外卖"}
        paid = asyncio.run(engine._pay(event, at(12, 30)))
        self.assertTrue(paid)
        rows = spend_rows(plugin)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], "meal:%s:lunch" % DAY,
                         "微型事件餐费必须挂餐次实例，不能自己造 micro: idem")


class CoalesceTests(unittest.TestCase):
    def test_two_meal_blocks_same_slot_merge_into_one(self):
        plugin = FakePlugin(rows=[
            {"start": "12:00", "end": "12:30", "activity": "吃面条", "place": "家",
             "zone": "home", "category": "meal", "event_id": "meal:%s:lunch" % DAY},
            {"start": "12:30", "end": "13:00", "activity": "又吃了点", "place": "家",
             "zone": "home", "category": "meal", "event_id": "old-second-block"},
        ])
        result = fc.coalesce_meal_blocks(plugin, DAY, slot="lunch")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["merged"], 1)
        blocks = plugin.state.schedule[DAY]
        self.assertEqual(len(blocks), 1, "同一餐次只能留一个块: %r" % (blocks,))
        self.assertEqual((blocks[0]["start"], blocks[0]["end"]), ("12:00", "13:00"))
        self.assertEqual(blocks[0]["event_id"], "meal:%s:lunch" % DAY)

    def test_meal_blocks_with_other_block_between_are_not_swallowed(self):
        plugin = FakePlugin(rows=[
            {"start": "07:30", "end": "07:45", "activity": "吃包子", "place": "家",
             "zone": "home", "category": "meal"},
            {"start": "07:45", "end": "09:00", "activity": "上班路上", "place": "路上",
             "zone": "road", "category": "commute"},
            {"start": "09:00", "end": "09:10", "activity": "吃了个蛋", "place": "公司",
             "zone": "office", "category": "meal"},
        ])
        fc.coalesce_meal_blocks(plugin, DAY, slot="breakfast")
        self.assertEqual(len(plugin.state.schedule[DAY]), 3,
                         "中间夹着通勤块时不能硬合并")


class TodoMealCostTests(unittest.TestCase):
    def test_todo_meal_cost_goes_through_pay_food(self):
        from amo_human_state import HumanState

        import clock

        plugin = FakePlugin(cash=50000)
        state = HumanState.__new__(HumanState)
        state.p = plugin
        clock.freeze(dt.datetime(2026, 11, 18, 12, 5))
        try:
            plan, cost, paid = state._todo_plan(
                {"id": 9, "day": "2026-11-18", "start": "12:00",
                 "activity": "和柳如烟吃饭", "place": "餐厅", "category": "social",
                 "cost_cents": 10000})
        finally:
            clock.unfreeze()
        self.assertTrue(paid, plan)
        rows = spend_rows(plugin)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], "meal:2026-11-18:lunch",
                         "社交饭局扣费也必须挂餐次实例")
        self.assertEqual(plan["_todo_id"], 9)


class JitMealCostTests(unittest.TestCase):
    """JIT 餐费必须走 pay_food（event_id=meal 实例），不能造 jit:<day>:<hour>。"""

    def test_jit_meal_cost_uses_meal_instance(self):
        import schedule_engine
        from amo_human_state import HumanState

        class WalletRecorder:
            def __init__(self):
                self.balance_cents = 100000
                self.calls = []

            def balance(self):
                return self.balance_cents

            def debit(self, amount, reason, idem, **kwargs):
                self.calls.append(("debit", amount, idem, kwargs))
                self.balance_cents -= amount
                return True

            def credit(self, amount, reason, idem, ref="", **kwargs):
                self.calls.append(("credit", amount, idem, ref))
                return True

            def ledger(self, limit=200):
                return []

        class DB:
            def __init__(self):
                self.kv = {}

            def kv_get(self, key, default=None):
                return self.kv.get(key, default)

            def kv_set(self, key, value):
                self.kv[key] = value

            def get_schedule(self, day):
                return []

        class Plugin:
            def __init__(self):
                self.wallet = WalletRecorder()

            def get_hunger(self):
                return 60.0

            def cfg_bool(self, key, default=False):
                return default

        state = HumanState.__new__(HumanState)
        state.p = Plugin()
        state.db = DB()
        state.schedule = {}
        state.schedule_ctx = {}
        state.mood = {"word": ""}
        state.late = None
        state.weather = {}
        state.health_info = lambda: {"sick": False}
        state._jit_place_of = lambda plan: plan.get("place") or "家"
        # task-25 的场景钳制不在本测试范围：实例上给一个直通实现。
        state._jit_scene_constrain = lambda plan, day, now: plan
        old = schedule_engine.jit_plan
        schedule_engine.jit_plan = lambda *a, **k: {
            "action": "在公司食堂吃午饭", "place": "公司", "zone": "office",
            "category": "meal", "cost_cents": 1500, "diary": "",
        }
        try:
            plan, cost, paid = state._jit_plan_for_hour(at(12, 5), 12, DAY)
        finally:
            schedule_engine.jit_plan = old
        self.assertTrue(paid)
        self.assertEqual(cost, 1500)
        debits = [call for call in state.p.wallet.calls if call[0] == "debit"]
        self.assertEqual(len(debits), 1, state.p.wallet.calls)
        self.assertTrue(debits[0][2].startswith("meal:%s:lunch" % DAY), debits)
        self.assertEqual(debits[0][3].get("event_id"), "meal:%s:lunch" % DAY)
        self.assertEqual(plan.get("_paid_instance"), "meal:%s:lunch" % DAY)


if __name__ == "__main__":
    unittest.main()
