# -*- coding: utf-8 -*-
"""单一事实源收口（2026-11-15 v2）：系统警告 / 禁止词 / 统一写入口 / 重试。

用户要求：
1. 日程和聊天都读 get_current_action()；
2. 生成前把这一小时的【已完成事实】贴到模型脸上（系统警告 + 禁止词）；
3. 命中矛盾词先重试（最多 2 次），仍不行就确定性改写；
4. 说"点了外卖"必须先查账，查不到就改。
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_current_action import get_current_action, set_action  # noqa: E402
from amo_fact_binder import ban_words, facts_lines, warning_line  # noqa: E402
from amo_llm import LlmMixin  # noqa: E402

TODAY = _dt.date.today().isoformat()
NOON = _dt.datetime.combine(_dt.date.today(), _dt.time(12, 30))
EVENING = _dt.datetime.combine(_dt.date.today(), _dt.time(19, 0))
COOK_ITEMS = [
    {"start": "12:00", "end": "13:00", "activity": "在家做饭", "place": "家", "category": "rest"},
]


class FakeDB:
    def __init__(self, kv=None):
        self._kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self._kv.get(key, default)

    def kv_set(self, key, value):
        self._kv[key] = value

    def get_schedule(self, day):
        return []


class FakeState:
    def __init__(self, activity, schedule=None):
        self._activity = activity
        self.schedule = {TODAY: list(schedule or [])}

    def current_activity(self):
        return dict(self._activity)

    def current_activity_info(self):
        return dict(self._activity), "schedule"


class FakeWallet:
    def __init__(self, rows=None, balance=200000):
        self.rows = list(rows or [])
        self._balance = balance
        self.debits = []

    def ledger(self, limit=50):
        return list(self.rows)

    def balance(self):
        return self._balance

    def debit(self, amount, reason, idem, **kw):
        self.debits.append({"amount": amount, "reason": reason})
        self._balance -= amount
        return True


class FakePlugin:
    def __init__(self, activity=None, schedule=None, rows=None, flags=None, replies=None):
        self.state = FakeState(activity or {"activity": "在家做饭", "place": "家",
                                            "zone": "home", "category": "meal"},
                               schedule or COOK_ITEMS)
        self.db = FakeDB()
        self.wallet = FakeWallet(rows or [])
        self.micro_events = None
        self._flags = dict(flags or {})
        self.llm = FakeLLM(replies or [])

    def cfg_bool(self, key, default=False):
        return bool(self._flags.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self._flags.get(key, default))


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    async def chat(self, umo, system, prompt, limit=220):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else ""


class WarningLineTests(unittest.TestCase):
    def test_warning_mentions_home_cooking_and_bans_takeout(self):
        line = warning_line(FakePlugin(), NOON)
        self.assertIn("【系统警告】", line)
        self.assertIn("在【家】", line)
        self.assertIn("做饭", line)
        self.assertIn("外卖", line)
        self.assertIn("出门", line)

    def test_warning_is_first_fact_line(self):
        lines = facts_lines(FakePlugin(), NOON)
        self.assertTrue(lines)
        self.assertTrue(lines[0].startswith("【系统警告】"), lines[0])

    def test_ban_words_away_from_home(self):
        plugin = FakePlugin(activity={"activity": "上班", "place": "公司",
                                      "zone": "office", "category": "work"},
                            schedule=[])
        words = ban_words(plugin, NOON)
        self.assertIn("在家", words)
        self.assertNotIn("在公司", words)

    def test_ban_words_allows_takeout_when_charged(self):
        ts = _dt.datetime.combine(_dt.date.today(), _dt.time(12, 0)).timestamp()
        plugin = FakePlugin(schedule=[],
                            rows=[{"ts": ts, "delta_cents": -2500,
                                   "reason": "点了个外卖", "category": ""}])
        words = ban_words(plugin, NOON)
        self.assertNotIn("外卖", words)


    def test_future_charge_is_not_a_fact(self):
        # 12:30 时 13:32 的外卖还没发生，禁止词里必须有外卖
        later = _dt.datetime.combine(_dt.date.today(), _dt.time(13, 32)).timestamp()
        plugin = FakePlugin(schedule=COOK_ITEMS,
                            rows=[{"ts": later, "delta_cents": -2500,
                                   "reason": "今天不想做饭，点了个外卖", "category": ""}])
        words = ban_words(plugin, NOON)
        self.assertIn("外卖", words)

    def test_past_charge_is_a_fact(self):
        past = _dt.datetime.combine(_dt.date.today(), _dt.time(11, 30)).timestamp()
        plugin = FakePlugin(schedule=[], rows=[
            {"ts": past, "delta_cents": -2500, "reason": "点了个外卖", "category": ""}])
        words = ban_words(plugin, NOON)
        self.assertNotIn("外卖", words)


    def test_refunded_charge_is_not_a_fact(self):
        # 13:32 那笔外卖已原路退回：不能再当成"她真的点过外卖"
        debit = _dt.datetime.combine(_dt.date.today(), _dt.time(13, 32)).timestamp()
        credit = _dt.datetime.combine(_dt.date.today(), _dt.time(13, 50)).timestamp()
        rows = [
            {"ts": debit, "delta_cents": -2500, "reason": "今天不想做饭，点了个外卖",
             "idem": "micro:today:takeout", "ref": ""},
            {"ts": credit, "delta_cents": 2500, "reason": "退回：外卖是系统误记",
             "idem": "refund:micro:today:takeout", "ref": "micro:today:takeout"},
        ]
        plugin = FakePlugin(schedule=[], rows=rows)
        now = _dt.datetime.combine(_dt.date.today(), _dt.time(14, 0))
        words = ban_words(plugin, now)
        self.assertIn("外卖", words)
        text = "\n".join(facts_lines(plugin, now))
        self.assertNotIn("确实点过一次外卖", text)
        self.assertIn("已退回", text)


class UnifiedWriterTests(unittest.TestCase):
    def test_set_action_then_read_back(self):
        plugin = FakePlugin(activity={"activity": "在家做饭", "place": "家",
                                      "zone": "home", "category": "meal"},
                            schedule=[])
        ok = set_action(plugin, "正在洗澡", 0, place="家", category="life", source="test")
        self.assertTrue(ok)
        data = get_current_action(plugin, NOON)
        self.assertEqual(data["action"], "正在洗澡")
        self.assertEqual(data["source"], "jit")
        self.assertEqual(data["zone"], "home")

    def test_set_action_derives_zone(self):
        plugin = FakePlugin(activity={"activity": "上班", "place": "公司",
                                      "zone": "office", "category": "work"},
                            schedule=[])
        set_action(plugin, "在开会", 0, place="公司", category="work")
        data = get_current_action(plugin, NOON)
        self.assertEqual(data["place"], "公司")
        self.assertNotEqual(data["zone"], "")


class ClaimRetryTests(unittest.TestCase):
    def _mixin(self, plugin):
        return plugin  # LlmMixin 的方法只用到 cfg_int / llm / fact_binder 的 plugin 接口

    def test_retry_replaces_fabricated_takeout(self):
        # replies 里放"重试时"模型才会说的话（第一次生成是外面传进来的原文）
        plugin = FakePlugin(replies=["（摸了摸肚子）自己做的饭，好撑呀～"])
        result = asyncio.run(LlmMixin._claim_retry(self._mixin(plugin), "老公我点了个外卖，刚吃完",
                                                   "umo", "sys", "prompt"))
        self.assertIn("自己做的饭", result)
        self.assertEqual(len(plugin.llm.prompts), 1)

    def test_retry_gives_up_after_two_attempts(self):
        plugin = FakePlugin(replies=["我又点了外卖", "还是点了外卖", "第三次也点外卖"])
        result = asyncio.run(LlmMixin._claim_retry(self._mixin(plugin), "我点了外卖",
                                                   "umo", "sys", "prompt"))
        self.assertEqual(len(plugin.llm.prompts), 2)  # 最多重试 2 次
        self.assertNotIn("外卖", result)

    def test_good_reply_passes_without_retry(self):
        plugin = FakePlugin(replies=["嗯嗯，我在家慢慢吃呢"])
        result = asyncio.run(LlmMixin._claim_retry(self._mixin(plugin), "嗯嗯，我在家慢慢吃呢",
                                                   "umo", "sys", "prompt"))
        self.assertEqual(result, "嗯嗯，我在家慢慢吃呢")
        self.assertEqual(plugin.llm.prompts, [])


if __name__ == "__main__":
    unittest.main()
