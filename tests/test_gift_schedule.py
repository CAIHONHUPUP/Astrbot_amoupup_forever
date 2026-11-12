# -*- coding: utf-8 -*-
"""礼物大件对日程的覆盖测试（纯标准库）。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schedule_engine import ScheduleEngine  # noqa: E402


class FakeGift:
    def __init__(self, assets=None, worn="", gift_log="", wear=None):
        self.assets = assets or {}
        self.worn = worn
        self.gift_log = gift_log
        self.wear = wear or []

    def asset_override(self, kind):
        return self.assets.get(kind, "")

    def asset_text(self):
        return "、".join(self.assets.values())

    def worn_text(self):
        return self.worn

    def gift_log_text(self):
        return self.gift_log

    def wear_labels(self, temp=None):
        return list(self.wear)


class FakePlugin:
    def __init__(self, gift):
        self.gift = gift
        self.conf = {}

    def cfg_str(self, key, default=""):
        return self.conf.get(key, default)


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self, gift):
        self.p = FakePlugin(gift)
        self.db = FakeDB()


class ScheduleAssetTests(unittest.TestCase):
    def test_gift_car_and_home_override_schedule(self):
        state = FakeState(FakeGift({"vehicle": "保时捷", "home": "别墅"}))
        engine = ScheduleEngine(state)
        profile = engine.resolve_profile({"city": "东莞"}, "住在东兴路", seed="1")
        self.assertEqual(profile["car"], "保时捷")
        self.assertEqual(profile["home"], "别墅")
        self.assertTrue(profile["has_gift_car"])
        self.assertTrue(profile["has_gift_home"])
        # 缓存里保留基础档案，礼物撤销后不会一直卡着
        self.assertNotEqual(state.db.kv["schedule_profile"]["car"], "保时捷")

    def test_generic_house_becomes_new_home(self):
        state = FakeState(FakeGift({"home": "房子"}))
        engine = ScheduleEngine(state)
        profile = engine.resolve_profile({"city": "东莞"}, "住在东兴路", seed="1")
        self.assertEqual(profile["home"], "新家")

    def test_workday_drives_after_car_gift(self):
        engine = ScheduleEngine(FakeState(FakeGift()))
        ctx = {
            "home": "家", "workplace": "公司", "yesterday": {}, "health": {}, "period": {},
            "has_gift_car": True, "car": "保时捷",
        }
        commute = [seg for seg in engine._workday_segments(ctx) if seg["category"] == "commute"]
        self.assertEqual(commute[0]["transit"], "drive")
        self.assertEqual(commute[0]["vehicle"], "保时捷")
        self.assertIn("开车", commute[0]["activity"])

    def test_workday_takes_subway_without_car_gift(self):
        engine = ScheduleEngine(FakeState(FakeGift()))
        ctx = {
            "home": "家", "workplace": "公司", "yesterday": {}, "health": {}, "period": {},
            "has_gift_car": False, "car": "白色大众高尔夫",
        }
        commute = [seg for seg in engine._workday_segments(ctx) if seg["category"] == "commute"]
        self.assertEqual(commute[0]["transit"], "subway")
        self.assertEqual(commute[0]["vehicle"], "")

    def test_no_gift_keeps_default(self):
        state = FakeState(FakeGift())
        engine = ScheduleEngine(state)
        profile = engine.resolve_profile({"city": "东莞"}, "住在东兴路", seed="1")
        self.assertTrue(profile["car"])
        self.assertTrue(profile["home"])

    def test_assets_text_in_context(self):
        state = FakeState(FakeGift({"vehicle": "保时捷"}))
        engine = ScheduleEngine(state)
        self.assertEqual(engine._gift_assets(), "保时捷")

    def test_worn_text_in_context(self):
        state = FakeState(FakeGift(worn="手表、围巾"))
        engine = ScheduleEngine(state)
        self.assertEqual(engine._gift_worn(), "手表、围巾")

    def test_gift_log_in_context(self):
        state = FakeState(FakeGift(gift_log="11-08 小挂件"))
        engine = ScheduleEngine(state)
        self.assertEqual(engine._gift_log(), "11-08 小挂件")

    def test_wear_gift_marks_attribution_and_own_scarf_has_none(self):
        ctx = {
            "home": "家", "workplace": "公司", "yesterday": {}, "health": {}, "period": {},
            "has_gift_car": False, "car": "", "weather": {"temp": 10},
        }
        engine = ScheduleEngine(FakeState(FakeGift(wear=["围巾"])))
        segs = engine._apply_gift_effects(engine._workday_segments(ctx), ctx)
        commute = [seg for seg in segs if seg["category"] == "commute"][0]
        self.assertIn("戴了围巾（他送的）", commute["detail"])

        engine2 = ScheduleEngine(FakeState(FakeGift()))
        segs2 = engine2._apply_gift_effects(engine2._workday_segments(ctx), ctx)
        commute2 = [seg for seg in segs2 if seg["category"] == "commute"][0]
        self.assertIn("戴了围巾", commute2["detail"])
        self.assertNotIn("他送的", commute2["detail"])

    def test_wear_choice_is_stable_per_day(self):
        engine = ScheduleEngine(FakeState(FakeGift()))
        ctx = {"day": "2026-11-08"}
        first = engine._use_gift_today(ctx, "围巾")
        for _ in range(5):
            self.assertEqual(engine._use_gift_today(ctx, "围巾"), first)
        self.assertEqual(engine._wear_phrase("围巾", True), "戴了围巾（他送的）")
        self.assertEqual(engine._wear_phrase("围巾", False), "戴了围巾")
        self.assertEqual(engine._wear_phrase("包", True), "背着包（他送的）")

    def test_gifted_item_can_alternate_with_her_own(self):
        engine = ScheduleEngine(FakeState(FakeGift(wear=["围巾"])))
        outcomes = set()
        for day in range(1, 21):
            ctx = {
                "day": "2026-11-%02d" % day,
                "home": "家", "workplace": "公司", "yesterday": {}, "health": {}, "period": {},
                "has_gift_car": False, "car": "", "weather": {"temp": 10},
            }
            segs = engine._apply_gift_effects(engine._workday_segments(ctx), ctx)
            detail = "；".join(str(seg.get("detail") or "") for seg in segs)
            outcomes.add("gift" if "（他送的）" in detail else "own")
            self.assertNotIn("戴了围巾（他送的）；戴了围巾", detail)
        self.assertEqual(outcomes, {"gift", "own"})

    def test_attribution_only_for_gifted_categories(self):
        ctx = {
            "home": "家", "workplace": "公司", "yesterday": {}, "health": {}, "period": {},
            "has_gift_car": False, "car": "", "weather": {"temp": 10},
        }
        engine = ScheduleEngine(FakeState(FakeGift(wear=["包"])))
        segs = engine._apply_gift_effects(engine._workday_segments(ctx), ctx)
        detail = "；".join(str(seg.get("detail") or "") for seg in segs)
        self.assertIn("背着包（他送的）", detail)
        self.assertIn("戴了围巾", detail)
        self.assertNotIn("戴了围巾（他送的）", detail)

    def test_own_scarf_only_when_cold(self):
        ctx = {
            "home": "家", "workplace": "公司", "yesterday": {}, "health": {}, "period": {},
            "has_gift_car": False, "car": "", "weather": {"temp": 28},
        }
        engine = ScheduleEngine(FakeState(FakeGift()))
        segs = engine._apply_gift_effects(engine._workday_segments(ctx), ctx)
        self.assertNotIn("围巾", str([seg.get("detail") for seg in segs]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
