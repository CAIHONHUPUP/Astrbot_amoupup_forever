# -*- coding: utf-8 -*-
"""日程细节与活动的一致性：同一小时不能既「在便利店」又「被拉进临时会议」。"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import schedule_engine as se  # noqa: E402


class DetailConflictTests(unittest.TestCase):
    def test_food_activity_cannot_keep_meeting_detail(self):
        self.assertTrue(se.detail_conflicts("在便利店纠结买啥", "被拉进临时会议"))
        self.assertTrue(se.detail_conflicts("下楼吃牛肉粉", "被拉进临时会议"))
        self.assertTrue(se.detail_conflicts("点个外卖垫两口", "过方案"))

    def test_sleep_activity_cannot_keep_work_detail(self):
        self.assertTrue(se.detail_conflicts("趴桌上眯一小会儿", "需求又变了"))
        self.assertTrue(se.detail_conflicts("赖床最后一分钟", "改到第三版"))

    def test_matching_pairs_are_kept(self):
        self.assertFalse(se.detail_conflicts("开会对齐方案", "被拉进临时会议"))
        self.assertFalse(se.detail_conflicts("在便利店纠结买啥", "吃到一半才想起拍照"))
        self.assertFalse(se.detail_conflicts("改需求", "需求又变了"))
        self.assertFalse(se.detail_conflicts("", "被拉进临时会议"))
        self.assertFalse(se.detail_conflicts("睡觉", ""))


class DetailPickTests(unittest.TestCase):
    def _engine(self):
        # 只测纯逻辑，不初始化 State：__new__ 跳过 __init__
        return se.ScheduleEngine.__new__(se.ScheduleEngine)

    def test_food_activity_never_picks_meeting_detail(self):
        engine = self._engine()
        item = {"zone": "office", "category": "work", "activity": "在便利店纠结买啥"}
        for seed in range(40):
            detail = engine._detail_for(item, {}, 11, random.Random(seed))
            self.assertNotIn("会议", detail or "", "seed=%d detail=%r" % (seed, detail))

    def test_no_safe_detail_returns_empty(self):
        engine = self._engine()
        item = {"zone": "office", "category": "work", "activity": "睡觉"}
        self.assertEqual(engine._detail_for(item, {}, 3, random.Random(1)), "")

    def test_repair_replaces_conflicting_detail(self):
        engine = self._engine()
        items = [
            {
                "start": "11:00", "zone": "office", "category": "work",
                "activity": "在便利店纠结买啥", "detail": "被拉进临时会议",
            },
            {
                "start": "12:00", "zone": "restaurant", "category": "meal",
                "activity": "吃午饭", "detail": "吃到一半才想起拍照",
            },
        ]
        engine._repair_details(items, {"seed": 7})
        self.assertNotIn("会议", items[0]["detail"])
        # 本来就不冲突的保持不变
        self.assertEqual(items[1]["detail"], "吃到一半才想起拍照")


if __name__ == "__main__":
    unittest.main()
