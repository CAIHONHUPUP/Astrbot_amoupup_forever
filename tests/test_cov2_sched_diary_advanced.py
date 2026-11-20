# -*- coding: utf-8 -*-
"""cov2: amo_state_diary 的整点/块缓存回放、地点闸、按小时兜底、手动改日程对账。"""
from __future__ import annotations

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

import amo_state_diary as dmod  # noqa: E402
from amo_state_diary import DiaryStateMixin  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}
        self.saved = []
        self.conn = None

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def save_diary(self, day, start, text, **kwargs):
        self.saved.append(("old", day, start, text, kwargs))

    def save_block_diary(self, day, bid, text, **kwargs):
        self.saved.append(("block", day, bid, text, kwargs))

    def get_block_diary(self, day, bid):
        return self.kv.get("block:" + str(bid), "")

    def get_diary(self, day, start):
        return self.kv.get("old:" + str(start), "")


class FakePlugin:
    def __init__(self, **cfg):
        self.config = {"debug_prompt": False, "photo_debug": False}
        self.config.update(cfg)

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))


class Host(DiaryStateMixin):
    def __init__(self):
        self.db = FakeDB()
        self.p = FakePlugin()
        self.engine = None
        self.schedule = {}
        self.schedule_ctx = {}
        self.health = {"status": "健康"}
        self.photo_infos = []


class Base(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.logger = types.SimpleNamespace(
            records=[],
            info=lambda m, *a, **k: self.logger.records.append(("info", str(m))),
            warning=lambda m, *a, **k: self.logger.records.append(("warning", str(m))),
            debug=lambda m, *a, **k: self.logger.records.append(("debug", str(m))),
            error=lambda m, *a, **k: self.logger.records.append(("error", str(m))),
        )
        self._saved = dmod.logger
        dmod.logger = self.logger

    def tearDown(self):
        dmod.logger = self._saved


class HourChatTests(Base):
    def test_filters_commands_and_money(self):
        self.host.db.conn = types.SimpleNamespace(
            execute=lambda sql, params: types.SimpleNamespace(fetchall=lambda: [
                ("user", "你好"), ("bot", "在呢"), ("user", "/命令"),
                ("user", "[图片]"), ("user", "给你转账了"), ("user", ""),
                ("user", "很长的\n内容" + "x" * 300),
            ]))
        lines = self.host._diary_hour_chat(dt.date(2026, 11, 18), "08:00", "09:00")
        self.assertEqual(lines[0], "老公：你好")
        self.assertEqual(lines[1], "我：在呢")
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[2].startswith("老公：很长的 内容"))
        self.assertLessEqual(len(lines[2]), 203)

    def test_reversed_range_and_errors(self):
        seen = []

        def execute(sql, params):
            seen.append(params)
            return types.SimpleNamespace(fetchall=lambda: [])

        self.host.db.conn = types.SimpleNamespace(execute=execute)
        self.host._diary_hour_chat(dt.date(2026, 11, 18), "09:00", "08:00")
        self.assertEqual(len(seen), 1)
        self.host.db.conn = types.SimpleNamespace(
            execute=lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
        self.assertEqual(self.host._diary_hour_chat(dt.date(2026, 11, 18),
                                                    "08:00", ""), [])

    def test_rewrite_budget_helpers(self):
        self.assertEqual(self.host._diary_rewrites("d", "08:00"), 0)
        self.assertEqual(self.host._bump_diary_rewrite("d", "08:00"), 1)
        self.assertEqual(self.host._bump_diary_rewrite("d", "08:00"), 2)
        self.assertEqual(self.host._diary_rewrites("d", "08:00"), 2)
        self.host.db.kv["diary_rewrites"] = {"d|08:00": None}
        self.assertEqual(self.host._diary_rewrites("d", "08:00"), 0)
        self.host.db.kv_set = lambda *a: (_ for _ in ()).throw(RuntimeError("x"))
        self.assertEqual(self.host._bump_diary_rewrite("d", "09:00"), 1)


class GuardTests(Base):
    def test_guard_cached_disabled_and_no_violation(self):
        with mock.patch("amo_location_rules.enabled", lambda plugin: False):
            self.assertEqual(self.host._guard_cached_location("正文", {}, "d", "08:00"),
                             "正文")
        with mock.patch("amo_location_rules.enabled", lambda plugin: True), \
                mock.patch("amo_location_rules.check", lambda *a: []):
            self.assertEqual(self.host._guard_cached_location("正文", {}, "d", "08:00"),
                             "正文")
        self.assertEqual(self.host._guard_cached_location("", {}, "d", "08:00"), "")

    def test_guard_cached_filters_and_saves(self):
        with mock.patch("amo_location_rules.enabled", lambda plugin: True), \
                mock.patch("amo_location_rules.check", lambda text, place, zone: ["被窝"]), \
                mock.patch("amo_location_rules.strip_violations",
                           lambda text, place, zone: "干净正文内容"), \
                mock.patch("amo_location_rules.fallback_line",
                           lambda place, zone: "兜底正文"):
            out = self.host._guard_cached_location(
                "我在被窝里", {"place": "公司", "zone": "office"}, "d", "08:00")
        self.assertEqual(out, "干净正文内容")
        self.assertEqual(self.host.db.saved[-1][0], "old")
        self.assertTrue(any("DIARY_GUARD" in m for _, m in self.logger.records))

    def test_guard_cached_short_filtered_uses_fallback(self):
        with mock.patch("amo_location_rules.enabled", lambda plugin: True), \
                mock.patch("amo_location_rules.check", lambda *a: ["x"]), \
                mock.patch("amo_location_rules.strip_violations", lambda *a: "短"), \
                mock.patch("amo_location_rules.fallback_line", lambda *a: "标准兜底"):
            out = self.host._guard_cached_location("原文", {}, "d", "08:00")
        self.assertEqual(out, "标准兜底")

    def test_guard_block_single_and_multi(self):
        with mock.patch("amo_location_rules.enabled", lambda plugin: True), \
                mock.patch("amo_location_rules.check", lambda *a: []), \
                mock.patch("amo_location_rules.strip_violations", lambda *a: ""), \
                mock.patch("amo_location_rules.fallback_line", lambda *a: ""):
            self.assertEqual(self.host._guard_block_location(
                "正文", {"place": "家", "zone": "home"}, "d", "bid"), "正文")
        with mock.patch("amo_location_rules.enabled", lambda plugin: True), \
                mock.patch("amo_location_rules.day_check", lambda *a: ["违规"]), \
                mock.patch("amo_location_rules.strip_day_violations",
                           lambda *a: "多地点正文内容"), \
                mock.patch("amo_location_rules.day_fallback_line", lambda: "兜底"):
            out = self.host._guard_block_location(
                "原文", {"place": "家"}, "d", "bid", places=["医院", "家"])
        self.assertEqual(out, "多地点正文内容")
        self.assertTrue(self.host.db.saved)

    def test_guard_block_short_filtered_and_save_error(self):
        with mock.patch("amo_location_rules.enabled", lambda plugin: True), \
                mock.patch("amo_location_rules.check", lambda *a: ["x"]), \
                mock.patch("amo_location_rules.strip_violations", lambda *a: ""), \
                mock.patch("amo_location_rules.fallback_line", lambda *a: ""):
            out = self.host._guard_block_location("原文", {}, "d", "bid")
        self.assertEqual(out, "原文")
        self.host.db.save_block_diary = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("x"))
        with mock.patch("amo_location_rules.enabled", lambda plugin: True), \
                mock.patch("amo_location_rules.check", lambda *a: ["x"]), \
                mock.patch("amo_location_rules.strip_violations", lambda *a: "干净正文内容"), \
                mock.patch("amo_location_rules.fallback_line", lambda *a: "兜底"):
            self.assertEqual(self.host._guard_block_location("原文", {}, "d", "bid"),
                             "干净正文内容")


class CacheReplyTests(Base):
    def setUp(self):
        super().setUp()
        self.host._guard_cached_location = lambda text, item, day, start: text
        self.host._guard_block_location = lambda text, item, day, bid, places=None: text

    def test_cached_reply_guards(self):
        self.assertIsNone(self.host._diary_cached_reply(
            "d", "08:00", {}, 1, True, [], {"status": "valid", "text": "文"}))
        self.assertIsNone(self.host._diary_cached_reply(
            "d", "08:00", {}, 1, False, [], None))
        self.assertIsNone(self.host._diary_cached_reply(
            "d", "08:00", {}, 1, False, [], {"status": "stale", "text": "文"}))
        self.assertIsNone(self.host._diary_cached_reply(
            "d", "08:00", {}, 2, False, [],
            {"status": "valid", "text": "文", "version": 1}))
        out = self.host._diary_cached_reply(
            "d", "08:00", {"mood": "开心", "place": "家", "activity": "休息"},
            2, False, [],
            {"status": "valid", "text": "正文", "version": 2})
        self.assertTrue(out["cached"])
        self.assertEqual(out["diary"], "正文")
        self.assertEqual(out["mood"], "开心")

    def test_cached_reply_sanitizes(self):
        self.host.engine = types.SimpleNamespace(
            _sanitize_hour_diary=lambda text, facts: "清洗后")
        out = self.host._diary_cached_reply(
            "d", "08:00", {}, 1, False, [{"fact": 1}],
            {"status": "valid", "text": "原文", "version": 1})
        self.assertEqual(out["diary"], "清洗后")
        self.assertEqual(self.host.db.saved[-1][0], "old")
        self.host.db.save_diary = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        out = self.host._diary_cached_reply(
            "d", "08:00", {}, 1, False, [1],
            {"status": "valid", "text": "原文", "version": 1})
        self.assertEqual(out["diary"], "清洗后")

    def test_block_cached_reply(self):
        self.assertIsNone(self.host._diary_block_cached_reply(
            "d", "bid", {}, 1, True, [], {"status": "valid", "text": "x"}, []))
        self.assertIsNone(self.host._diary_block_cached_reply(
            "d", "bid", {}, 1, False, [], {"status": "stale", "text": "x"}, []))
        self.assertIsNone(self.host._diary_block_cached_reply(
            "d", "bid", {}, 2, False, [],
            {"status": "valid", "text": "x", "version": 1}, []))
        out = self.host._diary_block_cached_reply(
            "d", "bid", {"mood": "平静", "activity": "散步"}, 1, False, [],
            {"status": "valid", "text": "正文", "version": 1}, ["公园"])
        self.assertTrue(out["cached"])
        self.assertEqual(out["block_id"], "bid")

    def test_block_cached_sanitizes(self):
        self.host.engine = types.SimpleNamespace(
            _sanitize_hour_diary=lambda text, facts: "清洗块")
        out = self.host._diary_block_cached_reply(
            "d", "bid", {"start": "08:00", "end": "09:00"}, 1, False, [1],
            {"status": "valid", "text": "原文", "version": 1}, [])
        self.assertEqual(out["diary"], "清洗块")
        self.assertEqual(self.host.db.saved[-1][0], "block")


class ReplyPayloadTests(Base):
    def test_archived_reply(self):
        self.assertIsNone(self.host._diary_archived_reply(
            "2999-01-01", None, {}, "", 1, "bid"))
        out = self.host._diary_archived_reply(
            "2020-01-01", {"text": "旧日记"}, {"mood": "平静"}, "休息", 1, "bid")
        self.assertTrue(out["archived"])
        self.assertEqual(out["diary"], "旧日记")
        out = self.host._diary_archived_reply("2020-01-01", None, {}, "休息", 1, "bid")
        self.assertIn("归档", out["empty_message"])

    def test_freeze_reply_with_record(self):
        out = self.host._diary_freeze_reply(
            "d", {"place": "家", "mood": "平静"}, {"text": "最后一版"},
            "08:00", "09:00", 2, "bid", "休息")
        self.assertTrue(out["frozen"])
        self.assertEqual(out["diary"], "最后一版")
        self.assertEqual(self.host.db.saved[-1][0], "block")
        self.assertEqual(self.host.db.saved[-1][4]["status"], "frozen")

    def test_freeze_reply_uses_db_and_empty(self):
        self.host.db.kv["block:bid"] = "库里的正文"
        out = self.host._diary_freeze_reply(
            "d", {}, {}, "08:00", "09:00", 1, "bid", "休息")
        self.assertEqual(out["diary"], "库里的正文")
        self.host.db.kv.pop("block:bid")
        self.host.db.save_block_diary = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("x"))
        out = self.host._diary_freeze_reply(
            "d", {}, {"text": ""}, "08:00", "09:00", 1, "bid", "休息")
        self.assertEqual(out["diary"], "")
        self.assertIn("锁定", out["empty_message"])

    def test_sleep_reply(self):
        out = self.host._diary_sleep_reply(
            "d", {"place": "家", "mood": "平静"}, "23:00", "24:00", 1, "bid", "睡觉")
        self.assertTrue(out["sleep"])
        self.assertTrue(out["placeholder"])
        self.assertIn("呼呼大睡", out["diary"])
        self.host.db.save_block_diary = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("x"))
        self.assertTrue(self.host._diary_sleep_reply("d", {}, "23:00", "24:00",
                                                     1, "bid", "睡觉")["sleep"])


class CurrentActivityTests(Base):
    def test_transit_wins(self):
        with mock.patch("amo_transit.transit_activity",
                        lambda plugin: {"action": "在路上", "place": "公司"}):
            data, reason = self.host.current_activity_info()
        self.assertEqual(reason, "transit")
        self.assertEqual(data["action"], "在路上")

    def test_transit_dropped_when_sick_elsewhere(self):
        self.host.health = {"status": "发烧"}
        with mock.patch("amo_transit.transit_activity",
                        lambda plugin: {"action": "在路上", "place": "公司"}), \
                mock.patch.object(dmod, "_jit_activity", lambda plugin: {"action": "休息"}), \
                mock.patch.object(dmod, "_today_items", lambda plugin, day: []), \
                mock.patch.object(dmod, "_pick_covering", lambda plugin, items, cur: (None, "")), \
                mock.patch.object(dmod, "_pick_stale", lambda plugin, items, cur: None), \
                mock.patch.object(dmod, "_pick_yesterday_hour", lambda plugin, cur: None), \
                mock.patch.object(dmod, "minutes_now", lambda: 600):
            data, reason = self.host.current_activity_info()
        self.assertEqual(reason, "jit")
        self.assertEqual(data["action"], "休息")

    def test_priority_chain(self):
        with mock.patch("amo_transit.transit_activity", lambda plugin: None), \
                mock.patch.object(dmod, "_jit_activity", lambda plugin: None), \
                mock.patch.object(dmod, "_today_items", lambda plugin, day: [{"start": "08:00"}]), \
                mock.patch.object(dmod, "_pick_covering",
                                  lambda plugin, items, cur: ({"activity": "覆盖"}, "covering")), \
                mock.patch.object(dmod, "minutes_now", lambda: 600):
            self.assertEqual(self.host.current_activity_info(), ({"activity": "覆盖"}, "covering"))
        with mock.patch("amo_transit.transit_activity", lambda plugin: None), \
                mock.patch.object(dmod, "_jit_activity", lambda plugin: None), \
                mock.patch.object(dmod, "_today_items", lambda plugin, day: [{"start": "08:00"}]), \
                mock.patch.object(dmod, "_pick_covering", lambda plugin, items, cur: (None, "")), \
                mock.patch.object(dmod, "_pick_stale", lambda plugin, items, cur: {"activity": "旧块"}), \
                mock.patch.object(dmod, "minutes_now", lambda: 600):
            self.assertEqual(self.host.current_activity_info()[1], "stale_slot")
        with mock.patch("amo_transit.transit_activity", lambda plugin: None), \
                mock.patch.object(dmod, "_jit_activity", lambda plugin: None), \
                mock.patch.object(dmod, "_today_items", lambda plugin, day: []), \
                mock.patch.object(dmod, "_pick_covering", lambda plugin, items, cur: (None, "")), \
                mock.patch.object(dmod, "_pick_stale", lambda plugin, items, cur: None), \
                mock.patch.object(dmod, "_pick_yesterday_hour", lambda plugin, cur: {"activity": "昨天"}), \
                mock.patch.object(dmod, "minutes_now", lambda: 600):
            self.assertEqual(self.host.current_activity_info()[1], "yesterday_same_hour")

    def test_no_schedule_fallback_and_current_activity(self):
        self.host.p.config["debug_prompt"] = True
        with mock.patch("amo_transit.transit_activity", lambda plugin: None), \
                mock.patch.object(dmod, "_jit_activity", lambda plugin: None), \
                mock.patch.object(dmod, "_today_items", lambda plugin, day: []), \
                mock.patch.object(dmod, "_pick_covering", lambda plugin, items, cur: (None, "")), \
                mock.patch.object(dmod, "_pick_stale", lambda plugin, items, cur: None), \
                mock.patch.object(dmod, "_pick_yesterday_hour", lambda plugin, cur: None), \
                mock.patch.object(dmod, "minutes_now", lambda: 600):
            data, reason = self.host.current_activity_info()
        self.assertEqual(reason, "no_schedule")
        self.assertEqual(data["_source"], "no_schedule")
        self.assertTrue(any("PHOTO_DEBUG" in m for _, m in self.logger.records))
        with mock.patch.object(self.host, "current_activity_info",
                               lambda: ({"activity": "当前"}, "x")):
            self.assertEqual(self.host.current_activity(), {"activity": "当前"})

    def test_transit_error_is_ignored(self):
        with mock.patch("amo_transit.transit_activity",
                        lambda plugin: (_ for _ in ()).throw(RuntimeError("x"))), \
                mock.patch.object(dmod, "_jit_activity", lambda plugin: {"action": "jit"}), \
                mock.patch.object(dmod, "_today_items", lambda plugin, day: []):
            self.assertEqual(self.host.current_activity_info()[1], "jit")

    def test_hour_fallback_bands(self):
        cases = [(3, "睡觉"), (7, "起床收拾"), (9, "上班"), (12, "吃午饭"),
                 (14, "上班"), (18, "吃晚饭"), (20, "休息"), (23, "准备睡觉")]
        for hour, expected in cases:
            with mock.patch.object(dmod, "_now",
                                   lambda h=hour: dt.datetime(2026, 11, 18, h, 0)):
                data = self.host._hour_fallback_activity()
            self.assertEqual(data["activity"], expected, hour)
            self.assertIn("zone", data)


class ReconcileTests(Base):
    def test_non_today_and_same_signature(self):
        none = {"activity_now": False, "current_action": False, "transit": False}
        with mock.patch.object(dmod, "today_str", lambda: "2026-11-18"):
            self.assertEqual(self.host._reconcile_manual_schedule_edit(
                "2026-11-17", [], []), none)
            self.assertEqual(self.host._reconcile_manual_schedule_edit(
                "2026-11-18", [{"start": "08:00"}], [{"start": "08:00"}]), none)

    def test_clears_stale_activity_action_transit(self):
        self.host.db.kv["activity_now"] = {"activity": "在公司", "start": "09:00"}
        self.host.db.kv["current_action"] = {"action": "上班", "place": "公司"}
        self.host.db.kv["transition"] = {"steps": [1], "to": "医院"}
        with mock.patch.object(dmod, "today_str", lambda: "2026-11-18"), \
                mock.patch.object(dmod, "minutes_now", lambda: 600):
            cleared = self.host._reconcile_manual_schedule_edit(
                "2026-11-18",
                [{"start": "09:00", "end": "18:00", "activity": "上班", "place": "公司"}],
                [{"start": "09:00", "end": "18:00", "activity": "休息", "place": "家"}])
        self.assertTrue(cleared["activity_now"])
        self.assertTrue(cleared["current_action"])
        self.assertTrue(cleared["transit"])
        self.assertEqual(self.host.db.kv["activity_now"], {})
        self.assertEqual(self.host.db.kv["current_action"], {})
        self.assertEqual(self.host.db.kv["transition"], {})

    def test_keeps_matching_current_block(self):
        self.host.db.kv["activity_now"] = {"activity": "上班", "start": "09:00",
                                           "end": "18:00", "place": "公司"}
        self.host.db.kv["current_action"] = {"action": "上班", "place": "公司"}
        self.host.db.kv["transition"] = {"steps": [1], "to": "公司"}
        with mock.patch.object(dmod, "today_str", lambda: "2026-11-18"), \
                mock.patch.object(dmod, "minutes_now", lambda: 600):
            cleared = self.host._reconcile_manual_schedule_edit(
                "2026-11-18",
                [{"start": "09:00", "end": "18:00", "activity": "上班", "place": "公司"},
                 {"start": "20:00", "end": "21:00", "activity": "休息", "place": "家"}],
                [{"start": "09:00", "end": "18:00", "activity": "上班", "place": "公司"},
                 {"start": "20:00", "end": "21:00", "activity": "散步", "place": "公园"}])
        self.assertFalse(any(cleared.values()))
        self.assertTrue(self.host.db.kv["current_action"])

    def test_kv_errors_are_swallowed(self):
        class BoomDB(FakeDB):
            def kv_get(self, key, default=None):
                raise RuntimeError("x")

        self.host.db = BoomDB()
        with mock.patch.object(dmod, "today_str", lambda: "2026-11-18"), \
                mock.patch.object(dmod, "minutes_now", lambda: 600):
            cleared = self.host._reconcile_manual_schedule_edit(
                "2026-11-18", [{"start": "08:00"}], [{"start": "09:00"}])
        self.assertFalse(any(cleared.values()))


class MiscTests(Base):
    def test_busy_level(self):
        self.assertEqual(DiaryStateMixin.busy_level({"category": "sleep"}), 1.0)
        self.assertEqual(DiaryStateMixin.busy_level({"category": "work"}), 0.75)
        self.assertEqual(DiaryStateMixin.busy_level({"category": "study"}), 0.75)
        self.assertLess(DiaryStateMixin.busy_level({"category": "rest"}), 0.75)
        self.assertEqual(DiaryStateMixin.busy_level({}), 0.1)

    def test_photo_info_toggle(self):
        self.host._photo_info("[PHOTO_DEBUG] x")
        self.assertEqual([m for _, m in self.logger.records if "PHOTO_DEBUG" in m], [])
        self.host.p.config["photo_debug"] = True
        self.host._photo_info("[PHOTO_DEBUG] y")
        self.assertTrue(any("PHOTO_DEBUG" in m for _, m in self.logger.records))
        self.host.p.cfg_bool = lambda key, default=False: (_ for _ in ()).throw(
            RuntimeError("x"))
        self.host._photo_info("z")


if __name__ == "__main__":
    unittest.main()
