# -*- coding: utf-8 -*-
"""JIT 到点决策：先扣钱、后落库；落库失败要原路退款，别留「钱没了时间轴没记录」。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import schedule_engine  # noqa: E402
from amo_human_state import HumanState  # noqa: E402

DAY = "2026-11-15"
HOUR = 14
NOW = dt.datetime(2026, 11, 15, HOUR, 5)


class FakeWallet:
    def __init__(self, balance=100000):
        self.balance_cents = balance
        self.calls = []

    def balance(self):
        return self.balance_cents

    def debit(self, amount, reason, idem, **kwargs):
        self.calls.append(("debit", amount, idem))
        self.balance_cents -= amount
        return True

    def credit(self, amount, reason, idem, ref="", **kwargs):
        self.calls.append(("credit", amount, idem, ref))
        self.balance_cents += amount
        return True


class FakeDB:
    def __init__(self):
        self.kv = {}
        self.schedules = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return list(self.schedules.get(day) or [])

    def save_schedule(self, day, items):
        self.schedules[day] = list(items)


class FakePlugin:
    def __init__(self, wallet):
        self.wallet = wallet

    def get_hunger(self):
        return 60.0


def build_state(commit, wallet=None):
    state = HumanState.__new__(HumanState)
    state.p = FakePlugin(wallet or FakeWallet())
    state.db = FakeDB()
    state.schedule = {}
    state.schedule_ctx = {}
    state.mood = {"word": "平静"}
    state.late = None
    state._jit_enabled = lambda: True
    state._jit_heartbeat = lambda now: None
    state._finish_open_sleep = lambda now: None
    state._hour_has_plan = lambda day, hour: False
    state._jit_place_of = lambda plan: plan.get("place") or "家"
    state.health_info = lambda: {"sick": False}
    state.commit_schedule_change = commit
    return state


class CommitSavedFlagTests(unittest.TestCase):
    """commit 必须把「落库成没成」告诉调用方，否则 JIT 的回滚分支是死代码。"""

    class RaisingDB(FakeDB):
        def __init__(self):
            super().__init__()
            self.version = 0

        def schedule_version(self, day):
            return self.version

        def bump_schedule_version(self, day):
            self.version += 1
            return self.version

        def mark_diaries_stale(self, day, hour, version):
            return 0

        def save_schedule(self, day, items):
            raise OSError("disk full")

    class PermissivePlugin:
        def __init__(self):
            self.events = None

        def __getattr__(self, name):
            return None

    def test_save_failure_reports_saved_false(self):
        state = HumanState.__new__(HumanState)
        state.p = self.PermissivePlugin()
        state.db = self.RaisingDB()
        state.schedule = {}
        state.schedule_ctx = {}
        item = {"start": "14:00", "end": "15:00", "activity": "去楼下咖啡店",
                "place": "楼下", "zone": "street", "category": "life"}
        result = HumanState._commit_schedule_change_locked(
            state, DAY, [item], old_items=[], source="jit", reason="测试")
        self.assertIsInstance(result, dict)
        self.assertTrue(result.get("changed"))
        self.assertIs(result.get("saved"), False, "落库失败必须 saved=False：%s" % result)


class JitRollbackTests(unittest.TestCase):
    def setUp(self):
        self._old_plan = schedule_engine.jit_plan
        schedule_engine.jit_plan = lambda *a, **k: {
            "action": "去楼下咖啡店", "place": "楼下", "zone": "street",
            "category": "life", "cost_cents": 2500, "diary": "",
        }

    def tearDown(self):
        schedule_engine.jit_plan = self._old_plan

    def run_tick(self, commit):
        state = build_state(commit)
        state.jit_tick(now=NOW)
        return state

    def test_success_keeps_money_spent(self):
        state = self.run_tick(lambda *a, **k: {"changed": True, "saved": True})
        self.assertEqual(state.p.wallet.calls, [("debit", 2500, "jit:%s:%02d" % (DAY, HOUR))])
        self.assertEqual(state.p.wallet.balance_cents, 100000 - 2500)
        self.assertFalse([c for c in state.p.wallet.calls if c[0] == "credit"])

    def test_commit_raises_refunds(self):
        def boom(*args, **kwargs):
            raise OSError("disk full")

        state = self.run_tick(boom)
        credits = [c for c in state.p.wallet.calls if c[0] == "credit"]
        self.assertEqual(len(credits), 1, state.p.wallet.calls)
        self.assertEqual(credits[0][1], 2500)
        self.assertEqual(credits[0][3], "jit:%s:%02d" % (DAY, HOUR),
                         "退款 ref 必须指向原扣款 idem，reversed_idems 才认得出")
        self.assertEqual(state.p.wallet.balance_cents, 100000, "钱要原路回来")

    def test_commit_reports_unsaved_refunds(self):
        state = self.run_tick(lambda *a, **k: {"changed": True, "saved": False})
        credits = [c for c in state.p.wallet.calls if c[0] == "credit"]
        self.assertEqual(len(credits), 1, state.p.wallet.calls)
        self.assertEqual(state.p.wallet.balance_cents, 100000)
        self.assertEqual(state.db.kv.get("current_action", {}).get("action"),
                         "没舍得花钱，先忍一忍", "退款后别再声称她去了咖啡店")

    def test_refund_idem_is_stable(self):
        state1 = self.run_tick(lambda *a, **k: {"changed": True, "saved": False})
        state2 = self.run_tick(lambda *a, **k: {"changed": True, "saved": False})
        idem1 = [c[2] for c in state1.p.wallet.calls if c[0] == "credit"][0]
        idem2 = [c[2] for c in state2.p.wallet.calls if c[0] == "credit"][0]
        self.assertEqual(idem1, idem2, "同一小时重复触发只能退一次（wallet 靠 idem 去重）")

    def test_free_plan_never_refunds(self):
        schedule_engine.jit_plan = lambda *a, **k: {
            "action": "在家发呆", "place": "家", "zone": "home",
            "category": "rest", "cost_cents": 0, "diary": "",
        }
        state = self.run_tick(lambda *a, **k: {"changed": True, "saved": False})
        self.assertEqual(state.p.wallet.calls, [])


class RefundPairingTests(unittest.TestCase):
    """退款流水靠 ref 指回原扣款：对账/事实提取要能把被退的那笔踢出去。"""

    def test_reversed_and_spent(self):
        from amo_fact_binder import reversed_idems, spent_rows

        ledger = [
            {"idem": "jit:2026-11-15:14", "delta": -2500, "ref": ""},
            {"idem": "jit:rollback:2026-11-15:14", "delta": 2500,
             "ref": "jit:2026-11-15:14"},
        ]
        self.assertEqual(reversed_idems(ledger), {"jit:2026-11-15:14"})
        self.assertEqual(spent_rows(ledger), [], "已退回的那笔不能再算支出事实")


class JitMoodWordTests(unittest.TestCase):
    """jit_plan 的心情词必须来自真实心情（self.mood 里只有十维数值，没有 word 键）。

    以前这里传 `self.mood.get("word")`，真实状态里永远是空串：
    jit_plan 里「15:00 郁闷/低落 → 窝着发呆」那支的心情参数一直是死的。
    """

    FULL_MOOD = {"开心": 10.0, "兴奋": 5.0, "平静": 20.0, "依恋": 10.0,
                 "郁闷": 90.0, "疲惫": 30.0, "生气": 0.0, "暴躁": 0.0,
                 "委屈": 20.0, "焦虑": 10.0}

    def setUp(self):
        self._old = schedule_engine.jit_plan
        self.seen = []

        def fake(hour, hunger, cash, sick=False, mood_word="", **kwargs):
            self.seen.append(mood_word)
            return {"action": "在忙手头的事", "place": "", "zone": "home",
                    "category": "life", "cost_cents": 0, "diary": ""}

        schedule_engine.jit_plan = fake

    def tearDown(self):
        schedule_engine.jit_plan = self._old

    def test_mood_word_comes_from_real_mood(self):
        state = build_state(lambda *a, **k: {"changed": True, "saved": True})
        state.mood = dict(self.FULL_MOOD)
        state._jit_plan_for_hour(NOW, HOUR, DAY)
        self.assertEqual(self.seen, ["郁闷"], "心情最高的那一维要传进 jit_plan")

    def test_partial_mood_dict_does_not_crash(self):
        state = build_state(lambda *a, **k: {"changed": True, "saved": True})
        state.mood = {"word": "平静"}
        state._jit_plan_for_hour(NOW, HOUR, DAY)
        self.assertEqual(self.seen, ["平静"])


if __name__ == "__main__":
    unittest.main()
