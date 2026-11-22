# -*- coding: utf-8 -*-
"""cov2: amo_state_schedule 的展示/时间轴/树/文案/追加/巡检等纯逻辑分支。"""
from __future__ import annotations

import copy
import os
import sys
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_state_schedule as sched  # noqa: E402
from amo_state_common import today_str  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}
        self.schedules = {}
        self.versions = {}
        self.saves = 0

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return [dict(row) for row in (self.schedules.get(day) or [])]

    def save_schedule(self, day, items):
        self.saves += 1
        self.schedules[day] = [dict(row) for row in items]

    def schedule_version(self, day):
        return self.versions.get(day, 0)

    def bump_schedule_version(self, day):
        self.versions[day] = self.versions.get(day, 0) + 1
        return self.versions[day]

    def mark_diaries_stale(self, *args, **kwargs):
        return []


class FakePlugin:
    def __init__(self):
        self.config = {"schedule_home": "", "schedule_workplace": "", "schedule_jit": False}
        self.calls = []

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_list(self, key, default=None):
        value = self.config.get(key, default)
        return list(value or [])


class Host(sched.ScheduleStateMixin):
    def __init__(self):
        self.db = FakeDB()
        self.schedule = {}
        self.schedule_ctx = {}
        self.schedule_profile = {"home": "出租屋", "workplace": "设计工作室"}
        self._schedule_commit_lock = threading.RLock()
        self._schedule_gen_locks = {}
        self.p = FakePlugin()
        self.jit = False
        self.commits = []
        self.settled = []

    def _jit_enabled(self):
        return self.jit

    def _jit_home(self):
        return "出租屋"

    def _booked_tree(self, day, items=None):
        return [{"day": day, "booked": True}]

    def _jit_schedule_tree(self, day, items=None):
        return [{"day": day, "jit": True}]

    def health_info(self):
        return {"status": "健康", "sick": False}

    def settle_date_end(self, day):
        self.settled.append(day)

    def _commit_schedule_change_locked(self, day, items, old_items=None, source="", reason="", **kw):
        self.commits.append((day, [dict(row) for row in items], source))
        self.schedule[day] = [dict(row) for row in items]
        return {"changed": True, "version": 1, "items": len(items)}


def row(start, end, activity="休息", **extra):
    item = {"start": start, "end": end, "activity": activity}
    item.update(extra)
    return item


class Base(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.day = today_str()


class HourlyTests(Base):
    def test_empty_and_invalid_starts(self):
        self.assertEqual(self.host._hourly_schedule([]), [])
        self.assertEqual(self.host._hourly_schedule(None), [])
        self.assertEqual(self.host._hourly_schedule(["x", {"start": "坏"}, {"start": "25:00"}]), [])

    def test_24_rows_and_defaults(self):
        rows = self.host._hourly_schedule([row("08:00", "18:00", "上班", place="公司",
                                               category="work", zone="office")])
        self.assertEqual(len(rows), 24)
        self.assertEqual(rows[0]["activity"], "睡觉")
        self.assertEqual(rows[0]["category"], "sleep")
        self.assertEqual(rows[8]["activity"], "上班")
        self.assertEqual(rows[8]["zone"], "office")
        self.assertEqual(rows[12]["category"], "work")
        self.assertEqual(rows[23]["end"], "24:00")
        self.assertEqual(rows[2]["start"], "02:00")

    def test_overlap_picks_longer_and_later(self):
        rows = self.host._hourly_schedule([
            row("13:00", "13:30", "旧安排", category="rest"),
            row("13:30", "14:00", "新安排", category="work"),
            row("13:10", "13:20", "短块", category="life"),
        ])
        self.assertEqual(rows[13]["activity"], "新安排")
        self.assertEqual(rows[13]["category"], "work")

    def test_extra_fields_copied(self):
        item = row("09:00", "10:00", "开会", detail="周会", event="overtime",
                   mood="平静", effects={"energy": -1}, soothe=True, swim=True)
        rows = self.host._hourly_schedule([item])
        self.assertEqual(rows[9]["detail"], "周会")
        self.assertEqual(rows[9]["event"], "overtime")
        self.assertEqual(rows[9]["effects"], {"energy": -1})
        self.assertTrue(rows[9]["soothe"])
        self.assertTrue(rows[9]["swim"])

    def test_changed_hours_and_brief(self):
        old = [row("08:00", "12:00", "上班", place="公司", category="work")]
        new = [row("08:00", "12:00", "在家", place="家", category="rest")]
        self.assertEqual(self.host.changed_hours(old, new), list(range(8, 12)))
        self.assertEqual(self.host.changed_hours([], []), [])
        brief = self.host._brief_schedule(new, [8, 9, 10, 11])
        self.assertIn("08:00|在家|家|rest", brief)
        self.assertIn("...+1", brief)
        self.assertEqual(self.host._brief_schedule([], []), "-")


class DisplayHelperTests(Base):
    def test_fmt_minutes(self):
        self.assertEqual(self.host._fmt_minutes(-5), "00:00")
        self.assertEqual(self.host._fmt_minutes(65), "01:05")
        self.assertEqual(self.host._fmt_minutes(24 * 60), "24:00")
        self.assertEqual(self.host._fmt_minutes(9999), "24:00")

    def test_display_priority(self):
        self.assertEqual(self.host._display_priority({"source": "chat"}), 90)
        self.assertEqual(self.host._display_priority({"category": "finance"}), 70)
        self.assertEqual(self.host._display_priority({"category": "meal"}), 55)
        self.assertEqual(self.host._display_priority({"category": "commute"}), 50)
        self.assertEqual(self.host._display_priority({"category": "work"}), 45)
        self.assertEqual(self.host._display_priority({"category": "play"}), 40)
        self.assertEqual(self.host._display_priority({"category": "life"}), 30)
        self.assertEqual(self.host._display_priority({"category": "rest"}), 20)
        self.assertEqual(self.host._display_priority({"category": "sleep"}), 10)
        self.assertEqual(self.host._display_priority({}), 25)

    def test_display_event(self):
        self.assertEqual(self.host._display_event("overtime"), "overtime")
        self.assertEqual(self.host._display_event("sick"), "sick")
        self.assertEqual(self.host._display_event("财务·支出"), "")
        self.assertEqual(self.host._display_event(None), "")

    def test_finance_activity(self):
        self.assertEqual(self.host._finance_activity("上班", "x"), "上班")
        self.assertEqual(self.host._finance_activity("工资到账", "¥7,212.64"), "工资到账（¥7,212.64）")
        self.assertEqual(self.host._finance_activity("工资", ""), "工资到账")
        self.assertEqual(self.host._finance_activity("财务·支出", "买了：热汤面，¥20.00"),
                         "买了热汤面（花销 ¥20.00）")
        self.assertEqual(self.host._finance_activity("财务·支出", "买了：热汤面"), "买了热汤面")
        self.assertEqual(self.host._finance_activity("财务·支出", "¥30"), "花销 ¥30")
        self.assertEqual(self.host._finance_activity("财务·其他", "备注"), "财务·其他")
        self.assertEqual(self.host._finance_activity("财务·支出", "7元"), "花销 ¥7")
        self.assertEqual(self.host._finance_activity("财务·入账", "备注"), "财务·入账")

    def test_default_slot_boundaries(self):
        self.assertEqual(self.host._default_slot(0, 360)["activity"], "深度睡眠")
        self.assertEqual(self.host._default_slot(360, 480)["activity"], "起床后的琐事")
        self.assertEqual(self.host._default_slot(480, 600)["activity"], "起床后的琐事")
        self.assertEqual(self.host._default_slot(600, 700)["activity"], "日常发呆")
        self.assertEqual(self.host._default_slot(720, 800)["activity"], "午饭后的放空")
        self.assertEqual(self.host._default_slot(840, 900)["activity"], "日常发呆")
        self.assertEqual(self.host._default_slot(1080, 1200)["activity"], "窝在家里的晚上")
        self.assertEqual(self.host._default_slot(1320, 1440)["activity"], "准备睡觉")
        self.assertEqual(self.host._default_slot(480, 600)["place"], "出租屋")


class JitVisibleTests(Base):
    def test_empty_and_future_events(self):
        slots = self.host._jit_visible_slots([], 600)
        self.assertEqual(sum(slot["end_min"] - slot["start_min"] for slot in slots), 600)
        self.assertEqual(slots[0]["activity"], "深度睡眠")
        self.assertEqual(slots[-1]["current"], False)
        slots = self.host._jit_visible_slots([row("12:00", "13:00", "午饭")], 600)
        self.assertTrue(slots)
        self.assertTrue(all(slot["start_min"] <= 600 for slot in slots))

    def test_event_bad_end_and_current(self):
        slots = self.host._jit_visible_slots([
            {"start": "09:00", "activity": "坏结束", "end": "坏"},
            {"start": "10:00", "end": "08:00", "activity": "倒置"},
            row("11:00", "13:00", "聊天", source="chat", category="social"),
        ], 12 * 60 + 30)
        current = [slot for slot in slots if slot["current"]]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["activity"], "聊天")
        self.assertTrue(current[0]["doing"])
        self.assertEqual(current[0]["end_min"], 13 * 60)

    def test_social_beats_finance(self):
        slots = self.host._jit_visible_slots([
            row("10:00", "11:00", "约会", category="date", priority=None),
            row("10:00", "10:30", "财务·支出", category="finance", detail="¥5"),
        ], 11 * 60)
        covering = [slot for slot in slots if slot["start_min"] == 600]
        self.assertEqual(covering[0]["activity"], "约会")

    def test_finance_translated_and_defaults_split(self):
        slots = self.host._jit_visible_slots([
            row("08:00", "09:00", "财务·支出", category="finance", detail="买了：豆浆，¥3"),
        ], 24 * 60)
        finance = [slot for slot in slots if slot["start_min"] == 480][0]
        self.assertEqual(finance["activity"], "买了豆浆（花销 ¥3）")
        activities = [slot["activity"] for slot in slots]
        self.assertIn("深度睡眠", activities)
        self.assertIn("日常发呆", activities)
        self.assertIn("午饭后的放空", activities)
        self.assertIn("窝在家里的晚上", activities)
        self.assertIn("准备睡觉", activities)

    def test_open_sleep_fields(self):
        slots = self.host._jit_visible_slots([
            {"start": "23:00", "end": "24:00", "activity": "睡觉", "open_sleep": True,
             "sleep_from": "23:00", "category": "sleep"},
        ], 24 * 60 - 1)
        sleep = [slot for slot in slots if slot["start_min"] == 23 * 60]
        self.assertEqual(len(sleep), 1)
        self.assertTrue(sleep[0]["open_sleep"])
        self.assertEqual(sleep[0]["current"], True)

    def test_merge_quiet_slots(self):
        slots = [
            {"start_min": 360, "end_min": 420, "activity": "A", "place": "p",
             "category": "life", "current": False, "doing": False, "open_sleep": False},
            {"start_min": 420, "end_min": 480, "activity": "A", "place": "p",
             "category": "life", "current": True, "doing": True, "open_sleep": False},
            {"start_min": 480, "end_min": 540, "activity": "A", "place": "p",
             "category": "rest", "current": False, "doing": False, "open_sleep": False},
            {"start_min": 1320, "end_min": 1340, "activity": "窝在家",
             "place": "家", "category": "rest", "current": False, "doing": False,
             "open_sleep": False},
        ]
        merged = self.host._merge_quiet_slots(slots)
        self.assertEqual(merged[0]["start_min"], 360)
        self.assertEqual(merged[0]["end_min"], 480)
        self.assertTrue(merged[0]["current"])
        self.assertEqual(merged[1]["category"], "rest")
        self.assertEqual(merged[-1]["end_min"], 23 * 60)


class TreeTextTests(Base):
    def test_cover_schedule(self):
        self.assertEqual(self.host._cover_schedule(None), None)
        self.assertEqual(self.host._cover_schedule([]), [])
        self.assertEqual(self.host._cover_schedule(["x"]), ["x"])
        covered = self.host._cover_schedule([row("08:00", "09:00", "早饭")])
        self.assertEqual(covered[0]["start"], "00:00")
        self.assertEqual(covered[0]["category"], "sleep")
        self.assertEqual(len(covered), 2)
        already = self.host._cover_schedule([row("00:00", "08:00", "睡觉")])
        self.assertEqual(len(already), 1)

    def test_emergency_schedule(self):
        items = self.host._emergency_schedule()
        self.assertEqual(len(items), 24)
        self.assertEqual(items[0]["category"], "sleep")
        self.assertEqual(items[7]["category"], "life")
        self.assertEqual(items[8]["category"], "commute")
        self.assertEqual(items[9]["category"], "work")
        self.assertEqual(items[12]["category"], "meal")
        self.assertEqual(items[19]["category"], "meal")
        self.assertEqual(items[22]["category"], "life")

    def test_blind_hint(self):
        self.assertEqual(self.host.schedule_blind_hint(), "")
        self.host.jit = True
        today = today_str()
        self.assertEqual(self.host.schedule_blind_hint("2020-01-01"), "")
        self.assertIn("那天还没到", self.host.schedule_blind_hint("2999-01-01"))
        self.assertIn("今天还没过完", self.host.schedule_blind_hint(today))

    def test_schedule_tree_empty_and_future(self):
        self.assertEqual(self.host.schedule_tree(self.day), [])
        self.assertEqual(self.host.schedule_tree("2999-01-01"),
                         [{"day": "2999-01-01", "booked": True}])
        self.host.jit = True
        self.assertEqual(self.host.schedule_tree(self.day),
                         [{"day": self.day, "jit": True}])

    def test_schedule_tree_blocks(self):
        self.host.schedule[self.day] = [row("08:00", "12:00", "上班", place="公司",
                                            category="work", zone="office")]
        with mock.patch.object(sched, "minutes_now", lambda: 9 * 60 + 30):
            blocks = self.host.schedule_tree(self.day)
        self.assertEqual(len(blocks), 4)
        morning = blocks[1]
        self.assertEqual(morning["title"], "上午")
        self.assertEqual(len(morning["children"]), 6)
        current = [child for child in morning["children"] if child["current"]]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["activity"], "上班")
        self.assertEqual(morning["facts"]["part"], "上午")
        self.assertEqual(len(morning["facts"]["events"]), 6)
        self.assertEqual(len(blocks[0]["children"]), 6)
        self.assertEqual(blocks[0]["children"][0]["activity"], "睡觉")
        self.assertEqual(blocks[0]["current"], False)

    def test_schedule_tree_from_items(self):
        items = [row("08:00", "12:00", "上班", category="work")]
        with mock.patch.object(sched, "minutes_now", lambda: 60):
            tree = self.host.schedule_tree_from_items(self.day, items)
        self.assertEqual(len(tree), 4)
        self.assertEqual(self.host.db.saves, 0)

    def test_schedule_text_of(self):
        self.assertEqual(self.host.schedule_text_of(self.day), "那天还没有安排。")
        self.host.db.kv["day_types"] = {self.day: {"id": "workday", "name": "上班日"}}
        self.host.schedule[self.day] = [
            row("08:00", "09:00", "上班", place="公司"),
            row("09:00", "10:00", "", place="公司"),
        ]
        text = self.host.schedule_text_of(self.day)
        self.assertIn("今天是上班日。", text)
        self.assertIn("08:00-09:00 上班（公司）", text)
        self.assertNotIn("09:00-10:00", text)
        self.host.jit = True
        with mock.patch.object(sched, "minutes_now", lambda: 8 * 60 + 30):
            text = self.host.schedule_text_of(self.day)
        self.assertIn("08:00-09:00", text)
        self.assertNotIn("09:00-10:00", text)

    def test_schedule_text_of_error(self):
        class BoomDB(FakeDB):
            def get_schedule(self, day):
                raise RuntimeError("库挂了")

        self.host.db = BoomDB()
        self.host.schedule = {}
        self.assertEqual(self.host.schedule_text_of(self.day), "想不起来…")

    def test_day_type_store_and_today(self):
        self.assertEqual(self.host._day_type_config(), [])
        self.assertIsInstance(self.host._day_types(), list)
        self.assertEqual(self.host._day_type_store(), {})
        self.host.db.kv["day_types"] = {self.day: {"id": "x", "name": "自定义"}}
        picked = self.host._today_day_type(self.day)
        self.assertEqual(picked["name"], "自定义")
        self.host.db.kv["day_types"] = {}
        picked = self.host._today_day_type("2026-11-21")
        self.assertIn("id", picked)
        self.assertIn("name", picked)
        self.assertIn("2026-11-21", self.host.db.kv["day_types"])


class SaveAddTests(Base):
    def test_save_day(self):
        self.assertFalse(self.host.save_day("", []))
        self.assertTrue(self.host.save_day(self.day, [row("08:00", "09:00"), "bad"]))
        self.assertEqual(len(self.host.schedule[self.day]), 1)
        self.assertEqual(self.host.db.get_schedule(self.day), self.host.schedule[self.day])
        self.assertEqual(self.host.db.kv["schedule"][self.day], self.host.schedule[self.day])

    def test_save_day_initializes_schedule_dict(self):
        self.host.schedule = []
        self.assertTrue(self.host.save_day(self.day, [row("08:00", "09:00")]))
        self.assertIsInstance(self.host.schedule, dict)

    def test_add_schedule_item_rejects_bad_input(self):
        empty = {"changed": False, "version": 0, "changed_hours": [], "cancelled": []}
        self.assertEqual(self.host.add_schedule_item("", row("08:00", "09:00")), empty)
        self.assertEqual(self.host.add_schedule_item(self.day, "bad"), empty)
        self.assertEqual(self.host.add_schedule_item(self.day, row("坏", "09:00")), empty)

    def test_add_schedule_item_commits_sorted(self):
        self.host.schedule[self.day] = [row("10:00", "11:00", "写代码")]
        result = self.host.add_schedule_item(
            self.day, row("08:00", "09:00", "吃早饭"), source="finance")
        self.assertEqual(result["changed"], True)
        self.assertEqual(self.host.commits[-1][2], "finance")
        starts = [item["start"] for item in self.host.commits[-1][1]]
        self.assertEqual(starts, ["08:00", "10:00"])

    def test_add_schedule_item_overlap_skipped(self):
        self.host.schedule[self.day] = [row("06:00", "07:00", "睡觉", category="sleep")]
        out = self.host.add_schedule_item(self.day, row("06:12", "06:22", "财务"))
        self.assertFalse(out["changed"])
        self.assertEqual(out["skipped"], "overlap")
        self.assertEqual(self.host.commits, [])

    def test_skipped_add_result_version_error(self):
        class BoomDB(FakeDB):
            def schedule_version(self, day):
                raise RuntimeError("x")

        self.host.db = BoomDB()
        out = self.host._skipped_add_result(self.day, None)
        self.assertEqual(out["version"], 0)
        self.assertEqual(out["skipped"], "overlap")

    def test_reconcile_health_no_schedule(self):
        self.assertEqual(self.host.reconcile_health_schedule(self.day), 0)

    def test_reconcile_health_removes_rows(self):
        self.host.schedule[self.day] = [row("08:00", "09:00", "病假", category="health")]
        with mock.patch("amo_schedule_health.reconcile_stale_health_schedule",
                        lambda items, health, now: (items[:0], 1)):
            removed = self.host.reconcile_health_schedule(self.day)
        self.assertEqual(removed, 1)
        self.assertTrue(self.host.commits)
        self.assertEqual(self.host.commits[-1][0], self.day)

    def test_reconcile_health_nothing_removed(self):
        self.host.schedule[self.day] = [row("08:00", "09:00", "上班")]
        with mock.patch("amo_schedule_health.reconcile_stale_health_schedule",
                        lambda items, health, now: (items, 0)):
            self.assertEqual(self.host.reconcile_health_schedule(self.day), 0)
        self.assertEqual(self.host.commits, [])

    def test_maintain_schedule_day_settles_only(self):
        self.assertTrue(self.host.maintain_schedule_day(self.day))
        # A1：维护任务只做约会收尾结算，不再修 transit 行。
        self.assertEqual(self.host.settled, [self.day])
        class Boom:
            def settle_date_end(self, day):
                raise RuntimeError("y")
        host = Boom()
        self.assertTrue(sched.ScheduleStateMixin.maintain_schedule_day(host, self.day))


class ResolveTests(Base):
    def test_resolve_exact_point_and_none(self):
        rows = [row("08:00", "09:00", "早饭"), row("09:00", "10:00", "上班")]
        with mock.patch("amo_timeline.Timeline.blocks", lambda self, day: rows):
            exact = self.host.resolve_schedule_item(self.day, "08:00", "09:00")
            self.assertEqual(exact["activity"], "早饭")
            point = self.host.resolve_schedule_item(self.day, "09:30")
            self.assertEqual(point["activity"], "上班")
            self.assertIsNone(self.host.resolve_schedule_item(self.day, "坏", ""))
            self.assertIsNone(self.host.resolve_schedule_item(self.day, "20:00"))
        with mock.patch("amo_timeline.Timeline.blocks",
                        lambda self, day: (_ for _ in ()).throw(RuntimeError("x"))):
            self.assertIsNone(self.host.resolve_schedule_item(self.day, "08:00"))

    def test_resolve_reversed_range_extends(self):
        rows = [{"start": "10:00", "end": "09:00", "activity": "坏区间"}]
        with mock.patch("amo_timeline.Timeline.blocks", lambda self, day: rows):
            found = self.host.resolve_schedule_item(self.day, "10:30")
        self.assertEqual(found["activity"], "坏区间")


if __name__ == "__main__":
    unittest.main()



class OfficeGapDefaultTests(Base):
    def test_office_day_gap_stays_at_office(self):
        self.assertEqual(self.host._default_slot(640, 660, office=True)["zone"], "office")
        self.assertEqual(self.host._default_slot(760, 800, office=True)["zone"], "office")
        # 没有 office 锚点时保持旧的 home 默认
        self.assertEqual(self.host._default_slot(640, 660)["zone"], "home")
