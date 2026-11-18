# -*- coding: utf-8 -*-
"""路上块的 activity 和 vehicle 必须是同一种交通方式。

2026-11-17 10:00 事故：`amo_transition._transit_row()` 先按目的地/天气定 vehicle
（医院 -> 开车）再写 activity，后面的活动填充 / 模型润色把文案改成「坐地铁」，
vehicle 却留在「开车」，面板头照样显示「路上 · 开车」，正文写坐地铁。

这里钉住收口 `ScheduleSlotsMixin._sync_transit_vehicle()`：
文案有交通词就同步 vehicle，没有就按 vehicle 补一句，两边都没有就原样保留。
反向验证见 RemovingCloseoutTests：把收口换掉，矛盾就留下来了。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import schedule_engine as se  # noqa: E402


def row(activity, vehicle="", zone="road", **extra):
    data = {"start": "10:00", "end": "10:30", "activity": activity, "vehicle": vehicle,
            "zone": zone, "place": "路上" if zone == "road" else "家",
            "category": "commute", "transit": "", "detail": ""}
    data.update(extra)
    return data


class SyncRuleTests(unittest.TestCase):
    def sync(self, *items):
        return se.ScheduleEngine._sync_transit_vehicle(list(items))

    def test_subway_text_fixes_drive_vehicle(self):
        """文案说坐地铁、vehicle 是开车 -> 以文案为准。"""
        item = self.sync(row("出门去医院，坐地铁（在路上）", "开车"))[0]
        self.assertEqual(item["vehicle"], "地铁")
        self.assertIn("地铁", item["activity"])

    def test_drive_text_fixes_subway_vehicle(self):
        """反向：文案说开车、vehicle 是地铁 -> 改成开车。"""
        item = self.sync(row("开车回去，路上有点堵", "地铁"))[0]
        self.assertEqual(item["vehicle"], "开车")

    def test_bus_taxi_walk_bicycle_are_synced(self):
        bus = self.sync(row("坐公交去公司", "开车"))[0]
        taxi = self.sync(row("打车去客户公司", "地铁"))[0]
        walk = self.sync(row("走路去楼下便利店", "开车"))[0]
        bike = self.sync(row("骑共享单车过去", "开车"))[0]
        self.assertEqual(bus["vehicle"], "公交")
        self.assertEqual(taxi["vehicle"], "打车")
        self.assertEqual(walk["vehicle"], "步行")
        self.assertEqual(bike["vehicle"], "自行车")

    def test_missing_transport_word_gets_phrase_from_vehicle(self):
        """activity 没写交通方式 -> 按 vehicle 补一句，不硬造新句。"""
        item = self.sync(row("导航一路飘红，堵在高架上", "地铁"))[0]
        self.assertEqual(item["vehicle"], "地铁")
        self.assertTrue(item["activity"].endswith("（坐地铁）"), item["activity"])

    def test_car_name_is_kept_for_drive_blocks(self):
        """vehicle 存的是车名：开车就开自己的车，车名不能删（日记要写「开着白色大众高尔夫」）。"""
        item = self.sync(row("开车出门，路上还算顺，找车位绕了两圈",
                             "白色大众高尔夫", transit="drive"))[0]
        self.assertEqual(item["vehicle"], "白色大众高尔夫")

    def test_non_road_block_is_untouched(self):
        item = row("坐地铁去开会", "白色大众高尔夫", zone="office", place="公司")
        before = dict(item)
        self.sync(item)
        self.assertEqual(item, before)

    def test_both_sides_unknown_keeps_original(self):
        item = row("在路上，有点堵", "")
        before = dict(item)
        self.sync(item)
        self.assertEqual(item, before)

    def test_sync_is_idempotent(self):
        once = self.sync(row("出门去医院，坐地铁（在路上）", "开车"))[0]
        twice = self.sync(dict(once))[0]
        self.assertEqual(once, twice)


class CloseoutEntryTests(unittest.TestCase):
    """三个出口都要收口：_fill_activities / _repair_details / _validate。"""

    def setUp(self):
        self.engine = se.ScheduleEngine.__new__(se.ScheduleEngine)

    def test_fill_activities_exit_syncs_vehicle(self):
        items = [row("出门去医院，坐地铁（在路上）", "开车", transit="subway")]
        with mock.patch.object(se.ScheduleEngine, "_activity_for",
                               lambda self, item, ctx, hour, rng, prev: item["activity"]):
            self.engine._fill_activities(items, {"seed": 1})
        self.assertEqual(items[0]["vehicle"], "地铁")

    def test_repair_details_exit_syncs_vehicle(self):
        items = [row("出门去医院，坐地铁（在路上）", "开车")]
        self.engine._repair_details(items, {"seed": 1})
        self.assertEqual(items[0]["vehicle"], "地铁")

    def test_validate_exit_syncs_vehicle(self):
        items = [row("开车回去，路上有点堵", "地铁")]
        self.engine._validate(items, {})
        self.assertEqual(items[0]["vehicle"], "开车")


class RemovingCloseoutTests(unittest.TestCase):
    def test_removing_closeout_turns_it_red(self):
        """反向证据：把收口换成空操作，「坐地铁 + vehicle=开车」就留下来了。"""
        engine = se.ScheduleEngine.__new__(se.ScheduleEngine)
        with mock.patch.object(se.ScheduleEngine, "_sync_transit_vehicle",
                               lambda self, items: items):
            items = [row("出门去医院，坐地铁（在路上）", "开车")]
            engine._repair_details(items, {"seed": 1})
        self.assertEqual(items[0]["vehicle"], "开车",
                         "去掉收口后应该保持矛盾（红）")


if __name__ == "__main__":
    unittest.main()
