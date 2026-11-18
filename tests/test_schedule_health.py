# -*- coding: utf-8 -*-
import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_schedule_health import reconcile_stale_health_schedule  # noqa: E402

SICK_ITEM = {"start": "00:00", "end": "23:59", "activity": "生病休息（感冒）",
             "category": "health", "place": "家"}
WORK_ITEM = {"start": "09:00", "end": "18:00", "activity": "写周报", "category": "work"}
REST_ITEM = {"start": "10:00", "end": "12:00", "activity": "在家休息", "category": "rest"}


class ReconcileTests(unittest.TestCase):
    def test_healthy_removes_sick_items(self):
        kept, removed = reconcile_stale_health_schedule([SICK_ITEM, WORK_ITEM], {"status": "健康"})
        self.assertEqual(removed, 1)
        self.assertEqual(kept, [WORK_ITEM])

    def test_sick_keeps_schedule(self):
        kept, removed = reconcile_stale_health_schedule([SICK_ITEM], {"status": "感冒", "sick": True})
        self.assertEqual(removed, 0)
        self.assertEqual(len(kept), 1)

    def test_rest_item_not_touched(self):
        kept, removed = reconcile_stale_health_schedule([REST_ITEM], {"status": "健康"})
        self.assertEqual(removed, 0)
        self.assertEqual(kept, [REST_ITEM])

    def test_sick_word_in_activity_removed(self):
        item = dict(WORK_ITEM, activity="病假一天")
        kept, removed = reconcile_stale_health_schedule([item], {"status": "健康"})
        self.assertEqual(removed, 1)
        self.assertEqual(kept, [])

    def test_future_sick_flavor_life_block_removed(self):
        # 2026-11-17 线上：早上生病重建的 23:00「起来洗漱，扶着墙慢慢来」没被清掉。
        item = {"start": "23:00", "end": "24:00", "activity": "起来洗漱，扶着墙慢慢来",
                "category": "life", "place": "家"}
        kept, removed = reconcile_stale_health_schedule(
            [item], {"status": "健康"}, now=dt.datetime(2026, 11, 17, 13, 58))
        self.assertEqual(removed, 1)
        self.assertEqual(kept, [])

    def test_past_sick_flavor_kept_as_history(self):
        item = {"start": "23:00", "end": "24:00", "activity": "起来洗漱，扶着墙慢慢来",
                "category": "life", "place": "家"}
        kept, removed = reconcile_stale_health_schedule(
            [item], {"status": "健康"}, now=dt.datetime(2026, 11, 17, 23, 30))
        self.assertEqual(removed, 0)
        self.assertEqual(kept, [item])

    def test_current_hour_sick_flavor_kept(self):
        item = {"start": "23:00", "end": "24:00", "activity": "扶着墙慢慢挪",
                "category": "life", "place": "家"}
        kept, removed = reconcile_stale_health_schedule(
            [item], {"status": "健康"}, now=dt.datetime(2026, 11, 17, 23, 10))
        self.assertEqual(removed, 0)

    def test_future_normal_life_block_kept(self):
        item = {"start": "23:00", "end": "24:00", "activity": "起来洗漱，准备睡觉",
                "category": "life", "place": "家"}
        kept, removed = reconcile_stale_health_schedule(
            [item], {"status": "健康"}, now=dt.datetime(2026, 11, 17, 13, 58))
        self.assertEqual(removed, 0)
        self.assertEqual(kept, [item])
