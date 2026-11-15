# -*- coding: utf-8 -*-
"""约会物理状态注入（2026-11-15）：面对面事实必须压过聊天记录反推。"""
import asyncio
import datetime
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_date_facts import date_presence_facts, date_windows, transfer_context  # noqa: E402
from amo_diary_guard import fact_rules, rewrite_together_chat, sanitize_diary  # noqa: E402
from amo_fact_pool import build_fact_pool, render_prompt  # noqa: E402
from schedule_engine import ScheduleEngine, diary_presence_rules  # noqa: E402


DAY = "2026-11-15"
ITEM = {"start": "19:22", "end": "20:27", "activity": "和老公火锅",
        "place": "楼下小馆", "category": "social"}
STAMP = datetime.datetime(2026, 11, 15, 19, 25).timestamp()


class FakeDB:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return []


class FakePlugin:
    def __init__(self, items=None, ledger=None, kv=None):
        self.db = FakeDB(kv)
        state = types.SimpleNamespace(schedule={DAY: list(items or [])})
        state.db = self.db
        state.health_info = lambda: {}
        self.state = state
        self.wallet = types.SimpleNamespace(ledger=lambda n=200: list(ledger or []))
        self.wardrobe = types.SimpleNamespace(current=lambda day: {})

    async def _data_call(self, *args, **kwargs):
        return []

    def _data_bot_id(self):
        return "bot"


class DateWindowTests(unittest.TestCase):
    def test_schedule_date_makes_window(self):
        plugin = FakePlugin(items=[ITEM])
        windows = date_windows(plugin, DAY)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["start"], "19:22")
        self.assertEqual(windows[0]["end"], "20:27")

    def test_friend_social_is_not_a_date(self):
        plugin = FakePlugin(items=[{"start": "14:00", "end": "17:00",
                                    "activity": "和柳如烟逛街", "place": "商场",
                                    "category": "social"}])
        self.assertEqual(date_windows(plugin, DAY), [])

    def test_presence_fact_says_face_to_face(self):
        plugin = FakePlugin(items=[ITEM])
        facts = date_presence_facts(plugin, DAY)
        self.assertEqual(facts[0]["type"], "物理场景")
        self.assertIn("面对面", facts[0]["detail"])
        self.assertIn("不是异地", facts[0]["detail"])

    def test_hour_slot_filters(self):
        plugin = FakePlugin(items=[ITEM])
        self.assertEqual(len(date_presence_facts(plugin, DAY, hour_slot=14)), 0)
        hourly = date_presence_facts(plugin, DAY, hour_slot=19)
        self.assertEqual(len(hourly), 1)
        self.assertIn("这一小时", hourly[0]["detail"])

    def test_transfer_inside_window_is_face_to_face(self):
        plugin = FakePlugin(items=[ITEM])
        ctx = transfer_context(plugin, DAY, STAMP, 30000, "转账")
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["type"], "约会转账")
        self.assertIn("面对面", ctx["detail"])
        self.assertIn("300.00", ctx["detail"])

    def test_transfer_outside_window_untouched(self):
        plugin = FakePlugin(items=[ITEM])
        self.assertIsNone(transfer_context(plugin, DAY, STAMP - 6 * 3600, 30000, "转账"))


class PoolTests(unittest.TestCase):
    def _pool(self, items=None, ledger=None, kv=None):
        plugin = FakePlugin(items=items, ledger=ledger, kv=kv)
        day = datetime.date(2026, 11, 15)
        return asyncio.run(build_fact_pool(plugin, day))

    def test_pool_leads_with_physical_scene(self):
        pool = self._pool(items=[ITEM])
        self.assertTrue(pool["meta"]["date_together"])
        self.assertEqual(pool["facts"][0]["type"], "物理场景")
        prompt = render_prompt(pool)
        self.assertIn("【物理场景事实·最高优先级】", prompt)
        self.assertNotIn("只能写“在微信上聊了几句”", prompt)

    def test_pool_keeps_old_rules_without_date(self):
        pool = self._pool(items=[])
        self.assertFalse(pool["meta"]["date_together"])
        prompt = render_prompt(pool)
        self.assertIn("【线上事实】", prompt)
        self.assertNotIn("【物理场景事实·最高优先级】", prompt)

    def test_pool_contextualizes_transfer(self):
        pool = self._pool(items=[ITEM], ledger=[{"ts": STAMP, "delta_cents": 30000, "reason": "转账"}])
        types_ = [fact["type"] for fact in pool["facts"]]
        self.assertIn("约会转账", types_)
        detail = next(fact["detail"] for fact in pool["facts"] if fact["type"] == "约会转账")
        self.assertIn("面对面", detail)


class GuardTests(unittest.TestCase):
    def test_together_rules_replace_online_rule(self):
        rules = fact_rules(True)
        self.assertIn("面对面", rules)
        self.assertNotIn("【线上/线下】", rules)

    def test_rewrite_drops_wechat_phrasing_when_together(self):
        out = rewrite_together_chat("在微信上跟老公聊了几句，心里稍微踏实了点。")
        self.assertNotIn("微信", out)
        self.assertIn("当面", out)

    def test_sanitize_keeps_real_intimacy_when_together(self):
        text = "他抱着我，我一下就哭了。"
        self.assertEqual(sanitize_diary(text, together=True), text)
        self.assertNotEqual(sanitize_diary(text), text)


class SmallDiaryRuleTests(unittest.TestCase):
    """小日记也必须有「面对面」硬规则（2026-11-15）。"""

    def test_together_rules_ban_wechat_framing(self):
        rules = diary_presence_rules(True)
        self.assertIn("【物理场景事实·最高优先级】", rules)
        self.assertIn("面对面", rules)
        self.assertIn("不是异地", rules)
        self.assertNotIn("线上聊天只能写", rules)

    def test_alone_rules_unchanged(self):
        rules = diary_presence_rules(False)
        self.assertIn("线上聊天只能写", rules)
        self.assertNotIn("面对面", rules)

    def test_diary_prompt_accepts_together(self):
        import inspect
        params = inspect.signature(ScheduleEngine._diary_prompt).parameters
        self.assertIn("together", params)


if __name__ == "__main__":
    unittest.main()
