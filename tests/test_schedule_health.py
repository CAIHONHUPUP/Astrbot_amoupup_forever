# -*- coding: utf-8 -*-
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
