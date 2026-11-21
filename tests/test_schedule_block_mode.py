# -*- coding: utf-8 -*-
"""块模式（schedule_block_mode=on）的生成结果验收（2026-11-21 Stage 3）。

开关打开后：一天不再是 24 行整点块，而是合并后的变长块。
这里验证四件事：时间轴仍连续、没有相邻同内容块、小时投影仍可消费（旧读路径不炸）、
字段齐全。默认关闭时本文件不影响黄金基线。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for _path in (ROOT, TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import work.simulate_weeks as weeks  # noqa: E402
import work.schedule_7day as golden7  # noqa: E402
import amo_state_schedule as sched  # noqa: E402
from amo_schedule_blocks import coalesce_blocks  # noqa: E402

FIELDS = golden7.FIELDS


def _minute(text):
    hour, minute = str(text)[:5].split(":")
    return int(hour) * 60 + int(minute)


class BlockModeGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._original = weeks.make_plugin

        def _patched(*args, **kwargs):
            plugin, state = cls._original(*args, **kwargs)
            real = plugin.cfg_bool

            def _cfg(key, default=False):
                if key == "schedule_block_mode":
                    return True
                return real(key, default)

            plugin.cfg_bool = _cfg
            return plugin, state

        weeks.make_plugin = _patched
        try:
            cls.days = golden7.build_days(days=3)
        finally:
            weeks.make_plugin = cls._original
            golden7.restore_clock()

    def test_days_are_not_all_24_rows(self):
        counts = [len(day["blocks"]) for day in self.days.values()]
        self.assertTrue(any(count < 24 for count in counts), counts)

    def test_timeline_is_contiguous(self):
        for day, item in self.days.items():
            blocks = item["blocks"]
            self.assertEqual("00:00", blocks[0][0], day)
            self.assertEqual("24:00", blocks[-1][1], day)
            for left, right in zip(blocks, blocks[1:]):
                self.assertEqual(left[1], right[0], "%s %s" % (day, left))

    def test_no_adjacent_identical_blocks(self):
        for day, item in self.days.items():
            rows = [dict(zip(FIELDS, block)) for block in item["blocks"]]
            self.assertEqual(len(rows), len(coalesce_blocks(rows)), day)

    def test_hourly_projection_still_works(self):
        for day, item in self.days.items():
            rows = [dict(zip(FIELDS, block)) for block in item["blocks"]]
            hourly = sched.ScheduleStateMixin._hourly_schedule(rows)
            self.assertEqual(24, len(hourly), day)
            self.assertEqual("00:00", hourly[0]["start"])
            self.assertEqual("24:00", hourly[-1]["end"])

    def test_fields_present(self):
        for day, item in self.days.items():
            for block in item["blocks"]:
                self.assertEqual(len(FIELDS), len(block), day)


if __name__ == "__main__":
    unittest.main()
