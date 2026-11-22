# -*- coding: utf-8 -*-
"""并发丢更新 / 写异常两项指标的回归测试（8 项指标中的第 7、8 项）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "tests"), os.path.join(ROOT, "work")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_transition  # noqa: E402
import schedule_engine_v2 as engine  # noqa: E402
import schedule_observer as observer  # noqa: E402
import sim_lib  # noqa: E402

DAY = "2026-11-21"


def _row(start, end, activity, place, zone, category="rest"):
    return {"start": start, "end": end, "activity": activity,
            "place": place, "zone": zone, "category": category}


class ConcurrencyMetricTests(unittest.TestCase):
    def setUp(self):
        self.plugin, self.state = sim_lib.make_state(day=DAY)
        # conftest 会冻结时钟；这里把「今天」固定成测试当天，避免走未来待办分支。
        sim_lib.set_clock(sim_lib.at(DAY, 9, 0))
        self.original = amo_transition.resolve_new_overlaps

    def tearDown(self):
        amo_transition.resolve_new_overlaps = self.original
        sim_lib.restore_clock()

    def _seed(self):
        rows = [
            _row("00:00", "07:00", "睡觉", "家", "home", "sleep"),
            _row("07:00", "08:00", "早饭", "家", "home", "meal"),
            _row("08:00", "09:00", "通勤去公司", "路上", "road", "commute"),
            _row("09:00", "12:00", "上班", "公司", "office", "work"),
            _row("12:00", "13:00", "午饭", "公司", "office", "meal"),
            _row("13:00", "18:00", "上班", "公司", "office", "work"),
            _row("18:00", "19:00", "下班回家", "路上", "road", "commute"),
            _row("19:00", "24:00", "在家休息", "家", "home", "rest"),
        ]
        result = self.state.commit_schedule_change(DAY, rows, old_items=[],
                                                   source="ensure", reason="seed")
        self.assertTrue(result.get("saved"), "seed result=%r" % (result,))
        return [dict(row) for row in (self.state.schedule.get(DAY) or [])]

    def test_version_change_between_entry_and_lock_counts_lost_update(self):
        current = self._seed()
        calls = {"n": 0}

        def bump_then_delegate(items):
            if not calls["n"]:
                calls["n"] += 1
                self.state.db.bump_schedule_version(DAY)  # 模拟另一个写者抢先提交
            return self.original(items)

        amo_transition.resolve_new_overlaps = bump_then_delegate
        new_rows = [dict(row) for row in current]
        new_rows[3]["activity"] = "在工位上忙工作"
        self.state.commit_schedule_change(DAY, new_rows, old_items=current,
                                          source="ensure", reason="concurrent")
        counts = observer.read_metrics(self.state.db, DAY)
        self.assertEqual(counts[engine.METRIC_LOST_UPDATE], 1)

    def test_save_failure_counts_write_error(self):
        current = self._seed()
        original_save = self.state.save_day

        def broken(*args, **kwargs):
            raise RuntimeError("disk full")

        self.state.save_day = broken
        try:
            new_rows = [dict(row) for row in current]
            new_rows[3]["activity"] = "在工位上忙工作"
            result = self.state.commit_schedule_change(DAY, new_rows, old_items=current,
                                                       source="ensure", reason="io")
        finally:
            self.state.save_day = original_save
        self.assertFalse(result.get("saved"))
        counts = observer.read_metrics(self.state.db, DAY)
        self.assertEqual(counts[engine.METRIC_WRITE_ERROR], 1)


if __name__ == "__main__":
    unittest.main()
