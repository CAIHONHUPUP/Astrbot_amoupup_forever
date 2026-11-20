# -*- coding: utf-8 -*-
"""块级补丁 + 预约块保护 + 碎片丢弃（2026-11-20）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_state_schedule as sched  # noqa: E402


def block(start, end, activity, category="life", **extra):
    row = {"start": start, "end": end, "activity": activity, "category": category}
    row.update(extra)
    return row


class DropShortBlocksTests(unittest.TestCase):
    def test_short_is_dropped(self):
        kept, dropped = sched.drop_short_blocks([
            block("20:39", "20:40", "和闺蜜吃饭", "social"),
            block("20:00", "21:00", "在家", "rest"),
        ])
        self.assertEqual(1, len(dropped))
        self.assertEqual("在家", kept[0]["activity"])

    def test_normal_kept(self):
        kept, dropped = sched.drop_short_blocks([block("18:00", "18:20", "路上", "commute")])
        self.assertEqual([], dropped)
        self.assertEqual(1, len(kept))


class RestoreAppointmentsTests(unittest.TestCase):
    def test_jit_rebuild_does_not_eat_social_block(self):
        old = [block("19:09", "20:00", "柳如烟下班吃火锅", "social"),
               block("18:00", "19:00", "在家", "rest")]
        new = [block("18:00", "20:00", "在家休息", "rest")]
        merged, restored = sched.restore_appointments(old, new, "jit")
        self.assertEqual(1, len(restored))
        self.assertIn("柳如烟下班吃火锅", [row["activity"] for row in merged])

    def test_authoritative_source_may_delete(self):
        old = [block("19:09", "20:00", "柳如烟下班吃火锅", "social")]
        new = [block("19:00", "20:00", "在家", "rest")]
        merged, restored = sched.restore_appointments(old, new, "manual")
        self.assertEqual([], restored)

    def test_no_restore_when_time_is_taken(self):
        old = [block("19:09", "20:00", "柳如烟下班吃火锅", "social")]
        new = [block("19:00", "20:30", "在公司加班", "work")]
        merged, restored = sched.restore_appointments(old, new, "jit")
        self.assertEqual([], restored)

    def test_ordinary_block_not_restored(self):
        old = [block("18:00", "19:00", "在家", "rest")]
        merged, restored = sched.restore_appointments(old, [], "jit")
        self.assertEqual([], restored)


class _State:
    def __init__(self, rows):
        self.schedule = {"2026-11-20": list(rows)}
        self.saved = None

    def apply_schedule(self, day, mutator, *, source="", reason=""):
        self.saved = mutator(self.schedule[day])
        return {"changed": True, "version": 1}


class PatchScheduleTests(unittest.TestCase):
    def test_upsert_by_event_id(self):
        state = _State([block("18:00", "19:00", "在家", "rest", event_id="ev_1")])
        out = sched.ScheduleStateMixin.patch_schedule(
            state, "2026-11-20",
            upserts=[{"event_id": "ev_1", "activity": "在沙发上看剧"}], source="chat")
        self.assertEqual(1, out["patched"])
        self.assertEqual("在沙发上看剧", state.saved[0]["activity"])

    def test_delete_by_event_id(self):
        state = _State([block("18:00", "19:00", "在家", "rest", event_id="ev_1"),
                        block("19:00", "20:00", "吃饭", "meal", event_id="ev_2")])
        out = sched.ScheduleStateMixin.patch_schedule(
            state, "2026-11-20", deletes=[{"event_id": "ev_2"}], source="chat")
        self.assertEqual(1, out["deleted"])
        self.assertEqual(1, len(state.saved))

    def test_upsert_appends_when_missing(self):
        state = _State([])
        out = sched.ScheduleStateMixin.patch_schedule(
            state, "2026-11-20",
            upserts=[block("21:00", "22:00", "洗澡", "life")], source="chat")
        self.assertEqual(1, out["patched"])
        self.assertEqual(1, len(state.saved))


if __name__ == "__main__":
    unittest.main()
