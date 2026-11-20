# -*- coding: utf-8 -*-
"""StateContext 硬校验：非工作状态绝不写职场事件，冲突记忆会被审查删除。"""

import asyncio
import datetime as dt
import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402
astrbot_stub.install()

import micro_events as me  # noqa: E402
import amo_state_context as sc  # noqa: E402
import amo_state_snapshot  # noqa: E402

HOME_SICK = {"zone": "home", "place": "家", "is_sick": True, "rest": True,
             "is_weekend": True, "is_working": False,
             "health": {"status": "感冒", "sick": True, "score": 40}}
OFFICE_OK = {"zone": "office", "place": "公司", "is_sick": False, "rest": False,
             "is_weekend": False, "is_working": True,
             "health": {"status": "健康", "sick": False, "score": 100}}
EVENTS = {e["key"]: e for e in me.POOL}


def _ctx(**kw):
    base = dict(HOME_SICK)
    base.update(kw)
    return base


class StateContextTests(unittest.TestCase):
    def setUp(self):
        # 固定「今天」为用例里的 2026-11-15（周日），不依赖跑测当天。
        self._today_patch = mock.patch.object(amo_state_snapshot, "today_str", return_value="2026-11-15")
        self._today_patch.start()

    def tearDown(self):
        self._today_patch.stop()

    def test_build_context_sick_rest_is_not_working(self):
        class State:
            def snapshot(self):
                return {"activity": {"place": "家", "zone": "home", "activity": "生病休息"},
                        "health": {"status": "感冒", "sick": True}}

        class P:
            state = State()

        ctx = sc.build_state_context(P(), dt.datetime(2026, 11, 15, 10, 0))
        self.assertEqual(ctx["location"], "home")
        self.assertTrue(ctx["is_sick"])
        self.assertTrue(ctx["is_weekend"])
        self.assertFalse(ctx["is_working"])

    def test_office_event_conflicts_at_home_sick(self):
        self.assertTrue(sc.event_conflict(EVENTS["desk_slack"], HOME_SICK))
        self.assertTrue(sc.event_conflict(EVENTS["subway"], HOME_SICK))
        self.assertTrue(sc.event_conflict(EVENTS["mall_shopping"], HOME_SICK))

    def test_office_event_allowed_while_working(self):
        self.assertEqual(sc.event_conflict(EVENTS["desk_slack"], OFFICE_OK), "")

    def test_sick_home_sick_events_allowed(self):
        self.assertEqual(sc.event_conflict(EVENTS["sick_water"], HOME_SICK), "")

    def test_format_memory_raises_on_conflict(self):
        with self.assertRaises(sc.StateConflict):
            sc.format_event_memory(EVENTS["desk_slack"], HOME_SICK)

    def test_format_memory_adds_prefix(self):
        text = sc.format_event_memory(EVENTS["sick_water"], HOME_SICK)
        self.assertTrue(text.startswith("[生病在家]"))
        text = sc.format_event_memory(EVENTS["desk_slack"], OFFICE_OK)
        self.assertTrue(text.startswith("[在公司]"))

    def test_prefix_unknown_falls_back(self):
        self.assertEqual(sc.memory_prefix({"zone": "unknown"}), "[生活小事]")


class MemoryGeneratorTests(unittest.TestCase):
    def _plugin(self):
        from amo_memory_bridge import BridgeMixin

        class P:
            pass

        p = P()
        p.generate_memory_text = types.MethodType(BridgeMixin.generate_memory_text, p)
        return p

    def test_conflict_is_blocked_before_writing(self):
        p = self._plugin()
        with self.assertRaises(sc.StateConflict):
            asyncio.run(p.generate_memory_text(EVENTS["desk_slack"], HOME_SICK))

    def test_no_llm_returns_prefixed_fact(self):
        p = self._plugin()
        text = asyncio.run(p.generate_memory_text(EVENTS["sick_blanket"], HOME_SICK))
        self.assertTrue(text.startswith("[生病在家]"))
        self.assertIn("被子", text)


class AuditorTests(unittest.TestCase):
    def test_auditor_deletes_conflict_but_keeps_past(self):
        from amo_memory_bridge import BridgeMixin

        class State:
            def snapshot(self):
                return {"activity": {"place": "家", "zone": "home", "activity": "休息"},
                        "health": {"status": "健康", "sick": False}}

        class P:
            def __init__(self):
                self.state = State()
                self._last_uid = "u1"
                self.deleted = []

            async def _data_mem_list(self, uid, keyword, limit):
                return [
                    {"id": 1, "kind": "event", "content": "[生活小事] 在工位上摸鱼刷了会儿手机"},
                    {"id": 2, "kind": "event", "content": "[生活小事] 昨天在公司被老板说了一顿"},
                    {"id": 3, "kind": "event", "content": "[在家休息] 窝在沙发上看了会儿综艺"},
                    {"id": 4, "kind": "conversation_summary",
                     "content": "今天下午他说上班摸鱼，我怼了他一句"},
                ]

            async def _data_call(self, name, *args):
                if name == "delete_memory":
                    self.deleted.append(args[-1])
                return True

            def _data_bot_id(self):
                return "bot"

        p = P()
        p._audit_memories = types.MethodType(BridgeMixin._audit_memories, p)
        n = asyncio.run(p._audit_memories())
        self.assertEqual(n, 1)
        self.assertEqual(p.deleted, [1])


if __name__ == "__main__":
    unittest.main()
