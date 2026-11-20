# -*- coding: utf-8 -*-
"""礼物在日程日记里的出现频率。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schedule_engine import ScheduleEngine  # noqa: E402


def _engine():
    """跳过 __init__，只测纯逻辑。"""
    return ScheduleEngine.__new__(ScheduleEngine)


def _ctx(day="2026-11-09", seed=7, worn="蓝色鲸鱼小挂件", gift_log="7-11 围巾"):
    return {
        "day": day, "seed": seed, "worn": worn, "gift_log": gift_log,
        "meta": {"label": "工作日"}, "weather": {}, "world": {},
        "health": {}, "period": {}, "mood": {}, "yesterday": {},
        "event": {}, "scenic": {}, "assets": "", "location": "东莞",
    }


def _item(start="13:00", end="14:00"):
    return {"start": start, "end": end, "activity": "在工位写报告",
            "place": "莞城区的写字楼", "zone": "office", "mood": "平静"}


class GiftMentionHoursTests(unittest.TestCase):
    def test_exactly_two_hours(self):
        engine = _engine()
        for day in ("2026-11-09", "2026-11-10", "2026-11-11"):
            hours = engine._gift_mention_hours(_ctx(day=day))
            self.assertEqual(len(hours), 2)
            self.assertTrue(all(9 <= h <= 21 for h in hours))

    def test_deterministic_per_day(self):
        engine = _engine()
        self.assertEqual(
            engine._gift_mention_hours(_ctx(day="2026-11-09")),
            engine._gift_mention_hours(_ctx(day="2026-11-09")),
        )

    def test_varies_across_days(self):
        engine = _engine()
        sets = {
            tuple(sorted(engine._gift_mention_hours(_ctx(day="2026-11-%02d" % d))))
            for d in range(1, 15)
        }
        self.assertGreater(len(sets), 1)

    def test_prompt_only_mentions_on_allowed_hour(self):
        engine = _engine()
        ctx = _ctx()
        hours = engine._gift_mention_hours(ctx)
        allowed = sorted(hours)[0]
        blocked = next(h for h in range(9, 22) if h not in hours)

        prompt_allowed = engine._diary_prompt(
            ctx, _item(start="%02d:00" % allowed, end="%02d:00" % (allowed + 1)), None, None
        )
        prompt_blocked = engine._diary_prompt(
            ctx, _item(start="%02d:00" % blocked, end="%02d:00" % (blocked + 1)), None, None
        )

        self.assertIn("蓝色鲸鱼小挂件", prompt_allowed)
        self.assertNotIn("蓝色鲸鱼小挂件", prompt_blocked)
        self.assertIn("不要主动提", prompt_blocked)


if __name__ == "__main__":
    unittest.main()
