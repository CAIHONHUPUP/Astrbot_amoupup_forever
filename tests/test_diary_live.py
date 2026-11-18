# -*- coding: utf-8 -*-
"""当天连载小记（diary-live）：只追加、debounce、重启恢复、次日收束、事实门、event_id。

覆盖：
1. 追加不覆盖 / event_id 幂等 / 同文去重；
2. debounce（5-10 分钟夹取）、到点只写一条、单飞；
3. 重启恢复（pending 持久化在 kv）；
4. 次日最终版合并去重 + 归档 + 连载删除 + 只留 2 天；
5. 事实门：和 build_day_view 矛盾时不写，且记跳过不再重试；
6. event_id/mood 落库、第一人称拼句、空/坏数据/读库异常都不崩；
7. 契约：/diary/read 暴露连载、后台调度真的挂上了 tick/finalize。
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_state_diary as live  # noqa: E402

DAY = "2026-11-17"
NOW = _dt.datetime(2026, 11, 17, 16, 0)
HEALTHY = {"status": "健康", "sick": False}
SICK = {"status": "感冒", "sick": True}

MORNING = {"start": "08:00", "end": "09:30", "activity": "在家赖床", "place": "家",
           "category": "rest", "zone": "home", "event_id": "ev_morning"}
LUNCH = {"start": "12:00", "end": "13:00", "activity": "在家煮面", "place": "家",
         "category": "meal", "zone": "home", "event_id": "ev_lunch"}
HOSPITAL = {"start": "14:00", "end": "15:00", "activity": "去医院排队看诊", "place": "医院",
            "category": "health", "zone": "hospital", "event_id": "ev_hosp"}
LATE = {"start": "17:00", "end": "18:00", "activity": "准备晚饭", "place": "家",
        "category": "meal", "zone": "home", "event_id": "ev_late"}


class FakeDB:
    def __init__(self, kv=None, diaries=None, schedule=None):
        self.kv = dict(kv or {})
        self.diaries = dict(diaries or {})
        self.schedule = dict(schedule or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def kv_delete(self, key):
        return self.kv.pop(key, None) is not None

    def get_schedule(self, day):
        return list(self.schedule.get(day) or [])

    def list_block_diaries(self, day):
        return list(self.diaries.get(day) or [])


class BrokenDB(FakeDB):
    """读库必炸：连载读取/调度都必须吞掉异常。"""

    def kv_get(self, key, default=None):
        raise RuntimeError("db exploded")

    def kv_set(self, key, value):
        raise RuntimeError("db exploded")

    def kv_delete(self, key):
        raise RuntimeError("db exploded")


class FakeState:
    def __init__(self, schedule=None, health=None, db=None):
        self.schedule = dict(schedule or {})
        self.health = dict(health or HEALTHY)
        self.db = db

    def health_info(self):
        return dict(self.health)

    def dominant_mood(self):
        return "依恋"


class FakeWallet:
    def ledger(self, limit=200):
        return []


class FakePlugin:
    def __init__(self, blocks=None, diaries=None, health=None, kv=None, flags=None,
                 debounce=None):
        blocks = list(blocks or [])
        self.db = FakeDB(kv=kv, diaries={DAY: list(diaries or [])}, schedule={DAY: blocks})
        self.state = FakeState(schedule={DAY: blocks}, health=health or HEALTHY, db=self.db)
        self.wallet = FakeWallet()
        self._flags = dict(flags or {})
        self._debounce = debounce

    def cfg_bool(self, key, default=False):
        return bool(self._flags.get(key, default))

    def cfg_int(self, key, default=0):
        if key == "diary_live_debounce_minutes" and self._debounce is not None:
            return int(self._debounce)
        return int(self._flags.get(key, default))


def diary_row(ident, text, start="12:00", end="13:00", status="valid"):
    return {"event_id": ident, "block_id": ident, "start": start, "end": end,
            "place": "家", "text": text, "status": status}


def plugin(blocks=None, diaries=None, **kwargs):
    return FakePlugin(blocks=blocks, diaries=diaries, **kwargs)


def tick_at(plugin_obj, minutes=0, now=NOW):
    return live.diary_live_tick(plugin_obj, now + _dt.timedelta(minutes=minutes))


def hm(minute):
    return "%02d:%02d" % (minute // 60, minute % 60)


def backfill_day(count=12, first=6 * 60, step=45):
    """一天里已经过完的多个块：块日记齐全，用来测首次启用的批量补写。"""
    blocks, diaries = [], []
    for index in range(count):
        start = first + index * step
        end = start + 30
        ident = "ev_b%02d" % index
        blocks.append({"start": hm(start), "end": hm(end), "activity": "活动%d" % index,
                       "place": "家", "category": "rest", "zone": "home", "event_id": ident})
        diaries.append({"event_id": ident, "block_id": ident, "start": hm(start),
                        "end": hm(end), "place": "家",
                        "text": "第%d段：我做了点自己的事，心里踏实。" % index,
                        "status": "valid"})
    return blocks, diaries


class AppendTests(unittest.TestCase):
    def test_append_does_not_overwrite(self):
        p = plugin()
        first = live.diary_live_append(p, DAY, "第一段：中午煮了面。", "e1", "开心", now=NOW)
        second = live.diary_live_append(p, DAY, "第二段：下午有点困。", "e2", "疲惫", now=NOW)
        self.assertEqual(first.get("appended"), 1)
        self.assertEqual(second.get("appended"), 1)
        rows = live.diary_live_entries(p, DAY)
        self.assertEqual([row["text"] for row in rows],
                         ["第一段：中午煮了面。", "第二段：下午有点困。"])
        self.assertEqual([row["event_id"] for row in rows], ["e1", "e2"])
        # 旧的一条原封不动（不是整篇覆盖）
        self.assertEqual(rows[0]["mood"], "开心")

    def test_same_event_id_is_idempotent(self):
        p = plugin()
        live.diary_live_append(p, DAY, "只该写一遍。", "e1", "平静", now=NOW)
        again = live.diary_live_append(p, DAY, "重复触发。", "e1", "平静", now=NOW)
        self.assertEqual(again.get("appended"), 0)
        self.assertTrue(again.get("duplicate"))
        self.assertEqual(len(live.diary_live_entries(p, DAY)), 1)

    def test_identical_text_is_deduped(self):
        p = plugin()
        live.diary_live_append(p, DAY, "晚上煮了碗面。", "e1", "平静", now=NOW)
        same = live.diary_live_append(p, DAY, "晚上煮了碗面。", "e2", "平静", now=NOW)
        self.assertEqual(same.get("appended"), 0)
        self.assertEqual(len(live.diary_live_entries(p, DAY)), 1)


class DebounceTests(unittest.TestCase):
    def test_debounce_schedules_waits_then_appends_once(self):
        p = plugin(blocks=[LUNCH],
                   diaries=[diary_row("ev_lunch", "中午给自己煮了碗面，热汤下肚整个人都暖了。")])
        scheduled = tick_at(p, 0)
        self.assertEqual(scheduled.get("appended"), 0)
        self.assertEqual(len(live.diary_live_entries(p, DAY)), 0)
        due = float(live.diary_live_pending(p, DAY).get("due") or 0)
        self.assertAlmostEqual(due, NOW.timestamp() + 7 * 60, delta=2)
        waiting = tick_at(p, 3)
        self.assertTrue(waiting.get("waiting"))
        self.assertEqual(len(live.diary_live_entries(p, DAY)), 0)
        written = tick_at(p, 8)
        self.assertEqual(written.get("appended"), 1)
        rows = live.diary_live_entries(p, DAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], "ev_lunch")
        self.assertIn("煮了碗面", rows[0]["text"])

    def test_debounce_is_clamped_between_5_and_10_minutes(self):
        low = plugin(blocks=[MORNING], debounce=1)
        tick_at(low, 0)
        due_low = float(live.diary_live_pending(low, DAY).get("due") or 0)
        self.assertAlmostEqual(due_low - NOW.timestamp(), 5 * 60, delta=2)
        high = plugin(blocks=[MORNING], debounce=99)
        tick_at(high, 0)
        due_high = float(live.diary_live_pending(high, DAY).get("due") or 0)
        self.assertAlmostEqual(due_high - NOW.timestamp(), 10 * 60, delta=2)

    def test_single_flight_guard(self):
        p = plugin(blocks=[LUNCH], diaries=[diary_row("ev_lunch", "中午煮了面。")])
        p._diary_live_running = True
        result = tick_at(p, 0)
        self.assertEqual(result.get("skipped"), "running")
        self.assertEqual(live.diary_live_entries(p, DAY), [])
        self.assertFalse(live.diary_live_pending(p, DAY))

    def test_restart_recovers_from_persisted_pending(self):
        due = (NOW - _dt.timedelta(minutes=1)).timestamp()
        kv = {live.DIARY_LIVE_PENDING_PREFIX + DAY: {"due": due, "event_ids": ["ev_lunch"]}}
        p = plugin(blocks=[LUNCH],
                   diaries=[diary_row("ev_lunch", "中午煮了面，热乎乎的。")], kv=kv)
        result = tick_at(p, 0)  # 新实例、pending 还在、due 已过：直接补写
        self.assertEqual(result.get("appended"), 1)
        self.assertEqual(len(live.diary_live_entries(p, DAY)), 1)


class BatchBackfillTests(unittest.TestCase):
    """首次启用：当天已经过完的多个块要有界批量补写（不丢、不重复、按顺序）。"""

    def test_first_enable_backfills_in_bounded_chronological_batches(self):
        blocks, diaries = backfill_day(12)
        p = plugin(blocks=blocks, diaries=diaries)
        late_now = _dt.datetime(2026, 11, 17, 22, 0)
        first = live.diary_live_tick(p, late_now)
        self.assertEqual(first.get("appended"), 0)  # 先等 debounce
        self.assertGreater(first.get("scheduled") or 0, 0)
        second = live.diary_live_tick(p, late_now + _dt.timedelta(minutes=8))
        self.assertGreaterEqual(second.get("appended") or 0, 1)
        self.assertLessEqual(second.get("appended") or 0, live.DIARY_LIVE_BATCH_MAX)
        third = live.diary_live_tick(p, late_now + _dt.timedelta(minutes=9))
        self.assertLessEqual(third.get("appended") or 0, live.DIARY_LIVE_BATCH_MAX)
        live.diary_live_tick(p, late_now + _dt.timedelta(minutes=10))
        rows = live.diary_live_entries(p, DAY)
        self.assertEqual(len(rows), 12)  # 一条都不丢
        self.assertEqual([row["event_id"] for row in rows],
                         ["ev_b%02d" % index for index in range(12)])  # 按时间顺序
        self.assertEqual(len({row["event_id"] for row in rows}), 12)  # 不重复

    def test_batch_skips_contradiction_and_continues(self):
        p = plugin(blocks=[MORNING, LUNCH, HOSPITAL], health=SICK,
                   diaries=[diary_row("ev_hosp", "今天一整天都在家，哪儿也没去。",
                                      start="14:00", end="15:00")])
        tick_at(p, 0)
        result = tick_at(p, 8)
        self.assertEqual(result.get("appended"), 2)  # 矛盾的那条跳过，其它照写
        self.assertGreaterEqual(result.get("blocked") or 0, 1)
        ids = [row["event_id"] for row in live.diary_live_entries(p, DAY)]
        self.assertEqual(ids, ["ev_morning", "ev_lunch"])
        self.assertIn("ev_hosp", live.diary_live_skip_ids(p, DAY))
        again = tick_at(p, 30)
        self.assertEqual(again.get("appended"), 0)
        self.assertEqual(len(live.diary_live_entries(p, DAY)), 2)

    def test_batch_does_not_rewrite_duplicate_text(self):
        p = plugin(blocks=[LUNCH], diaries=[diary_row("ev_lunch", "中午煮了面。")],
                   kv={live.diary_live_key(DAY): [
                       {"ts": 1.0, "event_id": "e_old", "text": "中午煮了面。", "mood": "平静"}]})
        tick_at(p, 0)
        result = tick_at(p, 8)
        self.assertEqual(result.get("appended"), 0)
        self.assertEqual(len(live.diary_live_entries(p, DAY)), 1)
        tick_at(p, 30)
        self.assertEqual(len(live.diary_live_entries(p, DAY)), 1)


class FinalizeTests(unittest.TestCase):
    def test_finalize_merges_dedupes_and_archives(self):
        rows = [{"ts": 1.0, "event_id": "e1", "text": "第一段。", "mood": "开心"},
                {"ts": 2.0, "event_id": "e2", "text": "第一段。", "mood": "开心"},
                {"ts": 3.0, "event_id": "e3", "text": "第二段。", "mood": "平静"}]
        kv = {live.diary_live_key(DAY): rows,
              live.DIARY_LIVE_DAYS_KEY: [DAY],
              live.DIARY_LIVE_PENDING_PREFIX + DAY: {"due": 0}}
        p = plugin(kv=kv)
        result = live.diary_live_finalize(p, DAY, NOW)
        self.assertEqual(result.get("count"), 3)
        archive = p.db.kv.get(live.DIARY_LIVE_ARCHIVE_PREFIX + DAY) or {}
        self.assertEqual(archive.get("text"), "第一段。\n\n第二段。")
        self.assertEqual(len(archive.get("entries") or []), 3)
        self.assertNotIn(live.diary_live_key(DAY), p.db.kv)  # 连载本身清掉
        self.assertNotIn(live.DIARY_LIVE_PENDING_PREFIX + DAY, p.db.kv)
        self.assertIn(live.DIARY_LIVE_ARCHIVE_PREFIX + DAY, p.db.kv)
        self.assertIn(DAY, p.db.kv.get(live.DIARY_LIVE_ARCHIVE_DAYS_KEY) or [])

    def test_purge_keeps_only_two_days(self):
        old_day = (NOW.date() - _dt.timedelta(days=3)).isoformat()
        recent_day = (NOW.date() - _dt.timedelta(days=1)).isoformat()
        kv = {live.DIARY_LIVE_DAYS_KEY: [old_day, recent_day],
              live.diary_live_key(old_day): [{"ts": 1, "event_id": "x", "text": "旧", "mood": ""}],
              live.diary_live_key(recent_day): [{"ts": 1, "event_id": "y", "text": "新", "mood": ""}],
              live.DIARY_LIVE_PENDING_PREFIX + old_day: {"due": 0}}
        p = plugin(kv=kv)
        live.diary_live_purge(p, NOW)
        self.assertNotIn(live.diary_live_key(old_day), p.db.kv)
        self.assertNotIn(live.DIARY_LIVE_PENDING_PREFIX + old_day, p.db.kv)
        self.assertIn(live.diary_live_key(recent_day), p.db.kv)
        self.assertEqual(p.db.kv.get(live.DIARY_LIVE_DAYS_KEY), [recent_day])


class FactGateTests(unittest.TestCase):
    def test_contradiction_is_not_written_and_is_skipped(self):
        p = plugin(blocks=[HOSPITAL], health=SICK,
                   diaries=[diary_row("ev_hosp", "今天一整天都在家，哪儿也没去。",
                                      start="14:00", end="15:00")])
        tick_at(p, 0)  # 先排 debounce
        blocked = tick_at(p, 10)  # 到点，但和「出门了/生病了」矛盾
        self.assertEqual(blocked.get("appended"), 0)
        self.assertGreaterEqual(blocked.get("blocked") or 0, 1)
        self.assertEqual(live.diary_live_entries(p, DAY), [])
        self.assertIn("ev_hosp", live.diary_live_skip_ids(p, DAY))
        self.assertFalse(live.diary_live_pending(p, DAY))
        again = tick_at(p, 30)  # 记了跳过，不再反复重试
        self.assertEqual(again.get("appended"), 0)
        self.assertEqual(live.diary_live_entries(p, DAY), [])


class ContentTests(unittest.TestCase):
    def test_entry_links_event_id_and_mood(self):
        p = plugin(blocks=[LUNCH], diaries=[diary_row("ev_lunch", "中午煮了面。")])
        tick_at(p, 0)
        tick_at(p, 8)
        entry = live.diary_live_entries(p, DAY)[0]
        self.assertEqual(entry["event_id"], "ev_lunch")
        self.assertEqual(entry["mood"], "依恋")
        self.assertIsInstance(entry["ts"], float)
        self.assertGreater(entry["ts"], 0)

    def test_compose_first_person_from_facts_when_no_block_diary(self):
        p = plugin(blocks=[MORNING])  # 没有块日记 → 用事实拼第一人称
        candidates = live.diary_live_candidates(p, DAY, NOW)
        self.assertEqual(len(candidates), 1)
        text = live.diary_live_compose(p, candidates[0], None, NOW)
        self.assertIn("我", text)
        self.assertIn("赖床", text)
        self.assertTrue(text.startswith("早上"))

    def test_unfinished_block_is_not_a_candidate(self):
        p = plugin(blocks=[MORNING, LATE])
        ids = [row["event_id"] for row in live.diary_live_candidates(p, DAY, NOW)]
        self.assertEqual(ids, ["ev_morning"])  # 17:00 的块还没过完


class EdgeTests(unittest.TestCase):
    def test_empty_day_is_noop(self):
        p = plugin(blocks=[])
        result = tick_at(p, 0)
        self.assertEqual(result.get("appended"), 0)
        self.assertEqual(live.diary_live_entries(p, DAY), [])
        self.assertEqual(live.diary_live_snapshot(p, DAY)["count"], 0)

    def test_malformed_kv_is_tolerated(self):
        p = plugin(kv={live.diary_live_key(DAY): "不是列表",
                       live.DIARY_LIVE_PENDING_PREFIX + DAY: "也不是字典"})
        self.assertEqual(live.diary_live_entries(p, DAY), [])
        self.assertEqual(live.diary_live_pending(p, DAY), {})
        p.db.kv[live.diary_live_key(DAY)] = [1, {"text": ""}, {"text": "好的", "ts": "坏"}]
        rows = live.diary_live_entries(p, DAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ts"], 0.0)

    def test_broken_db_never_raises(self):
        p = plugin(blocks=[LUNCH])
        p.db = BrokenDB()
        p.state.db = p.db
        result = live.diary_live_tick(p, NOW)  # 不抛异常
        self.assertIsInstance(result, dict)
        self.assertNotIn("error", result)
        self.assertEqual(live.diary_live_entries(p, DAY), [])

    def test_snapshot_reports_pending_and_dedupes(self):
        p = plugin(blocks=[LUNCH], diaries=[diary_row("ev_lunch", "中午煮了面。")])
        tick_at(p, 0)
        snap = live.diary_live_snapshot(p, DAY)
        self.assertTrue(snap["pending"])
        self.assertEqual(snap["count"], 0)
        tick_at(p, 8)
        snap = live.diary_live_snapshot(p, DAY)
        self.assertFalse(snap["pending"])
        self.assertEqual(snap["count"], 1)
        self.assertIn("中午煮了面", snap["text"])


class ContractTests(unittest.TestCase):
    def _source(self, name):
        with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
            return handle.read()

    def test_diary_read_exposes_live_feed(self):
        source = self._source("amo_api_diary.py")
        self.assertIn("diary_live_snapshot", source)
        self.assertIn('"live_text"', source)
        self.assertIn('"live"', source)

    def test_scheduler_and_daily_write_hooks(self):
        source = self._source("amo_schedule.py")
        self.assertIn("diary_live_tick", source)
        self.assertIn("diary_live_finalize", source)

    def test_settings_page_can_show_diary_live(self):
        source = self._source(os.path.join("views", "dashboard", "assets", "panel-views.js"))
        self.assertIn("function settingsCategory", source)
        self.assertIn('return "日记与连载"', source)
        index = self._source(os.path.join("views", "dashboard", "index.html"))
        self.assertIn("diaryLiveSub", index)
        self.assertIn("设置里可调", index)


class ApiContractTests(unittest.TestCase):
    def test_api_diary_read_returns_live_feed(self):
        import asyncio
        from unittest import mock
        import amo_api_diary as api_module

        p = plugin(blocks=[LUNCH])
        live.diary_live_append(p, DAY, "中午煮了面，热乎乎的。", "ev_lunch", "依恋", now=NOW)

        class Stub(api_module.DiaryApiMixin):
            def __init__(self):
                self.db = p.db
                self.state = p.state

            async def _ensure_ready(self):
                return None

            async def _body(self):
                return {"date": DAY}

            def _q(self, key, default=""):
                return default

            def _diary_dir(self):
                return ""

        with mock.patch.object(api_module, "_diary_read", lambda root, day: ""):
            data = asyncio.run(Stub().api_diary_read())
        self.assertEqual(data.get("code"), 0)
        payload = data.get("data") or {}
        self.assertEqual(len(payload.get("live") or []), 1)
        self.assertEqual(payload["live"][0]["event_id"], "ev_lunch")
        self.assertIn("中午煮了面", payload.get("live_text") or "")
        self.assertFalse(payload.get("live_pending"))


if __name__ == "__main__":
    unittest.main()
