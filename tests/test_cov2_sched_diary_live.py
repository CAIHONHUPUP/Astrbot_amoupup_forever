# -*- coding: utf-8 -*-
"""cov2: amo_state_diary 的「连载小日记」模块级函数（读写/去重/候选/tick/收束/清理）。"""
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


class FakeLogger:
    def __init__(self):
        self.records = []

    def _add(self, level, message):
        self.records.append((level, str(message)))

    def warning(self, message, *a, **k):
        self._add("warning", message)

    def info(self, message, *a, **k):
        self._add("info", message)

    def debug(self, message, *a, **k):
        self._add("debug", message)

    def error(self, message, *a, **k):
        self._add("error", message)


class FakeDB:
    def __init__(self):
        self.kv = {}
        self.deleted = []
        self.fail_set = False
        self.fail_get = False
        self.fail_delete = False

    def kv_get(self, key, default=None):
        if self.fail_get:
            raise RuntimeError("读失败")
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        if self.fail_set:
            raise RuntimeError("写失败")
        self.kv[key] = value

    def kv_delete(self, key):
        if self.fail_delete:
            raise RuntimeError("删失败")
        self.deleted.append(key)
        self.kv.pop(key, None)


class FakePlugin:
    def __init__(self, **cfg):
        self.db = FakeDB()
        self.config = {"diary_enabled": True, "diary_live_enabled": True,
                       "diary_live_debounce_minutes": 7}
        self.config.update(cfg)
        self.state = None
        self._diary_live_running = False

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))


def moment(hour=12, minute=0, day=18):
    return dt.datetime(2026, 11, day, hour, minute)


class Base(unittest.TestCase):
    def setUp(self):
        self.plugin = FakePlugin()
        self.logger = FakeLogger()
        self._saved_logger = dmod.logger
        dmod.logger = self.logger

    def tearDown(self):
        dmod.logger = self._saved_logger


class BasicHelpers(Base):
    def test_key_and_norm_and_day_list(self):
        self.assertEqual(dmod.diary_live_key("2026-11-18"), "diary_live:2026-11-18")
        self.assertEqual(dmod.diary_live_key(""), "diary_live:")
        self.assertEqual(dmod._live_norm(" 你 好，呀！"), "你好呀")
        self.assertEqual(dmod._live_norm(None), "")
        self.assertEqual(dmod._live_day_list(["2026-11-18", "2026-11-18", "x", ""]),
                         ["2026-11-18"])
        self.assertEqual(dmod._live_day_list("not-list"), [])

    def test_live_days_error(self):
        self.plugin.db.fail_get = True
        self.assertEqual(dmod._live_days(self.plugin, "k"), [])
        self.assertTrue(any("索引读取失败" in m for _, m in self.logger.records))

    def test_touch_day_dedupes_and_sorts(self):
        dmod._live_touch_day(self.plugin, "days", "2026-11-18")
        dmod._live_touch_day(self.plugin, "days", "2026-11-17")
        dmod._live_touch_day(self.plugin, "days", "2026-11-18")
        self.assertEqual(self.plugin.db.kv["days"], ["2026-11-17", "2026-11-18"])
        dmod._live_touch_day(self.plugin, "days", "")
        self.assertEqual(self.plugin.db.kv["days"], ["2026-11-17", "2026-11-18"])

    def test_touch_day_keeps_eight_and_swallows_error(self):
        for day in range(1, 12):
            dmod._live_touch_day(self.plugin, "days", "2026-11-%02d" % day)
        self.assertEqual(len(self.plugin.db.kv["days"]), 8)
        self.plugin.db.fail_set = True
        dmod._live_touch_day(self.plugin, "days", "2026-12-01")
        self.assertTrue(any("索引写入失败" in m for _, m in self.logger.records))


class EntriesTests(Base):
    def test_entries_filters_bad_rows(self):
        self.plugin.db.kv["diary_live:2026-11-18"] = [
            {"text": " 第一段 ", "ts": "12", "event_id": "e1", "mood": "开心"},
            {"text": ""}, {"no_text": 1}, "not-dict",
            {"text": "坏时间", "ts": "abc"},
        ]
        rows = dmod.diary_live_entries(self.plugin, "2026-11-18")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"ts": 12.0, "event_id": "e1",
                                   "text": "第一段", "mood": "开心"})
        self.assertEqual(rows[1]["ts"], 0.0)

    def test_entries_bad_value_and_error(self):
        self.plugin.db.kv["diary_live:x"] = {"not": "list"}
        self.assertEqual(dmod.diary_live_entries(self.plugin, "x"), [])
        self.plugin.db.fail_get = True
        self.assertEqual(dmod.diary_live_entries(self.plugin, "x"), [])
        self.assertTrue(any("连载读取失败" in m for _, m in self.logger.records))

    def test_append_empty_returns_empty(self):
        self.assertEqual(dmod.diary_live_append(self.plugin, "2026-11-18", "   "), {})

    def test_append_and_duplicates(self):
        first = dmod.diary_live_append(self.plugin, "2026-11-18", "去吃午饭了",
                                       event_id="e1", mood="平静", now=moment())
        self.assertEqual(first["appended"], 1)
        self.assertEqual(first["entry"]["event_id"], "e1")
        self.assertEqual(first["entry"]["mood"], "平静")
        self.assertIn("diary_live:2026-11-18", self.plugin.db.kv)
        self.assertIn("2026-11-18", self.plugin.db.kv[dmod.DIARY_LIVE_DAYS_KEY])

        same_id = dmod.diary_live_append(self.plugin, "2026-11-18", "另一句",
                                         event_id="e1", now=moment())
        self.assertTrue(same_id["duplicate"])
        same_text = dmod.diary_live_append(self.plugin, "2026-11-18", " 去吃 午饭了！",
                                           event_id="e2", now=moment())
        self.assertTrue(same_text["duplicate"])
        self.assertEqual(len(dmod.diary_live_entries(self.plugin, "2026-11-18")), 1)

    def test_append_falls_back_timestamp_and_swallows_write_error(self):
        now = types.SimpleNamespace(date=lambda: dt.date(2026, 11, 18))
        out = dmod.diary_live_append(self.plugin, "", "没有时间戳", now=now)
        self.assertEqual(out["appended"], 1)
        self.assertGreater(out["entry"]["ts"], 0)

        self.plugin.db.fail_set = True
        self.assertEqual(dmod.diary_live_append(self.plugin, "2026-11-19", "写不进去",
                                                now=moment()), {})
        self.assertTrue(any("连载写入失败" in m for _, m in self.logger.records))


class MoodTimeComposeTests(Base):
    def test_mood_from_event_state_and_default(self):
        self.assertEqual(dmod.diary_live_mood(self.plugin, {"mood": "开心"}), "开心")
        state = types.SimpleNamespace(dominant_mood=lambda: "疲惫")
        self.plugin.state = state
        self.assertEqual(dmod.diary_live_mood(self.plugin, {}), "疲惫")
        state = types.SimpleNamespace(dominant_mood=lambda: (_ for _ in ()).throw(RuntimeError("x")))
        self.plugin.state = state
        self.assertEqual(dmod.diary_live_mood(self.plugin, {}), "平静")
        self.plugin.state = types.SimpleNamespace()
        self.assertEqual(dmod.diary_live_mood(self.plugin, {}), "平静")

    def test_time_hint_bands(self):
        cases = [(3, "天还没亮"), (8, "早上"), (12, "中午"), (15, "下午"),
                 (20, "晚上"), (23, "深夜")]
        for hour, expected in cases:
            self.assertEqual(dmod.diary_live_time_hint({}, moment(hour=hour)), expected)
        self.assertEqual(dmod.diary_live_time_hint({"end": 12 * 60 + 30}), "中午")
        self.assertEqual(dmod.diary_live_time_hint({"start": 18 * 60}), "晚上")

    def test_compose_variants(self):
        self.assertEqual(dmod.diary_live_compose(self.plugin, {"diary": "现成的。"}), "现成的。")
        self.assertEqual(dmod.diary_live_compose(self.plugin, {"activity": "去开会"},
                                                 now=moment(10)), "早上，我去开会。")
        self.assertEqual(dmod.diary_live_compose(self.plugin, {"activity": "写代码",
                                                               "place": "公司"},
                                                 now=moment(15)), "下午，我在公司，写代码。")
        self.assertEqual(dmod.diary_live_compose(self.plugin, {"place": "公园"},
                                                 now=moment(19)), "晚上，我在公园。")
        self.assertEqual(dmod.diary_live_compose(self.plugin, {}, now=moment(13)),
                         "中午，我还在忙自己的事。")
        self.assertEqual(dmod.diary_live_compose(self.plugin, {"diary": "没有句号"},
                                                 now=moment()), "没有句号。")

    def test_gate_passthrough_and_failure(self):
        with mock.patch("amo_diary_guard.narrative_check",
                        lambda text, view, kind, plugin=None, now=None: (text + "！", ["c"])):
            text, contradictions = dmod.diary_live_gate(self.plugin, "2026-11-18", "原文")
        self.assertEqual(text, "原文！")
        self.assertEqual(contradictions, ["c"])
        with mock.patch("amo_diary_guard.narrative_check",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("守卫挂了"))):
            text, contradictions = dmod.diary_live_gate(self.plugin, "2026-11-18", "原文")
        self.assertEqual((text, contradictions), ("原文", []))
        self.assertTrue(any("事实门失败" in m for _, m in self.logger.records))


class PendingSkipConfigTests(Base):
    def test_pending_read_write_clear(self):
        self.assertEqual(dmod.diary_live_pending(self.plugin, "2026-11-18"), {})
        dmod._live_set_pending(self.plugin, "2026-11-18", {"due": 5})
        self.assertEqual(dmod.diary_live_pending(self.plugin, "2026-11-18"), {"due": 5})
        dmod._live_clear_pending(self.plugin, "2026-11-18")
        self.assertEqual(dmod.diary_live_pending(self.plugin, "2026-11-18"), {})
        self.plugin.db.kv["diary_live_pending:bad"] = ["not-dict"]
        self.assertEqual(dmod.diary_live_pending(self.plugin, "bad"), {})
        self.plugin.db.fail_get = True
        self.assertEqual(dmod.diary_live_pending(self.plugin, "bad"), {})

    def test_pending_clear_falls_back_to_set(self):
        class NoDeleteDB(FakeDB):
            def kv_delete(self, key):
                raise RuntimeError("不支持删除")

        self.plugin.db = NoDeleteDB()
        dmod._live_set_pending(self.plugin, "2026-11-18", {"due": 1})
        dmod._live_clear_pending(self.plugin, "2026-11-18")
        self.assertEqual(self.plugin.db.kv["diary_live_pending:2026-11-18"], {})

    def test_skip_ids_and_add(self):
        self.assertEqual(dmod.diary_live_skip_ids(self.plugin, "d"), [])
        self.plugin.db.kv["diary_live_skip:d"] = ["e1", "", None]
        self.assertEqual(dmod.diary_live_skip_ids(self.plugin, "d"), ["e1"])
        self.plugin.db.kv["diary_live_skip:d"] = "bad"
        self.assertEqual(dmod.diary_live_skip_ids(self.plugin, "d"), [])
        self.assertEqual(dmod._live_add_skip(self.plugin, "d", ""), [])
        self.assertEqual(dmod._live_add_skip(self.plugin, "d", "e2"), ["e2"])
        self.assertEqual(dmod._live_add_skip(self.plugin, "d", "e2"), ["e2"])
        self.assertEqual(dmod.diary_live_skip_ids(self.plugin, "d"), ["e2"])

    def test_enabled_and_debounce(self):
        self.assertTrue(dmod.diary_live_enabled(self.plugin))
        self.plugin.config["diary_enabled"] = False
        self.assertFalse(dmod.diary_live_enabled(self.plugin))
        self.plugin.config["diary_enabled"] = True
        self.plugin.config["diary_live_enabled"] = False
        self.assertFalse(dmod.diary_live_enabled(self.plugin))
        self.plugin.config["diary_live_enabled"] = True

        self.assertEqual(dmod.diary_live_debounce_minutes(self.plugin), 7)
        self.plugin.config["diary_live_debounce_minutes"] = 1
        self.assertEqual(dmod.diary_live_debounce_minutes(self.plugin), 5)
        self.plugin.config["diary_live_debounce_minutes"] = 99
        self.assertEqual(dmod.diary_live_debounce_minutes(self.plugin), 10)
        self.plugin.config["diary_live_debounce_minutes"] = "bad"
        self.assertEqual(dmod.diary_live_debounce_minutes(self.plugin), 7)


class CandidatesTickTests(Base):
    def _view(self, events):
        return {"events": events}

    def test_candidates_filtering_and_sort(self):
        events = [
            {"event_id": "e2", "type": "日程", "start": 600, "end": 660,
             "activity": "午饭", "place": "食堂"},
            {"event_id": "e1", "type": "日程", "start": 480, "end": 540,
             "activity": "开会"},
            {"event_id": "", "type": "日程", "start": 100, "end": 200},
            {"event_id": "e3", "type": "账本", "start": 100, "end": 200},
            {"event_id": "e4", "type": "日程", "start": "bad", "end": 200},
            {"event_id": "e5", "type": "日程", "start": 700, "end": 800},
            {"event_id": "e6", "type": "日程", "start": 200, "end": 300},
            {"event_id": "e7", "type": "日程", "start": 300, "end": 400,
             "activity": " "},
            "not-dict",
        ]
        dmod.diary_live_append(self.plugin, "2026-11-18", "已写过的开会",
                               event_id="e6", now=moment())
        dmod._live_add_skip(self.plugin, "2026-11-18", "e7")
        with mock.patch.object(dmod, "_live_day_view", lambda p, d, n: self._view(events)):
            rows = dmod.diary_live_candidates(self.plugin, "2026-11-18", moment(12))
        self.assertEqual([row["event_id"] for row in rows], ["e1", "e2"])

    def test_live_ready_variants(self):
        self.assertTrue(dmod._live_ready({}, moment(12), 7))
        self.assertTrue(dmod._live_ready({"start": 700}, moment(12), 7))
        self.assertFalse(dmod._live_ready({"end": 715}, moment(12), 7))
        self.assertTrue(dmod._live_ready({"end": 712}, moment(12), 7))

    def test_tick_disabled_running_and_empty(self):
        self.plugin.config["diary_enabled"] = False
        self.assertEqual(dmod.diary_live_tick(self.plugin, moment()),
                         {"skipped": "disabled"})
        self.plugin.config["diary_enabled"] = True
        self.plugin._diary_live_running = True
        self.assertEqual(dmod.diary_live_tick(self.plugin, moment()),
                         {"skipped": "running"})
        self.plugin._diary_live_running = False
        with mock.patch.object(dmod, "_live_day_view", lambda p, d, n: {"events": []}):
            out = dmod.diary_live_tick(self.plugin, moment())
        self.assertEqual(out, {"day": "2026-11-18", "pending": 0, "appended": 0})

    def test_tick_clears_stale_pending(self):
        dmod._live_set_pending(self.plugin, "2026-11-18", {"due": 1})
        with mock.patch.object(dmod, "_live_day_view", lambda p, d, n: {"events": []}):
            out = dmod.diary_live_tick(self.plugin, moment())
        self.assertEqual(out["pending"], 0)
        self.assertNotIn("diary_live_pending:2026-11-18", self.plugin.db.kv)

    def test_tick_first_discovery_schedules(self):
        event = {"event_id": "e1", "type": "日程", "start": 480, "end": 540,
                 "activity": "开会"}
        with mock.patch.object(dmod, "_live_day_view", lambda p, d, n: {"events": [event]}):
            out = dmod.diary_live_tick(self.plugin, moment(12))
        self.assertEqual(out["scheduled"], 1)
        self.assertEqual(out["appended"], 0)
        pending = dmod.diary_live_pending(self.plugin, "2026-11-18")
        self.assertEqual(pending["event_ids"], ["e1"])
        self.assertEqual(pending["due"], moment(12).timestamp() + 7 * 60)

    def test_tick_waits_for_due(self):
        dmod._live_set_pending(self.plugin, "2026-11-18",
                               {"due": moment(12).timestamp() + 600})
        event = {"event_id": "e1", "type": "日程", "start": 480, "end": 540,
                 "activity": "开会"}
        with mock.patch.object(dmod, "_live_day_view", lambda p, d, n: {"events": [event]}):
            out = dmod.diary_live_tick(self.plugin, moment(12))
        self.assertTrue(out["waiting"])

    def test_tick_appends_ready_batch(self):
        dmod._live_set_pending(self.plugin, "2026-11-18", {"due": 1})
        event = {"event_id": "e1", "type": "日程", "start": 480, "end": 540,
                 "activity": "开会", "place": "公司"}
        def gate(plugin, day, text, now=None):
            return text, []

        with mock.patch.object(dmod, "_live_day_view", lambda p, d, n: {"events": [event]}),                 mock.patch.object(dmod, "diary_live_gate", gate):
            out = dmod.diary_live_tick(self.plugin, moment(12))
        self.assertEqual(out["appended"], 1)
        self.assertEqual(out["batch"], 1)
        rows = dmod.diary_live_entries(self.plugin, "2026-11-18")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], "e1")
        band = dmod.diary_live_time_hint(event, moment(12))
        self.assertEqual(rows[0]["text"], band + "，我在公司，开会。")
        self.assertNotIn("diary_live_pending:2026-11-18", self.plugin.db.kv)

    def test_tick_blocks_contradiction(self):
        dmod._live_set_pending(self.plugin, "2026-11-18", {"due": 1})
        events = [{"event_id": "e1", "type": "日程", "start": 480, "end": 540,
                   "activity": "开会"}]
        with mock.patch.object(dmod, "_live_day_view", lambda p, d, n: {"events": events}), \
                mock.patch.object(dmod, "diary_live_gate",
                                  lambda p, d, text, now=None: (text, ["矛盾"])):
            out = dmod.diary_live_tick(self.plugin, moment(12))
        self.assertEqual(out["appended"], 0)
        self.assertEqual(out["blocked"], 1)
        self.assertEqual(dmod.diary_live_skip_ids(self.plugin, "2026-11-18"), ["e1"])
        self.assertTrue(any("跳过矛盾段落" in m for _, m in self.logger.records))

    def test_tick_duplicate_text_marks_skip(self):
        dmod._live_set_pending(self.plugin, "2026-11-18", {"due": 1})
        events = [{"event_id": "e2", "type": "日程", "start": 540, "end": 600,
                   "activity": "写代码"}]
        band = dmod.diary_live_time_hint(events[0], moment(12))
        dmod.diary_live_append(self.plugin, "2026-11-18", band + "，我写代码。",
                               event_id="old", now=moment())
        with mock.patch.object(dmod, "_live_day_view", lambda p, d, n: {"events": events}), \
                mock.patch.object(dmod, "diary_live_gate",
                                  lambda p, d, text, now=None: (text, [])):
            out = dmod.diary_live_tick(self.plugin, moment(12))
        self.assertEqual(out["appended"], 0)
        self.assertEqual(dmod.diary_live_skip_ids(self.plugin, "2026-11-18"), ["e2"])

    def test_tick_leaves_rest_scheduled(self):
        dmod._live_set_pending(self.plugin, "2026-11-18", {"due": 1})
        events = [{"event_id": "e%d" % i, "type": "日程", "start": 60 * i,
                   "end": 60 * i + 30, "activity": "事情%d" % i} for i in range(1, 7)]
        with mock.patch.object(dmod, "_live_day_view", lambda p, d, n: {"events": events}):
            out = dmod.diary_live_tick(self.plugin, moment(12))
        self.assertEqual(out["batch"], 5)
        self.assertEqual(out["scheduled"], 1)
        self.assertTrue(dmod.diary_live_pending(self.plugin, "2026-11-18"))

    def test_tick_swallows_exception_and_resets_running(self):
        with mock.patch.object(dmod, "diary_live_candidates",
                               lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))):
            out = dmod.diary_live_tick(self.plugin, moment())
        self.assertIn("boom", out["error"])
        self.assertFalse(self.plugin._diary_live_running)


class SnapshotFinalizeTests(Base):
    def test_merge_and_snapshot(self):
        rows = [{"text": "第一段。"}, {"text": " 第一段！ "}, {"text": "第二段。"}, {"text": ""}]
        self.assertEqual(dmod.diary_live_merge(rows), "第一段。\n\n第二段。")
        self.assertEqual(dmod.diary_live_merge([]), "")
        dmod.diary_live_append(self.plugin, "2026-11-18", "第一段。", event_id="e1",
                               now=moment())
        dmod.diary_live_append(self.plugin, "2026-11-18", "第二段。", event_id="e2",
                               now=moment())
        dmod._live_set_pending(self.plugin, "2026-11-18", {"due": 9})
        snap = dmod.diary_live_snapshot(self.plugin, "2026-11-18")
        self.assertEqual(snap["count"], 2)
        self.assertEqual(snap["text"], "第一段。\n\n第二段。")
        self.assertTrue(snap["pending"])

    def test_finalize_empty_and_no_rows(self):
        self.assertEqual(dmod.diary_live_finalize(self.plugin, ""), {})
        dmod._live_set_pending(self.plugin, "2026-11-18", {"due": 1})
        out = dmod.diary_live_finalize(self.plugin, "2026-11-18")
        self.assertEqual(out, {"day": "2026-11-18", "count": 0})
        self.assertNotIn("diary_live_pending:2026-11-18", self.plugin.db.kv)

    def test_finalize_archives_and_deletes(self):
        dmod.diary_live_append(self.plugin, "2026-11-18", "第一段。", event_id="e1",
                               now=moment())
        dmod.diary_live_append(self.plugin, "2026-11-18", "第二段。", event_id="e2",
                               now=moment())
        dmod._live_set_pending(self.plugin, "2026-11-18", {"due": 1})
        dmod._live_add_skip(self.plugin, "2026-11-18", "e9")
        out = dmod.diary_live_finalize(self.plugin, "2026-11-18", moment(20))
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["text"], "第一段。\n\n第二段。")
        archive = self.plugin.db.kv["diary_live_archive:2026-11-18"]
        self.assertEqual(archive["text"], "第一段。\n\n第二段。")
        self.assertIn("2026-11-18", self.plugin.db.kv[dmod.DIARY_LIVE_ARCHIVE_DAYS_KEY])
        self.assertNotIn("diary_live:2026-11-18", self.plugin.db.kv)
        self.assertNotIn("diary_live_pending:2026-11-18", self.plugin.db.kv)
        self.assertNotIn("diary_live_skip:2026-11-18", self.plugin.db.kv)

    def test_finalize_archive_error(self):
        dmod.diary_live_append(self.plugin, "2026-11-18", "第一段。", event_id="e1",
                               now=moment())
        self.plugin.db.fail_set = True
        out = dmod.diary_live_finalize(self.plugin, "2026-11-18", moment(20))
        self.assertEqual(out["count"], 1)
        self.assertTrue(out["error"])

    def test_purge_removes_old_days(self):
        self.plugin.db.kv[dmod.DIARY_LIVE_DAYS_KEY] = ["2026-11-10", "2026-11-17"]
        self.plugin.db.kv[dmod.DIARY_LIVE_ARCHIVE_DAYS_KEY] = ["2026-11-01", "2026-11-17"]
        self.plugin.db.kv[dmod.diary_live_key("2026-11-10")] = []
        self.plugin.db.kv[dmod.DIARY_LIVE_ARCHIVE_PREFIX + "2026-11-01"] = {}
        removed = dmod.diary_live_purge(self.plugin, moment(20))
        self.assertEqual(removed, 4)
        self.assertEqual(self.plugin.db.kv[dmod.DIARY_LIVE_DAYS_KEY], ["2026-11-17"])
        self.assertEqual(self.plugin.db.kv[dmod.DIARY_LIVE_ARCHIVE_DAYS_KEY], ["2026-11-17"])
        self.assertNotIn(dmod.diary_live_key("2026-11-10"), self.plugin.db.kv)
        self.assertNotIn(dmod.DIARY_LIVE_ARCHIVE_PREFIX + "2026-11-01", self.plugin.db.kv)


if __name__ == "__main__":
    unittest.main()
