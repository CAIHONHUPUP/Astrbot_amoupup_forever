# -*- coding: utf-8 -*-
"""commit_schedule_change 的落库语义：整份替换不自撞、增量/外部重叠截断、幂等、时间轴无重叠。

和 tests/test_round8.py 里只读源码注释的文档 lint 不同，这里全部走**真实**
commit_schedule_change（含锁内落库）并断言落库后的数据；state/db 是测试替身，
不碰真库、不联网。
"""
from __future__ import annotations

import copy
import os
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_state_common import today_str  # noqa: E402
from amo_state_schedule import ScheduleStateMixin  # noqa: E402

DAY = today_str()


def _minute(text):
    body = str(text or "00:00").strip()[:5]
    hour, _sep, minute = body.partition(":")
    return int(hour or 0) * 60 + int(minute or 0)


def _row(start, end, activity, **extra):
    row = {"start": start, "end": end, "activity": activity}
    row.update(extra)
    return row


class FakeDB:
    """只实现 commit 路径用到的读写；saves/versions 留作幂等断言。"""

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
        # 没有事件总线时 _publish_schedule_change 的兼容分支；本文件不关心日记失效。
        return []


class FakePlugin:
    """参考 tests/test_intent_wiring.py：只给 commit 会用到的少数入口。"""

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


class CommitHarness(ScheduleStateMixin):
    """真实 commit 实现 + 假 plugin/db；只把文案清洗/地点归一换成浅拷贝。"""

    def __init__(self, db):
        self.p = FakePlugin(db)
        self.db = db
        self.schedule = {}
        self.schedule_ctx = {}
        self._schedule_commit_lock = threading.RLock()

    def _normalize_schedule_items(self, items, source, day=""):
        return [dict(row) if isinstance(row, dict) else row for row in items]


class CommitSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.state = CommitHarness(self.db)
        self.state.schedule = {DAY: []}

    def _seed(self, rows):
        self.state.schedule[DAY] = copy.deepcopy(rows)
        self.db.save_schedule(DAY, rows)
        self.db.saves = 0

    def _commit(self, old, new, source="test"):
        return self.state.commit_schedule_change(
            DAY, copy.deepcopy(new), old_items=copy.deepcopy(old), source=source)

    def _rows(self):
        return copy.deepcopy(self.state.schedule.get(DAY) or [])

    def _persisted(self):
        return self.db.get_schedule(DAY)

    def _assert_no_overlap(self, rows):
        """按 start 排序后逐对检查：前块 end 必须 <= 后块 start。"""
        ordered = sorted(rows, key=lambda row: _minute(row.get("start")))
        for prev, nxt in zip(ordered, ordered[1:]):
            self.assertLessEqual(
                _minute(prev.get("end")), _minute(nxt.get("start")),
                "时间轴重叠：%r -> %r" % (prev, nxt))
        return ordered

    def _assert_committed(self, rows):
        self.assertEqual(self._persisted(), rows, "state 与 DB 必须一致")
        return self._assert_no_overlap(rows)

    # a) 整份替换：old=[A,B,C] -> new=[A',B',C']，同 start/end/activity、只改内容，
    #    不能因为“同批”被 _resolve_overlaps 截断。
    def test_whole_replace_same_batch_keeps_every_new_row(self):
        old = [_row("09:00", "10:00", "A", place="旧A", happened=True),
               _row("10:00", "11:00", "B", place="旧B", happened=True),
               _row("11:00", "12:00", "C", place="旧C", happened=True)]
        new = [_row("09:00", "10:00", "A", place="新A", detail="改过", happened=True),
               _row("10:00", "11:00", "B", place="新B", detail="改过", happened=True),
               _row("11:00", "12:00", "C", place="新C", detail="改过", happened=True)]
        self._seed(old)
        result = self._commit(old, new)
        rows = self._rows()
        self.assertTrue(result.get("changed"))
        self.assertEqual(len(rows), 3, "整份替换不能把自己吃掉")
        self.assertEqual(
            [{key: value for key, value in row.items() if key != "event_id"}
             for row in rows], new, "落库内容必须是 new，不是旧块")
        self.assertTrue(all(str(row.get("event_id") or "").startswith("ev_")
                            for row in rows), "每块落库都要有事件身份")
        self._assert_committed(rows)

    # b) 增量新块：旧块这一轮被改写（identity 变了，按外部旧块处理）后再追加
    #    09:30-10:30；追加块必须让出 09:00-10:00 旧块，截断到 10:00 之后。
    #    注：若同批旧块 identity 完全不变，_new_ids 会把它过滤掉（见 a 的前提），
    #    这种“原样 old + 新块”的追加不会被截断——那是当前代码的已知边界。
    def test_incremental_append_is_trimmed_to_gap(self):
        old = [_row("09:00", "10:00", "在商场逛", place="商场", happened=True)]
        touched = _row("09:00", "10:00", "逛商场", place="商场",
                       detail="同一小时改文案", happened=True)
        appended = _row("09:30", "10:30", "喝奶茶", place="奶茶店")
        self._seed(old)
        result = self._commit(old, [touched, appended])
        rows = self._rows()
        self.assertTrue(result.get("changed"))
        extras = [row for row in rows if row.get("activity") == "喝奶茶"]
        self.assertTrue(extras, "增量追加的块不能整条消失")
        for row in extras:
            self.assertGreaterEqual(row["start"], "10:00",
                                    "追加块必须截断到旧块 end 之后")
        self._assert_committed(rows)

    # c) 真正的外部重叠：old 有 happened 块且不在 new 列表里 -> 新块被截断。
    def test_external_happened_block_trims_new_block(self):
        old = [_row("09:00", "10:00", "已经发生的事", place="商场", happened=True)]
        new = [_row("09:30", "10:30", "新安排", place="奶茶店")]
        self._seed(old)
        result = self._commit(old, new)
        rows = self._rows()
        self.assertTrue(result.get("changed"))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["start"], rows[0]["end"]), ("10:00", "10:30"))
        self.assertEqual(rows[0]["activity"], "新安排")
        self._assert_committed(rows)

    # d) 归一幂等：连续 commit 两次相同列表 -> 第二次不改变数据。
    def test_repeated_commit_is_idempotent(self):
        rows = [_row("09:00", "10:00", "A", place="家", happened=True),
                _row("10:00", "11:00", "B", place="家")]
        self._seed([])
        first = self._commit([], rows)
        self.assertTrue(first.get("changed"))
        snapshot = self._rows()
        version = self.db.schedule_version(DAY)
        saves = self.db.saves
        second = self._commit(snapshot, snapshot)
        self.assertFalse(second.get("changed"), "相同列表第二次必须走早退")
        self.assertEqual(self._rows(), snapshot, "条数/内容都不能变")
        self.assertEqual(self._persisted(), snapshot)
        self.assertEqual(self.db.schedule_version(DAY), version, "幂等提交不能再 bump 版本")
        self.assertEqual(self.db.saves, saves, "幂等提交不应重复落库")
        self._assert_no_overlap(snapshot)

    # f) 第十轮 B1 回归：old 原样（identity 没变）+ 一条重叠追加。
    #    旧实现把“同批旧块”排除在 blocker 外，追加块不截断，重叠直接落库。
    def test_unchanged_old_plus_overlapping_append_is_trimmed(self):
        old = [_row("09:00", "10:00", "在商场逛", place="商场", happened=True)]
        appended = _row("09:30", "10:30", "喝奶茶", place="奶茶店")
        self._seed(old)
        result = self._commit(old, [copy.deepcopy(old[0]), appended])
        rows = self._rows()
        self.assertTrue(result.get("changed"))
        extras = [row for row in rows if row.get("activity") == "喝奶茶"]
        self.assertTrue(extras, "追加块不能整条消失")
        for row in extras:
            self.assertGreaterEqual(row["start"], "10:00",
                                    "原样旧块 + 重叠追加必须截断到旧块 end 之后")
        self.assertEqual(len(rows), 2)
        self._assert_committed(rows)

    # g) 追加块被旧块完全覆盖 -> 丢弃，时间轴只剩旧块。
    def test_fully_covered_append_is_dropped(self):
        old = [_row("09:00", "10:00", "在商场逛", place="商场", happened=True)]
        covered = _row("09:10", "09:50", "试衣服", place="商场")
        self._seed(old)
        self._commit(old, [copy.deepcopy(old[0]), covered])
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["activity"], "在商场逛")
        self._assert_committed(rows)

    # e) 落库后按 start 排序无重叠（_assert_no_overlap 的独立用例）。
    def test_persisted_timeline_has_no_overlap(self):
        new = [_row("11:00", "12:00", "午饭", place="公司"),
               _row("09:00", "10:00", "通勤", place="路上", happened=True),
               _row("10:00", "11:00", "上班", place="公司", happened=True)]
        self._seed([])
        result = self._commit([], new)
        rows = self._rows()
        self.assertTrue(result.get("changed"))
        ordered = self._assert_committed(rows)
        self.assertEqual([row["start"] for row in ordered],
                         ["09:00", "10:00", "11:00"])


if __name__ == "__main__":
    unittest.main()
