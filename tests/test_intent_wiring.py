# -*- coding: utf-8 -*-
"""第六轮：文本意图接线（用户话术/她自己的话）、到达推进、短动作、面板读一致。"""
from __future__ import annotations

import datetime as dt
import inspect
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_transition as tr  # noqa: E402
import clock  # noqa: E402

NOW = dt.datetime(2026, 11, 16, 15, 0, 0)


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self, mood=None):
        self.schedule = {}
        self.activity = {"activity": "在被子里翻身", "place": "家", "zone": "home",
                         "category": "rest"}
        self.mood = dict(mood or {"郁闷": 10, "委屈": 10, "开心": 50, "平静": 50})
        self.commits = []

    def current_activity(self):
        return dict(self.activity)

    def commit_schedule_change(self, day, items, old_items=None, source="", reason=""):
        self.schedule[day] = list(items)
        self.commits.append({"day": day, "items": items, "source": source})
        return {"changed": True, "saved": True}


class FakePlugin:
    def __init__(self, db=None, state=None):
        self.db = db or FakeDB()
        self.state = state or FakeState()
        self._last_umo = ""

    def cfg_int(self, key, default=0):
        return default

    def _data_bot_id(self):
        return "amo"

    async def _data_call(self, *args, **kwargs):
        return None

    def _spawn(self, coro):
        try:
            coro.close()
        except Exception:
            pass
        return None

    async def _ensure_ready(self):
        return None

    async def _body(self):
        return {}

    def _q(self, key, default=None):
        return default


class DetectIntentTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(tr.detect_intent("快去洗澡")["kind"], "bath")
        self.assertEqual(tr.detect_intent("去洗澡")["minutes"], 25)
        self.assertEqual(tr.detect_intent("快去睡觉")["kind"], "sleep")
        self.assertEqual(tr.detect_intent("睡觉了")["kind"], "sleep")
        self.assertIsNone(tr.detect_intent("那我摸着睡觉"))
        self.assertEqual(tr.detect_intent("去吃饭吧")["kind"], "meal")
        errand = tr.detect_intent("去倒垃圾")
        self.assertEqual(errand["kind"], "errand")
        self.assertEqual(errand["minutes"], 8)
        self.assertEqual(tr.detect_intent("去逛街")["kind"], "shopping")
        commute = tr.detect_intent("快去医院")
        self.assertEqual(commute["kind"], "commute")
        self.assertEqual(commute["to"], "医院")

    def test_negation(self):
        for text in ("不去洗澡", "不想去睡觉", "别去医院", "不用去了", "懒得去逛街"):
            self.assertIsNone(tr.detect_intent(text), text)

    def test_empty(self):
        self.assertIsNone(tr.detect_intent(""))
        self.assertIsNone(tr.detect_intent("今天天气不错"))


class SleepGateTests(unittest.TestCase):
    def test_sleep_intent_skips_when_already_asleep(self):
        plugin = FakePlugin()
        plugin.state.activity = {
            "activity": "深度睡眠", "place": "家", "zone": "home", "category": "sleep",
        }
        self.assertIsNone(tr.maybe_start_from_text(plugin, "睡觉了", now=NOW))


class BathFlowTests(unittest.TestCase):
    def _plugin_with_bath(self):
        plugin = FakePlugin()
        self.assertIsNotNone(tr.maybe_start_from_text(plugin, "快去洗澡", now=NOW))
        return plugin

    def test_stages_after_text_start(self):
        plugin = self._plugin_with_bath()
        activity = tr.current_activity(plugin, NOW)
        self.assertEqual(activity["transit_kind"], "bath")
        self.assertEqual(activity["place"], "家")
        six = tr.current_activity(plugin, NOW + dt.timedelta(minutes=6))
        self.assertEqual(six["place"], "浴室")
        twelve = tr.current_activity(plugin, NOW + dt.timedelta(minutes=12))
        self.assertEqual(twelve["place"], "浴室")
        twenty_two = tr.current_activity(plugin, NOW + dt.timedelta(minutes=22))
        self.assertNotEqual(twenty_two["place"], "浴室")
        self.assertTrue(tr.stage_at(plugin.db.kv_get("transition"), NOW + dt.timedelta(minutes=27))["arrived"])

    def test_reply_cannot_claim_done(self):
        plugin = self._plugin_with_bath()
        six = NOW + dt.timedelta(minutes=6)
        out = tr.guard_terminal(plugin, "我洗完了", six)
        self.assertNotIn("洗完了", out)
        self.assertIn("还在洗澡", out)

    def test_same_thing_not_restarted(self):
        plugin = self._plugin_with_bath()
        self.assertIsNone(tr.maybe_start_from_text(plugin, "去洗澡", now=NOW))

    def test_reload_keeps_state(self):
        plugin = self._plugin_with_bath()
        reloaded = FakePlugin(db=plugin.db)
        self.assertEqual(tr.current_activity(reloaded, NOW + dt.timedelta(minutes=6))["place"], "浴室")


class ErrandFlowTests(unittest.TestCase):
    def _plugin(self):
        plugin = FakePlugin()
        plugin.state.schedule["2026-11-16"] = [
            {"start": "14:00", "end": "15:00", "activity": "在家收拾", "place": "家", "detail": ""}]
        return plugin

    def test_short_action_notes_detail_without_block(self):
        plugin = self._plugin()
        tr.maybe_start_from_text(plugin, "去倒垃圾", now=NOW)
        items = plugin.state.schedule["2026-11-16"]
        self.assertEqual(len(items), 1, "短动作不能新增日程块")
        self.assertIn("（期间下楼倒垃圾）", items[0]["detail"])
        self.assertEqual(plugin.db.kv_get("transition", {}).get("kind"), "errand")

    def test_guard_errand_terminal(self):
        plugin = self._plugin()
        tr.maybe_start_from_text(plugin, "去倒垃圾", now=NOW)
        out = tr.guard_terminal(plugin, "倒完了，回来了", NOW + dt.timedelta(minutes=3))
        self.assertNotIn("倒完了", out)
        self.assertIn("还在楼下", out)

    def test_expired_errand_clears(self):
        plugin = self._plugin()
        tr.maybe_start_from_text(plugin, "去倒垃圾", now=NOW)
        self.assertIsNone(tr.current(plugin, NOW + dt.timedelta(minutes=9)))
        self.assertEqual(plugin.db.kv_get("transition", {}), {})
        self.assertEqual(tr.guard_terminal(plugin, "倒完了", NOW + dt.timedelta(minutes=9)), "倒完了")


class AdvanceFlowTests(unittest.TestCase):
    def test_arrival_word_advances_and_writes_blocks(self):
        api_now = clock.now().replace(second=0, microsecond=0)
        plugin = FakePlugin()
        tr.maybe_start_from_text(plugin, "快去医院", now=api_now - dt.timedelta(minutes=25))
        tr.current(plugin, api_now)
        stage = tr.maybe_advance(plugin, "刚下车", api_now)
        self.assertIsNotNone(stage)
        self.assertTrue(stage["arrived"])
        self.assertIsNone(tr.current(plugin, api_now))
        day = api_now.strftime("%Y-%m-%d")
        items = sorted(plugin.state.schedule[day], key=lambda row: row["start"])
        self.assertEqual(items[-1]["place"], "医院")
        for previous, following in zip(items, items[1:]):
            self.assertLessEqual(previous["end"], following["start"], "块之间不能重叠")

    def test_early_terminal_word_does_not_advance(self):
        plugin = FakePlugin()
        tr.maybe_start_from_text(plugin, "快去医院", now=NOW)
        self.assertIsNone(tr.maybe_advance(plugin, "到医院了", NOW + dt.timedelta(minutes=3)))
        self.assertIn("还在收拾", tr.guard_terminal(plugin, "我在医院了", NOW + dt.timedelta(minutes=3)))


class ScheduleApiReadTests(unittest.TestCase):
    def test_api_schedule_matches_current_activity(self):
        from amo_api_schedule import ScheduleApiMixin

        api_now = clock.now().replace(second=0, microsecond=0)
        plugin = FakePlugin()
        tr.maybe_start_from_text(plugin, "快去医院", now=api_now)
        tr.current(plugin, api_now)
        day = api_now.strftime("%Y-%m-%d")

        def schedule_tree(_day):
            rows = plugin.state.schedule.get(_day) or []
            return [{"title": "今日", "date": _day, "children": [
                {"start": row["start"], "end": row["end"], "activity": row["activity"],
                 "place": row["place"], "zone": row.get("zone", ""), "doing": True}
                for row in rows]}]

        async def ensure_schedule(_umo, force=False):
            return plugin.state.schedule.get(day) or []

        async def enrich(_day, tree, _umo):
            return tree

        plugin.state.ensure_schedule = ensure_schedule
        plugin.state.schedule_tree = schedule_tree
        plugin.state.enrich_schedule_tree = enrich
        plugin.state.schedule_context = lambda _day: {}
        plugin.state.schedule_blind_hint = lambda _day: ""
        plugin.state.current_activity = lambda: tr.current_activity(plugin, api_now)

        result = ScheduleApiMixin.api_schedule.__get__(plugin, type(plugin))
        data = __import__("asyncio").run(result())["data"]
        current = tr.current_activity(plugin, api_now)
        children = [child for block in data["tree"] for child in block["children"]]
        def _minute_of(text):
            hour, _sep, minute = str(text or "00:00").strip()[:5].partition(":")
            return int(hour or 0) * 60 + int(minute or 0)

        def _covers(child, minute):
            start = _minute_of(child["start"])
            end = _minute_of(child["end"])
            if end <= start:  # 23:5x 的过渡块会跨 0 点，end 要顺延一天
                end += 24 * 60
            return start <= minute < end

        current_minute = _minute_of(api_now.strftime("%H:%M"))
        covering = [child for child in children if _covers(child, current_minute)]
        self.assertTrue(covering, children)
        self.assertEqual(covering[0]["place"], current["place"])
        self.assertEqual(covering[0]["activity"], current["activity"])


class WiringTests(unittest.TestCase):
    def test_user_message_entry_starts_transition(self):
        from amo_runtime import RuntimeMixin

        source = inspect.getsource(RuntimeMixin.on_message)
        self.assertIn("_maybe_transition_text(self, _user_text, source=\"user\")", source)

    def test_send_path_advances_and_starts(self):
        from amo_message import MessageMixin
        from amo_reply_pipeline import PIPELINE

        # Phase 6：推进/开启过渡改成 send.transition_advance 阶段（顺序仍是守卫之前）
        self.assertIn("amo_reply_pipeline", inspect.getsource(MessageMixin._send_to_umo))
        stage = {item.id: item for item in PIPELINE.stages}["send.transition_advance"]
        source = inspect.getsource(stage.run)
        self.assertIn("_maybe_advance(ctx.plugin, text)", source)
        self.assertIn("_maybe_transition_text(ctx.plugin, text, source=\"bot\")", source)


if __name__ == "__main__":
    unittest.main()
