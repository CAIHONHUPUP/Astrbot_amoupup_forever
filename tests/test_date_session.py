# -*- coding: utf-8 -*-
"""约会会话单一源（Phase 2）：窗口 / 地点 / together / 收尾的读写与老库适配。"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_date_context as dc  # noqa: E402
import amo_date_facts as facts  # noqa: E402
import amo_date_session as ds  # noqa: E402
from amo_feature_api import FeatureApiMixin  # noqa: E402


class FakeDB:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return []


class FakeState:
    def __init__(self, items=None):
        self.schedule = dict(items or {})
        self.db = None


class FakePlugin:
    def __init__(self, kv=None, items=None):
        self.db = FakeDB(kv)
        self.state = FakeState(items)
        self.state.db = self.db


class Holder:
    """只带 db 的宿主对象：验证 feature API 走的是会话单一源。"""

    def __init__(self, db):
        self.db = db


NOW = time.time()   # 锚在真实时钟：date_windows 内部用 now_ts() 收窗


def day_of(ts):
    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


class BeginTests(unittest.TestCase):
    def test_begin_writes_projection_and_together(self):
        p = FakePlugin()
        ds.begin(p, "和老公吃火锅", NOW + 3600, NOW)
        self.assertEqual(p.db.kv["date_now"],
                         {"line": "和老公吃火锅", "until": NOW + 3600, "since": NOW})
        self.assertTrue(ds.is_dating(p, NOW))
        self.assertTrue(ds.together(p, NOW))
        self.assertEqual(ds.window(p, NOW), (NOW, NOW + 3600))
        self.assertEqual(ds.line(p, NOW), "和老公吃火锅")
        self.assertEqual(p.db.kv["date_prep"], {})

    def test_meet_and_place_are_session_facts(self):
        p = FakePlugin()
        ds.meet(p, "火锅", "楼下小馆", now=NOW)
        self.assertEqual(ds.load(p, NOW)["phase"], ds.PHASE_PREP)
        self.assertEqual(ds.place(p, NOW), "楼下小馆")
        self.assertEqual(p.db.kv["date_meet"]["title"], "火锅")
        ds.set_place(p, "商场")
        self.assertEqual(ds.place(p, NOW), "商场")

    def test_day_had_date(self):
        p = FakePlugin()
        ds.begin(p, "和老公逛街", NOW + 3600, NOW)
        self.assertTrue(ds.day_had_date(p, day_of(NOW), NOW))
        self.assertFalse(ds.day_had_date(p, "2000-01-01", NOW))


class PauseTests(unittest.TestCase):
    def test_pause_resume_round_trip(self):
        p = FakePlugin()
        ds.begin(p, "和老公吃火锅", NOW + 3600, NOW)
        ds.pause(p, minutes=10, now=NOW)
        self.assertEqual(ds.load(p, NOW)["phase"], ds.PHASE_PAUSED)
        self.assertEqual(ds.paused(p, NOW), {"at": NOW, "until": NOW + 600})
        self.assertTrue(ds.together(p, NOW))
        self.assertEqual(p.db.kv["date_pause"]["until"], NOW + 600)
        ds.resume(p, now=NOW + 60)
        self.assertEqual(ds.load(p, NOW + 60)["phase"], ds.PHASE_ACTIVE)
        self.assertIsNone(ds.paused(p, NOW + 60))
        self.assertEqual(p.db.kv["date_pause"], {})


class EndingTests(unittest.TestCase):
    def test_request_end_writes_sent_projection(self):
        p = FakePlugin()
        ds.begin(p, "和老公吃火锅", NOW + 3600, NOW)
        ds.request_end(p, "该回去了", reason="bot", ttl=600, sent=True, now=NOW)
        self.assertEqual(p.db.kv["date_ending"]["sent"], True)
        self.assertEqual(p.db.kv["date_ending"]["fact"], "该回去了")
        self.assertEqual(ds.ending(p, NOW)["fact"], "该回去了")

    def test_set_date_ending_api_is_not_sent(self):
        p = FakePlugin()
        ds.begin(p, "和老公吃火锅", NOW + 3600, NOW)
        holder = Holder(p.db)
        FeatureApiMixin.set_date_ending(holder, "该回去了", reason="bot")
        self.assertEqual(ds.ending(p, NOW)["sent"], False)


class FinishTests(unittest.TestCase):
    def test_finish_clears_now_and_writes_home(self):
        p = FakePlugin()
        ds.begin(p, "和老公吃火锅", NOW + 7200, NOW)
        ds.request_end(p, "该回去了", reason="bot", ttl=600, sent=True, now=NOW)
        # 提前收尾：窗口必须钉在结束这一刻，而不是原定的 7200 秒
        ds.finish(p, NOW + 1800, reason="home", title="火锅")
        self.assertEqual(p.db.kv["date_now"], {})
        self.assertEqual(p.db.kv["date_home"]["title"], "火锅")
        self.assertEqual(p.db.kv["date_closed_at"], NOW + 1800)
        self.assertEqual(p.db.kv["date_ending"], {})
        data = ds.load(p, NOW + 1800)
        self.assertEqual(data["phase"], ds.PHASE_HOME)
        self.assertFalse(data["together"])
        self.assertFalse(ds.together(p, NOW + 1800))
        self.assertIsNone(ds.window(p, NOW + 1800))
        self.assertIsNone(ds.ending(p, NOW + 1800))

    def test_finish_prompt_block_is_alone(self):
        p = FakePlugin()
        ds.begin(p, "和老公吃火锅", NOW + 7200, NOW)
        ds.finish(p, NOW + 1800, reason="home", title="火锅")
        text = dc.prompt_block(p, _dt.datetime.fromtimestamp(NOW + 1800))
        for token in ("各回各家", "一个人回到出租屋", "只能手机", "重点回味"):
            self.assertIn(token, text)
        for banned in ("同居", "一起回家", "他还在屋里"):
            self.assertNotIn(banned, text)

    def test_cancel_closes_without_home(self):
        p = FakePlugin()
        ds.begin(p, "和老公吃火锅", NOW + 3600, NOW)
        ds.cancel(p, reason="interrupted", now=NOW + 60)
        self.assertEqual(p.db.kv["date_now"], {})
        self.assertEqual(p.db.kv["date_home"], {})
        self.assertIsNone(ds.home(p, NOW + 60))
        self.assertFalse(ds.together(p, NOW + 60))
        self.assertEqual(ds.load(p, NOW + 60)["phase"], ds.PHASE_CLOSED)

    def test_cancel_after_finish_keeps_home(self):
        p = FakePlugin()
        ds.finish(p, NOW + 1800, reason="home", title="火锅")
        ds.cancel(p, reason="late_interrupt", now=NOW + 2400)
        self.assertEqual(ds.load(p, NOW + 2400)["phase"], ds.PHASE_HOME)
        self.assertIsNotNone(ds.home(p, NOW + 2400))
        self.assertFalse(ds.together(p, NOW + 2400))

    def test_expired_window_is_not_together(self):
        p = FakePlugin()
        ds.begin(p, "和老公吃火锅", NOW - 10, NOW - 3600)
        self.assertFalse(ds.together(p, NOW))
        self.assertFalse(ds.is_dating(p, NOW))
        self.assertIsNone(ds.window(p, NOW))
        self.assertEqual(ds.load(p, NOW)["phase"], ds.PHASE_CLOSED)


class LegacyTests(unittest.TestCase):
    def test_legacy_live_date_now_is_adopted_readonly(self):
        p = FakePlugin({"date_now": {"line": "和老公火锅", "until": NOW + 3600, "since": NOW - 600}})
        data = ds.load(p, NOW)
        self.assertEqual(data["phase"], ds.PHASE_ACTIVE)
        self.assertTrue(data["together"])
        self.assertEqual(ds.window(p, NOW), (NOW - 600, NOW + 3600))
        self.assertNotIn(ds.KV, p.db.kv, "老库适配只读，不能顺手落库")

    def test_legacy_expired_now_with_home(self):
        p = FakePlugin({"date_now": {"line": "和老公火锅", "until": NOW - 3600, "since": NOW - 7200},
                        "date_home": {"title": "火锅", "at": NOW - 1800, "until": NOW + 3600},
                        "date_closed_at": NOW - 1800})
        data = ds.load(p, NOW)
        self.assertEqual(data["phase"], ds.PHASE_HOME)
        self.assertFalse(data["together"])
        self.assertFalse(ds.is_dating(p, NOW))
        self.assertIsNone(ds.window(p, NOW))
        self.assertEqual(ds.home(p, NOW)["title"], "火锅")

    def test_legacy_ending_only(self):
        p = FakePlugin({"date_ending": {"fact": "该回去了", "reason": "bot",
                                        "until": NOW + 300, "sent": True}})
        self.assertFalse(ds.is_dating(p, NOW))
        self.assertEqual(ds.load(p, NOW)["phase"], ds.PHASE_NONE)
        self.assertEqual(ds.ending(p, NOW)["fact"], "该回去了")
        self.assertTrue(ds.ending(p, NOW)["sent"])


class ClearNowTests(unittest.TestCase):
    def test_clear_now_keeps_home_fact(self):
        p = FakePlugin()
        ds.finish(p, NOW + 7200, reason="home", title="火锅")
        ds.clear_now(p, NOW + 7300)
        self.assertEqual(ds.load(p, NOW + 7300)["phase"], ds.PHASE_HOME)
        self.assertTrue(p.db.kv["date_home"])

    def test_feature_api_clear_date_now_keeps_home(self):
        p = FakePlugin()
        ds.finish(p, NOW + 7200, reason="home", title="火锅")
        holder = Holder(p.db)
        FeatureApiMixin.set_date_now(holder, "", 0)
        self.assertEqual(ds.load(p, NOW + 7300)["phase"], ds.PHASE_HOME)
        self.assertEqual(p.db.kv["date_home"]["title"], "火锅")
        self.assertFalse(ds.together(p, NOW + 7300))


class FeatureContractTests(unittest.TestCase):
    def test_guard_style_calls_work_on_feature_api(self):
        """守护侧真实调用形态：finish_date_session(title, reason, now_ts) 等。"""
        p = FakePlugin()
        holder = Holder(p.db)
        FeatureApiMixin.set_date_now(holder, "和老公吃火锅", NOW + 3600, NOW)
        self.assertTrue(FeatureApiMixin.is_dating(holder, NOW))
        self.assertIsNone(FeatureApiMixin.date_ending(holder, NOW))
        digest = FeatureApiMixin.date_session(holder, NOW)
        self.assertEqual(digest["phase"], ds.PHASE_ACTIVE)
        self.assertTrue(digest["together"])
        FeatureApiMixin.finish_date_session(holder, "火锅", "user_done", NOW + 1800)
        digest = FeatureApiMixin.date_session(holder, NOW + 1800)
        self.assertEqual(digest["phase"], ds.PHASE_HOME)
        self.assertFalse(digest["together"])
        self.assertIsNone(FeatureApiMixin.date_ending(holder, NOW + 1800))
        FeatureApiMixin.set_date_pause(holder, 5)
        FeatureApiMixin.clear_date_pause(holder)
        FeatureApiMixin.cancel_date_session(holder, "interrupted", NOW + 2000)
        self.assertFalse(FeatureApiMixin.is_dating(holder, NOW + 2000))
        self.assertEqual(ds.load(p, NOW + 2000)["phase"], ds.PHASE_HOME)


class DateFactsTests(unittest.TestCase):
    def test_session_window_wins_over_schedule_block(self):
        today = day_of(NOW)
        item = {"start": "19:00", "end": "21:00", "activity": "和老公火锅",
                "place": "楼下小馆", "category": "social"}
        p = FakePlugin(items={today: [item]})
        ds.begin(p, "和老公吃火锅", NOW + 3600, NOW)
        windows = facts.date_windows(p, today)
        self.assertEqual(len(windows), 1, windows)
        self.assertEqual(windows[0]["source"], "session")
        self.assertTrue(windows[0]["activity"].startswith("和老公吃火锅"))

    def test_session_window_not_leaked_to_other_day(self):
        p = FakePlugin()
        ds.begin(p, "和老公吃火锅", NOW + 3600, NOW)
        self.assertEqual(facts.date_windows(p, "2000-01-01"), [])


if __name__ == "__main__":
    unittest.main()
