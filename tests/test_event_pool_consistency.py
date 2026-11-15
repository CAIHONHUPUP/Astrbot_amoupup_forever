# -*- coding: utf-8 -*-
"""事件池一致性：池子本身合格 + 抽选/写入两条路径对同一个事件口径一致。

2026-11-15：validate_pool 原来只查「有没有标签」，99:99、10:00-10:00、重复 key、
zone 里空 token 这些都会静默放过去；而 event_allowed（抽选）和 event_conflict（写记忆前复核）
各用各的判断，一旦口径分叉，就会出现「抽中了却写不进」或者「白天写了夜里的事」。
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_state_context import event_conflict  # noqa: E402
from micro_events import POOL, event_allowed, validate_pool  # noqa: E402

NIGHT_EVENT = {
    "key": "night_snack_probe",
    "text": "半夜起来吃了碗面",
    "tags": ("zone:home", "time:22:00-24:00"),
}


def good_pool():
    return [dict(NIGHT_EVENT)]


class PoolValidationTests(unittest.TestCase):
    def test_shipped_pool_is_valid(self):
        self.assertTrue(POOL)
        self.assertTrue(validate_pool())

    def test_rejects_duplicate_key(self):
        pool = [dict(NIGHT_EVENT), dict(NIGHT_EVENT)]
        with self.assertRaises(ValueError) as ctx:
            validate_pool(pool)
        self.assertIn("key 重复", str(ctx.exception))

    def test_rejects_empty_text(self):
        pool = [dict(NIGHT_EVENT, text="   ")]
        with self.assertRaises(ValueError) as ctx:
            validate_pool(pool)
        self.assertIn("没有文案", str(ctx.exception))

    def test_rejects_out_of_range_window(self):
        pool = [dict(NIGHT_EVENT, tags=("zone:home", "time:99:99-100:00"))]
        with self.assertRaises(ValueError) as ctx:
            validate_pool(pool)
        self.assertIn("取值不对", str(ctx.exception))

    def test_rejects_zero_length_window(self):
        pool = [dict(NIGHT_EVENT, tags=("zone:home", "time:10:00-10:00"))]
        with self.assertRaises(ValueError) as ctx:
            validate_pool(pool)
        self.assertIn("长度为零", str(ctx.exception))

    def test_rejects_empty_zone_token(self):
        pool = [dict(NIGHT_EVENT, tags=("zone:home,", "time:22:00-24:00"))]
        with self.assertRaises(ValueError) as ctx:
            validate_pool(pool)
        self.assertIn("zone 标签有空项", str(ctx.exception))

    def test_rejects_missing_tags(self):
        pool = [{"key": "x", "text": "有话说"}]
        with self.assertRaises(ValueError) as ctx:
            validate_pool(pool)
        self.assertIn("没有 tags", str(ctx.exception))


class CrossPathAgreementTests(unittest.TestCase):
    def ctx(self, clock):
        return {"zone": "home", "time": clock, "health": {"score": 100}}

    def test_afternoon_rejects_by_both_paths(self):
        now = dt.datetime(2026, 11, 15, 14, 0)
        self.assertFalse(event_allowed(NIGHT_EVENT, now, snapshot=None),
                         "抽选路径：白天不该抽中夜间事件")
        self.assertTrue(event_conflict(NIGHT_EVENT, self.ctx("14:00")),
                        "写入路径：白天不该允许夜间事件落库")

    def test_night_accepts_by_both_paths(self):
        now = dt.datetime(2026, 11, 15, 23, 10)
        self.assertTrue(event_allowed(NIGHT_EVENT, now, snapshot=None))
        self.assertEqual(event_conflict(NIGHT_EVENT, self.ctx("23:10")), "")

    def test_any_zone_event_ignores_zone(self):
        event = {"key": "oops", "text": "手滑了一下",
                 "tags": ("zone:any", "time:00:00-24:00")}
        now = dt.datetime(2026, 11, 15, 9, 0)
        self.assertTrue(event_allowed(event, now, snapshot=None))
        self.assertEqual(event_conflict(event, self.ctx("09:00")), "")


if __name__ == "__main__":
    unittest.main()
