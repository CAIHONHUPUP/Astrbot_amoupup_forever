# -*- coding: utf-8 -*-
"""日程文案与地点自洽：place=家 不许写「楼下/外面」，place=楼下小馆 不许写「在家煮面」。

2026-11-17 事故：08:00 的 meal 块 place=家，activity 却是「楼下吃碗热汤粉」，
bot 直接在 08:02 对用户说「早，我在楼下吃粉呢」。根因是 schedule_plan 的 meal
文案池把「楼下吃的」和「在家弄的」混在同一个列表里，按小时而不是按地点挑。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT, TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import schedule_plan as sp  # noqa: E402
import schedule_slots  # noqa: E402
from plugin_fakes import ScheduleSkeletonsFakeState as FakeState  # noqa: E402
from schedule_engine import ScheduleEngine  # noqa: E402

BASE = {
    "day": "2026-11-17",
    "day_type": {"id": "workday", "label": "工作日"},
    "meta": {},
    "home": "家",
    "workplace": "莞城区的写字楼",
    "car": "小电驴",
    "has_gift_car": False,
    "city": "东莞",
    "scenic": {},
    "event": {},
    "good_weather": True,
    "world": {"occupation": "普通上班族", "workplace": "莞城区的写字楼"},
    "health": {},
    "period": {},
    "yesterday": {},
    "mood": {},
    "weather": {"temp": 22, "text": "晴"},
    "profile": {},
    "seed": 20261117,
}

#: 7 天：工作日 / 休息日 / 宅家 / 加班 / 生病 / 痛经 / 朋友聚餐
WEEK = [
    {},
    {"meta": {"rest": True}},
    {"day_type": {"id": "homebody"}},
    {"day_type": {"id": "overtime"}, "event": {"kind": "overtime"}},
    {"health": {"sick": True, "note": "感冒", "pain": 2}},
    {"period": {"phase": "月经期", "pain": 8}},
    {"event": {"kind": "friend_dinner", "effects": {}}},
]


def build(seed=20261117, **overrides):
    ctx = dict(BASE)
    ctx.update(overrides)
    ctx["seed"] = seed
    return ScheduleEngine(FakeState()).build_skeleton(ctx)


def conflicts(items):
    return [(item.get("start"), item.get("activity"), item.get("place"), item.get("zone"))
            for item in items or []
            if sp.activity_place_conflict(item.get("activity"), item.get("place"),
                                          item.get("zone"))]


class ConflictRuleTests(unittest.TestCase):
    def test_home_place_with_outdoor_meal_text(self):
        # 事故原句
        self.assertTrue(sp.activity_place_conflict(
            "楼下吃碗热汤粉，顺便买了个包子", "家", "home"))
        self.assertTrue(sp.activity_place_conflict("在路边摊吃碗面", "家里", "home"))
        self.assertTrue(sp.activity_place_conflict("出门吃碗牛肉面", "家", "home"))
        self.assertTrue(sp.activity_place_conflict("出去逛逛", "家", "home"))

    def test_leaving_home_to_work_is_not_a_conflict(self):
        self.assertFalse(sp.activity_place_conflict("出门上班", "家", "home"))
        self.assertFalse(sp.activity_place_conflict("收拾包，出门前检查一下", "家", "home"))
        self.assertFalse(sp.activity_place_conflict("没出去，在家躺了一天", "家", "home"))
        self.assertFalse(sp.activity_place_conflict("煮点速冻饺子", "家", "home"))

    def test_restaurant_place_with_home_text(self):
        self.assertTrue(sp.activity_place_conflict("在家煮碗面", "楼下小馆", "restaurant"))
        self.assertFalse(sp.activity_place_conflict("下楼吃碗牛肉粉", "楼下小馆", "restaurant"))
        self.assertFalse(sp.activity_place_conflict("吃碗牛肉面", "楼下小馆", "restaurant"))

    def test_other_zones_are_not_enforced(self):
        for zone in ("office", "road", "scenic", "mall", "hotel", "station"):
            self.assertFalse(sp.activity_place_conflict("楼下吃碗粉", "写字楼", zone))

    def test_empty_text_is_not_a_conflict(self):
        self.assertFalse(sp.activity_place_conflict("", "家", "home"))


class MealBankTests(unittest.TestCase):
    def test_home_bank_has_no_outdoor_words(self):
        for hour in (8, 12, 19):
            bank = sp.meal_bank_for(hour, "home", "家")
            self.assertTrue(bank)
            for word in bank:
                self.assertFalse(
                    any(bad in word for bad in sp.OUT_ACTIVITY_WORDS),
                    "hour=%d 家的池里有出门词: %r" % (hour, word))

    def test_out_bank_has_no_home_cooking_words(self):
        for hour in (8, 12, 19):
            bank = sp.meal_bank_for(hour, "restaurant", "楼下小馆")
            self.assertTrue(bank)
            for word in bank:
                self.assertFalse(
                    any(bad in word for bad in sp.HOME_ACTIVITY_WORDS),
                    "hour=%d 外面的池里有在家词: %r" % (hour, word))

    def test_banks_differ_by_place_and_hour(self):
        home = sp.meal_bank_for(8, "home", "家")
        out = sp.meal_bank_for(8, "restaurant", "楼下小馆")
        self.assertNotEqual(home, out)
        self.assertNotEqual(sp.meal_bank_for(8, "home", "家"),
                            sp.meal_bank_for(19, "home", "家"))

    def test_slots_meal_bank_passes_place_through(self):
        self.assertEqual(ScheduleEngine._meal_bank(8, "home", "家"), sp.BREAKFAST_HOME_BANK)
        self.assertEqual(ScheduleEngine._meal_bank(8, "restaurant", "楼下小馆"),
                         sp.BREAKFAST_OUT_BANK)
        self.assertTrue(ScheduleEngine._meal_bank(8))  # 老调用（只给小时）仍然可用


class PlaceTextGuardTests(unittest.TestCase):
    def setUp(self):
        self.engine = ScheduleEngine.__new__(ScheduleEngine)

    def test_repairs_the_0802_incident_block(self):
        item = {"start": "08:00", "end": "09:00", "category": "meal", "zone": "home",
                "place": "家", "activity": "楼下吃碗热汤粉，顺便买了个包子",
                "detail": ""}
        self.engine._enforce_place_text([item])
        self.assertNotIn("楼下", item["activity"])
        self.assertFalse(sp.activity_place_conflict(item["activity"], "家", "home"))

    def test_repairs_restaurant_block_written_as_home_cooking(self):
        item = {"start": "12:00", "end": "13:00", "category": "meal", "zone": "restaurant",
                "place": "楼下小馆", "activity": "在家煮碗面", "detail": ""}
        self.engine._enforce_place_text([item])
        self.assertFalse(sp.activity_place_conflict(
            item["activity"], "楼下小馆", "restaurant"))

    def test_align_zone_with_activity_also_repairs_home(self):
        items = ScheduleEngine._align_zone_with_activity([
            {"start": "08:00", "category": "meal", "zone": "home", "place": "家",
             "activity": "下楼吃碗粉", "detail": ""},
        ])
        self.assertFalse(sp.activity_place_conflict(
            items[0]["activity"], items[0]["place"], items[0]["zone"]))


class SevenDayInvariantTests(unittest.TestCase):
    def test_seven_days_have_no_place_conflict(self):
        for index, variant in enumerate(WEEK):
            for seed in (20261117 + index, 7, 99, 20261117):
                items = build(seed=seed, **variant)
                self.assertEqual(24, len(items))
                self.assertEqual([], conflicts(items),
                                 "day=%d seed=%d" % (index, seed))

    def test_removing_selection_logic_turns_it_red(self):
        """反向证据：去掉「按 place 选池」和兜底守卫，7 天里必然抓得到矛盾。"""
        def mixed_bank(hour, zone="", place=""):
            if hour < 10:
                return list(sp.BREAKFAST_BANK)
            if 10 <= hour < 15:
                return list(sp.LUNCH_BANK)
            return list(sp.DINNER_BANK)

        with mock.patch.object(schedule_slots, "meal_bank_for", mixed_bank), \
                mock.patch.object(ScheduleEngine, "_enforce_place_text",
                                  lambda self, items: items):
            bad = []
            for index, variant in enumerate(WEEK):
                for seed in (20261117 + index, 7, 99, 20261117):
                    bad.extend(conflicts(build(seed=seed, **variant)))
        self.assertTrue(bad, "去掉按地点选池后应该能抓到「楼下 + 家」的矛盾")


if __name__ == "__main__":
    unittest.main()
