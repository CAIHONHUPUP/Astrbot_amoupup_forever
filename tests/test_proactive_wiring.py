# -*- coding: utf-8 -*-
"""主动消息接线：日程提醒、同义追问合并、去重保留答案（纯标准库假库）。"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_proactive import (  # noqa: E402
    ProactiveMixin,
    followup_repeated,
    has_answer_cue,
    keep_answer,
    merge_synonym_questions,
    schedule_fact_current,
    schedule_reminder_fact,
)

DAY = "2026-11-18"


class _FakeDb:
    def __init__(self, rows=None, with_schedule=True):
        self.rows = [dict(row) for row in (rows or [])]
        self.kv = {}
        self.with_schedule = with_schedule

    def get_schedule(self, day):
        if not self.with_schedule:
            raise AssertionError("get_schedule should not be called")
        return [dict(row) for row in self.rows]

    def schedule_version(self, day):
        return 3

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


def _rows():
    return [
        {"start": "15:05", "end": "15:30", "activity": "洗脸", "place": "家", "zone": "home"},
        {"start": "15:10", "end": "15:50", "activity": "复诊拿药", "place": "医院", "zone": "hospital"},
        {"start": "15:40", "end": "16:20", "activity": "开会", "place": "公司", "zone": "office"},
        {"start": "17:00", "end": "18:00", "activity": "吃饭", "place": "餐厅", "zone": "restaurant"},
        {"start": "15:30", "end": "17:00", "activity": "睡觉", "place": "家", "zone": "home"},
    ]


class ScheduleReminderFactTests(unittest.TestCase):
    def test_picks_nearest_event_in_window(self):
        fact = schedule_reminder_fact(_FakeDb(_rows()), datetime(2026, 11, 18, 15, 0), day=DAY)
        self.assertEqual(fact["event"], "复诊拿药")
        self.assertEqual(fact["time"], "15:10")
        self.assertEqual(fact["place"], "医院")
        self.assertEqual(fact["key"], "复诊拿药|医院|15:10",
                         "幂等键必须含开始时间，改期/同日二次同活动才不会被误判已提醒")
        self.assertEqual(fact["version"], 3)

    def test_event_too_soon_is_skipped(self):
        fact = schedule_reminder_fact(_FakeDb(_rows()), datetime(2026, 11, 18, 15, 0), day=DAY)
        self.assertNotEqual(fact["event"], "洗脸")

    def test_event_too_far_is_skipped(self):
        fact = schedule_reminder_fact(_FakeDb(_rows()), datetime(2026, 11, 18, 15, 0), day=DAY)
        self.assertNotEqual(fact["event"], "吃饭")

    def test_sleep_is_skipped(self):
        fact = schedule_reminder_fact(_FakeDb(_rows()), datetime(2026, 11, 18, 15, 0), day=DAY)
        self.assertNotEqual(fact["event"], "睡觉")

    def test_already_reminded_is_skipped(self):
        db = _FakeDb(_rows())
        db.kv["schedule_reminded:%s:%s" % (DAY, "复诊拿药|医院|15:10")] = True
        fact = schedule_reminder_fact(db, datetime(2026, 11, 18, 15, 0), day=DAY)
        self.assertEqual(fact["event"], "开会")

    def test_nothing_upcoming_returns_empty(self):
        self.assertEqual(
            schedule_reminder_fact(_FakeDb(_rows()), datetime(2026, 11, 18, 20, 0), day=DAY),
            {},
        )


class ScheduleFactCurrentTests(unittest.TestCase):
    FACT = {"day": DAY, "key": "复诊拿药|医院|15:10", "time": "15:10",
            "place": "医院", "event": "复诊拿药"}

    def test_unchanged_event_is_current(self):
        self.assertTrue(schedule_fact_current(_FakeDb(_rows()), dict(self.FACT)))

    def test_cancelled_event_is_not_current(self):
        rows = [row for row in _rows() if row["activity"] != "复诊拿药"]
        self.assertFalse(schedule_fact_current(_FakeDb(rows), dict(self.FACT)))

    def test_rescheduled_event_is_not_current(self):
        rows = []
        for row in _rows():
            if row["activity"] == "复诊拿药":
                row = {**row, "start": "16:10"}
            rows.append(row)
        self.assertFalse(schedule_fact_current(_FakeDb(rows), dict(self.FACT)))

    def test_unknown_event_is_not_current(self):
        fact = {**self.FACT, "key": "机场接表姐|机场|16:00"}
        self.assertFalse(schedule_fact_current(_FakeDb(_rows()), fact))

    def test_missing_schedule_api_passes(self):
        fact = dict(self.FACT)
        self.assertTrue(schedule_fact_current(object(), fact))

    def test_missing_key_passes(self):
        self.assertTrue(schedule_fact_current(_FakeDb(_rows()), {"day": DAY}))

    def test_legacy_two_part_key_still_matches(self):
        """旧版 pending 的键没有开始时间：升级后不该把还在的日程当成取消了。"""
        fact = {"day": DAY, "key": "复诊拿药|医院", "time": "15:10",
                "place": "医院", "event": "复诊拿药"}
        self.assertTrue(schedule_fact_current(_FakeDb(_rows()), fact))
        self.assertFalse(schedule_fact_current(
            _FakeDb(_rows()), {**fact, "time": "16:10"}),
            "旧键也只能放行开始时间对得上的那条，改期照样拦")

    def test_followup_ignores_late_window(self):
        """迟到窗口只约束提醒；追问发生在提醒之后，日程已经开始仍要允许。"""
        now = datetime(2026, 11, 18, 15, 40)
        self.assertFalse(schedule_fact_current(_FakeDb(_rows()), dict(self.FACT), now=now))
        self.assertTrue(schedule_fact_current(
            _FakeDb(_rows()), dict(self.FACT), now=now, late_window=False))


class PendingScheduleTests(unittest.TestCase):
    def test_pending_keeps_schedule_identity(self):
        db = _FakeDb()
        plugin = ProactiveMixin.__new__(ProactiveMixin)
        plugin.db = db
        plugin._set_proactive_pending(
            "u1", "schedule_reminder", "我15:10在医院复诊",
            umo="sim:u1",
            schedule={"day": DAY, "key": "复诊拿药|医院|15:10", "version": 3,
                      "time": "15:10", "place": "医院", "event": "复诊拿药",
                      "extra": "ignored"},
        )
        pending = db.kv_get("proactive_pending_u1")
        self.assertEqual(pending["kind"], "schedule_reminder")
        self.assertEqual(pending["schedule"]["key"], "复诊拿药|医院|15:10")
        self.assertEqual(pending["schedule"]["time"], "15:10")
        self.assertNotIn("extra", pending["schedule"])

    def test_pending_without_schedule_has_no_meta(self):
        db = _FakeDb()
        plugin = ProactiveMixin.__new__(ProactiveMixin)
        plugin.db = db
        plugin._set_proactive_pending("u1", "greet", "在干嘛", umo="sim:u1")
        self.assertNotIn("schedule", db.kv_get("proactive_pending_u1"))


class MergeSynonymQuestionsTests(unittest.TestCase):
    def test_same_question_twice_is_merged(self):
        self.assertEqual(merge_synonym_questions("你忙完了没？忙完了吗？"), "你忙完了没？")

    def test_other_clauses_are_kept(self):
        self.assertEqual(
            merge_synonym_questions("忙完了吗？忙完了没？老公抱抱。"),
            "忙完了吗？老公抱抱。",
        )

    def test_different_questions_are_not_merged(self):
        text = "你到家了吗？你下班了吗？"
        self.assertEqual(merge_synonym_questions(text), text)

    def test_single_question_is_unchanged(self):
        text = "你忙完了吗？"
        self.assertEqual(merge_synonym_questions(text), text)

    def test_plain_statement_is_unchanged(self):
        text = "我在公司写周报，晚点回你。"
        self.assertEqual(merge_synonym_questions(text), text)

    def test_empty_input(self):
        self.assertEqual(merge_synonym_questions(""), "")


class FollowupRepeatedTests(unittest.TestCase):
    """追问闸门：同一句追问换语气词再说一遍 = 重复，不再问第二遍。"""

    def test_filler_suffix_same_question(self):
        self.assertTrue(followup_repeated("你忙完了没？呀？", ["你忙完了没？"]))

    def test_extra_particles_same_question(self):
        self.assertTrue(followup_repeated("你忙完了没？", ["你忙完了没？呀？"]))

    def test_other_question_is_not_repeat(self):
        self.assertEqual(followup_repeated("你吃饭了吗？", ["你忙完了没？"]), "")

    def test_plain_statement_is_not_repeat(self):
        # 追问不一定是问句：只跟问句比，陈述句/关心句不按重复处理。
        self.assertEqual(
            followup_repeated("我这边也在忙，等你忙完再说。", ["你忙完了没？"]), "")

    def test_no_previous_is_not_repeat(self):
        self.assertEqual(followup_repeated("你忙完了没？呀？", []), "")
        self.assertEqual(followup_repeated("你忙完了没？", ["", None]), "")

    def test_returns_repeated_line(self):
        self.assertEqual(followup_repeated("你忙完了没？呀？", ["你忙完了没？"]),
                         "你忙完了没？")


class KeepAnswerTests(unittest.TestCase):
    def test_time_answer_survives_empty_dedup(self):
        line = "我18:00到家，换鞋瘫一会儿。"
        self.assertTrue(has_answer_cue(line))
        self.assertEqual(keep_answer(line, ""), line)

    def test_place_answer_survives_empty_dedup(self):
        self.assertEqual(keep_answer("我在医院，刚拿完药。", ""), "我在医院，刚拿完药。")

    def test_plain_status_is_not_kept(self):
        self.assertEqual(keep_answer("嗯嗯，好呀。", ""), "")

    def test_non_empty_fixed_is_returned(self):
        self.assertEqual(keep_answer("我18:00到家。", "我到家了。"), "我到家了。")


if __name__ == "__main__":
    unittest.main(verbosity=2)
