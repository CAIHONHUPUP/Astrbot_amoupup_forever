# -*- coding: utf-8 -*-
"""事实强绑定单测（2026-11-15）：聊天前的事实注入 + 消费防口嗨校验。

线上事故：日程 12:00「在家做饭」，13:32 微型事件抽到 takeout 真扣 25 元，
聊天里她说「我今天懒得做饭，点了个外卖」——日程和聊天两套饭。
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_fact_binder  # noqa: E402
from amo_fact_binder import (  # noqa: E402
    claim_kinds,
    facts_lines,
    guard,
    ledger_today,
    meal_facts,
)


class FakeDB:
    def __init__(self, kv=None, schedule=None):
        self._kv = dict(kv or {})
        self._schedule = dict(schedule or {})

    def kv_get(self, key, default=None):
        return self._kv.get(key, default)

    def kv_set(self, key, value):
        self._kv[key] = value

    def get_schedule(self, day):
        return list(self._schedule.get(day) or [])


class FakeState:
    def __init__(self, schedule=None, activity=None):
        self.schedule = dict(schedule or {})
        self._activity = activity or {"activity": "在家做饭", "place": "家", "category": "meal"}

    def current_activity_info(self):
        return dict(self._activity), "schedule"


class FakeWallet:
    def __init__(self, rows=None, balance=100000):
        self.rows = list(rows or [])
        self.debits = []
        self._balance = balance

    def ledger(self, limit=50):
        return list(self.rows)

    def balance(self):
        return self._balance

    def debit(self, amount_cents, reason, idem, **kwargs):
        self.debits.append({"amount": amount_cents, "reason": reason, "idem": idem})
        self._balance -= amount_cents
        self.rows.insert(0, {"ts": _dt.datetime.now().timestamp(), "delta_cents": -amount_cents,
                             "reason": reason, "category": ""})
        return True


class FakePlugin:
    def __init__(self, schedule=None, rows=None, flags=None):
        today = _dt.date.today().isoformat()
        self.state = FakeState({today: list(schedule or [])})
        self.db = FakeDB(schedule={today: list(schedule or [])})
        self.wallet = FakeWallet(rows or [])
        self.micro_events = None
        self._flags = dict(flags or {})

    def cfg_bool(self, key, default=False):
        return bool(self._flags.get(key, default))


def _ts(hour, minute=0):
    now = _dt.datetime.now()
    return _dt.datetime.combine(now.date(), _dt.time(hour, minute)).timestamp()


COOK_SCHEDULE = [
    {"start": "12:00", "end": "13:00", "activity": "在家做饭", "place": "家", "category": "rest"},
    {"start": "13:00", "end": "14:00", "activity": "吃完饭后趴着歇一会儿", "place": "客厅沙发", "category": "rest"},
]


class FactsLinesTests(unittest.TestCase):
    def test_cooking_day_says_no_takeout(self):
        plugin = FakePlugin(schedule=COOK_SCHEDULE)
        text = "\n".join(facts_lines(plugin, _dt.datetime.combine(
            _dt.date.today(), _dt.time(13, 32))))
        self.assertIn("【绝对事实】", text)
        self.assertIn("自己做饭", text)
        self.assertIn("绝对没有点外卖", text)
        self.assertIn("【消费红线】", text)

    def test_ledger_is_injected(self):
        rows = [{"ts": _ts(9, 18), "delta_cents": -2000, "reason": "一碗热汤面", "category": ""},
                {"ts": _ts(11, 30), "delta_cents": -1500, "reason": "买了点菜", "category": ""}]
        plugin = FakePlugin(schedule=COOK_SCHEDULE, rows=rows)
        text = "\n".join(facts_lines(plugin, _dt.datetime.combine(
            _dt.date.today(), _dt.time(13, 32))))
        self.assertIn("【今天的账单】", text)
        self.assertIn("一碗热汤面", text)
        self.assertIn("买了点菜", text)

    def test_takeout_day_allows_it(self):
        rows = [{"ts": _ts(13, 32), "delta_cents": -2500,
                 "reason": "今天不想做饭，点了个外卖", "category": ""}]
        plugin = FakePlugin(schedule=[], rows=rows)
        text = "\n".join(facts_lines(plugin, _dt.datetime.combine(
            _dt.date.today(), _dt.time(14, 0))))
        self.assertIn("确实点过一次外卖", text)

    def test_meal_facts_cooked(self):
        plugin = FakePlugin(schedule=COOK_SCHEDULE)
        facts = meal_facts(COOK_SCHEDULE, ledger_today(plugin), _dt.datetime.combine(
            _dt.date.today(), _dt.time(13, 32)))
        self.assertTrue(facts["cooked"])
        self.assertFalse(facts["takeout"])


class ClaimGuardTests(unittest.TestCase):
    def setUp(self):
        # 把「现在」钉到 13:35；账本/饭点的断言都按这个时刻写。
        fixed = _dt.datetime.combine(_dt.date.today(), _dt.time(13, 35))
        self._clock = mock.patch.object(amo_fact_binder, "_now", return_value=fixed)
        self._clock.start()

    def tearDown(self):
        self._clock.stop()

    def test_fake_takeout_is_rewritten(self):
        plugin = FakePlugin(schedule=COOK_SCHEDULE)
        reply = "老公，我今天懒得做饭，点了个外卖 刚吃完，趴着歇一会儿，撑得动不了"
        fixed, changed, reason = guard(reply, plugin)
        self.assertTrue(changed)
        self.assertNotIn("外卖", fixed)
        self.assertTrue("自己" in fixed or "做饭" in fixed)

    def test_real_takeout_is_kept(self):
        rows = [{"ts": _ts(13, 32), "delta_cents": -2500,
                 "reason": "今天不想做饭，点了个外卖", "category": ""}]
        plugin = FakePlugin(schedule=[], rows=rows)
        reply = "老公我今天懒得做饭，点了个外卖。"
        fixed, changed, _ = guard(reply, plugin)
        self.assertFalse(changed)
        self.assertEqual(fixed, reply)

    def test_plain_reply_untouched(self):
        plugin = FakePlugin(schedule=COOK_SCHEDULE)
        reply = "嗯，我慢慢吃，一屋子香味呢。今天不干别的，吃饱了就瘫着。"
        fixed, changed, _ = guard(reply, plugin)
        self.assertFalse(changed)
        self.assertEqual(fixed, reply)

    def test_guard_can_be_turned_off(self):
        plugin = FakePlugin(schedule=COOK_SCHEDULE, flags={"chat_claim_guard": False})
        reply = "我点了个外卖吃得好撑"
        fixed, changed, _ = guard(reply, plugin)
        self.assertFalse(changed)

    def test_charge_mode_debits_first(self):
        # 没有"自己做饭"的证据时，charge 模式先真扣 25 元再让她说
        plugin = FakePlugin(schedule=[], flags={"chat_claim_charge": True})
        reply = "老公我点了个外卖，刚吃完"
        fixed, changed, _ = guard(reply, plugin)
        self.assertFalse(changed)
        self.assertIn("外卖", fixed)
        self.assertEqual(len(plugin.wallet.debits), 1)
        self.assertEqual(plugin.wallet.debits[0]["amount"], 2500)

    def test_charge_mode_never_charges_when_she_cooked(self):
        # 日程说在家做饭时，"点外卖"是编的，不能靠补扣钱把它圆回来
        # 时钟钉到午饭档：meal_band 只在 10-15 / 16-22 认「这一顿」，15:00-15:59 是空档，
        # 不钉的话这条测试一到下午三点就假红（2026-11-15 踩到）。
        import amo_fact_binder
        old_now = amo_fact_binder._now
        amo_fact_binder._now = lambda: _dt.datetime.combine(
            _dt.date.today(), _dt.time(12, 40))
        try:
            plugin = FakePlugin(schedule=COOK_SCHEDULE, flags={"chat_claim_charge": True})
            fixed, changed, _ = guard("老公我点了个外卖，刚吃完", plugin)
        finally:
            amo_fact_binder._now = old_now
        self.assertTrue(changed)
        self.assertEqual(plugin.wallet.debits, [])
        self.assertNotIn("外卖", fixed)

    def test_cooked_evidence_from_ledger(self):
        # 线上 11:30 那笔「其他：自己做饭 × 1」就是她自己下厨的铁证
        rows = [{"ts": _ts(11, 30), "delta_cents": -1500, "reason": "其他：自己做饭 × 1",
                 "category": ""},
                {"ts": _ts(13, 32), "delta_cents": -2500,
                 "reason": "今天不想做饭，点了个外卖", "category": ""}]
        plugin = FakePlugin(schedule=[], rows=rows)
        now = _dt.datetime.combine(_dt.date.today(), _dt.time(13, 32))
        facts = meal_facts([], ledger_today(plugin), now)
        self.assertTrue(facts["cooked"])
        text = "\n".join(facts_lines(plugin, now))
        self.assertIn("自己做饭", text)
        self.assertIn("系统误记", text)
        fixed, changed, _ = guard("老公我点了个外卖，刚吃完", plugin, now)
        self.assertTrue(changed)
        self.assertNotIn("外卖", fixed)

    def test_lunch_cooking_does_not_block_dinner_takeout(self):
        rows = [{"ts": _ts(11, 30), "delta_cents": -1500, "reason": "自己做饭 × 1",
                 "category": ""},
                {"ts": _ts(18, 40), "delta_cents": -3000, "reason": "晚饭点了个外卖",
                 "category": ""}]
        plugin = FakePlugin(schedule=COOK_SCHEDULE, rows=rows)
        now = _dt.datetime.combine(_dt.date.today(), _dt.time(19, 0))
        fixed, changed, _ = guard("晚饭点了个外卖，撑死了", plugin, now)
        self.assertFalse(changed, fixed)

    def test_claim_kinds(self):
        self.assertEqual(claim_kinds("我点了个外卖"), ["takeout"])
        self.assertEqual(claim_kinds("刚喝了一杯奶茶"), [])
        self.assertEqual(claim_kinds("我点了杯奶茶"), ["food"])
        self.assertEqual(claim_kinds("我不太爱吃外卖"), [])
        self.assertEqual(claim_kinds("我在等外卖呢"), ["takeout"])


if __name__ == "__main__":
    unittest.main()
