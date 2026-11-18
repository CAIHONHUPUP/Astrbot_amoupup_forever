# -*- coding: utf-8 -*-
"""add_schedule_item 的落库语义：追加块也必须过 resolve_new_overlaps。

2026-11-17 06:12 事故：add_schedule_item(source="finance") 直接调
_commit_schedule_change_locked，绕过了 commit_schedule_change 的第二道闸；
在 sleep 06:00-07:00 上追加 finance 06:12-06:22 会把重叠原样写进 schedule 表
（面板/详情看得到，clean_schedule 一开始也只读 kv 所以扫不到）。

这里走**真实** add_schedule_item + 真实去重叠（state/db 是测试替身），断言：
- 落库后时间轴无重叠；
- 被覆盖的新块整条不写（changed=False, skipped="overlap"）；
- 部分重叠的新块起点挪到前块结束之后；
- schedule 表与 kv['schedule'] 镜像一致。
"""
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

from amo_state_common import today_str  # noqa: E402
from amo_state_schedule import ScheduleStateMixin, _item_overlaps_any  # noqa: E402

DAY = today_str()


def _minute(text):
    body = str(text or "00:00").strip()[:5]
    hour, _sep, minute = body.partition(":")
    return int(hour or 0) * 60 + int(minute or 0)


def _row(start, end, activity, **extra):
    row = {"start": start, "end": end, "activity": activity}
    row.update(extra)
    return row


SLEEP = _row("06:00", "07:00", "还在床上不想动，赖会儿床", category="sleep", place="家")
FINANCE = {"start": "06:12", "end": "06:22", "activity": "本月支出：一碗热汤面 20 元",
           "category": "finance", "place": "家"}


class FakeDB:
    """只实现落库路径用到的读写；saves/kv 留作断言。"""

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
        return [dict(row) if isinstance(row, dict) else row
                for row in (self.schedules.get(day) or [])]

    def save_schedule(self, day, items):
        self.saves += 1
        self.schedules[day] = [dict(row) if isinstance(row, dict) else row
                               for row in items]

    def schedule_version(self, day):
        return self.versions.get(day, 0)

    def bump_schedule_version(self, day):
        self.versions[day] = self.versions.get(day, 0) + 1
        return self.versions[day]

    def mark_diaries_stale(self, day, from_hour, version):
        return []


class FakePlugin:
    def __init__(self, db):
        self.db = db

    def cfg_int(self, key, default=0):
        return default

    def _data_bot_id(self):
        return "amo"

    def _spawn(self, coro):
        try:
            coro.close()
        except Exception:
            pass


class AddHarness(ScheduleStateMixin):
    def __init__(self, db):
        self.p = FakePlugin(db)
        self.db = db
        self.schedule = {}
        self.schedule_ctx = {}
        self._schedule_commit_lock = threading.RLock()

    def _normalize_schedule_items(self, items, source, day=""):
        return [dict(row) if isinstance(row, dict) else row for row in items]


class AddScheduleItemOverlapTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.state = AddHarness(self.db)
        self.state.schedule = {DAY: []}

    def _seed(self, rows):
        self.state.schedule[DAY] = copy.deepcopy(rows)
        self.db.save_schedule(DAY, rows)
        self.db.kv["schedule"] = {DAY: copy.deepcopy(rows)}
        self.db.saves = 0

    def _persisted(self):
        return [dict(row) for row in self.db.get_schedule(DAY)]

    def _assert_no_overlap(self, rows):
        ordered = sorted(rows, key=lambda row: _minute(row.get("start")))
        for prev, nxt in zip(ordered, ordered[1:]):
            self.assertLessEqual(_minute(prev.get("end")), _minute(nxt.get("start")),
                                 "时间轴重叠：%r -> %r" % (prev, nxt))
        return ordered

    def _add(self, item, source="finance"):
        return self.state.add_schedule_item(DAY, copy.deepcopy(item), source=source)

    # ---- 事故回归 ----
    def test_finance_on_sleep_is_not_persisted_as_overlap(self):
        self._seed([SLEEP])
        result = self._add(FINANCE)
        persisted = self._persisted()
        ordered = self._assert_no_overlap(persisted)
        self.assertEqual(len(persisted), 1, "被睡眠块盖住的 finance 不该落库")
        self.assertEqual(ordered[0]["activity"], SLEEP["activity"])
        self.assertEqual(ordered[0]["start"], "06:00")
        self.assertEqual(ordered[0]["end"], "07:00")
        self.assertFalse(result.get("changed"))
        self.assertEqual(result.get("skipped"), "overlap")
        self.assertEqual(self.db.saves, 0, "拦下的追加不应写库")

    def test_partial_overlap_moves_start_after_previous_end(self):
        self._seed([SLEEP])
        result = self._add({"start": "06:30", "end": "07:30",
                            "activity": "出门买个早饭", "category": "meal"})
        persisted = self._persisted()
        self._assert_no_overlap(persisted)
        starts = {(row["start"], row["end"]) for row in persisted}
        self.assertIn(("06:00", "07:00"), starts)
        self.assertIn(("07:00", "07:30"), starts, "起点必须挪到睡眠结束之后")
        self.assertTrue(result.get("changed"))

    def test_non_overlapping_add_is_written(self):
        self._seed([_row("00:00", "07:00", "睡觉", category="sleep")])
        result = self._add({"start": "12:00", "end": "12:30",
                            "activity": "楼下买了碗面", "category": "meal"})
        persisted = self._persisted()
        self._assert_no_overlap(persisted)
        self.assertEqual(len(persisted), 2)
        self.assertTrue(result.get("changed"))

    # ---- 表 / kv 镜像 ----
    def test_table_and_kv_are_identical_after_add(self):
        self._seed([_row("00:00", "07:00", "睡觉", category="sleep")])
        self._add({"start": "12:00", "end": "12:30", "activity": "吃午饭", "category": "meal"})
        self.assertEqual(self.db.schedules[DAY], self.db.kv["schedule"][DAY])

    def test_table_and_kv_are_identical_when_add_is_refused(self):
        self._seed([SLEEP])
        self._add(FINANCE)
        self.assertEqual(self.db.schedules[DAY], self.db.kv["schedule"][DAY])
        self.assertEqual(self.db.schedules[DAY], [dict(row) for row in [SLEEP]])

    # ---- 反向证据：去掉闸门 → 重叠就会落库（正向断言也就红了）----
    def test_negative_control_without_gate_overlap_persists(self):
        self._seed([SLEEP])
        with mock.patch.object(ScheduleStateMixin, "_resolve_added_item_overlaps",
                               lambda self, day, current, merged, item: (merged, item)):
            self._add(FINANCE)
        persisted = self._persisted()
        self.assertEqual(len(persisted), 2)
        self.assertTrue(_item_overlaps_any(persisted[0], persisted[1:]),
                        "去掉 act 闸门后应当能抓到重叠（正向测试因它变红）")


if __name__ == "__main__":
    unittest.main()
